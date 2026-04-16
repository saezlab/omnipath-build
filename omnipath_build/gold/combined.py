from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.gold.schema import (
    ARTIFACT_OUTPUTS,
    ASSOCIATION_EVIDENCE_SCHEMA,
    ASSOCIATION_SCHEMA,
    EMPTY_IDENTIFIERS,
    ENTITY_ANNOTATION_SCHEMA,
    ENTITY_SCHEMA,
    IDENTIFIER_STRUCT,
    INTERACTION_ANNOTATION_SCHEMA,
    INTERACTION_EVIDENCE_SCHEMA,
    INTERACTION_SCHEMA,
    aggregate_unique_string_lists,
    empty_frame,
)


@dataclass(frozen=True)
class GoldSourceDir:
    source: str
    version: str
    path: Path


def discover_gold_source_dirs(gold_root: str | Path) -> list[GoldSourceDir]:
    root = Path(gold_root)
    if not root.exists():
        raise FileNotFoundError(f'Gold root does not exist: {root}')

    latest: dict[str, GoldSourceDir] = {}
    for artifact_path in root.rglob('entity.parquet'):
        version_dir = artifact_path.parent
        if version_dir == root or not version_dir.name.isdigit():
            continue
        source_rel = version_dir.parent.relative_to(root)
        source = source_rel.as_posix().replace('/', '.')
        candidate = GoldSourceDir(source=source, version=version_dir.name, path=version_dir)
        current = latest.get(source)
        if current is None or int(candidate.version) > int(current.version):
            latest[source] = candidate

    return sorted(latest.values(), key=lambda item: item.source)


def _scan_source_artifact(source_dir: GoldSourceDir, file_name: str, columns: list[pl.Expr | str]) -> pl.LazyFrame | None:
    path = source_dir.path / file_name
    if not path.exists():
        return None
    return pl.scan_parquet(path).select(columns)


def build_combined_parquets(
    *,
    gold_root: str | Path = 'data_v2/gold',
    output_dir: str | Path = 'data_v2/combined',
) -> dict[str, Any]:
    gold_root = Path(gold_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_dirs = discover_gold_source_dirs(gold_root)
    entity_output, entity_pk_map = _build_entity(source_dirs)
    interaction_output, interaction_pk_map = _build_interaction(source_dirs, entity_pk_map)
    association_output, association_pk_map = _build_association(source_dirs, entity_pk_map)

    outputs = {
        'entity.parquet': entity_output,
        'interaction_evidence.parquet': _build_interaction_evidence(source_dirs, interaction_pk_map),
        'association_evidence.parquet': _build_association_evidence(source_dirs, association_pk_map),
        'interaction.parquet': interaction_output,
        'association.parquet': association_output,
        'entity_annotation.parquet': _build_entity_annotation(source_dirs, entity_pk_map),
        'interaction_annotation.parquet': _build_interaction_annotation(source_dirs, interaction_pk_map),
    }

    for stale_name in (
        'entity_identifiers.parquet',
        'entity_identifier.parquet',
        'entity_annotation_evidence.parquet',
        'interaction_annotation_evidence.parquet',
    ):
        stale_path = output_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()

    for file_name, frame in outputs.items():
        _write_if_nonempty(frame, output_dir / file_name)

    summary = {
        'gold_root': str(gold_root),
        'output_dir': str(output_dir),
        'sources': [
            {
                'source': item.source,
                'version': item.version,
                'path': str(item.path),
            }
            for item in source_dirs
        ],
        'row_counts': {
            file_name: int(frame.height)
            for file_name, frame in outputs.items()
        },
    }
    (output_dir / 'combined_build_summary.json').write_text(
        json.dumps(summary, indent=2) + '\n',
        encoding='utf-8',
    )
    return summary


def _build_entity(source_dirs: list[GoldSourceDir]) -> tuple[pl.DataFrame, pl.DataFrame]:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'entity.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('entity_pk').cast(pl.Int64).alias('_local_entity_pk'),
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
        return empty_frame(ENTITY_SCHEMA), pl.DataFrame({
            '_source': pl.Series([], dtype=pl.String),
            '_local_entity_pk': pl.Series([], dtype=pl.Int64),
            'entity_pk': pl.Series([], dtype=pl.Int64),
        })

    source_entities = pl.concat(frames, how='vertical_relaxed').collect()
    exploded_identifiers = (
        source_entities
        .select([
            'canonical_identifier',
            'canonical_identifier_type',
            pl.col('identifiers'),
        ])
        .explode('identifiers')
        .drop_nulls(['identifiers'])
        .select([
            'canonical_identifier',
            'canonical_identifier_type',
            pl.col('identifiers').struct.field('identifier').alias('identifier'),
            pl.col('identifiers').struct.field('identifier_type').alias('identifier_type'),
        ])
        .unique()
    )
    identifier_lists = (
        exploded_identifiers
        .sort(['canonical_identifier_type', 'canonical_identifier', 'identifier_type', 'identifier'])
        .group_by(['canonical_identifier', 'canonical_identifier_type'])
        .agg([
            pl.struct(['identifier', 'identifier_type']).alias('identifiers'),
        ])
        if not exploded_identifiers.is_empty() else
        pl.DataFrame({
            'canonical_identifier': pl.Series([], dtype=pl.String),
            'canonical_identifier_type': pl.Series([], dtype=pl.String),
            'identifiers': pl.Series([], dtype=pl.List(IDENTIFIER_STRUCT)),
        })
    )

    combined = (
        source_entities
        .group_by(['canonical_identifier', 'canonical_identifier_type'])
        .agg([
            pl.col('entity_type').drop_nulls().first().alias('entity_type'),
            pl.col('taxonomy_id').drop_nulls().first().alias('taxonomy_id'),
            pl.col('entity_attributes').drop_nulls().first().alias('entity_attributes'),
            aggregate_unique_string_lists('sources'),
        ])
        .join(identifier_lists, on=['canonical_identifier', 'canonical_identifier_type'], how='left')
        .sort(['canonical_identifier_type', 'canonical_identifier'])
        .with_row_index('entity_pk', offset=1)
        .with_columns([
            pl.col('entity_pk').cast(pl.Int64),
            pl.when(pl.col('identifiers').is_null())
            .then(EMPTY_IDENTIFIERS)
            .otherwise(pl.col('identifiers'))
            .alias('identifiers'),
        ])
        .select(list(ENTITY_SCHEMA.keys()))
    )

    entity_pk_map = (
        source_entities
        .join(
            combined.select(['entity_pk', 'canonical_identifier', 'canonical_identifier_type']),
            on=['canonical_identifier', 'canonical_identifier_type'],
            how='inner',
        )
        .select(['_source', '_local_entity_pk', 'entity_pk'])
        .unique()
    )
    return combined, entity_pk_map


def _build_interaction(
    source_dirs: list[GoldSourceDir],
    entity_pk_map: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'interaction.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('interaction_pk').cast(pl.Int64).alias('_local_interaction_pk'),
            pl.col('entity_a_pk').cast(pl.Int64),
            pl.col('entity_b_pk').cast(pl.Int64),
            pl.col('direction').cast(pl.Int64, strict=False),
            pl.col('sign').cast(pl.Int64, strict=False),
            pl.col('evidence_count').cast(pl.Int64),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(frame)

    if not frames:
        return empty_frame(INTERACTION_SCHEMA), pl.DataFrame({
            '_source': pl.Series([], dtype=pl.String),
            '_local_interaction_pk': pl.Series([], dtype=pl.Int64),
            'interaction_pk': pl.Series([], dtype=pl.Int64),
        })

    source_interactions = (
        pl.concat(frames, how='vertical_relaxed').collect()
        .join(
            entity_pk_map.rename({'_local_entity_pk': 'entity_a_pk', 'entity_pk': '_global_entity_a_pk'}),
            on=['_source', 'entity_a_pk'],
            how='inner',
        )
        .join(
            entity_pk_map.rename({'_local_entity_pk': 'entity_b_pk', 'entity_pk': '_global_entity_b_pk'}),
            on=['_source', 'entity_b_pk'],
            how='inner',
        )
    )

    combined = (
        source_interactions
        .group_by(['_global_entity_a_pk', '_global_entity_b_pk', 'direction', 'sign'])
        .agg([
            pl.col('evidence_count').sum().cast(pl.Int64).alias('evidence_count'),
            aggregate_unique_string_lists('sources'),
        ])
        .sort(['_global_entity_a_pk', '_global_entity_b_pk', 'direction', 'sign'])
        .with_row_index('interaction_pk', offset=1)
        .with_columns(pl.col('interaction_pk').cast(pl.Int64))
        .rename({
            '_global_entity_a_pk': 'entity_a_pk',
            '_global_entity_b_pk': 'entity_b_pk',
        })
        .select(list(INTERACTION_SCHEMA.keys()))
    )

    interaction_pk_map = (
        source_interactions
        .join(
            combined.select(['interaction_pk', 'entity_a_pk', 'entity_b_pk', 'direction', 'sign']),
            left_on=['_global_entity_a_pk', '_global_entity_b_pk', 'direction', 'sign'],
            right_on=['entity_a_pk', 'entity_b_pk', 'direction', 'sign'],
            how='inner',
        )
        .select(['_source', '_local_interaction_pk', 'interaction_pk'])
        .unique()
    )
    return combined, interaction_pk_map


def _build_association(
    source_dirs: list[GoldSourceDir],
    entity_pk_map: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'association.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('association_pk').cast(pl.Int64).alias('_local_association_pk'),
            pl.col('parent_entity_pk').cast(pl.Int64),
            pl.col('member_entity_pk').cast(pl.Int64),
            pl.col('role_term_id').cast(pl.String),
            pl.col('stoichiometry').cast(pl.String),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(frame)

    if not frames:
        return empty_frame(ASSOCIATION_SCHEMA), pl.DataFrame({
            '_source': pl.Series([], dtype=pl.String),
            '_local_association_pk': pl.Series([], dtype=pl.Int64),
            'association_pk': pl.Series([], dtype=pl.Int64),
        })

    source_associations = (
        pl.concat(frames, how='vertical_relaxed').collect()
        .join(
            entity_pk_map.rename({'_local_entity_pk': 'parent_entity_pk', 'entity_pk': '_global_parent_entity_pk'}),
            on=['_source', 'parent_entity_pk'],
            how='inner',
        )
        .join(
            entity_pk_map.rename({'_local_entity_pk': 'member_entity_pk', 'entity_pk': '_global_member_entity_pk'}),
            on=['_source', 'member_entity_pk'],
            how='inner',
        )
    )

    combined = (
        source_associations
        .group_by(['_global_parent_entity_pk', '_global_member_entity_pk', 'role_term_id', 'stoichiometry'])
        .agg([
            aggregate_unique_string_lists('sources'),
        ])
        .sort(['_global_parent_entity_pk', '_global_member_entity_pk', 'role_term_id', 'stoichiometry'])
        .with_row_index('association_pk', offset=1)
        .with_columns(pl.col('association_pk').cast(pl.Int64))
        .rename({
            '_global_parent_entity_pk': 'parent_entity_pk',
            '_global_member_entity_pk': 'member_entity_pk',
        })
        .select(list(ASSOCIATION_SCHEMA.keys()))
    )

    association_pk_map = (
        source_associations
        .join(
            combined.select(['association_pk', 'parent_entity_pk', 'member_entity_pk', 'role_term_id', 'stoichiometry']),
            left_on=['_global_parent_entity_pk', '_global_member_entity_pk', 'role_term_id', 'stoichiometry'],
            right_on=['parent_entity_pk', 'member_entity_pk', 'role_term_id', 'stoichiometry'],
            how='inner',
        )
        .select(['_source', '_local_association_pk', 'association_pk'])
        .unique()
    )
    return combined, association_pk_map


def _build_interaction_evidence(source_dirs: list[GoldSourceDir], interaction_pk_map: pl.DataFrame) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'interaction_evidence.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('source').cast(pl.String),
            pl.col('interaction_pk').cast(pl.Int64).alias('_local_interaction_pk'),
            pl.col('direction').cast(pl.Int64, strict=False),
            pl.col('sign').cast(pl.Int64, strict=False),
            pl.col('record_attributes'),
            pl.col('entity_a_attributes'),
            pl.col('entity_b_attributes'),
            pl.col('evidence'),
        ])
        if frame is not None:
            frames.append(
                frame
                .join(interaction_pk_map.lazy(), on=['_source', '_local_interaction_pk'], how='inner')
                .select(list(INTERACTION_EVIDENCE_SCHEMA.keys()))
            )

    if not frames:
        return empty_frame(INTERACTION_EVIDENCE_SCHEMA)
    return pl.concat(frames, how='vertical_relaxed').unique().sort(['source', 'interaction_pk']).collect()


def _build_association_evidence(source_dirs: list[GoldSourceDir], association_pk_map: pl.DataFrame) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'association_evidence.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('source').cast(pl.String),
            pl.col('association_pk').cast(pl.Int64).alias('_local_association_pk'),
            pl.col('role_term_id').cast(pl.String),
            pl.col('stoichiometry').cast(pl.String),
            pl.col('record_attributes'),
            pl.col('parent_attributes'),
            pl.col('member_attributes'),
            pl.col('evidence'),
        ])
        if frame is not None:
            frames.append(
                frame
                .join(association_pk_map.lazy(), on=['_source', '_local_association_pk'], how='inner')
                .select(list(ASSOCIATION_EVIDENCE_SCHEMA.keys()))
            )

    if not frames:
        return empty_frame(ASSOCIATION_EVIDENCE_SCHEMA)
    return pl.concat(frames, how='vertical_relaxed').unique().sort(['source', 'association_pk']).collect()


def _build_entity_annotation(source_dirs: list[GoldSourceDir], entity_pk_map: pl.DataFrame) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'entity_annotation.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('entity_pk').cast(pl.Int64).alias('_local_entity_pk'),
            pl.col('cv_term').cast(pl.String),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(
                frame
                .join(entity_pk_map.lazy(), on=['_source', '_local_entity_pk'], how='inner')
                .select(['entity_pk', 'cv_term', 'sources'])
            )

    if not frames:
        return empty_frame(ENTITY_ANNOTATION_SCHEMA)
    return (
        pl.concat(frames, how='vertical_relaxed')
        .group_by(['entity_pk', 'cv_term'])
        .agg([
            aggregate_unique_string_lists('sources'),
        ])
        .select(list(ENTITY_ANNOTATION_SCHEMA.keys()))
        .sort(['entity_pk', 'cv_term'])
        .collect()
    )


def _build_interaction_annotation(source_dirs: list[GoldSourceDir], interaction_pk_map: pl.DataFrame) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'interaction_annotation.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('interaction_pk').cast(pl.Int64).alias('_local_interaction_pk'),
            pl.col('cv_term').cast(pl.String),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(
                frame
                .join(interaction_pk_map.lazy(), on=['_source', '_local_interaction_pk'], how='inner')
                .select(['interaction_pk', 'cv_term', 'sources'])
            )

    if not frames:
        return empty_frame(INTERACTION_ANNOTATION_SCHEMA)
    return (
        pl.concat(frames, how='vertical_relaxed')
        .group_by(['interaction_pk', 'cv_term'])
        .agg([
            aggregate_unique_string_lists('sources'),
        ])
        .select(list(INTERACTION_ANNOTATION_SCHEMA.keys()))
        .sort(['interaction_pk', 'cv_term'])
        .collect()
    )


def _write_if_nonempty(frame: pl.DataFrame, path: Path) -> None:
    if frame.is_empty():
        if path.exists():
            path.unlink()
        return
    frame.write_parquet(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build combined warehouse parquet artifacts from per-source gold outputs.',
    )
    parser.add_argument(
        '--gold-root',
        type=Path,
        default=Path('data_v2/gold'),
        help='Root directory containing per-source gold outputs (default: data_v2/gold)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data_v2/combined'),
        help='Directory to write combined parquet artifacts (default: data_v2/combined)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_combined_parquets(gold_root=args.gold_root, output_dir=args.output_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
