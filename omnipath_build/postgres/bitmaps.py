from __future__ import annotations

import logging
import time

import psycopg2.extensions
from psycopg2 import sql

logger = logging.getLogger(__name__)


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes, rem = divmod(seconds, 60)
    return f'{int(minutes)}m {rem:.0f}s'


def _log_done(started_at: float, message: str, *args: object) -> None:
    logger.info(
        '  ✓ ' + message + ' in %s',
        *args,
        _duration(time.monotonic() - started_at),
    )


def _remove_from_facet_bitmaps(
    conn: psycopg2.extensions.connection,
    schema: str,
    affected_entity_ids: list[int],
    affected_relation_ids: list[int],
) -> None:
    """Remove affected IDs from bitmaps BEFORE base tables are updated.

    This reads the current (old) facet values from the database and removes
    the affected IDs from their corresponding bitmap rows.
    """
    if not affected_entity_ids and not affected_relation_ids:
        return

    logger.info(
        'Removing %s entities and %s relations from bitmaps (pre-update)',
        len(affected_entity_ids),
        len(affected_relation_ids),
    )

    with conn.cursor() as cur:
        if affected_entity_ids:
            # Build bitmap of affected entity IDs once
            cur.execute(
                'SELECT rb_build(%s::integer[])',
                (affected_entity_ids,),
            )
            affected_entity_bitmap = cur.fetchone()[0]

            # Remove affected entities from all entity facet rows that contain them
            # We update ALL rows because we don't know which facets each entity had
            # without querying. But since there are only ~1000 rows, this is fine.
            cur.execute(
                sql.SQL(
                    """
                    UPDATE {}.facet_entity_bitmap
                    SET entity_bitmap = rb_andnot(entity_bitmap, %s::roaringbitmap),
                        entity_count = rb_cardinality(rb_andnot(entity_bitmap, %s::roaringbitmap))
                    """
                ).format(sql.Identifier(schema)),
                (affected_entity_bitmap, affected_entity_bitmap),
            )

            # Remove affected entities from annotation_term_entity_bitmap
            cur.execute(
                sql.SQL(
                    """
                    UPDATE {}.annotation_term_entity_bitmap
                    SET entity_bitmap = rb_andnot(entity_bitmap, %s::roaringbitmap),
                        global_count = rb_cardinality(rb_andnot(entity_bitmap, %s::roaringbitmap))
                    """
                ).format(sql.Identifier(schema)),
                (affected_entity_bitmap, affected_entity_bitmap),
            )

        if affected_relation_ids:
            cur.execute(
                'SELECT rb_build(%s::integer[])',
                (affected_relation_ids,),
            )
            affected_relation_bitmap = cur.fetchone()[0]

            # Remove affected relations from all relation facet rows
            cur.execute(
                sql.SQL(
                    """
                    UPDATE {}.facet_relation_bitmap
                    SET relation_bitmap = rb_andnot(relation_bitmap, %s::roaringbitmap),
                        relation_count = rb_cardinality(rb_andnot(relation_bitmap, %s::roaringbitmap))
                    """
                ).format(sql.Identifier(schema)),
                (affected_relation_bitmap, affected_relation_bitmap),
            )

            # Remove affected relations from annotation_term_relation_bitmap
            cur.execute(
                sql.SQL(
                    """
                    UPDATE {}.annotation_term_relation_bitmap
                    SET relation_bitmap = rb_andnot(relation_bitmap, %s::roaringbitmap),
                        global_count = rb_cardinality(rb_andnot(relation_bitmap, %s::roaringbitmap))
                    """
                ).format(sql.Identifier(schema)),
                (affected_relation_bitmap, affected_relation_bitmap),
            )

    conn.commit()
    logger.info('Pre-update bitmap removal complete')


def _add_to_facet_bitmaps(
    conn: psycopg2.extensions.connection,
    schema: str,
    affected_entity_ids: list[int],
    affected_relation_ids: list[int],
) -> None:
    """Add affected IDs to bitmaps AFTER base tables are updated.

    This reads the new facet values from the updated database and adds
    the affected IDs to their corresponding bitmap rows. Rows that don't
    exist are inserted.
    """
    if not affected_entity_ids and not affected_relation_ids:
        return

    logger.info(
        'Adding %s entities and %s relations to bitmaps (post-update)',
        len(affected_entity_ids),
        len(affected_relation_ids),
    )

    with conn.cursor() as cur:
        if affected_entity_ids:
            # entity_type facet
            step_started_at = time.monotonic()
            logger.info('  adding to entity_type facet')
            cur.execute(
                sql.SQL(
                    """
                    WITH affected AS (
                        SELECT entity_id, entity_type AS fv
                        FROM {}.entity
                        WHERE entity_id = ANY(%s) AND entity_type IS NOT NULL
                    )
                    INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                    SELECT 'entity_type', fv, rb_build_agg(entity_id::integer), COUNT(*)::integer
                    FROM affected
                    GROUP BY fv
                    ON CONFLICT (facet_name, facet_value) DO UPDATE SET
                      entity_bitmap = rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap),
                      entity_count = rb_cardinality(rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap))
                    """
                ).format(sql.Identifier(schema), sql.Identifier(schema)),
                (affected_entity_ids,),
            )
            _log_done(step_started_at, 'entity_type facet: %s rows', cur.rowcount)

            # taxonomy_id facet
            step_started_at = time.monotonic()
            logger.info('  adding to taxonomy_id facet')
            cur.execute(
                sql.SQL(
                    """
                    WITH affected AS (
                        SELECT entity_id, taxonomy_id AS fv
                        FROM {}.entity
                        WHERE entity_id = ANY(%s)
                          AND taxonomy_id IS NOT NULL AND taxonomy_id <> ''
                    )
                    INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                    SELECT 'taxonomy_id', fv, rb_build_agg(entity_id::integer), COUNT(*)::integer
                    FROM affected
                    GROUP BY fv
                    ON CONFLICT (facet_name, facet_value) DO UPDATE SET
                      entity_bitmap = rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap),
                      entity_count = rb_cardinality(rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap))
                    """
                ).format(sql.Identifier(schema), sql.Identifier(schema)),
                (affected_entity_ids,),
            )
            _log_done(step_started_at, 'taxonomy_id facet: %s rows', cur.rowcount)

            # source facet
            step_started_at = time.monotonic()
            logger.info('  adding to source facet')
            cur.execute(
                sql.SQL(
                    """
                    WITH affected AS (
                        SELECT e.entity_id, source.value AS fv
                        FROM {}.entity e
                        CROSS JOIN LATERAL jsonb_array_elements_text(e.sources) AS source(value)
                        WHERE e.entity_id = ANY(%s) AND source.value <> ''
                    )
                    INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                    SELECT 'source', fv, rb_build_agg(entity_id::integer), COUNT(*)::integer
                    FROM affected
                    GROUP BY fv
                    ON CONFLICT (facet_name, facet_value) DO UPDATE SET
                      entity_bitmap = rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap),
                      entity_count = rb_cardinality(rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap))
                    """
                ).format(sql.Identifier(schema), sql.Identifier(schema)),
                (affected_entity_ids,),
            )
            _log_done(step_started_at, 'source facet: %s rows', cur.rowcount)

            # ontology_id facet
            step_started_at = time.monotonic()
            logger.info('  adding to ontology_id facet')
            cur.execute(
                sql.SQL(
                    """
                    WITH affected AS (
                        SELECT e.entity_id, attr.value AS fv
                        FROM {}.entity e
                        CROSS JOIN LATERAL jsonb_to_recordset(COALESCE(e.entity_attributes, '[]'::jsonb))
                          AS attr(term text, value text, unit text)
                        WHERE e.entity_id = ANY(%s)
                          AND e.entity_type = 'OM:0012:Cv Term'
                          AND attr.term IN ('OM:0803', 'OM:0803:Ontology Id')
                          AND attr.value <> ''
                    )
                    INSERT INTO {}.facet_entity_bitmap (facet_name, facet_value, entity_bitmap, entity_count)
                    SELECT 'ontology_id', fv, rb_build_agg(DISTINCT entity_id::integer), COUNT(DISTINCT entity_id)::integer
                    FROM affected
                    GROUP BY fv
                    ON CONFLICT (facet_name, facet_value) DO UPDATE SET
                      entity_bitmap = rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap),
                      entity_count = rb_cardinality(rb_or(facet_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap))
                    """
                ).format(sql.Identifier(schema), sql.Identifier(schema)),
                (affected_entity_ids,),
            )
            _log_done(step_started_at, 'ontology_id facet: %s rows', cur.rowcount)

            # annotation_term_entity_bitmap
            step_started_at = time.monotonic()
            logger.info('  adding to annotation_term_entity_bitmap')
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.annotation_term_entity_bitmap (term_entity_id, entity_bitmap, global_count)
                    SELECT
                      er.object_entity_id AS term_entity_id,
                      rb_build_agg(er.subject_entity_id::integer),
                      COUNT(*)::integer
                    FROM {}.entity_relation er
                    JOIN {}.entity term ON term.entity_id = er.object_entity_id
                    WHERE er.relation_category = 'association'
                      AND term.entity_type = 'OM:0012:Cv Term'
                      AND er.subject_entity_id = ANY(%s)
                    GROUP BY er.object_entity_id
                    ON CONFLICT (term_entity_id) DO UPDATE SET
                      entity_bitmap = rb_or(annotation_term_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap),
                      global_count = rb_cardinality(rb_or(annotation_term_entity_bitmap.entity_bitmap, EXCLUDED.entity_bitmap))
                    """
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                ),
                (affected_entity_ids,),
            )
            _log_done(
                step_started_at,
                'annotation_term_entity_bitmap: %s rows',
                cur.rowcount,
            )

        if affected_relation_ids:
            # predicate facet
            step_started_at = time.monotonic()
            logger.info('  adding to predicate facet')
            cur.execute(
                sql.SQL(
                    """
                    WITH affected AS (
                        SELECT relation_id, predicate AS fv, relation_category AS cat
                        FROM {}.entity_relation
                        WHERE relation_id = ANY(%s)
                    )
                    INSERT INTO {}.facet_relation_bitmap (facet_name, facet_value, facet_category, relation_bitmap, relation_count)
                    SELECT 'predicate', fv, cat, rb_build_agg(relation_id::integer), COUNT(*)::integer
                    FROM affected
                    GROUP BY fv, cat
                    ON CONFLICT (facet_name, facet_value) DO UPDATE SET
                      facet_category = EXCLUDED.facet_category,
                      relation_bitmap = rb_or(facet_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap),
                      relation_count = rb_cardinality(rb_or(facet_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap))
                    """
                ).format(sql.Identifier(schema), sql.Identifier(schema)),
                (affected_relation_ids,),
            )
            _log_done(step_started_at, 'predicate facet: %s rows', cur.rowcount)

            # participant_type facet
            step_started_at = time.monotonic()
            logger.info('  adding to participant_type facet')
            cur.execute(
                sql.SQL(
                    """
                    WITH affected AS (
                        SELECT r.relation_id, pt.value AS fv
                        FROM {}.entity_relation r
                        CROSS JOIN LATERAL jsonb_array_elements_text(r.participant_types) AS pt(value)
                        WHERE r.relation_id = ANY(%s) AND pt.value <> ''
                    )
                    INSERT INTO {}.facet_relation_bitmap (facet_name, facet_value, relation_bitmap, relation_count)
                    SELECT 'participant_type', fv, rb_build_agg(relation_id::integer), COUNT(*)::integer
                    FROM affected
                    GROUP BY fv
                    ON CONFLICT (facet_name, facet_value) DO UPDATE SET
                      relation_bitmap = rb_or(facet_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap),
                      relation_count = rb_cardinality(rb_or(facet_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap))
                    """
                ).format(sql.Identifier(schema), sql.Identifier(schema)),
                (affected_relation_ids,),
            )
            _log_done(step_started_at, 'participant_type facet: %s rows', cur.rowcount)

            # source facet (relations)
            step_started_at = time.monotonic()
            logger.info('  adding to relation source facet')
            cur.execute(
                sql.SQL(
                    """
                    WITH affected AS (
                        SELECT r.relation_id, source.value AS fv
                        FROM {}.entity_relation r
                        CROSS JOIN LATERAL jsonb_array_elements_text(r.sources) AS source(value)
                        WHERE r.relation_id = ANY(%s) AND source.value <> ''
                    )
                    INSERT INTO {}.facet_relation_bitmap (facet_name, facet_value, relation_bitmap, relation_count)
                    SELECT 'source', fv, rb_build_agg(relation_id::integer), COUNT(*)::integer
                    FROM affected
                    GROUP BY fv
                    ON CONFLICT (facet_name, facet_value) DO UPDATE SET
                      relation_bitmap = rb_or(facet_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap),
                      relation_count = rb_cardinality(rb_or(facet_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap))
                    """
                ).format(sql.Identifier(schema), sql.Identifier(schema)),
                (affected_relation_ids,),
            )
            _log_done(step_started_at, 'relation source facet: %s rows', cur.rowcount)

            # annotation_term_relation_bitmap
            step_started_at = time.monotonic()
            logger.info('  adding to annotation_term_relation_bitmap')
            cur.execute(
                sql.SQL(
                    """
                    WITH ontology_associations AS (
                      SELECT ann.subject_entity_id, ann.object_entity_id
                      FROM {}.entity_relation ann
                      JOIN {}.entity term ON term.entity_id = ann.object_entity_id
                      WHERE ann.relation_category = 'association'
                        AND term.entity_type = 'OM:0012:Cv Term'
                        AND ann.subject_entity_id = ANY(%s)
                    ),
                    relation_terms AS (
                      SELECT ann.object_entity_id AS term_entity_id, er.relation_id
                      FROM ontology_associations ann
                      JOIN {}.entity_relation er ON er.subject_entity_id = ann.subject_entity_id
                      UNION ALL
                      SELECT ann.object_entity_id AS term_entity_id, er.relation_id
                      FROM ontology_associations ann
                      JOIN {}.entity_relation er ON er.object_entity_id = ann.subject_entity_id
                      UNION ALL
                      SELECT rat.term_entity_id, rat.relation_id
                      FROM {}.relation_annotation_term rat
                      WHERE rat.relation_id = ANY(%s)
                    )
                    INSERT INTO {}.annotation_term_relation_bitmap (term_entity_id, relation_bitmap, global_count)
                    SELECT
                      term_entity_id,
                      rb_build_agg(DISTINCT relation_id::integer),
                      COUNT(DISTINCT relation_id)::integer
                    FROM relation_terms
                    GROUP BY term_entity_id
                    ON CONFLICT (term_entity_id) DO UPDATE SET
                      relation_bitmap = rb_or(annotation_term_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap),
                      global_count = rb_cardinality(rb_or(annotation_term_relation_bitmap.relation_bitmap, EXCLUDED.relation_bitmap))
                    """
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                ),
                (affected_entity_ids or [], affected_relation_ids or []),
            )
            _log_done(
                step_started_at,
                'annotation_term_relation_bitmap: %s rows',
                cur.rowcount,
            )

    conn.commit()
    logger.info('Post-update bitmap addition complete')


def refresh_bitmap_tables_incremental(
    conn: psycopg2.extensions.connection,
    schema: str,
    affected_entity_ids: list[int],
    affected_relation_ids: list[int],
) -> None:
    """Refresh bitmap tables incrementally for affected entity/relation IDs.

    This is a two-phase operation:
    1. Remove affected IDs from current bitmaps (must be called BEFORE base tables update)
    2. Add affected IDs to new bitmaps (must be called AFTER base tables update)

    The caller is responsible for calling _remove_from_facet_bitmaps before
    updating base tables, and _add_to_facet_bitmaps after.
    """
    logger.info(
        'Bitmap incremental refresh prepared for %s entities and %s relations',
        len(affected_entity_ids),
        len(affected_relation_ids),
    )


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
                  term_entity_id bigint PRIMARY KEY,
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
                  term_entity_id bigint PRIMARY KEY,
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

    Reads ontology-object association relations directly from entity_relation
    instead of an entity_annotation materialized view.
    """
    logger.info('Populating bitmap tables in schema %s', schema)

    with conn.cursor() as cur:
        index_started_at = time.monotonic()
        logger.info('  ensuring bitmap helper indexes')
        cur.execute(
            sql.SQL(
                """
                CREATE INDEX IF NOT EXISTS entity_relation_annotation_subject_idx
                ON {}.entity_relation (subject_entity_id)
                INCLUDE (object_entity_id)
                WHERE relation_category = 'association'
                """
            ).format(sql.Identifier(schema))
        )
        _log_done(index_started_at, 'bitmap helper indexes ready')

        # 1. annotation_term_entity_bitmap: term -> set of annotated entity IDs
        step_started_at = time.monotonic()
        logger.info('  building annotation_term_entity_bitmap')
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
                INSERT INTO {}.annotation_term_entity_bitmap (term_entity_id, entity_bitmap, global_count)
                SELECT
                  er.object_entity_id AS term_entity_id,
                  rb_build_agg(er.subject_entity_id::integer),
                  COUNT(*)::integer
                FROM {}.entity_relation er
                JOIN {}.entity term
                  ON term.entity_id = er.object_entity_id
                WHERE er.relation_category = 'association'
                  AND term.entity_type = 'OM:0012:Cv Term'
                GROUP BY er.object_entity_id
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        _log_done(
            step_started_at,
            'annotation_term_entity_bitmap: %s rows',
            cur.rowcount,
        )

        # 2. annotation_term_relation_bitmap: term -> set of relation IDs
        #    where either endpoint is annotated with that term.
        step_started_at = time.monotonic()
        logger.info('  building annotation_term_relation_bitmap')
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
                WITH ontology_associations AS (
                  SELECT ann.subject_entity_id, ann.object_entity_id
                  FROM {}.entity_relation ann
                  JOIN {}.entity term
                    ON term.entity_id = ann.object_entity_id
                  WHERE ann.relation_category = 'association'
                    AND term.entity_type = 'OM:0012:Cv Term'
                ),
                relation_terms AS (
                  SELECT
                    ann.object_entity_id AS term_entity_id,
                    er.relation_id
                  FROM ontology_associations ann
                  JOIN {}.entity_relation er
                    ON er.subject_entity_id = ann.subject_entity_id
                  UNION ALL
                  SELECT
                    ann.object_entity_id AS term_entity_id,
                    er.relation_id
                  FROM ontology_associations ann
                  JOIN {}.entity_relation er
                    ON er.object_entity_id = ann.subject_entity_id
                  UNION ALL
                  SELECT
                    rat.term_entity_id,
                    rat.relation_id
                  FROM {}.relation_annotation_term rat
                )
                INSERT INTO {}.annotation_term_relation_bitmap (term_entity_id, relation_bitmap, global_count)
                SELECT
                  term_entity_id,
                  rb_build_agg(DISTINCT relation_id::integer),
                  COUNT(DISTINCT relation_id)::integer
                FROM relation_terms
                GROUP BY term_entity_id
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
        _log_done(
            step_started_at,
            'annotation_term_relation_bitmap: %s rows',
            cur.rowcount,
        )

        # 3. facet_entity_bitmap: entity_type, taxonomy_id, source, and ontology_id facets
        step_started_at = time.monotonic()
        logger.info('  building facet_entity_bitmap')
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
                SELECT 'entity_type', entity_type, rb_build_agg(entity_id::integer), COUNT(*)::integer
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
                SELECT 'taxonomy_id', taxonomy_id, rb_build_agg(entity_id::integer), COUNT(*)::integer
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
                SELECT 'source', source.value, rb_build_agg(entity_id::integer), COUNT(*)::integer
                FROM {}.entity e
                CROSS JOIN LATERAL jsonb_array_elements_text(e.sources) AS source(value)
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
                  rb_build_agg(DISTINCT e.entity_id::integer),
                  COUNT(DISTINCT e.entity_id)::integer
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
        _log_done(step_started_at, 'facet_entity_bitmap: %s rows', cur.rowcount)

        # 4. facet_relation_bitmap: predicate, participant_type, source facets
        step_started_at = time.monotonic()
        logger.info('  building facet_relation_bitmap')
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
                SELECT 'predicate', predicate, relation_category, rb_build_agg(relation_id::integer), COUNT(*)::integer
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
                SELECT 'participant_type', pt.value, rb_build_agg(relation_id::integer), COUNT(*)::integer
                FROM {}.entity_relation r
                CROSS JOIN LATERAL jsonb_array_elements_text(r.participant_types) AS pt(value)
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
                SELECT 'source', source.value, rb_build_agg(relation_id::integer), COUNT(*)::integer
                FROM {}.entity_relation r
                CROSS JOIN LATERAL jsonb_array_elements_text(r.sources) AS source(value)
                WHERE source.value <> ''
                GROUP BY source.value
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        _log_done(step_started_at, 'facet_relation_bitmap: %s rows', cur.rowcount)

    conn.commit()
    logger.info('Bitmap table population complete')
