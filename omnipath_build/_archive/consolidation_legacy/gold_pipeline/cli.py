from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from omnipath_build.gold_pipeline.pipeline import run_gold_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Gold pipeline: silver -> gold -> canonicalize with shared resolver mappings.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        'sources',
        nargs='*',
        help='Source module(s) to process. If omitted, discover all sources from --inputs-package.',
    )
    common.add_argument('--data-root', type=Path, default=Path('data_v2'))
    common.add_argument('--inputs-package', default='pypath.inputs_v2')
    common.add_argument('--batch-size', type=int, default=10_000)
    common.add_argument('--silver-test-mode', action='store_true')
    common.add_argument('--jobs', type=int, default=4)
    common.add_argument(
        '--resolver-mapping-dir',
        type=Path,
        default=Path('id_resolver/data'),
        help='Existing resolver mapping directory to reuse (default: id_resolver/data).',
    )

    subparsers.add_parser(
        'source',
        parents=[common],
        help='Run selected sources through silver, gold, and canonicalization.',
    )

    mappings = subparsers.add_parser(
        'mappings',
        help='Build shared resolver mapping tables only.',
    )
    mappings.add_argument('--data-root', type=Path, default=Path('data_v2'))
    mappings.add_argument('--jobs', type=int, default=1)
    mappings.add_argument(
        '--resolver-mapping-dir',
        type=Path,
        default=None,
        help='Optional existing resolver mapping directory to validate/reuse instead of rebuilding.',
    )

    subparsers.add_parser(
        'all',
        parents=[common],
        help='Build resolver mappings and selected sources.',
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    report = run_gold_pipeline(
        command=args.command,
        sources=getattr(args, 'sources', []) or [],
        data_root=args.data_root,
        inputs_package=getattr(args, 'inputs_package', 'pypath.inputs_v2'),
        batch_size=getattr(args, 'batch_size', 10_000),
        test_mode=getattr(args, 'silver_test_mode', False),
        jobs=max(1, args.jobs),
        resolver_mapping_dir=getattr(args, 'resolver_mapping_dir', None),
    )
    print(f"run_id={report['run_id']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
