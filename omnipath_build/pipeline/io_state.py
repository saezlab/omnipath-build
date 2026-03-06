"""State, artifact, reporting, and materialized-output IO helpers."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import hashlib

if TYPE_CHECKING:
    from omnipath_build.pipeline.dag_core import TaskDef, TaskResult


DATA_ROOT = Path('data')
ARTIFACTS_ROOT = DATA_ROOT / 'artifacts'
BUILD_ROOT = DATA_ROOT / 'build'
REPORTS_ROOT = DATA_ROOT / 'reports'
STATE_ROOT = REPORTS_ROOT / 'state'
REPORTS_RUNS_ROOT = REPORTS_ROOT / 'runs'
REPORTS_CHANGELOG_PATH = REPORTS_ROOT / 'changelog.ndjson'
OUTPUT_ROOT = DATA_ROOT / 'output'


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace('+00:00', 'Z')


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return _sha256_bytes(encoded)


def resolve_output_ref(output_ref: str) -> Path:
    return DATA_ROOT / output_ref


def _make_output_ref(path: Path) -> str:
    return path.relative_to(DATA_ROOT).as_posix()


def ensure_output_exists(output_ref: str) -> Path:
    path = resolve_output_ref(output_ref)
    if not path.exists():
        raise FileNotFoundError(f'Missing output referenced by state: {path}')
    return path


def _output_entries(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        return [{'path': '__file__', 'sha256': _sha256_file(path), 'size': path.stat().st_size}]

    entries: list[dict[str, Any]] = []
    for p in sorted(path.rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(path).as_posix()
        entries.append({'path': rel, 'sha256': _sha256_file(p), 'size': p.stat().st_size})

    if not entries:
        raise ValueError(f'Output directory contains no files: {path}')
    return entries


def compute_output_hash(path: Path) -> str:
    return _canonical_json_hash({'files': _output_entries(path)})


def store_artifact(task: TaskDef, tmp_output: Path, output_hash: str, suffix: str) -> str:
    artifact_root = ARTIFACTS_ROOT / output_hash
    files_root = artifact_root / 'files'
    target = files_root / f'output{suffix}' if suffix else files_root

    if not artifact_root.exists():
        files_root.mkdir(parents=True, exist_ok=True)
        if suffix:
            shutil.copy2(tmp_output, target)
        else:
            shutil.copytree(tmp_output, target, dirs_exist_ok=True)

        metadata = {
            'artifact_hash': output_hash,
            'task_type': task.task_type,
            'task_key': task.key,
            'source': task.source,
            'created_at': _iso(_utc_now()),
            'is_file': bool(suffix),
            'files': _output_entries(target),
        }
        (artifact_root / 'metadata.json').write_text(
            json.dumps(metadata, indent=2) + '\n',
            encoding='utf-8',
        )

    return _make_output_ref(target)


def _task_navigation_path(task: TaskDef) -> Path | None:
    if task.task_type == 'silver':
        assert task.source is not None
        return BUILD_ROOT / 'per_source' / task.source / 'silver'
    if task.task_type == 'local_gold':
        assert task.source is not None
        return BUILD_ROOT / 'per_source' / task.source / 'gold'
    if task.task_type == 'combined_gold':
        return BUILD_ROOT / 'combined' / 'gold'
    if task.task_type in {'search_entities', 'search_interactions', 'search_associations', 'search_sources'}:
        dataset = task.task_type.split('_', 1)[1]
        return BUILD_ROOT / 'combined' / 'search' / dataset / f'search_{dataset}.parquet'
    return None


def _safe_remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.exists() and path.is_dir():
        shutil.rmtree(path)


def _ensure_symlink(link_path: Path, target_path: Path) -> None:
    if link_path.is_symlink() and link_path.resolve() == target_path.resolve():
        return

    _safe_remove_path(link_path)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    relative_target = os.path.relpath(target_path, start=link_path.parent)
    os.symlink(relative_target, link_path)


def publish_navigation_view(task: TaskDef, output_ref: str) -> None:
    nav_path = _task_navigation_path(task)
    if nav_path is None:
        return
    target = resolve_output_ref(output_ref)
    _ensure_symlink(nav_path, target)


def load_latest_state() -> dict[str, Any] | None:
    latest = STATE_ROOT / 'latest.json'
    if not latest.exists():
        return None
    payload = json.loads(latest.read_text(encoding='utf-8'))
    if 'tasks' not in payload:
        raise ValueError('Invalid latest state (missing tasks)')
    return payload


def task_state_entry(result: TaskResult) -> dict[str, Any]:
    entry = {
        'task_type': result.task_type,
        'source': result.source,
        'fingerprint': result.fingerprint,
        'output_ref': result.output_ref,
        'output_hash': result.output_hash,
        'deps': result.deps,
    }
    if result.error:
        entry['error'] = result.error
    return entry


def write_reports_and_changelog(
    *,
    run_id: str,
    created_at: str,
    sources: list[str],
    tasks: list[TaskDef],
    task_results: dict[str, TaskResult],
    previous_state: dict[str, Any] | None,
) -> list[str]:
    previous_tasks = (previous_state or {}).get('tasks', {})

    changed_tasks: list[str] = []
    for task in tasks:
        previous = previous_tasks.get(task.key)
        current = task_state_entry(task_results[task.key])
        if previous is None:
            changed_tasks.append(task.key)
            continue

        previous_error = previous.get('error')
        current_error = current.get('error')
        materially_changed = (
            previous.get('output_hash') != current.get('output_hash')
            or previous.get('output_ref') != current.get('output_ref')
            or previous_error != current_error
        )
        if materially_changed:
            changed_tasks.append(task.key)

    freshness_report: dict[str, Any] = {}
    for source in sources:
        fres = task_results[f'freshness_scan:{source}']
        if fres.status == 'reused_on_error':
            freshness_report[source] = {'status': 'error_reused', 'error': fres.error}
            continue
        payload = json.loads(resolve_output_ref(fres.output_ref).read_text(encoding='utf-8'))
        freshness_report[source] = {
            'status': payload.get('status'),
            'method': payload.get('method'),
            'resources': payload.get('resources', []),
        }

    per_source: dict[str, Any] = {}
    for source in sources:
        local = task_results[f'local_gold:{source}']
        report_path = resolve_output_ref(local.output_ref) / 'report.json'
        if report_path.exists():
            per_source[source] = json.loads(report_path.read_text(encoding='utf-8'))
        else:
            per_source[source] = {
                'source': source,
                'status': local.status,
                'error': local.error,
                'finished_at': _iso(_utc_now()),
            }

    status_counts: dict[str, int] = {}
    for task in tasks:
        status = task_results[task.key].status
        status_counts[status] = status_counts.get(status, 0) + 1

    report = {
        'run_id': run_id,
        'created_at': created_at,
        'sources': sources,
        'task_summary': {
            'total': len(tasks),
            'changed': len(changed_tasks),
            'status_counts': status_counts,
        },
        'changed_tasks': changed_tasks,
        'freshness': freshness_report,
        'per_source': per_source,
    }

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    REPORTS_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORTS_RUNS_ROOT / f'{run_id}.json').write_text(
        json.dumps(report, indent=2) + '\n',
        encoding='utf-8',
    )
    (REPORTS_ROOT / 'latest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    with REPORTS_CHANGELOG_PATH.open('a', encoding='utf-8') as handle:
        for task_key in changed_tasks:
            current = task_state_entry(task_results[task_key])
            handle.write(
                json.dumps(
                    {
                        'event': 'task_changed',
                        'ts': _iso(_utc_now()),
                        'run_id': run_id,
                        'task_key': task_key,
                        'before': previous_tasks.get(task_key),
                        'after': current,
                    },
                    separators=(',', ':'),
                )
                + '\n'
            )
        handle.write(
            json.dumps(
                {
                    'event': 'run_completed',
                    'ts': _iso(_utc_now()),
                    'run_id': run_id,
                    'changed_tasks': len(changed_tasks),
                    'task_status_counts': status_counts,
                },
                separators=(',', ':'),
            )
            + '\n'
        )

    return changed_tasks


def write_output_snapshot(run_id: str, task_results: dict[str, TaskResult]) -> None:
    combined = resolve_output_ref(task_results['combined_gold'].output_ref)
    entities = resolve_output_ref(task_results['search_entities'].output_ref)
    interactions = resolve_output_ref(task_results['search_interactions'].output_ref)
    associations = resolve_output_ref(task_results['search_associations'].output_ref)
    sources = resolve_output_ref(task_results['search_sources'].output_ref)

    snapshot_sources = {
        'entity_identifier.parquet': combined / 'entity_identifier.parquet',
        'omnipath_mi.obo': combined / 'omnipath_mi.obo',
        'search_entities.parquet': entities,
        'search_interactions.parquet': interactions,
        'search_associations.parquet': associations,
        'search_sources.parquet': sources,
    }
    snapshot_hashes = {name: _sha256_file(path) for name, path in snapshot_sources.items()}

    latest = OUTPUT_ROOT / 'latest'
    latest_dir: Path | None = None
    if latest.is_symlink():
        link_target = os.readlink(latest)
        latest_dir = (latest.parent / link_target).resolve()
    elif latest.exists() and latest.is_dir():
        latest_dir = latest

    if latest_dir and latest_dir.exists():
        unchanged = True
        for name, expected_hash in snapshot_hashes.items():
            existing_file = latest_dir / name
            if not existing_file.exists() or _sha256_file(existing_file) != expected_hash:
                unchanged = False
                break
        if unchanged:
            return

    out_dir = OUTPUT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=False)
    for name, src in snapshot_sources.items():
        shutil.copy2(src, out_dir / name)

    if latest.exists() or latest.is_symlink():
        latest.unlink()
    os.symlink(run_id, latest)


def write_latest_state(state: dict[str, Any]) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    (STATE_ROOT / 'latest.json').write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
