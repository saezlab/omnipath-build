from __future__ import annotations

import logging
import time

import psycopg2.extensions
from psycopg2 import sql

logger = logging.getLogger(__name__)


def create_secondary_indexes(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    statements = [
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_entity_id_idx ON {}.entity_identifier (entity_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_value_hash_idx ON {}.entity_identifier USING HASH (identifier)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_identifier_lower_hash_idx ON {}.entity_identifier USING HASH (LOWER(identifier))').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_key_idx ON {}.entity (entity_key)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_taxonomy_idx ON {}.entity (taxonomy_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_subject_idx ON {}.entity_relation (subject_entity_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_object_idx ON {}.entity_relation (object_entity_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_subject_predicate_idx ON {}.entity_relation (subject_entity_id, predicate)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_category_predicate_idx ON {}.entity_relation (relation_category, predicate)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_subject_category_idx ON {}.entity_relation (subject_entity_id, relation_category)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_object_category_idx ON {}.entity_relation (object_entity_id, relation_category)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_relation_evidence_relation_idx ON {}.entity_relation_evidence (relation_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_evidence_source_idx ON {}.entity_evidence (source)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS relation_annotation_term_scope_term_relation_idx ON {}.relation_annotation_term (scope, term_entity_id, relation_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS relation_annotation_term_relation_idx ON {}.relation_annotation_term (relation_id)').format(sql.Identifier(schema)),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_cv_term_idx
            ON {}.entity (canonical_identifier)
            WHERE entity_type = 'OM:0012:Cv Term'
              AND canonical_identifier_type = 'OM:0204:Cv Term Accession'
            """
        ).format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS resources_build_status_idx ON {}.resources (build_status)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS resources_resource_name_trgm_idx ON {}.resources USING GIN (resource_name gin_trgm_ops)').format(sql.Identifier(schema)),
    ]
    with conn.cursor() as cur:
        for index_number, statement in enumerate(statements, start=1):
            started_at = time.monotonic()
            logger.info(
                '  creating secondary index %s/%s',
                index_number,
                len(statements),
            )
            cur.execute(statement)
            logger.info(
                '  created secondary index %s/%s in %.1fs',
                index_number,
                len(statements),
                time.monotonic() - started_at,
            )
    conn.commit()
