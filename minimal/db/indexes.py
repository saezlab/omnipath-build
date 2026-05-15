from __future__ import annotations

from psycopg2 import sql
import psycopg2.extensions

def create_secondary_indexes(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> None:
    """Create query-oriented indexes for the minimal tables."""

    schema_id = sql.Identifier(schema)
    statements = [
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_evidence_source_dataset_row_idx
            ON {}.entity_evidence (source, dataset, row_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_evidence_type_taxonomy_idx
            ON {}.entity_evidence (entity_type, taxonomy_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_type_taxonomy_idx
            ON {}.entity (entity_type, taxonomy_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS canonical_relation_subject_entity_idx
            ON {}.relation (subject_entity_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS canonical_relation_object_entity_idx
            ON {}.relation (object_entity_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS relation_predicate_category_idx
            ON {}.relation (predicate, relation_category)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS relation_category_subject_idx
            ON {}.relation (relation_category, subject_entity_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS relation_category_object_idx
            ON {}.relation (relation_category, object_entity_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS relation_evidence_predicate_category_idx
            ON {}.relation_evidence (predicate, relation_category)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS relation_evidence_source_dataset_row_idx
            ON {}.relation_evidence (source, dataset, row_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS relation_evidence_relation_relation_id_idx
            ON {}.relation_evidence_relation (relation_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS annotation_relation_evidence_term_idx
            ON {}.annotation (relation_evidence_id, term, unit)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS annotation_entity_term_idx
            ON {}.annotation (entity_id, term, unit)
            """
        ).format(schema_id),
    ]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()
