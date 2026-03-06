#!/usr/bin/env python3
"""Core DAG planning, fingerprinting, and orchestration."""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

INDEX_IMPORT_STATE_PATH = Path('data/reports/state/index_import_latest.json')

from omnipath_build.loaders.silver import discover_resources
from omnipath_build.pipeline.io_state import (
    compute_output_hash,
    ensure_output_exists,
    load_latest_state,
    publish_navigation_view,
    store_artifact,
    task_state_entry,
    write_latest_state,
    write_output_snapshot,
    write_reports_and_changelog,
)
from omnipath_build.pipeline.task_impl import execute_task


@dataclass(slots=True)
class TaskDef:
    key: str
    task_type: str
    source: str | None
    deps: list[str]


@dataclass(slots=True)
class TaskResult:
    task_key: str
    task_type: str
    source: str | None
    fingerprint: str
    output_ref: str
    output_hash: str
    status: str
    deps: list[str]
    error: dict[str, Any] | None = None


@dataclass(slots=True)
class TaskRunOutcome:
    result: TaskResult
    log_line: str


class _ProgressTracker:
    def __init__(self, tasks: list[TaskDef], mode: str) -> None:
        requested = mode
        if requested == 'auto':
            requested = 'rich' if sys.stdout.isatty() else 'plain'

        self._mode = requested if requested in {'rich', 'plain'} else 'plain'
        self._enabled = self._mode == 'rich'
        self._available = False

        self._state: dict[str, dict[str, Any]] = {
            task.key: {
                'key': task.key,
                'type': task.task_type,
                'source': task.source,
                'status': 'pending',
                'message': '',
                'seconds': 0.0,
                'function': '-',
                'records': 0,
            }
            for task in tasks
        }
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        if self._enabled:
            try:
                import rich  # noqa: F401

                self._available = True
            except Exception:
                self._enabled = False
                self._mode = 'plain'

    @property
    def is_plain(self) -> bool:
        return self._mode == 'plain'

    def start(self) -> None:
        if not (self._enabled and self._available):
            return
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not (self._enabled and self._available):
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def set_running(self, task: TaskDef, message: str = '') -> None:
        with self._lock:
            self._state[task.key]['status'] = 'running'
            self._state[task.key]['message'] = message

    def set_done(self, task: TaskDef, status: str, seconds: float, message: str) -> None:
        with self._lock:
            self._state[task.key]['status'] = status
            self._state[task.key]['seconds'] = round(seconds, 2)
            self._state[task.key]['message'] = message

    def set_silver_progress(self, task: TaskDef, function: str, records: int) -> None:
        with self._lock:
            self._state[task.key]['status'] = 'running'
            self._state[task.key]['function'] = function
            self._state[task.key]['records'] = int(records)

    def _status_style(self, value: str) -> str:
        return {
            'pending': 'dim',
            'running': 'yellow',
            'executed': 'green',
            'reused': 'cyan',
            'reused_on_error': 'magenta',
            'failed': 'red',
        }.get(value, 'white')

    def _render_table(self):
        from rich.console import Group
        from rich.table import Table

        with self._lock:
            rows = {k: self._state[k].copy() for k in sorted(self._state)}

        status_counts: dict[str, int] = {}
        for row in rows.values():
            status = str(row['status'])
            status_counts[status] = status_counts.get(status, 0) + 1
        summary = ', '.join(f'{k}={v}' for k, v in sorted(status_counts.items()))

        per_source_table = Table(title='Per-source progress', expand=True)
        per_source_table.caption = f'tasks={len(rows)} | {summary}'
        per_source_table.add_column('source')
        per_source_table.add_column('step', width=14)
        per_source_table.add_column('status', width=16)
        per_source_table.add_column('function', overflow='fold')
        per_source_table.add_column('records', justify='right', width=12)
        per_source_table.add_column('sec', justify='right', width=8)

        terminal = {'executed', 'reused', 'reused_on_error', 'failed'}
        sources = sorted(
            {
                str(r['source'])
                for r in rows.values()
                if r.get('source') and r['type'] in {'freshness_scan', 'silver', 'local_gold'}
            }
        )
        for source in sources:
            freshness = rows.get(f'freshness_scan:{source}', {'status': 'pending', 'seconds': 0.0})
            silver = rows.get(
                f'silver:{source}',
                {'status': 'pending', 'function': '-', 'records': 0, 'seconds': 0.0},
            )
            local = rows.get(f'local_gold:{source}', {'status': 'pending', 'seconds': 0.0})

            freshness_status = str(freshness.get('status', 'pending'))
            silver_status = str(silver.get('status', 'pending'))
            local_status = str(local.get('status', 'pending'))

            step = 'queued'
            status = 'pending'
            function = '-'
            records = 0
            seconds = 0.0

            if freshness_status == 'running':
                step = 'freshness'
                status = freshness_status
                seconds = float(freshness.get('seconds', 0.0) or 0.0)
            elif silver_status == 'running':
                step = 'silver'
                status = silver_status
                function = str(silver.get('function', '-'))
                records = int(silver.get('records', 0) or 0)
                seconds = float(silver.get('seconds', 0.0) or 0.0)
            elif local_status == 'running':
                step = 'local_gold'
                status = local_status
                seconds = float(local.get('seconds', 0.0) or 0.0)
            elif local_status in terminal:
                step = 'done'
                status = local_status
                seconds = float(local.get('seconds', 0.0) or 0.0)
            elif silver_status in terminal:
                step = 'local_gold'
                status = 'pending'
                seconds = float(silver.get('seconds', 0.0) or 0.0)
            elif freshness_status in terminal:
                step = 'silver'
                status = 'pending'
                seconds = float(freshness.get('seconds', 0.0) or 0.0)

            per_source_table.add_row(
                source,
                step,
                f"[{self._status_style(status)}]{status}[/]",
                function,
                f'{records:,}',
                f'{seconds:.1f}',
            )

        global_table = Table(title='Global tasks', expand=True)
        global_table.add_column('task', overflow='fold')
        global_table.add_column('status', width=14)
        global_table.add_column('sec', justify='right', width=8)
        global_table.add_column('info', overflow='fold')

        global_keys = [
            key
            for key, row in rows.items()
            if not (row.get('source') and row['type'] in {'freshness_scan', 'silver', 'local_gold'})
        ]
        for key in global_keys:
            row = rows[key]
            status_value = str(row['status'])
            global_table.add_row(
                key,
                f"[{self._status_style(status_value)}]{status_value}[/]",
                f"{float(row['seconds']):.1f}",
                str(row['message']),
            )

        return Group(per_source_table, global_table)

    def _render_loop(self) -> None:
        from rich.console import Console
        from rich.live import Live

        console = Console()
        with Live(self._render_table(), console=console, refresh_per_second=5, transient=True) as live:
            while not self._stop_event.is_set():
                live.update(self._render_table(), refresh=True)
                time.sleep(0.2)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace('+00:00', 'Z')


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def canonical_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return sha256_bytes(encoded)


def git_commit_hash() -> str:
    result = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def runtime_hashes(project_root: Path) -> dict[str, str]:
    uv_lock = project_root / 'uv.lock'
    if not uv_lock.exists():
        raise FileNotFoundError(f'Missing runtime lockfile: {uv_lock}')
    return {
        'uv_lock_sha256': sha256_file(uv_lock),
        'git_commit': git_commit_hash(),
    }


def hash_paths(paths: list[Path]) -> str:
    file_hashes = []
    for path in sorted(paths):
        file_hashes.append({'path': str(path), 'sha256': sha256_file(path)})
    return canonical_json_hash({'files': file_hashes})


def module_tree_files(module_name: str) -> list[Path]:
    module = importlib.import_module(module_name)
    files: list[Path] = []

    module_file = getattr(module, '__file__', None)
    if module_file:
        files.append(Path(module_file))

    module_path = getattr(module, '__path__', None)
    if module_path:
        for info in pkgutil.walk_packages(module_path, f'{module_name}.'):
            submodule = importlib.import_module(info.name)
            subfile = getattr(submodule, '__file__', None)
            if subfile:
                files.append(Path(subfile))

    existing = [p for p in files if p.exists()]
    if not existing:
        raise FileNotFoundError(f'No module files found for {module_name}')
    return existing


def discover_sources(inputs_package: str) -> list[str]:
    discovered, _ = discover_resources(
        database_name='omnipath',
        base_path=None,
        inputs_package=inputs_package,
    )
    return sorted(discovered.keys())


def build_dag(sources: list[str]) -> list[TaskDef]:
    tasks: list[TaskDef] = []

    for source in sources:
        tasks.append(TaskDef(f'freshness_scan:{source}', 'freshness_scan', source, []))
        tasks.append(TaskDef(f'silver:{source}', 'silver', source, [f'freshness_scan:{source}']))
        tasks.append(TaskDef(f'local_gold:{source}', 'local_gold', source, [f'silver:{source}']))

    tasks.append(TaskDef('combined_gold', 'combined_gold', None, [f'local_gold:{s}' for s in sources]))
    tasks.append(TaskDef('search_entities', 'search_entities', None, ['combined_gold']))
    tasks.append(TaskDef('search_interactions', 'search_interactions', None, ['combined_gold']))
    tasks.append(TaskDef('search_associations', 'search_associations', None, ['combined_gold']))
    tasks.append(TaskDef('search_sources', 'search_sources', None, ['combined_gold']))

    tasks.append(TaskDef('index_import:entities', 'index_import', 'entities', ['search_entities']))
    tasks.append(TaskDef('index_import:interactions', 'index_import', 'interactions', ['search_interactions']))
    tasks.append(TaskDef('index_import:associations', 'index_import', 'associations', ['search_associations']))
    tasks.append(TaskDef('index_import:sources', 'index_import', 'sources', ['search_sources']))

    return tasks


def fingerprint_for_task(
    *,
    task: TaskDef,
    dep_output_hashes: list[str],
    runtime_hash_values: dict[str, str],
    inputs_package: str,
    test_mode: bool,
    run_freshness_checks: bool,
    full_reindex: bool,
) -> str:
    code_hashes: dict[str, str] = {}
    config_hashes: dict[str, str] = {}
    params: dict[str, Any] = {}
    freshness_inputs: dict[str, Any] = {}

    if task.task_type == 'freshness_scan':
        assert task.source is not None
        code_hashes['freshness_impl'] = hash_paths(module_tree_files('download_manager'))
        code_hashes['pipeline'] = hash_paths([Path(__file__).resolve()])
        params['source'] = task.source
        params['inputs_package'] = inputs_package
        params['run_freshness_checks'] = run_freshness_checks
    elif task.task_type == 'silver':
        assert task.source is not None
        code_hashes['source_module'] = hash_paths(module_tree_files(f'{inputs_package}.{task.source}'))
        code_hashes['silver_loader'] = hash_paths(module_tree_files('omnipath_build.loaders.silver'))
        params['source'] = task.source
        params['inputs_package'] = inputs_package
        params['test_mode'] = test_mode
    elif task.task_type == 'local_gold':
        code_hashes['local_builder'] = hash_paths(module_tree_files('omnipath_build.gold.build_local_tables'))
        params['source'] = task.source
    elif task.task_type == 'combined_gold':
        code_hashes['entity_identifiers_builder'] = hash_paths(module_tree_files('omnipath_build.gold.build_entity_identifiers_v2'))
        code_hashes['global_tables_builder'] = hash_paths(module_tree_files('omnipath_build.gold.build_global_tables'))
        code_hashes['cv_terms'] = hash_paths(module_tree_files('pypath.internals.cv_terms'))
    elif task.task_type == 'search_entities':
        code_hashes['builder'] = hash_paths(module_tree_files('omnipath_build.search_builder.build_search_entities'))
    elif task.task_type == 'search_interactions':
        code_hashes['builder'] = hash_paths(module_tree_files('omnipath_build.search_builder.build_search_interactions'))
    elif task.task_type == 'search_associations':
        code_hashes['builder'] = hash_paths(module_tree_files('omnipath_build.search_builder.build_search_associations'))
    elif task.task_type == 'search_sources':
        code_hashes['builder'] = hash_paths(module_tree_files('omnipath_build.search_builder.build_sources'))
    elif task.task_type == 'index_import':
        code_hashes['importer'] = hash_paths(module_tree_files('omnipath_build.search.importer'))
        params['dataset'] = task.source
        params['full_reindex'] = full_reindex
    else:
        raise ValueError(f'Unsupported task type: {task.task_type}')

    payload = {
        'task_key': task.key,
        'task_type': task.task_type,
        'params': params,
        'code_hashes': code_hashes,
        'config_hashes': config_hashes,
        'dep_artifact_hashes': dep_output_hashes,
        'runtime_hashes': runtime_hash_values,
        'freshness_inputs': freshness_inputs,
    }
    return canonical_json_hash(payload)


def task_output_suffix(task: TaskDef) -> str:
    if task.task_type in {'freshness_scan', 'index_import'}:
        return '.json'
    if task.task_type in {
        'search_entities',
        'search_interactions',
        'search_associations',
        'search_sources',
    }:
        return '.parquet'
    return ''


def _execute_one_task(
    *,
    task: TaskDef,
    task_results: dict[str, TaskResult],
    previous_state: dict[str, Any] | None,
    previous_tasks: dict[str, dict[str, Any]],
    project_root: Path,
    sources: list[str],
    runtime_hash_values: dict[str, str],
    inputs_package: str,
    test_mode: bool,
    run_freshness_checks: bool,
    full_reindex: bool,
    progress: _ProgressTracker,
    task_log_dir: Path,
) -> TaskRunOutcome:
    start = time.time()
    safe_key = task.key.replace(':', '__').replace('/', '__')
    log_path = task_log_dir / f'{safe_key}.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    progress.set_running(task, message='computing fingerprint')

    dep_hashes = [task_results[d].output_hash for d in task.deps]
    fingerprint = fingerprint_for_task(
        task=task,
        dep_output_hashes=dep_hashes,
        runtime_hash_values=runtime_hash_values,
        inputs_package=inputs_package,
        test_mode=test_mode,
        run_freshness_checks=run_freshness_checks,
        full_reindex=full_reindex,
    )

    prev = previous_tasks.get(task.key)
    if prev and prev.get('fingerprint') == fingerprint:
        output_ref = prev['output_ref']
        output_path = ensure_output_exists(output_ref)
        result = TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            fingerprint=fingerprint,
            output_ref=output_ref,
            output_hash=compute_output_hash(output_path),
            status='reused',
            deps=task.deps,
        )
        publish_navigation_view(task, output_ref)
        elapsed = time.time() - start
        line = f'[TASK] {task.key} -> reused ({output_ref})'
        progress.set_done(task, 'reused', elapsed, 'reused')
        return TaskRunOutcome(result=result, log_line=line)

    try:
        with tempfile.TemporaryDirectory(prefix='op-task-') as tmp:
            tmp_base = Path(tmp)
            suffix = task_output_suffix(task)
            tmp_output = tmp_base / (f'out{suffix}' if suffix else 'out')

            progress.set_running(task, message='executing')

            def _task_progress(event: dict[str, Any]) -> None:
                if task.task_type == 'silver' and event.get('stage') == 'silver':
                    function = str(event.get('function', 'unknown'))
                    records = int(event.get('records', 0))
                    progress.set_silver_progress(task, function=function, records=records)

            execute_task(
                task=task,
                tmp_output=tmp_output,
                project_root=project_root,
                sources=sources,
                task_results=task_results,
                previous_state=previous_state,
                inputs_package=inputs_package,
                test_mode=test_mode,
                skip_index_import=False,
                run_freshness_checks=run_freshness_checks,
                full_reindex=full_reindex,
                log_path=log_path,
                progress_callback=_task_progress,
            )

            if suffix and not tmp_output.exists():
                raise FileNotFoundError(f'Task produced no output file: {task.key}')
            if not suffix and not tmp_output.exists():
                raise FileNotFoundError(f'Task produced no output directory: {task.key}')

            tmp_hash = compute_output_hash(tmp_output)

            prev_output_ref = prev.get('output_ref') if prev else None
            if prev_output_ref:
                prev_output_path = ensure_output_exists(prev_output_ref)
                prev_hash = prev.get('output_hash') or compute_output_hash(prev_output_path)
                if prev_hash == tmp_hash:
                    result = TaskResult(
                        task_key=task.key,
                        task_type=task.task_type,
                        source=task.source,
                        fingerprint=fingerprint,
                        output_ref=prev_output_ref,
                        output_hash=prev_hash,
                        status='reused',
                        deps=task.deps,
                    )
                    publish_navigation_view(task, prev_output_ref)
                    elapsed = time.time() - start
                    line = f'[TASK] {task.key} -> unchanged, reused ({prev_output_ref})'
                    progress.set_done(task, 'reused', elapsed, 'unchanged')
                    return TaskRunOutcome(result=result, log_line=line)

            output_ref = store_artifact(task, tmp_output, tmp_hash, suffix)
            result = TaskResult(
                task_key=task.key,
                task_type=task.task_type,
                source=task.source,
                fingerprint=fingerprint,
                output_ref=output_ref,
                output_hash=tmp_hash,
                status='executed',
                deps=task.deps,
            )
            publish_navigation_view(task, output_ref)
            elapsed = time.time() - start
            line = f'[TASK] {task.key} -> executed ({output_ref})'
            progress.set_done(task, 'executed', elapsed, 'executed')
            return TaskRunOutcome(result=result, log_line=line)

    except Exception as exc:
        reusable_on_error = task.task_type in {'freshness_scan', 'silver', 'local_gold'}
        if reusable_on_error and task.source and prev and prev.get('output_ref'):
            output_ref = prev['output_ref']
            out_path = ensure_output_exists(output_ref)
            result = TaskResult(
                task_key=task.key,
                task_type=task.task_type,
                source=task.source,
                fingerprint=fingerprint,
                output_ref=output_ref,
                output_hash=compute_output_hash(out_path),
                status='reused_on_error',
                deps=task.deps,
                error={'type': type(exc).__name__, 'message': str(exc)},
            )
            publish_navigation_view(task, output_ref)
            elapsed = time.time() - start
            line = f'[TASK] {task.key} -> reused_on_error ({output_ref}): {type(exc).__name__}: {exc}'
            progress.set_done(task, 'reused_on_error', elapsed, str(exc))
            return TaskRunOutcome(result=result, log_line=line)

        progress.set_done(task, 'failed', time.time() - start, f'{type(exc).__name__}: {exc}')
        raise


def _run_task_dag(
    *,
    tasks: list[TaskDef],
    task_results: dict[str, TaskResult],
    previous_state: dict[str, Any] | None,
    previous_tasks: dict[str, dict[str, Any]],
    project_root: Path,
    sources: list[str],
    runtime_hash_values: dict[str, str],
    inputs_package: str,
    test_mode: bool,
    run_freshness_checks: bool,
    full_reindex: bool,
    jobs: int,
    progress: _ProgressTracker,
    task_log_dir: Path,
) -> None:
    if not tasks:
        return

    max_workers = min(len(tasks), max(1, jobs))
    task_by_key = {task.key: task for task in tasks}
    pending_deps: dict[str, int] = {}
    dependents: dict[str, list[str]] = {task.key: [] for task in tasks}

    for task in tasks:
        unresolved = 0
        for dep in task.deps:
            if dep in task_results:
                continue
            if dep in task_by_key:
                unresolved += 1
                dependents.setdefault(dep, []).append(task.key)
                continue
            raise ValueError(f'Task {task.key} has missing dependency: {dep}')
        pending_deps[task.key] = unresolved

    ready: deque[str] = deque(sorted(k for k, count in pending_deps.items() if count == 0))

    def _submit_task(task: TaskDef) -> TaskRunOutcome:
        snapshot = dict(task_results)
        return _execute_one_task(
            task=task,
            task_results=snapshot,
            previous_state=previous_state,
            previous_tasks=previous_tasks,
            project_root=project_root,
            sources=sources,
            runtime_hash_values=runtime_hash_values,
            inputs_package=inputs_package,
            test_mode=test_mode,
            run_freshness_checks=run_freshness_checks,
            full_reindex=full_reindex,
            progress=progress,
            task_log_dir=task_log_dir,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        running: dict[Any, TaskDef] = {}

        def _drain_ready() -> None:
            while ready and len(running) < max_workers:
                key = ready.popleft()
                task = task_by_key[key]
                future = pool.submit(_submit_task, task)
                running[future] = task

        _drain_ready()

        while running:
            done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                task = running.pop(future)
                try:
                    outcome = future.result()
                except Exception:
                    for pending_future in running:
                        pending_future.cancel()
                    raise

                task_results[outcome.result.task_key] = outcome.result
                if progress.is_plain:
                    print(outcome.log_line)

                for dependent_key in dependents.get(task.key, []):
                    pending_deps[dependent_key] -= 1
                    if pending_deps[dependent_key] == 0:
                        ready.append(dependent_key)

            _drain_ready()

    completed_now = {task.key for task in tasks if task.key in task_results}
    if len(completed_now) != len(tasks):
        missing = sorted(set(task_by_key) - completed_now)
        raise RuntimeError(f'DAG execution incomplete; unresolved tasks: {", ".join(missing)}')


def run_pipeline(
    *,
    project_root: Path,
    inputs_package: str,
    test_mode: bool,
    run_freshness_checks: bool,
    full_reindex: bool,
    jobs: int = 4,
    progress_mode: str = 'rich',
    include_index_import: bool = False,
) -> dict[str, Any]:
    run_id = utc_now().strftime('run-%Y%m%d-%H%M%S')
    task_log_dir = Path('data/reports/task_logs') / run_id
    runtime_hash_values = runtime_hashes(project_root)

    sources = discover_sources(inputs_package)
    tasks = build_dag(sources)
    if not include_index_import:
        tasks = [t for t in tasks if t.task_type != 'index_import']

    previous_state = load_latest_state()
    previous_tasks = (previous_state or {}).get('tasks', {})

    task_results: dict[str, TaskResult] = {}

    progress = _ProgressTracker(tasks=tasks, mode=progress_mode)
    progress.start()
    if progress.is_plain:
        print(
            f'Running DAG with sources={len(sources)} tasks={len(tasks)} jobs={max(1, jobs)}'
        )

    try:
        _run_task_dag(
            tasks=tasks,
            task_results=task_results,
            previous_state=previous_state,
            previous_tasks=previous_tasks,
            project_root=project_root,
            sources=sources,
            runtime_hash_values=runtime_hash_values,
            inputs_package=inputs_package,
            test_mode=test_mode,
            run_freshness_checks=run_freshness_checks,
            full_reindex=full_reindex,
            jobs=max(1, jobs),
            progress=progress,
            task_log_dir=task_log_dir,
        )
    finally:
        progress.stop()

    created_at = iso(utc_now())
    write_reports_and_changelog(
        run_id=run_id,
        created_at=created_at,
        sources=sources,
        tasks=tasks,
        task_results=task_results,
        previous_state=previous_state,
    )
    write_output_snapshot(run_id=run_id, task_results=task_results)

    state = {
        'run_id': run_id,
        'created_at': created_at,
        'base_run_id': previous_state.get('run_id') if previous_state else None,
        'runtime': runtime_hash_values,
        'sources': sources,
        'tasks': {task.key: task_state_entry(task_results[task.key]) for task in tasks},
    }
    write_latest_state(state)
    return state


def _load_index_import_state() -> dict[str, Any] | None:
    if not INDEX_IMPORT_STATE_PATH.exists():
        return None
    return json.loads(INDEX_IMPORT_STATE_PATH.read_text(encoding='utf-8'))


def _write_index_import_state(state: dict[str, Any]) -> None:
    INDEX_IMPORT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_IMPORT_STATE_PATH.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')


def run_index_imports(
    *,
    project_root: Path,
    jobs: int = 4,
    full_reindex: bool = False,
    progress_mode: str = 'rich',
) -> dict[str, Any]:
    run_id = utc_now().strftime('index-%Y%m%d-%H%M%S')
    task_log_dir = Path('data/reports/task_logs') / run_id

    data_state = load_latest_state()
    if data_state is None:
        raise RuntimeError('No pipeline state found. Run the data pipeline first.')

    required_search = [
        'search_entities',
        'search_interactions',
        'search_associations',
        'search_sources',
    ]
    for key in required_search:
        if key not in data_state.get('tasks', {}):
            raise RuntimeError(f'Missing required task in latest data state: {key}')

    prev_index_state = _load_index_import_state() or {}
    previous_tasks = prev_index_state.get('tasks', {})

    synthetic_previous_for_execute = {'tasks': {}}
    prev_search_outputs = prev_index_state.get('search_outputs', {})
    for key in required_search:
        current_search = data_state['tasks'][key]
        synthetic_previous_for_execute['tasks'][key] = {
            'output_ref': prev_search_outputs.get(key, current_search['output_ref'])
        }

    index_tasks = [
        TaskDef('index_import:entities', 'index_import', 'entities', ['search_entities']),
        TaskDef('index_import:interactions', 'index_import', 'interactions', ['search_interactions']),
        TaskDef('index_import:associations', 'index_import', 'associations', ['search_associations']),
        TaskDef('index_import:sources', 'index_import', 'sources', ['search_sources']),
    ]

    task_results: dict[str, TaskResult] = {}
    for key in required_search:
        entry = data_state['tasks'][key]
        output_ref = entry['output_ref']
        output_path = ensure_output_exists(output_ref)
        task_results[key] = TaskResult(
            task_key=key,
            task_type=entry.get('task_type', key),
            source=entry.get('source'),
            fingerprint=entry.get('fingerprint', ''),
            output_ref=output_ref,
            output_hash=entry.get('output_hash') or compute_output_hash(output_path),
            status='reused',
            deps=entry.get('deps', []),
        )

    progress = _ProgressTracker(tasks=index_tasks, mode=progress_mode)
    progress.start()
    if progress.is_plain:
        print(f'Running index import tasks={len(index_tasks)} jobs={max(1, jobs)}')

    runtime_hash_values = runtime_hashes(project_root)
    try:
        _run_task_dag(
            tasks=index_tasks,
            task_results=task_results,
            previous_state=synthetic_previous_for_execute,
            previous_tasks=previous_tasks,
            project_root=project_root,
            sources=data_state.get('sources', []),
            runtime_hash_values=runtime_hash_values,
            inputs_package='pypath.inputs_v2',
            test_mode=False,
            run_freshness_checks=False,
            full_reindex=full_reindex,
            jobs=max(1, jobs),
            progress=progress,
            task_log_dir=task_log_dir,
        )
    finally:
        progress.stop()

    created_at = iso(utc_now())
    state = {
        'run_id': run_id,
        'created_at': created_at,
        'runtime': runtime_hash_values,
        'search_outputs': {k: data_state['tasks'][k]['output_ref'] for k in required_search},
        'tasks': {t.key: task_state_entry(task_results[t.key]) for t in index_tasks},
    }
    _write_index_import_state(state)
    return state
