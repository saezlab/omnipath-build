from __future__ import annotations

import logging

import psycopg2.extensions
from psycopg2 import sql

logger = logging.getLogger(__name__)


def create_bitmap_tables(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    """Create bitmap index tables if they do not already exist."""
    with conn.cursor() as cur:
        cur.execute('CREATE EXTENSION IF NOT EXISTS roaringbitmap')

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.annotation_term_entity_bitmap (
                  term_entity_pk bigint PRIMARY KEY,
                  entity_bitmap roaringbitmap NOT NULL,
                  global_count integer NOT NULL
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.annotation_term_relation_bitmap (
                  term_entity_pk bigint PRIMARY KEY,
                  relation_bitmap roaringbitmap NOT NULL,
                  global_count integer NOT NULL
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.facet_entity_bitmap (
                  facet_name text NOT NULL,
                  facet_value text NOT NULL,
                  entity_bitmap roaringbitmap NOT NULL,
                  entity_count integer NOT NULL,
                  PRIMARY KEY (facet_name, facet_value)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.facet_relation_bitmap (
                  facet_name text NOT NULL,
                  facet_value text NOT NULL,
                  facet_category text,
                  relation_bitmap roaringbitmap NOT NULL,
                  relation_count integer NOT NULL,
                  PRIMARY KEY (facet_name, facet_value)
                )
                """
            ).format(sql.Identifier(schema))
        )
    conn.commit()


def populate_bitmap_tables(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    """Populate bitmap index tables from the current database snapshot.

    Reads directly from entity_relation (relation_category = 'annotation')
    instead of an entity_annotation materialized view.
    """
    logger.info('Populating bitmap tables in schema %s', schema)

    with conn.cursor() as cur:
        # 1. annotation_term_entity_bitmap: term -> set of annotated entity PKs
        cur.execute(
            sql.SQL(
                """
                TRUNCATE {}.annotation_term_entity_bitmap
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.annotation_term_entity_bitmap (term_entity_pk, entity_bitmap, global_count)
                SELECT
                  object_entity_pk AS term_entity_pk,
                  rb_build_agg(subject_entity_pk::integer),
                  COUNT(*)::integer
                FROM {}.entity_relation
                WHERE relation_category = 'annotation'
                GROUP BY object_entity_pk
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        logger.info('  annotation_term_entity_bitmap: %s rows', cur.rowcount)

        # 2. annotation_term_relation_bitmap: term -> set of relation PKs
        #    where either endpoint is annotated with that term.
        cur.execute(
            sql.SQL(
                """
                TRUNCATE {}.annotation_term_relation_bitmap
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                WITH relation_terms AS (
                  SELECT
                    ann.object_entity_pk AS term_entity_pk,
                    er.relation_pk
                  FROM {}.entity_relation ann
                  JOIN {}.entity_relation er
                    ON er.subject_entity_pk = ann.subject_entity_pk
                    OR er.object_entity_pk = ann.subject_entity_pk
                  WHERE ann.relation_category = 'annotation'
                  UNION
                  SELECT
                    rat.term_entity_pk,
                    rat.relation_pk
                  FROM {}.relation_annotation_term rat
                )
                INSERT INTO {}.annotation_term_relation_bitmap (term_entity_pk, relation_bitmap, global_count)
                SELECT
                  term_entity_pk,
                  rb_build_agg(DISTINCT relation_pk::integer),
                  COUNT(DISTINCT relation_pk)::integer
                FROM relation_terms
                GROUP BY term_entity_pk
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        logger.info('  annotation_term_relation_bitmap: %s rows', cur.rowcount)

        # 3. facet_entity_bitmap: entity_type, taxonomy_id, source, and ontology_id facets
        cur.execute(
            sql.SQL(
                """
                TRUNCATE {}.facet_entity_bitmap
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                SELECT 'entity_type', entity_type, rb_build_agg(entity_pk::integer), COUNT(*)::integer
                FROM {}.entity
                WHERE entity_type IS NOT NULL
                GROUP BY entity_type
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                SELECT 'taxonomy_id', taxonomy_id, rb_build_agg(entity_pk::integer), COUNT(*)::integer
                FROM {}.entity
                WHERE taxonomy_id IS NOT NULL AND taxonomy_id <> ''
                GROUP BY taxonomy_id
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                SELECT 'source', source.value, rb_build_agg(entity_pk::integer), COUNT(*)::integer
                FROM {}.entity e
                CROSS JOIN LATERAL unnest(e.sources) AS source(value)
                WHERE source.value <> ''
                GROUP BY source.value
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                SELECT
                  'ontology_id',
                  attr.value,
                  rb_build_agg(DISTINCT e.entity_pk::integer),
                  COUNT(DISTINCT e.entity_pk)::integer
                FROM {}.entity e
                CROSS JOIN LATERAL jsonb_to_recordset(COALESCE(e.entity_attributes, '[]'::jsonb))
                  AS attr(term text, value text, unit text)
                WHERE e.entity_type = 'OM:0012:Cv Term'
                  AND attr.term IN ('OM:0803', 'OM:0803:Ontology Id')
                  AND attr.value <> ''
                GROUP BY attr.value
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        logger.info('  facet_entity_bitmap: %s rows', cur.rowcount)

        # 4. facet_relation_bitmap: predicate, participant_type, source facets
        cur.execute(
            sql.SQL(
                """
                TRUNCATE {}.facet_relation_bitmap
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.facet_relation_bitmap (facet_name, facet_value, facet_category, relation_bitmap, relation_count)
                SELECT 'predicate', predicate, relation_category, rb_build_agg(relation_pk::integer), COUNT(*)::integer
                FROM {}.entity_relation
                GROUP BY predicate, relation_category
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.facet_relation_bitmap (facet_name, facet_value, relation_bitmap, relation_count)
                SELECT 'participant_type', pt.value, rb_build_agg(relation_pk::integer), COUNT(*)::integer
                FROM {}.entity_relation r
                CROSS JOIN LATERAL unnest(r.participant_types) AS pt(value)
                WHERE pt.value <> ''
                GROUP BY pt.value
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.facet_relation_bitmap (facet_name, facet_value, relation_bitmap, relation_count)
                SELECT 'source', source.value, rb_build_agg(relation_pk::integer), COUNT(*)::integer
                FROM {}.entity_relation r
                CROSS JOIN LATERAL unnest(r.sources) AS source(value)
                WHERE source.value <> ''
                GROUP BY source.value
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        logger.info('  facet_relation_bitmap: %s rows', cur.rowcount)

    conn.commit()
    logger.info('Bitmap table population complete')
