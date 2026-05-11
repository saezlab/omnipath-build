#!/usr/bin/env python3
"""CLI commands for coordinating OmniPath database build steps."""

from __future__ import annotations

import sys
import json
from typing import Optional
import logging
from pathlib import Path
import argparse

from omnipath_build.silver.build import DiscoveryError, run_silver_loader
from omnipath_build.pipeline.pipeline import main as pipeline_main

def _configure_logging() -> None:
    """Configure CLI logging after imported dependencies had a chance to mutate it."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True,
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


def _handle_pipeline(args: argparse.Namespace) -> int:
    """Execute the active incremental build pipeline."""
    argv: list[str] = []
    positional_sources: list[str] = []
    source_list = list(args.source_list)
    from_stage = args.from_stage
    if args.sources:
        for item in args.sources:
            if item.startswith('sources='):
                source_list.append(item.split('=', 1)[1])
            elif item.startswith('from='):
                from_stage = item.split('=', 1)[1]
            else:
                positional_sources.append(item)
        argv.extend(positional_sources)
    if source_list:
        argv.extend(['--sources', ','.join(source_list)])
    argv.extend(['--from', from_stage])
    argv.extend(['--data-root', str(args.data_root)])
    argv.extend(['--inputs-package', args.inputs_package])
    argv.extend(['--batch-size', str(args.batch_size)])
    argv.extend(['--jobs', str(args.jobs)])
    if args.test_mode:
        argv.append('--test-mode')
    if args.resolver_mapping_dir is not None:
        argv.extend(['--resolver-mapping-dir', str(args.resolver_mapping_dir)])
    if not getattr(args, 'build_mappings', True):
        argv.append('--no-build-mappings')
    if not getattr(args, 'build_sources', True):
        argv.append('--no-build-sources')
    if not getattr(args, 'combine', True):
        argv.append('--no-combine')
    combined_output_dir = getattr(args, 'combined_output_dir', None)
    if combined_output_dir is not None:
        argv.extend(['--combined-output-dir', str(combined_output_dir)])
    argv.extend([
        '--combine-entity-batch-size',
        str(args.combine_entity_batch_size),
    ])
    argv.extend([
        '--combine-relation-batch-size',
        str(args.combine_relation_batch_size),
    ])
    postgres_uri = getattr(args, 'postgres_uri', None)
    if postgres_uri is not None:
        argv.extend(['--postgres-uri', postgres_uri])
    postgres_schema = getattr(args, 'postgres_schema', None)
    if postgres_schema is not None:
        argv.extend(['--postgres-schema', postgres_schema])
    if getattr(args, 'postgres_drop_existing', False):
        argv.append('--postgres-drop-existing')
    if getattr(args, 'yes', False):
        argv.append('--yes')
    try:
        return pipeline_main(argv)
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _handle_combined(args: argparse.Namespace) -> int:
    """Build combined warehouse parquet artifacts."""
    project_root = Path(__file__).resolve().parent.parent.parent

    from omnipath_build.gold.combine import build_combined

    gold_root: Path = args.gold_root
    output_dir: Path = args.output_dir
    if not gold_root.is_absolute():
        gold_root = project_root / gold_root
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    try:
        affected_entity_keys: set[str] | None = None
        affected_relation_keys: set[str] | None = None
        if args.affected_entities is not None:
            affected_entity_keys = set(json.loads(args.affected_entities.read_text()))
        if args.affected_relations is not None:
            affected_relation_keys = set(json.loads(args.affected_relations.read_text()))
        build_combined(
            gold_root=gold_root,
            output_dir=output_dir,
            affected_entity_keys=affected_entity_keys,
            affected_relation_keys=affected_relation_keys,
            freeze_monthly=args.freeze_monthly,
            changed_source=args.changed_source,
            entity_batch_size=args.entity_batch_size,
            relation_batch_size=args.relation_batch_size,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _handle_postgres(args: argparse.Namespace) -> int:
    """Execute PostgreSQL loader workflow based on CLI arguments."""
    project_root = Path(__file__).resolve().parent.parent.parent

    from omnipath_build.postgres import load_combined_schema_to_postgres

    output_dir: Path = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    try:
        return load_combined_schema_to_postgres(
            output_dir=output_dir,
            postgres_uri=args.postgres_uri,
            schema=args.schema,
            drop_existing=args.drop_existing,
            batch_size=args.batch_size,
            unlogged_tables=args.unlogged_tables,
            foreign_keys=args.foreign_keys,
            tables=args.tables,
            indexes=args.indexes,
            bitmaps=args.bitmaps,
            views=args.views,
            combine_run_dir=args.combine_run_dir,
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

    def add_pipeline_args(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            'sources',
            nargs='*',
            help='Optional source modules to process.',
        )
        command_parser.add_argument(
            '--sources',
            dest='source_list',
            action='append',
            default=[],
            help='Comma-separated source module list, e.g. signor,connectomedb.',
        )
        command_parser.add_argument(
            '--from',
            dest='from_stage',
            choices=('download', 'bronze', 'silver', 'gold'),
            default='download',
            help='Pipeline stage to start from for selected sources (default: download).',
        )
        command_parser.add_argument(
            '--data-root',
            type=Path,
            default=Path('data'),
            help='Pipeline data root (default: data)',
        )
        command_parser.add_argument(
            '--inputs-package',
            default='pypath.inputs_v2',
            help='Python package containing generator modules (default: pypath.inputs_v2)',
        )
        command_parser.add_argument(
            '--batch-size',
            type=int,
            default=10_000,
            help='Number of records per write batch',
        )
        command_parser.add_argument(
            '--jobs',
            type=int,
            default=4,
            help='Parallel workers for the active pipeline',
        )
        command_parser.add_argument(
            '--test-mode',
            action='store_true',
            help='Enable selective test limits for configured high-volume sources',
        )
        command_parser.add_argument(
            '--resolver-mapping-dir',
            type=Path,
            default=Path('id_resolver/data'),
            help='Existing resolver mapping directory to reuse (default: id_resolver/data).',
        )
        command_parser.add_argument(
            '--build-mappings',
            action=argparse.BooleanOptionalAction,
            default=True,
            help='Build or reuse resolver mappings (default: on).',
        )
        command_parser.add_argument(
            '--build-sources',
            action=argparse.BooleanOptionalAction,
            default=True,
            help='Build silver and gold source outputs (default: on).',
        )
        command_parser.add_argument(
            '--combine',
            action=argparse.BooleanOptionalAction,
            default=True,
            help='Build combined outputs after source builds (default: on).',
        )
        command_parser.add_argument(
            '--combined-output-dir',
            type=Path,
            default=None,
            help='Directory to write combined artifacts (default: <data-root>/combined).',
        )
        command_parser.add_argument(
            '--combine-entity-batch-size',
            type=int,
            default=50_000,
            help='Number of entity keys per DuckDB combine batch.',
        )
        command_parser.add_argument(
            '--combine-relation-batch-size',
            type=int,
            default=50_000,
            help='Number of relation keys per DuckDB combine batch.',
        )
        command_parser.add_argument(
            '--postgres-uri',
            type=str,
            default=None,
            help='Optional Postgres URI for loading combined artifacts.',
        )
        command_parser.add_argument(
            '--postgres-schema',
            type=str,
            default='public',
            help='Postgres schema to load into (default: public).',
        )
        command_parser.add_argument(
            '--postgres-drop-existing',
            action='store_true',
            default=False,
            help='Drop existing tables before loading into Postgres.',
        )
        command_parser.add_argument(
            '--yes',
            action='store_true',
            help='Execute the printed plan without waiting for Enter.',
        )
        command_parser.set_defaults(handler=_handle_pipeline)

    pipeline_parser = subparsers.add_parser(
        'pipeline',
        help='Run the incremental build pipeline for selected or autodiscovered sources.',
    )
    add_pipeline_args(pipeline_parser)

    combined_parser = subparsers.add_parser(
        'combined',
        help='Build combined warehouse parquet artifacts from per-source gold outputs.',
    )
    combined_parser.add_argument(
        '--gold-root',
        type=Path,
        default=Path('data/gold'),
        help='Root directory containing per-source gold outputs (default: data/gold)',
    )
    combined_parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data/combined'),
        help='Directory to write combined parquet artifacts (default: data/combined)',
    )
    combined_parser.add_argument(
        '--affected-entities',
        type=Path,
        default=None,
        help='Path to JSON file with list of affected entity_keys.',
    )
    combined_parser.add_argument(
        '--affected-relations',
        type=Path,
        default=None,
        help='Path to JSON file with list of affected relation_keys.',
    )
    combined_parser.add_argument(
        '--freeze-monthly',
        action='store_true',
        help=(
            'After writing, copy the latest/ directory to an immutable '
            'YYYY-MM/ snapshot. Useful for creating monthly baselines.'
        ),
    )
    combined_parser.add_argument(
        '--changed-source',
        type=str,
        default=None,
        help='Name of the source that changed (for build manifest).',
    )
    combined_parser.add_argument(
        '--entity-batch-size',
        type=int,
        default=50_000,
        help='Number of entity keys per DuckDB combine batch (default: 50000).',
    )
    combined_parser.add_argument(
        '--relation-batch-size',
        type=int,
        default=50_000,
        help='Number of relation keys per DuckDB combine batch (default: 50000).',
    )
    combined_parser.set_defaults(handler=_handle_combined)

    postgres_parser = subparsers.add_parser(
        'postgres',
        help='Load combined gold parquet artifacts into the PostgreSQL warehouse schema.',
    )
    postgres_parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('data/combined'),
        help='Path to the combined artifact directory or a single artifact directory (default: data/combined)',
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
    postgres_parser.add_argument(
        '--batch-size',
        type=int,
        default=200_000,
        help='Parquet/COPY batch size for table loading (default: 200000)',
    )
    postgres_parser.add_argument(
        '--unlogged-tables',
        action=argparse.BooleanOptionalAction,
        default=False,
        help='Create base tables as UNLOGGED tables to reduce WAL during rebuilds (default: false)',
    )
    postgres_parser.add_argument(
        '--foreign-keys',
        action=argparse.BooleanOptionalAction,
        default=False,
        help='Create foreign key constraints on base tables (default: false for faster rebuilds)',
    )
    postgres_parser.add_argument(
        '--tables',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Load table data (default: true)',
    )
    postgres_parser.add_argument(
        '--indexes',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Create secondary indexes (default: true)',
    )
    postgres_parser.add_argument(
        '--bitmaps',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Create and populate bitmap tables (default: true)',
    )
    postgres_parser.add_argument(
        '--views',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Create materialized views (default: true)',
    )
    postgres_parser.add_argument(
        '--combine-run-dir',
        type=Path,
        default=None,
        help='Path to data/combined/runs/<run_id> delta artifacts. Defaults to runs/latest.json when available.',
    )
    postgres_parser.set_defaults(handler=_handle_postgres)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the database manager CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging()
    handler = getattr(args, 'handler', None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == '__main__':
    sys.exit(main())
