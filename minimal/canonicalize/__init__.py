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
    'uniprot_primary',
    'uniprot_secondary',
    'standard_inchi_key_identity',
    'standard_inchi_identity',
)
ASSOCIATION_CATEGORY = 'association'
ASSOCIATION_PREDICATE = 'associated_with'
PATHWAY_PREDICATE = 'involved_in'

DEFAULT_POLICIES: tuple[
    tuple[str, str | None, str, str | None, str, bool], ...
] = (
    (
        'protein',
        'uniprot',
        UNIPROT_TYPE,
        'uniprot_primary',
        'accept',
        False,
    ),
    (
        'protein',
        'uniprot',
        UNIPROT_TYPE,
        'uniprot_secondary',
        'accept',
        False,
    ),
    (
        'protein',
        'uniprot',
        ENSEMBL_TYPE,
        'uniprot_reference',
        'accept',
        True,
    ),
    (
        'protein',
        'uniprot',
        ENTREZ_TYPE,
        'uniprot_reference',
        'accept',
        True,
    ),
    ('protein', 'uniprot', HGNC_TYPE, 'uniprot_reference', 'accept', True),
    (
        'protein',
        'uniprot',
        GENE_NAME_PRIMARY_TYPE,
        'uniprot_reference',
        'accept',
        True,
    ),
    (
        'protein',
        'uniprot',
        GENE_NAME_SYNONYM_TYPE,
        'uniprot_reference',
        'accept',
        True,
    ),
    (
        'protein',
        'uniprot',
        UNIPROT_ENTRY_NAME_TYPE,
        'uniprot_reference',
        'accept',
        True,
    ),
    ('chemical', 'chebi', CHEBI_TYPE, None, 'accept', False),
    ('chemical', 'pubchem', PUBCHEM_COMPOUND_TYPE, None, 'accept', False),
    ('chemical', 'hmdb', HMDB_TYPE, None, 'accept', False),
    ('chemical', 'lipidmaps', LIPIDMAPS_TYPE, None, 'accept', False),
    ('chemical', 'swisslipids', SWISSLIPIDS_TYPE, None, 'accept', False),
)


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
        _ensure_default_policy(cur, schema)
        _create_entity_scope(
            cur,
            schema=schema,
            source=source,
            dataset=dataset,
            unresolved_only=unresolved_only,
        )
        entity_scope = _count(cur, '_entity_scope')
        if entity_scope:
            _create_entity_keys(cur, schema)
            _delete_scoped_candidates(cur, schema)
            _create_raw_candidate_table(cur)
            _create_entity_taxonomy_conflict_table(cur, schema)
            _insert_protein_candidates(cur, schema)
            _insert_chemical_candidates(cur, schema)
            _insert_standard_inchi_key_identity_candidates(cur)
            _insert_standard_inchi_identity_candidates(cur, schema)
            _insert_chemical_resolver_identifier_links(cur, schema)
            _aggregate_candidates(cur, schema)
            _create_entity_resolution_stage(cur, schema)
            _insert_entities(cur, schema)
            _upsert_entity_resolution(cur, schema)

        candidate_rows = _scoped_candidate_count(cur, schema)
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
                _insert_relation_evidence_annotation_links(cur, schema)
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


def _ensure_default_policy(
    cur: psycopg2.extensions.cursor, schema: str
) -> None:
    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.resolver_mapping_policy (
              entity_family,
              resolver_source,
              key_type,
              mapping_type,
              action,
              requires_taxonomy
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """
        )
        .format(sql.Identifier(schema))
        .as_string(cur.connection),
        DEFAULT_POLICIES,
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
              OR r.status <> 'resolved'
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
            """
        ).format(sql.Identifier(schema))
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
              i.type AS key_type,
              i.type AS resolver_key_type,
              i.value AS key_value,
              i.value_hash AS key_value_hash
            FROM _entity_scope s
            JOIN {}.entity_evidence ee
              ON ee.entity_evidence_id = s.entity_evidence_id
            JOIN {}.entity_evidence_identifier eei
              ON eei.entity_evidence_id = ee.entity_evidence_id
            JOIN {}.identifier i
              ON i.identifier_id = eei.identifier_id
            WHERE i.value IS NOT NULL
              AND i.value <> ''
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        )
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_key (
          entity_type,
          resolver_key_type,
          key_value_hash
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_key (
          resolver_key_type,
          key_value_hash,
          taxonomy_id
        )
        """
    )
    cur.execute('ANALYZE _entity_key')


def _delete_scoped_candidates(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.entity_resolution_candidate c
            USING _entity_scope s
            WHERE c.entity_evidence_id = s.entity_evidence_id
            """
        ).format(sql.Identifier(schema))
    )


def _create_raw_candidate_table(cur: psycopg2.extensions.cursor) -> None:
    cur.execute('DROP TABLE IF EXISTS _raw_resolution_candidate')
    cur.execute(
        """
        CREATE TEMP TABLE _raw_resolution_candidate (
          entity_evidence_id bigint NOT NULL,
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


def _create_entity_taxonomy_conflict_table(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_taxonomy_conflict')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _entity_taxonomy_conflict ON COMMIT DROP AS
            SELECT DISTINCT
              k.entity_evidence_id,
              p.primary_uniprot,
              k.taxonomy_id AS evidence_taxonomy_id,
              NULLIF(p.taxonomy_id, '') AS resolver_taxonomy_id
            FROM _entity_key k
            JOIN {}.resolver_protein_identifier_lookup p
              ON p.key_type = k.resolver_key_type
             AND md5(p.key_value) = k.key_value_hash
             AND p.key_value = k.key_value
            JOIN {}.resolver_mapping_policy pol
              ON pol.entity_family = 'protein'
             AND pol.key_type = p.key_type
             AND COALESCE(pol.mapping_type, '') = COALESCE(p.mapping_type, '')
             AND (
                  pol.resolver_source IS NULL
                  OR pol.resolver_source = p.source
             )
             AND pol.action = 'accept'
            WHERE k.entity_type = ANY(%s)
              AND p.primary_uniprot IS NOT NULL
              AND p.primary_uniprot <> ''
              AND k.taxonomy_id IS NOT NULL
              AND NULLIF(p.taxonomy_id, '') IS NOT NULL
              AND NULLIF(p.taxonomy_id, '') <> k.taxonomy_id
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [list(PROTEIN_ENTITY_TYPES)],
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_taxonomy_conflict (
          entity_evidence_id
        )
        """
    )
    cur.execute('ANALYZE _entity_taxonomy_conflict')


def _insert_protein_candidates(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO _raw_resolution_candidate (
              entity_evidence_id,
              entity_type,
              id_type,
              id,
              taxonomy_id,
              resolver_source,
              key_type,
              mapping_type
            )
            SELECT
              k.entity_evidence_id,
              k.entity_type,
              %s,
              p.primary_uniprot,
              k.taxonomy_id,
              p.source,
              k.key_type,
              p.mapping_type
            FROM _entity_key k
            JOIN {}.resolver_protein_identifier_lookup p
              ON p.key_type = k.resolver_key_type
             AND md5(p.key_value) = k.key_value_hash
             AND p.key_value = k.key_value
            JOIN {}.resolver_mapping_policy pol
              ON pol.entity_family = 'protein'
             AND pol.key_type = p.key_type
             AND COALESCE(pol.mapping_type, '') = COALESCE(p.mapping_type, '')
             AND (
                  pol.resolver_source IS NULL
                  OR pol.resolver_source = p.source
             )
             AND pol.action = 'accept'
            WHERE k.entity_type = ANY(%s)
              AND p.primary_uniprot IS NOT NULL
              AND p.primary_uniprot <> ''
              AND (
                    pol.requires_taxonomy = false
                    OR (
                      k.taxonomy_id IS NOT NULL
                      AND NULLIF(p.taxonomy_id, '') = k.taxonomy_id
                    )
              )
              AND NOT (
                    k.taxonomy_id IS NOT NULL
                    AND NULLIF(p.taxonomy_id, '') IS NOT NULL
                    AND NULLIF(p.taxonomy_id, '') <> k.taxonomy_id
              )
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [UNIPROT_TYPE, list(PROTEIN_ENTITY_TYPES)],
    )


def _insert_chemical_candidates(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO _raw_resolution_candidate (
              entity_evidence_id,
              entity_type,
              id_type,
              id,
              taxonomy_id,
              resolver_source,
              key_type,
              mapping_type
            )
            SELECT
              k.entity_evidence_id,
              k.entity_type,
              %s,
              c.standard_inchi_key,
              k.taxonomy_id,
              c.source,
              k.key_type,
              NULL::text
            FROM _entity_key k
            JOIN {}.resolver_chemical_identifier_lookup c
              ON c.key_type = k.resolver_key_type
             AND md5(c.key_value) = k.key_value_hash
             AND c.key_value = k.key_value
            JOIN {}.resolver_mapping_policy pol
              ON pol.entity_family = 'chemical'
             AND pol.key_type = c.key_type
             AND COALESCE(pol.mapping_type, '') = ''
             AND (
                  pol.resolver_source IS NULL
                  OR pol.resolver_source = c.source
             )
             AND pol.action = 'accept'
            WHERE k.entity_type = ANY(%s)
              AND c.standard_inchi_key IS NOT NULL
              AND c.standard_inchi_key <> ''
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema)),
        [STANDARD_INCHI_KEY_TYPE, list(CHEMICAL_ENTITY_TYPES)],
    )


def _insert_standard_inchi_key_identity_candidates(
    cur: psycopg2.extensions.cursor,
) -> None:
    cur.execute(
        """
        INSERT INTO _raw_resolution_candidate (
          entity_evidence_id,
          entity_type,
          id_type,
          id,
          taxonomy_id,
          resolver_source,
          key_type,
          mapping_type
        )
        SELECT
          entity_evidence_id,
          entity_type,
          %s,
          key_value,
          taxonomy_id,
          'identity',
          key_type,
          'standard_inchi_key_identity'
        FROM _entity_key
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


def _insert_standard_inchi_identity_candidates(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO _raw_resolution_candidate (
              entity_evidence_id,
              entity_type,
              id_type,
              id,
              taxonomy_id,
              resolver_source,
              key_type,
              mapping_type
            )
            SELECT DISTINCT
              k.entity_evidence_id,
              k.entity_type,
              %s,
              c.standard_inchi_key,
              k.taxonomy_id,
              'identity',
              k.key_type,
              'standard_inchi_identity'
            FROM _entity_key k
            JOIN {}.resolver_chemical_identifier_lookup c
              ON c.standard_inchi = k.key_value
            WHERE k.entity_type = ANY(%s)
              AND k.resolver_key_type = %s
              AND c.standard_inchi_key IS NOT NULL
              AND c.standard_inchi_key <> ''
            """
        ).format(sql.Identifier(schema)),
        [
            STANDARD_INCHI_KEY_TYPE,
            list(CHEMICAL_ENTITY_TYPES),
            STANDARD_INCHI_TYPE,
        ],
    )


def _insert_chemical_resolver_identifier_links(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _chemical_resolver_identifier')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _chemical_resolver_identifier ON COMMIT DROP AS
            WITH mapped AS (
              SELECT DISTINCT
                k.entity_evidence_id,
                c.standard_inchi_key,
                c.standard_inchi
              FROM _entity_key k
              JOIN {}.resolver_chemical_identifier_lookup c
                ON c.key_type = k.resolver_key_type
               AND md5(c.key_value) = k.key_value_hash
               AND c.key_value = k.key_value
              WHERE k.entity_type = ANY(%s)
              UNION
              SELECT DISTINCT
                k.entity_evidence_id,
                c.standard_inchi_key,
                c.standard_inchi
              FROM _entity_key k
              JOIN {}.resolver_chemical_identifier_lookup c
                ON c.standard_inchi_key = k.key_value
              WHERE k.entity_type = ANY(%s)
                AND k.resolver_key_type = %s
              UNION
              SELECT DISTINCT
                k.entity_evidence_id,
                c.standard_inchi_key,
                c.standard_inchi
              FROM _entity_key k
              JOIN {}.resolver_chemical_identifier_lookup c
                ON c.standard_inchi = k.key_value
              WHERE k.entity_type = ANY(%s)
                AND k.resolver_key_type = %s
            )
            SELECT DISTINCT
              entity_evidence_id,
              type,
              value
            FROM (
              SELECT
                entity_evidence_id,
                %s::text AS type,
                standard_inchi_key AS value
              FROM mapped
              UNION ALL
              SELECT
                entity_evidence_id,
                %s::text AS type,
                standard_inchi AS value
              FROM mapped
            ) identifiers
            WHERE value IS NOT NULL
              AND value <> ''
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [
            list(CHEMICAL_ENTITY_TYPES),
            list(CHEMICAL_ENTITY_TYPES),
            STANDARD_INCHI_KEY_TYPE,
            list(CHEMICAL_ENTITY_TYPES),
            STANDARD_INCHI_TYPE,
            STANDARD_INCHI_KEY_TYPE,
            STANDARD_INCHI_TYPE,
        ],
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX ON _chemical_resolver_identifier (
          entity_evidence_id,
          type,
          md5(value)
        )
        """
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.identifier (type, value)
            SELECT DISTINCT type, value
            FROM _chemical_resolver_identifier
            ON CONFLICT (type, value_hash) DO NOTHING
            """
        ).format(sql.Identifier(schema))
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_evidence_identifier (
              entity_evidence_id,
              identifier_id
            )
            SELECT DISTINCT
              r.entity_evidence_id,
              i.identifier_id
            FROM _chemical_resolver_identifier r
            JOIN {}.identifier i
              ON i.type = r.type
             AND i.value_hash = md5(r.value)
             AND i.value = r.value
            ON CONFLICT DO NOTHING
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
    )


def _aggregate_candidates(cur: psycopg2.extensions.cursor, schema: str) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_resolution_candidate (
              entity_evidence_id,
              entity_type,
              id_type,
              id,
              taxonomy_id,
              support_count,
              resolver_sources,
              key_types,
              mapping_types
            )
            SELECT
              entity_evidence_id,
              entity_type,
              id_type,
              id,
              CASE
                WHEN COUNT(DISTINCT taxonomy_id) = 1
                  THEN MIN(taxonomy_id)
                ELSE NULL
              END AS taxonomy_id,
              COUNT(*) AS support_count,
              ARRAY_AGG(DISTINCT resolver_source ORDER BY resolver_source)
                FILTER (WHERE resolver_source IS NOT NULL) AS resolver_sources,
              ARRAY_AGG(DISTINCT key_type ORDER BY key_type) AS key_types,
              ARRAY_AGG(DISTINCT mapping_type ORDER BY mapping_type)
                FILTER (WHERE mapping_type IS NOT NULL) AS mapping_types
            FROM _raw_resolution_candidate
            GROUP BY
              entity_evidence_id,
              entity_type,
              id_type,
              id
            ON CONFLICT (
              entity_evidence_id,
              entity_type,
              id_type,
              id_hash
            )
            DO UPDATE SET
              taxonomy_id = EXCLUDED.taxonomy_id,
              support_count = EXCLUDED.support_count,
              resolver_sources = EXCLUDED.resolver_sources,
              key_types = EXCLUDED.key_types,
              mapping_types = EXCLUDED.mapping_types,
              created_at = now()
            """
        ).format(sql.Identifier(schema))
    )


def _create_entity_resolution_stage(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute('DROP TABLE IF EXISTS _entity_resolution_stage')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _entity_resolution_stage ON COMMIT DROP AS
            WITH candidate_counts AS (
              SELECT
                c.entity_evidence_id,
                COUNT(*) AS candidate_count
              FROM {}.entity_resolution_candidate c
              JOIN _entity_scope s
                ON s.entity_evidence_id = c.entity_evidence_id
              GROUP BY c.entity_evidence_id
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
              FROM {}.entity_resolution_candidate c
              JOIN _entity_scope s
                ON s.entity_evidence_id = c.entity_evidence_id
            ),
            best_rank AS (
              SELECT
                entity_evidence_id,
                MAX(resolution_rank) AS resolution_rank
              FROM ranked_candidates
              GROUP BY entity_evidence_id
            ),
            selected_candidate_counts AS (
              SELECT
                c.entity_evidence_id,
                COUNT(*) AS candidate_count
              FROM ranked_candidates c
              JOIN best_rank b
                ON b.entity_evidence_id = c.entity_evidence_id
               AND b.resolution_rank = c.resolution_rank
              GROUP BY c.entity_evidence_id
            ),
            singleton AS (
              SELECT
                c.entity_evidence_id,
                MAX(c.entity_type) AS entity_type,
                MAX(c.id_type) AS id_type,
                MAX(c.id) AS id,
                MAX(c.id_hash) AS id_hash,
                MAX(c.taxonomy_id) AS taxonomy_id
              FROM ranked_candidates c
              JOIN best_rank b
                ON b.entity_evidence_id = c.entity_evidence_id
               AND b.resolution_rank = c.resolution_rank
              GROUP BY c.entity_evidence_id
              HAVING COUNT(*) = 1
            ),
            fingerprint AS (
              SELECT
                k.entity_evidence_id,
                'fallback:' || md5(
                  COALESCE(k.entity_type, '') || '|' ||
                  COALESCE(k.taxonomy_id, '') || '|' ||
                  string_agg(
                    k.key_type || '=' || k.key_value,
                    '|'
                    ORDER BY k.key_type, k.key_value
                  )
                ) AS id
              FROM _entity_key k
              GROUP BY k.entity_evidence_id, k.entity_type, k.taxonomy_id
            ),
            taxonomy_conflicts AS (
              SELECT entity_evidence_id, COUNT(*) AS conflict_count
              FROM _entity_taxonomy_conflict
              GROUP BY entity_evidence_id
            )
            SELECT
              s.entity_evidence_id,
              CASE
                WHEN ee.entity_type IS NULL
                  THEN 'unsupported'
                WHEN COALESCE(cc.candidate_count, 0) = 0
                  THEN 'unresolved'
                WHEN scc.candidate_count = 1
                  THEN 'resolved'
                ELSE 'ambiguous'
              END AS status,
              CASE
                WHEN scc.candidate_count = 1
                  THEN si.entity_type
                WHEN ee.entity_type IS NOT NULL
                  THEN ee.entity_type
                ELSE NULL
              END AS entity_type,
              CASE
                WHEN scc.candidate_count = 1
                  THEN si.id_type
                WHEN COALESCE(scc.candidate_count, 0) <> 1
                 AND ee.entity_type IS NOT NULL
                  THEN 'evidence_identifier_set'
                ELSE NULL
              END AS id_type,
              CASE
                WHEN scc.candidate_count = 1
                  THEN si.id
                WHEN COALESCE(scc.candidate_count, 0) <> 1
                 AND ee.entity_type IS NOT NULL
                  THEN COALESCE(
                    fp.id,
                    'fallback:' || md5(
                      COALESCE(ee.entity_type, '') || '|' ||
                      COALESCE(NULLIF(ee.taxonomy_id, ''), '') || '|' ||
                      'no_identifiers|' || ee.entity_evidence_id::text
                    )
                  )
                ELSE NULL
              END AS id,
              CASE
                WHEN scc.candidate_count = 1
                  THEN si.taxonomy_id
                WHEN COALESCE(scc.candidate_count, 0) <> 1
                 AND ee.entity_type IS NOT NULL
                  THEN NULLIF(ee.taxonomy_id, '')
                ELSE NULL
              END AS taxonomy_id,
              COALESCE(scc.candidate_count, cc.candidate_count, 0)
                AS candidate_count,
              CASE
                WHEN ee.entity_type IS NULL
                  THEN 'missing_entity_type'
                WHEN COALESCE(cc.candidate_count, 0) = 0
                 AND COALESCE(tc.conflict_count, 0) > 0
                  THEN 'different_taxon'
                WHEN COALESCE(cc.candidate_count, 0) = 0
                  THEN 'no_accepted_resolver_candidate'
                WHEN scc.candidate_count > 1
                  THEN 'multiple_entity_candidates'
                ELSE NULL
              END AS reason
            FROM _entity_scope s
            JOIN {}.entity_evidence ee
              ON ee.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN candidate_counts cc
              ON cc.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN selected_candidate_counts scc
              ON scc.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN singleton si
              ON si.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN fingerprint fp
              ON fp.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN taxonomy_conflicts tc
              ON tc.entity_evidence_id = s.entity_evidence_id
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [
            list(DIRECT_IDENTIFIER_TYPES),
            list(DIRECT_MAPPING_TYPES),
            list(STABLE_REFERENCE_IDENTIFIER_TYPES),
            list(WEAK_IDENTIFIER_TYPES),
        ],
    )
    cur.execute(
        'CREATE UNIQUE INDEX ON _entity_resolution_stage (entity_evidence_id)'
    )
    cur.execute(
        """
        CREATE INDEX ON _entity_resolution_stage (
          entity_type,
          id_type,
          md5(id)
        )
        """
    )
    cur.execute('ANALYZE _entity_resolution_stage')


def _insert_entities(cur: psycopg2.extensions.cursor, schema: str) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity (
              entity_type,
              id_type,
              id,
              taxonomy_id,
              resolution_status
            )
            SELECT
              entity_type,
              id_type,
              id,
              CASE
                WHEN COUNT(DISTINCT taxonomy_id) = 1
                  THEN MIN(taxonomy_id)
                ELSE NULL
              END AS taxonomy_id,
              CASE
                WHEN BOOL_OR(status = 'resolved')
                  THEN 'resolved'
                ELSE 'unresolved'
              END AS resolution_status
            FROM _entity_resolution_stage
            WHERE status IN ('resolved', 'unresolved', 'ambiguous')
              AND entity_type IS NOT NULL
              AND id_type IS NOT NULL
              AND id IS NOT NULL
            GROUP BY entity_type, id_type, id
            ON CONFLICT (entity_type, id_type, id_hash)
            DO UPDATE SET
              taxonomy_id = COALESCE({}.entity.taxonomy_id, EXCLUDED.taxonomy_id),
              resolution_status = CASE
                WHEN EXCLUDED.resolution_status = 'resolved'
                  THEN 'resolved'
                ELSE 'unresolved'
              END
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
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
              status,
              entity_id,
              candidate_count,
              reason,
              resolved_at
            )
            SELECT
              st.entity_evidence_id,
              st.status,
              e.entity_id,
              st.candidate_count,
              st.reason,
              now()
            FROM _entity_resolution_stage st
            LEFT JOIN {}.entity e
              ON e.entity_type = st.entity_type
             AND e.id_type = st.id_type
             AND e.id_hash = md5(st.id)
             AND e.id = st.id
            ON CONFLICT (entity_evidence_id)
            DO UPDATE SET
              status = EXCLUDED.status,
              entity_id = EXCLUDED.entity_id,
              candidate_count = EXCLUDED.candidate_count,
              reason = EXCLUDED.reason,
              resolved_at = now()
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
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
                ELSE sr.status
              END AS subject_status,
              COALESCE(re.subject_entity_id, sr.entity_id) AS subject_entity_id,
              CASE
                WHEN re.object_entity_id IS NOT NULL THEN 'resolved'
                ELSE orr.status
              END AS object_status,
              COALESCE(re.object_entity_id, orr.entity_id) AS object_entity_id
            FROM _relation_scope rs
            JOIN {}.relation_evidence re
              ON re.relation_evidence_id = rs.relation_evidence_id
            LEFT JOIN {}.entity_evidence_resolution sr
              ON sr.entity_evidence_id = re.subject_entity_evidence_id
            LEFT JOIN {}.entity_evidence_resolution orr
              ON orr.entity_evidence_id = re.object_entity_evidence_id
            """
        ).format(
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
    cur.execute(
        sql.SQL(
            """
            DELETE FROM {}.relation_evidence_annotation rea
            USING _relation_scope rs
            WHERE rea.relation_evidence_id = rs.relation_evidence_id
            """
        ).format(sql.Identifier(schema))
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
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.relation_evidence_relation (
              relation_id,
              relation_evidence_id
            )
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
            ON CONFLICT DO NOTHING
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
    )


def _insert_relation_evidence_annotation_links(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.relation_evidence_annotation (
              relation_id,
              relation_evidence_id,
              annotation_id
            )
            SELECT
              rer.relation_id,
              rer.relation_evidence_id,
              a.annotation_id
            FROM {}.relation_evidence_relation rer
            JOIN _relation_scope rs
              ON rs.relation_evidence_id = rer.relation_evidence_id
            JOIN {}.annotation a
              ON a.relation_evidence_id = rer.relation_evidence_id
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


def _scoped_candidate_count(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    cur.execute(
        sql.SQL(
            """
            SELECT COUNT(*)
            FROM {}.entity_resolution_candidate c
            JOIN _entity_scope s
              ON s.entity_evidence_id = c.entity_evidence_id
            """
        ).format(sql.Identifier(schema))
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
            SELECT status, COUNT(*)
            FROM {}.{}
            GROUP BY status
            ORDER BY status
            """
        ).format(sql.Identifier(schema), sql.Identifier(table))
    )
    return {str(status): int(count) for status, count in cur.fetchall()}
