from __future__ import annotations

import logging

from psycopg2 import sql
import psycopg2.extensions

logger = logging.getLogger(__name__)

CV_TERM_ENTITY_TYPE = 'OM:0012:Cv Term'
CV_TERM_ACCESSION_TYPE = 'OM:0204:Cv Term Accession'
NAME_TERM = 'OM:0202:Name'
SYNONYM_TERM = 'OM:0203:Synonym'
DEFINITION_TERM = 'OM:0801:Definition'
ONTOLOGY_ID_SHORT_TERM = 'OM:0803'
ONTOLOGY_ID_TERM = 'OM:0803:Ontology Id'


def create_entity_relation_counts_materialized_view(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    """Create precomputed per-entity relation counts for search relevance."""
    logger.info(
        'Creating entity_relation_counts materialized view in schema %s', schema
    )

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                'DROP MATERIALIZED VIEW IF EXISTS {}.entity_relation_counts'
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE MATERIALIZED VIEW {}.entity_relation_counts AS
                SELECT entity_pk, COUNT(DISTINCT relation_pk)::bigint AS relation_count
                FROM (
                  SELECT subject_entity_pk AS entity_pk, relation_pk FROM {}.entity_relation
                  UNION ALL
                  SELECT object_entity_pk AS entity_pk, relation_pk FROM {}.entity_relation
                ) relation_endpoints
                GROUP BY entity_pk
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                'CREATE UNIQUE INDEX entity_relation_counts_pk_idx ON {}.entity_relation_counts (entity_pk)'
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                'CREATE INDEX entity_relation_counts_count_idx ON {}.entity_relation_counts (relation_count DESC, entity_pk ASC)'
            ).format(sql.Identifier(schema))
        )

    conn.commit()


def create_ontology_terms_materialized_view(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    """Create the precomputed ontology term search materialized view."""
    logger.info(
        'Creating ontology_terms materialized view in schema %s', schema
    )

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                'DROP MATERIALIZED VIEW IF EXISTS {}.ontology_terms'
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE MATERIALIZED VIEW {}.ontology_terms AS
                WITH term_entities AS (
                  SELECT
                    e.entity_pk,
                    e.canonical_identifier,
                    e.sources,
                    CASE
                      WHEN jsonb_typeof(COALESCE(e.entity_attributes, '[]'::jsonb)) = 'array'
                      THEN COALESCE(e.entity_attributes, '[]'::jsonb)
                      ELSE '[]'::jsonb
                    END AS attributes
                  FROM {}.entity e
                  WHERE e.entity_type = {}
                    AND e.canonical_identifier_type = {}
                ),
                identifier_values AS (
                  SELECT
                    te.entity_pk,
                    (ARRAY_AGG(ei.identifier ORDER BY ei.id) FILTER (
                      WHERE ei.identifier_type = {}
                        AND COALESCE(ei.identifier, '') <> ''
                    ))[1] AS name,
                    ARRAY_AGG(DISTINCT ei.identifier ORDER BY ei.identifier) FILTER (
                      WHERE ei.identifier_type = {}
                        AND COALESCE(ei.identifier, '') <> ''
                    ) AS synonyms
                  FROM term_entities te
                  LEFT JOIN {}.entity_identifier ei ON ei.entity_pk = te.entity_pk
                  GROUP BY te.entity_pk
                ),
                attribute_values AS (
                  SELECT
                    te.entity_pk,
                    (ARRAY_AGG(attr.item->>'value' ORDER BY attr.ordinality) FILTER (
                      WHERE attr.item->>'term' = {}
                        AND COALESCE(attr.item->>'value', '') <> ''
                    ))[1] AS name,
                    (ARRAY_AGG(attr.item->>'value' ORDER BY attr.ordinality) FILTER (
                      WHERE attr.item->>'term' = {}
                        AND COALESCE(attr.item->>'value', '') <> ''
                    ))[1] AS definition,
                    (ARRAY_AGG(attr.item->>'value' ORDER BY attr.ordinality) FILTER (
                      WHERE attr.item->>'term' IN ({}, {})
                        AND COALESCE(attr.item->>'value', '') <> ''
                    ))[1] AS ontology_id,
                    ARRAY_AGG(DISTINCT attr.item->>'value' ORDER BY attr.item->>'value') FILTER (
                      WHERE attr.item->>'term' = {}
                        AND COALESCE(attr.item->>'value', '') <> ''
                    ) AS synonyms
                  FROM term_entities te
                  LEFT JOIN LATERAL jsonb_array_elements(te.attributes)
                    WITH ORDINALITY AS attr(item, ordinality)
                    ON true
                  GROUP BY te.entity_pk
                )
                SELECT
                  term_entity_pk,
                  term_id,
                  ontology_prefix,
                  label,
                  definition,
                  ontology_id,
                  synonyms,
                  array_to_string(synonyms, ' ') AS synonyms_text,
                  sources
                FROM (
                  SELECT
                    te.entity_pk AS term_entity_pk,
                    te.canonical_identifier AS term_id,
                    CASE
                      WHEN te.canonical_identifier ~* '^KW-[0-9]+$' THEN 'kw'
                      ELSE lower(split_part(te.canonical_identifier, ':', 1))
                    END AS ontology_prefix,
                    COALESCE(iv.name, av.name, te.canonical_identifier) AS label,
                    av.definition,
                    av.ontology_id,
                    COALESCE(
                      ARRAY(
                        SELECT DISTINCT synonym.value
                        FROM unnest(COALESCE(iv.synonyms, '{{}}'::text[]) || COALESCE(av.synonyms, '{{}}'::text[])) AS synonym(value)
                        WHERE COALESCE(synonym.value, '') <> ''
                        ORDER BY synonym.value
                      ),
                      '{{}}'::text[]
                    ) AS synonyms,
                    te.sources
                  FROM term_entities te
                  LEFT JOIN identifier_values iv ON iv.entity_pk = te.entity_pk
                  LEFT JOIN attribute_values av ON av.entity_pk = te.entity_pk
                ) terms
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Literal(CV_TERM_ENTITY_TYPE),
                sql.Literal(CV_TERM_ACCESSION_TYPE),
                sql.Literal(NAME_TERM),
                sql.Literal(SYNONYM_TERM),
                sql.Identifier(schema),
                sql.Literal(NAME_TERM),
                sql.Literal(DEFINITION_TERM),
                sql.Literal(ONTOLOGY_ID_SHORT_TERM),
                sql.Literal(ONTOLOGY_ID_TERM),
                sql.Literal(SYNONYM_TERM),
            )
        )

        indexes = [
            sql.SQL(
                'CREATE UNIQUE INDEX ontology_terms_pk_idx ON {}.ontology_terms (term_entity_pk)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_term_id_idx ON {}.ontology_terms (term_id)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_ontology_id_idx ON {}.ontology_terms (ontology_id)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_ontology_prefix_idx ON {}.ontology_terms (ontology_prefix)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_sources_gin_idx ON {}.ontology_terms USING GIN (sources)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_term_id_trgm_idx ON {}.ontology_terms USING GIN (term_id gin_trgm_ops)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_label_trgm_idx ON {}.ontology_terms USING GIN (label gin_trgm_ops)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_definition_trgm_idx ON {}.ontology_terms USING GIN (definition gin_trgm_ops)'
            ).format(sql.Identifier(schema)),
            sql.SQL(
                'CREATE INDEX ontology_terms_synonyms_text_trgm_idx ON {}.ontology_terms USING GIN (synonyms_text gin_trgm_ops)'
            ).format(sql.Identifier(schema)),
        ]
        for statement in indexes:
            cur.execute(statement)

    conn.commit()
