from __future__ import annotations

import polars as pl


IDENTIFIER_STRUCT = pl.Struct({
    'identifier': pl.String,
    'identifier_type': pl.String,
})

ATTRIBUTE_STRUCT = pl.List(
    pl.Struct({
        'term': pl.String,
        'value': pl.String,
        'unit': pl.String,
    })
)

ENTITY_SCHEMA = {
    'entity_pk': pl.Int64,
    'canonical_identifier': pl.String,
    'canonical_identifier_type': pl.String,
    'identifiers': pl.List(IDENTIFIER_STRUCT),
    'entity_type': pl.String,
    'taxonomy_id': pl.String,
    'entity_attributes': ATTRIBUTE_STRUCT,
    'sources': pl.List(pl.String),
}

INTERACTION_EVIDENCE_SCHEMA = {
    'source': pl.String,
    'interaction_pk': pl.Int64,
    'direction': pl.Int64,
    'sign': pl.Int64,
    'record_attributes': ATTRIBUTE_STRUCT,
    'entity_a_attributes': ATTRIBUTE_STRUCT,
    'entity_b_attributes': ATTRIBUTE_STRUCT,
    'evidence': ATTRIBUTE_STRUCT,
}

INTERACTION_SCHEMA = {
    'interaction_pk': pl.Int64,
    'entity_a_pk': pl.Int64,
    'entity_b_pk': pl.Int64,
    'direction': pl.Int64,
    'sign': pl.Int64,
    'evidence_count': pl.Int64,
    'sources': pl.List(pl.String),
}

ASSOCIATION_EVIDENCE_SCHEMA = {
    'source': pl.String,
    'association_pk': pl.Int64,
    'role_term_id': pl.String,
    'stoichiometry': pl.String,
    'record_attributes': ATTRIBUTE_STRUCT,
    'parent_attributes': ATTRIBUTE_STRUCT,
    'member_attributes': ATTRIBUTE_STRUCT,
    'evidence': ATTRIBUTE_STRUCT,
}

ASSOCIATION_SCHEMA = {
    'association_pk': pl.Int64,
    'parent_entity_pk': pl.Int64,
    'member_entity_pk': pl.Int64,
    'role_term_id': pl.String,
    'stoichiometry': pl.String,
    'sources': pl.List(pl.String),
}

ENTITY_ANNOTATION_SCHEMA = {
    'entity_pk': pl.Int64,
    'cv_term': pl.String,
    'sources': pl.List(pl.String),
}

INTERACTION_ANNOTATION_SCHEMA = {
    'interaction_pk': pl.Int64,
    'cv_term': pl.String,
    'sources': pl.List(pl.String),
}

ENTITY_RELATION_SCHEMA = {
    'relation_pk': pl.Int64,
    'subject_entity_pk': pl.Int64,
    'predicate': pl.String,
    'object_entity_pk': pl.Int64,
    'relation_category': pl.String,
    'evidence_count': pl.Int64,
    'sources': pl.List(pl.String),
}

ENTITY_RELATION_EVIDENCE_SCHEMA = {
    'source': pl.String,
    'relation_evidence_pk': pl.Int64,
    'relation_pk': pl.Int64,
    'record_attributes': ATTRIBUTE_STRUCT,
    'subject_attributes': ATTRIBUTE_STRUCT,
    'object_attributes': ATTRIBUTE_STRUCT,
    'evidence': ATTRIBUTE_STRUCT,
}

ONTOLOGY_TERM_SCHEMA = {
    'term_id': pl.String,
    'ontology_prefix': pl.String,
    'label': pl.String,
    'definition': pl.String,
    'synonyms': pl.List(pl.String),
    'sources': pl.List(pl.String),
}

ARTIFACT_OUTPUTS = {
    'entity.parquet': ENTITY_SCHEMA,
    'interaction_evidence.parquet': INTERACTION_EVIDENCE_SCHEMA,
    'association_evidence.parquet': ASSOCIATION_EVIDENCE_SCHEMA,
    'interaction.parquet': INTERACTION_SCHEMA,
    'association.parquet': ASSOCIATION_SCHEMA,
    'entity_annotation.parquet': ENTITY_ANNOTATION_SCHEMA,
    'interaction_annotation.parquet': INTERACTION_ANNOTATION_SCHEMA,
}


EMPTY_SOURCES = pl.lit([], dtype=pl.List(pl.Utf8))
EMPTY_IDENTIFIERS = pl.lit([], dtype=pl.List(IDENTIFIER_STRUCT))


def empty_frame(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame({
        name: pl.Series([], dtype=dtype)
        for name, dtype in schema.items()
    })


def aggregate_unique_string_lists(column: str = 'sources') -> pl.Expr:
    return pl.col(column).explode().drop_nulls().unique().sort().alias(column)


def aggregate_unique_strings(column: str = 'source', *, alias: str | None = None) -> pl.Expr:
    return pl.col(column).drop_nulls().unique().sort().alias(alias or column)
