#!/usr/bin/env python3
"""CLI commands for coordinating OmniPath database build steps."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from omnipath_build.loaders.silver import DiscoveryError, run_silver_loader

# Configure logging for the entire application
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

__all__ = [
    'main',
]


def _handle_silver(args: argparse.Namespace) -> int:
    """Execute silver loader workflow based on CLI arguments."""
    try:
        discovered, _path_manager, selected_functions, outputs = run_silver_loader(
            database=args.database,
            base_path=args.base_path,
            source=args.source,
            function=args.function,
            list_only=args.list,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            override=args.override,
            test_mode=args.test_mode,
            inputs_package=args.inputs_package,
        )
    except DiscoveryError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1

    if args.list:
        print(f'Discovered resources in "{args.inputs_package}":')
        for source_name in sorted(discovered.keys()):
            fq_module = f'{args.inputs_package}.{source_name}'
            print(f'- {fq_module}:')
            for fn in discovered[source_name]:
                print(f'    • {fn.function_name}')
        return 0

    if not selected_functions or outputs is None:
        print('No resource functions were processed.', file=sys.stderr)
        return 1

    for fn, output in zip(selected_functions, outputs, strict=False):
        if output is None:
            print(f'[{fn.source}.{fn.function_name}] completed without writing a file.')
        else:
            print(f'[{fn.source}.{fn.function_name}] wrote: {output}')
    return 0


def _handle_gold(args: argparse.Namespace) -> int:
    """Execute gold loader workflow based on CLI arguments."""
    project_root = Path(__file__).resolve().parent.parent.parent

    from omnipath_build.loaders.gold import run_gold_loader_new

    data_root: Path = args.data_root
    if not data_root.is_absolute():
        data_root = project_root / data_root
    output_dir: Path = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    needs_data_root = args.step in (None, 'local_tables')
    if needs_data_root and not data_root.exists():
        print(f'Error: data root not found: {data_root}', file=sys.stderr)
        return 1

    local_tables_dir: Path | None = args.local_tables_dir
    if local_tables_dir is not None and not local_tables_dir.is_absolute():
        local_tables_dir = project_root / local_tables_dir

    try:
        run_gold_loader_new(
            data_root=data_root,
            output_dir=output_dir,
            step=args.step,
            source=args.source,
            local_tables_dir=local_tables_dir,
        )
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1

    return 0


def _handle_postgres(args: argparse.Namespace) -> int:
    """Execute PostgreSQL loader workflow based on CLI arguments."""
    project_root = Path(__file__).resolve().parent.parent.parent

    from omnipath_build._archive.postgres_loader import load_tables_to_postgres

    output_dir: Path = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    try:
        return load_tables_to_postgres(
            output_dir=output_dir,
            postgres_uri=args.postgres_uri,
            schema=args.schema,
            drop_existing=args.drop_existing,
        )
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    """Configure the top-level argument parser."""
    parser = argparse.ArgumentParser(
        description='Orchestrate OmniPath silver and gold data loaders.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    silver_parser = subparsers.add_parser(
        'silver',
        help='Discover and process silver resource generators.',
    )
    silver_parser.add_argument(
        '--database', default='omnipath', help='Database to process (default: omnipath)'
    )
    silver_parser.add_argument(
        '--base-path',
        type=Path,
        help='Override base path for databases/<database> (controls output location)',
    )
    silver_parser.add_argument(
        '--inputs-package',
        default='pypath.inputs_v2',
        help='Python package containing generator modules (default: pypath.inputs_v2)',
    )
    silver_parser.add_argument(
        '--source',
        help='Specific module under --inputs-package to process (e.g. "uniprot" or "ontologies.uniprot_keywords")',
    )
    silver_parser.add_argument(
        '--function',
        help='Specific generator function to process within the selected module',
    )
    silver_parser.add_argument(
        '--list', action='store_true', help='List discovered sources and exit'
    )
    silver_parser.add_argument(
        '--batch-size',
        type=int,
        default=10_000,
        help='Number of records per write batch',
    )
    silver_parser.add_argument(
        '--dry-run', action='store_true', help='Run without writing parquet outputs'
    )
    silver_parser.add_argument(
        '--override',
        action='store_true',
        help='Process even if output file already exists',
    )
    silver_parser.add_argument(
        '--test-mode',
        action='store_true',
        help='Enable selective test limits for configured high-volume sources',
    )
    silver_parser.set_defaults(handler=_handle_silver)

    gold_parser = subparsers.add_parser(
        'gold',
        help='Build gold tables from prepared silver outputs.',
    )
    gold_parser.add_argument(
        '--data-root',
        type=Path,
        default=Path('omnipath_build/data/silver'),
        help='Path to data directory containing silver files (default: omnipath_build/data/silver)',
    )
    gold_parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('omnipath_build/data/gold'),
        help='Path to output directory for gold tables (default: omnipath_build/data/gold)',
    )
    gold_parser.add_argument(
        '--step',
        type=str,
        choices=[
            'local_tables',
            'entity_identifiers',
            'global_tables',
        ],
        help=(
            'Run only a specific step '
            '(local_tables, entity_identifiers, global_tables)'
        ),
    )
    gold_parser.add_argument(
        '--source',
        type=str,
        help='Optional single source to process (local_tables step only)',
    )
    gold_parser.add_argument(
        '--local-tables-dir',
        type=Path,
        help='Optional directory to read local_* tables from for entity_identifiers/global_tables (supports nested per-source directories).',
    )
    gold_parser.set_defaults(handler=_handle_gold)

    postgres_parser = subparsers.add_parser(
        'postgres',
        help='Load gold tables from parquet files to PostgreSQL.',
    )
    postgres_parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('omnipath_build/data/gold'),
        help='Path to output directory containing parquet files (default: omnipath_build/data/gold)',
    )
    postgres_parser.add_argument(
        '--postgres-uri',
        type=str,
        required=True,
        help='PostgreSQL connection string (e.g., postgresql://user:pass@localhost:5432/dbname)',
    )
    postgres_parser.add_argument(
        '--schema',
        type=str,
        default='public',
        help='Target schema in PostgreSQL (default: public)',
    )
    postgres_parser.add_argument(
        '--drop-existing',
        action='store_true',
        help='Drop existing tables before creating new ones',
    )
    postgres_parser.set_defaults(handler=_handle_postgres)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the database manager CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, 'handler', None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == '__main__':
    sys.exit(main())
