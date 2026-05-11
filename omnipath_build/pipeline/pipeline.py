from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from omnipath_build.pipeline.dag import run_pipeline


def _normalize_key_value_args(argv: Sequence[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    normalized: list[str] = []
    for item in argv:
        if item.startswith('sources='):
            normalized.extend(['--sources', item.split('=', 1)[1]])
        elif item.startswith('from='):
            normalized.extend(['--from', item.split('=', 1)[1]])
        else:
            normalized.append(item)
    return normalized


def _split_sources(values: Sequence[str]) -> list[str]:
    sources: list[str] = []
    for value in values:
        sources.extend(
            item.strip()
            for item in value.split(',')
            if item.strip()
        )
    return sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Build resolver mappings, silver, gold, and combined artifacts.',
    )
    parser.add_argument(
        'sources',
        nargs='*',
        help='Source module(s) to process. If omitted, discover all sources from --inputs-package.',
    )
    parser.add_argument(
        '--sources',
        dest='source_list',
        action='append',
        default=[],
        help='Comma-separated source module list, e.g. signor,connectomedb.',
    )
    parser.add_argument(
        '--from',
        dest='from_stage',
        choices=('download', 'bronze', 'silver', 'gold'),
        default='download',
        help='Pipeline stage to start from for selected sources (default: download).',
    )
    parser.add_argument('--data-root', type=Path, default=Path('data'))
    parser.add_argument('--inputs-package', default='pypath.inputs_v2')
    parser.add_argument('--batch-size', type=int, default=10_000)
    parser.add_argument('--test-mode', action='store_true')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument(
        '--resolver-mapping-dir',
        type=Path,
        default=Path('id_resolver/data'),
        help='Resolver mapping directory to reuse or build into (default: id_resolver/data).',
    )
    parser.add_argument(
        '--build-mappings',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Build or reuse resolver mappings (default: on).',
    )
    parser.add_argument(
        '--build-sources',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Build silver and gold source outputs (default: on).',
    )
    parser.add_argument(
        '--combine',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Build combined outputs after source builds (default: on).',
    )
    parser.add_argument(
        '--combined-output-dir',
        type=Path,
        default=None,
        help='Directory to write combined artifacts (default: <data-root>/combined).',
    )
    parser.add_argument(
        '--combine-entity-batch-size',
        type=int,
        default=50_000,
        help='Number of entity keys per DuckDB combine batch.',
    )
    parser.add_argument(
        '--combine-relation-batch-size',
        type=int,
        default=50_000,
        help='Number of relation keys per DuckDB combine batch.',
    )
    parser.add_argument(
        '--postgres-uri',
        type=str,
        default=None,
        help='Optional Postgres URI for loading combined artifacts.',
    )
    parser.add_argument(
        '--postgres-schema',
        type=str,
        default='public',
        help='Postgres schema to load into (default: public).',
    )
    parser.add_argument(
        '--postgres-drop-existing',
        action='store_true',
        default=False,
        help='Drop existing tables before loading into Postgres.',
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Execute the printed plan without waiting for Enter.',
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_key_value_args(argv))
    selected_sources = _split_sources([*args.source_list, *args.sources])

    report = run_pipeline(
        sources=selected_sources,
        data_root=args.data_root,
        inputs_package=args.inputs_package,
        batch_size=args.batch_size,
        test_mode=args.test_mode,
        jobs=max(1, args.jobs),
        resolver_mapping_dir=args.resolver_mapping_dir,
        start_stage=args.from_stage,
        build_mappings=args.build_mappings,
        build_sources=args.build_sources,
        combine=args.combine,
        combined_output_dir=args.combined_output_dir,
        combine_entity_batch_size=args.combine_entity_batch_size,
        combine_relation_batch_size=args.combine_relation_batch_size,
        confirm_plan=not args.yes,
        postgres_uri=args.postgres_uri,
        postgres_schema=args.postgres_schema,
        postgres_drop_existing=args.postgres_drop_existing,
    )
    print(f"run_id={report['run_id']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
