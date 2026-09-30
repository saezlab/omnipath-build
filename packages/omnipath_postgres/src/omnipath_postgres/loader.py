"""Transactional PostgreSQL load with no source parsing or entity resolution."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from time import perf_counter

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from omnipath_core.biolink import qualifiers

from .derived import rebuild_derived
from .indexes import create_indexes
from .projection import iter_resource_records
from .releases import PinnedRelease, load_release as read_release, verify_release
from .schema import create_schema


COLUMNS = {
    "entities": (
        "resource",
        "version",
        "entity_key",
        "entity_type",
        "namespace",
        "identifier",
        "taxon",
        "label",
        "has_hierarchy",
        "parent_count",
        "child_count",
        "record_json",
    ),
    "identifiers": (
        "resource",
        "version",
        "entity_key",
        "ordinal",
        "ns",
        "id",
        "is_canonical",
        "source",
    ),
    "relations": (
        "resource",
        "version",
        "relation_key",
        "statement_kind",
        "subject_entity_key",
        "subject_label",
        "subject_type",
        "predicate",
        "object_entity_key",
        "object_label",
        "object_type",
        "taxon",
        "is_directed",
        "sign",
        "category",
        "interaction_class",
        "sources",
        "evidence_count",
        "record_json",
    ),
    "evidence": (
        "resource",
        "version",
        "relation_key",
        "ordinal",
        "source",
        "dataset",
        "row_id",
        "upstream_id",
        "record_json",
    ),
    "annotations": (
        "resource",
        "version",
        "owner_kind",
        "owner_key",
        "evidence_ordinal",
        "ordinal",
        "term",
        "value",
        "quantity",
        "source",
        "dataset",
        "scope",
    ),
    "payloads": (
        "resource",
        "version",
        "ordinal",
        "relation_key",
        "entity_key",
        "source",
        "row_id",
        "payload_json",
    ),
}
JSON_COLUMNS = frozenset({"record_json", "sources", "quantity"})


@dataclass(frozen=True)
class LoadResult:
    release: str
    schema: str
    manifest_sha256: str
    resources: dict[str, str]
    counts: dict[str, int]
    phase_seconds: dict[str, float]


def validate_schema(schema: str) -> None:
    if (
        not isinstance(schema, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", schema)
        or schema.lower() in {"public", "information_schema"}
        or schema.lower().startswith("pg_")
    ):
        raise ValueError("Destination must be a new, non-system PostgreSQL schema identifier")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _copy(conn, schema: str, table: str, rows: list[dict]) -> None:
    columns = COLUMNS[table]
    statement = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
    )
    with conn.cursor() as cur, cur.copy(statement) as copy:
        for row in rows:
            if row.keys() != set(columns):
                raise ValueError(f"Unexpected projection columns for {table}")
            copy.write_row(
                tuple(
                    Jsonb(row[column], dumps=_json)
                    if column in JSON_COLUMNS and row[column] is not None
                    else row[column]
                    for column in columns
                )
            )


def _analyze_schema(conn, schema: str) -> None:
    """Give joins current statistics inside the load transaction, before queries."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind IN ('r','m') ORDER BY c.relname",
            (schema,),
        )
        for (table,) in cur.fetchall():
            cur.execute(
                sql.SQL("ANALYZE {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
            )


def _validate_loaded(conn, schema: str) -> None:
    namespace = sql.Identifier(schema)
    checks = (
        (
            """SELECT entity_key FROM {s}.entities GROUP BY entity_key
               HAVING COUNT(DISTINCT ROW(entity_type, namespace, identifier)) > 1 LIMIT 1""",
            "Published entity identity disagrees across resources",
        ),
        (
            """SELECT relation_key FROM {s}.relations GROUP BY relation_key
               HAVING COUNT(DISTINCT ROW(statement_kind, subject_entity_key, subject_type, predicate,
                   object_entity_key, object_type, is_directed, sign)) > 1 LIMIT 1""",
            "Published relation identity disagrees across resources",
        ),
        (
            """SELECT r.relation_key FROM {s}.relations r
               LEFT JOIN {s}.evidence e USING (resource, version, relation_key)
               GROUP BY r.resource, r.version, r.relation_key, r.evidence_count
               HAVING r.evidence_count IS DISTINCT FROM COUNT(e.ordinal) LIMIT 1""",
            "Published evidence_count does not match evidence occurrences",
        ),
        (
            """SELECT a.owner_key FROM {s}.annotations a
               LEFT JOIN {s}.entities n ON a.owner_kind = 'entity'
                   AND (a.resource, a.version, a.owner_key) =
                       (n.resource, n.version, n.entity_key)
               LEFT JOIN {s}.relations r ON a.owner_kind = 'relation'
                   AND (a.resource, a.version, a.owner_key) =
                       (r.resource, r.version, r.relation_key)
               LEFT JOIN {s}.evidence e ON a.owner_kind = 'evidence'
                   AND (a.resource, a.version, a.owner_key, a.evidence_ordinal) =
                       (e.resource, e.version, e.relation_key, e.ordinal)
               WHERE n.entity_key IS NULL AND r.relation_key IS NULL AND e.relation_key IS NULL
               LIMIT 1""",
            "Annotation references an absent owner",
        ),
    )
    with conn.cursor() as cur:
        for query, message in checks:
            cur.execute(sql.SQL(query).format(s=namespace))
            if problem := cur.fetchone():
                raise ValueError(f"{message}: {problem[0]}")
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
    # Stream only shared keys. A qualifier is part of the prototype identity;
    # ordinary source annotations and taxon assertions may legitimately differ.
    with conn.cursor(name="shared_relation_qualifiers") as cur:
        cur.itersize = 1024
        cur.execute(
            sql.SQL("""
            SELECT relation_key, record_json -> 'annotations'
            FROM {s}.relations
            WHERE relation_key IN (
                SELECT relation_key FROM {s}.relations
                GROUP BY relation_key HAVING COUNT(*) > 1
            )
            ORDER BY relation_key, resource, version
        """).format(s=namespace)
        )
        previous_key = None
        previous_qualifiers = None
        for key, annotations in cur:
            qualified = qualifiers(annotations)
            if key == previous_key and qualified != previous_qualifiers:
                raise ValueError(f"Published relation qualifiers disagree across resources: {key}")
            previous_key, previous_qualifiers = key, qualified


def _metadata(conn, schema: str, release: PinnedRelease) -> None:
    statement = sql.SQL(
        "INSERT INTO {}.resource_versions "
        "(resource, version, manifest_json, manifest_text, manifest_sha256, parquet_checksums) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    ).format(sql.Identifier(schema))
    with conn.cursor() as cur:
        for resource in release.resources:
            cur.execute(
                statement,
                (
                    resource.source,
                    resource.version,
                    Jsonb(json.loads(resource.manifest_json)),
                    resource.manifest_json,
                    resource.manifest_sha256,
                    Jsonb({name: artifact.sha256 for name, artifact in resource.files.items()}),
                ),
            )


def load_release(
    data_root: str | Path,
    manifest_path: str | Path,
    database_url: str,
    *,
    schema: str = "omnipath",
    batch_size: int = 1024,
) -> LoadResult:
    """Load an exact release into a fresh schema; any failure rolls back all writes."""
    validate_schema(schema)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    timings = {}
    started = perf_counter()
    release = read_release(data_root, manifest_path)
    timings["validate_files"] = perf_counter() - started
    counts = dict.fromkeys(COLUMNS, 0)
    with psycopg.connect(database_url, autocommit=True) as conn, conn.transaction():
        with conn.cursor() as cur:
            # Serialize competing attempts at the same destination, without touching other releases.
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (schema,))
            cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
            if cur.fetchone():
                raise ValueError(f"Destination schema already exists: {schema}")
        started = perf_counter()
        create_schema(conn, schema)
        _metadata(conn, schema, release)
        buffers = {table: [] for table in COLUMNS}
        for resource in release.resources:
            before = counts.copy()
            for record in iter_resource_records(
                resource.directory, resource.source, resource.version, batch_size=batch_size
            ):
                buffers[record.table].append(record.values)
                counts[record.table] += 1
                if len(buffers[record.table]) >= batch_size:
                    _copy(conn, schema, record.table, buffers[record.table])
                    buffers[record.table].clear()
            for table, filename in (
                ("entities", "entities.parquet"),
                ("relations", "relations.parquet"),
                ("payloads", "evidence_payloads.parquet"),
            ):
                if counts[table] - before[table] != resource.files[filename].rows:
                    raise ValueError(
                        f"Projected row count differs from manifest: {resource.source}/{filename}"
                    )
        for table, rows in buffers.items():
            if rows:
                _copy(conn, schema, table, rows)
        # COPY into new tables does not provide column statistics. Collect them
        # before integrity joins and derivations, which run before autovacuum can
        # see this transaction's rows. Wide reaction joins especially need them.
        _analyze_schema(conn, schema)
        _validate_loaded(conn, schema)
        timings["copy_and_validate"] = perf_counter() - started
        started = perf_counter()
        create_indexes(conn, schema)
        rebuild_derived(conn, schema)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "INSERT INTO {}.release_metadata "
                    "(release_id, manifest_json, manifest_sha256, manifest_text, input_manifest_sha256) "
                    "VALUES (%s, %s, %s, %s, %s)"
                ).format(sql.Identifier(schema)),
                (
                    release.version,
                    Jsonb(json.loads(release.canonical_json)),
                    release.sha256,
                    release.manifest_json,
                    release.manifest_sha256,
                ),
            )
        # Include populated derived tables so consumers start with usable plans.
        _analyze_schema(conn, schema)
        timings["indexes_and_derived"] = perf_counter() - started
        started = perf_counter()
        verify_release(release)
        timings["recheck_files"] = perf_counter() - started
    return LoadResult(
        release.version,
        schema,
        release.sha256,
        {resource.source: resource.version for resource in release.resources},
        counts,
        {name: round(seconds, 6) for name, seconds in timings.items()},
    )
