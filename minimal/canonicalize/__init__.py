from __future__ import annotations

from typing import Any
from dataclasses import field, dataclass

from psycopg2 import sql
import psycopg2.extensions

PROTEIN_ENTITY_TYPES = (
    'MI:0326',
    'MI:0326:Protein',
    'MI:0250',
    'MI:0250:Gene',
    'protein',
    'gene',
)
CHEMICAL_ENTITY_TYPES = (
    'MI:0328',
    'MI:0328:Small Molecule',
    'OM:0011',
    'OM:0011:Lipid',
    'chemical',
    'small_molecule',
    'compound',
    'drug',
)
SUPPORTED_ENTITY_TYPES = PROTEIN_ENTITY_TYPES + CHEMICAL_ENTITY_TYPES
CV_TERM_ENTITY_TYPE = 'cv_term'
CV_TERM_ID_TYPE = 'cv_term_accession'
ONTOLOGY_IDENTIFIER_TERM = 'OM:0204'
ASSOCIATION_CATEGORY = 'association'
ASSOCIATION_PREDICATE = 'associated_with'
PATHWAY_PREDICATE = 'involved_in'

DEFAULT_POLICIES: tuple[
    tuple[str, str | None, str, str | None, str, bool], ...
] = (
    (
        'protein',
        'uniprot',
        'MI:1097:Uniprot',
        'uniprot_primary',
        'accept',
        False,
    ),
    (
        'protein',
        'uniprot',
        'MI:1097:Uniprot',
        'uniprot_secondary',
        'accept',
        False,
    ),
    (
        'protein',
        'uniprot',
        'MI:0476:Ensembl',
        'uniprot_reference',
        'accept',
        True,
    ),
    (
        'protein',
        'uniprot',
        'MI:0477:Entrez',
        'uniprot_reference',
        'accept',
        True,
    ),
    ('protein', 'uniprot', 'MI:1095:HGNC', 'uniprot_reference', 'accept', True),
    (
        'protein',
        'uniprot',
        'OM:0200:Gene Name Primary',
        'uniprot_reference',
        'accept',
        True,
    ),
    (
        'protein',
        'uniprot',
        'OM:0201:Gene Name Synonym',
        'uniprot_reference',
        'accept',
        True,
    ),
    (
        'protein',
        'uniprot',
        'OM:0221:Uniprot Entry Name',
        'uniprot_reference',
        'accept',
        True,
    ),
    ('chemical', 'chebi', 'MI:0474:Chebi', None, 'accept', False),
    ('chemical', 'hmdb', 'OM:0004:Hmdb', None, 'accept', False),
    ('chemical', 'lipidmaps', 'OM:0003:Lipidmaps', None, 'accept', False),
    ('chemical', 'swisslipids', 'OM:0009:Swisslipids', None, 'accept', False),
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
    schema: str = 'minimal',
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
            _insert_protein_candidates(cur, schema)
            _insert_chemical_candidates(cur, schema)
            _insert_standard_inchi_identity_candidates(cur)
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
              CASE i.type
                WHEN 'MI:1097' THEN 'MI:1097:Uniprot'
                WHEN 'MI:0476' THEN 'MI:0476:Ensembl'
                WHEN 'MI:0477' THEN 'MI:0477:Entrez'
                WHEN 'MI:1095' THEN 'MI:1095:HGNC'
                WHEN 'OM:0200' THEN 'OM:0200:Gene Name Primary'
                WHEN 'OM:0201' THEN 'OM:0201:Gene Name Synonym'
                WHEN 'OM:0221' THEN 'OM:0221:Uniprot Entry Name'
                WHEN 'MI:0474' THEN 'MI:0474:Chebi'
                WHEN 'OM:0004' THEN 'OM:0004:Hmdb'
                WHEN 'OM:0003' THEN 'OM:0003:Lipidmaps'
                WHEN 'OM:0009' THEN 'OM:0009:Swisslipids'
                WHEN 'MI:2010' THEN 'MI:2010:Standard Inchi'
                ELSE i.type
              END AS resolver_key_type,
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
              'protein',
              'uniprot_primary',
              p.primary_uniprot,
              NULLIF(p.taxonomy_id, ''),
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
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema)),
        [list(PROTEIN_ENTITY_TYPES)],
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
              'chemical',
              'standard_inchi',
              c.standard_inchi,
              NULL::text,
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
              AND c.standard_inchi IS NOT NULL
              AND c.standard_inchi <> ''
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema)),
        [list(CHEMICAL_ENTITY_TYPES)],
    )


def _insert_standard_inchi_identity_candidates(
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
          'chemical',
          'standard_inchi',
          key_value,
          NULL::text,
          'identity',
          key_type,
          'standard_inchi_identity'
        FROM _entity_key
        WHERE entity_type = ANY(%s)
          AND resolver_key_type = 'MI:2010:Standard Inchi'
          AND key_value IS NOT NULL
          AND key_value <> ''
        """,
        [list(CHEMICAL_ENTITY_TYPES)],
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
              MIN(taxonomy_id) AS taxonomy_id,
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
            singleton AS (
              SELECT
                c.entity_evidence_id,
                MAX(c.entity_type) AS entity_type,
                MAX(c.id_type) AS id_type,
                MAX(c.id) AS id,
                MAX(c.id_hash) AS id_hash,
                MIN(c.taxonomy_id) AS taxonomy_id
              FROM {}.entity_resolution_candidate c
              JOIN _entity_scope s
                ON s.entity_evidence_id = c.entity_evidence_id
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
            )
            SELECT
              s.entity_evidence_id,
              CASE
                WHEN ee.entity_type IS NULL
                  OR NOT (ee.entity_type = ANY(%s))
                  THEN 'unsupported'
                WHEN COALESCE(cc.candidate_count, 0) = 0
                  THEN 'unresolved'
                WHEN cc.candidate_count = 1
                  THEN 'resolved'
                ELSE 'ambiguous'
              END AS status,
              CASE
                WHEN cc.candidate_count = 1
                  THEN si.entity_type
                WHEN ee.entity_type = ANY(%s)
                  THEN 'protein'
                WHEN ee.entity_type = ANY(%s)
                  THEN 'chemical'
                ELSE NULL
              END AS entity_type,
              CASE
                WHEN cc.candidate_count = 1
                  THEN si.id_type
                WHEN COALESCE(cc.candidate_count, 0) <> 1
                 AND ee.entity_type = ANY(%s)
                  THEN 'evidence_identifier_set'
                ELSE NULL
              END AS id_type,
              CASE
                WHEN cc.candidate_count = 1
                  THEN si.id
                WHEN COALESCE(cc.candidate_count, 0) <> 1
                 AND ee.entity_type = ANY(%s)
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
                WHEN cc.candidate_count = 1
                  THEN si.taxonomy_id
                WHEN COALESCE(cc.candidate_count, 0) <> 1
                 AND ee.entity_type = ANY(%s)
                  THEN NULLIF(ee.taxonomy_id, '')
                ELSE NULL
              END AS taxonomy_id,
              COALESCE(cc.candidate_count, 0) AS candidate_count,
              CASE
                WHEN ee.entity_type IS NULL
                  OR NOT (ee.entity_type = ANY(%s))
                  THEN 'unsupported_entity_type'
                WHEN COALESCE(cc.candidate_count, 0) = 0
                  THEN 'no_accepted_resolver_candidate'
                WHEN cc.candidate_count > 1
                  THEN 'multiple_entity_candidates'
                ELSE NULL
              END AS reason
            FROM _entity_scope s
            JOIN {}.entity_evidence ee
              ON ee.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN candidate_counts cc
              ON cc.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN singleton si
              ON si.entity_evidence_id = s.entity_evidence_id
            LEFT JOIN fingerprint fp
              ON fp.entity_evidence_id = s.entity_evidence_id
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [
            list(SUPPORTED_ENTITY_TYPES),
            list(PROTEIN_ENTITY_TYPES),
            list(CHEMICAL_ENTITY_TYPES),
            list(SUPPORTED_ENTITY_TYPES),
            list(SUPPORTED_ENTITY_TYPES),
            list(SUPPORTED_ENTITY_TYPES),
            list(SUPPORTED_ENTITY_TYPES),
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
            SELECT DISTINCT
              entity_type,
              id_type,
              id,
              taxonomy_id,
              CASE
                WHEN status = 'resolved'
                  THEN 'resolved'
                ELSE 'unresolved'
              END AS resolution_status
            FROM _entity_resolution_stage
            WHERE status IN ('resolved', 'unresolved', 'ambiguous')
              AND entity_type IS NOT NULL
              AND id_type IS NOT NULL
              AND id IS NOT NULL
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
