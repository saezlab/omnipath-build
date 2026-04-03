#!/usr/bin/env python3
"""DAG data-pipeline CLI entrypoint (build artifacts/search parquet, no index import)."""

from __future__ import annotations

import argparse
from pathlib import Path

from omnipath_build.pipeline.dag_core import run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run DAG-based incremental data pipeline')
    parser.add_argument('--inputs-package', default='pypath.inputs_v2')
    parser.add_argument('--test-mode', action='store_true', help='Silver test-mode record caps')
    parser.add_argument(
        '--freshness-checks',
        action='store_true',
        help='Run remote freshness checks (may redownload when changes are detected). Default is disabled.',
    )
    parser.add_argument('--jobs', type=int, default=4, help='Parallel worker count for parallelizable DAG layers.')
    parser.add_argument(
        '--progress',
        choices=['auto', 'rich', 'plain'],
        default='rich',
        help='Terminal progress mode.',
    )
    args = parser.parse_args(argv)

    state = run_pipeline(
        project_root=Path(__file__).resolve().parents[2],
        inputs_package=args.inputs_package,
        test_mode=args.test_mode,
        run_freshness_checks=args.freshness_checks,
        jobs=max(1, args.jobs),
        progress_mode=args.progress,
    )
    print(f"Pipeline completed: {state['run_id']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
