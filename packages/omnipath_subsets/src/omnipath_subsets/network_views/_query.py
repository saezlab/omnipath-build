"""Query supported network recipes without inventing biological classifications.

Binary collapse uses the published statement key. Published records, qualifiers,
measurements and selected evidence occurrences travel with each result. Reaction
transport is a separate derived assertion with its supporting statements attached.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from typing import Any
import uuid

from psycopg import sql

from omnipath_core.source_attributes import PARTICIPANT_ROLE

from ._definitions import NETWORKS

CHEMICAL_TYPES = ("chemical_entity", "small_molecule")
PROTEIN_TYPES = ("protein", "gene", "macromolecular_complex", "protein_family")
LIMITATIONS = {
    "metalinksdb": [
        "Binary collapse retains published statement keys, rather than merging distinct qualified statements by endpoints.",
        "Supported binary rows have a chemical endpoint and a protein, gene, protein family or complex counterpart.",
        "Intercell attributes are resource entity annotations, without a cross-resource role or location consensus.",
        "Reaction transport requires an explicit enzyme and the same chemical in distinct known input/output compartments; GPR associations do not establish catalysis.",
    ],
    "liana": [
        "The current Biolink records do not publish a ligand_receptor biological class; the adapter selects ConnectomeDB2025 interacts_with assertions.",
        "Ligand/receptor orientation requires explicit endpoint role annotations within one evidence occurrence; missing or contradictory roles retain published endpoint order.",
        "Binary collapse retains published statement keys instead of merging by endpoints.",
    ],
    "reactions": [],
}


def _definition(name: str):
    for definition in NETWORKS:
        if definition.name == name:
            return definition
    raise ValueError(f"Unknown network preset: {name}")


def _taxon(value: Any) -> str:
    return str(value or "").removeprefix("NCBITaxon:").strip()


def _organism(value: Any) -> str | None:
    if value is None:
        return None
    result = _taxon(value)
    if isinstance(value, bool) or not result.isdigit():
        raise ValueError("organism must be a numeric NCBI taxon or NCBITaxon CURIE")
    return result


def _row_stream(conn, statement, params) -> Iterator[tuple]:
    with conn.cursor(name="network_" + uuid.uuid4().hex) as cur:
        cur.execute(statement, params)
        while batch := cur.fetchmany(128):
            yield from batch


def _binary_rows(conn, schema: str, name: str, organism: str | None):
    definition = _definition(name)
    if name == "liana":
        scope = sql.SQL("r.resource = ANY(%s) AND r.predicate = 'interacts_with'")
        params = [list(definition.included_sources)]
    else:
        curated = definition.composition["components"][1]["parameters"]["filters"]["resources"]
        scope = sql.SQL("""
            (r.subject_type = ANY(%s) OR r.object_type = ANY(%s))
            AND (r.subject_type = ANY(%s) OR r.object_type = ANY(%s))
            AND (r.resource = ANY(%s) OR
                 (r.resource = 'chembl' AND EXISTS (
                     SELECT 1 FROM {s}.evidence ev
                     WHERE (ev.resource, ev.version, ev.relation_key) =
                           (r.resource, r.version, r.relation_key)
                       AND ev.dataset = 'mechanisms')))
        """).format(s=sql.Identifier(schema))
        params = [
            list(CHEMICAL_TYPES),
            list(CHEMICAL_TYPES),
            list(PROTEIN_TYPES),
            list(PROTEIN_TYPES),
            curated,
        ]
    statement = sql.SQL("""
        SELECT r.resource, r.version, r.relation_key, r.record_json,
               subject.record_json, object.record_json,
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                   'evidence_ordinal', a.evidence_ordinal,
                   'scope', a.scope, 'value', a.value
               ) ORDER BY a.evidence_ordinal, a.ordinal) FROM {s}.annotations a
                 WHERE (a.resource, a.version, a.owner_key) =
                       (r.resource, r.version, r.relation_key)
                   AND a.owner_kind = 'evidence' AND a.term = %s), '[]'::jsonb)
        FROM {s}.relations r
        JOIN {s}.entities subject ON (subject.resource, subject.version, subject.entity_key) =
                                    (r.resource, r.version, r.subject_entity_key)
        JOIN {s}.entities object ON (object.resource, object.version, object.entity_key) =
                                   (r.resource, r.version, r.object_entity_key)
        WHERE r.statement_kind = 'relation' AND ({scope})
          AND (%s::text IS NULL OR r.taxon = %s OR subject.taxon = %s OR object.taxon = %s)
        ORDER BY r.relation_key, r.resource, r.version
    """).format(s=sql.Identifier(schema), scope=scope)
    params = [PARTICIPANT_ROLE, *params, *([organism] * 4)]
    yield from _row_stream(conn, statement, params)


def _explicit_roles(subject: dict, object: dict, annotations: list[dict]) -> dict | None:
    """Use a complete role pair from one published evidence occurrence only."""
    occurrences = {}
    for item in annotations:
        scopes = occurrences.setdefault(item["evidence_ordinal"], {})
        scopes.setdefault(item.get("scope"), set()).add(item.get("value"))
    orientations = set()
    supporting = []
    for ordinal, scopes in sorted(occurrences.items()):
        before, after = scopes.get("subject", set()), scopes.get("object", set())
        if before == {"ligand"} and after == {"receptor"}:
            orientations.add((subject["entity_key"], object["entity_key"]))
        elif before == {"receptor"} and after == {"ligand"}:
            orientations.add((object["entity_key"], subject["entity_key"]))
        else:
            continue
        supporting.append(ordinal)
    if len(orientations) != 1:
        return None
    ligand, receptor = next(iter(orientations))
    return {
        "ligand_entity_id": ligand,
        "receptor_entity_id": receptor,
        "evidence_ordinals": supporting,
    }


def _binary_record(row: tuple, name: str) -> dict:
    resource, version, key, published, subject, object, role_annotations = row
    statement = deepcopy(published)
    evidence_ordinals = list(range(len(statement.get("evidence") or [])))
    if resource == "chembl":
        evidence_ordinals = [
            i
            for i, e in enumerate(statement.get("evidence") or [])
            if e.get("dataset") == "mechanisms"
        ]
        statement["evidence"] = [
            e for e in statement.get("evidence") or [] if e.get("dataset") == "mechanisms"
        ]
        statement["evidence_count"] = len(statement["evidence"])
        statement["sources"] = sorted(
            {e["source"] for e in statement["evidence"] if e.get("source") is not None}
        )
        statement["annotations"] = [
            a
            for a in statement.get("annotations") or []
            if a.get("dataset") == "mechanisms"
            or (a.get("scope") == "relation" and a.get("term", "").endswith("_qualifier"))
        ]
    result = {
        "kind": "published_statement",
        "resource": resource,
        "version": version,
        "relation_key": key,
        "statement": statement,
        "subject": subject,
        "object": object,
        "evidence_ordinals": evidence_ordinals,
    }
    if name == "liana":
        result["roles"] = _explicit_roles(subject, object, role_annotations)
    if name == "metalinksdb":
        result["intercell"] = {
            "subject": subject.get("annotations") or [],
            "object": object.get("annotations") or [],
        }
    return result


def _reaction_rows(conn, schema: str):
    statement = sql.SQL("""
        SELECT to_jsonb(c), event.record_json,
            COALESCE(jsonb_agg(jsonb_build_object(
                'member', to_jsonb(p), 'entity', entity.record_json, 'statement', r.record_json
            ) ORDER BY p.ordinal) FILTER (WHERE p.ordinal IS NOT NULL), '[]'::jsonb)
        FROM {s}.reaction_context c
        JOIN {s}.entities event ON (event.resource, event.version, event.entity_key) =
                                  (c.resource, c.version, c.reaction_entity_id)
        LEFT JOIN {s}.reaction_participant p USING (context_id)
        LEFT JOIN {s}.entities entity ON (entity.resource, entity.version, entity.entity_key) =
            (c.resource, c.version, p.participant_entity_id)
        LEFT JOIN {s}.relations r ON (r.resource, r.version, r.relation_key) =
                                   (c.resource, c.version, p.relation_key)
        WHERE c.resource = ANY(%s)
        GROUP BY c.context_id, event.record_json
        ORDER BY c.context_id
    """).format(s=sql.Identifier(schema))
    sources = _definition("reactions").included_sources
    yield from _row_stream(conn, statement, [list(sources)])


def _context_matches(context: dict, members: list[dict], organism: str | None) -> bool:
    return organism is None or organism in {
        _taxon(context.get("taxon")),
        *(_taxon((item.get("entity") or {}).get("taxon")) for item in members),
    }


def _transports(context: dict, event: dict, members: list[dict]) -> Iterator[dict]:
    if context["resource"] not in {"recon3d", "rhea", "metatlas"}:
        return
    enzymes, inputs, outputs = {}, {}, {}
    for item in members:
        member, statement, entity = (
            item["member"],
            item.get("statement") or {},
            item.get("entity") or {},
        )
        key = member["participant_entity_id"]
        if member["role"] == "enzyme" and statement.get("predicate") == "enabled_by":
            if entity.get("entity_type") in PROTEIN_TYPES:
                enzymes.setdefault(key, []).append(item)
        elif entity.get("entity_type") in CHEMICAL_TYPES:
            compartment = str(member.get("compartment") or "").strip()
            if not compartment:
                continue
            if member["role"] == "reactant" and statement.get("predicate") == "has_input":
                inputs.setdefault((key, compartment), []).append(item)
            elif member["role"] == "product" and statement.get("predicate") == "has_output":
                outputs.setdefault(key, {}).setdefault(compartment, []).append(item)
    for enzyme_key, catalysts in enzymes.items():
        for (chemical_key, before_compartment), before in inputs.items():
            for after_compartment, after in outputs.get(chemical_key, {}).items():
                if before_compartment == after_compartment:
                    continue
                yield {
                    "kind": "derived_transport",
                    "resource": context["resource"],
                    "version": context["version"],
                    "transport_key": [
                        context["context_id"],
                        enzyme_key,
                        chemical_key,
                        before_compartment,
                        after_compartment,
                    ],
                    "context": context,
                    "event": event,
                    "transporter": catalysts[0]["entity"],
                    "cargo": before[0]["entity"],
                    "compartment_from": before[0]["member"]["compartment"],
                    "compartment_to": after[0]["member"]["compartment"],
                    "supporting_participants": [*catalysts, *before, *after],
                }


def iter_records(conn, schema: str, name: str, *, organism=None) -> Iterator[dict]:
    """Stream scoped records inside the caller's transaction using server cursors."""
    _definition(name)
    organism = _organism(organism)
    if name != "reactions":
        for row in _binary_rows(conn, schema, name, organism):
            yield _binary_record(row, name)
    if name in {"reactions", "metalinksdb"}:
        for context, event, members in _reaction_rows(conn, schema):
            if not _context_matches(context, members, organism):
                continue
            if name == "reactions":
                yield {
                    "kind": "reaction",
                    "resource": context["resource"],
                    "version": context["version"],
                    "context": context,
                    "event": event,
                    "participants": members,
                }
            else:
                yield from _transports(context, event, members)


def query(
    conn, schema: str, name: str, *, organism=None, limit: int = 100, offset: int = 0
) -> dict:
    """Return a bounded page; distinct qualifiers retain their statement keys.

    Pagination counts scoped source records before statement grouping. This keeps
    memory bounded and does not imply that a page includes every resource asserting
    one statement. ``has_more`` makes an incomplete page explicit.
    """
    definition = _definition(name)
    for field, value in (("limit", limit), ("offset", offset)):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < (1 if field == "limit" else 0)
        ):
            raise ValueError(
                f"{field} must be a {'positive' if field == 'limit' else 'nonnegative'} integer"
            )
    if limit > 10000:
        raise ValueError("limit cannot exceed 10000 source records")
    records, grouped, seen, has_more = [], {}, 0, False
    stream = iter_records(conn, schema, name, organism=organism)
    try:
        for index, record in enumerate(stream):
            if index < offset:
                continue
            if seen == limit:
                has_more = True
                break
            seen += 1
            if record["kind"] == "published_statement":
                key = record["relation_key"]
                if key not in grouped:
                    group = {
                        "kind": "published_statement",
                        "relation_key": key,
                        "resource_records": [],
                        "sources": [],
                        "source_count": 0,
                        "evidence_count": 0,
                    }
                    grouped[key] = group
                    records.append(group)
                group = grouped[key]
                group["resource_records"].append(record)
                group["sources"] = sorted({item["resource"] for item in group["resource_records"]})
                group["source_count"] = len(group["sources"])
                group["evidence_count"] += record["statement"]["evidence_count"]
            else:
                records.append(record)
    finally:
        stream.close()
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT resource, version FROM {}.resource_versions").format(
                sql.Identifier(schema)
            )
        )
        pins = dict(cur.fetchall())
    return {
        "name": name,
        "records": records,
        "source_records": seen,
        "has_more": has_more,
        "offset": offset,
        "limit": limit,
        "resources": {
            source: pins[source] for source in definition.included_sources if source in pins
        },
        "missing_resources": sorted(set(definition.included_sources) - pins.keys()),
        "limitations": list(LIMITATIONS[name]),
    }
