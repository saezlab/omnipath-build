"""Load frozen resolved Parquets into main's physical PostgreSQL contract.

DuckDB prepares dictionaries and relational COPY queries. PostgreSQL never
resolves an entity or reads a raw source body. Main's downstream functions own
any internal transactions; durable metadata records completed phase boundaries.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, is_dataclass
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import duckdb
import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

from .aligned_projection import companion_ddl, prepare_aligned_release
from .bulk import BulkCopyResult
from .loader import validate_schema
from .releases import load_release as read_release, verify_release
from .main_compat.db.schema import ensure_schema, ensure_source_partitions, ensure_deferred_indexes
from .main_compat.db.indexes import create_secondary_indexes

DIMENSION_IDS = {
    "data_source": "source_id",
    "dataset": "dataset_id",
    "vocab_identifier_type": "identifier_type_id",
    "vocab_entity_type": "entity_type_id",
    "vocab_relation_predicate": "relation_predicate_id",
    "vocab_relation_category": "relation_category_id",
    "vocab_annotation_scope": "annotation_scope_id",
    "vocab_entity_role": "entity_role_id",
    "vocab_resolution_status": "resolution_status_id",
}
PRODUCTS = ("metsigdb", "network_views", "cosmos")


@dataclass(frozen=True)
class AlignedLoadResult:
    release: str
    schema: str
    manifest_sha256: str
    resources: dict[str, str]
    counts: dict[str, int]
    phase_seconds: dict[str, float]
    committed_products: tuple[str, ...]
    compatibility: dict
    layout: str = "main"


def _emit(observer, event, **fields):
    if observer is not None:
        observer(event, **fields)


def _json(value):
    if is_dataclass(value):
        return _json(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _lock(conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
            ["omnipath:main-parquet:" + schema],
        )
        if not cur.fetchone()[0]:
            raise ValueError("Another migration owns this schema")
    conn.commit()


def _identity(conn, schema, expected=None):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT version,manifest_sha256 FROM {}.parquet_release WHERE singleton"
            ).format(sql.Identifier(schema))
        )
        row = cur.fetchone()
    if row is None or (expected is not None and tuple(row) != tuple(expected)):
        raise ValueError("Pinned PostgreSQL release identity changed")
    return tuple(row)


def _metadata_schema(conn, schema, release):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("""CREATE TABLE {}.parquet_release (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            version text NOT NULL, manifest_sha256 text NOT NULL,
            manifest jsonb NOT NULL, status text NOT NULL,
            counts jsonb NOT NULL DEFAULT '{{}}',
            phase_seconds jsonb NOT NULL DEFAULT '{{}}',
            compatibility jsonb NOT NULL DEFAULT '{{}}',
            constraint_plan jsonb NOT NULL DEFAULT '{{}}')""").format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL("""CREATE TABLE {}.parquet_resource (
            resource text PRIMARY KEY, version text NOT NULL,
            manifest_sha256 text NOT NULL, manifest jsonb NOT NULL)""").format(
                sql.Identifier(schema)
            )
        )
        cur.execute(
            sql.SQL("""CREATE TABLE {}.parquet_phase (
            phase text PRIMARY KEY, completed_at timestamptz NOT NULL DEFAULT now(),
            seconds double precision NOT NULL, result jsonb NOT NULL)""").format(
                sql.Identifier(schema)
            )
        )
        cur.execute(
            sql.SQL(
                "INSERT INTO {}.parquet_release(singleton,version,manifest_sha256,manifest,status) VALUES(true,%s,%s,%s,%s)"
            ).format(sql.Identifier(schema)),
            [
                release.version,
                release.sha256,
                Json(json.loads(release.canonical_json)),
                "preparing",
            ],
        )
        cur.executemany(
            sql.SQL("INSERT INTO {}.parquet_resource VALUES(%s,%s,%s,%s)").format(
                sql.Identifier(schema)
            ),
            [
                (r.source, r.version, r.manifest_sha256, Json(json.loads(r.manifest_json)))
                for r in release.resources
            ],
        )
    conn.commit()


def _read_dimensions(conn, schema):
    result = {}
    with conn.cursor() as cur:
        for table, idcolumn in DIMENSION_IDS.items():
            columns = (idcolumn, "source_id", "name") if table == "dataset" else (idcolumn, "name")
            cur.execute(
                sql.SQL("SELECT {} FROM {}.{} ORDER BY {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.Identifier(idcolumn),
                )
            )
            result[table] = tuple(cur.fetchall())
    return result


def _write_dimensions(conn, schema, dimensions):
    with conn.cursor() as cur:
        for table, rows in dimensions.items():
            idcolumn = DIMENSION_IDS[table]
            columns = (idcolumn, "source_id", "name") if table == "dataset" else (idcolumn, "name")
            if rows:
                cur.executemany(
                    sql.SQL("INSERT INTO {}.{}({}) VALUES({}) ON CONFLICT ({}) DO NOTHING").format(
                        sql.Identifier(schema),
                        sql.Identifier(table),
                        sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns),
                        sql.Identifier(idcolumn),
                    ),
                    rows,
                )
            cur.execute("SELECT pg_get_serial_sequence(%s,%s)", [schema + "." + table, idcolumn])
            sequence = cur.fetchone()[0]
            if sequence and rows:
                cur.execute("SELECT setval(%s,%s,true)", [sequence, max(row[0] for row in rows)])
    conn.commit()


def _defer_constraints(conn, schema, tables):
    """Capture real main DDL, so deferred COPY restores the exact constraint contract."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.relname,c.conname,c.contype,pg_get_constraintdef(c.oid,true)
            FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=%s AND (t.relname=ANY(%s) OR c.contype='f') AND c.conparentid=0
                AND c.contype IN ('p','u','f','c','x')
            ORDER BY c.contype='f' DESC,t.relname,c.conname""",
            [schema, list(tables)],
        )
        constraints = cur.fetchall()
        for table, name, kind, definition in constraints:
            cur.execute(
                sql.SQL("ALTER TABLE {}.{} DROP CONSTRAINT {} CASCADE").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.Identifier(name)
                )
            )
        cur.execute(
            """SELECT t.relname,i.relname,pg_get_indexdef(i.oid)
            FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
            JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=%s AND t.relname=ANY(%s)
              AND NOT EXISTS(SELECT 1 FROM pg_constraint c WHERE c.conindid=i.oid)
              AND NOT EXISTS(SELECT 1 FROM pg_inherits inh WHERE inh.inhrelid=i.oid)
            ORDER BY t.relname,i.relname""",
            [schema, list(tables)],
        )
        indexes = cur.fetchall()
        for table, name, definition in indexes:
            cur.execute(
                sql.SQL("DROP INDEX {}.{} CASCADE").format(
                    sql.Identifier(schema), sql.Identifier(name)
                )
            )
        plan = {"constraints": constraints, "indexes": indexes}
        cur.execute(
            sql.SQL("UPDATE {}.parquet_release SET constraint_plan=%s WHERE singleton").format(
                sql.Identifier(schema)
            ),
            [Json(plan)],
        )
    conn.commit()
    return plan


def _restore_constraints(conn, schema, plan):
    with conn.cursor() as cur:
        for table, name, kind, definition in sorted(plan["constraints"], key=lambda r: r[2] == "f"):
            cur.execute(
                sql.SQL("ALTER TABLE {}.{} ADD CONSTRAINT {} ").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.Identifier(name)
                )
                + sql.SQL(definition)
            )
        for table, name, definition in plan["indexes"]:
            cur.execute(definition)
    # Validation and exact indexes are present before the base is advertised.


def _copy(conn, schema, item, con, spool):
    def literal(v):
        return "'" + str(v).replace("'", "''") + "'"

    with TemporaryDirectory(prefix=item.table + "-", dir=spool) as directory:
        output = Path(directory) / "csv"
        start = perf_counter()
        row = con.execute(
            f"COPY ({item.query}) TO {literal(output)} (FORMAT CSV, HEADER false, NULL '\\N', PER_THREAD_OUTPUT true, FILE_SIZE_BYTES '32MB', FILENAME_PATTERN 'part_{{i}}')"
        ).fetchone()
        if not row or type(row[0]) is not int or row[0] < 0:
            raise ValueError(f"DuckDB returned no valid staged row count for {item.table}")
        staged = row[0]
        stage_seconds = perf_counter() - start
        files = sorted(output.glob("*.csv"))
        if staged and not files:
            raise ValueError(f"DuckDB produced no CSV chunks for {item.table}")
        statement = sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
            sql.Identifier(schema),
            sql.Identifier(item.table),
            sql.SQL(",").join(map(sql.Identifier, item.columns)),
        )
        copied = size = 0
        start = perf_counter()
        for path in files:
            size += path.stat().st_size
            with conn.cursor() as cur, path.open("rb") as stream:
                cur.copy_expert(statement.as_string(conn), stream, size=1024 * 1024)
                if cur.rowcount < 0:
                    raise ValueError(f"PostgreSQL returned no COPY count for {item.table}")
                copied += cur.rowcount
        if copied != staged:
            raise ValueError(f"COPY count differs for {item.table}: {copied}/{staged}")
        return BulkCopyResult(staged, stage_seconds, perf_counter() - start, len(files), size)


def _analyze(conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p','m') ORDER BY c.relname",
            [schema],
        )
        for (table,) in cur.fetchall():
            cur.execute(
                sql.SQL("ANALYZE {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
            )
    conn.commit()


def _checkpoint(conn, schema, phase, seconds, result, identity, observer):
    _identity(conn, schema, identity)
    with conn.cursor() as cur:
        if phase in PRODUCTS:
            cur.execute(
                sql.SQL("""CREATE TABLE IF NOT EXISTS {}.subset_build_metadata (
                product text PRIMARY KEY, release_id text NOT NULL,
                manifest_sha256 text NOT NULL, stats jsonb NOT NULL,
                built_at timestamptz NOT NULL DEFAULT now())""").format(sql.Identifier(schema))
            )
            stats = result.get("result", result) if isinstance(result, dict) else result
            cur.execute(
                sql.SQL("""INSERT INTO {}.subset_build_metadata
                (product,release_id,manifest_sha256,stats) VALUES(%s,%s,%s,%s)
                ON CONFLICT(product) DO UPDATE SET release_id=EXCLUDED.release_id,
                manifest_sha256=EXCLUDED.manifest_sha256,stats=EXCLUDED.stats,built_at=now()""").format(
                    sql.Identifier(schema)
                ),
                [phase, *identity, Json(_json(stats))],
            )
        cur.execute(
            sql.SQL("""INSERT INTO {}.parquet_phase(phase,seconds,result) VALUES(%s,%s,%s)
            ON CONFLICT(phase) DO UPDATE SET seconds=EXCLUDED.seconds,result=EXCLUDED.result,completed_at=now()""").format(
                sql.Identifier(schema)
            ),
            [phase, seconds, Json(_json(result))],
        )
        cur.execute(
            sql.SQL("""UPDATE {}.parquet_release SET status=%s,
            phase_seconds=phase_seconds || jsonb_build_object(%s::text,%s::double precision) WHERE singleton""").format(
                sql.Identifier(schema)
            ),
            [phase, phase, seconds],
        )
    conn.commit()
    _emit(observer, "phase_committed", phase=phase, seconds=seconds, result=_json(result))


def finish_main_release(database_url, *, schema="omnipath", products=PRODUCTS, observer=None):
    """Resume committed main substrate; never repeat COPY or completed products."""
    validate_schema(schema)
    if not isinstance(products, (tuple, list)) or any(not isinstance(p, str) for p in products):
        raise ValueError("Products must be unique known products")
    if len(set(products)) != len(products) or any(p not in PRODUCTS for p in products):
        raise ValueError("Products must be unique known products")
    from .main_compat.pipeline import run_main_derivations, run_main_product

    with closing(psycopg2.connect(database_url)) as conn:
        _lock(conn, schema)
        identity = _identity(conn, schema)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT phase FROM {}.parquet_phase").format(sql.Identifier(schema))
            )
            completed = {r[0] for r in cur.fetchall()}
            if "base" not in completed:
                raise ValueError("No committed base checkpoint; incomplete COPY cannot be resumed")
            cur.execute(
                sql.SQL(
                    "SELECT resource,manifest FROM {}.parquet_resource ORDER BY resource"
                ).format(sql.Identifier(schema))
            )
            metadata = dict(cur.fetchall())
        if "derived" not in completed:
            _emit(observer, "phase_start", phase="derived")
            start = perf_counter()
            outcome = run_main_derivations(
                conn, schema=schema, progress=True, published_metadata=metadata
            )
            _checkpoint(
                conn, schema, "derived", perf_counter() - start, outcome, identity, observer
            )
        for product in products:
            if product in completed:
                continue
            _identity(conn, schema, identity)
            _emit(observer, "phase_start", phase=product)
            start = perf_counter()
            # Main's MetSigDB temp_buffers requires a session unused by derivation.
            if product == "metsigdb":
                with closing(psycopg2.connect(database_url)) as product_conn:
                    outcome = run_main_product(
                        product_conn, product=product, schema=schema, progress=True
                    )
                    _checkpoint(
                        product_conn,
                        schema,
                        product,
                        perf_counter() - start,
                        outcome,
                        identity,
                        observer,
                    )
            else:
                outcome = run_main_product(conn, product=product, schema=schema, progress=True)
                _checkpoint(
                    conn, schema, product, perf_counter() - start, outcome, identity, observer
                )
            completed.add(product)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT phase FROM {}.parquet_phase").format(sql.Identifier(schema))
            )
            completed = {r[0] for r in cur.fetchall()}
            if all(p in completed for p in PRODUCTS):
                cur.execute(
                    sql.SQL(
                        "UPDATE {}.parquet_release SET status='complete' WHERE singleton"
                    ).format(sql.Identifier(schema))
                )
            cur.execute(
                sql.SQL(
                    "SELECT version,manifest_sha256,counts,phase_seconds,compatibility FROM {}.parquet_release WHERE singleton"
                ).format(sql.Identifier(schema))
            )
            version, digest, counts, times, compat = cur.fetchone()
            cur.execute(
                sql.SQL("SELECT resource,version FROM {}.parquet_resource").format(
                    sql.Identifier(schema)
                )
            )
            resources = dict(cur.fetchall())
        conn.commit()
        return AlignedLoadResult(
            version,
            schema,
            digest,
            resources,
            counts,
            times,
            tuple(p for p in PRODUCTS if p in completed),
            compat,
        )


def load_main_release(
    data_root,
    manifest_path,
    database_url,
    *,
    schema="omnipath",
    duckdb_threads=1,
    memory_limit="1GB",
    temp_directory=None,
    defer_constraints=True,
    base_only=False,
    products=PRODUCTS,
    observer=None,
):
    """Create a fresh main layout, commit its validated base, then finish it."""
    validate_schema(schema)
    if type(duckdb_threads) is not int or duckdb_threads < 1:
        raise ValueError("duckdb_threads must be a positive integer")
    if not isinstance(memory_limit, str) or not re.fullmatch(
        r"[1-9][0-9]*(?:\.[0-9]+)?\s*(?:KB|MB|GB|KiB|MiB|GiB)", memory_limit
    ):
        raise ValueError("memory_limit must be a positive size such as 512MB")
    if type(defer_constraints) is not bool or type(base_only) is not bool:
        raise ValueError("defer_constraints and base_only must be booleans")
    if not isinstance(products, (tuple, list)) or any(not isinstance(p, str) for p in products):
        raise ValueError("Products must be unique known products")
    if len(set(products)) != len(products) or any(p not in PRODUCTS for p in products):
        raise ValueError("Products must be unique known products")
    start = perf_counter()
    release = read_release(data_root, manifest_path)
    artifact_seconds = perf_counter() - start
    spool = Path(temp_directory or Path(manifest_path).parent / "main-staging")
    spool.mkdir(parents=True, exist_ok=True)
    with closing(psycopg2.connect(database_url)) as conn:
        _lock(conn, schema)
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)", [schema])
            if cur.fetchone()[0]:
                raise ValueError(
                    "Aligned loading requires a new schema; use finish for a checkpoint"
                )
        ensure_schema(conn, schema=schema, indexes=False, progress=True)
        _metadata_schema(conn, schema, release)
        identity = (release.version, release.sha256)
        with TemporaryDirectory(prefix="main-projection-", dir=spool) as directory:
            with duckdb.connect(str(Path(directory) / "projection.duckdb")) as con:
                con.execute("SET threads=?", [duckdb_threads])
                con.execute("SET memory_limit=?", [memory_limit])
                con.execute("SET temp_directory=?", [str(Path(directory) / "spill")])
                _emit(observer, "phase_start", phase="projection")
                start = perf_counter()
                plan = prepare_aligned_release(
                    con,
                    release,
                    dimension_rows=_read_dimensions(conn, schema),
                    progress=lambda fields: _emit(observer, "projection_progress", **fields),
                )
                projection_seconds = perf_counter() - start
                _write_dimensions(conn, schema, plan.dimensions)
                for _, source in plan.sources:
                    ensure_source_partitions(conn, schema=schema, source=source)
                with conn.cursor() as cur:
                    for statement in companion_ddl(schema):
                        cur.execute(statement)
                conn.commit()
                constraint_plan = (
                    _defer_constraints(conn, schema, [q.table for q in plan.queries])
                    if defer_constraints
                    else {"constraints": [], "indexes": []}
                )
                start = perf_counter()
                counts = {}
                staging = copy_seconds = 0.0
                for item in plan.queries:
                    _emit(observer, "copy_start", table=item.table, rows=plan.counts[item.table])
                    copied = _copy(conn, schema, item, con, spool)
                    if copied.rows != plan.counts[item.table]:
                        raise ValueError("Prepared projection changed during COPY")
                    counts[item.table] = copied.rows
                    staging += copied.stage_seconds
                    copy_seconds += copied.copy_seconds
                    _emit(observer, "copy_complete", table=item.table, **asdict(copied))
                _emit(observer, "phase_start", phase="constraints")
                constraints_start = perf_counter()
                _restore_constraints(conn, schema, constraint_plan)
                constraints_seconds = perf_counter() - constraints_start
                # Ensure helpers can commit only after every captured key/FK was restored.
                conn.commit()
                ensure_deferred_indexes(conn, schema=schema, progress=True)
                create_secondary_indexes(conn, schema=schema)
                _analyze(conn, schema)
                verify_release(release)
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            "UPDATE {}.parquet_release SET counts=%s,compatibility=%s,phase_seconds=%s WHERE singleton"
                        ).format(sql.Identifier(schema)),
                        [
                            Json(counts),
                            Json(dict(plan.compatibility)),
                            Json(
                                {
                                    "artifact_validation": artifact_seconds,
                                    "projection": projection_seconds,
                                    "csv_staging": staging,
                                    "copy": copy_seconds,
                                    "constraints": constraints_seconds,
                                }
                            ),
                        ],
                    )
                _checkpoint(
                    conn,
                    schema,
                    "base",
                    perf_counter() - start,
                    {"counts": counts},
                    identity,
                    observer,
                )
    if base_only:
        return AlignedLoadResult(
            release.version,
            schema,
            release.sha256,
            {r.source: r.version for r in release.resources},
            counts,
            {
                "artifact_validation": artifact_seconds,
                "projection": projection_seconds,
                "csv_staging": staging,
                "copy": copy_seconds,
                "constraints": constraints_seconds,
            },
            (),
            dict(plan.compatibility),
        )
    return finish_main_release(database_url, schema=schema, products=products, observer=observer)
