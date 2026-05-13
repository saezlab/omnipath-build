#!/usr/bin/env python3
"""CLI commands for coordinating OmniPath database build steps."""

from __future__ import annotations

import sys
import json
import time
from typing import Optional
import logging
from pathlib import Path
import argparse
import subprocess

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
    argv.extend([
        '--combine-min-part-size-mb',
        str(args.combine_min_part_size_mb),
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
    argv.extend([
        '--memory-sample-interval-seconds',
        str(args.memory_sample_interval_seconds),
    ])
    try:
        return pipeline_main(argv)
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _handle_bronze_rewrite(args: argparse.Namespace) -> int:
    """Materialize bronze rewrite DuckDB state for selected raw datasets."""
    import itertools

    from omnipath_build.rewrite.bronze import materialize_bronze_duckdb
    from omnipath_build.silver.build import discover_resources

    try:
        discovered, _path_manager = discover_resources(
            database_name=args.database,
            base_path=None,
            inputs_package=args.inputs_package,
        )
        selected_sources = _split_source_args(args.sources)
        unknown_sources = [
            source for source in selected_sources if source not in discovered
        ]
        if unknown_sources:
            raise ValueError(
                'Unknown source(s) '
                f'{", ".join(unknown_sources)}. Use silver --list to inspect sources.'
            )
        snapshots = []
        for selected_source in selected_sources:
            for fn in discovered[selected_source]:
                if fn.function_name == 'resource':
                    continue
                if args.function and fn.function_name != args.function:
                    continue
                raw_dataset = getattr(fn.call, '_raw_dataset', None)
                if raw_dataset is None:
                    continue

                records = raw_dataset.raw(force_refresh=args.force_refresh)
                if args.max_records is not None:
                    records = itertools.islice(records, args.max_records)
                print(
                    f'[bronze-rewrite:{fn.source}.{fn.function_name}] start',
                    flush=True,
                )
                snapshot = materialize_bronze_duckdb(
                    records=records,
                    source=fn.source,
                    dataset=fn.function_name,
                    data_root=args.data_root,
                    batch_size=args.batch_size,
                )
                snapshots.append(snapshot)
                print(
                    f'[bronze-rewrite:{fn.source}.{fn.function_name}] '
                    f'snapshot={snapshot.snapshot_id} state={snapshot.source_state_path}',
                    flush=True,
                )
        if not snapshots:
            raise ValueError('No raw datasets matched the requested filters.')
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _handle_silver_rewrite(args: argparse.Namespace) -> int:
    """Materialize silver rewrite DuckDB state from rewrite bronze state."""
    from omnipath_build.rewrite.silver import materialize_silver_duckdb
    from omnipath_build.silver.build import discover_resources

    try:
        discovered, _path_manager = discover_resources(
            database_name=args.database,
            base_path=None,
            inputs_package=args.inputs_package,
        )
        selected_sources = _split_source_args(args.sources)
        unknown_sources = [
            source for source in selected_sources if source not in discovered
        ]
        if unknown_sources:
            raise ValueError(
                'Unknown source(s) '
                f'{", ".join(unknown_sources)}. Use silver --list to inspect sources.'
            )
        for selected_source in selected_sources:
            functions = [
                fn
                for fn in discovered[selected_source]
                if args.function is None or fn.function_name == args.function
            ]
            print(f'[silver-rewrite:{selected_source}] start', flush=True)
            result = materialize_silver_duckdb(
                source=selected_source,
                resource_functions=functions,
                data_root=args.data_root,
                batch_size=args.batch_size,
            )
            row_summary = ', '.join(
                f'{name}={count:,}'
                for name, count in sorted(result.rows_by_table.items())
            )
            print(
                f'[silver-rewrite:{selected_source}] '
                f'mapped_raw_records={result.mapped_raw_record_count:,} '
                f'deleted_raw_records={result.deleted_raw_record_count:,} '
                f'state={result.source_state_path} rows: {row_summary}',
                flush=True,
            )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _handle_gold_rewrite(args: argparse.Namespace) -> int:
    """Materialize source-gold rewrite DuckDB state from rewrite silver state."""
    from omnipath_build.gold.build_entities import GoldPartitionConfig
    from omnipath_build.rewrite.gold import materialize_gold_duckdb
    from omnipath_build.silver.build import discover_resources

    try:
        discovered, _path_manager = discover_resources(
            database_name=args.database,
            base_path=None,
            inputs_package=args.inputs_package,
        )
        selected_sources = _split_source_args(args.sources)
        unknown_sources = [
            source for source in selected_sources if source not in discovered
        ]
        if unknown_sources:
            raise ValueError(
                'Unknown source(s) '
                f'{", ".join(unknown_sources)}. Use silver --list to inspect sources.'
            )
        cfg = GoldPartitionConfig(
            bucket_count=args.bucket_count,
            part_count=args.part_count,
            min_part_size_bytes=args.min_part_size_mb * 1024 * 1024,
            duckdb_memory_limit=args.duckdb_memory_limit,
            duckdb_threads=args.duckdb_threads,
            duckdb_max_temp_directory_size=args.duckdb_max_temp_directory_size,
            duckdb_partitioned_write_max_open_files=(
                args.duckdb_partitioned_write_max_open_files
            ),
        )
        for selected_source in selected_sources:
            print(f'[gold-rewrite:{selected_source}] start', flush=True)
            result = materialize_gold_duckdb(
                source=selected_source,
                data_root=args.data_root,
                mapping_dir=args.resolver_mapping_dir,
                partition_config=cfg,
            )
            row_summary = ', '.join(
                f'{name}={count:,}'
                for name, count in sorted(result.rows_by_table.items())
            )
            print(
                f'[gold-rewrite:{selected_source}] '
                f'state={result.source_state_path} '
                f'archive={result.archive_path} '
                f'gold_changed={result.gold_changed} '
                f'archive_written={result.archive_written} '
                f'rows: {row_summary}',
                flush=True,
            )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _handle_combined_rewrite(args: argparse.Namespace) -> int:
    """Build rewrite combined DuckDB state and public parquet export."""
    try:
        from omnipath_build.rewrite.combine_duckdb import CombinedRewriteConfig
        from omnipath_build.rewrite.combine import materialize_combined_duckdb

        selected_sources = _split_source_args(args.sources)
        cfg = CombinedRewriteConfig(
            bucket_count=args.bucket_count,
            part_count=args.part_count,
            duckdb_memory_limit=args.duckdb_memory_limit,
            duckdb_threads=args.duckdb_threads,
            duckdb_max_temp_directory_size=args.duckdb_max_temp_directory_size,
        )
        print(
            '[combined-rewrite] start '
            f'sources={",".join(selected_sources)} data_root={args.data_root}',
            flush=True,
        )
        result = materialize_combined_duckdb(
            sources=selected_sources,
            data_root=args.data_root,
            inputs_package=args.inputs_package,
            config=cfg,
        )
        row_summary = ', '.join(
            f'{name}={count:,}'
            for name, count in sorted(result.row_counts.items())
        )
        print(
            '[combined-rewrite] '
            f'state={result.combined_state_path} '
            f'latest={result.latest_dir} '
            f'reports={result.reports_dir} '
            f'mode={result.mode} '
            f'rows: {row_summary}',
            flush=True,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f'Unexpected error: {exc}', file=sys.stderr)
        return 1


def _handle_rewrite_pipeline(args: argparse.Namespace) -> int:
    """Run the rewrite pipeline with phase-aware RSS sampling."""
    from datetime import UTC, datetime

    from omnipath_build.pipeline.memory import start_memory_monitor
    from omnipath_build.pipeline.progress import phase

    selected_sources = _split_source_args(args.sources)
    if not selected_sources:
        print('Unexpected error: No sources selected.', file=sys.stderr)
        return 1

    run_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    memory_log = args.data_root / 'reports' / 'memory' / f'rewrite-{run_id}.ndjson'
    memory_summary_path = (
        args.data_root / 'reports' / 'memory' / f'rewrite-{run_id}.summary.json'
    )
    latest_summary_path = args.data_root / 'reports' / 'memory' / 'rewrite-latest.json'
    monitor = start_memory_monitor(
        output_path=memory_log,
        interval_seconds=args.memory_sample_interval_seconds,
    )
    source_args = [','.join(selected_sources)]
    stage_summaries: list[dict[str, object]] = []
    started_at = time.perf_counter()

    stages = [
        (
            'bronze',
            [
                'bronze-rewrite',
                *source_args,
                '--data-root',
                str(args.data_root),
                '--inputs-package',
                args.inputs_package,
                '--batch-size',
                str(args.batch_size),
                *(['--function', args.function] if args.function else []),
                *(['--max-records', str(args.max_records)] if args.max_records is not None else []),
                *(['--force-refresh'] if args.force_refresh else []),
            ],
        ),
        (
            'silver',
            [
                'silver-rewrite',
                *source_args,
                '--data-root',
                str(args.data_root),
                '--inputs-package',
                args.inputs_package,
                '--batch-size',
                str(args.batch_size),
                *(['--function', args.function] if args.function else []),
            ],
        ),
        (
            'gold',
            [
                'gold-rewrite',
                *source_args,
                '--data-root',
                str(args.data_root),
                '--inputs-package',
                args.inputs_package,
                '--resolver-mapping-dir',
                str(args.resolver_mapping_dir),
                '--bucket-count',
                str(args.bucket_count),
                '--part-count',
                str(args.gold_part_count),
                '--min-part-size-mb',
                str(args.gold_min_part_size_mb),
                '--duckdb-partitioned-write-max-open-files',
                str(args.duckdb_partitioned_write_max_open_files),
                *(['--duckdb-memory-limit', args.duckdb_memory_limit] if args.duckdb_memory_limit else []),
                *(['--duckdb-threads', str(args.duckdb_threads)] if args.duckdb_threads is not None else []),
                *(['--duckdb-max-temp-directory-size', args.duckdb_max_temp_directory_size] if args.duckdb_max_temp_directory_size else []),
            ],
        ),
        (
            'combined',
            [
                'combined-rewrite',
                *source_args,
                '--data-root',
                str(args.data_root),
                '--inputs-package',
                args.inputs_package,
                '--bucket-count',
                str(args.bucket_count),
                '--part-count',
                str(args.combined_part_count),
                *(['--duckdb-memory-limit', args.duckdb_memory_limit] if args.duckdb_memory_limit else []),
                *(['--duckdb-threads', str(args.duckdb_threads)] if args.duckdb_threads is not None else []),
                *(['--duckdb-max-temp-directory-size', args.duckdb_max_temp_directory_size] if args.duckdb_max_temp_directory_size else []),
            ],
        ),
    ]

    try:
        for stage_name, stage_args in stages:
            label = f'rewrite:{stage_name}'
            command = [sys.executable, '-m', 'omnipath_build.cli.commands', *stage_args]
            stage_started = time.perf_counter()
            print(
                f'[{label}] start command={" ".join(stage_args)}',
                flush=True,
            )
            with phase(label, 'running'):
                monitor.sample_now()
                completed = subprocess.run(command, check=False)
                monitor.sample_now()
            elapsed = time.perf_counter() - stage_started
            summary = monitor.summary()
            phase_peak = summary.get('peak_by_phase', {}).get(label, {})
            stage_summary = {
                'stage': stage_name,
                'label': label,
                'elapsed_seconds': elapsed,
                'return_code': completed.returncode,
                'peak_rss_bytes': phase_peak.get('rss_bytes'),
                'peak_rss_mebibytes': phase_peak.get('rss_mebibytes'),
            }
            stage_summaries.append(stage_summary)
            peak_text = (
                f'{float(phase_peak["rss_mebibytes"]):.1f} MiB'
                if phase_peak.get('rss_mebibytes') is not None
                else 'n/a'
            )
            print(
                f'[{label}] done elapsed={elapsed:.1f}s '
                f'peak_rss={peak_text} return_code={completed.returncode}',
                flush=True,
            )
            if completed.returncode != 0:
                return completed.returncode
        return 0
    finally:
        summary = monitor.stop()
        summary['run_id'] = run_id
        summary['sources'] = selected_sources
        summary['elapsed_seconds'] = time.perf_counter() - started_at
        summary['stages'] = stage_summaries
        memory_summary_path.parent.mkdir(parents=True, exist_ok=True)
        memory_summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        latest_summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        print(
            '[rewrite:pipeline] memory_summary='
            f'{memory_summary_path} latest={latest_summary_path}',
            flush=True,
        )


def _split_source_args(values: list[str]) -> list[str]:
    """Normalize comma-separated and positional source arguments."""
    sources: list[str] = []
    for value in values:
        for item in value.split(','):
            item = item.strip()
            if item:
                sources.append(item)
    return sources


def _handle_combined(args: argparse.Namespace) -> int:
    """Build combined warehouse parquet artifacts."""
    project_root = Path(__file__).resolve().parent.parent.parent

    from omnipath_build.gold.build_entities import GoldPartitionConfig
    from omnipath_build.gold.combine import build_combined

    gold_root: Path = args.gold_root
    output_dir: Path = args.output_dir
    if not gold_root.is_absolute():
        gold_root = project_root / gold_root
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    try:
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
            gold_root=gold_root,
            output_dir=output_dir,
            affected_entity_key_paths=args.affected_entities,
            affected_relation_key_paths=args.affected_relations,
            freeze_monthly=args.freeze_monthly,
            changed_source=args.changed_source,
            entity_batch_size=args.entity_batch_size,
            relation_batch_size=args.relation_batch_size,
            partition_config=partition_config,
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
            '--combine-min-part-size-mb',
            type=int,
            default=100,
            help='Target minimum combine part size in MiB before creating another part.',
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
        command_parser.add_argument(
            '--memory-sample-interval-seconds',
            type=float,
            default=5.0,
            help='Interval for phase-aware RSS memory samples (default: 5 seconds).',
        )
        command_parser.set_defaults(handler=_handle_pipeline)

    pipeline_parser = subparsers.add_parser(
        'pipeline',
        help='Run the incremental build pipeline for selected or autodiscovered sources.',
    )
    add_pipeline_args(pipeline_parser)

    bronze_rewrite_parser = subparsers.add_parser(
        'bronze-rewrite',
        help='Materialize rewrite bronze raw-record DuckDB state.',
    )
    bronze_rewrite_parser.add_argument(
        'sources',
        nargs='+',
        help='Source module to process, e.g. uniprot or signor.',
    )
    bronze_rewrite_parser.add_argument(
        '--function',
        help='Specific raw dataset/function within the source.',
    )
    bronze_rewrite_parser.add_argument(
        '--database',
        default='omnipath',
        help='Database name used for resource discovery (default: omnipath).',
    )
    bronze_rewrite_parser.add_argument(
        '--data-root',
        type=Path,
        default=Path('data_rewrite'),
        help='Rewrite data root (default: data_rewrite).',
    )
    bronze_rewrite_parser.add_argument(
        '--inputs-package',
        default='pypath.inputs_v2',
        help='Python package containing generator modules (default: pypath.inputs_v2).',
    )
    bronze_rewrite_parser.add_argument(
        '--batch-size',
        type=int,
        default=50_000,
        help='Rows per DuckDB insert batch.',
    )
    bronze_rewrite_parser.add_argument(
        '--max-records',
        type=int,
        default=None,
        help='Limit raw records per selected dataset for quick local trials.',
    )
    bronze_rewrite_parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Force pypath downloads to refresh before parsing.',
    )
    bronze_rewrite_parser.set_defaults(handler=_handle_bronze_rewrite)

    silver_rewrite_parser = subparsers.add_parser(
        'silver-rewrite',
        help='Materialize rewrite silver DuckDB state from rewrite bronze state.',
    )
    silver_rewrite_parser.add_argument(
        'sources',
        nargs='+',
        help='Source module to process, e.g. uniprot or signor.',
    )
    silver_rewrite_parser.add_argument(
        '--function',
        help='Specific raw dataset/function within the source.',
    )
    silver_rewrite_parser.add_argument(
        '--database',
        default='omnipath',
        help='Database name used for resource discovery (default: omnipath).',
    )
    silver_rewrite_parser.add_argument(
        '--data-root',
        type=Path,
        default=Path('data_rewrite'),
        help='Rewrite data root (default: data_rewrite).',
    )
    silver_rewrite_parser.add_argument(
        '--inputs-package',
        default='pypath.inputs_v2',
        help='Python package containing generator modules (default: pypath.inputs_v2).',
    )
    silver_rewrite_parser.add_argument(
        '--batch-size',
        type=int,
        default=10_000,
        help='Rows per DuckDB insert batch.',
    )
    silver_rewrite_parser.set_defaults(handler=_handle_silver_rewrite)

    gold_rewrite_parser = subparsers.add_parser(
        'gold-rewrite',
        help='Materialize rewrite source-gold DuckDB state from rewrite silver state.',
    )
    gold_rewrite_parser.add_argument(
        'sources',
        nargs='+',
        help='Source module to process, e.g. uniprot or signor.',
    )
    gold_rewrite_parser.add_argument(
        '--database',
        default='omnipath',
        help='Database name used for resource discovery (default: omnipath).',
    )
    gold_rewrite_parser.add_argument(
        '--data-root',
        type=Path,
        default=Path('data_rewrite'),
        help='Rewrite data root (default: data_rewrite).',
    )
    gold_rewrite_parser.add_argument(
        '--inputs-package',
        default='pypath.inputs_v2',
        help='Python package containing generator modules (default: pypath.inputs_v2).',
    )
    gold_rewrite_parser.add_argument(
        '--resolver-mapping-dir',
        type=Path,
        default=Path('id_resolver/data'),
        help='Identifier resolver mapping directory used by gold canonicalization.',
    )
    gold_rewrite_parser.add_argument(
        '--bucket-count',
        type=int,
        default=4096,
        help='Number of deterministic logical buckets from gold onward.',
    )
    gold_rewrite_parser.add_argument(
        '--part-count',
        type=int,
        default=128,
        help='Maximum number of compact physical Parquet parts per source gold table.',
    )
    gold_rewrite_parser.add_argument(
        '--min-part-size-mb',
        type=int,
        default=200,
        help='Target minimum physical Parquet part size in MiB before creating another part.',
    )
    gold_rewrite_parser.add_argument(
        '--duckdb-memory-limit',
        type=str,
        default=None,
        help="Optional DuckDB memory limit, for example '16GB'.",
    )
    gold_rewrite_parser.add_argument(
        '--duckdb-threads',
        type=int,
        default=None,
        help='Optional DuckDB thread count.',
    )
    gold_rewrite_parser.add_argument(
        '--duckdb-max-temp-directory-size',
        type=str,
        default=None,
        help="Optional DuckDB temporary spill limit, for example '500GB'.",
    )
    gold_rewrite_parser.add_argument(
        '--duckdb-partitioned-write-max-open-files',
        type=int,
        default=16,
        help='DuckDB partitioned writer open-file limit.',
    )
    gold_rewrite_parser.set_defaults(handler=_handle_gold_rewrite)

    combined_rewrite_parser = subparsers.add_parser(
        'combined-rewrite',
        help='Materialize rewrite combined DuckDB state from rewrite source-gold state.',
    )
    combined_rewrite_parser.add_argument(
        'sources',
        nargs='+',
        help='Source modules to combine, e.g. signor uniprot or signor,uniprot.',
    )
    combined_rewrite_parser.add_argument(
        '--data-root',
        type=Path,
        default=Path('data_rewrite'),
        help='Rewrite data root (default: data_rewrite).',
    )
    combined_rewrite_parser.add_argument(
        '--inputs-package',
        default='pypath.inputs_v2',
        help='Python package containing generator modules (default: pypath.inputs_v2).',
    )
    combined_rewrite_parser.add_argument(
        '--bucket-count',
        type=int,
        default=4096,
        help='Number of deterministic logical buckets from gold onward.',
    )
    combined_rewrite_parser.add_argument(
        '--part-count',
        type=int,
        default=16,
        help='Internal combined recompute part count; rewrite exports are flat Parquet files.',
    )
    combined_rewrite_parser.add_argument(
        '--duckdb-memory-limit',
        type=str,
        default=None,
        help="Optional DuckDB memory limit, for example '16GB'.",
    )
    combined_rewrite_parser.add_argument(
        '--duckdb-threads',
        type=int,
        default=None,
        help='Optional DuckDB thread count.',
    )
    combined_rewrite_parser.add_argument(
        '--duckdb-max-temp-directory-size',
        type=str,
        default=None,
        help="Optional DuckDB temporary spill limit, for example '500GB'.",
    )
    combined_rewrite_parser.set_defaults(handler=_handle_combined_rewrite)

    rewrite_pipeline_parser = subparsers.add_parser(
        'rewrite-pipeline',
        help='Run the full rewrite pipeline with stage-level memory tracking.',
    )
    rewrite_pipeline_parser.add_argument(
        'sources',
        nargs='+',
        help='Source modules to process, e.g. signor uniprot or signor,uniprot.',
    )
    rewrite_pipeline_parser.add_argument(
        '--function',
        help='Specific raw dataset/function within each selected source.',
    )
    rewrite_pipeline_parser.add_argument(
        '--data-root',
        type=Path,
        default=Path('data_rewrite'),
        help='Rewrite data root (default: data_rewrite).',
    )
    rewrite_pipeline_parser.add_argument(
        '--inputs-package',
        default='pypath.inputs_v2',
        help='Python package containing generator modules (default: pypath.inputs_v2).',
    )
    rewrite_pipeline_parser.add_argument(
        '--batch-size',
        type=int,
        default=10_000,
        help='Rows per DuckDB insert batch.',
    )
    rewrite_pipeline_parser.add_argument(
        '--max-records',
        type=int,
        default=None,
        help='Limit raw records per selected dataset for quick local trials.',
    )
    rewrite_pipeline_parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Force pypath downloads to refresh before parsing.',
    )
    rewrite_pipeline_parser.add_argument(
        '--resolver-mapping-dir',
        type=Path,
        default=Path('id_resolver/data'),
        help='Identifier resolver mapping directory used by gold canonicalization.',
    )
    rewrite_pipeline_parser.add_argument(
        '--bucket-count',
        type=int,
        default=4096,
        help='Number of deterministic logical buckets from gold onward.',
    )
    rewrite_pipeline_parser.add_argument(
        '--gold-part-count',
        type=int,
        default=128,
        help='Maximum number of compact physical Parquet parts per source gold table.',
    )
    rewrite_pipeline_parser.add_argument(
        '--combined-part-count',
        type=int,
        default=16,
        help='Internal combined recompute part count.',
    )
    rewrite_pipeline_parser.add_argument(
        '--gold-min-part-size-mb',
        type=int,
        default=200,
        help='Target minimum physical Parquet part size in MiB.',
    )
    rewrite_pipeline_parser.add_argument(
        '--duckdb-memory-limit',
        type=str,
        default=None,
        help="Optional DuckDB memory limit, for example '16GB'.",
    )
    rewrite_pipeline_parser.add_argument(
        '--duckdb-threads',
        type=int,
        default=None,
        help='Optional DuckDB thread count.',
    )
    rewrite_pipeline_parser.add_argument(
        '--duckdb-max-temp-directory-size',
        type=str,
        default=None,
        help="Optional DuckDB temporary spill limit, for example '500GB'.",
    )
    rewrite_pipeline_parser.add_argument(
        '--duckdb-partitioned-write-max-open-files',
        type=int,
        default=16,
        help='DuckDB partitioned writer open-file limit.',
    )
    rewrite_pipeline_parser.add_argument(
        '--memory-sample-interval-seconds',
        type=float,
        default=5.0,
        help='Interval for rewrite stage RSS memory samples.',
    )
    rewrite_pipeline_parser.set_defaults(handler=_handle_rewrite_pipeline)

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
        action='append',
        help='Path to parquet file with affected entity_key rows. Repeat for multiple sources.',
    )
    combined_parser.add_argument(
        '--affected-relations',
        type=Path,
        default=None,
        action='append',
        help='Path to parquet file with affected relation_key rows. Repeat for multiple sources.',
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
    combined_parser.add_argument(
        '--bucket-count',
        type=int,
        default=4096,
        help='Number of deterministic logical buckets from gold onward.',
    )
    combined_parser.add_argument(
        '--part-count',
        type=int,
        default=128,
        help='Maximum number of compact physical Parquet parts per public table.',
    )
    combined_parser.add_argument(
        '--min-part-size-mb',
        type=int,
        default=100,
        help='Target minimum physical Parquet part size in MiB before creating another part.',
    )
    combined_parser.add_argument(
        '--duckdb-memory-limit',
        type=str,
        default=None,
        help="Optional DuckDB memory limit, for example '16GB'.",
    )
    combined_parser.add_argument(
        '--duckdb-threads',
        type=int,
        default=None,
        help='Optional DuckDB thread count.',
    )
    combined_parser.add_argument(
        '--duckdb-max-temp-directory-size',
        type=str,
        default=None,
        help="Optional DuckDB temporary spill limit, for example '500GB'.",
    )
    combined_parser.add_argument(
        '--duckdb-partitioned-write-max-open-files',
        type=int,
        default=64,
        help='Maximum open files DuckDB may keep for partitioned writes.',
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
