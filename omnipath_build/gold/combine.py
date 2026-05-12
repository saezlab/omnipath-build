from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from omnipath_build.gold.build_entities import GoldPartitionConfig
from omnipath_build.gold.combine_duckdb import build_combined_duckdb


def build_combined(
    *,
    gold_root: str | Path = 'data/gold',
    output_dir: str | Path = 'data/combined',
    affected_entity_key_paths: list[str | Path] | None = None,
    affected_relation_key_paths: list[str | Path] | None = None,
    inputs_package: str = 'pypath.inputs_v2',
    freeze_monthly: bool = False,
    changed_source: str | None = None,
    entity_batch_size: int = 50_000,
    relation_batch_size: int = 50_000,
    partition_config: GoldPartitionConfig | None = None,
) -> dict[str, Any]:
    """Build or update combined artifacts using the partitioned DuckDB state store."""
    return build_combined_duckdb(
        gold_root=gold_root,
        output_dir=output_dir,
        affected_entity_key_paths=affected_entity_key_paths,
        affected_relation_key_paths=affected_relation_key_paths,
        inputs_package=inputs_package,
        freeze_monthly=freeze_monthly,
        changed_source=changed_source,
        entity_batch_size=entity_batch_size,
        relation_batch_size=relation_batch_size,
        partition_config=partition_config,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build or update combined warehouse parquet artifacts.',
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
        action='append',
        help='Path to parquet file with affected entity_key rows. Repeat for multiple sources.',
    )
    parser.add_argument(
        '--affected-relations',
        type=Path,
        default=None,
        action='append',
        help='Path to parquet file with affected relation_key rows. Repeat for multiple sources.',
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
    parser.add_argument(
        '--entity-batch-size',
        type=int,
        default=50_000,
        help='Compatibility option; combine now primarily uses part boundaries.',
    )
    parser.add_argument(
        '--relation-batch-size',
        type=int,
        default=50_000,
        help='Compatibility option; combine now primarily uses part boundaries.',
    )
    parser.add_argument(
        '--bucket-count',
        type=int,
        default=4096,
        help='Number of deterministic logical buckets from gold onward.',
    )
    parser.add_argument(
        '--part-count',
        type=int,
        default=128,
        help='Maximum number of compact physical Parquet parts per public table.',
    )
    parser.add_argument(
        '--min-part-size-mb',
        type=int,
        default=200,
        help='Target minimum physical Parquet part size in MiB before creating another part.',
    )
    parser.add_argument(
        '--duckdb-memory-limit',
        type=str,
        default=None,
        help="Optional DuckDB memory limit, for example '16GB'.",
    )
    parser.add_argument(
        '--duckdb-threads',
        type=int,
        default=None,
        help='Optional DuckDB thread count.',
    )
    parser.add_argument(
        '--duckdb-max-temp-directory-size',
        type=str,
        default=None,
        help="Optional DuckDB temporary spill limit, for example '500GB'.",
    )
    parser.add_argument(
        '--duckdb-partitioned-write-max-open-files',
        type=int,
        default=64,
        help='Maximum open files DuckDB may keep for partitioned writes.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    partition_config = GoldPartitionConfig(
        bucket_count=args.bucket_count,
        part_count=args.part_count,
        min_part_size_bytes=args.min_part_size_mb * 1024 * 1024,
        duckdb_memory_limit=args.duckdb_memory_limit,
        duckdb_threads=args.duckdb_threads,
        duckdb_max_temp_directory_size=args.duckdb_max_temp_directory_size,
        duckdb_partitioned_write_max_open_files=args.duckdb_partitioned_write_max_open_files,
    )
    build_combined(
        gold_root=args.gold_root,
        output_dir=args.output_dir,
        affected_entity_key_paths=args.affected_entities,
        affected_relation_key_paths=args.affected_relations,
        inputs_package=args.inputs_package,
        freeze_monthly=args.freeze_monthly,
        changed_source=args.changed_source,
        entity_batch_size=args.entity_batch_size,
        relation_batch_size=args.relation_batch_size,
        partition_config=partition_config,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
