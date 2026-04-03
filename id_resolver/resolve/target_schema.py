from __future__ import annotations

from pathlib import Path

import polars as pl

from id_resolver.resolve.parquet import (
    RESOLVED_ID_COLUMN,
    RESOLVED_ID_TYPE_COLUMN,
    RESOLUTION_STATUS_COLUMN,
    STANDARD_INCHI_TYPE,
    UNIPROT_TYPE,
    resolve_identifier_frame,
)

PROTEIN_ENTITY_TYPES: frozenset[str] = frozenset({
    'MI:0326:Protein',
})

CHEMICAL_ENTITY_TYPES: frozenset[str] = frozenset({
    'MI:0328:Small Molecule',
    'OM:0011:Lipid',
})

TARGET_ENTITY_TYPES: frozenset[str] = PROTEIN_ENTITY_TYPES | CHEMICAL_ENTITY_TYPES


def _normalize_target_schema_entities(entities: pl.DataFrame) -> pl.DataFrame:
    return entities.with_columns([
        pl.col('entity_id').cast(pl.Int64),
        pl.col('entity_type').cast(pl.Utf8),
        pl.col('canonical_identifier').cast(pl.Utf8),
        pl.col('canonical_identifier_type').cast(pl.Utf8),
        pl.when(pl.col('taxonomy_id').is_null() | (pl.col('taxonomy_id').cast(pl.Utf8) == ''))
        .then(pl.lit(None, dtype=pl.Utf8))
        .otherwise(pl.col('taxonomy_id').cast(pl.Utf8))
        .alias('taxonomy_id'),
    ])


def _normalize_target_schema_identifiers(identifiers: pl.DataFrame) -> pl.DataFrame:
    return identifiers.with_columns([
        pl.col('entity_id').cast(pl.Int64),
        pl.col('identifier').cast(pl.Utf8),
        pl.col('identifier_type').cast(pl.Utf8),
        pl.col('is_canonical').cast(pl.Boolean),
        pl.col('source').cast(pl.Utf8),
    ])


def normalize_target_schema_dir(
    source_dir: str | Path,
    mapping_dir: str | Path,
    source_name: str | None = None,
) -> dict[str, int]:
    source_dir = Path(source_dir)
    entities_path = source_dir / 'entities.parquet'
    identifiers_path = source_dir / 'entity_identifiers.parquet'

    if not entities_path.exists() or not identifiers_path.exists():
        return {
            'entities_seen': 0,
            'eligible_entities': 0,
            'resolved_entities': 0,
            'identifier_rows_added': 0,
            'entities_updated': 0,
        }

    entities = _normalize_target_schema_entities(pl.read_parquet(entities_path))
    identifiers = _normalize_target_schema_identifiers(pl.read_parquet(identifiers_path))
    if entities.is_empty() or identifiers.is_empty():
        return {
            'entities_seen': int(entities.height),
            'eligible_entities': 0,
            'resolved_entities': 0,
            'identifier_rows_added': 0,
            'entities_updated': 0,
        }

    eligible_entities = entities.filter(pl.col('entity_type').is_in(list(TARGET_ENTITY_TYPES))).select([
        'entity_id',
        'entity_type',
        'taxonomy_id',
    ])
    if eligible_entities.is_empty():
        return {
            'entities_seen': int(entities.height),
            'eligible_entities': 0,
            'resolved_entities': 0,
            'identifier_rows_added': 0,
            'entities_updated': 0,
        }

    resolver_input = (
        identifiers
        .join(eligible_entities, on='entity_id', how='inner')
        .select([
            'entity_id',
            'entity_type',
            'taxonomy_id',
            pl.col('identifier').alias('id'),
            pl.col('identifier_type').alias('id_type'),
        ])
        .unique()
    )

    resolved = resolve_identifier_frame(
        resolver_input,
        mapping_dir,
        id_column='id',
        id_type_column='id_type',
        taxonomy_column='taxonomy_id',
    )

    preferred = (
        resolved
        .filter(pl.col(RESOLUTION_STATUS_COLUMN).is_in(['identity', 'mapped']))
        .with_columns([
            pl.when(pl.col('entity_type').is_in(list(PROTEIN_ENTITY_TYPES)))
            .then(pl.lit(UNIPROT_TYPE))
            .when(pl.col('entity_type').is_in(list(CHEMICAL_ENTITY_TYPES)))
            .then(pl.lit(STANDARD_INCHI_TYPE))
            .otherwise(pl.lit(None, dtype=pl.Utf8))
            .alias('_preferred_type')
        ])
        .filter(pl.col(RESOLVED_ID_TYPE_COLUMN) == pl.col('_preferred_type'))
        .group_by('entity_id')
        .agg([
            pl.col(RESOLVED_ID_COLUMN).n_unique().alias('_resolved_count'),
            pl.col(RESOLVED_ID_COLUMN).first().alias('resolved_identifier'),
            pl.col(RESOLVED_ID_TYPE_COLUMN).first().alias('resolved_identifier_type'),
        ])
        .filter(pl.col('_resolved_count') == 1)
        .select(['entity_id', 'resolved_identifier', 'resolved_identifier_type'])
    )

    source_value = source_name or source_dir.name

    additions = (
        preferred
        .select([
            'entity_id',
            pl.col('resolved_identifier').alias('identifier'),
            pl.col('resolved_identifier_type').alias('identifier_type'),
        ])
        .join(
            identifiers.select(['entity_id', 'identifier', 'identifier_type']).unique(),
            on=['entity_id', 'identifier', 'identifier_type'],
            how='anti',
        )
        .with_columns([
            pl.lit(False).alias('is_canonical'),
            pl.lit(source_value).alias('source'),
        ])
        .select(identifiers.columns)
    )

    updated_entities = (
        entities
        .join(preferred, on='entity_id', how='left')
        .with_columns([
            pl.coalesce([pl.col('resolved_identifier'), pl.col('canonical_identifier')]).alias('canonical_identifier'),
            pl.coalesce([pl.col('resolved_identifier_type'), pl.col('canonical_identifier_type')]).alias('canonical_identifier_type'),
        ])
        .drop(['resolved_identifier', 'resolved_identifier_type'], strict=False)
        .select(entities.columns)
    )

    identifiers_with_additions = pl.concat([identifiers, additions], how='vertical_relaxed') if not additions.is_empty() else identifiers
    updated_identifiers = (
        identifiers_with_additions
        .join(
            updated_entities.select([
                'entity_id',
                pl.col('canonical_identifier').alias('_canonical_identifier'),
                pl.col('canonical_identifier_type').alias('_canonical_identifier_type'),
            ]),
            on='entity_id',
            how='left',
        )
        .with_columns([
            ((pl.col('identifier') == pl.col('_canonical_identifier')) & (pl.col('identifier_type') == pl.col('_canonical_identifier_type'))).alias('is_canonical')
        ])
        .drop(['_canonical_identifier', '_canonical_identifier_type'], strict=False)
        .select(identifiers.columns)
        .unique()
        .sort(['entity_id', 'identifier_type', 'identifier', 'source'])
    )

    updated_entities.write_parquet(entities_path)
    updated_identifiers.write_parquet(identifiers_path)

    return {
        'entities_seen': int(entities.height),
        'eligible_entities': int(eligible_entities.height),
        'resolved_entities': int(preferred.height),
        'identifier_rows_added': int(additions.height),
        'entities_updated': int(
            updated_entities
            .join(entities.select([
                'entity_id',
                pl.col('canonical_identifier').alias('_old_canonical_identifier'),
                pl.col('canonical_identifier_type').alias('_old_canonical_identifier_type'),
            ]), on='entity_id', how='inner')
            .filter(
                (pl.col('canonical_identifier') != pl.col('_old_canonical_identifier'))
                | (pl.col('canonical_identifier_type') != pl.col('_old_canonical_identifier_type'))
            )
            .height
        ),
    }


__all__ = [
    'PROTEIN_ENTITY_TYPES',
    'CHEMICAL_ENTITY_TYPES',
    'TARGET_ENTITY_TYPES',
    'normalize_target_schema_dir',
]
