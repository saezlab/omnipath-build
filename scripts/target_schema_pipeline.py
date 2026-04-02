#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Iterable

from omnipath_build.loaders.silver import run_silver_loader
from scripts.build_global_entity_identifiers import process_source
from scripts.silver_to_target_schema import SourceConverter
from scripts.target_schema_entity_dedup import deduplicate_target_schema_dir

from id_resolver.build.mapping_tables import CHEMICAL_SOURCES, run_sources as materialize_resolver_tables
from id_resolver.resolve.target_schema import normalize_target_schema_dir

DEFAULT_DATA_V2_ROOT = Path('data_v2')
DEFAULT_SILVER_ROOT = DEFAULT_DATA_V2_ROOT / 'silver'
DEFAULT_GOLD_ROOT = DEFAULT_DATA_V2_ROOT / 'gold'
DEFAULT_MAPPING_DIR = Path('id_resolver/data')
DEFAULT_GLOBAL_DIR = DEFAULT_GOLD_ROOT / '_global'
DEFAULT_INPUTS_PACKAGE = 'pypath.inputs_v2'
REFERENCE_MAPPING_SOURCES = ['uniprot', *CHEMICAL_SOURCES]


def _source_relpath(source: str) -> Path:
    return Path(source.replace('.', '/'))


def _source_dir(root: Path, source: str) -> Path:
    return root / _source_relpath(source)


def _discover_gold_sources(gold_root: Path) -> list[str]:
    if not gold_root.exists():
        return []
    return sorted(
        p.name for p in gold_root.iterdir() if p.is_dir() and not p.name.startswith('_')
    )


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def build_silver_sources(
    sources: Iterable[str],
    *,
    silver_root: Path,
    inputs_package: str,
    batch_size: int,
    overwrite: bool,
    test_mode: bool,
) -> None:
    silver_root.mkdir(parents=True, exist_ok=True)

    for source in sources:
        source_silver_dir = _source_dir(silver_root, source)
        if overwrite and source_silver_dir.exists():
            print(f'[{source}] removing existing silver output: {source_silver_dir}')
            _remove_path(source_silver_dir)

        print(f'[{source}] building silver -> {source_silver_dir}')
        run_silver_loader(
            database='.',
            base_path=silver_root.parent,
            source=source,
            list_only=False,
            batch_size=batch_size,
            dry_run=False,
            override=overwrite,
            test_mode=test_mode,
            inputs_package=inputs_package,
        )


def convert_sources_to_gold(
    sources: Iterable[str],
    *,
    silver_root: Path,
    gold_root: Path,
    batch_size: int,
    overwrite: bool,
) -> None:
    for source in sources:
        source_silver_dir = _source_dir(silver_root, source)
        source_gold_dir = gold_root / source

        if overwrite and source_gold_dir.exists():
            print(f'[{source}] removing existing gold output: {source_gold_dir}')
            _remove_path(source_gold_dir)

        source_gold_dir.mkdir(parents=True, exist_ok=True)
        converter = SourceConverter(
            source=source,
            silver_dir=source_silver_dir,
            output_dir=source_gold_dir,
            batch_size=batch_size,
        )
        try:
            converter.convert()
        finally:
            converter.close()

        dedup_summary = deduplicate_target_schema_dir(source_gold_dir)
        print(f'[{source}] wrote gold tables to {source_gold_dir} (dedup: {dedup_summary})')


def rebuild_mapping_tables(
    *,
    mapping_dir: Path,
    chemical_reference_sources: Iterable[str],
    overwrite: bool,
) -> dict[str, int]:
    if overwrite and mapping_dir.exists():
        print(f'Removing existing mapping tables: {mapping_dir}')
        _remove_path(mapping_dir)

    summary = materialize_resolver_tables(
        sources=['uniprot', *chemical_reference_sources],
        output_dir=mapping_dir,
    )
    print(f'Materialized resolver mapping tables in {mapping_dir}')
    print(summary)
    return summary


def normalize_sources(
    sources: Iterable[str],
    *,
    gold_root: Path,
    mapping_dir: Path,
) -> None:
    for source in sources:
        source_dir = gold_root / source
        try:
            summary = normalize_target_schema_dir(
                source_dir=source_dir,
                mapping_dir=mapping_dir,
            )
            print(f'[{source}] normalization: {summary}')
        except FileNotFoundError as exc:
            print(f'[{source}] normalization skipped: {exc}')


def build_global_outputs(
    sources: Iterable[str],
    *,
    gold_root: Path,
    global_dir: Path,
    overwrite: bool,
) -> None:
    if overwrite and global_dir.exists():
        print(f'Removing existing global outputs: {global_dir}')
        _remove_path(global_dir)
    global_dir.mkdir(parents=True, exist_ok=True)

    total_matched = 0
    total_new = 0
    total_identifier_rows_added = 0

    for source in sources:
        source_dir = gold_root / source
        summary = process_source(source, source_dir, global_dir)
        total_matched += summary['matched_existing_global_entities']
        total_new += summary['new_global_entities_added']
        total_identifier_rows_added += summary['global_identifier_rows_added']
        print(f'[{source}] {summary}')

    print('\nFinal global summary:')
    print(f'  matched existing global entities: {total_matched:,}')
    print(f'  new global entities added: {total_new:,}')
    print(f'  global identifier rows added: {total_identifier_rows_added:,}')


def _warn_missing_mappings(mapping_dir: Path) -> None:
    print(
        f'Resolver mapping tables not found in {mapping_dir}. '
        'Use id_resolver/data or run `make target-schema-mappings` explicitly.'
    )


def _add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('sources', nargs='+', help='Source module(s) to process, e.g. signor reactome')
    parser.add_argument('--silver-test-mode', action='store_true', help='Enable test mode only for the silver build step')
    parser.add_argument('--skip-silver', action='store_true', help='Reuse existing data_v2/silver inputs instead of rebuilding silver')
    parser.add_argument('--skip-mappings', action='store_true', help='Skip id_resolver normalization for per-source outputs')
    parser.add_argument('--with-global', action='store_true', help='Also rebuild global outputs after per-source processing')
    parser.add_argument('--no-overwrite', action='store_true', help='Do not delete existing outputs before rebuilding')
    parser.add_argument('--silver-root', type=Path, default=DEFAULT_SILVER_ROOT)
    parser.add_argument('--gold-root', type=Path, default=DEFAULT_GOLD_ROOT)
    parser.add_argument('--mapping-dir', type=Path, default=DEFAULT_MAPPING_DIR)
    parser.add_argument('--global-dir', type=Path, default=DEFAULT_GLOBAL_DIR)
    parser.add_argument('--inputs-package', default=DEFAULT_INPUTS_PACKAGE)
    parser.add_argument('--batch-size', type=int, default=10_000)
    parser.add_argument(
        '--chemical-reference-sources',
        nargs='*',
        default=list(CHEMICAL_SOURCES),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Consolidated target-schema pipeline: silver -> gold -> resolver normalization -> global.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    source_parser = subparsers.add_parser(
        'source',
        help='Build one or more sources from silver through normalized per-source gold outputs.',
    )
    _add_common_source_args(source_parser)

    mappings_parser = subparsers.add_parser(
        'mappings',
        help='Materialize id_resolver mapping tables under data_v2/gold/_mapping_tables.',
    )
    mappings_parser.add_argument('--mapping-dir', type=Path, default=DEFAULT_MAPPING_DIR)
    mappings_parser.add_argument('--no-overwrite', action='store_true', help='Do not delete existing mapping outputs before rebuilding')
    mappings_parser.add_argument(
        '--chemical-reference-sources',
        nargs='*',
        default=list(CHEMICAL_SOURCES),
    )

    global_parser = subparsers.add_parser(
        'global',
        help='Build global identifier outputs from per-source gold outputs.',
    )
    global_parser.add_argument('sources', nargs='*', help='Optional source names; defaults to all sources under the gold root')
    global_parser.add_argument('--gold-root', type=Path, default=DEFAULT_GOLD_ROOT)
    global_parser.add_argument('--global-dir', type=Path, default=DEFAULT_GLOBAL_DIR)
    global_parser.add_argument('--no-overwrite', action='store_true', help='Do not delete existing global outputs before rebuilding')

    all_parser = subparsers.add_parser(
        'all',
        help='Build source outputs, shared mappings, and global outputs in order.',
    )
    _add_common_source_args(all_parser)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overwrite = not getattr(args, 'no_overwrite', False)

    if args.command == 'mappings':
        rebuild_mapping_tables(
            mapping_dir=args.mapping_dir,
            chemical_reference_sources=args.chemical_reference_sources,
            overwrite=overwrite,
        )
        return 0

    if args.command == 'global':
        sources = args.sources or _discover_gold_sources(args.gold_root)
        build_global_outputs(
            sources,
            gold_root=args.gold_root,
            global_dir=args.global_dir,
            overwrite=overwrite,
        )
        return 0

    sources = args.sources

    if not args.skip_silver:
        build_silver_sources(
            sources,
            silver_root=args.silver_root,
            inputs_package=args.inputs_package,
            batch_size=args.batch_size,
            overwrite=overwrite,
            test_mode=args.silver_test_mode,
        )

    convert_sources_to_gold(
        sources,
        silver_root=args.silver_root,
        gold_root=args.gold_root,
        batch_size=args.batch_size,
        overwrite=overwrite,
    )

    if not args.skip_mappings:
        normalize_sources(
            sources,
            gold_root=args.gold_root,
            mapping_dir=args.mapping_dir,
        )

    if args.command == 'all' or args.with_global:
        build_global_outputs(
            sources,
            gold_root=args.gold_root,
            global_dir=args.global_dir,
            overwrite=overwrite,
        )

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
