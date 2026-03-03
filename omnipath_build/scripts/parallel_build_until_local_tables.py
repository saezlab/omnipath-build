#!/usr/bin/env python3
"""Parallel per-source silver -> local_tables pipeline with per-source reports."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from omnipath_build.loaders.silver import discover_resources


_PROGRESS_PREFIX = '__OMNIPATH_PROGRESS__'


@dataclass(slots=True)
class StepResult:
    status: str
    message: str
    seconds: float


def _update_progress_state(
    progress_state: dict[int, dict[str, Any]],
    progress_lock: threading.Lock,
    source_id: int,
    **fields: Any,
) -> None:
    with progress_lock:
        progress_state[source_id].update(fields)


def _render_progress_loop(
    progress_state: dict[int, dict[str, Any]],
    progress_lock: threading.Lock,
    stop_event: threading.Event,
    refresh_seconds: float = 0.2,
) -> None:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table

    console = Console()

    def _status_style(value: str) -> str:
        return {
            'ok': 'green',
            'running': 'yellow',
            'pending': 'dim',
            'skipped': 'cyan',
            'error': 'red',
            'failed': 'red',
        }.get(value, 'white')

    def _build_table() -> Table:
        table = Table(title='Parallel progress (silver -> local_tables)', expand=True)
        table.add_column('id', justify='right', width=4)
        table.add_column('source', overflow='fold')
        table.add_column('silver', justify='center', width=10)
        table.add_column('local', justify='center', width=10)
        table.add_column('stage', width=14)
        table.add_column('function', overflow='fold')
        table.add_column('records', justify='right', width=12)

        with progress_lock:
            rows = [progress_state[k].copy() for k in sorted(progress_state)]

        for row in rows:
            silver = f"[{_status_style(str(row['silver_status']))}]{row['silver_status']}[/]"
            local = f"[{_status_style(str(row['local_status']))}]{row['local_status']}[/]"
            table.add_row(
                f"{row['source_id']:03d}",
                str(row['source']),
                silver,
                local,
                str(row['stage']),
                str(row['function']),
                f"{int(row['records']):,}",
            )
        return table

    with Live(_build_table(), console=console, refresh_per_second=max(2, int(1 / refresh_seconds)), transient=True) as live:
        while not stop_event.is_set():
            live.update(_build_table(), refresh=True)
            time.sleep(refresh_seconds)



def _run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
) -> StepResult:
    start = time.time()
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    stdout_tail: deque[str] = deque(maxlen=8)
    stderr_tail: deque[str] = deque(maxlen=8)

    def _reader(stream, sink: deque[str], callback: Callable[[str], None] | None) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ''):
                stripped = line.rstrip('\n')
                sink.append(stripped)
                if callback is not None:
                    callback(stripped)
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=_reader,
        args=(proc.stdout, stdout_tail, on_stdout_line),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        args=(proc.stderr, stderr_tail, None),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    return_code = proc.wait()
    stdout_thread.join()
    stderr_thread.join()
    elapsed = time.time() - start

    if return_code == 0:
        return StepResult(status='ok', message='completed', seconds=elapsed)

    tail = '\n'.join([*stderr_tail, *stdout_tail]).strip()
    message = tail if tail else f'command failed with exit code {return_code}'
    return StepResult(status='error', message=message, seconds=elapsed)



def _write_source_report(report_path: Path, payload: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def _build_source_worker(
    *,
    source: str,
    source_id: int,
    inputs_package: str,
    test_mode: bool,
    stage_root: Path,
    reports_dir: Path,
    progress_state: dict[int, dict[str, Any]],
    progress_lock: threading.Lock,
) -> dict:
    started_at = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    _update_progress_state(
        progress_state,
        progress_lock,
        source_id,
        stage='silver',
        function='(starting)',
        records=0,
        silver_status='running',
    )

    source_slug = source.replace('.', '__')
    source_stage_dir = stage_root / f'{source_id:03d}__{source_slug}'
    if source_stage_dir.exists():
        shutil.rmtree(source_stage_dir)
    source_stage_dir.mkdir(parents=True, exist_ok=True)

    silver_root = source_stage_dir
    gold_root = source_stage_dir / 'gold'

    silver_cmd = [
        sys.executable,
        '-m',
        'omnipath_build.cli.commands',
        'silver',
        '--base-path',
        str(silver_root),
        '--database',
        '.',
        '--source',
        source,
        '--inputs-package',
        inputs_package,
    ]
    if test_mode:
        silver_cmd.append('--test-mode')

    silver_function_records: dict[str, int] = {}

    def _silver_progress_handler(line: str) -> None:
        if not line.startswith(_PROGRESS_PREFIX):
            return
        try:
            payload = json.loads(line[len(_PROGRESS_PREFIX):])
        except json.JSONDecodeError:
            return

        function = str(payload.get('function', 'unknown'))
        output = payload.get('output')
        records = int(payload.get('records', 0))
        key = f'{function}:{output}' if output else function

        silver_function_records[key] = records
        _update_progress_state(
            progress_state,
            progress_lock,
            source_id,
            stage='silver',
            function=key,
            records=records,
            silver_status='running',
        )

    env = dict(os.environ)
    env['OMNIPATH_PROGRESS_STDOUT'] = '1'

    silver_result = _run_command(
        silver_cmd,
        env=env,
        on_stdout_line=_silver_progress_handler,
    )

    _update_progress_state(
        progress_state,
        progress_lock,
        source_id,
        silver_status=silver_result.status,
        stage='local_tables' if silver_result.status == 'ok' else 'done',
        function='(waiting)' if silver_result.status == 'ok' else '(failed)',
    )

    local_result = StepResult(status='skipped', message='silver failed', seconds=0.0)

    if silver_result.status == 'ok':
        # Flatten single-source silver layout: silver/<source>/*.parquet -> silver/*.parquet
        source_leaf = source.split('.')[-1]
        source_silver_dir = silver_root / 'silver' / source_leaf
        if source_silver_dir.exists():
            for parquet in sorted(source_silver_dir.glob('*.parquet')):
                target = silver_root / 'silver' / parquet.name
                if target.exists():
                    target.unlink()
                shutil.move(str(parquet), str(target))
            source_silver_dir.rmdir()

        local_cmd = [
            sys.executable,
            '-m',
            'omnipath_build.cli.commands',
            'gold',
            '--data-root',
            str(silver_root / 'silver'),
            '--output-dir',
            str(gold_root),
            '--step',
            'local_tables',
            '--source',
            source,
        ]
        _update_progress_state(
            progress_state,
            progress_lock,
            source_id,
            stage='local_tables',
            function='(running)',
            local_status='running',
        )
        local_result = _run_command(local_cmd)


    _update_progress_state(
        progress_state,
        progress_lock,
        source_id,
        local_status=local_result.status,
        stage='done',
        function='(complete)' if local_result.status == 'ok' else '(failed)',
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
            'function_records': dict(sorted(silver_function_records.items())),
        },
        'local_tables': {
            'status': local_result.status,
            'message': local_result.message,
            'seconds': round(local_result.seconds, 3),
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

    progress_lock = threading.Lock()
    progress_state: dict[int, dict[str, Any]] = {
        selected_source_map[source]: {
            'source_id': selected_source_map[source],
            'source': source,
            'silver_status': 'pending',
            'local_status': 'pending',
            'stage': 'queued',
            'function': '-',
            'records': 0,
        }
        for source in sorted(selected_sources)
    }

    reports_dir = args.build_dir / 'reports'
    stage_root = args.build_dir
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f'Running {len(selected_sources)} source(s) with jobs={args.jobs}')
    print(f'Reports dir: {reports_dir}')

    render_enabled = sys.stdout.isatty()
    stop_event = threading.Event()
    render_thread: threading.Thread | None = None
    if render_enabled:
        render_thread = threading.Thread(
            target=_render_progress_loop,
            args=(progress_state, progress_lock, stop_event),
            daemon=True,
        )
        render_thread.start()

    futures = []
    completed_reports: list[dict[str, Any]] = []
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
                    stage_root=stage_root,
                    reports_dir=reports_dir,
                    progress_state=progress_state,
                    progress_lock=progress_lock,
                )
            )

        for future in as_completed(futures):
            report = future.result()
            completed_reports.append(report)

    if render_enabled:
        stop_event.set()
        if render_thread is not None:
            render_thread.join(timeout=1.0)
        for report in sorted(completed_reports, key=lambda r: r['source_id']):
            print(
                f"[{report['source_id']:03d} {report['source']}] "
                f"silver={report['silver']['status']} local_tables={report['local_tables']['status']}"
            )
    else:
        for report in sorted(completed_reports, key=lambda r: r['source_id']):
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
