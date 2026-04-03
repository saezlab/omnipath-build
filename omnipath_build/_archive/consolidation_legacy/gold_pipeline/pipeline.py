from __future__ import annotations

import hashlib
import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnipath_build.gold_pipeline.paths import (
    GoldPipelinePaths,
    build_paths,
    next_numeric_version,
    source_version_dir,
    update_latest_pointer,
)
from omnipath_build.loaders.silver import ResourceFunction, discover_resources
from omnipath_build.gold_pipeline.tasks import (
    build_gold_source,
    build_resolver_mappings,
    build_silver_source,
    module_file_hash,
    resolver_mappings_ready,
    tree_sha256,
)


@dataclass(frozen=True)
class TaskDef:
    key: str
    task_type: str
    source: str | None
    deps: tuple[str, ...]


@dataclass
class TaskResult:
    task_key: str
    task_type: str
    source: str | None
    status: str
    fingerprint: str
    version: str | None
    output_dir: str | None
    reused_from_run: str | None
    dependency_versions: dict[str, str]
    metadata: dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace('+00:00', 'Z')


def _stable_json_sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def _run_id() -> str:
    return utc_now().strftime('run-%Y%m%d-%H%M%S')


def _report_path(paths: GoldPipelinePaths, run_id: str) -> Path:
    return paths.reports_root / 'runs' / f'{run_id}.json'


def _latest_report_path(paths: GoldPipelinePaths) -> Path:
    return paths.reports_root / 'latest.json'


def _changelog_path(paths: GoldPipelinePaths) -> Path:
    return paths.reports_root / 'changelog.ndjson'


def _load_latest_report(paths: GoldPipelinePaths) -> dict[str, Any] | None:
    latest = _latest_report_path(paths)
    if not latest.exists():
        return None
    return json.loads(latest.read_text(encoding='utf-8'))


def _write_report(paths: GoldPipelinePaths, report: dict[str, Any]) -> None:
    report_path = _report_path(paths, report['run_id'])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    latest_path = _latest_report_path(paths)
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    changelog_path = _changelog_path(paths)
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    with changelog_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(report, sort_keys=True) + '\n')


def build_task_graph(sources: list[str], include_mappings: bool, include_sources: bool) -> list[TaskDef]:
    tasks: list[TaskDef] = []
    if include_mappings:
        tasks.append(TaskDef('resolver_mappings', 'resolver_mappings', None, ()))
    if include_sources:
        for source in sources:
            tasks.append(TaskDef(f'silver:{source}', 'silver', source, ()))
            deps = [f'silver:{source}']
            if include_mappings:
                deps.append('resolver_mappings')
            tasks.append(TaskDef(f'gold:{source}', 'gold', source, tuple(deps)))
    return tasks


def _task_fingerprint(
    task: TaskDef,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    dep_versions: dict[str, str],
) -> str:
    payload: dict[str, Any] = {
        'task_type': task.task_type,
        'source': task.source,
        'batch_size': batch_size,
        'test_mode': test_mode,
        'deps': dep_versions,
    }
    if task.task_type == 'resolver_mappings':
        payload['code'] = {
            'mapping_tables': module_file_hash('id_resolver.build.mapping_tables'),
            'protein_sources': module_file_hash('id_resolver.build.sources.proteins'),
            'chemical_sources': module_file_hash('id_resolver.build.sources.chemicals'),
        }
    elif task.task_type == 'silver':
        payload['code'] = {
            'silver_loader': module_file_hash('omnipath_build.loaders.silver'),
            'source_module': module_file_hash(f'{inputs_package}.{task.source}'),
        }
    elif task.task_type == 'gold':
        payload['code'] = {
            'converter': module_file_hash('omnipath_build.gold_package.converter'),
            'dedup': module_file_hash('omnipath_build.gold_package.dedup'),
            'resolver': module_file_hash('id_resolver.resolve.target_schema'),
        }
    else:
        raise ValueError(f'Unsupported task type: {task.task_type}')
    return _stable_json_sha(payload)


def _reuse_previous_result(
    previous_report: dict[str, Any] | None,
    task: TaskDef,
    fingerprint: str,
) -> TaskResult | None:
    if previous_report is None:
        return None
    previous_task = previous_report.get('tasks', {}).get(task.key)
    if not previous_task:
        return None
    if previous_task.get('fingerprint') != fingerprint:
        return None
    output_dir = previous_task.get('output_dir')
    if output_dir and not Path(output_dir).exists():
        return None
    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='reused',
        fingerprint=fingerprint,
        version=previous_task.get('version'),
        output_dir=output_dir,
        reused_from_run=previous_report.get('run_id'),
        dependency_versions=dict(previous_task.get('dependency_versions', {})),
        metadata=dict(previous_task.get('metadata', {})),
    )


def _failed_task_result(
    task: TaskDef,
    dep_versions: dict[str, str],
    exc: Exception,
) -> TaskResult:
    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='failed',
        fingerprint='',
        version=None,
        output_dir=None,
        reused_from_run=None,
        dependency_versions=dep_versions,
        metadata={
            'error': {
                'type': type(exc).__name__,
                'message': str(exc),
            }
        },
    )



def _skipped_task_result(
    task: TaskDef,
    dep_versions: dict[str, str],
    reason: str,
    failed_dependencies: list[str],
) -> TaskResult:
    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='skipped',
        fingerprint='',
        version=None,
        output_dir=None,
        reused_from_run=None,
        dependency_versions=dep_versions,
        metadata={
            'reason': reason,
            'failed_dependencies': failed_dependencies,
        },
    )



def _execute_task(
    *,
    task: TaskDef,
    results: dict[str, TaskResult],
    previous_report: dict[str, Any] | None,
    paths: GoldPipelinePaths,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    resolver_mapping_dir: Path | None,
) -> TaskResult:
    dep_versions = {
        dep: results[dep].version
        for dep in task.deps
        if results[dep].version is not None
    }
    fingerprint = _task_fingerprint(task, inputs_package, batch_size, test_mode, dep_versions)
    reused = _reuse_previous_result(previous_report, task, fingerprint)
    if reused is not None:
        return reused

    if task.task_type == 'resolver_mappings':
        if resolver_mapping_dir is not None:
            if not resolver_mappings_ready(resolver_mapping_dir):
                raise FileNotFoundError(
                    f'Resolver mapping dir is missing required files: {resolver_mapping_dir}'
                )
            version = f'external-{fingerprint[:12]}'
            return TaskResult(
                task_key=task.key,
                task_type=task.task_type,
                source=task.source,
                status='executed',
                fingerprint=fingerprint,
                version=version,
                output_dir=str(resolver_mapping_dir),
                reused_from_run=None,
                dependency_versions=dep_versions,
                metadata={'external': True},
            )

        output_dir = resolver_mapping_dir or Path('id_resolver/data')
        version = 'id-resolver-data'
        metadata = build_resolver_mappings(output_dir)
        result = TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='executed',
            fingerprint=fingerprint,
            version=version,
            output_dir=str(output_dir),
            reused_from_run=None,
            dependency_versions=dep_versions,
            metadata=metadata,
        )
        return result

    if task.source is None:
        raise ValueError(f'Task source missing for {task.key}')

    if task.task_type == 'silver':
        version = next_numeric_version(paths.silver_root, task.source)
        output_dir = source_version_dir(paths.silver_root, task.source, version)
        metadata = build_silver_source(
            source=task.source,
            output_dir=output_dir,
            inputs_package=inputs_package,
            batch_size=batch_size,
            test_mode=test_mode,
        )
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='executed',
            fingerprint=fingerprint,
            version=version,
            output_dir=str(output_dir),
            reused_from_run=None,
            dependency_versions=dep_versions,
            metadata=metadata,
        )

    if task.task_type == 'gold':
        version = next_numeric_version(paths.gold_root, task.source)
        output_dir = source_version_dir(paths.gold_root, task.source, version)
        silver_dir = Path(results[f'silver:{task.source}'].output_dir or '')
        mapping_dep = Path(results['resolver_mappings'].output_dir or '')
        metadata = build_gold_source(
            source=task.source,
            silver_dir=silver_dir,
            output_dir=output_dir,
            mapping_dir=mapping_dep,
            batch_size=batch_size,
        )
        metadata['content_hash'] = tree_sha256(output_dir)
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='executed',
            fingerprint=fingerprint,
            version=version,
            output_dir=str(output_dir),
            reused_from_run=None,
            dependency_versions=dep_versions,
            metadata=metadata,
        )

    raise ValueError(f'Unsupported task type: {task.task_type}')


def _run_dag(
    *,
    tasks: list[TaskDef],
    previous_report: dict[str, Any] | None,
    paths: GoldPipelinePaths,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    jobs: int,
    resolver_mapping_dir: Path | None,
) -> dict[str, TaskResult]:
    task_map = {task.key: task for task in tasks}
    pending_deps = {task.key: len(task.deps) for task in tasks}
    dependents: dict[str, list[str]] = {task.key: [] for task in tasks}
    for task in tasks:
        for dep in task.deps:
            dependents.setdefault(dep, []).append(task.key)

    ready = sorted([task.key for task in tasks if pending_deps[task.key] == 0])
    results: dict[str, TaskResult] = {}
    blocked: set[str] = set()

    def submit(pool: ThreadPoolExecutor, key: str):
        task = task_map[key]
        snapshot = dict(results)
        return pool.submit(
            _execute_task,
            task=task,
            results=snapshot,
            previous_report=previous_report,
            paths=paths,
            inputs_package=inputs_package,
            batch_size=batch_size,
            test_mode=test_mode,
            resolver_mapping_dir=resolver_mapping_dir,
        )

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        running: dict[Any, str] = {}
        while ready and len(running) < max(1, jobs):
            key = ready.pop(0)
            running[submit(pool, key)] = key

        while running:
            done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                key = running.pop(future)
                task = task_map[key]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    dep_versions = {
                        dep: results[dep].version
                        for dep in task.deps
                        if dep in results and results[dep].version is not None
                    }
                    result = _failed_task_result(task, dep_versions, exc)

                results[key] = result
                print(f'[{result.status}] {key} -> {result.output_dir or result.version or result.metadata.get("error", {}).get("message", "-")}')

                for dependent in dependents.get(key, []):
                    pending_deps[dependent] -= 1
                    if result.status == 'failed':
                        blocked.add(dependent)
                    if pending_deps[dependent] == 0:
                        dep_task = task_map[dependent]
                        failed_dependencies = [
                            dep for dep in dep_task.deps
                            if dep in results and results[dep].status in {'failed', 'skipped'}
                        ]
                        if failed_dependencies:
                            dep_versions = {
                                dep: results[dep].version
                                for dep in dep_task.deps
                                if dep in results and results[dep].version is not None
                            }
                            skipped = _skipped_task_result(
                                dep_task,
                                dep_versions,
                                'dependency_failed',
                                failed_dependencies,
                            )
                            results[dependent] = skipped
                            print(f'[skipped] {dependent} -> dependency_failed')
                            for child in dependents.get(dependent, []):
                                pending_deps[child] -= 1
                                blocked.add(child)
                                if pending_deps[child] == 0:
                                    ready.append(child)
                        else:
                            ready.append(dependent)
                ready = sorted(k for k in ready if k not in results)
            while ready and len(running) < max(1, jobs):
                key = ready.pop(0)
                running[submit(pool, key)] = key

    return results


def _has_gold_buildable_dataset(functions: list[ResourceFunction]) -> bool:
    return any(
        fn.output_kind == 'entity' and fn.function_name != 'resource'
        for fn in functions
    )



def _discover_all_sources(inputs_package: str) -> list[str]:
    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )
    return sorted(
        source
        for source, functions in discovered.items()
        if _has_gold_buildable_dataset(functions)
    )



def run_gold_pipeline(
    *,
    command: str,
    sources: list[str],
    data_root: str | Path = 'data_v2',
    inputs_package: str = 'pypath.inputs_v2',
    batch_size: int = 10_000,
    test_mode: bool = False,
    jobs: int = 4,
    resolver_mapping_dir: str | Path | None = None,
) -> dict[str, Any]:
    include_mappings = command in {'mappings', 'source', 'all'}
    include_sources = command in {'source', 'all'}

    if include_sources and not sources:
        sources = _discover_all_sources(inputs_package)
        print(f'Autodiscovered {len(sources)} sources from {inputs_package}')

    paths = build_paths(data_root)
    for base in [
        paths.data_root,
        paths.silver_root,
        paths.gold_root,
        paths.reports_root,
    ]:
        base.mkdir(parents=True, exist_ok=True)

    previous_report = _load_latest_report(paths)
    tasks = build_task_graph(sources, include_mappings=include_mappings, include_sources=include_sources)
    results = _run_dag(
        tasks=tasks,
        previous_report=previous_report,
        paths=paths,
        inputs_package=inputs_package,
        batch_size=batch_size,
        test_mode=test_mode,
        jobs=jobs,
        resolver_mapping_dir=Path(resolver_mapping_dir) if resolver_mapping_dir is not None else None,
    )

    for source in sources:
        silver_result = results.get(f'silver:{source}')
        if silver_result and silver_result.version:
            update_latest_pointer(paths.silver_root, source, silver_result.version)
        gold_result = results.get(f'gold:{source}')
        if gold_result and gold_result.version:
            update_latest_pointer(paths.gold_root, source, gold_result.version)

    mapping_result = results.get('resolver_mappings')
    report = {
        'run_id': _run_id(),
        'created_at': iso_now(),
        'command': command,
        'selected_sources': sources,
        'resolver_mapping_version': mapping_result.version if mapping_result else None,
        'tasks': {key: asdict(value) for key, value in results.items()},
    }
    _write_report(paths, report)
    return report
