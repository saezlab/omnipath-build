#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl

from omnipath_build.gold.build_entities import GoldPartitionConfig
from omnipath_build.gold.combine import build_combined
from omnipath_build.gold.utils.partitioning import (
    add_entity_partition_columns,
    add_relation_partition_columns,
    write_part_dataset,
)
from omnipath_build.pipeline.tasks import _write_gold_delta_artifacts
from omnipath_build.postgres.postgres import (
    resolve_combined_dir,
    resolve_parquet_artifact,
)


def _attrs_series(length: int) -> pl.Series:
    return pl.Series(
        [[] for _ in range(length)],
        dtype=pl.List(pl.Struct([
            pl.Field('term', pl.String),
            pl.Field('value', pl.String),
            pl.Field('unit', pl.String),
        ])),
    )


def _write_synthetic_gold(
    gold_root: Path,
    *,
    cfg: GoldPartitionConfig,
) -> None:
    source_dir = gold_root / 'test_source'
    entities_dir = source_dir / 'entities'
    relations_dir = source_dir / 'relations'
    entities_dir.mkdir(parents=True)
    relations_dir.mkdir(parents=True)

    entity = pl.DataFrame({
        'entity_key': ['key1', 'key2'],
        'canonical_identifier': ['P12345', 'P67890'],
        'canonical_identifier_type': ['uniprot', 'uniprot'],
        'identifiers': [
            [{'identifier': 'P12345', 'identifier_type': 'uniprot'}],
            [{'identifier': 'P67890', 'identifier_type': 'uniprot'}],
        ],
        'entity_type': ['protein', 'protein'],
        'taxonomy_id': ['9606', '9606'],
        'entity_attributes': _attrs_series(2),
        'sources': [['test_source'], ['test_source']],
    })
    entity = add_entity_partition_columns(
        entity,
        bucket_count=cfg.bucket_count,
        part_count=cfg.part_count,
    )
    write_part_dataset(
        entity,
        entities_dir / 'entity',
        part_col='entity_part',
        bucket_col='entity_bucket',
        key_col='entity_key',
        part_count=cfg.part_count,
    )

    entity_evidence = pl.DataFrame({
        'source': ['test_source', 'test_source'],
        'entity_key': ['key1', 'key2'],
        'raw_record_id': ['r1', 'r2'],
        'entity_type': ['protein', 'protein'],
        'taxonomy_id': ['9606', '9606'],
        'identifiers': [
            [{'identifier': 'P12345', 'identifier_type': 'uniprot'}],
            [{'identifier': 'P67890', 'identifier_type': 'uniprot'}],
        ],
        'entity_attributes': _attrs_series(2),
    })
    entity_evidence = add_entity_partition_columns(
        entity_evidence,
        bucket_count=cfg.bucket_count,
        part_count=cfg.part_count,
    )
    write_part_dataset(
        entity_evidence,
        entities_dir / 'entity_evidence',
        part_col='entity_part',
        bucket_col='entity_bucket',
        key_col='entity_key',
        part_count=cfg.part_count,
    )

    relation = pl.DataFrame({
        'relation_key': ['rel1'],
        'subject_entity_key': ['key1'],
        'predicate': ['interacts_with'],
        'object_entity_key': ['key2'],
        'relation_category': ['interaction'],
        'evidence_count': [1],
        'sources': [['test_source']],
    })
    relation = add_relation_partition_columns(
        relation,
        bucket_count=cfg.bucket_count,
        part_count=cfg.part_count,
    )
    write_part_dataset(
        relation,
        relations_dir / 'entity_relation',
        part_col='relation_part',
        bucket_col='relation_bucket',
        key_col='relation_key',
        part_count=cfg.part_count,
    )

    relation_evidence = pl.DataFrame({
        'source': ['test_source'],
        'relation_key': ['rel1'],
        'raw_record_id': ['r1'],
        'record_attributes': _attrs_series(1),
        'subject_attributes': _attrs_series(1),
        'object_attributes': _attrs_series(1),
        'evidence': _attrs_series(1),
    })
    relation_evidence = add_relation_partition_columns(
        relation_evidence,
        bucket_count=cfg.bucket_count,
        part_count=cfg.part_count,
    )
    write_part_dataset(
        relation_evidence,
        relations_dir / 'entity_relation_evidence',
        part_col='relation_part',
        bucket_col='relation_bucket',
        key_col='relation_key',
        part_count=cfg.part_count,
    )


def run_test() -> None:
    cfg = GoldPartitionConfig(
        bucket_count=8,
        part_count=4,
        min_part_size_bytes=0,
        duckdb_memory_limit='512MB',
        duckdb_threads=1,
    )
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        gold_root = root / 'gold'
        combined_root = root / 'combined'
        output_dir = gold_root / 'test_source'
        _write_synthetic_gold(gold_root, cfg=cfg)

        delta_summary = _write_gold_delta_artifacts(
            source='test_source',
            silver_dir=root / 'silver',
            previous_dir=output_dir,
            staged_dir=output_dir,
            output_dir=output_dir,
            previous_output_ready=False,
        )
        assert delta_summary['affected_entity_count'] == 2
        assert delta_summary['affected_relation_count'] == 1
        delta_dir = Path(delta_summary['delta_dir'])
        assert (delta_dir / 'affected_entity_parts.parquet').exists()
        assert (delta_dir / 'affected_relation_parts.parquet').exists()

        summary = build_combined(
            gold_root=gold_root,
            output_dir=combined_root,
            inputs_package='pypath.inputs_v2',
            partition_config=cfg,
        )
        latest = combined_root / 'latest'
        assert summary['mode'] == 'bootstrap'
        assert resolve_combined_dir(combined_root) == latest.resolve()
        assert resolve_parquet_artifact(latest, 'entity') == latest / 'entity'
        assert len(list((latest / 'entity').glob('part=*/data.parquet'))) == cfg.part_count
        assert len(list((latest / 'entity_relation').glob('part=*/data.parquet'))) == cfg.part_count

        entities = pl.read_parquet(str(latest / 'entity' / '**' / '*.parquet'))
        relations = pl.read_parquet(str(latest / 'entity_relation' / '**' / '*.parquet'))
        assert entities.height == 2
        assert relations.height == 1


if __name__ == '__main__':
    run_test()
    print('memory-safe partitioning smoke test passed')
