"""Reaction contexts derived only from published evidence-owned annotations.

No raw source table, source parser, reference or Parquet checkout is required at
rebuild time. Exact source-record SHA references keep distinct observations and
conflicting source events detectable after raw payloads have been omitted.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import hashlib
from itertools import groupby
import json
import re
import uuid

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from omnipath_core.source_attributes import (
    CELLULAR_LOCATION,
    CONVERSION_DIRECTION,
    SOURCE_RECORD_REFERENCE,
    SOURCE_RECORD_SHA256_PREFIX,
    SOURCE_RECORD_TYPE,
)

ROLES = {"has_input": "reactant", "has_output": "product", "enabled_by": "enzyme"}


def _values(annotations, term):
    return [
        a["value"]
        for a in annotations
        if a.get("term") == term and a.get("value") not in (None, "")
    ]


def direction_context(assertions, resource):
    """Preserve original assertions and diagnose unsupported/conflicting direction.

    KEGG RIGHT-TO-LEFT already oriented its has_input/has_output participants in
    inputs_v2, so it must not swap them a second time.
    """
    assertions = sorted(set(assertions))
    normalized = {item.lower().replace("-", "_") for item in assertions}
    diagnostics = []
    if len(normalized) > 1:
        diagnostics.append("contradictory_direction_assertions")
        direction = None
    else:
        direction = next(iter(normalized), None)
    if direction not in (None, "reversible", "left_to_right", "right_to_left"):
        diagnostics.append("unsupported_direction_assertion")
        direction = None
    if direction == "right_to_left":
        if resource.lower() == "kegg":
            direction = "left_to_right"
            diagnostics.append("kegg_inputs_already_oriented_right_to_left")
        else:
            diagnostics.append("unsupported_right_to_left_orientation")
            direction = None
    original = (
        assertions[0] if len(assertions) == 1 else json.dumps(assertions) if assertions else None
    )
    return direction, original, diagnostics


def participant_context(row):
    """Copy exact published coefficient annotations and explicit compartments."""
    match = re.fullmatch(
        re.escape(row["row_id"] or "") + r":member:(\d+)", row["upstream_id"] or ""
    )
    annotations = row["annotations"]
    coefficients = [
        a for a in annotations if (a.get("term") or "").removeprefix("biolink:") == "stoichiometry"
    ]
    locations = set(_values(annotations, CELLULAR_LOCATION))
    compartment = next(iter(locations)) if len(locations) == 1 else None
    status = "annotation" if compartment else "unavailable"
    diagnostics = []
    if len(locations) > 1:
        status = "compartment_conflict"
        diagnostics.append(status)
    values = {a["value"] for a in coefficients if a.get("value") not in (None, "")}
    if len(values) > 1:
        diagnostics.append("contradictory_stoichiometry_assertions")
    return dict(
        member_ordinal=int(match[1]) if match else None,
        compartment=compartment,
        stoichiometry={"annotations": coefficients} if coefficients else None,
        raw_stoichiometry=next(iter(values)) if len(values) == 1 else None,
        context_status=status,
        diagnostics=diagnostics,
    )


def _scope_key(row):
    values = [
        row[field]
        for field in ("resource", "version", "reaction_entity_id", "source", "dataset", "row_id")
    ]
    if not row["row_id"]:
        values.extend((row["relation_key"], row["evidence_ordinal"]))
    return tuple(values)


def context_id(row):
    return (
        "reaction-context:"
        + hashlib.sha256(
            json.dumps(_scope_key(row), ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _source_hash(row):
    values = _values(row["annotations"], SOURCE_RECORD_REFERENCE)
    hashes = set()
    for value in values:
        if not value.startswith(SOURCE_RECORD_SHA256_PREFIX):
            continue  # Other provenance links are retained but are not record hashes.
        digest = value.removeprefix(SOURCE_RECORD_SHA256_PREFIX)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(
                f"Invalid reaction source-record SHA reference for {row['resource']}@{row['version']} {row['relation_key']}"
            )
        hashes.add(digest)
    if len(hashes) != 1:
        raise ValueError(
            f"Reaction evidence requires exactly one source-record SHA reference in {row['resource']}@{row['version']} {row['relation_key']}; rebuild the resource with evidence source attributes (legacy raw-payload-only reaction releases are unsupported)"
        )
    return next(iter(hashes))


def _source_type(records):
    values = {value for row in records for value in _values(row["annotations"], SOURCE_RECORD_TYPE)}
    if len(values) > 1:
        return None, ["contradictory_source_record_types"]
    value = next(iter(values), None)
    if value not in ("object", "array", "scalar"):
        return value, [
            "unrecorded_source_record_type" if value is None else "unsupported_source_record_type"
        ]
    return value, ["unsupported_payload_shape"] if value != "object" else []


def _ensure_tables(conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {s}.reaction_context (
                context_id text PRIMARY KEY, resource text NOT NULL, version text NOT NULL,
                reaction_entity_id text NOT NULL, source text, dataset text, row_id text,
                namespace text, identifier text, taxon text, direction text, raw_direction text,
                transport boolean NOT NULL, source_record_sha256 text NOT NULL,
                source_record_type text, sources text[] NOT NULL, diagnostics jsonb NOT NULL,
                FOREIGN KEY (resource, version, reaction_entity_id)
                    REFERENCES {s}.entities (resource, version, entity_key)
            );
            CREATE TABLE IF NOT EXISTS {s}.reaction_participant (
                context_id text NOT NULL REFERENCES {s}.reaction_context (context_id),
                ordinal bigint NOT NULL, relation_key text NOT NULL, evidence_ordinal bigint NOT NULL,
                participant_entity_id text NOT NULL, role text NOT NULL CHECK(role IN ('reactant','product','enzyme')),
                compartment text, stoichiometry jsonb, member_ordinal bigint,
                raw_stoichiometry text, context_status text NOT NULL,
                PRIMARY KEY(context_id,ordinal)
            );
            CREATE INDEX IF NOT EXISTS reaction_context_event_idx ON {s}.reaction_context(reaction_entity_id);
            CREATE INDEX IF NOT EXISTS reaction_participant_entity_idx ON {s}.reaction_participant(participant_entity_id);
            TRUNCATE {s}.reaction_participant, {s}.reaction_context;
        """).format(s=sql.Identifier(schema))
        )


def _context_rows(conn, schema):
    with conn.cursor(name="reaction_" + uuid.uuid4().hex, row_factory=dict_row) as cur:
        cur.execute(
            sql.SQL("""
            SELECT r.resource,r.version,r.subject_entity_key AS reaction_entity_id,
                e.source,e.dataset,e.row_id,e.upstream_id,e.ordinal AS evidence_ordinal,
                r.relation_key,r.predicate,r.sources,r.object_entity_key AS participant_entity_id,
                subject.namespace,subject.identifier,subject.taxon,
                COALESCE(a.annotations,'[]'::jsonb) AS annotations
            FROM {s}.relations r
            JOIN {s}.evidence e USING(resource,version,relation_key)
            JOIN {s}.entities subject ON subject.resource=r.resource AND subject.version=r.version
                AND subject.entity_key=r.subject_entity_key
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(jsonb_build_object('term',a.term,'value',a.value,'quantity',a.quantity,
                    'source',a.source,'dataset',a.dataset,'scope',a.scope) ORDER BY a.ordinal) AS annotations
                FROM {s}.annotations a WHERE a.owner_kind='evidence' AND a.resource=e.resource
                    AND a.version=e.version AND a.owner_key=e.relation_key AND a.evidence_ordinal=e.ordinal
            ) a ON true
            WHERE subject.entity_type='molecular_activity' AND r.statement_kind='relation'
                AND r.predicate IN ('has_input','has_output','enabled_by')
            ORDER BY r.resource,r.version,r.subject_entity_key,e.source NULLS FIRST,
                e.dataset NULLS FIRST,e.row_id NULLS FIRST,e.upstream_id NULLS FIRST,r.relation_key,e.ordinal
        """).format(s=sql.Identifier(schema))
        )
        while rows := cur.fetchmany(256):
            yield from rows


def rebuild_reactions(conn, schema: str) -> dict:
    """Rebuild source-scoped reactions exclusively from PostgreSQL annotations."""
    _ensure_tables(conn, schema)
    stats = defaultdict(int)
    fields = ("resource", "version", "reaction_entity_id", "source", "dataset", "row_id")
    with closing(_context_rows(conn, schema)) as stream, conn.cursor() as cur:
        for _, rows in groupby(stream, key=_scope_key):
            records = list(rows)
            first = records[0]
            hashes = {_source_hash(row) for row in records}
            if len(hashes) != 1:
                raise ValueError(
                    f"Conflicting source record SHA references for reaction source event {context_id(first)}"
                )
            shape, diagnostics = _source_type(records)
            direction, original, direction_diagnostics = direction_context(
                [
                    value
                    for row in records
                    for value in _values(row["annotations"], CONVERSION_DIRECTION)
                ],
                first["resource"],
            )
            diagnostics.extend(direction_diagnostics)
            if not first["row_id"]:
                diagnostics.append("unscoped_evidence_missing_row_id")
            parties = [participant_context(row) for row in records]
            compartments = defaultdict(lambda: defaultdict(set))
            for row, party in zip(records, parties, strict=True):
                diagnostics.extend(party["diagnostics"])
                if party["compartment"] and row["predicate"] in ("has_input", "has_output"):
                    compartments[row["participant_entity_id"]][row["predicate"]].add(
                        party["compartment"]
                    )
            transport = (
                first["resource"] in ("rhea", "recon3d", "metatlas", "human_gem")
                and first["dataset"] == "transport_reactions"
            ) or any(
                any(a != b for a in sides["has_input"] for b in sides["has_output"])
                for sides in compartments.values()
            )
            sources = sorted(
                {value for row in records for value in row["sources"] or [] if value is not None}
                | {first["resource"]}
                | ({first["source"]} if first["source"] is not None else set())
            )
            identity = context_id(first)
            values = [
                identity,
                *[first[k] for k in fields],
                first["namespace"],
                first["identifier"],
                first["taxon"],
                direction,
                original,
                transport,
                next(iter(hashes)),
                shape,
                sources,
                Jsonb(sorted(set(diagnostics))),
            ]
            cur.execute(
                sql.SQL("INSERT INTO {}.reaction_context VALUES({})").format(
                    sql.Identifier(schema), sql.SQL(",").join(sql.Placeholder() for _ in values)
                ),
                values,
            )
            for ordinal, (row, party) in enumerate(zip(records, parties, strict=True)):
                values = [
                    identity,
                    ordinal,
                    row["relation_key"],
                    row["evidence_ordinal"],
                    row["participant_entity_id"],
                    ROLES[row["predicate"]],
                    party["compartment"],
                    Jsonb(party["stoichiometry"]) if party["stoichiometry"] is not None else None,
                    party["member_ordinal"],
                    party["raw_stoichiometry"],
                    party["context_status"],
                ]
                cur.execute(
                    sql.SQL("INSERT INTO {}.reaction_participant VALUES({})").format(
                        sql.Identifier(schema), sql.SQL(",").join(sql.Placeholder() for _ in values)
                    ),
                    values,
                )
                stats["participants"] += 1
            stats["contexts"] += 1
            stats["contexts_with_diagnostics"] += bool(diagnostics)
    return {key: stats[key] for key in ("contexts", "participants", "contexts_with_diagnostics")}
