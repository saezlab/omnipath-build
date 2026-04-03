from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.target_schema.canonical_priority import (
    SAFE_MERGE_IDENTIFIER_TYPES,
    SAFE_MERGE_PRIORITY,
    canonical_priority_rank,
)
from omnipath_build.target_schema.cv_labels import format_cv_term


def _raw_accession(type_value: str | None) -> str | None:
    if type_value is None:
        return None
    text = str(type_value)
    parts = text.split(':')
    if len(parts) >= 3:
        return ':'.join(parts[:2])
    return text


def _priority_df() -> pl.DataFrame:
    return pl.DataFrame({
        'identifier_type_id_raw': list(SAFE_MERGE_PRIORITY.keys()),
        'priority_rank': list(SAFE_MERGE_PRIORITY.values()),
    })


def _build_entity_id_mapping(entities: pl.DataFrame, entity_identifiers: pl.DataFrame) -> pl.DataFrame:
    safe_claims = (
        entity_identifiers
        .with_columns([
            pl.col('identifier_type').map_elements(_raw_accession, return_dtype=pl.Utf8).alias('identifier_type_id_raw'),
            pl.col('identifier').cast(pl.Utf8),
        ])
        .filter(pl.col('identifier_type_id_raw').is_in(SAFE_MERGE_IDENTIFIER_TYPES))
        .filter(pl.col('identifier').is_not_null() & (pl.col('identifier') != ''))
        .select(['entity_id', 'identifier_type_id_raw', 'identifier'])
        .unique()
    )

    if safe_claims.is_empty():
        return entities.select([
            pl.col('entity_id').alias('old_entity_id'),
            pl.col('entity_id').alias('canonical_entity_id'),
        ])

    primary_keys = (
        safe_claims
        .join(_priority_df(), on='identifier_type_id_raw', how='left')
        .sort(['entity_id', 'priority_rank', 'identifier_type_id_raw', 'identifier'])
        .group_by('entity_id')
        .agg([
            pl.col('identifier_type_id_raw').first().alias('primary_identifier_type_id'),
            pl.col('identifier').first().alias('primary_identifier'),
        ])
    )

    primary_key_canonical = (
        primary_keys
        .group_by(['primary_identifier_type_id', 'primary_identifier'])
        .agg(pl.col('entity_id').min().alias('canonical_entity_id'))
    )

    mapped = (
        primary_keys
        .join(primary_key_canonical, on=['primary_identifier_type_id', 'primary_identifier'], how='left')
        .select([
            pl.col('entity_id').alias('old_entity_id'),
            pl.col('canonical_entity_id'),
        ])
    )

    unmapped = (
        entities
        .select(pl.col('entity_id').alias('old_entity_id'))
        .join(mapped, on='old_entity_id', how='left')
        .with_columns(pl.coalesce([pl.col('canonical_entity_id'), pl.col('old_entity_id')]).alias('canonical_entity_id'))
        .select(['old_entity_id', 'canonical_entity_id'])
    )
    return unmapped.unique().sort('old_entity_id')


def deduplicate_target_schema_dir(output_dir: str | Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    entities_path = output_dir / 'entities.parquet'
    identifiers_path = output_dir / 'entity_identifiers.parquet'
    interactions_path = output_dir / 'interactions.parquet'
    associations_path = output_dir / 'associations.parquet'
    annotations_path = output_dir / 'annotations.parquet'
    if not annotations_path.exists():
        legacy_annotations_path = output_dir / 'cv_annotations.parquet'
        annotations_path = legacy_annotations_path if legacy_annotations_path.exists() else annotations_path

    if not entities_path.exists() or not identifiers_path.exists():
        return {'merged_entities': 0, 'entity_count_before': 0, 'entity_count_after': 0}

    entities = pl.read_parquet(entities_path)
    entity_identifiers = pl.read_parquet(identifiers_path)

    if entities.is_empty():
        return {'merged_entities': 0, 'entity_count_before': 0, 'entity_count_after': 0}

    entity_map = _build_entity_id_mapping(entities, entity_identifiers)

    remapped_entities = (
        entities
        .join(entity_map, left_on='entity_id', right_on='old_entity_id', how='left')
        .with_columns(pl.coalesce([pl.col('canonical_entity_id'), pl.col('entity_id')]).alias('entity_id'))
        .drop('canonical_entity_id')
    )

    remapped_identifiers = (
        entity_identifiers
        .join(entity_map, left_on='entity_id', right_on='old_entity_id', how='left')
        .with_columns([
            pl.coalesce([pl.col('canonical_entity_id'), pl.col('entity_id')]).alias('entity_id'),
        ])
        .drop(['old_entity_id', 'canonical_entity_id'], strict=False)
    )

    canonical_identifier_rows = (
        remapped_identifiers
        .with_columns([
            pl.col('identifier_type').map_elements(_raw_accession, return_dtype=pl.Utf8).alias('identifier_type_id_raw'),
            pl.col('identifier_type').map_elements(lambda x: canonical_priority_rank(_raw_accession(x)), return_dtype=pl.Int64).alias('priority_rank'),
        ])
        .sort(['entity_id', 'priority_rank', 'identifier_type_id_raw', 'identifier'])
        .group_by('entity_id')
        .agg([
            pl.col('identifier_type_id_raw').first().alias('canonical_identifier_type_id_raw'),
            pl.col('identifier').first().alias('canonical_identifier'),
        ])
        .with_columns([
            pl.col('canonical_identifier_type_id_raw').map_elements(format_cv_term, return_dtype=pl.Utf8).alias('canonical_identifier_type'),
        ])
    )

    taxonomy_summary = (
        remapped_entities
        .group_by('entity_id')
        .agg([
            pl.col('taxonomy_id').drop_nulls().unique().alias('taxonomy_ids'),
        ])
        .with_columns([
            pl.when(pl.col('taxonomy_ids').list.len() == 1)
            .then(pl.col('taxonomy_ids').list.first())
            .otherwise(pl.lit(None, dtype=pl.Utf8))
            .alias('taxonomy_id')
        ])
        .select(['entity_id', 'taxonomy_id'])
    )

    type_summary = (
        remapped_entities
        .group_by('entity_id')
        .agg([
            pl.col('entity_type').drop_nulls().unique().alias('entity_types'),
        ])
        .with_columns([
            pl.when(pl.col('entity_types').list.len() == 1)
            .then(pl.col('entity_types').list.first())
            .otherwise(pl.col('entity_types').list.first())
            .alias('entity_type')
        ])
        .select(['entity_id', 'entity_type'])
    )

    display_name_summary = (
        remapped_entities
        .group_by('entity_id')
        .agg([
            pl.col('display_name').drop_nulls().first().alias('display_name'),
            pl.col('entity_attributes').drop_nulls().first().alias('entity_attributes'),
            pl.col('source').drop_nulls().first().alias('source'),
        ])
    )

    entities_dedup = (
        remapped_entities
        .select('entity_id')
        .unique()
        .join(type_summary, on='entity_id', how='left')
        .join(display_name_summary, on='entity_id', how='left')
        .join(taxonomy_summary, on='entity_id', how='left')
        .join(canonical_identifier_rows, on='entity_id', how='left')
        .select([
            'entity_id',
            'entity_type',
            'display_name',
            'canonical_identifier',
            'canonical_identifier_type',
            'entity_attributes',
            'taxonomy_id',
            'source',
        ])
        .sort('entity_id')
    )

    entity_identifiers_dedup = (
        remapped_identifiers
        .with_columns([
            pl.col('identifier_type').map_elements(_raw_accession, return_dtype=pl.Utf8).alias('identifier_type_id_raw'),
        ])
        .select(['entity_id', 'identifier', 'identifier_type', 'identifier_type_id_raw', 'source'])
        .unique()
        .join(
            canonical_identifier_rows,
            on='entity_id',
            how='left',
        )
        .with_columns([
            ((pl.col('identifier_type_id_raw') == pl.col('canonical_identifier_type_id_raw')) & (pl.col('identifier') == pl.col('canonical_identifier'))).alias('is_canonical')
        ])
        .select(['entity_id', 'identifier', 'identifier_type', 'is_canonical', 'source'])
        .sort(['entity_id', 'identifier_type', 'identifier', 'source'])
    )

    if interactions_path.exists():
        interactions = pl.read_parquet(interactions_path)
        interactions = (
            interactions
            .join(entity_map.rename({'old_entity_id': 'entity_a_id', 'canonical_entity_id': 'canonical_entity_a_id'}), on='entity_a_id', how='left')
            .join(entity_map.rename({'old_entity_id': 'entity_b_id', 'canonical_entity_id': 'canonical_entity_b_id'}), on='entity_b_id', how='left')
            .with_columns([
                pl.coalesce([pl.col('canonical_entity_a_id'), pl.col('entity_a_id')]).alias('entity_a_id'),
                pl.coalesce([pl.col('canonical_entity_b_id'), pl.col('entity_b_id')]).alias('entity_b_id'),
            ])
            .drop(['canonical_entity_a_id', 'canonical_entity_b_id'], strict=False)
        )
        interactions.write_parquet(interactions_path)

    if associations_path.exists():
        associations = pl.read_parquet(associations_path)
        associations = (
            associations
            .join(entity_map.rename({'old_entity_id': 'parent_entity_id', 'canonical_entity_id': 'canonical_parent_entity_id'}), on='parent_entity_id', how='left')
            .join(entity_map.rename({'old_entity_id': 'member_entity_id', 'canonical_entity_id': 'canonical_member_entity_id'}), on='member_entity_id', how='left')
            .with_columns([
                pl.coalesce([pl.col('canonical_parent_entity_id'), pl.col('parent_entity_id')]).alias('parent_entity_id'),
                pl.coalesce([pl.col('canonical_member_entity_id'), pl.col('member_entity_id')]).alias('member_entity_id'),
            ])
            .drop(['canonical_parent_entity_id', 'canonical_member_entity_id'], strict=False)
        )
        associations.write_parquet(associations_path)

    if annotations_path.exists():
        annotations = pl.read_parquet(annotations_path)
        annotations = (
            annotations
            .join(entity_map.rename({'old_entity_id': 'subject_id', 'canonical_entity_id': 'canonical_subject_id'}), on='subject_id', how='left')
            .with_columns([
                pl.when(pl.col('subject_type') == 'entity')
                .then(pl.coalesce([pl.col('canonical_subject_id'), pl.col('subject_id')]))
                .otherwise(pl.col('subject_id'))
                .alias('subject_id')
            ])
            .drop('canonical_subject_id', strict=False)
            .unique()
        )
        annotations.write_parquet(annotations_path)

    entities_dedup.write_parquet(entities_path)
    entity_identifiers_dedup.write_parquet(identifiers_path)

    merged_entities = int(entity_map.filter(pl.col('old_entity_id') != pl.col('canonical_entity_id')).height)
    return {
        'merged_entities': merged_entities,
        'entity_count_before': int(entities.height),
        'entity_count_after': int(entities_dedup.height),
    }
