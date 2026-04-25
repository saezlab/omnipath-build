from __future__ import annotations

import psycopg2.extensions
from psycopg2 import sql


def create_secondary_indexes(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    statements = [
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_entity_pk_idx ON {}.entity_identifier (entity_pk)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_value_hash_idx ON {}.entity_identifier USING HASH (identifier)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_identifier_lower_hash_idx ON {}.entity_identifier USING HASH (LOWER(identifier))').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_taxonomy_idx ON {}.entity (taxonomy_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_subject_idx ON {}.entity_relation (subject_entity_pk)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_object_idx ON {}.entity_relation (object_entity_pk)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_subject_predicate_idx ON {}.entity_relation (subject_entity_pk, predicate)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_category_predicate_idx ON {}.entity_relation (relation_category, predicate)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_subject_category_idx ON {}.entity_relation (subject_entity_pk, relation_category)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_object_category_idx ON {}.entity_relation (object_entity_pk, relation_category)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_evidence_relation_idx ON {}.entity_relation_evidence (relation_pk)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS relation_annotation_term_scope_term_relation_idx ON {}.relation_annotation_term (scope, term_id, relation_pk)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS relation_annotation_term_relation_idx ON {}.relation_annotation_term (relation_pk)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS ontology_term_label_trgm_idx ON {}.ontology_term USING GIN (label gin_trgm_ops)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS ontology_term_definition_trgm_idx ON {}.ontology_term USING GIN (definition gin_trgm_ops)').format(sql.Identifier(schema)),
        sql.SQL('CREATE UNIQUE INDEX IF NOT EXISTS ontology_term_annotation_counts_term_id_idx ON {}.ontology_term_annotation_counts (term_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS ontology_term_annotation_counts_count_idx ON {}.ontology_term_annotation_counts (annotated_item_count DESC, term_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS resources_build_status_idx ON {}.resources (build_status)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS resources_resource_name_trgm_idx ON {}.resources USING GIN (resource_name gin_trgm_ops)').format(sql.Identifier(schema)),
    ]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()
