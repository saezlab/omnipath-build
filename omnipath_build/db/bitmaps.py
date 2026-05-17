from __future__ import annotations

from dataclasses import dataclass

from psycopg2 import sql
import psycopg2.extensions
from pypath.internals.cv_terms import (
    OntologyAnnotationCv,
    cv_term_label_accession,
)

from omnipath_build.cv_terms import CV_TERM_ENTITY_TYPE

ONTOLOGY_ID_TERM = cv_term_label_accession(OntologyAnnotationCv.ONTOLOGY_ID)


@dataclass(frozen=True)
class BitmapStats:
    """Summary counts from bitmap table population."""

    annotation_term_entities: int = 0
    annotation_term_relations: int = 0
    entity_facets: int = 0
    relation_facets: int = 0


def rebuild_bitmap_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> BitmapStats:
    """Create and fully rebuild bitmap index tables."""

    with conn.cursor() as cur:
        _create_bitmap_tables(cur, schema)
        annotation_term_entities = _populate_annotation_term_entity_bitmap(
            cur, schema
        )
        annotation_term_relations = _populate_annotation_term_relation_bitmap(
            cur, schema
        )
        entity_facets = _populate_facet_entity_bitmap(cur, schema)
        relation_facets = _populate_facet_relation_bitmap(cur, schema)
        _create_bitmap_indexes(cur, schema)
    conn.commit()
    return BitmapStats(
        annotation_term_entities=annotation_term_entities,
        annotation_term_relations=annotation_term_relations,
        entity_facets=entity_facets,
        relation_facets=relation_facets,
    )


def _create_bitmap_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute('CREATE EXTENSION IF NOT EXISTS roaringbitmap')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.annotation_term_entity_bitmap (
              term_entity_id bigint PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              entity_bitmap roaringbitmap NOT NULL,
              global_count integer NOT NULL
            )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.annotation_term_relation_bitmap (
              term_entity_id bigint PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              relation_bitmap roaringbitmap NOT NULL,
              global_count integer NOT NULL
            )
            """
        ).format(schema_id, schema_id)
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
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.facet_relation_bitmap (
              facet_name text NOT NULL,
              facet_value text NOT NULL,
              facet_category text NOT NULL DEFAULT '',
              relation_bitmap roaringbitmap NOT NULL,
              relation_count integer NOT NULL,
              PRIMARY KEY (facet_name, facet_value, facet_category)
            )
            """
        ).format(schema_id)
    )


def _populate_annotation_term_entity_bitmap(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.annotation_term_entity_bitmap').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.annotation_term_entity_bitmap (
              term_entity_id,
              entity_bitmap,
              global_count
            )
            SELECT
              r.object_entity_id AS term_entity_id,
              rb_build_agg(r.subject_entity_id::integer),
              COUNT(DISTINCT r.subject_entity_id)::integer
            FROM {}.relation r
            JOIN {}.entity term
              ON term.entity_id = r.object_entity_id
            JOIN {}.vocab_entity_type term_type
              ON term_type.entity_type_id = term.entity_type_id
            JOIN {}.vocab_relation_category rc
              ON rc.relation_category_id = r.relation_category_id
            WHERE rc.name = 'association'
              AND term_type.name = {}
            GROUP BY r.object_entity_id
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            sql.Literal(CV_TERM_ENTITY_TYPE),
        )
    )
    return int(cur.rowcount)


def _populate_annotation_term_relation_bitmap(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.annotation_term_relation_bitmap').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL(
            """
            WITH ontology_associations AS (
              SELECT r.subject_entity_id, r.object_entity_id
              FROM {}.relation r
              JOIN {}.entity term
                ON term.entity_id = r.object_entity_id
              JOIN {}.vocab_entity_type term_type
                ON term_type.entity_type_id = term.entity_type_id
              JOIN {}.vocab_relation_category rc
                ON rc.relation_category_id = r.relation_category_id
              WHERE rc.name = 'association'
                AND term_type.name = {}
            ),
            relation_terms AS (
              SELECT
                ann.object_entity_id AS term_entity_id,
                r.relation_id
              FROM ontology_associations ann
              JOIN {}.relation r
                ON r.subject_entity_id = ann.subject_entity_id
              UNION
              SELECT
                ann.object_entity_id AS term_entity_id,
                r.relation_id
              FROM ontology_associations ann
              JOIN {}.relation r
                ON r.object_entity_id = ann.subject_entity_id
            )
            INSERT INTO {}.annotation_term_relation_bitmap (
              term_entity_id,
              relation_bitmap,
              global_count
            )
            SELECT
              term_entity_id,
              rb_build_agg(DISTINCT relation_id::integer),
              COUNT(DISTINCT relation_id)::integer
            FROM relation_terms
            GROUP BY term_entity_id
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            sql.Literal(CV_TERM_ENTITY_TYPE),
            schema_id,
            schema_id,
            schema_id,
        )
    )
    return int(cur.rowcount)


def _populate_facet_entity_bitmap(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(sql.SQL('TRUNCATE {}.facet_entity_bitmap').format(schema_id))
    total = 0
    statements = [
        sql.SQL(
            """
            INSERT INTO {}.facet_entity_bitmap (
              facet_name,
              facet_value,
              entity_bitmap,
              entity_count
            )
            SELECT
              'entity_type',
              et.name,
              rb_build_agg(e.entity_id::integer),
              COUNT(*)::integer
            FROM {}.entity e
            JOIN {}.vocab_entity_type et
              ON et.entity_type_id = e.entity_type_id
            GROUP BY et.name
            """
        ).format(schema_id, schema_id, schema_id),
        sql.SQL(
            """
            INSERT INTO {}.facet_entity_bitmap (
              facet_name,
              facet_value,
              entity_bitmap,
              entity_count
            )
            SELECT
              'taxonomy_id',
              taxonomy_id,
              rb_build_agg(entity_id::integer),
              COUNT(*)::integer
            FROM {}.entity
            WHERE taxonomy_id IS NOT NULL
            GROUP BY taxonomy_id
            """
        ).format(schema_id, schema_id),
        sql.SQL(
            """
            INSERT INTO {}.facet_entity_bitmap (
              facet_name,
              facet_value,
              entity_bitmap,
              entity_count
            )
            WITH entity_sources AS (
              SELECT DISTINCT r.entity_id, ds.name AS source
              FROM {}.entity_evidence_resolution r
              JOIN {}.entity_evidence ee
                ON ee.source_id = r.source_id
               AND ee.entity_evidence_id = r.entity_evidence_id
              JOIN {}.data_source ds
                ON ds.source_id = ee.source_id
              WHERE r.entity_id IS NOT NULL
              UNION
              SELECT DISTINCT re.subject_entity_id AS entity_id, ds.name AS source
              FROM {}.relation_evidence re
              JOIN {}.data_source ds
                ON ds.source_id = re.source_id
              WHERE re.subject_entity_id IS NOT NULL
              UNION
              SELECT DISTINCT re.object_entity_id AS entity_id, ds.name AS source
              FROM {}.relation_evidence re
              JOIN {}.data_source ds
                ON ds.source_id = re.source_id
              WHERE re.object_entity_id IS NOT NULL
            )
            SELECT
              'source',
              source,
              rb_build_agg(entity_id::integer),
              COUNT(*)::integer
            FROM entity_sources
            WHERE source IS NOT NULL
              AND source <> ''
            GROUP BY source
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
        ),
        sql.SQL(
            """
            INSERT INTO {}.facet_entity_bitmap (
              facet_name,
              facet_value,
              entity_bitmap,
              entity_count
            )
            SELECT
              'ontology_id',
              a.value,
              rb_build_agg(DISTINCT e.entity_id::integer),
              COUNT(DISTINCT e.entity_id)::integer
            FROM {}.entity e
            JOIN {}.vocab_entity_type et
              ON et.entity_type_id = e.entity_type_id
            JOIN {}.entity_evidence_resolution er
              ON er.entity_id = e.entity_id
            JOIN {}.entity_evidence_annotation ea
              ON ea.source_id = er.source_id
             AND ea.entity_evidence_id = er.entity_evidence_id
            JOIN {}.annotation a
              ON a.annotation_key = ea.annotation_key
            WHERE et.name = {}
              AND a.term = {}
              AND COALESCE(a.value, '') <> ''
            GROUP BY a.value
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            sql.Literal(CV_TERM_ENTITY_TYPE),
            sql.Literal(ONTOLOGY_ID_TERM),
        ),
    ]
    for statement in statements:
        cur.execute(statement)
        total += int(cur.rowcount)
    return total


def _populate_facet_relation_bitmap(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(sql.SQL('TRUNCATE {}.facet_relation_bitmap').format(schema_id))
    total = 0
    statements = [
        sql.SQL(
            """
            INSERT INTO {}.facet_relation_bitmap (
              facet_name,
              facet_value,
              facet_category,
              relation_bitmap,
              relation_count
            )
            SELECT
              'predicate',
              rp.name,
              COALESCE(rc.name, ''),
              rb_build_agg(relation_id::integer),
              COUNT(*)::integer
            FROM {}.relation r
            JOIN {}.vocab_relation_predicate rp
              ON rp.relation_predicate_id = r.predicate_id
            LEFT JOIN {}.vocab_relation_category rc
              ON rc.relation_category_id = r.relation_category_id
            GROUP BY rp.name, rc.name
            """
        ).format(schema_id, schema_id, schema_id, schema_id),
        sql.SQL(
            """
            INSERT INTO {}.facet_relation_bitmap (
              facet_name,
              facet_value,
              relation_bitmap,
              relation_count
            )
            WITH relation_sources AS (
              SELECT DISTINCT rer.relation_id, ds.name AS source
              FROM {}.relation_evidence_relation rer
              JOIN {}.relation_evidence re
                ON re.source_id = rer.source_id
               AND re.relation_evidence_id = rer.relation_evidence_id
              JOIN {}.data_source ds
                ON ds.source_id = re.source_id
            )
            SELECT
              'source',
              source,
              rb_build_agg(relation_id::integer),
              COUNT(*)::integer
            FROM relation_sources
            WHERE source IS NOT NULL
              AND source <> ''
            GROUP BY source
            """
        ).format(schema_id, schema_id, schema_id, schema_id),
        sql.SQL(
            """
            INSERT INTO {}.facet_relation_bitmap (
              facet_name,
              facet_value,
              relation_bitmap,
              relation_count
            )
            WITH participant_types AS (
              SELECT r.relation_id, subject_type.name AS vocab_entity_type
              FROM {}.relation r
              JOIN {}.entity subject
                ON subject.entity_id = r.subject_entity_id
              JOIN {}.vocab_entity_type subject_type
                ON subject_type.entity_type_id = subject.entity_type_id
              UNION
              SELECT r.relation_id, object_type.name AS vocab_entity_type
              FROM {}.relation r
              JOIN {}.entity object
                ON object.entity_id = r.object_entity_id
              JOIN {}.vocab_entity_type object_type
                ON object_type.entity_type_id = object.entity_type_id
            )
            SELECT
              'participant_type',
              vocab_entity_type,
              rb_build_agg(relation_id::integer),
              COUNT(DISTINCT relation_id)::integer
            FROM participant_types
            WHERE vocab_entity_type IS NOT NULL
            GROUP BY vocab_entity_type
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
        ),
        sql.SQL(
            """
            INSERT INTO {}.facet_relation_bitmap (
              facet_name,
              facet_value,
              relation_bitmap,
              relation_count
            )
            WITH relation_taxonomy AS (
              SELECT r.relation_id, subject.taxonomy_id
              FROM {}.relation r
              JOIN {}.entity subject
                ON subject.entity_id = r.subject_entity_id
              WHERE subject.taxonomy_id IS NOT NULL
              UNION
              SELECT r.relation_id, object.taxonomy_id
              FROM {}.relation r
              JOIN {}.entity object
                ON object.entity_id = r.object_entity_id
              WHERE object.taxonomy_id IS NOT NULL
            )
            SELECT
              'taxonomy_id',
              taxonomy_id,
              rb_build_agg(relation_id::integer),
              COUNT(*)::integer
            FROM relation_taxonomy
            GROUP BY taxonomy_id
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
        ),
    ]
    for statement in statements:
        cur.execute(statement)
        total += int(cur.rowcount)
    return total


def _create_bitmap_indexes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    statements = [
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS annotation_term_entity_count_idx
            ON {}.annotation_term_entity_bitmap (global_count DESC)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS annotation_term_relation_count_idx
            ON {}.annotation_term_relation_bitmap (global_count DESC)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS facet_entity_count_idx
            ON {}.facet_entity_bitmap (
              facet_name,
              entity_count DESC,
              facet_value
            )
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS facet_relation_count_idx
            ON {}.facet_relation_bitmap (
              facet_name,
              relation_count DESC,
              facet_value
            )
            """
        ).format(schema_id),
    ]
    for statement in statements:
        cur.execute(statement)
