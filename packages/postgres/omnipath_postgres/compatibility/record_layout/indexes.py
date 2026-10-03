"""Query indexes adapted from the legacy PostgreSQL endpoint/search indexes."""

from __future__ import annotations

from psycopg import sql


_INDEXES = (
    ("entities_key_idx", "entities", "entity_key, resource, version"),
    ("entities_type_taxon_idx", "entities", "entity_type, taxon"),
    ("entities_identifier_lower_idx", "entities", "lower(identifier) text_pattern_ops"),
    ("entities_label_lower_idx", "entities", "lower(label) text_pattern_ops"),
    ("relations_key_idx", "relations", "relation_key, resource, version"),
    ("relations_subject_idx", "relations", "subject_entity_key"),
    ("relations_object_idx", "relations", "object_entity_key"),
    ("relations_predicate_category_idx", "relations", "predicate, category"),
    (
        "identifiers_lookup_idx",
        "identifiers",
        'ns, "left"(lower(id), 256) text_pattern_ops, entity_key',
    ),
    ("evidence_source_dataset_row_idx", "evidence", "source, dataset, row_id"),
    ("annotations_term_owner_idx", "annotations", "term, owner_kind, owner_key"),
)


def create_indexes(conn, schema: str) -> None:
    """Create ordinary B-tree indexes without committing or loading extensions."""
    namespace = sql.Identifier(schema)
    with conn.cursor() as cur:
        for name, table, expressions in _INDEXES:
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.{} ({})").format(
                    sql.Identifier(name), namespace, sql.Identifier(table), sql.SQL(expressions)
                )
            )
