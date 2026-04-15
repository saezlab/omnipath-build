from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl


def _empty_identifiers() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_id': pl.Series([], dtype=pl.Utf8),
        'entity_id_type': pl.Series([], dtype=pl.Utf8),
        'identifier': pl.Series([], dtype=pl.Utf8),
        'identifier_type': pl.Series([], dtype=pl.Utf8),
        'is_canonical': pl.Series([], dtype=pl.Boolean),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _dedup_entities(entities: pl.DataFrame) -> pl.DataFrame:
    if entities.is_empty():
        return entities
    return (
        entities
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.col('entity_type').drop_nulls().first().alias('entity_type'),
            pl.col('entity_attributes').drop_nulls().first().alias('entity_attributes'),
            pl.col('taxonomy_id').drop_nulls().first().alias('taxonomy_id'),
            pl.col('source').drop_nulls().first().alias('source'),
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

    if not entities_path.exists():
        return {'merged_entities': 0, 'entity_count_before': 0, 'entity_count_after': 0}

    entities = pl.read_parquet(entities_path)
    if entities.is_empty():
        if identifiers_path.exists() and pl.read_parquet(identifiers_path).is_empty():
            _empty_identifiers().write_parquet(identifiers_path)
        return {'merged_entities': 0, 'entity_count_before': 0, 'entity_count_after': 0}

    entities_dedup = _dedup_entities(entities)
    entities_dedup.write_parquet(entities_path)

    if identifiers_path.exists():
        identifiers = pl.read_parquet(identifiers_path)
        _dedup_identifiers(identifiers).write_parquet(identifiers_path)

    if interactions_path.exists():
        pl.read_parquet(interactions_path).unique().write_parquet(interactions_path)

    if associations_path.exists():
        pl.read_parquet(associations_path).unique().write_parquet(associations_path)

    if annotations_path.exists():
        pl.read_parquet(annotations_path).unique().write_parquet(annotations_path)

    merged_entities = int(entities.height - entities_dedup.height)
    return {
        'merged_entities': merged_entities,
        'entity_count_before': int(entities.height),
        'entity_count_after': int(entities_dedup.height),
    }
