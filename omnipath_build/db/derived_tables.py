"""Derived query tables built from the canonical graph.

These tables are not primary evidence. They summarize canonical relations and
ontology-term entities into shapes that are cheaper for search, filtering, and
resource summaries. They are rebuilt after selected sources have been ingested
and canonicalized.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg2 import sql
import psycopg2.extensions

from omnipath_build.db.schema import _ensure_ontology_terms_table

@dataclass(frozen=True)
class DerivedTableStats:
    """Summary counts from derived table population."""

    entity_identifier_lookup: int = 0
    entity_relation_counts: int = 0
    ontology_terms: int = 0


def rebuild_derived_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> DerivedTableStats:
    """Create and fully rebuild derived search/count tables."""

    with conn.cursor() as cur:
        _create_derived_tables(cur, schema)
        entity_identifier_lookup = _populate_entity_identifier_lookup(
            cur,
            schema,
        )
        relation_counts = _populate_entity_relation_counts(cur, schema)
        ontology_terms = _count_ontology_terms(cur, schema)
        _create_derived_indexes(cur, schema)
    conn.commit()
    return DerivedTableStats(
        entity_identifier_lookup=entity_identifier_lookup,
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
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'entity_relation_counts'
          AND column_name = 'entity_id'
        """,
        [schema],
    )
    row = cur.fetchone()
    if row is not None and row[0] != 'uuid':
        cur.execute(
            sql.SQL('DROP TABLE {}.entity_relation_counts').format(schema_id)
        )
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = %s
            AND table_name = 'entity_identifier_lookup'
            AND column_name IN ('identifier', 'identifier_type_id')
        )
        """,
        [schema],
    )
    if bool(cur.fetchone()[0]):
        cur.execute(
            sql.SQL('DROP TABLE {}.entity_identifier_lookup').format(
                schema_id
            )
        )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_identifier_lookup (
              entity_id uuid NOT NULL,
              identifier_id uuid NOT NULL,
              PRIMARY KEY (entity_id, identifier_id)
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_relation_counts (
              entity_id uuid PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              relation_count bigint NOT NULL
            )
            """
        ).format(schema_id, schema_id)
    )
    _ensure_ontology_terms_table(cur, schema)


def _populate_entity_identifier_lookup(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.entity_identifier_lookup').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_identifier_lookup (
              entity_id,
              identifier_id
            )
            WITH identifier_rows AS (
              SELECT
                er.entity_id,
                eei.identifier_id
              FROM {}.entity_evidence_resolution er
              JOIN {}.entity_evidence_identifier eei
                ON eei.source_id = er.source_id
               AND eei.entity_evidence_id = er.entity_evidence_id
              JOIN {}.identifier_evidence i
                ON i.identifier_id = eei.identifier_id
              WHERE er.entity_id IS NOT NULL
                AND i.value IS NOT NULL
                AND i.value <> ''
              UNION ALL
              SELECT
                e.entity_id,
                i.identifier_id
              FROM {}.entity e
              JOIN {}.identifier_evidence i
                ON i.identifier_type_id = e.canonical_identifier_type_id
               AND i.value = e.canonical_identifier
              WHERE e.canonical_identifier_type_id IS NOT NULL
                AND e.canonical_identifier IS NOT NULL
                AND e.canonical_identifier <> ''
            )
            SELECT DISTINCT entity_id, identifier_id
            FROM identifier_rows
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
        )
    )
    return int(cur.rowcount)


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


def _count_ontology_terms(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    cur.execute(
        sql.SQL('SELECT COUNT(*) FROM {}.ontology_terms').format(
            sql.Identifier(schema)
        )
    )
    return int(cur.fetchone()[0])


def _create_derived_indexes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    statements = [
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_identifier_lookup_identifier_id_idx
            ON {}.entity_identifier_lookup (identifier_id, entity_id)
            """
        ).format(schema_id),
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
