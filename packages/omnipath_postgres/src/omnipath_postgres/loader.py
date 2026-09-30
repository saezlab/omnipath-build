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
from omnipath_core.source_attributes import (
    SOURCE_RECORD_REFERENCE,
    SOURCE_RECORD_SHA256_PREFIX,
    SOURCE_RECORD_TYPE,
)

from .derived import rebuild_derived
from .indexes import create_indexes
from .projection import PayloadReference, iter_resource_records, iter_validated_payloads
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
}
JSON_COLUMNS = frozenset({"record_json", "sources", "quantity"})
_COPY_PARENTS = {
    "identifiers": ("entities",),
    "relations": ("entities",),
    "evidence": ("relations",),
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
    validated_payload_rows: dict[str, int]
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


def _flush_copy_buffer(conn, schema: str, buffers: dict[str, list[dict]], table: str) -> None:
    """Copy buffered parents before children while keeping immediate FKs valid.

    A resource's entity file precedes its relation file, and each projection
    yields the parent row before its identifier/evidence rows. A child may fill
    its batch before the parent buffer does, so flush that partial parent batch
    first. Annotation ownership is still checked by final integrity queries.
    """
    if not buffers[table]:
        return
    for parent in _COPY_PARENTS.get(table, ()):
        _flush_copy_buffer(conn, schema, buffers, parent)
    _copy(conn, schema, table, buffers[table])
    buffers[table].clear()


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
    validated_payload_rows = {}
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
        # Validate each COPY statement instead of retaining FK trigger events
        # for the entire release. All writes still share one outer transaction.
        with conn.cursor() as cur:
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        buffers = {table: [] for table in COLUMNS}
        for resource in release.resources:
            before = counts.copy()
            has_activity = has_participant_relation = False
            for record in iter_resource_records(
                resource.directory, resource.source, resource.version, batch_size=batch_size
            ):
                if record.table == "entities":
                    has_activity |= record.values["entity_type"] == "molecular_activity"
                elif record.table == "relations":
                    has_participant_relation |= record.values[
                        "statement_kind"
                    ] == "relation" and record.values["predicate"] in (
                        "has_input",
                        "has_output",
                        "enabled_by",
                    )
                buffers[record.table].append(record.values)
                counts[record.table] += 1
                if len(buffers[record.table]) >= batch_size:
                    _flush_copy_buffer(conn, schema, buffers, record.table)
            for table, filename in (
                ("entities", "entities.parquet"),
                ("relations", "relations.parquet"),
            ):
                if counts[table] - before[table] != resource.files[filename].rows:
                    raise ValueError(
                        f"Projected row count differs from manifest: {resource.source}/{filename}"
                    )
            # Owner validation sees every row, with COPY FKs already checked.
            for table in buffers:
                _flush_copy_buffer(conn, schema, buffers, table)
            check_reaction_payloads = has_activity and has_participant_relation
            if check_reaction_payloads:
                # Autovacuum cannot see rows in this transaction. Supply current
                # statistics before repeated batch joins into newly copied data.
                # Only join/filter columns need preliminary samples; avoid wide
                # record_json/quantity statistics and their TOAST work here.
                _analyze_tables(conn, schema, _HASH_JOIN_COLUMNS, columns=_HASH_JOIN_COLUMNS)
            references = []
            validated_count = 0
            # Bound transient digest batches even when COPY uses larger batches.
            payload_batch_size = min(batch_size, 1024)
            for reference in iter_validated_payloads(
                resource.directory, batch_size=payload_batch_size
            ):
                references.append(reference)
                validated_count += 1
                if len(references) >= payload_batch_size:
                    _validate_payload_owners(
                        conn, schema, resource.source, resource.version, references
                    )
                    if check_reaction_payloads:
                        _validate_reaction_payloads(
                            conn, schema, resource.source, resource.version, references
                        )
                    references.clear()
            if references:
                _validate_payload_owners(
                    conn, schema, resource.source, resource.version, references
                )
                if check_reaction_payloads:
                    _validate_reaction_payloads(
                        conn, schema, resource.source, resource.version, references
                    )
            if validated_count != resource.files["evidence_payloads.parquet"].rows:
                raise ValueError(
                    f"Validated row count differs from manifest: {resource.source}/evidence_payloads.parquet"
                )
            validated_payload_rows[resource.source] = validated_count
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
        validated_payload_rows,
        {name: round(seconds, 6) for name, seconds in timings.items()},
    )
