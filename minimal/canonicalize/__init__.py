"""Canonicalize evidence entities into graph entities and relations.

Example input evidence can contain repeated participants across interaction
rows:

1. TP53/P04637 -> MDM2/Q00987
2. TP53/P04637 -> EGFR/P00533
3. BRCA1/P38398 -> MDM2/Q00987

Ingest keeps those as six separate `entity_evidence` occurrences and three
`relation_evidence` rows. Each participant occurrence links to rows in
`identifier_evidence`; for example both TP53 occurrences link to the same
UniProt and gene-name evidence identifiers, but keep separate
`entity_evidence_id` values.

Canonicalization first groups equivalent evidence occurrences by entity type,
taxonomy, and the set of evidence identifier IDs. The two TP53 occurrences
therefore share one entity group, while MDM2 also shares one entity group
across rows 1 and 3. Each group is resolved through normalized resolver tables.
Protein resolver joins are taxonomy-scoped: a protein evidence group must carry
taxonomy, and the resolver row must have the same taxonomy. Chemicals resolve
through taxonless chemical resolver rows.

For each group, resolver candidates are ranked by identifier strength. A direct
UniProt or standard InChI key candidate wins over stable cross references, which
win over weak names. If the best rank has exactly one canonical target, every
evidence occurrence in that group gets one `entity_evidence_resolution` row
pointing to the canonical `entity`. If two evidence identifiers in the same
group disagree at the same best rank, the group becomes `ambiguous`. If there is
no accepted resolver candidate, the group gets an unresolved fallback entity
with a normalized resolution reason.

After entity resolution, relation canonicalization replaces each
`relation_evidence` endpoint occurrence with the resolved canonical `entity_id`
and upserts distinct graph `relation` rows. In the example above, six evidence
participants collapse to four graph entities: TP53, MDM2, EGFR, and BRCA1.
"""

from __future__ import annotations

from typing import Any
from dataclasses import field, dataclass

from psycopg2 import sql
import psycopg2.extensions
from pypath.internals.cv_terms import (
    IdentifierNamespaceCv,
    cv_term_label_accession,
)

from minimal.cv_terms import (
    CHEMICAL_ENTITY_TYPE_ALIASES,
    CV_TERM_ENTITY_TYPE,
    CV_TERM_ID_TYPE,
    PROTEIN_ENTITY_TYPE_ALIASES,
)
from minimal.resolver.identifier_types import FALLBACK_IDENTIFIER_TYPE

PROTEIN_ENTITY_TYPES = PROTEIN_ENTITY_TYPE_ALIASES
CHEMICAL_ENTITY_TYPES = CHEMICAL_ENTITY_TYPE_ALIASES
UNIPROT_TYPE = cv_term_label_accession(IdentifierNamespaceCv.UNIPROT)
ENSEMBL_TYPE = cv_term_label_accession(IdentifierNamespaceCv.ENSEMBL)
ENTREZ_TYPE = cv_term_label_accession(IdentifierNamespaceCv.ENTREZ)
HGNC_TYPE = cv_term_label_accession(IdentifierNamespaceCv.HGNC)
GENE_NAME_PRIMARY_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.GENE_NAME_PRIMARY
)
GENE_NAME_SYNONYM_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.GENE_NAME_SYNONYM
)
UNIPROT_ENTRY_NAME_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.UNIPROT_ENTRY_NAME
)
CHEBI_TYPE = cv_term_label_accession(IdentifierNamespaceCv.CHEBI)
PUBCHEM_COMPOUND_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.PUBCHEM_COMPOUND
)
HMDB_TYPE = cv_term_label_accession(IdentifierNamespaceCv.HMDB)
LIPIDMAPS_TYPE = cv_term_label_accession(IdentifierNamespaceCv.LIPIDMAPS)
SWISSLIPIDS_TYPE = cv_term_label_accession(IdentifierNamespaceCv.SWISSLIPIDS)
STANDARD_INCHI_KEY_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.STANDARD_INCHI_KEY
)
STANDARD_INCHI_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.STANDARD_INCHI
)
ONTOLOGY_IDENTIFIER_TERM = cv_term_label_accession(
    IdentifierNamespaceCv.CV_TERM_ACCESSION
)
DIRECT_IDENTIFIER_TYPES = (
    UNIPROT_TYPE,
    STANDARD_INCHI_KEY_TYPE,
    STANDARD_INCHI_TYPE,
)
STABLE_REFERENCE_IDENTIFIER_TYPES = (
    ENSEMBL_TYPE,
    ENTREZ_TYPE,
    HGNC_TYPE,
    CHEBI_TYPE,
    PUBCHEM_COMPOUND_TYPE,
    HMDB_TYPE,
    LIPIDMAPS_TYPE,
    SWISSLIPIDS_TYPE,
)
WEAK_IDENTIFIER_TYPES = (
    GENE_NAME_PRIMARY_TYPE,
    GENE_NAME_SYNONYM_TYPE,
    UNIPROT_ENTRY_NAME_TYPE,
)
DIRECT_MAPPING_TYPES = (
    'standard_inchi_key_identity',
)
ASSOCIATION_CATEGORY = 'association'
ASSOCIATION_PREDICATE = 'associated_with'
PATHWAY_PREDICATE = 'involved_in'

@dataclass(frozen=True)
class CanonicalizationStats:
    """Summary counts from a scoped entity/relation materialization pass."""

    entity_scope: int = 0
    candidate_rows: int = 0
    entities: int = 0
    entity_status: dict[str, int] = field(default_factory=dict)
    relation_scope: int = 0
    relations: int = 0
    relation_mapped: int = 0
    relation_unmapped: int = 0


def canonicalize(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    source: str | None = None,
    dataset: str | None = None,
    unresolved_only: bool = False,
    include_relations: bool = True,
) -> CanonicalizationStats:
    """Resolve scoped evidence and materialize the general entity graph."""

    with conn.cursor() as cur:
        _create_entity_scope(
            cur,
            schema=schema,
            source=source,
            dataset=dataset,
            unresolved_only=unresolved_only,
        )
        entity_scope = _count(cur, '_entity_scope')
        candidate_rows = 0
        if entity_scope:
            _create_entity_keys(cur, schema)
            _create_entity_groups(cur, schema)
            _create_raw_group_candidate_table(cur)
            _insert_group_protein_candidates(cur, schema)
            _insert_group_chemical_candidates(cur, schema)
            _insert_group_standard_inchi_key_identity_candidates(cur)
            _create_entity_group_taxonomy_conflict_table(cur, schema)
            _aggregate_group_candidates(cur)
            candidate_rows = _count(cur, '_entity_group_resolution_candidate')
            _create_entity_group_resolution_stage(cur)
            _project_entity_group_resolution_stage(cur)
            _insert_scoped_entity_types(cur, schema)
            _insert_entities(cur, schema)
            _upsert_entity_resolution(cur, schema)
            _insert_entity_annotations(cur, schema)

        entities = _count_schema_table(cur, schema, 'entity')
        entity_status = _status_counts(
            cur, schema, 'entity_evidence_resolution'
        )

        relation_scope = 0
        relations = _count_schema_table(cur, schema, 'relation')
        relation_mapped = 0
        relation_unmapped = 0
        if include_relations:
            _create_relation_scope(
                cur, schema=schema, source=source, dataset=dataset
            )
            relation_scope = _count(cur, '_relation_scope')
            if relation_scope:
                _create_relation_endpoint(cur, schema)
                _delete_scoped_relation_evidence(cur, schema)
                _insert_relations(cur, schema)
                _insert_relation_evidence_links(cur, schema)
                _refresh_relation_annotations(cur, schema)
            relations = _count_schema_table(cur, schema, 'relation')
            relation_mapped, relation_unmapped = _relation_mapping_counts(
                cur, schema
            )

    conn.commit()
    return CanonicalizationStats(
        entity_scope=entity_scope,
        candidate_rows=candidate_rows,
        entities=entities,
        entity_status=entity_status,
        relation_scope=relation_scope,
        relations=relations,
        relation_mapped=relation_mapped,
        relation_unmapped=relation_unmapped,
    )

def _create_entity_scope(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
    source: str | None,
    dataset: str | None,
    unresolved_only: bool,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_scope')
    cur.execute(
        """
        CREATE TEMP TABLE _entity_scope (
          entity_evidence_id bigint PRIMARY KEY
        ) ON COMMIT DROP
        """
    )
    where = []
    params: list[Any] = []
    if source is not None:
        where.append('ee.source = %s')
        params.append(source)
    if dataset is not None:
        where.append('ee.dataset = %s')
        params.append(dataset)
    if unresolved_only:
        where.append(
            """
            (
              r.entity_evidence_id IS NULL
              OR rs.name IS DISTINCT FROM 'resolved'
            )
            """
        )
    where_sql = (
        sql.SQL('WHERE ')
        + sql.SQL(' AND ').join(sql.SQL(part) for part in where)
        if where
        else sql.SQL('')
    )
    join_sql = (
        sql.SQL(
            """
            LEFT JOIN {}.entity_evidence_resolution r
              ON r.entity_evidence_id = ee.entity_evidence_id
            LEFT JOIN {}.resolution_status rs
              ON rs.resolution_status_id = r.status_id
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
        if unresolved_only
        else sql.SQL('')
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO _entity_scope (entity_evidence_id)
            SELECT ee.entity_evidence_id
            FROM {}.entity_evidence ee
            {}
            {}
            ON CONFLICT DO NOTHING
            """
        ).format(sql.Identifier(schema), join_sql, where_sql),
        params,
    )
    cur.execute('ANALYZE _entity_scope')


def _create_entity_keys(cur: psycopg2.extensions.cursor, schema: str) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_key')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _entity_key ON COMMIT DROP AS
            SELECT
              ee.entity_evidence_id,
              ee.entity_type,
              NULLIF(ee.taxonomy_id, '') AS taxonomy_id,
              i.identifier_id,
              i.identifier_type_id AS key_identifier_type_id,
              it.name AS key_type,
              it.name AS resolver_key_type,
              i.value AS key_value
            FROM _entity_scope s
            JOIN {}.entity_evidence ee
              ON ee.entity_evidence_id = s.entity_evidence_id
            JOIN {}.entity_evidence_identifier eei
              ON eei.entity_evidence_id = ee.entity_evidence_id
            JOIN {}.identifier_evidence i
              ON i.identifier_id = eei.identifier_id
            JOIN {}.identifier_type it
              ON it.identifier_type_id = i.identifier_type_id
            WHERE i.value IS NOT NULL
              AND i.value <> ''
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        )
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_key (
          entity_type,
          key_identifier_type_id,
          key_value
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_key (
          key_identifier_type_id,
          key_value,
          taxonomy_id
        )
        """
    )
    cur.execute('ANALYZE _entity_key')


def _create_entity_groups(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_group_member')
    cur.execute('DROP TABLE IF EXISTS _entity_group_key')
    cur.execute('DROP TABLE IF EXISTS _entity_group')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _entity_group ON COMMIT DROP AS
            WITH scoped_evidence AS (
              SELECT
                ee.entity_evidence_id,
                ee.entity_type,
                NULLIF(ee.taxonomy_id, '') AS taxonomy_id,
                COALESCE(
                  array_agg(
                    DISTINCT k.identifier_id
                    ORDER BY k.identifier_id
                  ) FILTER (WHERE k.identifier_id IS NOT NULL),
                  ARRAY[]::bigint[]
                ) AS identifier_ids
              FROM _entity_scope s
              JOIN {}.entity_evidence ee
                ON ee.entity_evidence_id = s.entity_evidence_id
              LEFT JOIN _entity_key k
                ON k.entity_evidence_id = ee.entity_evidence_id
              GROUP BY
                ee.entity_evidence_id,
                ee.entity_type,
                NULLIF(ee.taxonomy_id, '')
            ),
            grouped AS (
              SELECT DISTINCT
                entity_type,
                taxonomy_id,
                identifier_ids
              FROM scoped_evidence
            )
            SELECT
              row_number() OVER (
                ORDER BY
                  entity_type NULLS LAST,
                  taxonomy_id NULLS LAST,
                  identifier_ids
              )::bigint AS entity_group_id,
              entity_type,
              taxonomy_id,
              identifier_ids
            FROM grouped
            """
        ).format(sql.Identifier(schema))
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _entity_group (
          entity_group_id
        )
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _entity_group (
          entity_type,
          taxonomy_id,
          identifier_ids
        ) NULLS NOT DISTINCT
        """
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _entity_group_member ON COMMIT DROP AS
            WITH scoped_evidence AS (
              SELECT
                ee.entity_evidence_id,
                ee.entity_type,
                NULLIF(ee.taxonomy_id, '') AS taxonomy_id,
                COALESCE(
                  array_agg(
                    DISTINCT k.identifier_id
                    ORDER BY k.identifier_id
                  ) FILTER (WHERE k.identifier_id IS NOT NULL),
                  ARRAY[]::bigint[]
                ) AS identifier_ids
              FROM _entity_scope s
              JOIN {}.entity_evidence ee
                ON ee.entity_evidence_id = s.entity_evidence_id
              LEFT JOIN _entity_key k
                ON k.entity_evidence_id = ee.entity_evidence_id
              GROUP BY
                ee.entity_evidence_id,
                ee.entity_type,
                NULLIF(ee.taxonomy_id, '')
            )
            SELECT
              se.entity_evidence_id,
              g.entity_group_id
            FROM scoped_evidence se
            JOIN _entity_group g
              ON g.entity_type IS NOT DISTINCT FROM se.entity_type
             AND g.taxonomy_id IS NOT DISTINCT FROM se.taxonomy_id
             AND g.identifier_ids = se.identifier_ids
            """
        ).format(sql.Identifier(schema))
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _entity_group_member (
          entity_evidence_id
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_group_member (
          entity_group_id
        )
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE _entity_group_key ON COMMIT DROP AS
        SELECT DISTINCT
          gm.entity_group_id,
          k.identifier_id,
          k.entity_type,
          k.taxonomy_id,
          k.key_identifier_type_id,
          k.key_type,
          k.resolver_key_type,
          k.key_value
        FROM _entity_group_member gm
        JOIN _entity_key k
          ON k.entity_evidence_id = gm.entity_evidence_id
        """
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_group_key (
          entity_group_id
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_group_key (
          key_identifier_type_id,
          key_value,
          taxonomy_id
        )
        """
    )
    cur.execute('ANALYZE _entity_group')
    cur.execute('ANALYZE _entity_group_member')
    cur.execute('ANALYZE _entity_group_key')


def _create_raw_group_candidate_table(cur: psycopg2.extensions.cursor) -> None:
    cur.execute('DROP TABLE IF EXISTS _raw_group_resolution_candidate')
    cur.execute(
        """
        CREATE TEMP TABLE _raw_group_resolution_candidate (
          entity_group_id bigint NOT NULL,
          entity_type text NOT NULL,
          id_type text NOT NULL,
          id text NOT NULL,
          taxonomy_id text,
          resolver_source text,
          key_type text,
          mapping_type text
        ) ON COMMIT DROP
        """
    )


def _create_entity_group_taxonomy_conflict_table(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_group_taxonomy_conflict')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _entity_group_taxonomy_conflict ON COMMIT DROP AS
            SELECT DISTINCT
              k.entity_group_id,
              p.canonical_identifier,
              k.taxonomy_id AS evidence_taxonomy_id,
              NULLIF(p.taxonomy_id, '') AS resolver_taxonomy_id
            FROM _entity_group_key k
            JOIN {}.resolver_protein_identifier_lookup p
              ON p.key_identifier_type_id = k.key_identifier_type_id
             AND p.key_value = k.key_value
            LEFT JOIN _raw_group_resolution_candidate rc
              ON rc.entity_group_id = k.entity_group_id
            WHERE k.entity_type = ANY(%s)
              AND rc.entity_group_id IS NULL
              AND k.key_identifier_type_id IS NOT NULL
              AND p.canonical_identifier IS NOT NULL
              AND p.canonical_identifier <> ''
              AND k.taxonomy_id IS NOT NULL
              AND NULLIF(p.taxonomy_id, '') IS NOT NULL
              AND NULLIF(p.taxonomy_id, '') <> k.taxonomy_id
            """
        ).format(
            sql.Identifier(schema),
        ),
        [list(PROTEIN_ENTITY_TYPES)],
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_group_taxonomy_conflict (
          entity_group_id
        )
        """
    )
    cur.execute('ANALYZE _entity_group_taxonomy_conflict')


def _insert_group_protein_candidates(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO _raw_group_resolution_candidate (
              entity_group_id,
              entity_type,
              id_type,
              id,
              taxonomy_id,
              resolver_source,
              key_type,
              mapping_type
            )
            SELECT DISTINCT
              k.entity_group_id,
              k.entity_type,
              canonical_type.name,
              p.canonical_identifier,
              COALESCE(NULLIF(p.taxonomy_id, ''), k.taxonomy_id),
              NULL::text,
              k.key_type,
              NULL::text
            FROM _entity_group_key k
            JOIN {}.resolver_protein_identifier_lookup p
              ON p.key_identifier_type_id = k.key_identifier_type_id
             AND p.key_value = k.key_value
            JOIN {}.identifier_type canonical_type
              ON canonical_type.identifier_type_id =
                 p.canonical_identifier_type_id
            WHERE k.entity_type = ANY(%s)
              AND k.key_identifier_type_id IS NOT NULL
              AND p.canonical_identifier IS NOT NULL
              AND p.canonical_identifier <> ''
              AND k.taxonomy_id IS NOT NULL
              AND NULLIF(p.taxonomy_id, '') = k.taxonomy_id
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [list(PROTEIN_ENTITY_TYPES)],
    )


def _insert_group_chemical_candidates(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO _raw_group_resolution_candidate (
              entity_group_id,
              entity_type,
              id_type,
              id,
              taxonomy_id,
              resolver_source,
              key_type,
              mapping_type
            )
            SELECT DISTINCT
              k.entity_group_id,
              k.entity_type,
              canonical_type.name,
              c.canonical_identifier,
              k.taxonomy_id,
              NULL::text,
              k.key_type,
              NULL::text
            FROM _entity_group_key k
            JOIN {}.resolver_chemical_identifier_lookup c
              ON c.key_identifier_type_id = k.key_identifier_type_id
             AND c.key_value = k.key_value
            JOIN {}.identifier_type canonical_type
              ON canonical_type.identifier_type_id =
                 c.canonical_identifier_type_id
            WHERE k.entity_type = ANY(%s)
              AND k.key_identifier_type_id IS NOT NULL
              AND c.canonical_identifier IS NOT NULL
              AND c.canonical_identifier <> ''
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema)),
        [list(CHEMICAL_ENTITY_TYPES)],
    )


def _insert_group_standard_inchi_key_identity_candidates(
    cur: psycopg2.extensions.cursor,
) -> None:
    cur.execute(
        """
        INSERT INTO _raw_group_resolution_candidate (
          entity_group_id,
          entity_type,
          id_type,
          id,
          taxonomy_id,
          resolver_source,
          key_type,
          mapping_type
        )
        SELECT
          entity_group_id,
          entity_type,
          %s,
          key_value,
          taxonomy_id,
          'identity',
          key_type,
          'standard_inchi_key_identity'
        FROM _entity_group_key
        WHERE entity_type = ANY(%s)
          AND resolver_key_type = %s
          AND key_value IS NOT NULL
          AND key_value <> ''
        """,
        [
            STANDARD_INCHI_KEY_TYPE,
            list(CHEMICAL_ENTITY_TYPES),
            STANDARD_INCHI_KEY_TYPE,
        ],
    )


def _aggregate_group_candidates(cur: psycopg2.extensions.cursor) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_group_resolution_candidate')
    cur.execute(
        """
        CREATE TEMP TABLE _entity_group_resolution_candidate ON COMMIT DROP AS
        SELECT
          entity_group_id,
          entity_type,
          id_type,
          id,
          md5(id) AS id_hash,
          CASE
            WHEN COUNT(DISTINCT taxonomy_id) = 1
              THEN MIN(taxonomy_id)
            ELSE NULL
          END AS taxonomy_id,
          COUNT(*) AS support_count,
          COALESCE(
            ARRAY_AGG(DISTINCT resolver_source ORDER BY resolver_source)
              FILTER (WHERE resolver_source IS NOT NULL),
            ARRAY[]::text[]
          ) AS resolver_sources,
          ARRAY_AGG(DISTINCT key_type ORDER BY key_type) AS key_types,
          ARRAY_AGG(DISTINCT mapping_type ORDER BY mapping_type)
            FILTER (WHERE mapping_type IS NOT NULL) AS mapping_types
        FROM _raw_group_resolution_candidate
        GROUP BY
          entity_group_id,
          entity_type,
          id_type,
          id
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _entity_group_resolution_candidate (
          entity_group_id,
          entity_type,
          id_type,
          id_hash
        )
        """
    )
    cur.execute('ANALYZE _entity_group_resolution_candidate')


def _create_entity_group_resolution_stage(
    cur: psycopg2.extensions.cursor,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_group_resolution_stage')
    cur.execute(
        """
        CREATE TEMP TABLE _entity_group_resolution_stage ON COMMIT DROP AS
        WITH candidate_presence AS (
          SELECT DISTINCT c.entity_group_id
          FROM _entity_group_resolution_candidate c
        ),
        ranked_candidates AS (
          SELECT
            c.*,
            CASE
              WHEN c.key_types && %s::text[]
                OR COALESCE(c.mapping_types, ARRAY[]::text[])
                   && %s::text[]
                THEN 100
              WHEN c.key_types && %s::text[]
                THEN 80
              WHEN c.key_types && %s::text[]
                THEN 20
              ELSE 0
            END AS resolution_rank
          FROM _entity_group_resolution_candidate c
        ),
        best_rank AS (
          SELECT
            entity_group_id,
            MAX(resolution_rank) AS resolution_rank
          FROM ranked_candidates
          GROUP BY entity_group_id
        ),
        selected_candidate_state AS (
          SELECT
            c.entity_group_id,
            COUNT(*) > 1 AS has_multiple_best
          FROM ranked_candidates c
          JOIN best_rank b
            ON b.entity_group_id = c.entity_group_id
           AND b.resolution_rank = c.resolution_rank
          GROUP BY c.entity_group_id
        ),
        singleton AS (
          SELECT
            c.entity_group_id,
            MAX(c.entity_type) AS entity_type,
            MAX(c.id_type) AS id_type,
            MAX(c.id) AS id,
            MAX(c.id_hash) AS id_hash,
            MAX(c.taxonomy_id) AS taxonomy_id
          FROM ranked_candidates c
          JOIN best_rank b
            ON b.entity_group_id = c.entity_group_id
           AND b.resolution_rank = c.resolution_rank
          GROUP BY c.entity_group_id
          HAVING COUNT(*) = 1
        ),
        fallback AS (
          SELECT
            g.entity_group_id,
            md5(
              COALESCE(g.entity_type, '') || '|' ||
              COALESCE(g.taxonomy_id, '') || '|' ||
              COALESCE(
                string_agg(
                  k.key_type || '=' || k.key_value,
                  '|'
                  ORDER BY k.key_type, k.key_value
                ),
                'no_identifiers'
              )
            ) AS id
          FROM _entity_group g
          LEFT JOIN _entity_group_key k
            ON k.entity_group_id = g.entity_group_id
          GROUP BY g.entity_group_id, g.entity_type, g.taxonomy_id
        ),
        taxonomy_conflicts AS (
          SELECT entity_group_id, COUNT(*) AS conflict_count
          FROM _entity_group_taxonomy_conflict
          GROUP BY entity_group_id
        )
        SELECT
          g.entity_group_id,
          CASE
            WHEN g.entity_type IS NULL
              THEN 'unsupported'
            WHEN cp.entity_group_id IS NULL
              THEN 'unresolved'
            WHEN si.entity_group_id IS NOT NULL
              THEN 'resolved'
            ELSE 'ambiguous'
          END AS status,
          CASE
            WHEN si.entity_group_id IS NOT NULL
              THEN si.entity_type
            WHEN g.entity_type IS NOT NULL
              THEN g.entity_type
            ELSE NULL
          END AS entity_type,
          CASE
            WHEN si.entity_group_id IS NOT NULL
              THEN si.id_type
            WHEN si.entity_group_id IS NULL
             AND g.entity_type IS NOT NULL
              THEN %s
            ELSE NULL
          END AS id_type,
          CASE
            WHEN si.entity_group_id IS NOT NULL
              THEN si.id
            WHEN si.entity_group_id IS NULL
             AND g.entity_type IS NOT NULL
              THEN fb.id
            ELSE NULL
          END AS id,
          CASE
            WHEN si.entity_group_id IS NOT NULL
              THEN si.taxonomy_id
            WHEN si.entity_group_id IS NULL
             AND g.entity_type IS NOT NULL
              THEN g.taxonomy_id
            ELSE NULL
          END AS taxonomy_id,
          CASE
            WHEN g.entity_type IS NULL
              THEN 'missing_entity_type'
            WHEN cp.entity_group_id IS NULL
             AND COALESCE(tc.conflict_count, 0) > 0
              THEN 'different_taxon'
            WHEN cp.entity_group_id IS NULL
              THEN 'no_accepted_resolver_candidate'
            WHEN scs.has_multiple_best
              THEN 'multiple_entity_candidates'
            ELSE NULL
          END AS reason
        FROM _entity_group g
        LEFT JOIN candidate_presence cp
          ON cp.entity_group_id = g.entity_group_id
        LEFT JOIN selected_candidate_state scs
          ON scs.entity_group_id = g.entity_group_id
        LEFT JOIN singleton si
          ON si.entity_group_id = g.entity_group_id
        LEFT JOIN fallback fb
          ON fb.entity_group_id = g.entity_group_id
        LEFT JOIN taxonomy_conflicts tc
          ON tc.entity_group_id = g.entity_group_id
        """,
        [
            list(DIRECT_IDENTIFIER_TYPES),
            list(DIRECT_MAPPING_TYPES),
            list(STABLE_REFERENCE_IDENTIFIER_TYPES),
            list(WEAK_IDENTIFIER_TYPES),
            FALLBACK_IDENTIFIER_TYPE,
        ],
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _entity_group_resolution_stage (
          entity_group_id
        )
        """
    )
    cur.execute('ANALYZE _entity_group_resolution_stage')


def _project_entity_group_resolution_stage(
    cur: psycopg2.extensions.cursor,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_resolution_stage')
    cur.execute(
        """
        CREATE TEMP TABLE _entity_resolution_stage ON COMMIT DROP AS
        SELECT
          gm.entity_evidence_id,
          gr.status,
          gr.entity_type,
          gr.id_type,
          gr.id,
          md5(gr.id) AS id_hash,
          gr.taxonomy_id,
          gr.reason
        FROM _entity_group_member gm
        JOIN _entity_group_resolution_stage gr
          ON gr.entity_group_id = gm.entity_group_id
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _entity_resolution_stage (
          entity_evidence_id
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_resolution_stage (
          entity_type,
          md5(id)
        )
        """
    )
    cur.execute('ANALYZE _entity_resolution_stage')


def _insert_entities(cur: psycopg2.extensions.cursor, schema: str) -> None:
    cur.execute(
        sql.SQL(
            """
            WITH staged AS (
              SELECT
                st.*,
                et.entity_type_id,
                it.identifier_type_id AS canonical_identifier_type_id,
                CASE
                  WHEN st.status = 'resolved' THEN 1::smallint
                  ELSE 2::smallint
                END AS entity_resolution_status_id
              FROM _entity_resolution_stage st
              LEFT JOIN {}.identifier_type it
                ON it.name = st.id_type
              JOIN {}.entity_type et
                ON et.name = st.entity_type
            )
            INSERT INTO {}.entity (
              entity_type_id,
              taxonomy_id,
              canonical_identifier_type_id,
              canonical_identifier,
              identifiers,
              resolution_status_id
            )
            SELECT
              entity_type_id,
              taxonomy_id,
              canonical_identifier_type_id,
              id AS canonical_identifier,
              '[]'::jsonb AS identifiers,
              MIN(entity_resolution_status_id) AS resolution_status_id
            FROM staged
            WHERE status IN ('resolved', 'unresolved', 'ambiguous')
              AND id IS NOT NULL
            GROUP BY
              entity_type_id,
              taxonomy_id,
              canonical_identifier_type_id,
              id
            ON CONFLICT (
              entity_type_id,
              taxonomy_id,
              canonical_identifier_type_id,
              canonical_identifier
            )
            DO UPDATE SET
              taxonomy_id = COALESCE({}.entity.taxonomy_id, EXCLUDED.taxonomy_id),
              identifiers = EXCLUDED.identifiers,
              resolution_status_id = LEAST(
                {}.entity.resolution_status_id,
                EXCLUDED.resolution_status_id
              )
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
    )


def _insert_scoped_entity_types(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_type (name)
            SELECT DISTINCT entity_type
            FROM _entity_resolution_stage
            WHERE entity_type IS NOT NULL
            ON CONFLICT (name) DO NOTHING
            """
        ).format(sql.Identifier(schema))
    )


def _upsert_entity_resolution(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_evidence_resolution (
              entity_evidence_id,
              status_id,
              entity_id,
              reason_id,
              resolved_at
            )
            SELECT
              st.entity_evidence_id,
              rs.resolution_status_id,
              e.entity_id,
              rr.resolution_reason_id,
              now()
            FROM _entity_resolution_stage st
            LEFT JOIN {}.identifier_type it
              ON it.name = st.id_type
            JOIN {}.resolution_status rs
              ON rs.name = st.status
            LEFT JOIN {}.resolution_reason rr
              ON rr.name = st.reason
            LEFT JOIN {}.entity_type et
              ON et.name = st.entity_type
            LEFT JOIN {}.entity e
              ON e.entity_type_id = et.entity_type_id
             AND e.taxonomy_id IS NOT DISTINCT FROM st.taxonomy_id
             AND e.canonical_identifier_type_id IS NOT DISTINCT FROM
                 it.identifier_type_id
             AND e.canonical_identifier = st.id
            ON CONFLICT (entity_evidence_id)
            DO UPDATE SET
              status_id = EXCLUDED.status_id,
              entity_id = EXCLUDED.entity_id,
              reason_id = EXCLUDED.reason_id,
              resolved_at = now()
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        )
    )


def _insert_entity_annotations(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_annotation (
              entity_id,
              annotation_key,
              scope
            )
            SELECT DISTINCT
              er.entity_id,
              eea.annotation_key,
              eea.scope
            FROM _entity_scope es
            JOIN {}.entity_evidence_resolution er
              ON er.entity_evidence_id = es.entity_evidence_id
            JOIN {}.entity_evidence_annotation eea
              ON eea.entity_evidence_id = es.entity_evidence_id
            WHERE er.entity_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        )
    )


def _create_relation_scope(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
    source: str | None,
    dataset: str | None,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _relation_scope')
    where = []
    params: list[Any] = []
    if source is not None:
        where.append('re.source = %s')
        params.append(source)
    if dataset is not None:
        where.append('re.dataset = %s')
        params.append(dataset)
    where_sql = (
        sql.SQL('WHERE ')
        + sql.SQL(' AND ').join(sql.SQL(part) for part in where)
        if where
        else sql.SQL('')
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _relation_scope ON COMMIT DROP AS
            SELECT re.relation_evidence_id
            FROM {}.relation_evidence re
            {}
            """
        ).format(sql.Identifier(schema), where_sql),
        params,
    )
    cur.execute('CREATE UNIQUE INDEX ON _relation_scope (relation_evidence_id)')
    cur.execute('ANALYZE _relation_scope')


def _create_relation_endpoint(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _relation_endpoint')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _relation_endpoint ON COMMIT DROP AS
            SELECT
              re.relation_evidence_id,
              re.subject_entity_evidence_id,
              re.subject_entity_id AS direct_subject_entity_id,
              re.object_entity_evidence_id,
              re.object_entity_id AS direct_object_entity_id,
              re.predicate,
              re.relation_category,
              CASE
                WHEN re.subject_entity_id IS NOT NULL THEN 'resolved'
                ELSE srs.name
              END AS subject_status,
              COALESCE(re.subject_entity_id, sr.entity_id) AS subject_entity_id,
              CASE
                WHEN re.object_entity_id IS NOT NULL THEN 'resolved'
                ELSE ors.name
              END AS object_status,
              COALESCE(re.object_entity_id, orr.entity_id) AS object_entity_id
            FROM _relation_scope rs
            JOIN {}.relation_evidence re
              ON re.relation_evidence_id = rs.relation_evidence_id
            LEFT JOIN {}.entity_evidence_resolution sr
              ON sr.entity_evidence_id = re.subject_entity_evidence_id
            LEFT JOIN {}.resolution_status srs
              ON srs.resolution_status_id = sr.status_id
            LEFT JOIN {}.entity_evidence_resolution orr
              ON orr.entity_evidence_id = re.object_entity_evidence_id
            LEFT JOIN {}.resolution_status ors
              ON ors.resolution_status_id = orr.status_id
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        )
    )
    cur.execute(
        """
        CREATE INDEX ON _relation_endpoint (
          subject_entity_id,
          predicate,
          object_entity_id,
          relation_category
        )
        """
    )
    cur.execute('ANALYZE _relation_endpoint')


def _delete_scoped_relation_evidence(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _affected_relation')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _affected_relation ON COMMIT DROP AS
            SELECT DISTINCT rer.relation_id
            FROM {}.relation_evidence_relation rer
            JOIN _relation_scope rs
              ON rs.relation_evidence_id = rer.relation_evidence_id
            """
        ).format(sql.Identifier(schema))
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _affected_relation (
          relation_id
        )
        """
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.relation_evidence_relation rer
            USING _relation_scope rs
            WHERE rer.relation_evidence_id = rs.relation_evidence_id
            """
        ).format(sql.Identifier(schema))
    )


def _insert_relations(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.relation (
              subject_entity_id,
              predicate,
              object_entity_id,
              relation_category
            )
            SELECT DISTINCT
              subject_entity_id,
              predicate,
              object_entity_id,
              relation_category
            FROM _relation_endpoint
            WHERE subject_entity_id IS NOT NULL
              AND object_entity_id IS NOT NULL
            ON CONFLICT (
              subject_entity_id,
              predicate,
              object_entity_id,
              relation_category
            )
            DO NOTHING
            """
        ).format(sql.Identifier(schema))
    )


def _insert_relation_evidence_links(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _relation_link')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _relation_link ON COMMIT DROP AS
            SELECT
              r.relation_id,
              ep.relation_evidence_id
            FROM _relation_endpoint ep
            JOIN {}.relation r
              ON r.subject_entity_id = ep.subject_entity_id
             AND r.predicate = ep.predicate
             AND r.object_entity_id = ep.object_entity_id
             AND r.relation_category IS NOT DISTINCT FROM ep.relation_category
            WHERE ep.subject_entity_id IS NOT NULL
              AND ep.object_entity_id IS NOT NULL
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _relation_link (
          relation_evidence_id
        )
        """
    )
    cur.execute('ANALYZE _relation_link')
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.relation_evidence_relation (
              relation_id,
              relation_evidence_id
            )
            SELECT
              relation_id,
              relation_evidence_id
            FROM _relation_link
            ON CONFLICT DO NOTHING
            """
        ).format(sql.Identifier(schema))
    )


def _refresh_relation_annotations(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        """
        INSERT INTO _affected_relation (relation_id)
        SELECT DISTINCT relation_id
        FROM _relation_link
        ON CONFLICT DO NOTHING
        """
    )
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.relation_annotation ra
            USING _affected_relation ar
            WHERE ra.relation_id = ar.relation_id
            """
        ).format(sql.Identifier(schema))
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.relation_annotation (
              relation_id,
              annotation_key,
              scope
            )
            SELECT
              rer.relation_id,
              rea.annotation_key,
              rea.scope
            FROM {}.relation_evidence_relation rer
            JOIN _affected_relation ar
              ON ar.relation_id = rer.relation_id
            JOIN {}.relation_evidence_annotation rea
              ON rea.relation_evidence_id = rer.relation_evidence_id
            ON CONFLICT DO NOTHING
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        )
    )


def _count(cur: psycopg2.extensions.cursor, table: str) -> int:
    cur.execute(
        sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))
    )
    return int(cur.fetchone()[0])


def _count_schema_table(
    cur: psycopg2.extensions.cursor,
    schema: str,
    table: str,
) -> int:
    cur.execute(
        sql.SQL('SELECT COUNT(*) FROM {}.{}').format(
            sql.Identifier(schema),
            sql.Identifier(table),
        )
    )
    return int(cur.fetchone()[0])


def _relation_mapping_counts(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> tuple[int, int]:
    cur.execute(
        sql.SQL(
            """
            SELECT
              COUNT(rer.relation_evidence_id) AS mapped,
              COUNT(*) - COUNT(rer.relation_evidence_id) AS unmapped
            FROM _relation_scope rs
            LEFT JOIN {}.relation_evidence_relation rer
              ON rer.relation_evidence_id = rs.relation_evidence_id
            """
        ).format(sql.Identifier(schema))
    )
    mapped, unmapped = cur.fetchone()
    return int(mapped), int(unmapped)


def _status_counts(
    cur: psycopg2.extensions.cursor,
    schema: str,
    table: str,
) -> dict[str, int]:
    cur.execute(
        sql.SQL(
            """
            SELECT rs.name, COUNT(*)
            FROM {}.{} t
            JOIN {}.resolution_status rs
              ON rs.resolution_status_id = t.status_id
            GROUP BY rs.name
            ORDER BY rs.name
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.Identifier(schema),
        )
    )
    return {str(status): int(count) for status, count in cur.fetchall()}
