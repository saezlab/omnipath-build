from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.gold.build_relation_annotation import build_relation_annotation
from omnipath_build.gold.build_resources import build_resources_parquet
from omnipath_build.gold.utils.table_schema import (
    COMBINED_ENTITY_RELATION_EVIDENCE_SCHEMA,
    COMBINED_ENTITY_RELATION_SCHEMA,
    COMBINED_ENTITY_SCHEMA,
    EMPTY_IDENTIFIERS,
    ENTITY_EVIDENCE_SCHEMA,
    IDENTIFIER_STRUCT,
    aggregate_unique_attribute_lists,
    aggregate_unique_string_lists,
    empty_frame,
)


@dataclass(frozen=True)
class GoldSourceDir:
    source: str
    path: Path


def discover_gold_source_dirs(gold_root: str | Path) -> list[GoldSourceDir]:
    root = Path(gold_root)
    if not root.exists():
        raise FileNotFoundError(f'Gold root does not exist: {root}')

    sources: list[GoldSourceDir] = []
    for source_dir in sorted(root.iterdir()):
        if not source_dir.is_dir():
            continue
        entity_path = source_dir / 'entities' / 'entity.parquet'
        if entity_path.exists():
            sources.append(GoldSourceDir(source=source_dir.name, path=source_dir))
    return sources


def _scan_source_artifact(
    source_dir: GoldSourceDir,
    sub_path: str,
    columns: list[pl.Expr | str],
) -> pl.LazyFrame | None:
    path = source_dir.path / sub_path
    if not path.exists():
        return None
    return pl.scan_parquet(path).select(columns)


def _build_entity(
    source_dirs: list[GoldSourceDir],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'entities/entity.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('entity_pk').cast(pl.Int64).alias('_local_entity_pk'),
            pl.col('entity_key').cast(pl.String),
            pl.col('canonical_identifier').cast(pl.String),
            pl.col('canonical_identifier_type').cast(pl.String),
            pl.col('identifiers'),
            pl.col('entity_type').cast(pl.String),
            pl.col('taxonomy_id').cast(pl.String),
            pl.col('entity_attributes'),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(frame)

    if not frames:
        return empty_frame(COMBINED_ENTITY_SCHEMA), pl.DataFrame({
            '_source': pl.Series([], dtype=pl.String),
            '_local_entity_pk': pl.Series([], dtype=pl.Int64),
            'entity_id': pl.Series([], dtype=pl.Int64),
        })

    source_entities = pl.concat(frames, how='vertical_relaxed').collect()

    # Explode all per-source identifiers and deduplicate globally by entity_key
    exploded_identifiers = (
        source_entities
        .select([
            'entity_key',
            pl.col('identifiers'),
        ])
        .explode('identifiers')
        .drop_nulls(['identifiers'])
        .select([
            'entity_key',
            pl.col('identifiers').struct.field('identifier').alias('identifier'),
            pl.col('identifiers').struct.field('identifier_type').alias('identifier_type'),
        ])
    )
    canonical_identifier_rows = (
        source_entities
        .select([
            'entity_key',
            pl.col('canonical_identifier').alias('identifier'),
            pl.col('canonical_identifier_type').alias('identifier_type'),
        ])
        .drop_nulls(['identifier', 'identifier_type'])
    )
    all_identifiers = pl.concat([
        exploded_identifiers,
        canonical_identifier_rows,
    ], how='vertical_relaxed').unique()
    identifier_lists = (
        all_identifiers
        .sort(['entity_key', 'identifier_type', 'identifier'])
        .group_by('entity_key')
        .agg([
            pl.struct(['identifier', 'identifier_type']).alias('identifiers'),
        ])
        if not exploded_identifiers.is_empty()
        else pl.DataFrame({
            'entity_key': pl.Series([], dtype=pl.String),
            'identifiers': pl.Series([], dtype=pl.List(IDENTIFIER_STRUCT)),
        })
    )

    combined = (
        source_entities
        .group_by('entity_key')
        .agg([
            pl.col('canonical_identifier').drop_nulls().first().alias('canonical_identifier'),
            pl.col('canonical_identifier_type').drop_nulls().first().alias('canonical_identifier_type'),
            pl.col('entity_type').drop_nulls().first().alias('entity_type'),
            pl.col('taxonomy_id').drop_nulls().first().alias('taxonomy_id'),
            aggregate_unique_attribute_lists('entity_attributes'),
            aggregate_unique_string_lists('sources'),
        ])
        .join(identifier_lists, on='entity_key', how='left')
        .sort('entity_key')
        .with_row_index('entity_id', offset=1)
        .with_columns([
            pl.col('entity_id').cast(pl.Int64),
            pl.when(pl.col('identifiers').is_null())
            .then(EMPTY_IDENTIFIERS)
            .otherwise(pl.col('identifiers'))
            .alias('identifiers'),
        ])
        .select(list(COMBINED_ENTITY_SCHEMA.keys()))
    )

    entity_id_map = (
        source_entities
        .filter(pl.col('_source').is_not_null() & pl.col('_local_entity_pk').is_not_null())
        .join(
            combined.select(['entity_id', 'entity_key']),
            on='entity_key',
            how='inner',
        )
        .select(['_source', '_local_entity_pk', 'entity_id', 'entity_key'])
        .unique()
    )
    return combined, entity_id_map


def _build_relation(
    source_dirs: list[GoldSourceDir],
    entity_id_map: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'relations/entity_relation.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('relation_pk').cast(pl.Int64).alias('_local_relation_pk'),
            pl.col('relation_key').cast(pl.String),
            pl.col('subject_entity_pk').cast(pl.Int64),
            pl.col('subject_entity_key').cast(pl.String),
            pl.col('predicate').cast(pl.String),
            pl.col('object_entity_pk').cast(pl.Int64),
            pl.col('object_entity_key').cast(pl.String),
            pl.col('relation_category').cast(pl.String),
            pl.col('evidence_count').cast(pl.Int64),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(frame)

    if not frames:
        return empty_frame(COMBINED_ENTITY_RELATION_SCHEMA), pl.DataFrame({
            '_source': pl.Series([], dtype=pl.String),
            '_local_relation_pk': pl.Series([], dtype=pl.Int64),
            'relation_id': pl.Series([], dtype=pl.Int64),
        })

    source_relations = pl.concat(frames, how='vertical_relaxed').collect()

    # Map per-source entity_pks to combined entity_ids using entity_key
    source_relations = (
        source_relations
        .join(
            entity_id_map.select(['entity_key', 'entity_id']).rename({
                'entity_key': 'subject_entity_key',
                'entity_id': 'subject_entity_id',
            }).unique(),
            on='subject_entity_key',
            how='inner',
        )
        .join(
            entity_id_map.select(['entity_key', 'entity_id']).rename({
                'entity_key': 'object_entity_key',
                'entity_id': 'object_entity_id',
            }).unique(),
            on='object_entity_key',
            how='inner',
        )
    )

    entity_types = (
        entity_id_map
        .join(
            pl.concat([
                _scan_source_artifact(sd, 'entities/entity.parquet', [
                    pl.col('entity_key').cast(pl.String),
                    pl.col('entity_type').cast(pl.String),
                ])
                for sd in source_dirs
            ], how='vertical_relaxed').collect(),
            on='entity_key',
            how='inner',
        )
        .select(['entity_id', 'entity_type'])
        .unique()
    )

    combined = (
        source_relations
        .group_by('relation_key')
        .agg([
            pl.col('subject_entity_id').drop_nulls().first().alias('subject_entity_id'),
            pl.col('subject_entity_key').drop_nulls().first().alias('subject_entity_key'),
            pl.col('predicate').drop_nulls().first().alias('predicate'),
            pl.col('object_entity_id').drop_nulls().first().alias('object_entity_id'),
            pl.col('object_entity_key').drop_nulls().first().alias('object_entity_key'),
            pl.col('relation_category').drop_nulls().first().alias('relation_category'),
            pl.col('evidence_count').sum().cast(pl.Int64).alias('evidence_count'),
            aggregate_unique_string_lists('sources'),
        ])
        .join(
            entity_types.rename({
                'entity_id': 'subject_entity_id',
                'entity_type': '_subject_entity_type',
            }),
            on='subject_entity_id',
            how='left',
        )
        .join(
            entity_types.rename({
                'entity_id': 'object_entity_id',
                'entity_type': '_object_entity_type',
            }),
            on='object_entity_id',
            how='left',
        )
        .with_columns(
            pl.concat_list(['_subject_entity_type', '_object_entity_type'])
            .list.drop_nulls()
            .list.unique()
            .list.sort()
            .alias('participant_types')
        )
        .sort('relation_key')
        .with_row_index('relation_id', offset=1)
        .with_columns(pl.col('relation_id').cast(pl.Int64))
        .select(list(COMBINED_ENTITY_RELATION_SCHEMA.keys()))
    )

    relation_id_map = (
        source_relations
        .join(
            combined.select([
                'relation_id',
                'relation_key',
            ]),
            on='relation_key',
            how='inner',
        )
        .select(['_source', '_local_relation_pk', 'relation_id', 'relation_key'])
        .unique()
    )
    return combined, relation_id_map


def _build_relation_evidence(
    source_dirs: list[GoldSourceDir],
    relation_id_map: pl.DataFrame,
) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'relations/entity_relation_evidence.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('source').cast(pl.String),
            pl.col('relation_evidence_pk').cast(pl.Int64),
            pl.col('relation_pk').cast(pl.Int64).alias('_local_relation_pk'),
            pl.col('relation_key').cast(pl.String),
            pl.col('raw_record_id').cast(pl.String),
            pl.col('record_attributes'),
            pl.col('subject_attributes'),
            pl.col('object_attributes'),
            pl.col('evidence'),
        ])
        if frame is not None:
            mapped = (
                frame
                .join(
                    relation_id_map.lazy(),
                    on=['_source', '_local_relation_pk'],
                    how='inner',
                )
                .select([
                    pl.col('source'),
                    pl.col('relation_id'),
                    pl.col('relation_key'),
                    pl.col('raw_record_id'),
                    pl.col('record_attributes'),
                    pl.col('subject_attributes'),
                    pl.col('object_attributes'),
                    pl.col('evidence'),
                ])
            )
            frames.append(mapped)

    if not frames:
        return empty_frame(COMBINED_ENTITY_RELATION_EVIDENCE_SCHEMA)

    combined = pl.concat(frames, how='vertical_relaxed').collect()
    return (
        combined
        .sort(['source', 'relation_id', 'raw_record_id'])
        .with_row_index('relation_evidence_id', offset=1)
        .with_columns(pl.col('relation_evidence_id').cast(pl.Int64))
        .select(list(COMBINED_ENTITY_RELATION_EVIDENCE_SCHEMA.keys()))
    )


def _build_entity_evidence(
    source_dirs: list[GoldSourceDir],
    entity_id_map: pl.DataFrame,
) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        path = source_dir.path / 'entities' / 'entity_evidence.parquet'
        if not path.exists():
            continue
        frame = (
            pl.scan_parquet(path)
            .select([
                pl.col('source').cast(pl.String),
                pl.col('entity_key').cast(pl.String),
                pl.col('raw_record_ids'),
                pl.col('entity_type').cast(pl.String),
                pl.col('taxonomy_id').cast(pl.String),
                pl.col('identifiers'),
                pl.col('entity_attributes'),
            ])
        )
        frames.append(frame)

    if not frames:
        return empty_frame(ENTITY_EVIDENCE_SCHEMA)

    combined = pl.concat(frames, how='vertical_relaxed').collect()
    # Join with combined entity_ids
    entity_ids = entity_id_map.select(['entity_key', 'entity_id']).unique()
    return (
        combined
        .join(entity_ids, on='entity_key', how='inner')
        .select([
            'source',
            'entity_key',
            'raw_record_ids',
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
        ])
    )


def _append_build_manifest(
    output_dir: Path,
    *,
    mode: str,
    freeze_monthly: bool,
    row_counts: dict[str, int],
    affected_entities: int = 0,
    affected_relations: int = 0,
    changed_source: str | None = None,
) -> None:
    """Append an entry to the mutable build manifest in ``latest/build_manifest.jsonl``."""
    manifest_path = output_dir / 'build_manifest.jsonl'
    entry = {
        'timestamp': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
        'mode': mode,
        'freeze_monthly': freeze_monthly,
        'changed_source': changed_source,
        'affected_entities': affected_entities,
        'affected_relations': affected_relations,
        'row_counts': row_counts,
    }
    with manifest_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, sort_keys=True) + '\n')


def _freeze_monthly_snapshot(output_dir: Path, source_dir: Path) -> Path:
    """Copy the current ``latest`` directory to an immutable ``YYYY-MM/`` snapshot."""
    snapshot_name = datetime.now(UTC).strftime('%Y-%m')
    snapshot_dir = output_dir / snapshot_name
    if snapshot_dir.exists():
        # If a snapshot for this month already exists, remove it first
        shutil.rmtree(snapshot_dir)
    shutil.copytree(source_dir, snapshot_dir)
    return snapshot_dir


def _read_previous_combined(output_dir: Path) -> dict[str, pl.DataFrame] | None:
    latest = output_dir / 'latest'
    if latest.is_symlink():
        previous_dir = latest.resolve()
    elif latest.is_dir():
        previous_dir = latest
    else:
        return None
    if not previous_dir.exists():
        return None

    result: dict[str, pl.DataFrame] = {}
    for name in [
        'entity.parquet',
        'entity_relation.parquet',
        'entity_relation_evidence.parquet',
        'entity_evidence.parquet',
    ]:
        path = previous_dir / name
        if path.exists():
            result[name] = pl.read_parquet(path)
    return result


def _merge_entities(source_entities: pl.DataFrame) -> pl.DataFrame:
    """Merge per-source entity rows into combined entities."""
    exploded_identifiers = (
        source_entities
        .select([
            'entity_key',
            pl.col('identifiers'),
        ])
        .explode('identifiers')
        .drop_nulls(['identifiers'])
        .select([
            'entity_key',
            pl.col('identifiers').struct.field('identifier').alias('identifier'),
            pl.col('identifiers').struct.field('identifier_type').alias('identifier_type'),
        ])
    )
    canonical_identifier_rows = (
        source_entities
        .select([
            'entity_key',
            pl.col('canonical_identifier').alias('identifier'),
            pl.col('canonical_identifier_type').alias('identifier_type'),
        ])
        .drop_nulls(['identifier', 'identifier_type'])
    )
    all_identifiers = pl.concat([
        exploded_identifiers,
        canonical_identifier_rows,
    ], how='vertical_relaxed').unique()
    identifier_lists = (
        all_identifiers
        .sort(['entity_key', 'identifier_type', 'identifier'])
        .group_by('entity_key')
        .agg([
            pl.struct(['identifier', 'identifier_type']).alias('identifiers'),
        ])
        if not exploded_identifiers.is_empty()
        else pl.DataFrame({
            'entity_key': pl.Series([], dtype=pl.String),
            'identifiers': pl.Series([], dtype=pl.List(IDENTIFIER_STRUCT)),
        })
    )

    return (
        source_entities
        .group_by('entity_key')
        .agg([
            pl.col('canonical_identifier').drop_nulls().first().alias('canonical_identifier'),
            pl.col('canonical_identifier_type').drop_nulls().first().alias('canonical_identifier_type'),
            pl.col('entity_type').drop_nulls().first().alias('entity_type'),
            pl.col('taxonomy_id').drop_nulls().first().alias('taxonomy_id'),
            aggregate_unique_attribute_lists('entity_attributes'),
            aggregate_unique_string_lists('sources'),
        ])
        .join(identifier_lists, on='entity_key', how='left')
        .with_columns([
            pl.when(pl.col('identifiers').is_null())
            .then(EMPTY_IDENTIFIERS)
            .otherwise(pl.col('identifiers'))
            .alias('identifiers'),
        ])
    )


def _recompute_entities(
    source_dirs: list[GoldSourceDir],
    affected_entity_keys: set[str],
) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'entities/entity.parquet', [
            pl.col('entity_key').cast(pl.String),
            pl.col('canonical_identifier').cast(pl.String),
            pl.col('canonical_identifier_type').cast(pl.String),
            pl.col('identifiers'),
            pl.col('entity_type').cast(pl.String),
            pl.col('taxonomy_id').cast(pl.String),
            pl.col('entity_attributes'),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(frame.filter(pl.col('entity_key').is_in(list(affected_entity_keys))))

    if not frames:
        return empty_frame(COMBINED_ENTITY_SCHEMA)

    source_entities = pl.concat(frames, how='vertical_relaxed').collect()
    if source_entities.is_empty():
        return empty_frame(COMBINED_ENTITY_SCHEMA)

    merged = _merge_entities(source_entities)
    # Add placeholder entity_id; caller will assign real IDs
    return (
        merged
        .with_columns(pl.lit(None, dtype=pl.Int64).alias('entity_id'))
        .select(list(COMBINED_ENTITY_SCHEMA.keys()))
    )


def _merge_relations(
    source_relations: pl.DataFrame,
    entity_types: pl.DataFrame,
) -> pl.DataFrame:
    """Merge per-source relation rows into combined relations."""
    return (
        source_relations
        .group_by('relation_key')
        .agg([
            pl.col('subject_entity_id').drop_nulls().first().alias('subject_entity_id'),
            pl.col('subject_entity_key').drop_nulls().first().alias('subject_entity_key'),
            pl.col('predicate').drop_nulls().first().alias('predicate'),
            pl.col('object_entity_id').drop_nulls().first().alias('object_entity_id'),
            pl.col('object_entity_key').drop_nulls().first().alias('object_entity_key'),
            pl.col('relation_category').drop_nulls().first().alias('relation_category'),
            pl.col('evidence_count').sum().cast(pl.Int64).alias('evidence_count'),
            aggregate_unique_string_lists('sources'),
        ])
        .join(
            entity_types.rename({
                'entity_id': 'subject_entity_id',
                'entity_type': '_subject_entity_type',
            }),
            on='subject_entity_id',
            how='left',
        )
        .join(
            entity_types.rename({
                'entity_id': 'object_entity_id',
                'entity_type': '_object_entity_type',
            }),
            on='object_entity_id',
            how='left',
        )
        .with_columns(
            pl.concat_list(['_subject_entity_type', '_object_entity_type'])
            .list.drop_nulls()
            .list.unique()
            .list.sort()
            .alias('participant_types')
        )
        .select([
            'relation_key',
            'subject_entity_id',
            'subject_entity_key',
            'predicate',
            'object_entity_id',
            'object_entity_key',
            'relation_category',
            'participant_types',
            'evidence_count',
            'sources',
        ])
    )


def _recompute_relations(
    source_dirs: list[GoldSourceDir],
    affected_relation_keys: set[str],
    entity_id_map: pl.DataFrame,
) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'relations/entity_relation.parquet', [
            pl.col('relation_key').cast(pl.String),
            pl.col('subject_entity_pk').cast(pl.Int64),
            pl.col('subject_entity_key').cast(pl.String),
            pl.col('predicate').cast(pl.String),
            pl.col('object_entity_pk').cast(pl.Int64),
            pl.col('object_entity_key').cast(pl.String),
            pl.col('relation_category').cast(pl.String),
            pl.col('evidence_count').cast(pl.Int64),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(frame.filter(pl.col('relation_key').is_in(list(affected_relation_keys))))

    if not frames:
        return empty_frame(COMBINED_ENTITY_RELATION_SCHEMA)

    source_relations = pl.concat(frames, how='vertical_relaxed').collect()
    if source_relations.is_empty():
        return empty_frame(COMBINED_ENTITY_RELATION_SCHEMA)

    source_relations = (
        source_relations
        .join(
            entity_id_map.select(['entity_key', 'entity_id']).rename({
                'entity_key': 'subject_entity_key',
                'entity_id': 'subject_entity_id',
            }).unique(),
            on='subject_entity_key',
            how='inner',
        )
        .join(
            entity_id_map.select(['entity_key', 'entity_id']).rename({
                'entity_key': 'object_entity_key',
                'entity_id': 'object_entity_id',
            }).unique(),
            on='object_entity_key',
            how='inner',
        )
    )

    entity_types = (
        entity_id_map
        .join(
            pl.concat([
                _scan_source_artifact(sd, 'entities/entity.parquet', [
                    pl.col('entity_key').cast(pl.String),
                    pl.col('entity_type').cast(pl.String),
                ])
                for sd in source_dirs
            ], how='vertical_relaxed').collect(),
            on='entity_key',
            how='inner',
        )
        .select(['entity_id', 'entity_type'])
        .unique()
    )

    merged = _merge_relations(source_relations, entity_types)
    return (
        merged
        .with_columns(pl.lit(None, dtype=pl.Int64).alias('relation_id'))
        .select(list(COMBINED_ENTITY_RELATION_SCHEMA.keys()))
    )


def _recompute_relation_evidence(
    source_dirs: list[GoldSourceDir],
    affected_relation_keys: set[str],
    relation_id_map: pl.DataFrame,
) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'relations/entity_relation_evidence.parquet', [
            pl.col('source').cast(pl.String),
            pl.col('relation_key').cast(pl.String),
            pl.col('raw_record_id').cast(pl.String),
            pl.col('record_attributes'),
            pl.col('subject_attributes'),
            pl.col('object_attributes'),
            pl.col('evidence'),
        ])
        if frame is not None:
            frames.append(frame.filter(pl.col('relation_key').is_in(list(affected_relation_keys))))

    if not frames:
        return empty_frame(COMBINED_ENTITY_RELATION_EVIDENCE_SCHEMA)

    combined = pl.concat(frames, how='vertical_relaxed').collect()
    if combined.is_empty():
        return empty_frame(COMBINED_ENTITY_RELATION_EVIDENCE_SCHEMA)

    joined = (
        combined
        .join(
            relation_id_map.select(['relation_id', 'relation_key']).unique(),
            on='relation_key',
            how='inner',
        )
        .select([
            'source',
            'relation_id',
            'relation_key',
            'raw_record_id',
            'record_attributes',
            'subject_attributes',
            'object_attributes',
            'evidence',
        ])
    )
    return (
        joined
        .with_columns(pl.lit(None, dtype=pl.Int64).alias('relation_evidence_id'))
        .select(list(COMBINED_ENTITY_RELATION_EVIDENCE_SCHEMA.keys()))
    )


def _write_if_nonempty(frame: pl.DataFrame, path: Path) -> None:
    if frame.is_empty():
        if path.exists():
            path.unlink()
        return
    frame.write_parquet(path)


def _write_combined_outputs(
    outputs: dict[str, pl.DataFrame],
    output_dir: Path,
    gold_root: Path,
    inputs_package: str,
    source_dirs: list[GoldSourceDir],
) -> dict[str, Any]:
    for file_name, frame in outputs.items():
        _write_if_nonempty(frame, output_dir / file_name)

    relation_annotation_summary = build_relation_annotation(output_dir=output_dir)
    ontology_term_path = output_dir / 'ontology_term.parquet'
    if ontology_term_path.exists():
        ontology_term_path.unlink()
    resources_path = build_resources_parquet(
        gold_root=gold_root,
        output_path=output_dir / 'resources.parquet',
        inputs_package=inputs_package,
    )

    row_counts = {
        file_name: int(frame.height)
        for file_name, frame in outputs.items()
    }

    row_counts['relation_annotation_term.parquet'] = int(relation_annotation_summary['row_count'])
    if resources_path.exists():
        row_counts['resources.parquet'] = int(
            pl.scan_parquet(resources_path).select(pl.len()).collect().item()
        )

    summary = {
        'gold_root': str(gold_root),
        'output_dir': str(output_dir),
        'sources': [
            {
                'source': item.source,
                'path': str(item.path),
            }
            for item in source_dirs
        ],
        'row_counts': row_counts,
        'relation_annotation_summary': relation_annotation_summary,
        'resources_path': str(resources_path),
    }
    (output_dir / 'combined_build_summary.json').write_text(
        json.dumps(summary, indent=2) + '\n',
        encoding='utf-8',
    )
    return summary


def build_combined(
    *,
    gold_root: str | Path = 'data/gold',
    output_dir: str | Path = 'data/combined',
    affected_entity_keys: set[str] | None = None,
    affected_relation_keys: set[str] | None = None,
    inputs_package: str = 'pypath.inputs_v2',
    freeze_monthly: bool = False,
    changed_source: str | None = None,
) -> dict[str, Any]:
    """Build combined parquets.

    Reads previous combined state from ``latest/`` under ``output_dir``.
    If previous state exists and affected keys are provided, recomputes only
    the affected keys and merges them with the preserved previous state.
    If no previous state exists, builds everything from scratch.

    Output is always written directly to ``latest/``. To force a full rebuild,
    delete ``latest/`` before running.
    """
    gold_root = Path(gold_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    affected_entity_keys = affected_entity_keys or set()
    affected_relation_keys = affected_relation_keys or set()

    previous = _read_previous_combined(output_dir)
    source_dirs = discover_gold_source_dirs(gold_root)

    if previous is None:
        # No previous state: full build from scratch
        entity_output, entity_id_map = _build_entity(source_dirs)
        relation_output, relation_id_map = _build_relation(source_dirs, entity_id_map)
        outputs = {
            'entity.parquet': entity_output,
            'entity_relation.parquet': relation_output,
            'entity_relation_evidence.parquet': _build_relation_evidence(
                source_dirs, relation_id_map
            ),
            'entity_evidence.parquet': _build_entity_evidence(
                source_dirs, entity_id_map
            ),
        }
        mode = 'full'
        affected_entities_count = 0
        affected_relations_count = 0
    else:
        # Previous state exists: incremental update
        # Entities
        previous_entities = previous.get('entity.parquet', empty_frame(COMBINED_ENTITY_SCHEMA))
        if not affected_entity_keys:
            entity_output = previous_entities
        else:
            unchanged_entities = previous_entities.filter(
                ~pl.col('entity_key').is_in(list(affected_entity_keys))
            )
            recomputed_entities = _recompute_entities(source_dirs, affected_entity_keys)

            if not recomputed_entities.is_empty():
                previous_id_map = dict(zip(
                    previous_entities['entity_key'].to_list(),
                    previous_entities['entity_id'].to_list(),
                ))
                max_prev_id = int(previous_entities['entity_id'].max()) if not previous_entities.is_empty() else 0

                existing_mask = recomputed_entities['entity_key'].is_in(list(previous_id_map.keys()))
                existing_entities = recomputed_entities.filter(existing_mask).with_columns(
                    pl.col('entity_key').map_elements(
                        lambda k: previous_id_map.get(k), return_dtype=pl.Int64
                    ).alias('entity_id')
                )
                new_entities = recomputed_entities.filter(~existing_mask)
                if not new_entities.is_empty():
                    new_entities = new_entities.drop('entity_id').with_row_index(
                        'entity_id', offset=max_prev_id + 1
                    ).with_columns(pl.col('entity_id').cast(pl.Int64))
                    recomputed_entities = pl.concat([existing_entities, new_entities])
                else:
                    recomputed_entities = existing_entities

            entity_output = pl.concat([unchanged_entities, recomputed_entities]).sort('entity_id')

        entity_id_map = entity_output.select(['entity_id', 'entity_key']).unique()

        # Relations
        previous_relations = previous.get('entity_relation.parquet', empty_frame(COMBINED_ENTITY_RELATION_SCHEMA))
        if not affected_relation_keys:
            relation_output = previous_relations
        else:
            unchanged_relations = previous_relations.filter(
                ~pl.col('relation_key').is_in(list(affected_relation_keys))
            )
            recomputed_relations = _recompute_relations(
                source_dirs, affected_relation_keys, entity_id_map
            )

            if not recomputed_relations.is_empty():
                previous_relation_id_map = dict(zip(
                    previous_relations['relation_key'].to_list(),
                    previous_relations['relation_id'].to_list(),
                ))
                max_prev_relation_id = int(previous_relations['relation_id'].max()) if not previous_relations.is_empty() else 0

                existing_mask = recomputed_relations['relation_key'].is_in(list(previous_relation_id_map.keys()))
                existing_relations = recomputed_relations.filter(existing_mask).with_columns(
                    pl.col('relation_key').map_elements(
                        lambda k: previous_relation_id_map.get(k), return_dtype=pl.Int64
                    ).alias('relation_id')
                )
                new_relations = recomputed_relations.filter(~existing_mask)
                if not new_relations.is_empty():
                    new_relations = new_relations.drop('relation_id').with_row_index(
                        'relation_id', offset=max_prev_relation_id + 1
                    ).with_columns(pl.col('relation_id').cast(pl.Int64))
                    recomputed_relations = pl.concat([existing_relations, new_relations])
                else:
                    recomputed_relations = existing_relations

            relation_output = pl.concat([unchanged_relations, recomputed_relations]).sort('relation_id')

        relation_id_map = relation_output.select(['relation_id', 'relation_key']).unique()

        # Relation evidence
        previous_evidence = previous.get(
            'entity_relation_evidence.parquet',
            empty_frame(COMBINED_ENTITY_RELATION_EVIDENCE_SCHEMA)
        )
        if not affected_relation_keys:
            evidence_output = previous_evidence
        else:
            unchanged_evidence = previous_evidence.filter(
                ~pl.col('relation_key').is_in(list(affected_relation_keys))
            )
            recomputed_evidence = _recompute_relation_evidence(
                source_dirs, affected_relation_keys, relation_id_map
            )

            if not recomputed_evidence.is_empty():
                max_prev_evidence_id = int(previous_evidence['relation_evidence_id'].max()) if not previous_evidence.is_empty() else 0
                recomputed_evidence = recomputed_evidence.drop('relation_evidence_id').with_row_index(
                    'relation_evidence_id', offset=max_prev_evidence_id + 1
                ).with_columns(pl.col('relation_evidence_id').cast(pl.Int64))

            evidence_output = pl.concat([unchanged_evidence, recomputed_evidence]).sort('relation_evidence_id')

        # Entity evidence - full rebuild (cheap concatenation)
        entity_evidence_output = _build_entity_evidence(source_dirs, entity_id_map)

        outputs = {
            'entity.parquet': entity_output,
            'entity_relation.parquet': relation_output,
            'entity_relation_evidence.parquet': evidence_output,
            'entity_evidence.parquet': entity_evidence_output,
        }
        mode = 'incremental'
        affected_entities_count = len(affected_entity_keys)
        affected_relations_count = len(affected_relation_keys)

    version_dir = output_dir / 'latest'
    version_dir.mkdir(parents=True, exist_ok=True)

    summary = _write_combined_outputs(
        outputs, version_dir, gold_root, inputs_package, source_dirs
    )

    _append_build_manifest(
        version_dir,
        mode=mode,
        freeze_monthly=freeze_monthly,
        row_counts=summary['row_counts'],
        affected_entities=affected_entities_count,
        affected_relations=affected_relations_count,
        changed_source=changed_source,
    )

    if freeze_monthly:
        snapshot_dir = _freeze_monthly_snapshot(output_dir, version_dir)
        summary['monthly_snapshot'] = str(snapshot_dir)

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build combined warehouse parquet artifacts from per-source gold outputs.',
    )
    parser.add_argument(
        '--gold-root',
        type=Path,
        default=Path('data/gold'),
        help='Root directory containing per-source gold outputs (default: data/gold)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data/combined'),
        help='Directory to write combined parquet artifacts (default: data/combined)',
    )
    parser.add_argument(
        '--inputs-package',
        type=str,
        default='pypath.inputs_v2',
        help='Python package containing resource definitions for resources.parquet metadata.',
    )
    parser.add_argument(
        '--affected-entities',
        type=Path,
        default=None,
        help='Path to JSON file with list of affected entity_keys.',
    )
    parser.add_argument(
        '--affected-relations',
        type=Path,
        default=None,
        help='Path to JSON file with list of affected relation_keys.',
    )
    parser.add_argument(
        '--freeze-monthly',
        action='store_true',
        help=(
            'After writing, copy the latest/ directory to an immutable '
            'YYYY-MM/ snapshot. Useful for creating monthly baselines.'
        ),
    )
    parser.add_argument(
        '--changed-source',
        type=str,
        default=None,
        help='Name of the source that changed (for build manifest).',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    affected_entity_keys: set[str] | None = None
    affected_relation_keys: set[str] | None = None
    if args.affected_entities is not None:
        affected_entity_keys = set(json.loads(args.affected_entities.read_text()))
    if args.affected_relations is not None:
        affected_relation_keys = set(json.loads(args.affected_relations.read_text()))

    build_combined(
        gold_root=args.gold_root,
        output_dir=args.output_dir,
        affected_entity_keys=affected_entity_keys,
        affected_relation_keys=affected_relation_keys,
        inputs_package=args.inputs_package,
        freeze_monthly=args.freeze_monthly,
        changed_source=args.changed_source,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
