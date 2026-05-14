from __future__ import annotations

from dataclasses import dataclass

from psycopg2 import sql
import psycopg2.extensions
from pypath.internals.cv_terms import (
    IdentifierNamespaceCv,
    OntologyAnnotationCv,
    cv_term_label_accession,
)

from minimal.cv_terms import CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE

NAME_TERM = cv_term_label_accession(IdentifierNamespaceCv.NAME)
SYNONYM_TERM = cv_term_label_accession(IdentifierNamespaceCv.SYNONYM)
CV_TERM_ACCESSION_TERM = cv_term_label_accession(
    IdentifierNamespaceCv.CV_TERM_ACCESSION
)
DEFINITION_TERM = cv_term_label_accession(OntologyAnnotationCv.DEFINITION)
ONTOLOGY_ID_TERM = cv_term_label_accession(OntologyAnnotationCv.ONTOLOGY_ID)


@dataclass(frozen=True)
class DerivedTableStats:
    """Summary counts from derived table population."""

    entity_relation_counts: int = 0
    ontology_terms: int = 0


def rebuild_derived_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'minimal',
) -> DerivedTableStats:
    """Create and fully rebuild derived search/count tables."""

    with conn.cursor() as cur:
        _create_derived_tables(cur, schema)
        relation_counts = _populate_entity_relation_counts(cur, schema)
        ontology_terms = _populate_ontology_terms(cur, schema)
        _create_derived_indexes(cur, schema)
    conn.commit()
    return DerivedTableStats(
        entity_relation_counts=relation_counts,
        ontology_terms=ontology_terms,
    )


def _create_derived_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_relation_counts (
              entity_id bigint PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              relation_count bigint NOT NULL
            )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.ontology_terms (
              term_entity_id bigint PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              term_id text NOT NULL,
              ontology_prefix text,
              label text NOT NULL,
              definition text,
              ontology_id text,
              synonyms text[] NOT NULL DEFAULT '{{}}',
              synonyms_text text NOT NULL DEFAULT '',
              sources text[] NOT NULL DEFAULT '{{}}'
            )
            """
        ).format(schema_id, schema_id)
    )


def _populate_entity_relation_counts(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.entity_relation_counts').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_relation_counts (
              entity_id,
              relation_count
            )
            SELECT entity_id, COUNT(DISTINCT relation_id)::bigint
            FROM (
              SELECT subject_entity_id AS entity_id, relation_id
              FROM {}.relation
              UNION ALL
              SELECT object_entity_id AS entity_id, relation_id
              FROM {}.relation
            ) relation_endpoints
            GROUP BY entity_id
            """
        ).format(schema_id, schema_id, schema_id)
    )
    return int(cur.rowcount)


def _populate_ontology_terms(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(sql.SQL('TRUNCATE {}.ontology_terms').format(schema_id))
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.ontology_terms (
              term_entity_id,
              term_id,
              ontology_prefix,
              label,
              definition,
              ontology_id,
              synonyms,
              synonyms_text,
              sources
            )
            WITH term_entities AS (
              SELECT e.entity_id, e.id AS term_id
              FROM {}.entity e
              WHERE e.entity_type = {}
                AND e.id_type = {}
            ),
            annotation_values AS (
              SELECT
                te.entity_id,
                (
                  ARRAY_AGG(a.value ORDER BY a.annotation_id)
                  FILTER (
                    WHERE a.term = {}
                      AND COALESCE(a.value, '') <> ''
                  )
                )[1] AS label,
                (
                  ARRAY_AGG(a.value ORDER BY a.annotation_id)
                  FILTER (
                    WHERE a.term = {}
                      AND COALESCE(a.value, '') <> ''
                  )
                )[1] AS definition,
                (
                  ARRAY_AGG(a.value ORDER BY a.annotation_id)
                  FILTER (
                    WHERE a.term = {}
                      AND COALESCE(a.value, '') <> ''
                  )
                )[1] AS ontology_id,
                ARRAY_AGG(DISTINCT a.value ORDER BY a.value)
                  FILTER (
                    WHERE (
                      a.term = {}
                      OR (a.term = {} AND a.unit = 'alt_id')
                    )
                    AND COALESCE(a.value, '') <> ''
                  ) AS synonyms
              FROM term_entities te
              LEFT JOIN {}.annotation a
                ON a.entity_id = te.entity_id
              GROUP BY te.entity_id
            )
            SELECT
              te.entity_id AS term_entity_id,
              te.term_id,
              CASE
                WHEN te.term_id ~* '^KW-[0-9]+$' THEN 'kw'
                WHEN position(':' in te.term_id) > 0
                  THEN lower(split_part(te.term_id, ':', 1))
                ELSE NULL
              END AS ontology_prefix,
              COALESCE(av.label, te.term_id) AS label,
              av.definition,
              av.ontology_id,
              COALESCE(av.synonyms, '{{}}'::text[]) AS synonyms,
              array_to_string(COALESCE(av.synonyms, '{{}}'::text[]), ' ')
                AS synonyms_text,
              CASE
                WHEN av.ontology_id IS NULL THEN '{{}}'::text[]
                ELSE ARRAY[av.ontology_id]
              END AS sources
            FROM term_entities te
            LEFT JOIN annotation_values av
              ON av.entity_id = te.entity_id
            """
        ).format(
            schema_id,
            schema_id,
            sql.Literal(CV_TERM_ENTITY_TYPE),
            sql.Literal(CV_TERM_ID_TYPE),
            sql.Literal(NAME_TERM),
            sql.Literal(DEFINITION_TERM),
            sql.Literal(ONTOLOGY_ID_TERM),
            sql.Literal(SYNONYM_TERM),
            sql.Literal(CV_TERM_ACCESSION_TERM),
            schema_id,
        )
    )
    return int(cur.rowcount)


def _create_derived_indexes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    statements = [
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_relation_counts_count_idx
            ON {}.entity_relation_counts (relation_count DESC, entity_id ASC)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_term_id_idx
            ON {}.ontology_terms (term_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_ontology_id_idx
            ON {}.ontology_terms (ontology_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_ontology_prefix_idx
            ON {}.ontology_terms (ontology_prefix)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_sources_gin_idx
            ON {}.ontology_terms USING GIN (sources)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_term_id_trgm_idx
            ON {}.ontology_terms USING GIN (term_id gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_label_trgm_idx
            ON {}.ontology_terms USING GIN (label gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_definition_trgm_idx
            ON {}.ontology_terms USING GIN (definition gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_synonyms_text_trgm_idx
            ON {}.ontology_terms USING GIN (synonyms_text gin_trgm_ops)
            """
        ).format(schema_id),
    ]
    for statement in statements:
        cur.execute(statement)
