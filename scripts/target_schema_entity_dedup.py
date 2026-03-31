from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from pypath.internals.cv_terms import IdentifierNamespaceCv


SAFE_MERGE_IDENTIFIER_TYPES: tuple[str, ...] = (
    str(IdentifierNamespaceCv.UNIPROT.value),
    str(IdentifierNamespaceCv.UNIPROT_TREMBL.value),
    str(IdentifierNamespaceCv.UNIPARC.value),
    str(IdentifierNamespaceCv.REFSEQ_PROTEIN.value),
    str(IdentifierNamespaceCv.ENTREZ.value),
    str(IdentifierNamespaceCv.HGNC.value),
    str(IdentifierNamespaceCv.ENSEMBL.value),
    str(IdentifierNamespaceCv.REFSEQ.value),
    str(IdentifierNamespaceCv.STANDARD_INCHI.value),
    str(IdentifierNamespaceCv.STANDARD_INCHI_KEY.value),
    str(IdentifierNamespaceCv.COMPLEXPORTAL.value),
    str(IdentifierNamespaceCv.REACTOME_STABLE_ID.value),
    str(IdentifierNamespaceCv.REACTOME_ID.value),
    str(IdentifierNamespaceCv.SIGNOR.value),
    str(IdentifierNamespaceCv.INTACT.value),
)

SAFE_MERGE_PRIORITY: dict[str, int] = {type_id: rank for rank, type_id in enumerate(SAFE_MERGE_IDENTIFIER_TYPES)}

CASE_NORMALIZE_UPPER: frozenset[str] = frozenset({
    str(IdentifierNamespaceCv.UNIPROT.value),
    str(IdentifierNamespaceCv.UNIPROT_TREMBL.value),
    str(IdentifierNamespaceCv.UNIPARC.value),
    str(IdentifierNamespaceCv.REFSEQ.value),
    str(IdentifierNamespaceCv.REFSEQ_PROTEIN.value),
    str(IdentifierNamespaceCv.ENSEMBL.value),
    str(IdentifierNamespaceCv.ENTREZ.value),
    str(IdentifierNamespaceCv.HGNC.value),
    str(IdentifierNamespaceCv.STANDARD_INCHI_KEY.value),
    str(IdentifierNamespaceCv.CHEBI.value),
})


def _normalize_identifier_expr(type_col: str = 'identifier_type_id', value_col: str = 'identifier') -> pl.Expr:
    ident = pl.col(value_col).cast(pl.Utf8).str.strip_chars().str.replace_all(r'\s+', ' ')
    return (
        pl.when(pl.col(type_col).is_in(sorted(CASE_NORMALIZE_UPPER))).then(ident.str.to_uppercase())
        .when(pl.col(type_col) == str(IdentifierNamespaceCv.CHEBI.value))
        .then(
            pl.when(ident.str.to_uppercase().str.contains(r'^\d+$'))
            .then(pl.lit('CHEBI:') + ident.str.to_uppercase())
            .otherwise(ident.str.to_uppercase().str.replace(r'^(CHEBI:)+', 'CHEBI:'))
        )
        .otherwise(ident)
        .alias('normalized_identifier')
    )


def _priority_df() -> pl.DataFrame:
    return pl.DataFrame({
        'identifier_type_id': list(SAFE_MERGE_PRIORITY.keys()),
        'priority_rank': list(SAFE_MERGE_PRIORITY.values()),
    })


def _build_entity_id_mapping(entities: pl.DataFrame, entity_identifiers: pl.DataFrame) -> pl.DataFrame:
    safe_claims = (
        entity_identifiers
        .filter(pl.col('identifier_type_id').is_in(SAFE_MERGE_IDENTIFIER_TYPES))
        .with_columns(_normalize_identifier_expr())
        .filter(pl.col('normalized_identifier').is_not_null() & (pl.col('normalized_identifier') != ''))
        .select(['entity_id', 'identifier_type_id', 'normalized_identifier'])
        .unique()
    )

    if safe_claims.is_empty():
        return entities.select([
            pl.col('entity_id').alias('old_entity_id'),
            pl.col('entity_id').alias('canonical_entity_id'),
        ])

    primary_keys = (
        safe_claims
        .join(_priority_df(), on='identifier_type_id', how='left')
        .sort(['entity_id', 'priority_rank', 'identifier_type_id', 'normalized_identifier'])
        .group_by('entity_id')
        .agg([
            pl.col('identifier_type_id').first().alias('primary_identifier_type_id'),
            pl.col('normalized_identifier').first().alias('primary_identifier'),
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


def _first_non_null_expr(col_name: str, alias: str | None = None) -> pl.Expr:
    expr = pl.col(col_name).drop_nulls().first()
    return expr.alias(alias or col_name)


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

    identifiers_norm = entity_identifiers.with_columns(_normalize_identifier_expr())
    remapped_identifiers = (
        identifiers_norm
        .join(entity_map, left_on='entity_id', right_on='old_entity_id', how='left')
        .with_columns([
            pl.coalesce([pl.col('canonical_entity_id'), pl.col('entity_id')]).alias('entity_id'),
            pl.coalesce([pl.col('normalized_identifier'), pl.col('identifier')]).alias('identifier'),
        ])
        .drop(['old_entity_id', 'canonical_entity_id', 'normalized_identifier'], strict=False)
    )

    canonical_identifier_rows = (
        remapped_identifiers
        .with_columns([
            pl.col('identifier_type_id').replace(SAFE_MERGE_PRIORITY, default=1_000_000).alias('priority_rank'),
            _normalize_identifier_expr(),
        ])
        .sort(['entity_id', 'priority_rank', 'identifier_type_id', 'normalized_identifier'])
        .group_by('entity_id')
        .agg([
            pl.col('identifier_type_id').first().alias('canonical_identifier_type_id'),
            pl.col('identifier').first().alias('canonical_identifier'),
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
            pl.col('entity_type_id').drop_nulls().unique().alias('entity_type_ids'),
        ])
        .with_columns([
            pl.when(pl.col('entity_type_ids').list.len() == 1)
            .then(pl.col('entity_type_ids').list.first())
            .otherwise(pl.col('entity_type_ids').list.first())
            .alias('entity_type_id')
        ])
        .select(['entity_id', 'entity_type_id'])
    )

    display_name_summary = (
        remapped_entities
        .group_by('entity_id')
        .agg([
            pl.col('display_name').drop_nulls().first().alias('display_name'),
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
        .with_columns([
            pl.coalesce([pl.col('display_name'), pl.col('canonical_identifier')]).alias('display_name'),
        ])
        .select([
            'entity_id',
            'entity_type_id',
            'display_name',
            'canonical_identifier',
            'canonical_identifier_type_id',
            'taxonomy_id',
            'source',
        ])
        .sort('entity_id')
    )

    entity_identifiers_dedup = (
        remapped_identifiers
        .select(['entity_id', 'identifier', 'identifier_type_id', 'source'])
        .unique()
        .join(
            canonical_identifier_rows,
            on='entity_id',
            how='left',
        )
        .with_columns([
            ((pl.col('identifier_type_id') == pl.col('canonical_identifier_type_id')) & (pl.col('identifier') == pl.col('canonical_identifier'))).alias('is_canonical')
        ])
        .select(['entity_id', 'identifier', 'identifier_type_id', 'is_canonical', 'source'])
        .sort(['entity_id', 'identifier_type_id', 'identifier', 'source'])
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
