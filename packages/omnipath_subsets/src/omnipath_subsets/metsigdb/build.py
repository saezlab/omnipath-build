"""Rebuild MetSigDB from published entities, relations and identifiers.

This adapter performs no identifier resolution. The current serving Parquet
schema does not preserve the old per-evidence ``matched`` flag: both matched
and fallback identifiers can have ``source='resolver'``. The migration uses
published chemical entities by default; requesting the old matched-only policy
is an error before any database writes.
"""

from __future__ import annotations

from typing import Literal

from psycopg import sql
from psycopg.pq import TransactionStatus

from omnipath_core.source_attributes import TRAIT_TYPE

from .mapping import CHEMICAL_TYPES, KEGG_OVERVIEW_MAPS, RESOURCES, ResourceRule


_DDL = """
CREATE TABLE IF NOT EXISTS {s}.metsigdb_membership (
    resource text NOT NULL,
    set_source_id text NOT NULL,
    metabolite_entity_id text NOT NULL,
    metabolite_label text NOT NULL,
    metabolite_entity_type text NOT NULL,
    inchikey text,
    metabolite_structure_key text,
    smiles text,
    hmdb text,
    pubchem text,
    chebi text,
    kegg text,
    set_entity_id text,
    set_label text,
    set_type text NOT NULL CHECK (set_type IN ('disease', 'pathway', 'chemical_class')),
    set_sub_type text,
    organism bigint,
    set_size integer NOT NULL CHECK (set_size > 0),
    set_context jsonb,
    provenance_source text NOT NULL,
    provenance_record jsonb,
    build_id text NOT NULL,
    PRIMARY KEY (resource, set_source_id, metabolite_entity_id),
    CHECK (resource IN ('Reactome', 'WikiPathways', 'KEGG', 'MACdb', 'ClassyFire'))
)
"""

# Membership joins remain resource/version scoped. Typed endpoints handle the
# symmetric associated_with edge regardless of its canonical orientation.
# Canonical rows are selected only for relevant entities; projected aliases span
# all release pins. Duplicate aliases and empty pins do not change MAX values.
_SOURCE = """
WITH source_edges AS (
    SELECT r.*, se.entity_type AS subject_entity_type, oe.entity_type AS object_entity_type
    FROM {s}.relations r
    JOIN {s}.entities se ON se.resource = r.resource AND se.version = r.version
        AND se.entity_key = r.subject_entity_key
    JOIN {s}.entities oe ON oe.resource = r.resource AND oe.version = r.version
        AND oe.entity_key = r.object_entity_key
    WHERE r.resource = %(source)s AND r.version = %(version)s
        AND r.statement_kind = 'relation'
), onehop AS (
    SELECT object_entity_key AS set_entity_id, subject_entity_key AS metabolite_entity_id,
        relation_key, NULL::text AS via_reaction, NULL::text AS via_relation
    FROM source_edges
    WHERE subject_entity_type = ANY(%(chemical_types)s)
        AND object_entity_type = %(set_entity_type)s
        AND predicate = ANY(%(forward_predicates)s)
    UNION ALL
    SELECT subject_entity_key, object_entity_key, relation_key, NULL::text, NULL::text
    FROM source_edges
    WHERE object_entity_type = ANY(%(chemical_types)s)
        AND subject_entity_type = %(set_entity_type)s
        AND predicate = ANY(%(reverse_predicates)s)
), reaction_sets AS (
    SELECT subject_entity_key AS reaction_id, object_entity_key AS set_entity_id, relation_key
    FROM source_edges
    WHERE subject_entity_type = 'molecular_activity' AND object_entity_type = 'pathway'
        AND predicate IN ('associated_with', 'part_of', 'member_of')
    UNION ALL
    SELECT object_entity_key, subject_entity_key, relation_key
    FROM source_edges
    WHERE object_entity_type = 'molecular_activity' AND subject_entity_type = 'pathway'
        AND predicate IN ('associated_with', 'has_part', 'has_member')
), reaction_chemicals AS (
    SELECT subject_entity_key AS reaction_id, object_entity_key AS metabolite_entity_id, relation_key
    FROM source_edges
    WHERE subject_entity_type = 'molecular_activity'
        AND object_entity_type = ANY(%(chemical_types)s)
        AND predicate IN ('has_input', 'has_output', 'has_participant')
), kegg_pairs AS (
    SELECT rs.set_entity_id, rc.metabolite_entity_id, rc.relation_key,
        rc.reaction_id AS via_reaction, rs.relation_key AS via_relation
    FROM reaction_sets rs
    JOIN reaction_chemicals rc USING (reaction_id)
), base_pairs AS (
    SELECT * FROM onehop WHERE %(extraction)s <> 'kegg'
    UNION ALL
    SELECT * FROM kegg_pairs WHERE %(extraction)s = 'kegg'
), expanded AS (
    SELECT *, 0 AS depth, NULL::text AS via_class FROM base_pairs
    UNION ALL
    SELECT oc.ancestor_entity_id, bp.metabolite_entity_id, bp.relation_key,
        bp.via_reaction, bp.via_relation, oc.depth, bp.set_entity_id AS via_class
    FROM base_pairs bp
    JOIN {s}.ontology_closure oc ON oc.resource = %(hierarchy_source)s
        AND oc.version = %(hierarchy_version)s
        AND oc.descendant_entity_id = bp.set_entity_id
        AND oc.hierarchy_kind = 'subclass' AND oc.depth BETWEEN 1 AND 20
    WHERE %(extraction)s = 'classyfire'
), native_sets AS (
    SELECT x.*, se.identifier AS set_identifier,
        COALESCE(
            CASE WHEN se.namespace = %(set_namespace)s THEN se.identifier END,
            native.id
        ) AS set_source_id,
        COALESCE(NULLIF(se.label, se.identifier), NULLIF(ce.label, ce.identifier), names.id) AS set_label,
        CASE WHEN regexp_replace(se.taxon, '^NCBITaxon:', '') ~ '^[1-9][0-9]{{0,17}}$'
            THEN regexp_replace(se.taxon, '^NCBITaxon:', '')::bigint END AS organism
    FROM expanded x
    JOIN {s}.entities se ON se.entity_key = x.set_entity_id
        AND se.resource = CASE WHEN x.depth > 0 THEN %(hierarchy_source)s ELSE %(source)s END
        AND se.version = CASE WHEN x.depth > 0 THEN %(hierarchy_version)s ELSE %(version)s END
    JOIN LATERAL (
        SELECT entity_key AS entity_id, label, identifier
        FROM {s}.entities canonical
        WHERE canonical.entity_key = x.set_entity_id
        ORDER BY canonical.resource, canonical.version LIMIT 1
    ) ce ON true
    LEFT JOIN LATERAL (
        SELECT MIN(i.id) AS id FROM {s}.identifiers i
        WHERE i.resource = se.resource AND i.version = se.version
            AND i.entity_key = se.entity_key AND i.ns = %(set_namespace)s
    ) native ON true
    LEFT JOIN LATERAL (
        SELECT MIN(i.id) AS id FROM {s}.identifiers i
        WHERE i.resource = se.resource AND i.version = se.version
            AND i.entity_key = se.entity_key AND i.ns = 'name'
    ) names ON true
    WHERE se.entity_type = %(set_entity_type)s
), evidenced AS (
    SELECT ns.*, ev.dataset, ev.row_id, ev.upstream_id,
        CASE WHEN ev.row_id ~ '^[0-9]+$' THEN ev.row_id::numeric END AS numeric_row_id,
        jsonb_strip_nulls(jsonb_build_object(
            'resource', %(source)s, 'version', %(version)s, 'relation_key', ns.relation_key,
            'source', ev.source, 'dataset', ev.dataset, 'row_id', ev.row_id,
            'upstream_id', ev.upstream_id, 'evidence_ordinal', ev.ordinal,
            'via_reaction', ns.via_reaction, 'via_relation', ns.via_relation
        )) AS provenance_record
    FROM native_sets ns
    LEFT JOIN LATERAL (
        SELECT source, dataset, row_id, upstream_id, ordinal FROM {s}.evidence ev
        WHERE ev.resource = %(source)s AND ev.version = %(version)s
            AND ev.relation_key = ns.relation_key
        ORDER BY CASE WHEN ev.row_id ~ '^[0-9]+$' THEN ev.row_id::numeric END NULLS LAST,
            ev.row_id, ev.dataset, ev.ordinal LIMIT 1
    ) ev ON true
    WHERE ns.set_source_id IS NOT NULL AND BTRIM(ns.set_source_id) <> ''
), chosen AS (
    SELECT DISTINCT ON (set_source_id, metabolite_entity_id) *
    FROM evidenced
    ORDER BY set_source_id, metabolite_entity_id, depth, numeric_row_id NULLS LAST,
        row_id NULLS LAST, relation_key, set_entity_id
), projected AS MATERIALIZED (
    SELECT e.entity_id,
        COALESCE(NULLIF(e.label, ''), e.identifier) AS metabolite_label,
        e.entity_type AS metabolite_entity_type,
        COALESCE(
            MAX(i.id) FILTER (WHERE i.ns = 'inchikey' AND i.is_canonical
                AND i.id ~ '^[A-Z]{{14}}-[A-Z]{{10}}-[A-Z]$'),
            MAX(i.id) FILTER (WHERE i.ns = 'inchikey'
                AND i.id ~ '^[A-Z]{{14}}-[A-Z]{{10}}-[A-Z]$')
        ) AS inchikey,
        MAX(i.id) FILTER (WHERE i.ns = 'smiles') AS smiles,
        MAX(CASE WHEN i.id ~ '^HMDB[0-9]+$' THEN
            'HMDB' || lpad(substring(i.id FROM 5), greatest(7, length(i.id) - 4), '0')
            ELSE i.id END) FILTER (WHERE i.ns = 'hmdb') AS hmdb,
        MAX(i.id) FILTER (WHERE i.ns = 'pubchem') AS pubchem,
        MAX(i.id) FILTER (WHERE i.ns = 'chebi') AS chebi,
        MAX(i.id) FILTER (WHERE i.ns = 'kegg') AS kegg
    FROM (SELECT DISTINCT metabolite_entity_id FROM chosen) c
    JOIN LATERAL (
        SELECT entity_key AS entity_id, label, identifier, entity_type
        FROM {s}.entities canonical
        WHERE canonical.entity_key = c.metabolite_entity_id
        ORDER BY canonical.resource, canonical.version LIMIT 1
    ) e ON true
    LEFT JOIN {s}.resource_versions rv ON true
    LEFT JOIN {s}.identifiers i ON i.resource = rv.resource AND i.version = rv.version
        AND i.entity_key = e.entity_id
        AND i.ns IN ('inchikey', 'smiles', 'hmdb', 'pubchem', 'chebi', 'kegg')
    GROUP BY e.entity_id, e.label, e.identifier, e.entity_type
), complete_projection AS (
    SELECT *, split_part(inchikey, '-', 1) AS metabolite_structure_key FROM projected
)
"""

_PUBLISH = """
INSERT INTO {s}.metsigdb_membership (
    resource, set_source_id, metabolite_entity_id, metabolite_label, metabolite_entity_type,
    inchikey, metabolite_structure_key, smiles, hmdb, pubchem, chebi, kegg,
    set_entity_id, set_label, set_type, set_sub_type, organism, set_size, set_context,
    provenance_source, provenance_record, build_id
)
SELECT %(resource)s, c.set_source_id, c.metabolite_entity_id,
    p.metabolite_label, p.metabolite_entity_type,
    p.inchikey, p.metabolite_structure_key, p.smiles, p.hmdb, p.pubchem, p.chebi, p.kegg,
    c.set_entity_id, c.set_label, %(set_type)s,
    CASE WHEN %(resource)s = 'KEGG' THEN
        CASE WHEN c.set_source_id = ANY(%(overview_maps)s) THEN 'overview_map' ELSE 'metabolic_map' END
        WHEN %(resource)s = 'MACdb' THEN subtype.value END,
    c.organism, (COUNT(*) OVER (PARTITION BY c.set_source_id))::integer,
    CASE WHEN %(extraction)s = 'classyfire' THEN jsonb_strip_nulls(jsonb_build_object(
        'assignment', CASE WHEN c.depth = 0 THEN 'direct' ELSE 'ancestor' END,
        'depth', c.depth, 'via', via.identifier
    )) END,
    %(provenance_source)s, c.provenance_record, %(build_id)s
FROM chosen c
JOIN complete_projection p ON p.entity_id = c.metabolite_entity_id
LEFT JOIN {s}.entities via ON via.resource = %(source)s AND via.version = %(version)s
    AND via.entity_key = c.via_class
LEFT JOIN LATERAL (
    SELECT MIN(NULLIF(a.value, '')) AS value
    FROM {s}.annotations a
    WHERE %(resource)s = 'MACdb' AND a.resource = %(source)s AND a.version = %(version)s
        AND a.owner_kind = 'entity' AND a.owner_key = c.set_entity_id
        AND a.term = %(trait_type_term)s
) subtype ON true
"""


def _params(rule: ResourceRule, versions: dict[str, str], build_id: str) -> dict:
    """Bind source rules as values, keeping identifiers separately quoted."""
    forward = ["associated_with"]
    reverse = ["associated_with"]
    if rule.set_type == "pathway":
        forward.extend(("part_of", "member_of"))
        reverse.extend(("has_part", "has_member"))
    return dict(
        trait_type_term=TRAIT_TYPE,
        resource=rule.name,
        source=rule.source,
        version=versions[rule.source],
        set_type=rule.set_type,
        set_entity_type=rule.set_entity_type,
        set_namespace=rule.set_namespace,
        extraction=rule.extraction,
        chemical_types=list(CHEMICAL_TYPES),
        forward_predicates=forward,
        reverse_predicates=reverse,
        hierarchy_source=rule.hierarchy_source,
        hierarchy_version=versions.get(rule.hierarchy_source),
        overview_maps=list(KEGG_OVERVIEW_MAPS),
        provenance_source=f"parquet:{rule.source}@{versions[rule.source]}",
        build_id=build_id,
    )


def rebuild(
    conn,
    schema: str,
    *,
    eligibility_policy: Literal["published_entities", "legacy_matched"] = "published_entities",
) -> dict:
    """Replace all five membership products in the caller's transaction.

    ``published_entities`` uses the chemical endpoints and aliases already
    published in the pinned release, including source fallback identities.
    This is the migration's chosen replacement for the legacy matched-only
    filter. ``legacy_matched`` is unavailable in serving schema 3 and raises
    before writes.

    A missing source is reported and publishes no rows. HMDB requires ChemOnt
    for a complete ClassyFire extraction. No row limit is reapplied here: the
    published build cap already controls the input, and truncating derived
    pairs would change set sizes. The result contains JSON-serializable counts
    and the exact release stamp. This function neither commits nor resolves IDs.
    """
    if eligibility_policy != "published_entities":
        raise ValueError(
            "MetSigDB legacy matched-only eligibility cannot be recovered from serving schema 3; "
            "choose eligibility_policy='published_entities' explicitly"
        )
    if conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE:
        raise ValueError("MetSigDB rebuild requires a caller-owned transaction")
    namespace = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT release_id, manifest_sha256 FROM {}.release_metadata").format(namespace)
        )
        releases = cur.fetchall()
        if len(releases) != 1:
            raise ValueError("MetSigDB requires exactly one pinned release in the target schema")
        release_id, build_id = releases[0]
        cur.execute(sql.SQL("SELECT resource, version FROM {}.resource_versions").format(namespace))
        pins = cur.fetchall()
        versions = dict(pins)
        if len(versions) != len(pins):
            raise ValueError("MetSigDB requires exactly one version per resource")
        cur.execute(sql.SQL(_DDL).format(s=namespace))
        cur.execute(sql.SQL("TRUNCATE {}.metsigdb_membership").format(namespace))
        result = dict(
            release_id=release_id,
            build_id=build_id,
            eligibility_policy=eligibility_policy,
            resources={},
        )
        for rule in RESOURCES:
            missing = [
                source
                for source in (rule.source, rule.hierarchy_source)
                if source and source not in versions
            ]
            if missing:
                result["resources"][rule.name] = dict(
                    status="skipped", missing_sources=missing, memberships=0, sets=0
                )
                continue
            params = _params(rule, versions, build_id)
            cur.execute(sql.SQL(_SOURCE + _PUBLISH).format(s=namespace), params)
            cur.execute(
                sql.SQL(
                    "SELECT COUNT(*), COUNT(DISTINCT set_source_id) FROM {}.metsigdb_membership WHERE resource=%s"
                ).format(namespace),
                (rule.name,),
            )
            memberships, sets = cur.fetchone()
            result["resources"][rule.name] = dict(
                status="built", memberships=memberships, sets=sets
            )
        for column in (
            "metabolite_entity_id",
            "set_type",
            "set_sub_type",
            "metabolite_structure_key",
            "organism",
            "inchikey",
            "hmdb",
            "pubchem",
            "chebi",
            "kegg",
        ):
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.metsigdb_membership ({})").format(
                    sql.Identifier(f"metsigdb_membership_{column}_idx"),
                    namespace,
                    sql.Identifier(column),
                )
            )
        cur.execute(sql.SQL("ANALYZE {}.metsigdb_membership").format(namespace))
    return result
