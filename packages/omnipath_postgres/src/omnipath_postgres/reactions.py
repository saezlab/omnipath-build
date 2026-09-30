"""Source-event reaction contexts over resolved Biolink participant relations.

This module reads preserved inputs_v2 payload fields, never reparses a source or
resolves an identifier. Membership ordinals are those published by the extractor.
Compartment lookup additionally checks the published alias and role, because an
invalid upstream member can shift ordinals. Unverifiable context stays unknown.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
from itertools import groupby
import json
import re
import uuid

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


ROLES = {"has_input": "reactant", "has_output": "product", "enabled_by": "enzyme"}


def _parts(value):
    return str(value).split("||") if value not in (None, "") else []


def _normal_id(namespace, value):
    value = str(value)
    if namespace == "chebi":
        value = value.removeprefix("CHEBI:").removeprefix("chebi:")
    return namespace, value


def _members(raw, resource):
    """Documented Rhea, Recon3D/Human-GEM and KEGG membership field order."""
    if "participant_role" in raw:
        roles = _parts(raw.get("participant_role"))
        ids = _parts(raw.get("participant_chebi"))
        compartments = _parts(raw.get("participant_compartment"))
        return [
            dict(
                role=role,
                identifiers=[("chebi", ids[i])] if i < len(ids) else [],
                compartment=compartments[i] or None if i < len(compartments) else None,
                stoichiometry=None,
            )
            for i, role in enumerate(roles)
        ]
    members = []
    packed_namespace = {
        "recon3d": "bigg_metabolite",
        "metatlas": "human_gem_metabolite",
        "human_gem": "human_gem_metabolite",
    }.get(resource.lower())
    for side, role in (("reactant", "reactant"), ("product", "product")):
        packed = _parts(raw.get("reactants" if side == "reactant" else "products"))
        if packed:
            for entry in packed:
                fields = entry.split(":")
                members.append(
                    dict(
                        role=role,
                        identifiers=[(packed_namespace, fields[0])] if packed_namespace else [],
                        compartment=fields[1] or None if len(fields) >= 3 else None,
                        stoichiometry=fields[2] if len(fields) >= 3 else None,
                    )
                )
        else:
            ids = _parts(raw.get(f"{side}_kegg_id"))
            coefficients = _parts(raw.get(f"{side}_stoichiometry"))
            for i, identifier in enumerate(ids):
                members.append(
                    dict(
                        role=role,
                        identifiers=[("kegg", identifier)],
                        compartment=None,
                        stoichiometry=coefficients[i] if i < len(coefficients) else None,
                    )
                )
    return members


def direction_context(raw, resource):
    """Return effective direction, original assertion and explicit diagnostics.

    KEGG's <= parser has already oriented its inputs/outputs. Other unsupported
    assertions are retained but cannot justify a reverse COSMOS half.
    """
    assertions = [
        str(raw[key])
        for key in ("direction", "conversion_direction")
        if raw.get(key) not in (None, "")
    ]
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
            # Current supported parsers only orient this assertion in KEGG.
            diagnostics.append("unsupported_right_to_left_orientation")
            direction = None
    original = (
        assertions[0] if len(assertions) == 1 else json.dumps(assertions) if assertions else None
    )
    return direction, original, diagnostics


def participant_context(row, raw):
    """Retain exact coefficient annotations; attach only verified compartments."""
    match = re.fullmatch(
        re.escape(row["row_id"] or "") + r":member:(\d+)", row["upstream_id"] or ""
    )
    member_ordinal = int(match[1]) if match else None
    annotations = row["evidence_record"].get("annotations") or []
    coefficients = [
        a for a in annotations if a.get("term", "").removeprefix("biolink:") == "stoichiometry"
    ]
    compartment_annotations = {
        a.get("value")
        for a in annotations
        if a.get("term") == "biopax:cellularLocation" and a.get("value")
    }
    compartment = next(iter(compartment_annotations)) if len(compartment_annotations) == 1 else None
    status = "annotation" if compartment else "unavailable"
    raw_coefficient = None
    members = _members(raw, row["resource"])
    if member_ordinal is not None and member_ordinal < len(members):
        candidate = members[member_ordinal]
        published = {
            _normal_id(i.get("ns"), i["id"])
            for i in row["participant_record"].get("identifiers") or []
            if i.get("id") is not None
        }
        published.add(
            _normal_id(
                row["participant_record"].get("namespace"),
                row["participant_record"].get("identifier"),
            )
        )
        if candidate["role"] == ROLES[row["predicate"]] and published.intersection(
            _normal_id(namespace, value) for namespace, value in candidate["identifiers"]
        ):
            raw_coefficient = candidate["stoichiometry"]
            if not compartment and not compartment_annotations:
                compartment = candidate["compartment"]
                status = "verified_member_ordinal"
            elif compartment and candidate["compartment"] not in (None, compartment):
                status = "compartment_conflict"
                compartment = None
        elif not compartment:
            status = "unverified_member_ordinal"
    if len(compartment_annotations) > 1:
        status, compartment = "compartment_conflict", None
    return dict(
        member_ordinal=member_ordinal,
        compartment=compartment,
        stoichiometry={"annotations": coefficients} if coefficients else None,
        raw_stoichiometry=raw_coefficient,
        context_status=status,
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
    values = _scope_key(row)
    return (
        "reaction-context:"
        + hashlib.sha256(
            json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _ensure_tables(conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {s}.reaction_context (
                context_id text PRIMARY KEY, resource text NOT NULL, version text NOT NULL,
                reaction_entity_id text NOT NULL, source text, dataset text, row_id text,
                namespace text, identifier text, taxon text, direction text, raw_direction text,
                transport boolean NOT NULL, payload_json text, sources text[] NOT NULL,
                diagnostics jsonb NOT NULL,
                FOREIGN KEY (resource, version, reaction_entity_id)
                    REFERENCES {s}.entities (resource, version, entity_key)
            );
            CREATE TABLE IF NOT EXISTS {s}.reaction_participant (
                context_id text NOT NULL REFERENCES {s}.reaction_context (context_id),
                ordinal bigint NOT NULL, relation_key text NOT NULL, evidence_ordinal bigint NOT NULL,
                participant_entity_id text NOT NULL, role text NOT NULL
                    CHECK (role IN ('reactant', 'product', 'enzyme')),
                compartment text, stoichiometry jsonb, member_ordinal bigint,
                raw_stoichiometry text, context_status text NOT NULL,
                PRIMARY KEY (context_id, ordinal)
            );
            CREATE INDEX IF NOT EXISTS reaction_context_event_idx
                ON {s}.reaction_context (reaction_entity_id);
            CREATE INDEX IF NOT EXISTS reaction_participant_entity_idx
                ON {s}.reaction_participant (participant_entity_id);
            TRUNCATE {s}.reaction_participant, {s}.reaction_context;
        """).format(s=sql.Identifier(schema))
        )


def _context_rows(conn, schema):
    # Server cursor bounds the fetched rows; a single source event is grouped in
    # memory. Lateral payload lookup avoids multiplying duplicate evidence rows.
    with conn.cursor(name="reaction_" + uuid.uuid4().hex, row_factory=dict_row) as cur:
        cur.execute(
            sql.SQL("""
            SELECT r.resource, r.version, r.subject_entity_key AS reaction_entity_id,
                e.source, e.dataset, e.row_id, e.upstream_id, e.ordinal AS evidence_ordinal,
                e.record_json AS evidence_record, r.relation_key, r.predicate, r.sources,
                r.object_entity_key AS participant_entity_id,
                subject.namespace, subject.identifier, subject.taxon,
                object.record_json AS participant_record, p.payloads
            FROM {s}.relations r
            JOIN {s}.evidence e USING (resource, version, relation_key)
            JOIN {s}.entities subject ON subject.resource=r.resource AND subject.version=r.version
                AND subject.entity_key=r.subject_entity_key
            JOIN {s}.entities object ON object.resource=r.resource AND object.version=r.version
                AND object.entity_key=r.object_entity_key
            LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT payload_json) FILTER (WHERE payload_json IS NOT NULL) AS payloads
                FROM {s}.payloads p WHERE p.resource=e.resource AND p.version=e.version
                    AND p.relation_key=e.relation_key
                    AND p.source IS NOT DISTINCT FROM e.source AND p.row_id IS NOT DISTINCT FROM e.row_id
            ) p ON true
            WHERE subject.entity_type='molecular_activity' AND r.statement_kind='relation'
                AND r.predicate IN ('has_input', 'has_output', 'enabled_by')
            ORDER BY r.resource, r.version, r.subject_entity_key, e.source NULLS FIRST,
                e.dataset NULLS FIRST, e.row_id NULLS FIRST, e.upstream_id NULLS FIRST,
                r.relation_key, e.ordinal
        """).format(s=sql.Identifier(schema))
        )
        while rows := cur.fetchmany(256):
            yield from rows


def rebuild_reactions(conn, schema: str) -> dict:
    """Rebuild shared contexts/parties without committing the caller's transaction."""
    _ensure_tables(conn, schema)
    stats = defaultdict(int)
    fields = ("resource", "version", "reaction_entity_id", "source", "dataset", "row_id")
    with conn.cursor() as cur:
        for _, records in groupby(_context_rows(conn, schema), key=_scope_key):
            records = list(records)
            first = records[0]
            payloads = {value for row in records for value in row["payloads"] or []}
            if len(payloads) > 1:
                raise ValueError(
                    f"Conflicting payloads for reaction source event {context_id(first)}"
                )
            payload = next(iter(payloads), None)
            raw = json.loads(payload) if payload is not None else {}
            unsupported_shape = not isinstance(raw, dict)
            if unsupported_shape:
                raw = {}
            direction, original, diagnostics = direction_context(raw, first["resource"])
            if unsupported_shape:
                diagnostics.append("unsupported_payload_shape")
            if not first["row_id"]:
                diagnostics.append("unscoped_evidence_missing_row_id")
            parties = [participant_context(row, raw) for row in records]
            compartments = defaultdict(lambda: defaultdict(set))
            for row, party in zip(records, parties, strict=True):
                if party["compartment"] and row["predicate"] in ("has_input", "has_output"):
                    compartments[row["participant_entity_id"]][row["predicate"]].add(
                        party["compartment"]
                    )
                if party["context_status"] in ("compartment_conflict", "unverified_member_ordinal"):
                    diagnostics.append(party["context_status"])
            transport = (
                first["resource"] in ("rhea", "recon3d", "metatlas", "human_gem")
                and first["dataset"] == "transport_reactions"
            ) or any(
                any(a != b for a in sides["has_input"] for b in sides["has_output"])
                for sides in compartments.values()
            )
            sources = sorted(
                {value for row in records for value in (row["sources"] or []) if value is not None}
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
                payload,
                sources,
                Jsonb(sorted(set(diagnostics))),
            ]
            cur.execute(
                sql.SQL("INSERT INTO {}.reaction_context VALUES ({})").format(
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
                    sql.SQL("INSERT INTO {}.reaction_participant VALUES ({})").format(
                        sql.Identifier(schema), sql.SQL(",").join(sql.Placeholder() for _ in values)
                    ),
                    values,
                )
                stats["participants"] += 1
            stats["contexts"] += 1
            stats["contexts_with_diagnostics"] += bool(diagnostics)
    return {key: stats[key] for key in ("contexts", "participants", "contexts_with_diagnostics")}
