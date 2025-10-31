#!/usr/bin/env python3
"""Minimal CLI entry point for coordinating OmniPath database build steps."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from omnipath_build.gold_loader_new import run_gold_loader_new
from omnipath_build.silver_loader import DiscoveryError, run_silver_loader

# Configure logging for the entire application
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
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
        )
    except DiscoveryError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1

    if args.list:
        print(f'Discovered resources for database "{args.database}":')
        for source_name, functions in discovered.items():
            print(f'- {source_name}:')
            for fn in functions:
                hint = fn.schema_type or 'unknown'
                print(f'    • {fn.function_name} (schema: {hint})')
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
    project_root = Path(__file__).resolve().parent.parent

    data_root: Path = args.data_root
    if not data_root.is_absolute():
        data_root = project_root / data_root
    output_dir: Path = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    if not data_root.exists():
        print(f'Error: data root not found: {data_root}', file=sys.stderr)
        return 1

    try:
        run_gold_loader_new(
            data_root=data_root,
            output_dir=output_dir,
            phase=args.phase,
            step=args.step,
        )
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1

    return 0


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
    silver_parser.add_argument('--database', default='omnipath', help='Database to process (default: omnipath)')
    silver_parser.add_argument('--base-path', type=Path, help='Override base databases directory')
    silver_parser.add_argument('--source', help='Specific source to process (module name)')
    silver_parser.add_argument('--function', help='Specific function to process within the source')
    silver_parser.add_argument('--list', action='store_true', help='List discovered sources and exit')
    silver_parser.add_argument('--batch-size', type=int, default=10_000, help='Number of records per write batch')
    silver_parser.add_argument('--dry-run', action='store_true', help='Run without writing parquet outputs')
    silver_parser.add_argument('--override', action='store_true', help='Process even if output file already exists')
    silver_parser.set_defaults(handler=_handle_silver)

    gold_parser = subparsers.add_parser(
        'gold',
        help='Build gold tables from prepared silver outputs.',
    )
    gold_parser.add_argument(
        '--data-root',
        type=Path,
        default=Path('databases/omnipath/data'),
        help='Path to data directory containing silver files (default: databases/omnipath/data)',
    )
    gold_parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('databases/omnipath/output'),
        help='Path to output directory for gold tables (default: databases/omnipath/output)',
    )
    gold_parser.add_argument(
        '--phase',
        type=str,
        choices=['1', '2', '3'],
        help='Run only a specific phase (1=cross-source, 2=evidence extraction, 3=compound properties)',
    )
    gold_parser.add_argument(
        '--step',
        type=str,
        choices=['sources', 'cv_terms', 'local_tables', 'entity_identifiers', 'references'],
        help='Run only a specific step within phase 1 (sources, cv_terms, local_tables, entity_identifiers, references)',
    )
    gold_parser.set_defaults(handler=_handle_gold)

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
