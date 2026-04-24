from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl


_EMPTY_SOURCES = pl.lit([], dtype=pl.List(pl.Utf8))
_IDENTIFIER_STRUCT = pl.Struct({
    'identifier': pl.Utf8,
    'identifier_type': pl.Utf8,
})
_EMPTY_IDENTIFIERS = pl.lit([], dtype=pl.List(_IDENTIFIER_STRUCT))


INTERACTION_KEY_COLUMNS = [
    'entity_a_id',
    'entity_a_id_type',
    'entity_b_id',
    'entity_b_id_type',
    'direction',
    'sign',
]

ASSOCIATION_KEY_COLUMNS = [
    'parent_entity_id',
    'parent_entity_id_type',
    'member_entity_id',
    'member_entity_id_type',
    'role_term_id',
    'stoichiometry',
]


def _empty_identifiers() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_id': pl.Series([], dtype=pl.Utf8),
        'entity_id_type': pl.Series([], dtype=pl.Utf8),
        'identifier': pl.Series([], dtype=pl.Utf8),
        'identifier_type': pl.Series([], dtype=pl.Utf8),
        'is_canonical': pl.Series([], dtype=pl.Boolean),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _empty_interaction() -> pl.DataFrame:
    return pl.DataFrame({
        'interaction_id': pl.Series([], dtype=pl.Utf8),
        'entity_a_id': pl.Series([], dtype=pl.Utf8),
        'entity_a_id_type': pl.Series([], dtype=pl.Utf8),
        'entity_b_id': pl.Series([], dtype=pl.Utf8),
        'entity_b_id_type': pl.Series([], dtype=pl.Utf8),
        'direction': pl.Series([], dtype=pl.Int64),
        'sign': pl.Series([], dtype=pl.Int64),
        'evidence_count': pl.Series([], dtype=pl.Int64),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _empty_association() -> pl.DataFrame:
    return pl.DataFrame({
        'association_id': pl.Series([], dtype=pl.Utf8),
        'parent_entity_id': pl.Series([], dtype=pl.Utf8),
        'parent_entity_id_type': pl.Series([], dtype=pl.Utf8),
        'member_entity_id': pl.Series([], dtype=pl.Utf8),
        'member_entity_id_type': pl.Series([], dtype=pl.Utf8),
        'role_term_id': pl.Series([], dtype=pl.Utf8),
        'stoichiometry': pl.Series([], dtype=pl.Utf8),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _empty_entity_annotation() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_id': pl.Series([], dtype=pl.Utf8),
        'entity_id_type': pl.Series([], dtype=pl.Utf8),
        'cv_term': pl.Series([], dtype=pl.Utf8),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _empty_interaction_annotation() -> pl.DataFrame:
    return pl.DataFrame({
        'interaction_id': pl.Series([], dtype=pl.Utf8),
        'cv_term': pl.Series([], dtype=pl.Utf8),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _dedup_entities(entities: pl.DataFrame) -> pl.DataFrame:
    if entities.is_empty():
        return entities
    return (
        entities
        .with_columns([
            pl.col('entity_id').cast(pl.Utf8),
            pl.col('entity_id_type').cast(pl.Utf8),
            pl.when(pl.col('sources').is_null()).then(_EMPTY_SOURCES).otherwise(pl.col('sources')).alias('sources'),
        ])
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.col('entity_type').drop_nulls().first().alias('entity_type'),
            pl.col('entity_attributes').drop_nulls().first().alias('entity_attributes'),
            pl.col('taxonomy_id').drop_nulls().first().alias('taxonomy_id'),
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .sort(['entity_id_type', 'entity_id'])
    )


def _dedup_identifiers(identifier_rows: pl.DataFrame) -> pl.DataFrame:
    if identifier_rows.is_empty():
        return _empty_identifiers()
    return (
        identifier_rows
        .group_by(['entity_id', 'entity_id_type', 'identifier', 'identifier_type'])
        .agg([
            pl.col('is_canonical').any().alias('is_canonical'),
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .sort(['entity_id_type', 'entity_id', 'identifier_type', 'identifier'])
    )


def _normalize_interaction_evidence(interactions: pl.DataFrame) -> pl.DataFrame:
    if interactions.is_empty():
        return interactions

    left_key = pl.concat_str([
        pl.col('entity_a_id_type').fill_null(''),
        pl.col('entity_a_id').fill_null(''),
    ], separator='\x1f')
    right_key = pl.concat_str([
        pl.col('entity_b_id_type').fill_null(''),
        pl.col('entity_b_id').fill_null(''),
    ], separator='\x1f')
    should_swap = pl.col('direction').is_null() & (left_key > right_key)

    return interactions.with_columns([
        pl.col('source').cast(pl.Utf8),
        pl.col('interaction_id').cast(pl.Int64),
        pl.when(should_swap).then(pl.col('entity_b_id')).otherwise(pl.col('entity_a_id')).cast(pl.Utf8).alias('entity_a_id'),
        pl.when(should_swap).then(pl.col('entity_b_id_type')).otherwise(pl.col('entity_a_id_type')).cast(pl.Utf8).alias('entity_a_id_type'),
        pl.when(should_swap).then(pl.col('entity_a_id')).otherwise(pl.col('entity_b_id')).cast(pl.Utf8).alias('entity_b_id'),
        pl.when(should_swap).then(pl.col('entity_a_id_type')).otherwise(pl.col('entity_b_id_type')).cast(pl.Utf8).alias('entity_b_id_type'),
        pl.when(pl.col('direction').is_null() & pl.col('sign').is_not_null())
        .then(pl.lit(1))
        .otherwise(pl.col('direction'))
        .cast(pl.Int64, strict=False)
        .alias('direction'),
        pl.col('sign').cast(pl.Int64, strict=False),
    ])


def _interaction_key_expr(alias: str = 'interaction_id') -> pl.Expr:
    return pl.concat_str([
        pl.col('entity_a_id_type').fill_null(''),
        pl.col('entity_a_id').fill_null(''),
        pl.col('entity_b_id_type').fill_null(''),
        pl.col('entity_b_id').fill_null(''),
        pl.col('direction').cast(pl.Utf8).fill_null(''),
        pl.col('sign').cast(pl.Utf8).fill_null(''),
    ], separator='\x1f').alias(alias)


def _association_key_expr(alias: str = 'association_id') -> pl.Expr:
    return pl.concat_str([
        pl.col('parent_entity_id_type').fill_null(''),
        pl.col('parent_entity_id').fill_null(''),
        pl.col('member_entity_id_type').fill_null(''),
        pl.col('member_entity_id').fill_null(''),
        pl.col('role_term_id').fill_null(''),
        pl.col('stoichiometry').fill_null(''),
    ], separator='\x1f').alias(alias)


def _build_interaction(interaction_evidence: pl.DataFrame) -> pl.DataFrame:
    if interaction_evidence.is_empty():
        return _empty_interaction()

    normalized = _normalize_interaction_evidence(interaction_evidence)
    return (
        normalized
        .group_by(INTERACTION_KEY_COLUMNS)
        .agg([
            pl.len().cast(pl.Int64).alias('evidence_count'),
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .with_columns([
            _interaction_key_expr(),
        ])
        .select([
            'interaction_id',
            'entity_a_id',
            'entity_a_id_type',
            'entity_b_id',
            'entity_b_id_type',
            'direction',
            'sign',
            'evidence_count',
            'sources',
        ])
        .sort(INTERACTION_KEY_COLUMNS)
    )


def _build_interaction_annotation(
    annotations: pl.DataFrame,
    interaction_evidence: pl.DataFrame,
) -> pl.DataFrame:
    if annotations.is_empty() or interaction_evidence.is_empty():
        return _empty_interaction_annotation()

    interaction_mapping = (
        _normalize_interaction_evidence(interaction_evidence)
        .select([
            'source',
            pl.col('interaction_id').alias('evidence_interaction_id'),
            *INTERACTION_KEY_COLUMNS,
            _interaction_key_expr('aggregate_interaction_id'),
        ])
    )

    return (
        annotations
        .filter(pl.col('subject_type') == 'interaction')
        .with_columns([
            pl.col('subject_id').cast(pl.Int64, strict=False).alias('evidence_interaction_id'),
            pl.col('cv_term').cast(pl.Utf8),
            pl.col('source').cast(pl.Utf8),
        ])
        .drop_nulls(['evidence_interaction_id', 'cv_term'])
        .join(
            interaction_mapping,
            on=['source', 'evidence_interaction_id'],
            how='inner',
        )
        .group_by(['aggregate_interaction_id', 'cv_term'])
        .agg([
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .rename({'aggregate_interaction_id': 'interaction_id'})
        .sort(['interaction_id', 'cv_term'])
    )


def _build_association(association_evidence: pl.DataFrame) -> pl.DataFrame:
    if association_evidence.is_empty():
        return _empty_association()

    return (
        association_evidence
        .with_columns([
            pl.col('parent_entity_id').cast(pl.Utf8),
            pl.col('parent_entity_id_type').cast(pl.Utf8),
            pl.col('member_entity_id').cast(pl.Utf8),
            pl.col('member_entity_id_type').cast(pl.Utf8),
            pl.col('role_term_id').cast(pl.Utf8),
            pl.col('stoichiometry').cast(pl.Utf8),
            pl.col('source').cast(pl.Utf8),
        ])
        .group_by(ASSOCIATION_KEY_COLUMNS)
        .agg([
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .with_columns([
            _association_key_expr(),
        ])
        .select([
            'association_id',
            'parent_entity_id',
            'parent_entity_id_type',
            'member_entity_id',
            'member_entity_id_type',
            'role_term_id',
            'stoichiometry',
            'sources',
        ])
        .sort(ASSOCIATION_KEY_COLUMNS)
    )


def _build_entity_annotation(annotations: pl.DataFrame) -> pl.DataFrame:
    if annotations.is_empty():
        return _empty_entity_annotation()

    return (
        annotations
        .filter(pl.col('subject_type') == 'entity')
        .with_columns([
            pl.col('subject_id').cast(pl.Utf8).alias('entity_id'),
            pl.col('subject_id_type').cast(pl.Utf8).alias('entity_id_type'),
            pl.col('cv_term').cast(pl.Utf8),
            pl.col('source').cast(pl.Utf8),
        ])
        .drop_nulls(['entity_id', 'entity_id_type', 'cv_term'])
        .group_by(['entity_id', 'entity_id_type', 'cv_term'])
        .agg([
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .sort(['entity_id_type', 'entity_id', 'cv_term'])
    )


def _write_if_nonempty(frame: pl.DataFrame, path: Path) -> None:
    if frame.is_empty():
        if path.exists():
            path.unlink()
        return
    frame.write_parquet(path)


def _reduce_entities(entities: pl.DataFrame, identifiers: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    canonical = (
        identifiers
        .filter(pl.col('is_canonical'))
        .sort(['entity_id_type', 'entity_id', 'identifier_type', 'identifier'])
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.col('identifier').first().alias('canonical_identifier'),
            pl.col('identifier_type').first().alias('canonical_identifier_type'),
        ])
        if not identifiers.is_empty() else
        pl.DataFrame({
            'entity_id': pl.Series([], dtype=pl.Utf8),
            'entity_id_type': pl.Series([], dtype=pl.Utf8),
            'canonical_identifier': pl.Series([], dtype=pl.Utf8),
            'canonical_identifier_type': pl.Series([], dtype=pl.Utf8),
        })
    )

    folded = (
        identifiers
        .filter(~pl.col('is_canonical'))
        .sort(['entity_id_type', 'entity_id', 'identifier_type', 'identifier'])
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.struct(['identifier', 'identifier_type']).alias('identifiers'),
        ])
        if not identifiers.is_empty() else
        pl.DataFrame({
            'entity_id': pl.Series([], dtype=pl.Utf8),
            'entity_id_type': pl.Series([], dtype=pl.Utf8),
            'identifiers': pl.Series([], dtype=pl.List(_IDENTIFIER_STRUCT)),
        })
    )

    reduced = (
        entities
        .sort(['entity_id_type', 'entity_id'])
        .join(canonical, on=['entity_id', 'entity_id_type'], how='left')
        .join(folded, on=['entity_id', 'entity_id_type'], how='left')
        .with_row_index('entity_pk', offset=1)
        .with_columns([
            pl.col('entity_pk').cast(pl.Int64),
            pl.coalesce([pl.col('canonical_identifier'), pl.col('entity_id')]).cast(pl.Utf8).alias('canonical_identifier'),
            pl.coalesce([pl.col('canonical_identifier_type'), pl.col('entity_id_type')]).cast(pl.Utf8).alias('canonical_identifier_type'),
            pl.when(pl.col('identifiers').is_null()).then(_EMPTY_IDENTIFIERS).otherwise(pl.col('identifiers')).alias('identifiers'),
        ])
        .select([
            'entity_pk',
            'canonical_identifier',
            'canonical_identifier_type',
            'identifiers',
            'entity_type',
            'taxonomy_id',
            'entity_attributes',
            'sources',
        ])
    )

    entity_key_map = (
        entities
        .sort(['entity_id_type', 'entity_id'])
        .with_row_index('entity_pk', offset=1)
        .select([
            pl.col('entity_id').cast(pl.Utf8),
            pl.col('entity_id_type').cast(pl.Utf8),
            pl.col('entity_pk').cast(pl.Int64),
        ])
    )
    return reduced, entity_key_map


def _reduce_interaction_tables(
    interactions: pl.DataFrame,
    interaction_evidence: pl.DataFrame,
    interaction_annotations: pl.DataFrame,
    entity_key_map: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    if interactions.is_empty():
        empty_interaction = pl.DataFrame({
            'interaction_pk': pl.Series([], dtype=pl.Int64),
            'entity_a_pk': pl.Series([], dtype=pl.Int64),
            'entity_b_pk': pl.Series([], dtype=pl.Int64),
            'direction': pl.Series([], dtype=pl.Int64),
            'sign': pl.Series([], dtype=pl.Int64),
            'evidence_count': pl.Series([], dtype=pl.Int64),
            'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
        })
        empty_evidence = pl.DataFrame({
            'source': pl.Series([], dtype=pl.Utf8),
            'interaction_pk': pl.Series([], dtype=pl.Int64),
            'direction': pl.Series([], dtype=pl.Int64),
            'sign': pl.Series([], dtype=pl.Int64),
            'record_attributes': pl.Series([], dtype=interaction_evidence.schema.get('record_attributes', pl.Null)),
            'entity_a_attributes': pl.Series([], dtype=interaction_evidence.schema.get('entity_a_attributes', pl.Null)),
            'entity_b_attributes': pl.Series([], dtype=interaction_evidence.schema.get('entity_b_attributes', pl.Null)),
            'evidence': pl.Series([], dtype=interaction_evidence.schema.get('evidence', pl.Null)),
        })
        empty_annotations = pl.DataFrame({
            'interaction_pk': pl.Series([], dtype=pl.Int64),
            'cv_term': pl.Series([], dtype=pl.Utf8),
            'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
        })
        return empty_interaction, empty_evidence, empty_annotations

    interaction_with_pks = (
        interactions
        .join(
            entity_key_map.rename({
                'entity_id': 'entity_a_id',
                'entity_id_type': 'entity_a_id_type',
                'entity_pk': 'entity_a_pk',
            }),
            on=['entity_a_id', 'entity_a_id_type'],
            how='inner',
        )
        .join(
            entity_key_map.rename({
                'entity_id': 'entity_b_id',
                'entity_id_type': 'entity_b_id_type',
                'entity_pk': 'entity_b_pk',
            }),
            on=['entity_b_id', 'entity_b_id_type'],
            how='inner',
        )
        # interaction_pk assignment is deterministic because it is based on the
        # normalized structural interaction key, not source row order.
        .sort(INTERACTION_KEY_COLUMNS)
        .with_row_index('interaction_pk', offset=1)
        .with_columns(pl.col('interaction_pk').cast(pl.Int64))
    )

    reduced_interactions = interaction_with_pks.select([
        'interaction_pk',
        'entity_a_pk',
        'entity_b_pk',
        'direction',
        'sign',
        'evidence_count',
        'sources',
    ])

    if interaction_evidence.is_empty():
        reduced_evidence = pl.DataFrame({
            'source': pl.Series([], dtype=pl.Utf8),
            'interaction_pk': pl.Series([], dtype=pl.Int64),
            'direction': pl.Series([], dtype=pl.Int64),
            'sign': pl.Series([], dtype=pl.Int64),
            'record_attributes': pl.Series([], dtype=pl.Null),
            'entity_a_attributes': pl.Series([], dtype=pl.Null),
            'entity_b_attributes': pl.Series([], dtype=pl.Null),
            'evidence': pl.Series([], dtype=pl.Null),
        })
    else:
        reduced_evidence = (
            interaction_evidence
            .with_columns([
                pl.col('entity_a_id').cast(pl.Utf8),
                pl.col('entity_a_id_type').cast(pl.Utf8),
                pl.col('entity_b_id').cast(pl.Utf8),
                pl.col('entity_b_id_type').cast(pl.Utf8),
                pl.col('direction').cast(pl.Int64, strict=False),
                pl.col('sign').cast(pl.Int64, strict=False),
            ])
            .join(
                interaction_with_pks.select(INTERACTION_KEY_COLUMNS + ['interaction_pk']),
                on=INTERACTION_KEY_COLUMNS,
                how='inner',
            )
            .select([
                'source',
                'interaction_pk',
                'direction',
                'sign',
                'record_attributes',
                'entity_a_attributes',
                'entity_b_attributes',
                'evidence',
            ])
            .unique()
            .sort(['source', 'interaction_pk'])
        )

    if interaction_annotations.is_empty():
        reduced_annotations = pl.DataFrame({
            'interaction_pk': pl.Series([], dtype=pl.Int64),
            'cv_term': pl.Series([], dtype=pl.Utf8),
            'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
        })
    else:
        reduced_annotations = (
            interaction_annotations
            .join(
                interaction_with_pks.select(['interaction_id', 'interaction_pk']),
                on='interaction_id',
                how='inner',
            )
            .select(['interaction_pk', 'cv_term', 'sources'])
            .group_by(['interaction_pk', 'cv_term'])
            .agg([
                pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
            ])
            .sort(['interaction_pk', 'cv_term'])
        )

    return reduced_interactions, reduced_evidence, reduced_annotations


def _reduce_association_tables(
    associations: pl.DataFrame,
    association_evidence: pl.DataFrame,
    entity_key_map: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if associations.is_empty():
        empty_association = pl.DataFrame({
            'association_pk': pl.Series([], dtype=pl.Int64),
            'parent_entity_pk': pl.Series([], dtype=pl.Int64),
            'member_entity_pk': pl.Series([], dtype=pl.Int64),
            'role_term_id': pl.Series([], dtype=pl.Utf8),
            'stoichiometry': pl.Series([], dtype=pl.Utf8),
            'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
        })
        empty_evidence = pl.DataFrame({
            'source': pl.Series([], dtype=pl.Utf8),
            'association_pk': pl.Series([], dtype=pl.Int64),
            'role_term_id': pl.Series([], dtype=pl.Utf8),
            'stoichiometry': pl.Series([], dtype=pl.Utf8),
            'record_attributes': pl.Series([], dtype=association_evidence.schema.get('record_attributes', pl.Null)),
            'parent_attributes': pl.Series([], dtype=association_evidence.schema.get('parent_attributes', pl.Null)),
            'member_attributes': pl.Series([], dtype=association_evidence.schema.get('member_attributes', pl.Null)),
            'evidence': pl.Series([], dtype=association_evidence.schema.get('evidence', pl.Null)),
        })
        return empty_association, empty_evidence

    association_with_pks = (
        associations
        .join(
            entity_key_map.rename({
                'entity_id': 'parent_entity_id',
                'entity_id_type': 'parent_entity_id_type',
                'entity_pk': 'parent_entity_pk',
            }),
            on=['parent_entity_id', 'parent_entity_id_type'],
            how='inner',
        )
        .join(
            entity_key_map.rename({
                'entity_id': 'member_entity_id',
                'entity_id_type': 'member_entity_id_type',
                'entity_pk': 'member_entity_pk',
            }),
            on=['member_entity_id', 'member_entity_id_type'],
            how='inner',
        )
        # association_pk assignment is deterministic because it is based on the
        # normalized structural association key, not source row order.
        .sort(ASSOCIATION_KEY_COLUMNS)
        .with_row_index('association_pk', offset=1)
        .with_columns(pl.col('association_pk').cast(pl.Int64))
    )

    reduced_associations = association_with_pks.select([
        'association_pk',
        'parent_entity_pk',
        'member_entity_pk',
        'role_term_id',
        'stoichiometry',
        'sources',
    ])

    if association_evidence.is_empty():
        reduced_evidence = pl.DataFrame({
            'source': pl.Series([], dtype=pl.Utf8),
            'association_pk': pl.Series([], dtype=pl.Int64),
            'role_term_id': pl.Series([], dtype=pl.Utf8),
            'stoichiometry': pl.Series([], dtype=pl.Utf8),
            'record_attributes': pl.Series([], dtype=pl.Null),
            'parent_attributes': pl.Series([], dtype=pl.Null),
            'member_attributes': pl.Series([], dtype=pl.Null),
            'evidence': pl.Series([], dtype=pl.Null),
        })
    else:
        reduced_evidence = (
            association_evidence
            .with_columns([
                pl.col('parent_entity_id').cast(pl.Utf8),
                pl.col('parent_entity_id_type').cast(pl.Utf8),
                pl.col('member_entity_id').cast(pl.Utf8),
                pl.col('member_entity_id_type').cast(pl.Utf8),
                pl.col('role_term_id').cast(pl.Utf8),
                pl.col('stoichiometry').cast(pl.Utf8),
            ])
            .join(
                association_with_pks.select(ASSOCIATION_KEY_COLUMNS + ['association_pk']),
                on=ASSOCIATION_KEY_COLUMNS,
                how='inner',
            )
            .select([
                'source',
                'association_pk',
                'role_term_id',
                'stoichiometry',
                'record_attributes',
                'parent_attributes',
                'member_attributes',
                'evidence',
            ])
            .unique()
            .sort(['source', 'association_pk'])
        )

    return reduced_associations, reduced_evidence


def _reduce_entity_annotations(entity_annotations: pl.DataFrame, entity_key_map: pl.DataFrame) -> pl.DataFrame:
    if entity_annotations.is_empty():
        return pl.DataFrame({
            'entity_pk': pl.Series([], dtype=pl.Int64),
            'cv_term': pl.Series([], dtype=pl.Utf8),
            'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
        })

    return (
        entity_annotations
        .join(
            entity_key_map,
            on=['entity_id', 'entity_id_type'],
            how='inner',
        )
        .select(['entity_pk', 'cv_term', 'sources'])
        .group_by(['entity_pk', 'cv_term'])
        .agg([
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .sort(['entity_pk', 'cv_term'])
    )


def _reduce_frames(
    *,
    entities: pl.DataFrame,
    identifiers: pl.DataFrame,
    interaction_evidence: pl.DataFrame,
    interactions: pl.DataFrame,
    association_evidence: pl.DataFrame,
    associations: pl.DataFrame,
    entity_annotations: pl.DataFrame,
    interaction_annotations: pl.DataFrame,
) -> tuple[dict[str, pl.DataFrame], dict[str, int]]:
    reduced_entities, entity_key_map = _reduce_entities(entities, identifiers)
    reduced_interactions, reduced_interaction_evidence, reduced_interaction_annotations = _reduce_interaction_tables(
        interactions,
        interaction_evidence,
        interaction_annotations,
        entity_key_map,
    )
    reduced_associations, reduced_association_evidence = _reduce_association_tables(
        associations,
        association_evidence,
        entity_key_map,
    )
    reduced_entity_annotations = _reduce_entity_annotations(entity_annotations, entity_key_map)

    reduced_outputs = {
        'entity.parquet': reduced_entities,
        'interaction.parquet': reduced_interactions,
        'interaction_evidence.parquet': reduced_interaction_evidence,
        'association.parquet': reduced_associations,
        'association_evidence.parquet': reduced_association_evidence,
        'entity_annotation.parquet': reduced_entity_annotations,
        'interaction_annotation.parquet': reduced_interaction_annotations,
    }
    reduction_summary = {
        'entity_count': int(reduced_entities.height),
        'interaction_count': int(reduced_interactions.height),
        'association_count': int(reduced_associations.height),
        'entity_annotation_count': int(reduced_entity_annotations.height),
        'interaction_annotation_count': int(reduced_interaction_annotations.height),
    }
    return reduced_outputs, reduction_summary


def deduplicate_target_schema_dir(output_dir: str | Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    entities_path = output_dir / 'entity.parquet'
    identifiers_path = output_dir / 'entity_identifiers.parquet'
    interactions_path = output_dir / 'interaction_evidence.parquet'
    interaction_aggregate_path = output_dir / 'interaction.parquet'
    associations_path = output_dir / 'association_evidence.parquet'
    association_aggregate_path = output_dir / 'association.parquet'
    annotations_path = output_dir / 'annotations.parquet'
    entity_annotations_path = output_dir / 'entity_annotation.parquet'
    interaction_annotations_path = output_dir / 'interaction_annotation.parquet'

    if not entities_path.exists():
        return {'merged_entities': 0, 'entity_count_before': 0, 'entity_count_after': 0}

    entities = pl.read_parquet(entities_path)
    if entities.is_empty():
        if identifiers_path.exists() and pl.read_parquet(identifiers_path).is_empty():
            identifiers_path.unlink()
        for path in (
            interaction_aggregate_path,
            association_aggregate_path,
            entity_annotations_path,
            interaction_annotations_path,
        ):
            if path.exists():
                path.unlink()
        return {'merged_entities': 0, 'entity_count_before': 0, 'entity_count_after': 0}

    entities_dedup = _dedup_entities(entities)
    identifiers = (
        _dedup_identifiers(pl.read_parquet(identifiers_path))
        if identifiers_path.exists() else
        _empty_identifiers()
    )

    interaction_evidence = _normalize_interaction_evidence(
        pl.read_parquet(interactions_path) if interactions_path.exists() else _empty_interaction().drop(['interaction_id', 'evidence_count', 'sources'])
    )
    interaction_evidence = interaction_evidence.unique() if not interaction_evidence.is_empty() else interaction_evidence
    interaction = _build_interaction(interaction_evidence)

    association_evidence = (
        pl.read_parquet(associations_path).unique() if associations_path.exists() else pl.DataFrame()
    )
    association = _build_association(association_evidence)

    annotations = pl.read_parquet(annotations_path).unique() if annotations_path.exists() else pl.DataFrame()
    entity_annotations = _build_entity_annotation(annotations)
    interaction_annotations = _build_interaction_annotation(annotations, interaction_evidence)

    reduced_outputs, reduction_summary = _reduce_frames(
        entities=entities_dedup,
        identifiers=identifiers,
        interaction_evidence=interaction_evidence,
        interactions=interaction,
        association_evidence=association_evidence,
        associations=association,
        entity_annotations=entity_annotations,
        interaction_annotations=interaction_annotations,
    )

    reduced_outputs['entity.parquet'].write_parquet(entities_path)
    if identifiers_path.exists():
        identifiers_path.unlink()
    _write_if_nonempty(reduced_outputs['interaction.parquet'], interaction_aggregate_path)
    _write_if_nonempty(reduced_outputs['interaction_evidence.parquet'], interactions_path)
    _write_if_nonempty(reduced_outputs['association.parquet'], association_aggregate_path)
    _write_if_nonempty(reduced_outputs['association_evidence.parquet'], associations_path)
    _write_if_nonempty(reduced_outputs['entity_annotation.parquet'], entity_annotations_path)
    _write_if_nonempty(reduced_outputs['interaction_annotation.parquet'], interaction_annotations_path)
    if annotations_path.exists():
        annotations_path.unlink()

    merged_entities = int(entities.height - entities_dedup.height)
    return {
        'merged_entities': merged_entities,
        'entity_count_before': int(entities.height),
        'entity_count_after': int(entities_dedup.height),
        'interaction_count': int(interaction.height),
        'reduction_summary': reduction_summary,
    }
