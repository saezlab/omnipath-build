from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnipath_build.gold.combine import build_combined_parquets
from omnipath_build.pipeline.paths import (
    PipelinePaths,
    build_paths,
    next_numeric_version,
    read_latest_pointer,
    source_stage_dir,
    source_version_dir,
    update_latest_pointer,
)
from omnipath_build.pipeline.tasks import (
    build_gold_source,
    build_resolver_mappings,
    build_silver_source,
    gold_output_ready,
    resolve_silver_version,
    resolver_mappings_ready,
)
from omnipath_build.silver.build import (
    ResourceFunction,
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
    output_dir: str | None
    metadata: dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace('+00:00', 'Z')


def _run_id() -> str:
    return utc_now().strftime('run-%Y%m%d-%H%M%S')


def _report_path(paths: PipelinePaths, run_id: str) -> Path:
    return paths.reports_root / 'runs' / f'{run_id}.json'


def _latest_report_path(paths: PipelinePaths) -> Path:
    return paths.reports_root / 'latest.json'


def _changelog_path(paths: PipelinePaths) -> Path:
    return paths.reports_root / 'changelog.ndjson'


def _write_report(paths: PipelinePaths, report: dict[str, Any]) -> None:
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


def build_task_graph(
    sources: list[str],
    gold_sources: list[str] | None = None,
    build_mappings: bool | None = None,
    build_sources: bool | None = None,
    combine: bool = False,
    postgres: bool = False,
    include_mappings: bool | None = None,
    include_sources: bool | None = None,
) -> list[TaskDef]:
    if build_mappings is None:
        build_mappings = bool(include_mappings)
    if build_sources is None:
        build_sources = True if include_sources is None else bool(include_sources)
    tasks: list[TaskDef] = []
    silver_keys: list[str] = []
    gold_keys: list[str] = []

    if build_mappings:
        tasks.append(TaskDef('resolver_mappings', 'resolver_mappings', None, ()))

    gold_source_set = set(gold_sources if gold_sources is not None else sources)

    if build_sources:
        for source in sources:
            silver_key = f'silver:{source}'
            silver_keys.append(silver_key)
            tasks.append(TaskDef(silver_key, 'silver', source, ()))
            if source not in gold_source_set:
                continue
            gold_key = f'gold:{source}'
            gold_keys.append(gold_key)
            deps = [silver_key]
            if build_mappings:
                deps.append('resolver_mappings')
            tasks.append(TaskDef(gold_key, 'gold', source, tuple(deps)))

    if build_sources and combine:
        tasks.append(TaskDef('combine', 'combine', None, tuple(gold_keys)))
        if postgres:
            tasks.append(TaskDef('postgres', 'postgres', None, ('combine',)))

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
    *,
    paths: PipelinePaths,
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
            source=None,
            status='reused',
            output_dir=str(mapping_dir),
            metadata={'external': resolver_mapping_dir is not None},
        )

    if task.task_type == 'silver' and task.source is not None:
        version = read_latest_pointer(paths.silver_root, task.source)
        if version is None:
            return None
        output_dir = source_version_dir(paths.silver_root, task.source, version)
        if not output_dir.exists():
            return None
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='reused',
            output_dir=str(output_dir),
            metadata={'version': version},
        )

    if task.task_type == 'gold' and task.source is not None:
        output_dir = source_stage_dir(paths.gold_root, task.source)
        if not gold_output_ready(output_dir):
            return None
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='reused',
            output_dir=str(output_dir),
            metadata={'reused_existing_output': True},
        )

    return None


def _failed_task_result(task: TaskDef, exc: Exception) -> TaskResult:
    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='failed',
        output_dir=None,
        metadata={
            'error': {
                'type': type(exc).__name__,
                'message': str(exc),
            }
        },
    )


def _skipped_task_result(task: TaskDef, reason: str, failed_dependencies: list[str]) -> TaskResult:
    return TaskResult(
        task_key=task.key,
        task_type=task.task_type,
        source=task.source,
        status='skipped',
        output_dir=None,
        metadata={
            'reason': reason,
            'failed_dependencies': failed_dependencies,
        },
    )


def _can_run_with_failed_dependencies(task: TaskDef, failed_dependencies: list[str]) -> bool:
    if task.task_type == 'combine':
        return all(dep.startswith('gold:') for dep in failed_dependencies)
    return False


def _execute_task(
    *,
    task: TaskDef,
    results: dict[str, TaskResult],
    paths: PipelinePaths,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    resolver_mapping_dir: Path | None,
    overwrite_task_types: frozenset[str],
    combined_output_dir: Path,
    postgres_uri: str | None,
    postgres_schema: str,
    postgres_drop_existing: bool,
) -> TaskResult:
    reused = _reuse_existing_result(
        task,
        paths=paths,
        resolver_mapping_dir=resolver_mapping_dir,
        overwrite_task_types=overwrite_task_types,
    )
    if reused is not None:
        return reused

    if task.task_type == 'resolver_mappings':
        output_dir = resolver_mapping_dir or Path('id_resolver/data')
        metadata = build_resolver_mappings(output_dir, test_mode=test_mode)
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=None,
            status='executed',
            output_dir=str(output_dir),
            metadata=metadata,
        )

    if task.task_type == 'silver':
        if task.source is None:
            raise ValueError(f'Task source missing for {task.key}')
        version = next_numeric_version(paths.silver_root, task.source)
        output_dir = source_version_dir(paths.silver_root, task.source, version)
        metadata = build_silver_source(
            source=task.source,
            output_dir=output_dir,
            inputs_package=inputs_package,
            batch_size=batch_size,
            test_mode=test_mode,
        )
        metadata = {**metadata, 'version': version}
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='executed',
            output_dir=str(output_dir),
            metadata=metadata,
        )

    if task.task_type == 'gold':
        if task.source is None:
            raise ValueError(f'Task source missing for {task.key}')
        silver_result = results.get(f'silver:{task.source}')
        if silver_result is None or silver_result.output_dir is None:
            raise FileNotFoundError(f'Missing silver output for {task.source}')
        mapping_result = results.get('resolver_mappings')
        mapping_dir = Path(mapping_result.output_dir) if mapping_result and mapping_result.output_dir else (resolver_mapping_dir or Path('id_resolver/data'))
        output_dir = source_stage_dir(paths.gold_root, task.source)
        metadata = build_gold_source(
            source=task.source,
            silver_dir=Path(silver_result.output_dir),
            output_dir=output_dir,
            mapping_dir=mapping_dir,
        )
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='executed',
            output_dir=str(output_dir),
            metadata=metadata,
        )

    if task.task_type == 'combine':
        metadata = build_combined_parquets(
            gold_root=paths.gold_root,
            output_dir=combined_output_dir,
            inputs_package=inputs_package,
        )
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=None,
            status='executed',
            output_dir=str(combined_output_dir),
            metadata=metadata,
        )

    if task.task_type == 'postgres':
        if not postgres_uri:
            return TaskResult(
                task_key=task.key,
                task_type=task.task_type,
                source=None,
                status='skipped',
                output_dir=str(combined_output_dir),
                metadata={'reason': 'missing_postgres_uri'},
            )
        from omnipath_build.postgres import load_combined_schema_to_postgres

        load_combined_schema_to_postgres(
            output_dir=combined_output_dir,
            postgres_uri=postgres_uri,
            schema=postgres_schema,
            drop_existing=postgres_drop_existing,
        )
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=None,
            status='executed',
            output_dir=str(combined_output_dir),
            metadata={'schema': postgres_schema},
        )

    raise ValueError(f'Unsupported task type: {task.task_type}')


def _run_dag(
    *,
    tasks: list[TaskDef],
    paths: PipelinePaths,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    jobs: int,
    resolver_mapping_dir: Path | None,
    overwrite_task_types: frozenset[str],
    combined_output_dir: Path,
    postgres_uri: str | None,
    postgres_schema: str,
    postgres_drop_existing: bool,
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
            combined_output_dir=combined_output_dir,
            postgres_uri=postgres_uri,
            postgres_schema=postgres_schema,
            postgres_drop_existing=postgres_drop_existing,
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
                print(f'[{result.status}] {key} -> {result.output_dir or result.metadata.get("error", {}).get("message", "-")}')

                for dependent in dependents.get(key, []):
                    pending_deps[dependent] -= 1
                    if pending_deps[dependent] == 0:
                        dep_task = task_map[dependent]
                        failed_dependencies = [
                            dep for dep in dep_task.deps
                            if dep in results and results[dep].status in {'failed', 'skipped'}
                        ]
                        if failed_dependencies and not _can_run_with_failed_dependencies(dep_task, failed_dependencies):
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
        fn.function_name != 'resource' and fn.output_kind in {'entity', 'ontology'}
        for fn in functions
    )


def _discover_sources_by_capability(inputs_package: str) -> tuple[list[str], list[str]]:
    discovered, _ = discover_resources(
        database_name='.',
        base_path=None,
        inputs_package=inputs_package,
    )
    silver_sources = sorted(discovered)
    gold_sources = sorted(
        source for source, functions in discovered.items() if _has_gold_buildable_dataset(functions)
    )
    return silver_sources, gold_sources


def _discover_all_sources(inputs_package: str, *, test_mode: bool = False) -> list[str]:
    silver_sources, _ = _discover_sources_by_capability(inputs_package)
    return silver_sources


def run_pipeline(
    *,
    sources: list[str],
    data_root: str | Path = 'data',
    inputs_package: str = 'pypath.inputs_v2',
    batch_size: int = 10_000,
    test_mode: bool = False,
    jobs: int = 4,
    resolver_mapping_dir: str | Path | None = None,
    overwrite: str | None = None,
    build_mappings: bool = True,
    build_sources: bool = True,
    combine: bool = True,
    combined_output_dir: str | Path | None = None,
    postgres_uri: str | None = None,
    postgres_schema: str = 'public',
    postgres_drop_existing: bool = False,
) -> dict[str, Any]:
    discovered_gold_sources: list[str] | None = None
    if build_sources and not sources:
        sources, discovered_gold_sources = _discover_sources_by_capability(inputs_package)
        print(f'Autodiscovered {len(sources)} sources from {inputs_package}')
    elif build_sources:
        _, all_gold_sources = _discover_sources_by_capability(inputs_package)
        requested = set(sources)
        discovered_gold_sources = [source for source in all_gold_sources if source in requested]

    paths = build_paths(data_root)
    for base in [
        paths.data_root,
        paths.silver_root,
        paths.gold_root,
        paths.reports_root,
    ]:
        base.mkdir(parents=True, exist_ok=True)

    resolved_mapping_dir = Path(resolver_mapping_dir) if resolver_mapping_dir is not None else None
    final_combined_dir = Path(combined_output_dir) if combined_output_dir is not None else (paths.data_root / 'combined')
    final_combined_dir.mkdir(parents=True, exist_ok=True)

    overwrite_task_types = _normalize_overwrite(overwrite)
    tasks = build_task_graph(
        sources=sources,
        gold_sources=discovered_gold_sources,
        build_mappings=build_mappings,
        build_sources=build_sources,
        combine=combine,
        postgres=postgres_uri is not None,
    )
    results = _run_dag(
        tasks=tasks,
        paths=paths,
        inputs_package=inputs_package,
        batch_size=batch_size,
        test_mode=test_mode,
        jobs=jobs,
        resolver_mapping_dir=resolved_mapping_dir,
        overwrite_task_types=overwrite_task_types,
        combined_output_dir=final_combined_dir,
        postgres_uri=postgres_uri,
        postgres_schema=postgres_schema,
        postgres_drop_existing=postgres_drop_existing,
    )

    for source in sources:
        silver_result = results.get(f'silver:{source}')
        if silver_result and silver_result.status in {'executed', 'reused'}:
            version = silver_result.metadata.get('version')
            if version:
                update_latest_pointer(paths.silver_root, source, str(version))
            elif silver_result.output_dir:
                try:
                    resolved_dir = resolve_silver_version(paths.silver_root / source.replace('.', '/'))
                    update_latest_pointer(paths.silver_root, source, resolved_dir.name)
                except FileNotFoundError:
                    pass

    report = {
        'run_id': _run_id(),
        'created_at': iso_now(),
        'selected_sources': sources,
        'data_root': str(paths.data_root),
        'combined_output_dir': str(final_combined_dir),
        'inputs_package': inputs_package,
        'overwrite': overwrite,
        'build_mappings': build_mappings,
        'build_sources': build_sources,
        'combine': combine,
        'postgres_enabled': postgres_uri is not None,
        'tasks': {key: asdict(value) for key, value in results.items()},
    }
    _write_report(paths, report)
    return report
