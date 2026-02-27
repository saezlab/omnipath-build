#!/usr/bin/env python3
"""Parallel per-source silver -> local_tables pipeline with per-source reports."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from omnipath_build.loaders.silver import discover_resources


@dataclass(slots=True)
class StepResult:
    status: str
    message: str
    seconds: float


def _source_leaf(source: str) -> str:
    return source.split('.')[-1]


def _source_relpath(source: str) -> Path:
    return Path(*source.split('.'))


def _run_command(command: list[str]) -> StepResult:
    start = time.time()
    proc = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.time() - start

    if proc.returncode == 0:
        return StepResult(status='ok', message='completed', seconds=elapsed)

    stderr_tail = (proc.stderr or '').strip().splitlines()[-8:]
    stdout_tail = (proc.stdout or '').strip().splitlines()[-8:]
    tail = '\n'.join([*stderr_tail, *stdout_tail]).strip()
    message = tail if tail else f'command failed with exit code {proc.returncode}'
    return StepResult(status='error', message=message, seconds=elapsed)


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def _copy_local_tables(source: str, stage_local_tables: Path, final_local_tables: Path) -> int:
    source_leaf = _source_leaf(source)
    final_local_tables.mkdir(parents=True, exist_ok=True)

    for existing in final_local_tables.glob(f'local_*_{source_leaf}.parquet'):
        existing.unlink()

    copied = 0
    for file in stage_local_tables.glob(f'local_*_{source_leaf}.parquet'):
        shutil.copy2(file, final_local_tables / file.name)
        copied += 1
    return copied


def _write_source_report(report_path: Path, payload: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def _build_source_worker(
    *,
    source: str,
    source_id: int,
    inputs_package: str,
    test_mode: bool,
    map_path: Path,
    stage_root: Path,
    final_silver_root: Path,
    final_gold_output_dir: Path,
    reports_dir: Path,
) -> dict:
    started_at = time.strftime('%Y-%m-%dT%H:%M:%S%z')

    source_slug = source.replace('.', '__')
    source_stage_dir = stage_root / f'{source_id:03d}__{source_slug}'
    if source_stage_dir.exists():
        shutil.rmtree(source_stage_dir)
    source_stage_dir.mkdir(parents=True, exist_ok=True)

    silver_stage_root = source_stage_dir / 'silver_stage'
    gold_stage_root = source_stage_dir / 'gold_stage'

    silver_cmd = [
        sys.executable,
        '-m',
        'omnipath_build.cli.commands',
        'silver',
        '--base-path',
        str(silver_stage_root),
        '--database',
        '.',
        '--source',
        source,
        '--inputs-package',
        inputs_package,
    ]
    if test_mode:
        silver_cmd.append('--test-mode')

    silver_result = _run_command(silver_cmd)

    local_result = StepResult(status='skipped', message='silver failed', seconds=0.0)
    copied_local_files = 0

    if silver_result.status == 'ok':
        source_rel = _source_relpath(source)
        silver_stage_source_dir = silver_stage_root / 'silver' / source_rel
        final_silver_source_dir = final_silver_root / source_rel
        if silver_stage_source_dir.exists():
            _copy_tree(silver_stage_source_dir, final_silver_source_dir)

        local_cmd = [
            sys.executable,
            '-m',
            'omnipath_build.cli.commands',
            'gold',
            '--data-root',
            str(silver_stage_root / 'silver'),
            '--output-dir',
            str(gold_stage_root),
            '--step',
            'local_tables',
            '--source',
            source,
            '--source-id-map',
            str(map_path),
        ]
        local_result = _run_command(local_cmd)

        if local_result.status == 'ok':
            copied_local_files = _copy_local_tables(
                source=source,
                stage_local_tables=gold_stage_root / 'local_tables',
                final_local_tables=final_gold_output_dir / 'local_tables',
            )

    overall_status = (
        'ok'
        if silver_result.status == 'ok' and local_result.status == 'ok'
        else 'failed'
    )

    finished_at = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    report = {
        'source_id': source_id,
        'source': source,
        'silver': {
            'status': silver_result.status,
            'message': silver_result.message,
            'seconds': round(silver_result.seconds, 3),
        },
        'local_tables': {
            'status': local_result.status,
            'message': local_result.message,
            'seconds': round(local_result.seconds, 3),
            'copied_files': copied_local_files,
        },
        'overall_status': overall_status,
        'started_at': started_at,
        'finished_at': finished_at,
    }

    report_name = f'{source_id:03d}__{source_slug}.json'
    _write_source_report(reports_dir / report_name, report)
    return report


def _build_overview(reports_dir: Path, source_map: dict[str, int]) -> dict:
    reports = []
    for path in sorted(reports_dir.glob('*.json')):
        if path.name == 'overview.json':
            continue
        reports.append(json.loads(path.read_text(encoding='utf-8')))

    total = len(source_map)
    silver_ok = sum(1 for r in reports if r['silver']['status'] == 'ok')
    local_ok = sum(1 for r in reports if r['local_tables']['status'] == 'ok')
    overall_ok = sum(1 for r in reports if r['overall_status'] == 'ok')

    failed = [
        {
            'source_id': r['source_id'],
            'source': r['source'],
            'silver_status': r['silver']['status'],
            'local_tables_status': r['local_tables']['status'],
            'silver_message': r['silver']['message'],
            'local_tables_message': r['local_tables']['message'],
        }
        for r in reports
        if r['overall_status'] != 'ok'
    ]

    missing_sources = sorted(
        [s for s in source_map if s not in {r['source'] for r in reports}]
    )

    overview = {
        'total_sources': total,
        'reported_sources': len(reports),
        'silver_ok': silver_ok,
        'silver_failed': len(reports) - silver_ok,
        'local_tables_ok': local_ok,
        'local_tables_failed_or_skipped': len(reports) - local_ok,
        'overall_ok': overall_ok,
        'overall_failed': len(reports) - overall_ok,
        'missing_reports': missing_sources,
        'failed_sources': failed,
    }

    (reports_dir / 'overview.json').write_text(
        json.dumps(overview, indent=2) + '\n', encoding='utf-8'
    )
    return overview


def _select_sources(all_sources: Iterable[str], requested: str | None) -> list[str]:
    source_list = sorted(set(all_sources))
    if not requested:
        return source_list

    requested_set = {s.strip() for s in requested.split(',') if s.strip()}
    unknown = sorted(requested_set - set(source_list))
    if unknown:
        raise ValueError(f'Unknown source(s): {", ".join(unknown)}')

    return [s for s in source_list if s in requested_set]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Parallel silver -> local_tables build with per-source reports.',
    )
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--inputs-package', type=str, default='pypath.inputs_v2')
    parser.add_argument('--sources', type=str, help='Comma-separated source filter')
    parser.add_argument('--test-mode', action='store_true')
    parser.add_argument('--build-dir', type=Path, default=Path('.build/parallel_until_local_tables'))
    parser.add_argument('--final-silver-root', type=Path, default=Path('omnipath_build/data/silver'))
    parser.add_argument('--final-gold-output-dir', type=Path, default=Path('omnipath_build/data/gold'))
    args = parser.parse_args(argv)

    discovered, _ = discover_resources(
        database_name='omnipath',
        base_path=None,
        inputs_package=args.inputs_package,
    )
    all_sources = sorted(discovered.keys())
    global_source_map = {source: i + 1 for i, source in enumerate(all_sources)}
    selected_sources = _select_sources(all_sources, args.sources)
    selected_source_map = {source: global_source_map[source] for source in selected_sources}

    reports_dir = args.build_dir / 'reports'
    stage_root = args.build_dir / 'stages'
    if reports_dir.exists():
        for old_report in reports_dir.glob('*.json'):
            old_report.unlink()
    reports_dir.mkdir(parents=True, exist_ok=True)
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)

    map_path = args.build_dir / 'source_map.tsv'
    lines = ['source_id\tsource', *[f'{sid}\t{src}' for src, sid in global_source_map.items()]]
    map_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print(f'Running {len(selected_sources)} source(s) with jobs={args.jobs}')
    print(f'Source map: {map_path}')
    print(f'Reports dir: {reports_dir}')

    futures = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as executor:
        for source in sorted(selected_sources):
            source_id = selected_source_map[source]
            futures.append(
                executor.submit(
                    _build_source_worker,
                    source=source,
                    source_id=source_id,
                    inputs_package=args.inputs_package,
                    test_mode=args.test_mode,
                    map_path=map_path,
                    stage_root=stage_root,
                    final_silver_root=args.final_silver_root,
                    final_gold_output_dir=args.final_gold_output_dir,
                    reports_dir=reports_dir,
                )
            )

        for future in as_completed(futures):
            report = future.result()
            print(
                f"[{report['source_id']:03d} {report['source']}] "
                f"silver={report['silver']['status']} local_tables={report['local_tables']['status']}"
            )

    overview = _build_overview(reports_dir, selected_source_map)
    print('')
    print('Overview:')
    print(f"  total={overview['total_sources']}")
    print(f"  silver_ok={overview['silver_ok']} silver_failed={overview['silver_failed']}")
    print(f"  local_tables_ok={overview['local_tables_ok']} local_tables_failed_or_skipped={overview['local_tables_failed_or_skipped']}")
    print(f"  overall_ok={overview['overall_ok']} overall_failed={overview['overall_failed']}")
    print(f"  file={reports_dir / 'overview.json'}")

    return 0 if overview['overall_failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
