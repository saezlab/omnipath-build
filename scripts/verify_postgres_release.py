"""Verify an exact PostgreSQL release without source parsers or entity resolution.

Default mode is read-only: attest the pinned artifacts, compare every base record
and scalar column in bounded batches, and validate normalized rows against those
records in SQL. Missing optional product resources are reported, not invented.

Use --pg-only-rebuild separately while the caller has made its private Parquet
root unavailable. This mode reads PostgreSQL only, rebuilds products in a single
transaction, exercises network queries, then ROLLS BACK all changes. It never
renames files or commits. --baseline-schema compares only common, unambiguous
source events with identical original text SHA; canonical IDs are excluded when
resolution references differ. This is scoped parity evidence, not full-release
scientific equivalence.

Credentials come from OMNIPATH_DATABASE_URL. Example:
uv run python scripts/verify_postgres_release.py --schema release_2026_09 \
    --data-root /private/release --manifest /private/release/release.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from omnipath_core.biolink import hierarchy_direction, is_descendant
from omnipath_core.source_attributes import (
    CELLULAR_LOCATION,
    CONVERSION_DIRECTION,
    SOURCE_RECORD_REFERENCE,
    SOURCE_RECORD_SHA256_PREFIX,
    SOURCE_RECORD_TYPE,
)
from omnipath_postgres.indexes import _INDEXES
from omnipath_postgres.loader import COLUMNS, validate_schema
from omnipath_postgres.projection import iter_rows
from omnipath_postgres.reactions import _source_type, direction_context, participant_context
from omnipath_postgres.releases import load_release as load_pinned_release, verify_release


REACTION_SOURCES = ("rhea", "recon3d", "metatlas", "kegg", "reactome")

_DERIVED_INDEXES = (
    ("ontology_edge_parent_idx", "entity_ontology_relation", "parent_entity_id"),
    ("ontology_edge_child_idx", "entity_ontology_relation", "child_entity_id"),
    ("ontology_closure_ancestor_idx", "ontology_closure", "ancestor_entity_id, hierarchy_kind"),
    ("ontology_ancestor_ancestor_idx", "ontology_ancestor", "ancestor_entity_id, hierarchy_kind"),
    ("ontology_term_search_idx", "entity_ontology_term", "lower(term_id) text_pattern_ops"),
    ("reaction_context_event_idx", "reaction_context", "reaction_entity_id"),
    ("reaction_participant_entity_idx", "reaction_participant", "participant_entity_id"),
    (
        "entity_relation_counts_search_count_idx",
        "entity_relation_counts",
        "search_count DESC, entity_id",
    ),
)


def _normalized_definition(value):
    """Normalize deparser whitespace; retain expressions, casts and parentheses."""
    return " ".join(value.split())


class VerificationError(ValueError):
    """The database does not represent its pinned release faithfully."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def _query(conn, schema, statement, params=()):
    return conn.execute(sql.SQL(statement).format(s=sql.Identifier(schema)), params)


def _guard(conn, schema, check, statement, params=()):
    row = _query(conn, schema, statement, params).fetchone()
    require(row is None, f"{check}: first inconsistent owner {row}")


def _counts(conn, schema):
    result = {}
    for table in COLUMNS:
        rows = conn.execute(
            sql.SQL("SELECT resource,version,count(*) FROM {}.{} GROUP BY resource,version").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
        ).fetchall()
        for source, version, count in rows:
            result.setdefault((source, version), dict.fromkeys(COLUMNS, 0))[table] = count
    return result


def _attest_metadata(conn, schema, release):
    rows = _query(
        conn,
        schema,
        """SELECT release_id,manifest_json,manifest_sha256,
        manifest_text,input_manifest_sha256 FROM {s}.release_metadata""",
    ).fetchall()
    require(
        rows
        == [
            (
                release.version,
                json.loads(release.canonical_json),
                release.sha256,
                release.manifest_json,
                release.manifest_sha256,
            )
        ],
        "Release metadata differs from pinned manifest",
    )
    rows = _query(
        conn,
        schema,
        """SELECT resource,version,manifest_text,manifest_sha256,
        manifest_json,parquet_checksums FROM {s}.resource_versions ORDER BY resource,version""",
    ).fetchall()
    expected = [
        (
            r.source,
            r.version,
            r.manifest_json,
            r.manifest_sha256,
            json.loads(r.manifest_json),
            {name: item.sha256 for name, item in r.files.items()},
        )
        for r in release.resources
    ]
    require(rows == expected, "Resource versions/manifests/checksums differ from pinned selections")


def _compare_batch(conn, schema, table, source, version, expected):
    key = "entity_key" if table == "entities" else "relation_key"
    columns = COLUMNS[table]
    rows = conn.execute(
        sql.SQL("SELECT {} FROM {}.{} WHERE resource=%s AND version=%s AND {}=ANY(%s)").format(
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.Identifier(key),
        ),
        (source, version, [item[key] for item in expected]),
    ).fetchall()
    actual = {row[columns.index(key)]: dict(zip(columns, row, strict=True)) for row in rows}
    require(
        len(actual) == len(expected), f"Missing/duplicate {table} records in {source}@{version}"
    )
    for item in expected:
        projected = {
            name: item.get(name)
            for name in columns
            if name not in {"resource", "version", "record_json"}
        }
        projected.update(resource=source, version=version, record_json=item)
        require(
            actual.get(item[key]) == projected,
            f"Complete {table} record/scalar mismatch in {source}@{version}: {item[key]}",
        )


def _compare_resource(conn, schema, resource, batch_size):
    counts = dict.fromkeys(COLUMNS, 0)
    for table, path in (
        ("entities", resource.entities_path),
        ("relations", resource.relations_path),
    ):
        batch = []
        with closing(iter_rows(path, batch_size=batch_size)) as stream:
            for item in stream:
                counts[table] += 1
                counts["annotations"] += len(item.get("annotations") or [])
                if table == "entities":
                    counts["identifiers"] += len(item.get("identifiers") or [])
                else:
                    evidence = item.get("evidence") or []
                    counts["evidence"] += len(evidence)
                    counts["annotations"] += sum(
                        len(ev.get("annotations") or []) for ev in evidence
                    )
                batch.append(item)
                if len(batch) == batch_size:
                    _compare_batch(conn, schema, table, resource.source, resource.version, batch)
                    batch.clear()
        if batch:
            _compare_batch(conn, schema, table, resource.source, resource.version, batch)
    require(
        counts["entities"] == resource.files["entities.parquet"].rows,
        f"Entity manifest count differs: {resource.source}",
    )
    require(
        counts["relations"] == resource.files["relations.parquet"].rows,
        f"Relation manifest count differs: {resource.source}",
    )
    return counts


def _flat_invariants(conn, schema, source, version):
    params = (source, version)
    _guard(
        conn,
        schema,
        "Identifier occurrence/ordinal/value",
        """
        SELECT n.entity_key FROM {s}.entities n
        CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(n.record_json->'identifiers')='array'
            THEN n.record_json->'identifiers' ELSE '[]'::jsonb END) WITH ORDINALITY x(value,position)
        LEFT JOIN {s}.identifiers i ON (i.resource,i.version,i.entity_key,i.ordinal)=
            (n.resource,n.version,n.entity_key,x.position-1)
        WHERE n.resource=%s AND n.version=%s AND (i.entity_key IS NULL OR
            to_jsonb(i)-ARRAY['resource','version','entity_key','ordinal'] IS DISTINCT FROM x.value)
        LIMIT 1""",
        params,
    )
    _guard(
        conn,
        schema,
        "Evidence occurrence/ordinal/value",
        """
        SELECT r.relation_key FROM {s}.relations r
        CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(r.record_json->'evidence')='array'
            THEN r.record_json->'evidence' ELSE '[]'::jsonb END) WITH ORDINALITY x(value,position)
        LEFT JOIN {s}.evidence e ON (e.resource,e.version,e.relation_key,e.ordinal)=
            (r.resource,r.version,r.relation_key,x.position-1)
        WHERE r.resource=%s AND r.version=%s AND (e.relation_key IS NULL OR
            e.record_json IS DISTINCT FROM x.value OR
            to_jsonb(e)-ARRAY['resource','version','relation_key','ordinal','record_json']
                IS DISTINCT FROM x.value-'annotations') LIMIT 1""",
        params,
    )
    _guard(
        conn,
        schema,
        "Annotation owner/ordinal/scope/value/quantity",
        """
        WITH owners AS (
            SELECT resource,version,'entity'::text AS kind,entity_key AS key,NULL::bigint AS ev,
                record_json->'annotations' AS items FROM {s}.entities WHERE resource=%s AND version=%s
            UNION ALL SELECT resource,version,'relation',relation_key,NULL::bigint,record_json->'annotations'
                FROM {s}.relations WHERE resource=%s AND version=%s
            UNION ALL SELECT resource,version,'evidence',relation_key,ordinal,record_json->'annotations'
                FROM {s}.evidence WHERE resource=%s AND version=%s
        )
        SELECT o.key FROM owners o
        CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(items)='array'
            THEN items ELSE '[]'::jsonb END) WITH ORDINALITY x(value,position)
        LEFT JOIN {s}.annotations a ON a.resource=o.resource AND a.version=o.version
            AND a.owner_kind=o.kind AND a.owner_key=o.key
            AND COALESCE(a.evidence_ordinal,-1)=COALESCE(o.ev,-1) AND a.ordinal=x.position-1
        WHERE a.annotation_id IS NULL OR jsonb_build_object('term',a.term,'value',a.value,
            'quantity',a.quantity,'source',a.source,'dataset',a.dataset,'scope',a.scope)
            IS DISTINCT FROM CASE WHEN o.kind='entity' THEN x.value||jsonb_build_object('scope',NULL)
                ELSE x.value END LIMIT 1""",
        params * 3,
    )
    _guard(
        conn,
        schema,
        "Declared evidence count",
        """
        SELECT r.relation_key FROM {s}.relations r WHERE r.resource=%s AND r.version=%s
        AND r.evidence_count IS DISTINCT FROM jsonb_array_length(CASE WHEN
            jsonb_typeof(r.record_json->'evidence')='array' THEN r.record_json->'evidence'
            ELSE '[]'::jsonb END) LIMIT 1""",
        params,
    )


def _catalog(conn, schema):
    # Fix deparser visibility so built-in operators/classes and the destination
    # namespace have deterministic qualification; this changes no stored data.
    conn.execute("SET LOCAL search_path=pg_catalog")
    qualified = conn.execute("SELECT quote_ident(%s)", (schema,)).fetchone()[0]
    columns = conn.execute(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=%s",
        (schema,),
    ).fetchall()
    require(
        not any(table == "payloads" or column == "payload_json" for table, column in columns),
        "Raw payload table/column remains in PostgreSQL",
    )
    rows = conn.execute(
        """SELECT t.relname,i.relname,x.indisvalid,x.indisready,x.indisunique,pg_get_indexdef(i.oid)
        FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s""",
        (schema,),
    ).fetchall()
    indexes = {row[1]: row for row in rows}
    expected_indexes = (*_INDEXES, *_DERIVED_INDEXES)
    for name, table, expression in expected_indexes:
        expected = f"CREATE INDEX {name} ON {qualified}.{table} USING btree ({expression})"
        require(
            name in indexes
            and indexes[name][0] == table
            and all(indexes[name][2:4])
            and not indexes[name][4]
            and _normalized_definition(indexes[name][5]) == _normalized_definition(expected),
            f"Required query index missing/invalid/wrong definition: {name}",
        )
    name = "annotations_owner_ordinal_idx"
    expected = (
        f"CREATE UNIQUE INDEX {name} ON {qualified}.annotations USING btree "
        "(resource, version, owner_kind, owner_key, "
        "COALESCE(evidence_ordinal, ('-1'::integer)::bigint), ordinal)"
    )
    require(
        name in indexes
        and indexes[name][0] == "annotations"
        and all(indexes[name][2:5])
        and _normalized_definition(indexes[name][5]) == _normalized_definition(expected),
        "Annotation occurrence unique index missing/invalid/wrong definition",
    )
    constraints = conn.execute(
        """SELECT t.relname,c.contype,c.convalidated,c.condeferrable,c.condeferred,
        f.relname,ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num,pos)
            JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.pos),
        ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num,pos)
            JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.pos),
        pg_get_constraintdef(c.oid),fn.nspname,c.connoinherit
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_class f ON f.oid=c.confrelid
        LEFT JOIN pg_namespace fn ON fn.oid=f.relnamespace
        WHERE n.nspname=%s""",
        (schema,),
    ).fetchall()
    require(all(row[2] for row in constraints), "Unvalidated constraint in release schema")
    primary_keys = {
        "resource_versions": ["resource", "version"],
        "release_metadata": ["release_id"],
        "entities": ["resource", "version", "entity_key"],
        "relations": ["resource", "version", "relation_key"],
        "identifiers": ["resource", "version", "entity_key", "ordinal"],
        "evidence": ["resource", "version", "relation_key", "ordinal"],
        "annotations": ["annotation_id"],
        "entity_ontology_relation": ["resource", "version", "relation_id"],
        "ontology_closure": [
            "resource",
            "version",
            "hierarchy_kind",
            "descendant_entity_id",
            "ancestor_entity_id",
        ],
        "ontology_ancestor": ["hierarchy_kind", "descendant_entity_id", "ancestor_entity_id"],
        "entity_ontology_term": ["term_entity_id"],
        "reaction_context": ["context_id"],
        "reaction_participant": ["context_id", "ordinal"],
        "entity_relation_counts": ["entity_id"],
        "entity_source_count": ["entity_id"],
        "resource_overlap_summary": ["source_a", "source_b", "content_kind"],
    }
    for table, keys in primary_keys.items():
        require(
            any(
                row[0] == table
                and row[1] == "p"
                and row[6] == keys
                and not row[3]
                and not row[4]
                and row[8] == f"PRIMARY KEY ({', '.join(keys)})"
                for row in constraints
            ),
            f"Primary key contract missing: {table}",
        )
    foreign_keys = (
        [
            (table, ["resource", "version"], "resource_versions", ["resource", "version"])
            for table in ("entities", "relations", "annotations")
        ]
        + [
            (
                "relations",
                ["resource", "version", key],
                "entities",
                ["resource", "version", "entity_key"],
            )
            for key in ("subject_entity_key", "object_entity_key")
        ]
        + [
            (
                "identifiers",
                ["resource", "version", "entity_key"],
                "entities",
                ["resource", "version", "entity_key"],
            ),
            (
                "evidence",
                ["resource", "version", "relation_key"],
                "relations",
                ["resource", "version", "relation_key"],
            ),
        ]
    )
    immediate_foreign_keys = (
        (
            "reaction_context",
            ["resource", "version", "reaction_entity_id"],
            "entities",
            ["resource", "version", "entity_key"],
        ),
        ("reaction_participant", ["context_id"], "reaction_context", ["context_id"]),
    )
    for deferred, keys_group in ((True, foreign_keys), (False, immediate_foreign_keys)):
        for table, keys, parent, parent_keys in keys_group:
            expected = (
                f"FOREIGN KEY ({', '.join(keys)}) REFERENCES "
                f"{qualified}.{parent}({', '.join(parent_keys)})"
                + (" DEFERRABLE INITIALLY DEFERRED" if deferred else "")
            )
            require(
                any(
                    row[0] == table
                    and row[1] == "f"
                    and row[5] == parent
                    and row[6] == keys
                    and row[7] == parent_keys
                    and row[9] == schema
                    and row[3] == deferred
                    and row[4] == deferred
                    and _normalized_definition(row[8]) == _normalized_definition(expected)
                    for row in constraints
                ),
                f"{'Deferred' if deferred else 'Immediate'} foreign key contract missing: {table}/{keys}",
            )
    expected_checks = (
        ("identifiers", "CHECK ((ordinal >= 0))"),
        ("evidence", "CHECK ((ordinal >= 0))"),
        ("annotations", "CHECK ((ordinal >= 0))"),
        (
            "annotations",
            "CHECK ((owner_kind = ANY (ARRAY['entity'::text, 'relation'::text, 'evidence'::text])))",
        ),
        (
            "annotations",
            "CHECK ((((owner_kind = 'evidence'::text) AND (evidence_ordinal IS NOT NULL) AND (evidence_ordinal >= 0)) OR ((owner_kind = ANY (ARRAY['entity'::text, 'relation'::text])) AND (evidence_ordinal IS NULL))))",
        ),
        ("ontology_closure", "CHECK ((depth > 0))"),
        ("ontology_ancestor", "CHECK ((depth > 0))"),
        (
            "reaction_participant",
            "CHECK ((role = ANY (ARRAY['reactant'::text, 'product'::text, 'enzyme'::text])))",
        ),
    )
    for table, definition in expected_checks:
        require(
            any(
                row[0] == table
                and row[1] == "c"
                and not row[3]
                and not row[4]
                and not row[10]
                and _normalized_definition(row[8]) == _normalized_definition(definition)
                for row in constraints
            ),
            f"Required check constraint missing/wrong definition: {table}/{definition}",
        )
    return {
        "required_query_indexes": len(_INDEXES),
        "required_derived_indexes": len(_DERIVED_INDEXES),
        "index_definitions": [row[5] for row in rows],
        "validated_constraints": len(constraints),
        "raw_storage_absent": True,
    }


def _ontology_checks(conn, schema):
    """Prove edge coverage and shortest-path fixed points without rebuilding."""
    _guard(
        conn,
        schema,
        "Ontology edge owner/predicate",
        """SELECT d.relation_id FROM {s}.entity_ontology_relation d
        LEFT JOIN {s}.relations r ON (r.resource,r.version,r.relation_key)=
            (d.resource,d.version,d.relation_id)
        WHERE r.relation_key IS NULL OR r.statement_kind IS DISTINCT FROM 'ontology'
            OR d.predicate IS DISTINCT FROM r.predicate
            OR d.subject_entity_id IS DISTINCT FROM r.subject_entity_key
            OR d.object_entity_id IS DISTINCT FROM r.object_entity_key LIMIT 1""",
    )
    # Distinct vocabulary terms are small metadata, not entity/relation rows.
    # Fetch them in bounded batches and compare expected orientation in SQL.
    with conn.cursor(name="audit_ontology_predicates") as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT DISTINCT predicate FROM {}.relations WHERE statement_kind='ontology' AND predicate IS NOT NULL"
            ).format(sql.Identifier(schema))
        )
        while predicates := cursor.fetchmany(256):
            policy = []
            for (predicate,) in predicates:
                reverse = hierarchy_direction(predicate)
                subclass = (
                    predicate == "is_a"
                    or is_descendant(predicate, "subclass_of")
                    or is_descendant(predicate, "superclass_of")
                )
                policy.extend((predicate, reverse, "subclass" if subclass else "part_of"))
            values = ",".join("(%s::text,%s::boolean,%s::text)" for _ in predicates)
            _guard(
                conn,
                schema,
                "Ontology edge coverage/orientation",
                """WITH policy(predicate,reverse,kind) AS (VALUES """
                + values
                + """)
                SELECT r.relation_key FROM {s}.relations r JOIN policy p USING(predicate)
                LEFT JOIN {s}.entity_ontology_relation d ON (d.resource,d.version,d.relation_id)=
                    (r.resource,r.version,r.relation_key)
                WHERE r.statement_kind='ontology' AND (
                    (p.reverse IS NULL AND d.relation_id IS NOT NULL)
                    OR (p.reverse IS NOT NULL AND (d.relation_id IS NULL
                        OR d.hierarchy_kind IS DISTINCT FROM p.kind
                        OR d.child_entity_id IS DISTINCT FROM CASE WHEN p.reverse THEN r.object_entity_key ELSE r.subject_entity_key END
                        OR d.parent_entity_id IS DISTINCT FROM CASE WHEN p.reverse THEN r.subject_entity_key ELSE r.object_entity_key END))) LIMIT 1""",
                policy,
            )
    for table, scoped in (("ontology_closure", True), ("ontology_ancestor", False)):
        # Self paths are intentionally excluded, including cycles. Direct paths
        # plus complete one-step expansion prove coverage; a depth-decreasing
        # predecessor witness proves each stored path is real. Together the
        # expansion inequalities prove shortest depth, without recursion here.
        scope_ce = " AND c.resource=e.resource AND c.version=e.version" if scoped else ""
        scope_ne = " AND n.resource=e.resource AND n.version=e.version" if scoped else ""
        scope_pc = " AND p.resource=c.resource AND p.version=c.version" if scoped else ""
        _guard(
            conn,
            schema,
            f"{table} positive depth/nonself",
            f"SELECT descendant_entity_id FROM {{s}}.{table} WHERE depth<=0 OR descendant_entity_id=ancestor_entity_id LIMIT 1",
        )
        _guard(
            conn,
            schema,
            f"{table} direct edge coverage",
            f"""SELECT e.relation_id FROM {{s}}.entity_ontology_relation e
            LEFT JOIN {{s}}.{table} c ON c.descendant_entity_id=e.child_entity_id
                AND c.ancestor_entity_id=e.parent_entity_id AND c.hierarchy_kind=e.hierarchy_kind{scope_ce}
            WHERE e.child_entity_id<>e.parent_entity_id AND
                (c.descendant_entity_id IS NULL OR c.depth<>1) LIMIT 1""",
        )
        _guard(
            conn,
            schema,
            f"{table} path witness",
            f"""SELECT c.descendant_entity_id FROM {{s}}.{table} c WHERE
            (c.depth=1 AND NOT EXISTS(SELECT 1 FROM {{s}}.entity_ontology_relation e
                WHERE e.child_entity_id=c.descendant_entity_id AND e.parent_entity_id=c.ancestor_entity_id
                AND e.hierarchy_kind=c.hierarchy_kind{scope_ce}))
            OR (c.depth>1 AND NOT EXISTS(SELECT 1 FROM {{s}}.{table} p
                JOIN {{s}}.entity_ontology_relation e ON e.child_entity_id=p.ancestor_entity_id
                    AND e.hierarchy_kind=p.hierarchy_kind{scope_ce}
                WHERE p.descendant_entity_id=c.descendant_entity_id AND p.hierarchy_kind=c.hierarchy_kind
                    AND e.parent_entity_id=c.ancestor_entity_id AND p.depth=c.depth-1{scope_pc})) LIMIT 1""",
        )
        _guard(
            conn,
            schema,
            f"{table} complete shortest expansion",
            f"""SELECT c.descendant_entity_id FROM {{s}}.{table} c
            JOIN {{s}}.entity_ontology_relation e ON e.child_entity_id=c.ancestor_entity_id
                AND e.hierarchy_kind=c.hierarchy_kind{scope_ce}
            LEFT JOIN {{s}}.{table} n ON n.descendant_entity_id=c.descendant_entity_id
                AND n.ancestor_entity_id=e.parent_entity_id AND n.hierarchy_kind=c.hierarchy_kind{scope_ne}
            WHERE c.descendant_entity_id<>e.parent_entity_id AND
                (n.descendant_entity_id IS NULL OR n.depth>c.depth+1) LIMIT 1""",
        )
    return {
        "direct_edges": _query(
            conn, schema, "SELECT count(*) FROM {s}.entity_ontology_relation"
        ).fetchone()[0],
        "scoped_closure_rows": _query(
            conn, schema, "SELECT count(*) FROM {s}.ontology_closure"
        ).fetchone()[0],
        "release_closure_rows": _query(
            conn, schema, "SELECT count(*) FROM {s}.ontology_ancestor"
        ).fetchone()[0],
        "scope": "Exact direct-edge coverage/orientation and complete shortest closures verified; ontology display fields are not recomputed",
    }


def _reaction_provenance_checks(conn, schema):
    """Compare PG-only provenance, retaining one occurrence and event value sets.

    The source helpers consume published annotation values; they do not parse raw
    records or resolve IDs. No complete reaction/resource/release record list is
    collected. Event state holds only distinct direction/type/source/diagnostic
    strings and one stored context. Exact raw-direction serialization and the
    sorted, deduplicated diagnostic/source arrays follow the product contract.
    """
    current = None
    sources, directions, shapes, diagnostics = set(), set(), set(), set()
    counts = {"contexts": 0, "participant_occurrences": 0}

    def finish_context():
        if current is None:
            return
        shape, shape_diagnostics = _source_type(
            [{"annotations": [{"term": SOURCE_RECORD_TYPE, "value": value} for value in shapes]}]
        )
        direction, original, direction_diagnostics = direction_context(
            directions, current["resource"]
        )
        expected_diagnostics = diagnostics | set(shape_diagnostics) | set(direction_diagnostics)
        if not current["row_id"]:
            expected_diagnostics.add("unscoped_evidence_missing_row_id")
        expected = {
            "sources": sorted(sources),
            "direction": direction,
            "raw_direction": original,
            "source_record_type": shape,
            "diagnostics": sorted(expected_diagnostics),
        }
        for field, value in expected.items():
            require(
                current[field] == value,
                f"Reaction provenance {field} differs: {current['context_id']}",
            )
        counts["contexts"] += 1

    with conn.cursor(name="audit_reaction_provenance", row_factory=dict_row) as cursor:
        cursor.execute(
            sql.SQL("""
            SELECT c.context_id,c.resource,c.source,c.row_id,c.direction,c.raw_direction,
                c.source_record_type,c.sources,c.diagnostics,
                p.ordinal,p.member_ordinal,p.context_status,e.upstream_id,
                r.sources AS relation_sources,COALESCE(a.annotations,'[]'::jsonb) AS annotations
            FROM {s}.reaction_participant p JOIN {s}.reaction_context c USING(context_id)
            JOIN {s}.relations r ON (r.resource,r.version,r.relation_key)=
                (c.resource,c.version,p.relation_key)
            JOIN {s}.evidence e ON (e.resource,e.version,e.relation_key,e.ordinal)=
                (c.resource,c.version,p.relation_key,p.evidence_ordinal)
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(jsonb_build_object('term',a.term,'value',a.value,'quantity',a.quantity,
                    'source',a.source,'dataset',a.dataset,'scope',a.scope) ORDER BY a.ordinal) AS annotations
                FROM {s}.annotations a WHERE a.resource=e.resource AND a.version=e.version
                    AND a.owner_kind='evidence' AND a.owner_key=e.relation_key
                    AND COALESCE(a.evidence_ordinal,-1)=e.ordinal
            ) a ON true ORDER BY c.context_id,p.ordinal
        """).format(s=sql.Identifier(schema))
        )
        while rows := cursor.fetchmany(32):
            for row in rows:
                if current is None or row["context_id"] != current["context_id"]:
                    finish_context()
                    current = {
                        key: row[key]
                        for key in (
                            "context_id",
                            "resource",
                            "source",
                            "row_id",
                            "direction",
                            "raw_direction",
                            "source_record_type",
                            "sources",
                            "diagnostics",
                        )
                    }
                    sources = {row["resource"]}
                    if row["source"] is not None:
                        sources.add(row["source"])
                    directions, shapes, diagnostics = set(), set(), set()
                sources.update(
                    value for value in row["relation_sources"] or [] if value is not None
                )
                for annotation in row["annotations"]:
                    value = annotation["value"]
                    if value not in (None, ""):
                        if annotation["term"] == CONVERSION_DIRECTION:
                            directions.add(value)
                        elif annotation["term"] == SOURCE_RECORD_TYPE:
                            shapes.add(value)
                party = participant_context(row)
                for field in ("member_ordinal", "context_status"):
                    require(
                        row[field] == party[field],
                        f"Reaction provenance {field} differs: {row['context_id']}/{row['ordinal']}",
                    )
                diagnostics.update(party["diagnostics"])
                counts["participant_occurrences"] += 1
    finish_context()
    return counts


def _derived_checks(conn, schema):
    ontology = _ontology_checks(conn, schema)
    _guard(
        conn,
        schema,
        "Reaction context occurrence/entity",
        """SELECT c.context_id FROM {s}.reaction_context c
        LEFT JOIN {s}.entities e ON (e.resource,e.version,e.entity_key)=
            (c.resource,c.version,c.reaction_entity_id)
        LEFT JOIN LATERAL (SELECT min(ordinal) lo,max(ordinal) hi,count(*) n
            FROM {s}.reaction_participant p WHERE p.context_id=c.context_id) p ON true
        WHERE e.entity_key IS NULL OR e.entity_type IS DISTINCT FROM 'molecular_activity'
            OR c.namespace IS DISTINCT FROM e.namespace OR c.identifier IS DISTINCT FROM e.identifier
            OR c.taxon IS DISTINCT FROM e.taxon OR p.n=0 OR p.lo<>0 OR p.hi<>p.n-1
            OR (COALESCE(c.row_id,'')='' AND p.n<>1) LIMIT 1""",
    )
    with conn.cursor(name="audit_reaction_context_identity") as cursor:
        cursor.execute(
            sql.SQL("""SELECT c.context_id,c.resource,c.version,c.reaction_entity_id,
            c.source,c.dataset,c.row_id,p.relation_key,p.evidence_ordinal
            FROM {s}.reaction_context c JOIN {s}.reaction_participant p ON p.context_id=c.context_id AND p.ordinal=0""").format(
                s=sql.Identifier(schema)
            )
        )
        while rows := cursor.fetchmany(256):
            for row in rows:
                identity = list(row[1:7])
                if not row[6]:
                    identity.extend(row[7:9])
                digest = hashlib.sha256(
                    json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest()
                require(
                    row[0] == "reaction-context:" + digest,
                    f"Reaction context scope identity differs: {row[0]}",
                )
    _guard(
        conn,
        schema,
        "Reaction transport context",
        """SELECT c.context_id FROM {s}.reaction_context c WHERE c.transport IS DISTINCT FROM (
        COALESCE(c.resource IN ('rhea','recon3d','metatlas','human_gem') AND c.dataset='transport_reactions',false)
        OR EXISTS(SELECT 1 FROM {s}.reaction_participant i JOIN {s}.reaction_participant o
            ON o.context_id=i.context_id AND o.participant_entity_id=i.participant_entity_id
            WHERE i.context_id=c.context_id AND i.role='reactant' AND o.role='product'
                AND NULLIF(i.compartment,'') IS NOT NULL AND NULLIF(o.compartment,'') IS NOT NULL
                AND i.compartment<>o.compartment)) LIMIT 1""",
    )
    _guard(
        conn,
        schema,
        "Reaction source/evidence/role/hash",
        """
        SELECT p.context_id FROM {s}.reaction_participant p JOIN {s}.reaction_context c USING(context_id)
        LEFT JOIN {s}.relations r ON (r.resource,r.version,r.relation_key)=(c.resource,c.version,p.relation_key)
        LEFT JOIN {s}.evidence e ON (e.resource,e.version,e.relation_key,e.ordinal)=
            (c.resource,c.version,p.relation_key,p.evidence_ordinal)
        WHERE r.relation_key IS NULL OR e.relation_key IS NULL OR r.statement_kind<>'relation'
            OR r.subject_entity_key IS DISTINCT FROM c.reaction_entity_id
            OR r.object_entity_key IS DISTINCT FROM p.participant_entity_id
            OR p.role IS DISTINCT FROM CASE r.predicate WHEN 'has_input' THEN 'reactant'
                WHEN 'has_output' THEN 'product' WHEN 'enabled_by' THEN 'enzyme' END
            OR e.source IS DISTINCT FROM c.source OR e.dataset IS DISTINCT FROM c.dataset OR e.row_id IS DISTINCT FROM c.row_id
            OR c.source_record_sha256 !~ '^[0-9a-f]{{64}}$' OR (
                SELECT array_agg(DISTINCT a.value ORDER BY a.value) FROM {s}.annotations a
                WHERE a.resource=e.resource AND a.version=e.version
                AND a.owner_kind='evidence' AND a.owner_key=e.relation_key
                AND COALESCE(a.evidence_ordinal,-1)=e.ordinal AND a.term=%s AND starts_with(a.value,%s)
            ) IS DISTINCT FROM ARRAY[%s||c.source_record_sha256] LIMIT 1""",
        (SOURCE_RECORD_REFERENCE, SOURCE_RECORD_SHA256_PREFIX, SOURCE_RECORD_SHA256_PREFIX),
    )
    _guard(
        conn,
        schema,
        "Eligible reaction evidence coverage",
        """
        SELECT r.relation_key FROM {s}.relations r JOIN {s}.entities n ON
            (n.resource,n.version,n.entity_key)=(r.resource,r.version,r.subject_entity_key)
        JOIN {s}.evidence e ON (e.resource,e.version,e.relation_key)=(r.resource,r.version,r.relation_key)
        LEFT JOIN ({s}.reaction_participant p JOIN {s}.reaction_context c USING(context_id))
            ON p.relation_key=r.relation_key AND p.evidence_ordinal=e.ordinal
            AND c.resource=r.resource AND c.version=r.version
        WHERE n.entity_type='molecular_activity' AND r.statement_kind='relation'
            AND r.predicate IN ('has_input','has_output','enabled_by') AND c.context_id IS NULL LIMIT 1""",
    )
    eligible = _query(
        conn,
        schema,
        """SELECT count(*) FROM {s}.relations r JOIN {s}.entities n ON
        (n.resource,n.version,n.entity_key)=(r.resource,r.version,r.subject_entity_key)
        JOIN {s}.evidence e ON (e.resource,e.version,e.relation_key)=(r.resource,r.version,r.relation_key)
        WHERE n.entity_type='molecular_activity' AND r.statement_kind='relation'
        AND r.predicate IN ('has_input','has_output','enabled_by')""",
    ).fetchone()[0]
    require(
        _query(conn, schema, "SELECT count(*) FROM {s}.reaction_participant").fetchone()[0]
        == eligible,
        "Reaction participant occurrence count differs from eligible evidence",
    )
    _guard(
        conn,
        schema,
        "Reaction member compartment/coefficient annotations",
        """
        SELECT p.context_id FROM {s}.reaction_participant p JOIN {s}.reaction_context c USING(context_id)
        LEFT JOIN LATERAL (
            SELECT count(DISTINCT NULLIF(value,'')) FILTER(WHERE term=%s) AS locations,
                max(NULLIF(value,'')) FILTER(WHERE term=%s) AS compartment,
                count(DISTINCT NULLIF(value,'')) FILTER(WHERE term IN ('stoichiometry','biolink:stoichiometry')) AS coefficients,
                max(NULLIF(value,'')) FILTER(WHERE term IN ('stoichiometry','biolink:stoichiometry')) AS coefficient,
                jsonb_agg(jsonb_build_object('term',term,'value',value,'quantity',quantity,'source',source,
                    'dataset',dataset,'scope',scope) ORDER BY ordinal)
                    FILTER(WHERE term IN ('stoichiometry','biolink:stoichiometry')) AS assertions
            FROM {s}.annotations a WHERE a.resource=c.resource AND a.version=c.version
                AND a.owner_kind='evidence' AND a.owner_key=p.relation_key
                AND COALESCE(a.evidence_ordinal,-1)=p.evidence_ordinal
        ) a ON true
        WHERE p.compartment IS DISTINCT FROM CASE WHEN a.locations=1 THEN a.compartment END
            OR p.raw_stoichiometry IS DISTINCT FROM CASE WHEN a.coefficients=1 THEN a.coefficient END
            OR p.stoichiometry IS DISTINCT FROM CASE WHEN a.assertions IS NOT NULL
                THEN jsonb_build_object('annotations',a.assertions) END LIMIT 1""",
        (CELLULAR_LOCATION, CELLULAR_LOCATION),
    )
    _guard(
        conn,
        schema,
        "Reaction direction/type summary",
        """
        SELECT c.context_id FROM {s}.reaction_context c
        LEFT JOIN LATERAL (
            SELECT count(DISTINCT lower(replace(NULLIF(a.value,''),'-','_'))) FILTER(WHERE term=%s) AS directions,
                max(lower(replace(NULLIF(a.value,''),'-','_'))) FILTER(WHERE term=%s) AS direction,
                count(DISTINCT NULLIF(a.value,'')) FILTER(WHERE term=%s) AS shapes,
                max(NULLIF(a.value,'')) FILTER(WHERE term=%s) AS shape
            FROM {s}.reaction_participant p JOIN {s}.annotations a ON a.resource=c.resource AND a.version=c.version
                AND a.owner_kind='evidence' AND a.owner_key=p.relation_key
                AND COALESCE(a.evidence_ordinal,-1)=p.evidence_ordinal
            WHERE p.context_id=c.context_id
        ) a ON true
        WHERE c.source_record_type IS DISTINCT FROM CASE WHEN a.shapes=1 THEN a.shape END
            OR c.direction IS DISTINCT FROM CASE WHEN a.directions=1 THEN CASE
                WHEN a.direction IN ('reversible','left_to_right') THEN a.direction
                WHEN a.direction='right_to_left' AND lower(c.resource)='kegg' THEN 'left_to_right'
                END END LIMIT 1""",
        (CONVERSION_DIRECTION, CONVERSION_DIRECTION, SOURCE_RECORD_TYPE, SOURCE_RECORD_TYPE),
    )
    _guard(
        conn,
        schema,
        "Measurement view identity/provenance/values",
        """
        SELECT a.annotation_id FROM {s}.annotations a LEFT JOIN {s}.annotation_quantities q USING(annotation_id)
        WHERE a.quantity IS NOT NULL AND (q.annotation_id IS NULL OR q.quantity IS DISTINCT FROM a.quantity
            OR q.resource IS DISTINCT FROM a.resource OR q.version IS DISTINCT FROM a.version
            OR q.owner_kind IS DISTINCT FROM a.owner_kind OR q.owner_key IS DISTINCT FROM a.owner_key
            OR q.evidence_ordinal IS DISTINCT FROM a.evidence_ordinal OR q.ordinal IS DISTINCT FROM a.ordinal
            OR q.term IS DISTINCT FROM a.term OR q.value IS DISTINCT FROM a.value
            OR q.source IS DISTINCT FROM a.source OR q.dataset IS DISTINCT FROM a.dataset
            OR q.scope IS DISTINCT FROM a.scope
            OR q.has_numeric_value IS DISTINCT FROM (a.quantity->>'has_numeric_value')::double precision
            OR q.has_unit IS DISTINCT FROM a.quantity->>'has_unit'
            OR q.has_unit_prefix IS DISTINCT FROM a.quantity->>'has_unit_prefix'
            OR q.has_binary_relation IS DISTINCT FROM a.quantity->>'has_binary_relation'
            OR q.source_field IS DISTINCT FROM a.quantity->>'source_field'
            OR q.comparator IS DISTINCT FROM a.quantity->>'comparator') LIMIT 1""",
    )
    quantities = _query(
        conn, schema, "SELECT count(*) FROM {s}.annotations WHERE quantity IS NOT NULL"
    ).fetchone()[0]
    require(
        _query(conn, schema, "SELECT count(*) FROM {s}.annotation_quantities").fetchone()[0]
        == quantities,
        "Measurement view row count differs from quantity annotations",
    )
    return {
        "ontology": ontology,
        "reaction_provenance": _reaction_provenance_checks(conn, schema),
        "quantity_annotations": quantities,
        "reaction_contexts_by_source": _query(
            conn,
            schema,
            """SELECT resource,count(*),
            count(*) FILTER(WHERE direction IS NULL),count(*) FILTER(WHERE jsonb_array_length(diagnostics)>0)
            FROM {s}.reaction_context GROUP BY resource ORDER BY resource""",
        ).fetchall(),
        "reaction_participants_by_source_role": _query(
            conn,
            schema,
            """SELECT c.resource,p.role,count(*),
            count(*) FILTER(WHERE p.compartment IS NOT NULL),count(*) FILTER(WHERE p.stoichiometry IS NOT NULL)
            FROM {s}.reaction_participant p JOIN {s}.reaction_context c USING(context_id)
            GROUP BY c.resource,p.role ORDER BY c.resource,p.role""",
        ).fetchall(),
        "measurement_units_comparators_top_1000": _query(
            conn,
            schema,
            """SELECT resource,term,has_unit,has_unit_prefix,
            comparator,count(*) FROM {s}.annotation_quantities GROUP BY resource,term,has_unit,has_unit_prefix,comparator
            ORDER BY count(*) DESC,resource,term,has_unit,has_unit_prefix,comparator LIMIT 1000""",
        ).fetchall(),
        "measurement_profile_groups": _query(
            conn,
            schema,
            """SELECT count(*) FROM (
            SELECT 1 FROM {s}.annotation_quantities GROUP BY resource,term,has_unit,has_unit_prefix,comparator
            ) groups""",
        ).fetchone()[0],
    }


def _products(conn, schema, release_id, digest, pins):
    from omnipath_subsets.metsigdb.mapping import RESOURCES
    from omnipath_subsets.network_views import NETWORKS

    metadata = _query(
        conn,
        schema,
        "SELECT product,release_id,manifest_sha256,stats FROM {s}.subset_build_metadata ORDER BY product",
    ).fetchall()
    require(
        {row[0] for row in metadata} == {"metsigdb", "network_views", "cosmos"},
        "Missing subset build metadata",
    )
    require(
        all(row[1:3] == (release_id, digest) for row in metadata), "Subset release stamp differs"
    )
    memberships = _query(
        conn,
        schema,
        "SELECT resource,count(*),count(DISTINCT set_source_id) FROM {s}.metsigdb_membership GROUP BY resource ORDER BY resource",
    ).fetchall()
    _guard(
        conn,
        schema,
        "MetSigDB provenance/set size",
        """SELECT metabolite_entity_id FROM (
        SELECT *,count(*) OVER(PARTITION BY resource,set_source_id) AS actual_size FROM {s}.metsigdb_membership
        ) m WHERE build_id<>%s OR set_size<>actual_size LIMIT 1""",
        (digest,),
    )
    _guard(
        conn,
        schema,
        "COSMOS release stamp",
        "SELECT cosmos_edge_id FROM {s}.cosmos_edge WHERE build_id<>%s LIMIT 1",
        (digest,),
    )
    registry = _query(
        conn, schema, "SELECT name FROM {s}.network_registry ORDER BY name"
    ).fetchall()
    require(
        registry == [("liana",), ("metalinksdb",), ("reactions",)],
        "Network registry recipes missing",
    )
    stats = {row[0]: row[3] for row in metadata}
    actual_memberships = {resource: (count, sets) for resource, count, sets in memberships}
    for rule in RESOURCES:
        recorded = stats["metsigdb"]["resources"][rule.name]
        require(
            (recorded["memberships"], recorded["sets"])
            == actual_memberships.get(rule.name, (0, 0)),
            f"MetSigDB build metadata count differs: {rule.name}",
        )
    cosmos_edges = _query(conn, schema, "SELECT count(*) FROM {s}.cosmos_edge").fetchone()[0]
    require(stats["cosmos"]["edges"] == cosmos_edges, "COSMOS build metadata edge count differs")
    require(
        stats["network_views"]["presets"] == len(registry),
        "Network build metadata recipe count differs",
    )
    return {
        "build_stats": {row[0]: row[3] for row in metadata},
        "metsigdb_counts": memberships,
        "cosmos_edges": cosmos_edges,
        "missing_resources": {
            "metsigdb": {
                rule.name: [s for s in (rule.source, rule.hierarchy_source) if s and s not in pins]
                for rule in RESOURCES
            },
            "networks": {d.name: sorted(set(d.included_sources) - pins) for d in NETWORKS},
        },
    }


def _baseline(conn, schema, baseline_schema, batch_size):
    validate_schema(baseline_schema)
    columns = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='reaction_context'",
        (baseline_schema,),
    ).fetchall()
    names = {row[0] for row in columns}
    if not {"payload_json", "context_id", "resource", "source", "dataset", "row_id"} <= names:
        return {
            "status": "not_comparable",
            "reason": "Baseline lacks original raw reaction context text",
        }
    statement = sql.SQL("""
        WITH current AS (SELECT *,count(*) OVER(PARTITION BY resource,source,dataset,row_id) AS same_row
            FROM {s}.reaction_context WHERE resource=ANY(%s) AND NULLIF(row_id,'') IS NOT NULL),
        baseline AS (SELECT *,count(*) OVER(PARTITION BY resource,source,dataset,row_id) AS same_row
            FROM {b}.reaction_context WHERE resource=ANY(%s) AND NULLIF(row_id,'') IS NOT NULL)
        SELECT c.resource,c.source,c.dataset,c.row_id,c.source_record_sha256,b.payload_json,
            c.direction,b.direction,c.transport,b.transport,
            (SELECT jsonb_agg(jsonb_build_array(p.member_ordinal,p.role,p.compartment,p.raw_stoichiometry)
                ORDER BY p.member_ordinal,p.role,p.compartment,p.raw_stoichiometry) FROM {s}.reaction_participant p WHERE p.context_id=c.context_id),
            (SELECT jsonb_agg(jsonb_build_array(p.member_ordinal,p.role,p.compartment,p.raw_stoichiometry)
                ORDER BY p.member_ordinal,p.role,p.compartment,p.raw_stoichiometry) FROM {b}.reaction_participant p WHERE p.context_id=b.context_id)
        FROM current c JOIN baseline b ON c.resource=b.resource AND c.source IS NOT DISTINCT FROM b.source
            AND c.dataset IS NOT DISTINCT FROM b.dataset AND c.row_id=b.row_id
        WHERE c.same_row=1 AND b.same_row=1
    """).format(s=sql.Identifier(schema), b=sql.Identifier(baseline_schema))
    stats, examples, by_source = Counter(), [], {}
    with conn.cursor(name="release_baseline") as cur:
        cur.execute(statement, (list(REACTION_SOURCES), list(REACTION_SOURCES)))
        while rows := cur.fetchmany(batch_size):
            for (
                source,
                reported,
                dataset,
                row_id,
                digest,
                body,
                direction,
                old_direction,
                transport,
                old_transport,
                members,
                old_members,
            ) in rows:
                stats["matched_unambiguous_source_rows"] += 1
                source_stats = by_source.setdefault(source, Counter())
                source_stats["matched_unambiguous_source_rows"] += 1
                if body is None or hashlib.sha256(body.encode("utf-8")).hexdigest() != digest:
                    stats["excluded_missing_or_changed_original_text"] += 1
                    source_stats["excluded_missing_or_changed_original_text"] += 1
                    continue
                stats["identical_source_text_rows"] += 1
                source_stats["identical_source_text_rows"] += 1
                differences = [
                    name
                    for name, before, after in (
                        ("direction", old_direction, direction),
                        ("transport", old_transport, transport),
                        ("member_positions_roles_compartments_coefficients", old_members, members),
                    )
                    if before != after
                ]
                if differences:
                    stats["rows_with_semantic_differences"] += 1
                    source_stats["rows_with_semantic_differences"] += 1
                    stats.update(differences)
                    if len(examples) < 10:
                        examples.append(
                            dict(
                                resource=source,
                                source=reported,
                                dataset=dataset,
                                row_id=row_id,
                                differences=differences,
                            )
                        )
    return {
        "status": "differences"
        if stats["rows_with_semantic_differences"]
        else "compared"
        if stats["identical_source_text_rows"]
        else "no_identical_comparable_rows",
        "counts": dict(stats),
        "matched_rows_by_resource": {source: dict(counts) for source, counts in by_source.items()},
        "examples": examples,
        "baseline_contexts_by_resource": conn.execute(
            sql.SQL(
                "SELECT resource,count(*) FROM {}.reaction_context GROUP BY resource ORDER BY resource"
            ).format(sql.Identifier(baseline_schema))
        ).fetchall(),
        "scope": "Only unique nonempty source row scopes with identical original text; canonical entity IDs, unmatched/ambiguous events and entire-release parity are excluded",
    }


def verify_postgres_release(
    database_url, data_root, manifest_path, *, schema, batch_size=256, baseline_schema=None
):
    validate_schema(schema)
    require(
        isinstance(batch_size, int)
        and not isinstance(batch_size, bool)
        and 1 <= batch_size <= 1024,
        "batch_size must be an integer from 1 to 1024",
    )
    started = perf_counter()
    release = load_pinned_release(data_root, manifest_path)
    report = {
        "status": "success",
        "mode": "read_only",
        "schema": schema,
        "release": release.version,
        "manifest_sha256": release.sha256,
        "batch_size": batch_size,
        "resources": {},
        "scope": "All base records/scalars, all nested occurrence values/ordinals, derived provenance and product stamps; no source parser/resolver",
    }
    with psycopg.connect(database_url, autocommit=True) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _attest_metadata(conn, schema, release)
        report["catalog"] = _catalog(conn, schema)
        actual_counts = _counts(conn, schema)
        expected_pins = {(r.source, r.version) for r in release.resources}
        require(set(actual_counts) <= expected_pins, "Unpinned resource/version rows stored")
        for resource in release.resources:
            tick = perf_counter()
            counts = _compare_resource(conn, schema, resource, batch_size)
            require(
                actual_counts.get((resource.source, resource.version), dict.fromkeys(COLUMNS, 0))
                == counts,
                f"Stored/nested projection counts differ: {resource.source}@{resource.version}",
            )
            _flat_invariants(conn, schema, resource.source, resource.version)
            report["resources"][resource.source] = {
                "version": resource.version,
                "stored_counts": counts,
                "source_payload_rows": resource.files["evidence_payloads.parquet"].rows,
                "seconds": round(perf_counter() - tick, 3),
            }
            print(
                json.dumps({"verified_resource": resource.source, "counts": counts}),
                file=sys.stderr,
                flush=True,
            )
        report["derived"] = _derived_checks(conn, schema)
        report["products"] = _products(
            conn, schema, release.version, release.sha256, {r.source for r in release.resources}
        )
        if baseline_schema:
            report["baseline"] = _baseline(conn, schema, baseline_schema, batch_size)
        verify_release(release)
    report["seconds"] = round(perf_counter() - started, 3)
    return report


def pg_only_rebuild(database_url, *, schema):
    """Exercise real PG-only builders/queries, then roll back every mutation."""
    validate_schema(schema)
    from omnipath_postgres.reactions import rebuild_reactions
    from omnipath_subsets import cosmos, metsigdb, network_views

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (schema,))
            stamps = _query(
                conn, schema, "SELECT release_id,manifest_sha256 FROM {s}.release_metadata"
            ).fetchall()
            require(len(stamps) == 1, "PG-only check needs one release")
            counts = _counts(conn, schema)
            stats = {
                "reactions": rebuild_reactions(conn, schema),
                "metsigdb": metsigdb.rebuild(conn, schema),
                "network_views": network_views.rebuild(conn, schema),
                "cosmos": cosmos.rebuild(conn, schema),
            }
            pages = {
                name: network_views.query(conn, schema, name, limit=1)
                for name in ("liana", "metalinksdb", "reactions")
            }
            require(_counts(conn, schema) == counts, "PG-only rebuild changed base rows")
            require(
                _query(
                    conn, schema, "SELECT release_id,manifest_sha256 FROM {s}.release_metadata"
                ).fetchall()
                == stamps,
                "PG-only rebuild changed release",
            )
            report = {
                "status": "success",
                "mode": "pg_only_rebuild",
                "schema": schema,
                "release": stamps[0][0],
                "manifest_sha256": stamps[0][1],
                "rolled_back": True,
                "products": stats,
                "network_query_source_rows": {
                    name: page["source_records"] for name, page in pages.items()
                },
                "scope": "Real PG-only rebuild/query with no artifact paths supplied; caller manages Parquet availability; all writes rolled back",
            }
        finally:
            conn.rollback()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--baseline-schema")
    parser.add_argument("--pg-only-rebuild", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    database_url = os.environ.get("OMNIPATH_DATABASE_URL")
    parser.error("Set OMNIPATH_DATABASE_URL") if not database_url else None
    if args.pg_only_rebuild and (args.data_root or args.manifest or args.baseline_schema):
        parser.error("PG-only mode takes no artifact paths or baseline")
    if not args.pg_only_rebuild and (not args.data_root or not args.manifest):
        parser.error("Read-only mode requires --data-root and --manifest")
    try:
        report = (
            pg_only_rebuild(database_url, schema=args.schema)
            if args.pg_only_rebuild
            else verify_postgres_release(
                database_url,
                args.data_root,
                args.manifest,
                schema=args.schema,
                batch_size=args.batch_size,
                baseline_schema=args.baseline_schema,
            )
        )
        code = 0
    except Exception as exc:
        report = {
            "status": "failed",
            "schema": args.schema,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        code = 1
    encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
