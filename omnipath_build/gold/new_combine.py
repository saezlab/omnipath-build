from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.gold.schema import (
    EMPTY_IDENTIFIERS,
    ENTITY_RELATION_EVIDENCE_SCHEMA,
    ENTITY_RELATION_SCHEMA,
    ENTITY_SCHEMA,
    IDENTIFIER_STRUCT,
    ONTOLOGY_TERM_SCHEMA,
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
    )
    canonical_identifier_rows = (
        source_entities
        .select([
            'canonical_identifier',
            'canonical_identifier_type',
            pl.col('canonical_identifier').alias('identifier'),
            pl.col('canonical_identifier_type').alias('identifier_type'),
        ])
        .drop_nulls(['canonical_identifier', 'canonical_identifier_type'])
    )
    all_identifiers = pl.concat([
        exploded_identifiers,
        canonical_identifier_rows,
    ], how='vertical_relaxed').unique()
    identifier_lists = (
        all_identifiers
        .sort(['canonical_identifier_type', 'canonical_identifier', 'identifier_type', 'identifier'])
        .group_by(['canonical_identifier', 'canonical_identifier_type'])
        .agg([
            pl.struct(['identifier', 'identifier_type']).alias('identifiers'),
        ])
        if not exploded_identifiers.is_empty()
        else pl.DataFrame({
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


def _build_relation(
    source_dirs: list[GoldSourceDir],
    entity_pk_map: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'relations/entity_relation.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('relation_pk').cast(pl.Int64).alias('_local_relation_pk'),
            pl.col('subject_entity_pk').cast(pl.Int64),
            pl.col('predicate').cast(pl.String),
            pl.col('object_entity_pk').cast(pl.Int64),
            pl.col('relation_category').cast(pl.String),
            pl.col('evidence_count').cast(pl.Int64),
            pl.col('sources'),
        ])
        if frame is not None:
            frames.append(frame)

    if not frames:
        return empty_frame(ENTITY_RELATION_SCHEMA), pl.DataFrame({
            '_source': pl.Series([], dtype=pl.String),
            '_local_relation_pk': pl.Series([], dtype=pl.Int64),
            'relation_pk': pl.Series([], dtype=pl.Int64),
        })

    source_relations = (
        pl.concat(frames, how='vertical_relaxed').collect()
        .join(
            entity_pk_map.rename({
                '_local_entity_pk': 'subject_entity_pk',
                'entity_pk': '_global_subject_pk',
            }),
            on=['_source', 'subject_entity_pk'],
            how='inner',
        )
        .join(
            entity_pk_map.rename({
                '_local_entity_pk': 'object_entity_pk',
                'entity_pk': '_global_object_pk',
            }),
            on=['_source', 'object_entity_pk'],
            how='inner',
        )
    )

    combined = (
        source_relations
        .group_by(['_global_subject_pk', 'predicate', '_global_object_pk', 'relation_category'])
        .agg([
            pl.col('evidence_count').sum().cast(pl.Int64).alias('evidence_count'),
            aggregate_unique_string_lists('sources'),
        ])
        .sort(['_global_subject_pk', 'predicate', '_global_object_pk', 'relation_category'])
        .with_row_index('relation_pk', offset=1)
        .with_columns(pl.col('relation_pk').cast(pl.Int64))
        .rename({
            '_global_subject_pk': 'subject_entity_pk',
            '_global_object_pk': 'object_entity_pk',
        })
        .select(list(ENTITY_RELATION_SCHEMA.keys()))
    )

    relation_pk_map = (
        source_relations
        .join(
            combined.select([
                'relation_pk',
                'subject_entity_pk',
                'predicate',
                'object_entity_pk',
                'relation_category',
            ]),
            left_on=['_global_subject_pk', 'predicate', '_global_object_pk', 'relation_category'],
            right_on=['subject_entity_pk', 'predicate', 'object_entity_pk', 'relation_category'],
            how='inner',
        )
        .select(['_source', '_local_relation_pk', 'relation_pk'])
        .unique()
    )
    return combined, relation_pk_map


def _build_relation_evidence(
    source_dirs: list[GoldSourceDir],
    entity_pk_map: pl.DataFrame,
    relation_pk_map: pl.DataFrame,
) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'relations/entity_relation_evidence.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('source').cast(pl.String),
            pl.col('relation_evidence_pk').cast(pl.Int64),
            pl.col('relation_pk').cast(pl.Int64).alias('_local_relation_pk'),
            pl.col('subject_entity_pk').cast(pl.Int64),
            pl.col('predicate').cast(pl.String),
            pl.col('object_entity_pk').cast(pl.Int64),
            pl.col('relation_category').cast(pl.String),
            pl.col('record_attributes'),
            pl.col('subject_attributes'),
            pl.col('object_attributes'),
            pl.col('evidence'),
        ])
        if frame is not None:
            mapped = (
                frame
                .join(
                    entity_pk_map.rename({
                        '_local_entity_pk': 'subject_entity_pk',
                        'entity_pk': '_global_subject_pk',
                    }).lazy(),
                    on=['_source', 'subject_entity_pk'],
                    how='inner',
                )
                .join(
                    entity_pk_map.rename({
                        '_local_entity_pk': 'object_entity_pk',
                        'entity_pk': '_global_object_pk',
                    }).lazy(),
                    on=['_source', 'object_entity_pk'],
                    how='inner',
                )
                .join(
                    relation_pk_map.lazy(),
                    on=['_source', '_local_relation_pk'],
                    how='inner',
                )
                .select([
                    pl.col('source'),
                    pl.col('relation_evidence_pk'),
                    pl.col('relation_pk'),
                    pl.col('_global_subject_pk').alias('subject_entity_pk'),
                    pl.col('predicate'),
                    pl.col('_global_object_pk').alias('object_entity_pk'),
                    pl.col('relation_category'),
                    pl.col('record_attributes'),
                    pl.col('subject_attributes'),
                    pl.col('object_attributes'),
                    pl.col('evidence'),
                ])
            )
            frames.append(mapped)

    if not frames:
        return empty_frame(ENTITY_RELATION_EVIDENCE_SCHEMA)

    combined = pl.concat(frames, how='vertical_relaxed').collect()
    deduped = (
        combined
        .drop('relation_evidence_pk')
        .unique()
        .sort([
            'source',
            'relation_pk',
            'subject_entity_pk',
            'predicate',
            'object_entity_pk',
            'relation_category',
        ])
        .with_row_index('relation_evidence_pk', offset=1)
        .with_columns(pl.col('relation_evidence_pk').cast(pl.Int64))
    )
    return deduped.select(list(ENTITY_RELATION_EVIDENCE_SCHEMA.keys()))


def _build_ontology_terms(source_dirs: list[GoldSourceDir]) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'entities/ontology_term.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('term_id').cast(pl.String),
            pl.col('ontology_prefix').cast(pl.String),
            pl.col('label').cast(pl.String),
            pl.col('definition').cast(pl.String),
            pl.col('synonyms'),
        ])
        if frame is not None:
            frames.append(frame)

    if not frames:
        return empty_frame(ONTOLOGY_TERM_SCHEMA)

    source_terms = pl.concat(frames, how='vertical_relaxed').collect()
    combined = (
        source_terms
        .group_by('term_id')
        .agg([
            pl.col('ontology_prefix').drop_nulls().first().alias('ontology_prefix'),
            pl.col('label').drop_nulls().first().alias('label'),
            pl.col('definition').drop_nulls().first().alias('definition'),
            aggregate_unique_string_lists('synonyms'),
            aggregate_unique_string_lists('_source').alias('sources'),
        ])
        .select(list(ONTOLOGY_TERM_SCHEMA.keys()))
        .sort('term_id')
    )
    return combined


def _write_if_nonempty(frame: pl.DataFrame, path: Path) -> None:
    if frame.is_empty():
        if path.exists():
            path.unlink()
        return
    frame.write_parquet(path)


def build_combined_parquets(
    *,
    gold_root: str | Path = 'data_v2/gold_new',
    output_dir: str | Path = 'data_v2/combined_new',
) -> dict[str, Any]:
    gold_root = Path(gold_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_dirs = discover_gold_source_dirs(gold_root)

    entity_output, entity_pk_map = _build_entity(source_dirs)
    relation_output, relation_pk_map = _build_relation(source_dirs, entity_pk_map)

    outputs = {
        'entity.parquet': entity_output,
        'entity_relation.parquet': relation_output,
        'entity_relation_evidence.parquet': _build_relation_evidence(
            source_dirs, entity_pk_map, relation_pk_map
        ),
        'ontology_term.parquet': _build_ontology_terms(source_dirs),
    }

    for file_name, frame in outputs.items():
        _write_if_nonempty(frame, output_dir / file_name)

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build combined warehouse parquet artifacts from B3 per-source gold outputs.',
    )
    parser.add_argument(
        '--gold-root',
        type=Path,
        default=Path('data_v2/gold_new'),
        help='Root directory containing per-source gold outputs (default: data_v2/gold_new)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data_v2/combined_new'),
        help='Directory to write combined parquet artifacts (default: data_v2/combined_new)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_combined_parquets(gold_root=args.gold_root, output_dir=args.output_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
