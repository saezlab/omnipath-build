"""COSMOS reaction projection using published identifiers and source contexts.

Pseudo-nodes are local to this product. Source-event contexts remain separate:
neither roles, compartments nor catalytic claims are pooled across resources.
Reaction indexes enumerate ordered source contexts, so two source rows for the
same event have distinct pseudo-nodes. This differs from the legacy participant-
multiset merge and prevents conflicting event context from being combined.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
import time
import uuid

from psycopg import sql
from psycopg.rows import dict_row


REACTION_SOURCES = ("kegg", "rhea", "metatlas", "recon3d")
_WRITE_BATCH_SIZE = 1000
EDGE_COLUMNS = (
    "build_id",
    "source_label",
    "target_label",
    "source_entity_id",
    "target_entity_id",
    "source_type",
    "target_type",
    "source_compartment",
    "target_compartment",
    "mor",
    "interaction_type",
    "reaction_entity_id",
    "interaction_id",
    "reaction_index",
    "orphan",
    "reverse",
    "direction",
    "source_id_type",
    "target_id_type",
    "sources",
)


@dataclass(frozen=True)
class Label:
    identifier: str
    namespace: str | None
    status: str  # canonical, mapped, ambiguous or fallback


def published_label(entity, wanted):
    """Use an authoritative canonical ID or one unambiguous carried alias.

    Alias candidates shared by multiple published entities are rejected. No
    external resolver or mapping service is consulted, and fallback namespaces
    are recorded rather than presented as UniProt/ChEBI.
    """
    canonical = entity.get("namespace")
    identifier = entity.get("identifier") or entity["entity_id"]
    if canonical == wanted:
        return Label(_format(identifier, wanted), canonical, "canonical")
    candidates = {
        _format(alias["id"], wanted)
        for alias in entity.get("aliases") or []
        if alias.get("ns") == wanted and alias.get("id") not in (None, "")
    }
    unsafe = {
        _format(alias["id"], wanted)
        for alias in entity.get("aliases") or []
        if alias.get("ns") == wanted and alias.get("ambiguous")
    }
    if len(candidates) == 1 and not unsafe:
        return Label(next(iter(candidates)), wanted, "mapped")
    return Label(
        _format(identifier, canonical),
        canonical,
        "ambiguous" if len(candidates) > 1 or unsafe else "fallback",
    )


def _format(identifier, namespace):
    return (
        "CHEBI:" + str(identifier).removeprefix("CHEBI:")
        if namespace == "chebi"
        else str(identifier)
    )


def project_context(context, parties, build_id, reaction_index):
    """Yield legacy-compatible metabolic edges and distinct connector nodes."""
    metabolites = [
        party
        for party in parties
        if party["role"] in ("reactant", "product")
        and party["entity_type"] in ("small_molecule", "chemical_entity")
    ]
    enzymes = [
        party
        for party in parties
        if party["role"] == "enzyme" and party["entity_type"] == "protein"
    ]
    if not metabolites:
        return
    orphan = not enzymes
    genes = {(party["entity_id"], published_label(party, "uniprot")) for party in enzymes}
    if orphan:
        genes = {
            (
                None,
                Label(
                    str(context["identifier"] or context["reaction_entity_id"]),
                    context["namespace"],
                    "fallback",
                ),
            )
        }
    common = dict(
        build_id=build_id,
        mor=1,
        reaction_entity_id=context["reaction_entity_id"],
        interaction_id=context["context_id"],
        reaction_index=reaction_index,
        orphan=orphan,
        direction=context["direction"],
        sources=context["sources"],
    )
    for reverse in (False, True) if context["direction"] == "reversible" else (False,):
        for entity_id, label in sorted(genes, key=lambda pair: (pair[0] or "", pair[1].identifier)):
            gene_label = (
                f"Gene{reaction_index}__"
                + ("orphanReac" if orphan else "")
                + label.identifier
                + ("_rev" if reverse else "")
            )
            gene_type = "reaction" if orphan else "protein"
            emitted = set()
            for party in metabolites:
                chemical = published_label(party, "chebi")
                metabolite = "Metab__" + chemical.identifier
                if party["compartment"]:
                    metabolite += "_" + party["compartment"]
                endpoints = [
                    (
                        metabolite,
                        party["entity_id"],
                        "metabolite",
                        party["compartment"],
                        chemical.namespace,
                    ),
                    (gene_label, entity_id, gene_type, None, label.namespace),
                ]
                if (party["role"] == "product") != reverse:
                    endpoints.reverse()
                # Multiple occurrences/stoichiometries remain in participant
                # provenance; COSMOS is an unweighted simple edge projection.
                fingerprint = tuple(endpoints)
                if fingerprint in emitted:
                    continue
                emitted.add(fingerprint)
                yield dict(
                    common,
                    reverse=reverse,
                    interaction_type="transport" if context["transport"] else "catalysis",
                    **dict(
                        zip(
                            (
                                "source_label",
                                "source_entity_id",
                                "source_type",
                                "source_compartment",
                                "source_id_type",
                            ),
                            endpoints[0],
                            strict=True,
                        )
                    ),
                    **dict(
                        zip(
                            (
                                "target_label",
                                "target_entity_id",
                                "target_type",
                                "target_compartment",
                                "target_id_type",
                            ),
                            endpoints[1],
                            strict=True,
                        )
                    ),
                )
            yield dict(
                common,
                reverse=reverse,
                source_label=label.identifier,
                target_label=gene_label,
                source_entity_id=entity_id,
                target_entity_id=entity_id,
                source_type="protein",
                target_type="protein",
                source_compartment=None,
                target_compartment=None,
                source_id_type=label.namespace,
                target_id_type=label.namespace,
                interaction_type="connector",
            )


def _ensure_tables(conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {s}.cosmos_edge (
                cosmos_edge_id bigserial PRIMARY KEY, build_id text NOT NULL,
                source_label text NOT NULL, target_label text NOT NULL,
                source_entity_id text, target_entity_id text,
                source_type text NOT NULL, target_type text NOT NULL,
                source_compartment text, target_compartment text,
                mor smallint NOT NULL CHECK (mor IN (-1,1)),
                interaction_type text NOT NULL CHECK (interaction_type IN ('catalysis','transport','connector')),
                reaction_entity_id text NOT NULL, interaction_id text NOT NULL, reaction_index integer NOT NULL,
                orphan boolean NOT NULL, reverse boolean NOT NULL,
                direction text CHECK (direction IN ('reversible','left_to_right')),
                source_id_type text, target_id_type text, sources text[] NOT NULL,
                CHECK (NOT reverse OR direction='reversible'),
                CHECK (source_type IN ('metabolite','protein','reaction')
                    AND target_type IN ('metabolite','protein','reaction'))
            );
            CREATE TABLE IF NOT EXISTS {s}.cosmos_label (
                entity_id text NOT NULL, wanted_namespace text NOT NULL,
                identifier text NOT NULL, namespace text, status text NOT NULL,
                PRIMARY KEY (entity_id, wanted_namespace)
            );
            CREATE INDEX IF NOT EXISTS cosmos_edge_source_label_idx ON {s}.cosmos_edge (source_label);
            CREATE INDEX IF NOT EXISTS cosmos_edge_target_label_idx ON {s}.cosmos_edge (target_label);
            CREATE INDEX IF NOT EXISTS cosmos_edge_reaction_idx ON {s}.cosmos_edge (reaction_entity_id);
            CREATE INDEX IF NOT EXISTS cosmos_edge_interaction_idx ON {s}.cosmos_edge (interaction_id);
            CREATE INDEX IF NOT EXISTS cosmos_edge_type_idx ON {s}.cosmos_edge (interaction_type);
            TRUNCATE {s}.cosmos_edge, {s}.cosmos_label RESTART IDENTITY;
        """).format(s=sql.Identifier(schema))
        )


def _rows(conn, schema):
    with conn.cursor(name="cosmos_" + uuid.uuid4().hex, row_factory=dict_row) as cur:
        cur.execute(
            sql.SQL("""
            WITH conflicts AS (
                SELECT ns, id, COUNT(DISTINCT entity_key)>1 AS ambiguous
                FROM {s}.identifiers WHERE ns IN ('uniprot','chebi') GROUP BY ns,id
            ), aliases AS (
                SELECT i.entity_key, jsonb_agg(DISTINCT jsonb_build_object(
                    'ns',i.ns,'id',i.id,'ambiguous',COALESCE(c.ambiguous,false))) AS aliases
                FROM {s}.identifiers i LEFT JOIN conflicts c USING (ns,id)
                WHERE i.ns IN ('uniprot','chebi') GROUP BY i.entity_key
            )
            SELECT c.context_id, c.reaction_entity_id, c.identifier AS reaction_identifier,
                c.namespace AS reaction_namespace, c.direction,c.transport,c.sources,
                p.role,p.compartment,p.ordinal,e.entity_id,e.entity_type,e.namespace,e.identifier,a.aliases
            FROM {s}.reaction_context c
            JOIN {s}.reaction_participant p USING (context_id)
            JOIN {s}.entity e ON e.entity_id=p.participant_entity_id
            LEFT JOIN aliases a ON a.entity_key=e.entity_id
            WHERE c.resource=ANY(%s) OR c.sources && %s
            ORDER BY c.reaction_entity_id,c.resource,c.version,c.source NULLS FIRST,
                c.dataset NULLS FIRST,c.row_id NULLS FIRST,c.context_id,p.ordinal
        """).format(s=sql.Identifier(schema)),
            (list(REACTION_SOURCES), list(REACTION_SOURCES)),
        )
        while rows := cur.fetchmany(256):
            yield from rows


def rebuild(conn, schema: str) -> dict:
    """Rebuild COSMOS in the caller's transaction and return JSON-ready counts."""
    started = time.monotonic()
    stats = {"skipped_parties": 0, "headers_without_event": 0}
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT release_id,manifest_sha256 FROM {}.release_metadata").format(
                sql.Identifier(schema)
            )
        )
        releases = cur.fetchall()
        if len(releases) != 1:
            raise ValueError("COSMOS requires exactly one loaded release")
        release_id, build_id = releases[0]
        _ensure_tables(conn, schema)
        insert_edge = sql.SQL("INSERT INTO {}.cosmos_edge ({}) VALUES ({})").format(
            sql.Identifier(schema),
            sql.SQL(",").join(map(sql.Identifier, EDGE_COLUMNS)),
            sql.SQL(",").join(sql.Placeholder() for _ in EDGE_COLUMNS),
        )
        insert_label = sql.SQL(
            "INSERT INTO {}.cosmos_label VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING"
        ).format(sql.Identifier(schema))
        edge_rows, label_rows = [], {}
        reaction_index = 0
        for _, records in groupby(_rows(conn, schema), key=lambda row: row["context_id"]):
            parties = list(records)
            first = parties[0]
            context = {
                **first,
                "identifier": first["reaction_identifier"],
                "namespace": first["reaction_namespace"],
            }
            eligible = [
                p
                for p in parties
                if (
                    p["role"] in ("reactant", "product")
                    and p["entity_type"] in ("small_molecule", "chemical_entity")
                )
                or (p["role"] == "enzyme" and p["entity_type"] == "protein")
            ]
            stats["skipped_parties"] += len(parties) - len(eligible)
            if not any(p["role"] in ("reactant", "product") for p in eligible):
                continue
            reaction_index += 1
            for party in eligible:
                wanted = "uniprot" if party["role"] == "enzyme" else "chebi"
                label = published_label(party, wanted)
                # Retain the serial writer's first-occurrence conflict behavior.
                # The pending map is bounded; repeats after a flush are handled by
                # the existing primary key/ON CONFLICT rather than a global cache.
                label_rows.setdefault(
                    (party["entity_id"], wanted),
                    (party["entity_id"], wanted, label.identifier, label.namespace, label.status),
                )
                if len(label_rows) >= _WRITE_BATCH_SIZE:
                    cur.executemany(insert_label, label_rows.values())
                    label_rows.clear()
            for edge in project_context(context, eligible, build_id, reaction_index):
                edge_rows.append([edge[column] for column in EDGE_COLUMNS])
                if len(edge_rows) >= _WRITE_BATCH_SIZE:
                    # executemany's automatic pipeline ends inside this call,
                    # before the server cursor's next FETCH. Input edge order,
                    # and therefore generated cosmos_edge_id order, is retained.
                    cur.executemany(insert_edge, edge_rows)
                    edge_rows.clear()
        if label_rows:
            cur.executemany(insert_label, label_rows.values())
        if edge_rows:
            cur.executemany(insert_edge, edge_rows)
        cur.execute(
            sql.SQL("""
            SELECT COUNT(*) AS edges,COUNT(DISTINCT interaction_id) AS reactions,
                COUNT(DISTINCT interaction_id) FILTER (WHERE orphan) AS orphan_reactions,
                COUNT(*) FILTER (WHERE interaction_type='connector') AS connectors,
                COUNT(*) FILTER (WHERE reverse) AS reverse_edges,
                COUNT(DISTINCT interaction_id) FILTER (WHERE direction='reversible') AS reversible_reactions
            FROM {}.cosmos_edge
        """).format(sql.Identifier(schema))
        )
        stats.update(
            dict(
                zip(
                    (
                        "edges",
                        "reactions",
                        "orphan_reactions",
                        "connectors",
                        "reverse_edges",
                        "reversible_reactions",
                    ),
                    cur.fetchone(),
                    strict=True,
                )
            )
        )
        cur.execute(
            sql.SQL("""
            WITH metabolites AS (
                SELECT source_label AS label FROM {s}.cosmos_edge WHERE source_type='metabolite'
                UNION SELECT target_label FROM {s}.cosmos_edge WHERE target_type='metabolite'
            )
            SELECT (SELECT COUNT(*) FROM metabolites),
                COUNT(DISTINCT target_label) FILTER (WHERE interaction_type='connector')
            FROM {s}.cosmos_edge
        """).format(s=sql.Identifier(schema))
        )
        stats["metabolite_nodes"], stats["gene_nodes"] = cur.fetchone()
        cur.execute(
            sql.SQL(
                "SELECT wanted_namespace,status,COUNT(*) FROM {}.cosmos_label GROUP BY wanted_namespace,status"
            ).format(sql.Identifier(schema))
        )
        label_counts = {(ns, status): count for ns, status, count in cur.fetchall()}
        for prefix, ns in (("gene", "uniprot"), ("chemical", "chebi")):
            stats[prefix + "_labels_translated"] = label_counts.get(
                (ns, "canonical"), 0
            ) + label_counts.get((ns, "mapped"), 0)
            stats[prefix + "_labels_mapped"] = label_counts.get((ns, "mapped"), 0)
            stats[prefix + "_labels_fallback"] = label_counts.get(
                (ns, "fallback"), 0
            ) + label_counts.get((ns, "ambiguous"), 0)
            stats[prefix + "_labels_ambiguous"] = label_counts.get((ns, "ambiguous"), 0)
    return dict(
        stats,
        build_id=build_id,
        release_id=release_id,
        identifier_translation=False,
        identifier_translation_method="published_aliases",
        seconds=time.monotonic() - started,
    )
