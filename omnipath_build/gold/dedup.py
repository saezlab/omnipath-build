from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import polars as pl


_EMPTY_SOURCES = pl.lit([], dtype=pl.List(pl.Utf8))


def _stable_md5(value: str) -> str:
    return hashlib.md5(value.encode('utf-8')).hexdigest()


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
        pl.col('direction').cast(pl.Int64, strict=False),
        pl.col('sign').cast(pl.Int64, strict=False),
    ])


def _interaction_id_expr() -> pl.Expr:
    return pl.concat_str([
        pl.col('entity_a_id_type').fill_null(''),
        pl.col('entity_a_id').fill_null(''),
        pl.col('entity_b_id_type').fill_null(''),
        pl.col('entity_b_id').fill_null(''),
        pl.col('direction').cast(pl.Utf8).fill_null(''),
        pl.col('sign').cast(pl.Utf8).fill_null(''),
    ], separator='\x1f').map_elements(_stable_md5, return_dtype=pl.Utf8)


def _build_interaction(interaction_evidence: pl.DataFrame) -> pl.DataFrame:
    if interaction_evidence.is_empty():
        return _empty_interaction()

    normalized = _normalize_interaction_evidence(interaction_evidence)
    return (
        normalized
        .group_by([
            'entity_a_id',
            'entity_a_id_type',
            'entity_b_id',
            'entity_b_id_type',
            'direction',
            'sign',
        ], maintain_order=True)
        .agg([
            pl.len().cast(pl.Int64).alias('evidence_count'),
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .with_columns([
            _interaction_id_expr().alias('interaction_id'),
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
        .sort('interaction_id')
    )


def _build_interaction_annotation(
    annotations: pl.DataFrame,
    interaction_evidence: pl.DataFrame,
) -> pl.DataFrame:
    if annotations.is_empty() or interaction_evidence.is_empty():
        return _empty_interaction_annotation()

    interaction_mapping = (
        _normalize_interaction_evidence(interaction_evidence)
        .with_columns([
            _interaction_id_expr().alias('aggregate_interaction_id'),
        ])
        .select(['source', 'interaction_id', 'aggregate_interaction_id'])
        .rename({'interaction_id': 'evidence_interaction_id'})
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
        .group_by([
            'parent_entity_id',
            'parent_entity_id_type',
            'member_entity_id',
            'member_entity_id_type',
            'role_term_id',
            'stoichiometry',
        ], maintain_order=True)
        .agg([
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .with_columns([
            pl.concat_str([
                pl.col('parent_entity_id_type').fill_null(''),
                pl.col('parent_entity_id').fill_null(''),
                pl.col('member_entity_id_type').fill_null(''),
                pl.col('member_entity_id').fill_null(''),
                pl.col('role_term_id').fill_null(''),
                pl.col('stoichiometry').fill_null(''),
            ], separator='\x1f').map_elements(_stable_md5, return_dtype=pl.Utf8).alias('association_id'),
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
        .sort('association_id')
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
    _write_if_nonempty(entities_dedup, entities_path)

    if identifiers_path.exists():
        identifiers = pl.read_parquet(identifiers_path)
        _write_if_nonempty(_dedup_identifiers(identifiers), identifiers_path)

    interaction_evidence = _normalize_interaction_evidence(
        pl.read_parquet(interactions_path) if interactions_path.exists() else _empty_interaction().drop(['interaction_id', 'evidence_count', 'sources'])
    )
    if interactions_path.exists():
        _write_if_nonempty(interaction_evidence.unique(), interactions_path)
    interaction = _build_interaction(interaction_evidence.unique() if not interaction_evidence.is_empty() else interaction_evidence)
    _write_if_nonempty(interaction, interaction_aggregate_path)

    association_evidence = (
        pl.read_parquet(associations_path).unique() if associations_path.exists() else pl.DataFrame()
    )
    if associations_path.exists():
        _write_if_nonempty(association_evidence, associations_path)
    _write_if_nonempty(_build_association(association_evidence), association_aggregate_path)

    annotations = pl.read_parquet(annotations_path).unique() if annotations_path.exists() else pl.DataFrame()
    _write_if_nonempty(_build_entity_annotation(annotations), entity_annotations_path)
    _write_if_nonempty(
        _build_interaction_annotation(annotations, interaction_evidence.unique() if not interaction_evidence.is_empty() else interaction_evidence),
        interaction_annotations_path,
    )
    if annotations_path.exists():
        annotations_path.unlink()

    merged_entities = int(entities.height - entities_dedup.height)
    return {
        'merged_entities': merged_entities,
        'entity_count_before': int(entities.height),
        'entity_count_after': int(entities_dedup.height),
        'interaction_count': int(interaction.height),
    }
