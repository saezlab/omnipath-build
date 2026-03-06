#!/usr/bin/env python3
"""CLI entrypoint for running Meilisearch index import tasks only."""

from __future__ import annotations

import argparse
from pathlib import Path

from omnipath_build.pipeline.dag_core import run_index_imports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run index import tasks from latest data pipeline outputs')
    parser.add_argument('--jobs', type=int, default=4, help='Parallel worker count for index tasks.')
    parser.add_argument('--full-reindex', action='store_true', help='Force full Meilisearch reindex for all datasets.')
    parser.add_argument(
        '--progress',
        choices=['auto', 'rich', 'plain'],
        default='rich',
        help='Terminal progress mode.',
    )
    args = parser.parse_args(argv)

    state = run_index_imports(
        project_root=Path(__file__).resolve().parents[2],
        jobs=max(1, args.jobs),
        full_reindex=args.full_reindex,
        progress_mode=args.progress,
    )
    print(f"Index import completed: {state['run_id']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
