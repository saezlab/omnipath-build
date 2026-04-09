from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnipath_build.pipeline.paths import (
    GoldPipelinePaths,
    build_paths,
    next_numeric_version,
    read_latest_pointer,
    source_version_dir,
    update_latest_pointer,
)
from omnipath_build.gold.canonicalize import write_canonicalization_overview_report
from omnipath_build.pipeline.resources_index import build_resources_parquet
from omnipath_build.pipeline.tasks import (
    build_gold_source,
    build_resolver_mappings,
    build_silver_source,
    resolver_mappings_ready,
)
from omnipath_build.silver.build import (
    ResourceFunction,
    TEST_MODE_INCLUDED_SOURCES,
    discover_resources,
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
    version: str | None
    output_dir: str | None
    metadata: dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace('+00:00', 'Z')


def _run_id() -> str:
    return utc_now().strftime('run-%Y%m%d-%H%M%S')


def _report_path(paths: GoldPipelinePaths, run_id: str) -> Path:
    return paths.reports_root / 'runs' / f'{run_id}.json'


def _latest_report_path(paths: GoldPipelinePaths) -> Path:
    return paths.reports_root / 'latest.json'


def _changelog_path(paths: GoldPipelinePaths) -> Path:
    return paths.reports_root / 'changelog.ndjson'


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


def _normalize_overwrite(overwrite: str | None) -> frozenset[str]:
    if overwrite in {None, ''}:
        return frozenset()
    if overwrite == 'gold':
        return frozenset({'gold'})
    if overwrite in {'silver', 'both'}:
        return frozenset({'silver', 'gold'})
    raise ValueError(f'Unsupported overwrite mode: {overwrite}')


def _reuse_existing_result(
    task: TaskDef,
    paths: GoldPipelinePaths,
    resolver_mapping_dir: Path | None,
    overwrite_task_types: frozenset[str],
) -> TaskResult | None:
    if task.task_type in overwrite_task_types:
        return None

    if task.task_type == 'resolver_mappings':
        mapping_dir = resolver_mapping_dir or Path('id_resolver/data')
        if not resolver_mappings_ready(mapping_dir):
            return None
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='reused',
            version='id-resolver-data',
            output_dir=str(mapping_dir),
            metadata={'external': resolver_mapping_dir is not None},
        )

    if task.source is None:
        return None

    stage_root = paths.silver_root if task.task_type == 'silver' else paths.gold_root
    version = read_latest_pointer(stage_root, task.source)
    if version is None:
        return None
    output_dir = source_version_dir(stage_root, task.source, version)
    if not output_dir.exists():
        return None
    metadata: dict[str, Any] = {}
    if task.task_type == 'gold':
        summary_path = output_dir / 'canonicalization_summary.json'
        if summary_path.exists():
            try:
                metadata['canonicalize_summary'] = json.loads(
                    summary_path.read_text(encoding='utf-8')
                )
            except Exception:
                metadata = {}

    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='reused',
        version=version,
        output_dir=str(output_dir),
        metadata=metadata,
    )


def _failed_task_result(
    task: TaskDef,
    exc: Exception,
) -> TaskResult:
    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='failed',
        version=None,
        output_dir=None,
        metadata={
            'error': {
                'type': type(exc).__name__,
                'message': str(exc),
            }
        },
    )



def _skipped_task_result(
    task: TaskDef,
    reason: str,
    failed_dependencies: list[str],
) -> TaskResult:
    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='skipped',
        version=None,
        output_dir=None,
        metadata={
            'reason': reason,
            'failed_dependencies': failed_dependencies,
        },
    )



def _execute_task(
    *,
    task: TaskDef,
    results: dict[str, TaskResult],
    paths: GoldPipelinePaths,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    resolver_mapping_dir: Path | None,
    overwrite_task_types: frozenset[str],
) -> TaskResult:
    reused = _reuse_existing_result(task, paths, resolver_mapping_dir, overwrite_task_types)
    if reused is not None:
        return reused

    if task.task_type == 'resolver_mappings':
        output_dir = resolver_mapping_dir or Path('id_resolver/data')
        metadata = build_resolver_mappings(output_dir, test_mode=test_mode)
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='executed',
            version='id-resolver-data',
            output_dir=str(output_dir),
            metadata=metadata,
        )

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
            version=version,
            output_dir=str(output_dir),
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
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='executed',
            version=version,
            output_dir=str(output_dir),
            metadata=metadata,
        )

    raise ValueError(f'Unsupported task type: {task.task_type}')


def _run_dag(
    *,
    tasks: list[TaskDef],
    paths: GoldPipelinePaths,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    jobs: int,
    resolver_mapping_dir: Path | None,
    overwrite_task_types: frozenset[str],
) -> dict[str, TaskResult]:
    task_map = {task.key: task for task in tasks}
    pending_deps = {task.key: len(task.deps) for task in tasks}
    dependents: dict[str, list[str]] = {task.key: [] for task in tasks}
    for task in tasks:
        for dep in task.deps:
            dependents.setdefault(dep, []).append(task.key)

    ready = sorted([task.key for task in tasks if pending_deps[task.key] == 0])
    results: dict[str, TaskResult] = {}

    def submit(pool: ThreadPoolExecutor, key: str):
        task = task_map[key]
        snapshot = dict(results)
        return pool.submit(
            _execute_task,
            task=task,
            results=snapshot,
            paths=paths,
            inputs_package=inputs_package,
            batch_size=batch_size,
            test_mode=test_mode,
            resolver_mapping_dir=resolver_mapping_dir,
            overwrite_task_types=overwrite_task_types,
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
                    result = _failed_task_result(task, exc)

                results[key] = result
                print(f'[{result.status}] {key} -> {result.output_dir or result.version or result.metadata.get("error", {}).get("message", "-")}')

                for dependent in dependents.get(key, []):
                    pending_deps[dependent] -= 1
                    if pending_deps[dependent] == 0:
                        dep_task = task_map[dependent]
                        failed_dependencies = [
                            dep for dep in dep_task.deps
                            if dep in results and results[dep].status in {'failed', 'skipped'}
                        ]
                        if failed_dependencies:
                            skipped = _skipped_task_result(
                                dep_task,
                                'dependency_failed',
                                failed_dependencies,
                            )
                            results[dependent] = skipped
                            print(f'[skipped] {dependent} -> dependency_failed')
                            for child in dependents.get(dependent, []):
                                pending_deps[child] -= 1
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
        fn.function_name != 'resource' and fn.output_kind != 'ontology'
        for fn in functions
    )



def _discover_all_sources(inputs_package: str, *, test_mode: bool = False) -> list[str]:
    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )
    sources = sorted(
        source
        for source, functions in discovered.items()
        if _has_gold_buildable_dataset(functions)
    )
    if test_mode:
        sources = [
            source for source in sources
            if source in TEST_MODE_INCLUDED_SOURCES
        ]
    return sources



def run_pipeline(
    *,
    command: str,
    sources: list[str],
    data_root: str | Path = 'data_v2',
    inputs_package: str = 'pypath.inputs_v2',
    batch_size: int = 10_000,
    test_mode: bool = False,
    jobs: int = 4,
    resolver_mapping_dir: str | Path | None = None,
    overwrite: str | None = None,
) -> dict[str, Any]:
    include_mappings = command in {'mappings', 'source', 'all'}
    include_sources = command in {'source', 'all'}

    if include_sources and not sources:
        sources = _discover_all_sources(inputs_package, test_mode=test_mode)
        print(f'Autodiscovered {len(sources)} sources from {inputs_package}')

    paths = build_paths(data_root)
    for base in [
        paths.data_root,
        paths.silver_root,
        paths.gold_root,
        paths.reports_root,
    ]:
        base.mkdir(parents=True, exist_ok=True)

    overwrite_task_types = _normalize_overwrite(overwrite)
    tasks = build_task_graph(sources, include_mappings=include_mappings, include_sources=include_sources)
    results = _run_dag(
        tasks=tasks,
        paths=paths,
        inputs_package=inputs_package,
        batch_size=batch_size,
        test_mode=test_mode,
        jobs=jobs,
        resolver_mapping_dir=Path(resolver_mapping_dir) if resolver_mapping_dir is not None else None,
        overwrite_task_types=overwrite_task_types,
    )

    for source in sources:
        silver_result = results.get(f'silver:{source}')
        if silver_result and silver_result.version:
            update_latest_pointer(paths.silver_root, source, silver_result.version)
        gold_result = results.get(f'gold:{source}')
        if gold_result and gold_result.version:
            update_latest_pointer(paths.gold_root, source, gold_result.version)

    resources_parquet = None
    canonicalization_overview = None
    if include_sources:
        resources_parquet = build_resources_parquet(
            gold_root=paths.gold_root,
            inputs_package=inputs_package,
        )
        source_summaries = {
            source: results[f'gold:{source}'].metadata.get('canonicalize_summary', {})
            for source in sources
            if results.get(f'gold:{source}') is not None and results[f'gold:{source}'].status in {'executed', 'reused'}
        }
        canonicalization_overview = write_canonicalization_overview_report(
            paths.gold_root,
            source_summaries=source_summaries,
        )

    mapping_result = results.get('resolver_mappings')
    report = {
        'run_id': _run_id(),
        'created_at': iso_now(),
        'command': command,
        'overwrite': overwrite,
        'selected_sources': sources,
        'resolver_mapping_version': mapping_result.version if mapping_result else None,
        'resources_parquet': str(resources_parquet) if resources_parquet else None,
        'canonicalization_overview': str(canonicalization_overview) if canonicalization_overview else None,
        'tasks': {key: asdict(value) for key, value in results.items()},
    }
    _write_report(paths, report)
    return report


run_gold_pipeline = run_pipeline
