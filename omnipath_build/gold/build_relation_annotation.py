from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from omnipath_build.gold.utils.canonicalization import (
    ONTOLOGY_ENTITY_TYPE_LABEL,
    ONTOLOGY_IDENTIFIER_TYPE_LABEL,
)
from omnipath_build.gold.utils.table_schema import RELATION_ANNOTATION_TERM_SCHEMA, empty_frame


def _write_if_nonempty(frame: pl.DataFrame, path: Path) -> None:
    if frame.is_empty():
        if path.exists():
            path.unlink()
        return
    frame.write_parquet(path)


def build_relation_annotation(
    *,
    output_dir: str | Path = 'data/combined',
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    entity_relation_path = output_dir / 'entity_relation.parquet'
    entity_relation_evidence_path = output_dir / 'entity_relation_evidence.parquet'
    entity_path = output_dir / 'entity.parquet'
    relation_annotation_path = output_dir / 'relation_annotation_term.parquet'

    required_paths = [
        entity_relation_path,
        entity_relation_evidence_path,
        entity_path,
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        _write_if_nonempty(empty_frame(RELATION_ANNOTATION_TERM_SCHEMA), relation_annotation_path)
        summary = {
            'output_dir': str(output_dir),
            'relation_annotation_path': str(relation_annotation_path),
            'row_count': 0,
            'missing_inputs': missing,
        }
        (output_dir / 'relation_annotation_summary.json').write_text(
            json.dumps(summary, indent=2) + '\n',
            encoding='utf-8',
        )
        return summary

    relations = pl.scan_parquet(entity_relation_path)
    relation_evidence = pl.scan_parquet(entity_relation_evidence_path)
    term_entities = (
        pl.scan_parquet(entity_path)
        .filter(
            (pl.col('entity_type') == ONTOLOGY_ENTITY_TYPE_LABEL)
            & (pl.col('canonical_identifier_type') == ONTOLOGY_IDENTIFIER_TYPE_LABEL)
        )
        .select([
            pl.col('entity_id').cast(pl.Int64).alias('term_entity_id'),
            pl.col('canonical_identifier').cast(pl.String).alias('term_id'),
        ])
    )

    interaction_relation_evidence = (
        relations
        .filter(pl.col('relation_category') == 'interaction')
        .select([
            pl.col('relation_id').cast(pl.Int64),
            pl.col('subject_entity_id').cast(pl.Int64),
            pl.col('object_entity_id').cast(pl.Int64),
        ])
        .join(
            relation_evidence.select([
                pl.col('relation_evidence_id').cast(pl.Int64),
                pl.col('relation_id').cast(pl.Int64),
                pl.col('source').cast(pl.String),
                pl.col('record_attributes'),
            ]),
            on='relation_id',
            how='inner',
        )
    )

    interaction_terms = (
        interaction_relation_evidence
        .explode('record_attributes')
        .drop_nulls('record_attributes')
        .select([
            pl.col('relation_id'),
            pl.col('relation_evidence_id'),
            pl.col('source'),
            pl.lit('relation').alias('scope'),
            pl.col('record_attributes').struct.field('term').alias('_raw_term'),
            pl.col('record_attributes').struct.field('value').alias('_value'),
            pl.col('record_attributes').struct.field('unit').alias('_unit'),
        ])
        .filter(
            pl.col('_raw_term').is_not_null()
            & pl.col('_raw_term').str.contains(r'^[^:]+:[^:]+')
            & pl.col('_value').is_null()
            & pl.col('_unit').is_null()
        )
        .with_columns(
            pl.col('_raw_term').str.extract(r'^([^:]+:[^:]+)$|^([^:]+:[^:]+):', 1).fill_null(
                pl.col('_raw_term').str.extract(r'^([^:]+:[^:]+)$|^([^:]+:[^:]+):', 2)
            ).alias('term_id')
        )
        .drop(['_raw_term', '_value', '_unit'])
        .join(term_entities, on='term_id', how='inner')
        .drop('term_id')
        .unique()
    )

    annotation_relations = (
        relations
        .filter(pl.col('relation_category') == 'association')
        .select([
            pl.col('subject_entity_id').cast(pl.Int64),
            pl.col('object_entity_id').cast(pl.Int64).alias('term_entity_id'),
        ])
        .join(term_entities.select('term_entity_id'), on='term_entity_id', how='inner')
    )

    participant_term_candidates = pl.concat([
        interaction_relation_evidence.select([
            pl.col('relation_id'),
            pl.col('relation_evidence_id'),
            pl.col('source'),
            pl.col('subject_entity_id').alias('annotated_entity_id'),
        ]),
        interaction_relation_evidence.select([
            pl.col('relation_id'),
            pl.col('relation_evidence_id'),
            pl.col('source'),
            pl.col('object_entity_id').alias('annotated_entity_id'),
        ]),
    ], how='vertical_relaxed').join(
        annotation_relations,
        left_on='annotated_entity_id',
        right_on='subject_entity_id',
        how='inner',
    )

    participant_terms = (
        participant_term_candidates
        .join(term_entities.select('term_entity_id'), on='term_entity_id', how='inner')
        .select([
            pl.col('relation_id'),
            pl.col('relation_evidence_id'),
            pl.col('source'),
            pl.lit('participants').alias('scope'),
            pl.col('term_entity_id'),
        ])
        .unique()
    )

    combined = (
        pl.concat([interaction_terms, participant_terms], how='vertical_relaxed')
        .unique()
        .sort(['relation_id', 'relation_evidence_id', 'scope', 'term_entity_id', 'source'])
        .collect()
        .select(list(RELATION_ANNOTATION_TERM_SCHEMA.keys()))
    )

    _write_if_nonempty(combined, relation_annotation_path)

    summary = {
        'output_dir': str(output_dir),
        'relation_annotation_path': str(relation_annotation_path),
        'row_count': int(combined.height),
        'missing_inputs': [],
    }
    (output_dir / 'relation_annotation_summary.json').write_text(
        json.dumps(summary, indent=2) + '\n',
        encoding='utf-8',
    )
    return summary



