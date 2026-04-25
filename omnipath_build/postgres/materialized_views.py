from __future__ import annotations

import logging

import psycopg2.extensions
from psycopg2 import sql

logger = logging.getLogger(__name__)


def refresh_materialized_views(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    logger.info('Refreshing materialized views in schema %s', schema)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.ontology_term_annotation_counts').format(
                sql.Identifier(schema)
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE MATERIALIZED VIEW {}.ontology_term_annotation_counts AS
                WITH entity_counts AS (
                  SELECT
                    term_entity.canonical_identifier AS term_id,
                    COUNT(DISTINCT er.subject_entity_pk)::bigint AS annotated_entity_count
                  FROM {}.entity_relation er
                  JOIN {}.entity term_entity
                    ON term_entity.entity_pk = er.object_entity_pk
                  WHERE er.relation_category = 'annotation'
                  GROUP BY term_entity.canonical_identifier
                ),
                relation_counts AS (
                  SELECT
                    term_id,
                    COUNT(DISTINCT relation_pk)::bigint AS annotated_relation_count
                  FROM {}.relation_annotation_term
                  GROUP BY term_id
                )
                SELECT
                  ot.term_id,
                  COALESCE(ec.annotated_entity_count, 0)::bigint AS annotated_entity_count,
                  COALESCE(rc.annotated_relation_count, 0)::bigint AS annotated_relation_count,
                  (
                    COALESCE(ec.annotated_entity_count, 0)
                    + COALESCE(rc.annotated_relation_count, 0)
                  )::bigint AS annotated_item_count
                FROM {}.ontology_term ot
                LEFT JOIN entity_counts ec ON ec.term_id = ot.term_id
                LEFT JOIN relation_counts rc ON rc.term_id = ot.term_id
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
    conn.commit()
