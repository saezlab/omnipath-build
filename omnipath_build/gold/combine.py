from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from omnipath_build.gold.combine_duckdb import build_combined_duckdb


def build_combined(
    *,
    gold_root: str | Path = 'data/gold',
    output_dir: str | Path = 'data/combined',
    affected_entity_keys: set[str] | None = None,
    affected_relation_keys: set[str] | None = None,
    inputs_package: str = 'pypath.inputs_v2',
    freeze_monthly: bool = False,
    changed_source: str | None = None,
    entity_batch_size: int = 50_000,
    relation_batch_size: int = 50_000,
) -> dict[str, Any]:
    """Build or update combined artifacts using the DuckDB state store.

    There is one combine path. If the DuckDB state is empty, the same path
    bootstraps it from all gold outputs. Otherwise, supplied affected key sets
    drive a targeted update.
    """
    return build_combined_duckdb(
        gold_root=gold_root,
        output_dir=output_dir,
        affected_entity_keys=affected_entity_keys,
        affected_relation_keys=affected_relation_keys,
        inputs_package=inputs_package,
        freeze_monthly=freeze_monthly,
        changed_source=changed_source,
        entity_batch_size=entity_batch_size,
        relation_batch_size=relation_batch_size,
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
    parser.add_argument(
        '--entity-batch-size',
        type=int,
        default=50_000,
        help='Number of entity keys per DuckDB combine batch (default: 50000).',
    )
    parser.add_argument(
        '--relation-batch-size',
        type=int,
        default=50_000,
        help='Number of relation keys per DuckDB combine batch (default: 50000).',
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
        entity_batch_size=args.entity_batch_size,
        relation_batch_size=args.relation_batch_size,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
