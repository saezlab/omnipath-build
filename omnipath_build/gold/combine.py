from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.gold.build_relation_annotation import build_relation_annotation
from omnipath_build.gold.build_resources import build_resources_parquet
from omnipath_build.gold.utils.canonicalization import (
    ONTOLOGY_ENTITY_TYPE_LABEL,
    ONTOLOGY_IDENTIFIER_TYPE_LABEL,
)
from omnipath_build.gold.utils.cv_terms import format_cv_term
from omnipath_build.gold.utils.table_schema import (
    ATTRIBUTE_STRUCT,
    EMPTY_IDENTIFIERS,
    ENTITY_RELATION_EVIDENCE_SCHEMA,
    ENTITY_RELATION_SCHEMA,
    ENTITY_SCHEMA,
    IDENTIFIER_STRUCT,
    ONTOLOGY_TERM_SCHEMA,
    aggregate_unique_attribute_lists,
    aggregate_unique_string_lists,
    empty_frame,
)
from pypath.internals.cv_terms import IdentifierNamespaceCv, OntologyAnnotationCv


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


def _empty_ontology_terms() -> pl.DataFrame:
    return empty_frame(ONTOLOGY_TERM_SCHEMA)


def _read_ontology_terms(path: Path) -> pl.DataFrame | None:
    if not path.exists():
        return None
    return pl.read_parquet(path).select(list(ONTOLOGY_TERM_SCHEMA.keys()))


def _build_ontology_terms(
    source_dirs: list[GoldSourceDir],
    output_dir: Path,
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    combined_path = output_dir / 'ontology_term.parquet'
    combined_terms = _read_ontology_terms(combined_path)
    if combined_terms is not None:
        frames.append(combined_terms)

    for source_dir in source_dirs:
        source_terms = _read_ontology_terms(source_dir.path / 'entities' / 'ontology_term.parquet')
        if source_terms is not None:
            frames.append(source_terms)

    if not frames:
        return _empty_ontology_terms()

    return (
        pl.concat(frames, how='vertical_relaxed')
        .group_by('term_id')
        .agg([
            pl.col('ontology_prefix').drop_nulls().first().alias('ontology_prefix'),
            pl.col('label').drop_nulls().first().alias('label'),
            pl.col('definition').drop_nulls().first().alias('definition'),
            aggregate_unique_string_lists('synonyms'),
            aggregate_unique_string_lists('sources'),
        ])
        .select(list(ONTOLOGY_TERM_SCHEMA.keys()))
        .sort('term_id')
    )


def _ontology_entity_rows(ontology_terms: pl.DataFrame) -> pl.DataFrame:
    if ontology_terms.is_empty():
        return pl.DataFrame({
            '_source': pl.Series([], dtype=pl.String),
            '_local_entity_pk': pl.Series([], dtype=pl.Int64),
            'canonical_identifier': pl.Series([], dtype=pl.String),
            'canonical_identifier_type': pl.Series([], dtype=pl.String),
            'identifiers': pl.Series([], dtype=pl.List(IDENTIFIER_STRUCT)),
            'entity_type': pl.Series([], dtype=pl.String),
            'taxonomy_id': pl.Series([], dtype=pl.String),
            'entity_attributes': pl.Series([], dtype=ATTRIBUTE_STRUCT),
            'sources': pl.Series([], dtype=pl.List(pl.String)),
        })

    name_type = format_cv_term(str(IdentifierNamespaceCv.NAME))
    synonym_type = format_cv_term(str(IdentifierNamespaceCv.SYNONYM))
    definition_term = format_cv_term(str(OntologyAnnotationCv.DEFINITION))
    obsolete_term = format_cv_term(str(OntologyAnnotationCv.IS_OBSOLETE))

    rows: list[dict[str, Any]] = []
    for term in ontology_terms.to_dicts():
        term_id = term.get('term_id')
        if not term_id:
            continue
        identifiers = [
            {
                'identifier': str(term_id),
                'identifier_type': ONTOLOGY_IDENTIFIER_TYPE_LABEL,
            },
        ]
        label = term.get('label')
        if label:
            identifiers.append({'identifier': str(label), 'identifier_type': name_type})
        for synonym in term.get('synonyms') or []:
            if synonym:
                identifiers.append({'identifier': str(synonym), 'identifier_type': synonym_type})

        attributes = []
        if label:
            attributes.append({'term': name_type, 'value': str(label), 'unit': None})
        if term.get('definition'):
            attributes.append({'term': definition_term, 'value': str(term['definition']), 'unit': None})
        for synonym in term.get('synonyms') or []:
            if synonym:
                attributes.append({'term': synonym_type, 'value': str(synonym), 'unit': None})
        if term.get('ontology_prefix'):
            attributes.append({'term': 'ontology_prefix', 'value': str(term['ontology_prefix']).lower(), 'unit': None})
        if str(term_id).endswith(':obsolete'):
            attributes.append({'term': obsolete_term, 'value': 'true', 'unit': None})

        sources = term.get('sources') or []
        if not sources and term.get('ontology_prefix'):
            sources = [str(term['ontology_prefix']).lower()]

        rows.append({
            '_source': None,
            '_local_entity_pk': None,
            'canonical_identifier': str(term_id),
            'canonical_identifier_type': ONTOLOGY_IDENTIFIER_TYPE_LABEL,
            'identifiers': identifiers,
            'entity_type': ONTOLOGY_ENTITY_TYPE_LABEL,
            'taxonomy_id': None,
            'entity_attributes': attributes,
            'sources': sorted({str(source) for source in sources if source}),
        })

    return pl.DataFrame(rows).select([
        '_source',
        '_local_entity_pk',
        'canonical_identifier',
        'canonical_identifier_type',
        'identifiers',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
    ])


def _build_entity(
    source_dirs: list[GoldSourceDir],
    ontology_terms: pl.DataFrame,
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
    ontology_entities = _ontology_entity_rows(ontology_terms)
    if not ontology_entities.is_empty():
        source_entities = pl.concat([source_entities, ontology_entities], how='vertical_relaxed')
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
            aggregate_unique_attribute_lists('entity_attributes'),
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
        .filter(pl.col('_source').is_not_null() & pl.col('_local_entity_pk').is_not_null())
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
    entity_output: pl.DataFrame,
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

    entity_types = entity_output.select([
        pl.col('entity_pk').cast(pl.Int64),
        pl.col('entity_type').cast(pl.String),
    ])

    combined = (
        source_relations
        .group_by(['_global_subject_pk', 'predicate', '_global_object_pk', 'relation_category'])
        .agg([
            pl.col('evidence_count').sum().cast(pl.Int64).alias('evidence_count'),
            aggregate_unique_string_lists('sources'),
        ])
        .join(
            entity_types.rename({
                'entity_pk': '_global_subject_pk',
                'entity_type': '_subject_entity_type',
            }),
            on='_global_subject_pk',
            how='left',
        )
        .join(
            entity_types.rename({
                'entity_pk': '_global_object_pk',
                'entity_type': '_object_entity_type',
            }),
            on='_global_object_pk',
            how='left',
        )
        .with_columns(
            pl.concat_list(['_subject_entity_type', '_object_entity_type'])
            .list.drop_nulls()
            .list.unique()
            .list.sort()
            .alias('participant_types')
        )
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
    relation_pk_map: pl.DataFrame,
) -> pl.DataFrame:
    frames: list[pl.LazyFrame] = []
    for source_dir in source_dirs:
        frame = _scan_source_artifact(source_dir, 'relations/entity_relation_evidence.parquet', [
            pl.lit(source_dir.source).alias('_source'),
            pl.col('source').cast(pl.String),
            pl.col('relation_evidence_pk').cast(pl.Int64),
            pl.col('relation_pk').cast(pl.Int64).alias('_local_relation_pk'),
            pl.col('record_attributes'),
            pl.col('subject_attributes'),
            pl.col('object_attributes'),
            pl.col('evidence'),
        ])
        if frame is not None:
            mapped = (
                frame
                .join(
                    relation_pk_map.lazy(),
                    on=['_source', '_local_relation_pk'],
                    how='inner',
                )
                .select([
                    pl.col('source'),
                    pl.col('relation_evidence_pk'),
                    pl.col('relation_pk'),
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
        .sort(['source', 'relation_pk'])
        .with_row_index('relation_evidence_pk', offset=1)
        .with_columns(pl.col('relation_evidence_pk').cast(pl.Int64))
    )
    return deduped.select(list(ENTITY_RELATION_EVIDENCE_SCHEMA.keys()))


def _write_if_nonempty(frame: pl.DataFrame, path: Path) -> None:
    if frame.is_empty():
        if path.exists():
            path.unlink()
        return
    frame.write_parquet(path)


def build_combined_parquets(
    *,
    gold_root: str | Path = 'data/gold',
    output_dir: str | Path = 'data/combined',
    inputs_package: str = 'pypath.inputs_v2',
) -> dict[str, Any]:
    gold_root = Path(gold_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_dirs = discover_gold_source_dirs(gold_root)

    ontology_terms = _build_ontology_terms(source_dirs, output_dir)
    entity_output, entity_pk_map = _build_entity(source_dirs, ontology_terms)
    relation_output, relation_pk_map = _build_relation(source_dirs, entity_pk_map, entity_output)

    outputs = {
        'entity.parquet': entity_output,
        'entity_relation.parquet': relation_output,
        'entity_relation_evidence.parquet': _build_relation_evidence(
            source_dirs, relation_pk_map
        ),
    }

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build combined warehouse parquet artifacts from B3 per-source gold outputs.',
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_combined_parquets(
        gold_root=args.gold_root,
        output_dir=args.output_dir,
        inputs_package=args.inputs_package,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
