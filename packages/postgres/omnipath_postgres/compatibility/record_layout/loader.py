"""Transactional PostgreSQL load with no source parsing or entity resolution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from time import perf_counter
from tempfile import TemporaryDirectory

import duckdb

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from omnipath_subsets.db import validate_schema
from omnipath_core.biolink import qualifiers
from omnipath_core.source_attributes import (
    SOURCE_RECORD_REFERENCE,
    SOURCE_RECORD_SHA256_PREFIX,
    SOURCE_RECORD_TYPE,
)

from .derived import rebuild_derived
from .indexes import create_indexes
from .projection import PayloadReference, iter_validated_payloads
from .bulk import copy_projected_table as _copy_projected_table
from .duckdb_projection import (
    PROJECTION_COLUMNS as COLUMNS,
    projection_query,
    validate_resource,
)
from omnipath_postgres.releases import PinnedRelease, load_release as read_release, verify_release
from .schema import add_base_constraints, create_schema


_OWNER_LOOKUP_COLUMNS = {
    "entities": ("resource", "version", "entity_key"),
    "relations": ("resource", "version", "relation_key"),
}
_HASH_JOIN_COLUMNS = {
    "entities": ("resource", "version", "entity_key", "entity_type"),
    "relations": (
        "resource",
        "version",
        "relation_key",
        "statement_kind",
        "subject_entity_key",
        "predicate",
    ),
    "evidence": ("resource", "version", "relation_key", "ordinal", "source", "row_id"),
    "annotations": (
        "resource",
        "version",
        "owner_kind",
        "owner_key",
        "evidence_ordinal",
        "term",
        "value",
    ),
}


@dataclass(frozen=True)
class LoadResult:
    release: str
    schema: str
    manifest_sha256: str
    resources: dict[str, str]
    counts: dict[str, int]
    validate_source_records: bool
    validated_payload_rows: dict[str, int]
    phase_seconds: dict[str, float]


def _validate_payload_owners(
    conn, schema: str, resource: str, version: str, references: list[PayloadReference]
) -> None:
    """Validate discarded payload owners without staging or storing source bodies."""
    with conn.cursor() as cur:
        for kind, table, key in (
            ("entity", "entities", "entity_key"),
            ("relation", "relations", "relation_key"),
        ):
            expected = {
                reference.owner_key for reference in references if reference.owner_kind == kind
            }
            if not expected:
                continue
            cur.execute(
                sql.SQL(
                    "SELECT {} FROM {}.{} WHERE resource=%s AND version=%s AND {}=ANY(%s)"
                ).format(
                    sql.Identifier(key),
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.Identifier(key),
                ),
                (resource, version, list(expected)),
            )
            absent = expected - {row[0] for row in cur.fetchall()}
            if absent:
                raise ValueError(
                    f"Payload references an absent {kind} owner in {resource}@{version}: {min(absent)}"
                )


def _validate_reaction_payloads(
    conn, schema: str, resource: str, version: str, references: list[PayloadReference]
) -> None:
    """Cross-check existing nonnull reaction bodies before discarding them.

    Missing source rows remain allowed: published annotations support PG-only
    derivation. Each existing matching source row must agree with every evidence
    occurrence at its exact relation/source/row scope, including null values.
    Hashes cover the original text, including whitespace, rather than reencoded
    JSON. Only pointer, digest and shape metadata reach this parameterized query.
    """
    rows = [
        (
            reference.ordinal,
            reference.owner_key,
            reference.source,
            reference.row_id,
            SOURCE_RECORD_SHA256_PREFIX + reference.source_record_sha256,
            reference.source_record_type,
        )
        for reference in references
        if reference.owner_kind == "relation" and reference.source_record_sha256 is not None
    ]
    if not rows:
        return
    values = sql.SQL(",").join(
        sql.SQL("(%s::bigint,%s::text,%s::text,%s::text,%s::text,%s::text)") for _ in rows
    )
    statement = sql.SQL("""
        WITH payload(ordinal,relation_key,source,row_id,sha,shape) AS (VALUES {values})
        SELECT p.ordinal,p.relation_key,e.ordinal,a.hashes,a.shapes,p.sha,p.shape
        FROM payload p
        JOIN {s}.relations r ON r.resource=%s AND r.version=%s AND r.relation_key=p.relation_key
        JOIN {s}.entities subject ON subject.resource=r.resource AND subject.version=r.version
            AND subject.entity_key=r.subject_entity_key
        JOIN {s}.evidence e ON e.resource=r.resource AND e.version=r.version
            AND e.relation_key=r.relation_key AND e.source IS NOT DISTINCT FROM p.source
            AND e.row_id IS NOT DISTINCT FROM p.row_id
        LEFT JOIN LATERAL (
            SELECT array_agg(DISTINCT a.value ORDER BY a.value) FILTER (
                    WHERE a.term={sha_term} AND starts_with(a.value,{sha_prefix})
                ) AS hashes,
                array_agg(DISTINCT a.value ORDER BY a.value) FILTER (
                    WHERE a.term={type_term} AND a.value IS NOT NULL AND a.value<>''
                ) AS shapes
            FROM {s}.annotations a WHERE a.resource=e.resource AND a.version=e.version
                AND a.owner_kind='evidence' AND a.owner_key=e.relation_key
                AND COALESCE(a.evidence_ordinal,-1)=e.ordinal
        ) a ON true
        WHERE r.statement_kind='relation' AND subject.entity_type='molecular_activity'
            AND r.predicate IN ('has_input','has_output','enabled_by')
            AND (a.hashes IS DISTINCT FROM ARRAY[p.sha]
                OR (a.shapes IS NOT NULL AND a.shapes IS DISTINCT FROM ARRAY[p.shape]))
        LIMIT 1
    """).format(
        values=values,
        s=sql.Identifier(schema),
        sha_term=sql.Literal(SOURCE_RECORD_REFERENCE),
        sha_prefix=sql.Literal(SOURCE_RECORD_SHA256_PREFIX),
        type_term=sql.Literal(SOURCE_RECORD_TYPE),
    )
    with conn.cursor() as cur:
        cur.execute(statement, (*[value for row in rows for value in row], resource, version))
        if problem := cur.fetchone():
            ordinal, relation_key, evidence_ordinal, hashes, shapes, digest, shape = problem
            attribute = (
                "source-record SHA reference" if hashes != [digest] else "source-record type"
            )
            raise ValueError(
                f"Reaction {attribute} does not match payload {ordinal} in {resource}@{version} "
                f"{relation_key} evidence {evidence_ordinal}; rebuild the resource with consistent "
                "evidence source attributes"
            )


def _analyze_tables(conn, schema: str, tables, *, columns=None) -> None:
    with conn.cursor() as cur:
        for table in tables:
            statement = sql.SQL("ANALYZE {}.{}").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
            if columns is not None:
                statement += sql.SQL(" ({})").format(
                    sql.SQL(",").join(map(sql.Identifier, columns[table]))
                )
            cur.execute(statement)


def _analyze_schema(conn, schema: str) -> None:
    """Give joins current statistics inside the load transaction, before queries."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind IN ('r','m') ORDER BY c.relname",
            (schema,),
        )
        tables = [table for (table,) in cur.fetchall()]
    _analyze_tables(conn, schema, tables)


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


def _audit_resource_records(
    conn, schema, resource, check_reaction_payloads, batch_size, timings, *, analyze=True
):
    """Audit one pinned resource in bounded batches, after its lookup keys exist."""
    if analyze:
        # Autovacuum cannot see this transaction's copied rows. Every
        # resource needs current scoped-key statistics before batched owner
        # lookups; otherwise fresh tables can produce repeated broad scans.
        # Reaction checks add their scalar join/filter columns in the same
        # sample. Nonreactions never preliminarily scan evidence/annotations
        # or wide record_json/quantity fields.
        lookup_columns = _HASH_JOIN_COLUMNS if check_reaction_payloads else _OWNER_LOOKUP_COLUMNS
        tick = perf_counter()
        _analyze_tables(conn, schema, lookup_columns, columns=lookup_columns)
        timings[f"analyze_payload_lookup_{resource.source}"] = perf_counter() - tick
    references = []
    validated_count = 0
    # Bound transient digest batches even when COPY uses larger batches.
    payload_batch_size = min(batch_size, 1024)
    for reference in iter_validated_payloads(resource.directory, batch_size=payload_batch_size):
        references.append(reference)
        validated_count += 1
        if len(references) >= payload_batch_size:
            _validate_payload_owners(conn, schema, resource.source, resource.version, references)
            if check_reaction_payloads:
                _validate_reaction_payloads(
                    conn, schema, resource.source, resource.version, references
                )
            references.clear()
    if references:
        _validate_payload_owners(conn, schema, resource.source, resource.version, references)
        if check_reaction_payloads:
            _validate_reaction_payloads(conn, schema, resource.source, resource.version, references)
    if validated_count != resource.files["evidence_payloads.parquet"].rows:
        raise ValueError(
            f"Validated row count differs from manifest: {resource.source}/evidence_payloads.parquet"
        )
    return validated_count


def _lock_destination(conn, schema: str) -> None:
    # Session ownership spans the base commit and derivation transaction. It
    # uses the original key, so older transaction-locking loaders also serialize.
    conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (schema,))


def _index_snapshot(conn, schema):
    records = conn.execute(
        "SELECT c.relname,pg_get_indexdef(c.oid),i.indisvalid,i.indisready "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s",
        (schema,),
    ).fetchall()
    if not records or any(not valid or not ready for _, _, valid, ready in records):
        raise ValueError("Checkpoint base indexes are absent or invalid")
    return {name: definition for name, definition, _, _ in records}


def _state(release, counts, validate_source_records, validated_payload_rows, timings):
    return {
        "release_id": release.version,
        "manifest_json": json.loads(release.canonical_json),
        "manifest_text": release.manifest_json,
        "manifest_sha256": release.sha256,
        "input_manifest_sha256": release.manifest_sha256,
        "resources": {item.source: item.version for item in release.resources},
        "resource_metadata": {
            item.source: {
                "version": item.version,
                "manifest_sha256": item.manifest_sha256,
                "parquet_checksums": {
                    name: artifact.sha256 for name, artifact in item.files.items()
                },
            }
            for item in release.resources
        },
        "counts": counts,
        "validate_source_records": validate_source_records,
        "validated_payload_rows": validated_payload_rows,
        "phase_seconds": timings,
    }


def _result(schema, state):
    return LoadResult(
        state["release_id"],
        schema,
        state["manifest_sha256"],
        state["resources"],
        state["counts"],
        state["validate_source_records"],
        state["validated_payload_rows"],
        {name: round(seconds, 6) for name, seconds in state["phase_seconds"].items()},
    )


def _save_checkpoint(conn, schema, state):
    columns = ["format_version", *state]
    values = [
        1,
        *[
            Jsonb(value)
            if key
            in {
                "manifest_json",
                "resources",
                "resource_metadata",
                "base_indexes",
                "counts",
                "validated_payload_rows",
                "phase_seconds",
            }
            else value
            for key, value in state.items()
        ],
    ]
    conn.execute(
        sql.SQL("INSERT INTO {}.base_checkpoint ({}) VALUES ({})").format(
            sql.Identifier(schema),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in columns),
        ),
        values,
    )


def _checked_checkpoint(conn, schema):
    namespace = sql.Identifier(schema)
    if (
        conn.execute(
            "SELECT to_regclass(%s)", (sql.Identifier(schema, "base_checkpoint").as_string(conn),)
        ).fetchone()[0]
        is None
    ):
        raise ValueError("Destination has no resumable base checkpoint")
    if conn.execute(
        sql.SQL("SELECT count(*) FROM {}.release_metadata").format(namespace)
    ).fetchone()[0]:
        raise ValueError("Destination release is already published")
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql.SQL("SELECT * FROM {}.base_checkpoint FOR UPDATE").format(namespace))
        markers = cur.fetchall()
    if (
        len(markers) != 1
        or markers[0]["format_version"] != 1
        or markers[0]["completed_at"] is not None
    ):
        raise ValueError("Destination has no complete, supported base checkpoint")
    state = markers[0]
    try:
        original = json.loads(state["manifest_text"])
        canonical = json.dumps(
            original, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        if (
            original != state["manifest_json"]
            or original["version"] != state["release_id"]
            or original["resources"] != state["resources"]
            or hashlib.sha256(canonical.encode()).hexdigest() != state["manifest_sha256"]
            or hashlib.sha256(state["manifest_text"].encode()).hexdigest()
            != state["input_manifest_sha256"]
        ):
            raise ValueError("Checkpoint release manifest/hash/pins disagree")
        resources, counts, audited = (
            state["resources"],
            state["counts"],
            state["validated_payload_rows"],
        )
        if (
            not isinstance(resources, dict)
            or not resources
            or not isinstance(state["resource_metadata"], dict)
            or state["resource_metadata"].keys() != resources.keys()
            or not isinstance(counts, dict)
            or counts.keys() != COLUMNS.keys()
            or any(type(value) is not int or value < 0 for value in counts.values())
            or type(state["validate_source_records"]) is not bool
            or not isinstance(audited, dict)
            or (
                audited.keys() != resources.keys()
                if state["validate_source_records"]
                else bool(audited)
            )
            or any(type(value) is not int or value < 0 for value in audited.values())
            or not isinstance(state["phase_seconds"], dict)
            or any(
                type(value) not in {int, float} or not math.isfinite(value) or value < 0
                for value in state["phase_seconds"].values()
            )
        ):
            raise ValueError("Checkpoint counts, audit mode or timings are invalid")
        records = conn.execute(
            sql.SQL(
                "SELECT resource,version,manifest_json,manifest_text,manifest_sha256,parquet_checksums "
                "FROM {}.resource_versions"
            ).format(namespace)
        ).fetchall()
        if {(resource, version) for resource, version, *_ in records} != set(resources.items()):
            raise ValueError("Checkpoint resource version pins disagree")
        for resource, version, manifest, text, digest, checksums in records:
            expected = state["resource_metadata"][resource]
            if (
                expected
                != {"version": version, "manifest_sha256": digest, "parquet_checksums": checksums}
                or hashlib.sha256(text.encode()).hexdigest() != digest
                or json.loads(text) != manifest
                or manifest["resource"] != resource
                or manifest["version"] != version
                or {name: item["sha256"] for name, item in manifest["files"].items()} != checksums
            ):
                raise ValueError("Checkpoint resource manifest/checksums disagree")
            if (
                state["validate_source_records"]
                and audited[resource] != manifest["files"]["evidence_payloads.parquet"]["rows"]
            ):
                raise ValueError("Checkpoint source audit count disagrees")
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid checkpoint metadata") from exc
    if state["base_indexes"] != _index_snapshot(conn, schema):
        raise ValueError("Checkpoint base index definitions disagree")
    for table, expected in counts.items():
        actual = conn.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(namespace, sql.Identifier(table))
        ).fetchone()[0]
        if actual != expected:
            raise ValueError(f"Checkpoint base count differs for {table}: {actual}/{expected}")
    return state


def _derive_and_publish(conn, schema, state):
    """Finish inside the caller's transaction; no file access or commit."""
    started = perf_counter()
    rebuild_derived(conn, schema)
    conn.execute(
        sql.SQL(
            "INSERT INTO {}.release_metadata "
            "(release_id,manifest_json,manifest_sha256,manifest_text,input_manifest_sha256) "
            "VALUES (%s,%s,%s,%s,%s)"
        ).format(sql.Identifier(schema)),
        (
            state["release_id"],
            Jsonb(state["manifest_json"]),
            state["manifest_sha256"],
            state["manifest_text"],
            state["input_manifest_sha256"],
        ),
    )
    _analyze_schema(conn, schema)
    state["phase_seconds"]["derive_and_analyze"] = perf_counter() - started


def _finish_checkpoint(conn, schema):
    """Resume a committed base; the session-locked autocommit connection is owned by the loader.

    This entry point is also a private observation hook: base commit has already
    completed before entry. Only this separate derivation transaction may roll
    back; neither COPY nor artifact/reference access occurs here.
    """
    with conn.transaction():
        started = perf_counter()
        state = _checked_checkpoint(conn, schema)
        state["phase_seconds"]["resume_validate"] = perf_counter() - started
        _derive_and_publish(conn, schema, state)
        conn.execute(
            sql.SQL(
                "UPDATE {}.base_checkpoint SET completed_at=clock_timestamp(), phase_seconds=%s "
                "WHERE singleton"
            ).format(sql.Identifier(schema)),
            (Jsonb(state["phase_seconds"]),),
        )
        result = _result(schema, state)
    return result


def finish_release(database_url: str, *, schema: str = "omnipath") -> LoadResult:
    """Finish an unpublished, verified base checkpoint entirely from PostgreSQL.

    Invalid pins/counts reject the checkpoint without mutation. Derivation
    failures leave the base/index checkpoint unchanged and unpublished for retry.
    """
    validate_schema(schema)
    with psycopg.connect(database_url, autocommit=True) as conn:
        _lock_destination(conn, schema)
        return _finish_checkpoint(conn, schema)


def load_release(
    data_root: str | Path,
    manifest_path: str | Path,
    database_url: str,
    *,
    schema: str = "omnipath",
    batch_size: int = 1024,
    duckdb_threads: int = 1,
    memory_limit: str = "512MB",
    temp_directory: str | Path | None = None,
    validate_source_records: bool = False,
    checkpoint_base: bool = False,
    defer_constraints: bool = False,
) -> LoadResult:
    """Load an exact release into a fresh schema; atomic by default.

    With checkpoint_base=True, verified base tables/indexes commit before the
    derivation transaction. A failed derivation can then use finish_release,
    without rereading Parquet or repeating COPY. release_metadata remains empty
    until derivation succeeds, so a staged schema is not a published release.

    With defer_constraints=True, base heaps retain NOT NULL/CHECK during COPY;
    exact keys, annotation uniqueness and all seven validated FKs are installed
    afterwards, before integrity checks, checkpoint or derivation. Requested raw
    audits run after these lookup keys exist.

    Source bodies are discarded. Their immutable file/schema/count checks remain
    mandatory; parsing and owner/hash cross-checks run only for an explicit
    ``validate_source_records`` audit. Stored projections and provenance
    annotations are validated in both modes.
    """
    validate_schema(schema)
    if type(defer_constraints) is not bool:
        raise ValueError("defer_constraints must be a boolean")
    if type(checkpoint_base) is not bool:
        raise ValueError("checkpoint_base must be a boolean")
    if not isinstance(validate_source_records, bool):
        raise ValueError("validate_source_records must be a boolean")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if (
        isinstance(duckdb_threads, bool)
        or not isinstance(duckdb_threads, int)
        or duckdb_threads < 1
    ):
        raise ValueError("duckdb_threads must be a positive integer")
    if not isinstance(memory_limit, str) or not re.fullmatch(
        r"[1-9][0-9]*(?:\.[0-9]+)?\s*(?:KB|MB|GB|KiB|MiB|GiB)", memory_limit
    ):
        raise ValueError("memory_limit must be a positive size such as 512MB")
    timings = {}
    started = perf_counter()
    release = read_release(data_root, manifest_path)
    timings["validate_files"] = perf_counter() - started
    counts = dict.fromkeys(COLUMNS, 0)
    validated_payload_rows = {}
    deferred_audits = []
    spool_parent = (
        Path(temp_directory) if temp_directory is not None else Path(manifest_path).parent
    )
    with (
        TemporaryDirectory(prefix=".postgres-stage-", dir=spool_parent) as spool,
        duckdb.connect(
            config={
                "threads": duckdb_threads,
                "memory_limit": memory_limit,
                "temp_directory": str(Path(spool) / "spill"),
                "preserve_insertion_order": False,
            }
        ) as con,
        psycopg.connect(database_url, autocommit=True) as conn,
    ):
        _lock_destination(conn, schema)
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
                if cur.fetchone():
                    raise ValueError(f"Destination schema already exists: {schema}")
            started = perf_counter()
            if defer_constraints:
                create_schema(conn, schema, defer_constraints=True)
            else:
                create_schema(conn, schema)
            _metadata(conn, schema, release)
            # Default mode validates each COPY statement. Deferred mode has no
            # base PK/FK/index work during COPY; column checks remain active.
            # Both paths share the same base transaction and final constraints.
            with conn.cursor() as cur:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for resource in release.resources:
                tick = perf_counter()
                validation = validate_resource(con, resource.directory)
                timings[f"validate_projection_{resource.source}"] = perf_counter() - tick
                for table, filename in (
                    ("entities", "entities.parquet"),
                    ("relations", "relations.parquet"),
                ):
                    if validation.counts[table] != resource.files[filename].rows:
                        raise ValueError(
                            f"Projected row count differs from manifest: {resource.source}/{filename}"
                        )
                # Whole parent tables precede their children. DuckDB's CSV sink avoids
                # returning expanded rows through Python; bounded files make each
                # immediate-FK COPY statement independent of total resource size.
                for table in COLUMNS:
                    result = _copy_projected_table(
                        conn,
                        schema,
                        table,
                        con,
                        projection_query(
                            resource.directory, resource.source, resource.version, table
                        ),
                        COLUMNS[table],
                        spool,
                    )
                    if result.rows != validation.counts[table]:
                        raise ValueError(
                            f"COPY count differs from source arrays: {resource.source}/{table}"
                        )
                    counts[table] += result.rows
                    timings[f"stage_{resource.source}_{table}"] = result.stage_seconds
                    timings[f"copy_{resource.source}_{table}"] = result.copy_seconds
                if validate_source_records:
                    check_reaction_payloads = (
                        validation.has_activity and validation.has_participant_relation
                    )
                    if defer_constraints:
                        deferred_audits.append((resource, check_reaction_payloads))
                    else:
                        validated_payload_rows[resource.source] = _audit_resource_records(
                            conn, schema, resource, check_reaction_payloads, batch_size, timings
                        )
            if defer_constraints:
                tick = perf_counter()
                add_base_constraints(conn, schema)
                timings["base_constraints"] = perf_counter() - tick
                if deferred_audits:
                    lookup_columns = (
                        _HASH_JOIN_COLUMNS
                        if any(flag for _, flag in deferred_audits)
                        else _OWNER_LOOKUP_COLUMNS
                    )
                    tick = perf_counter()
                    _analyze_tables(conn, schema, lookup_columns, columns=lookup_columns)
                    timings["analyze_deferred_audit_lookup"] = perf_counter() - tick
                    for resource, check_reaction_payloads in deferred_audits:
                        validated_payload_rows[resource.source] = _audit_resource_records(
                            conn,
                            schema,
                            resource,
                            check_reaction_payloads,
                            batch_size,
                            timings,
                            analyze=False,
                        )
            # COPY into new tables does not provide column statistics. Collect them
            # before integrity joins and derivations, which run before autovacuum can
            # see this transaction's rows. Wide reaction joins especially need them.
            _analyze_schema(conn, schema)
            _validate_loaded(conn, schema)
            timings["copy_and_validate"] = perf_counter() - started
            started = perf_counter()
            create_indexes(conn, schema)
            timings["indexes"] = perf_counter() - started
            state = _state(
                release, counts, validate_source_records, validated_payload_rows, timings
            )
            if checkpoint_base:
                state["base_indexes"] = _index_snapshot(conn, schema)
                tick = perf_counter()
                verify_release(release)
                timings["recheck_files"] = perf_counter() - tick
                _save_checkpoint(conn, schema, state)
            else:
                _derive_and_publish(conn, schema, state)
                timings["indexes_and_derived"] = perf_counter() - started
                tick = perf_counter()
                verify_release(release)
                timings["recheck_files"] = perf_counter() - tick
        if checkpoint_base:
            return _finish_checkpoint(conn, schema)
    return _result(schema, state)
