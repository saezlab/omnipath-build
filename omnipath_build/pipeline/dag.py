from __future__ import annotations

import json
import os
from typing import Any
from pathlib import Path
from datetime import UTC, datetime
from dataclasses import asdict, dataclass
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import polars as pl

from omnipath_build.gold.combine import build_combined
from omnipath_build.silver.build import (
    ResourceFunction,
    discover_resources,
)
from omnipath_build.pipeline.paths import (
    PipelinePaths,
    build_paths,
    source_stage_dir,
    source_version_dir,
    read_latest_pointer,
    next_numeric_version,
    update_latest_pointer,
)
from omnipath_build.pipeline.tasks import (
    build_gold_source,
    gold_output_ready,
    hash_inputs_module,
    build_silver_source,
    resolve_silver_version,
    build_resolver_mappings,
    read_inputs_module_hash,
    resolver_mappings_ready,
)
from omnipath_build.pipeline.progress import phase, start_heartbeat, stop_heartbeat

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


@dataclass
class PlannedTask:
    task_key: str
    task_type: str
    source: str | None
    action: str
    detail: str
    metadata: dict[str, Any]


def _download_cache_dir() -> Path:
    configured = os.environ.get('PYPATH_DOWNLOAD_DATADIR')
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (Path.cwd() / path).resolve()
    return Path(__file__).resolve().parents[2] / 'pypath-data'


def _format_names(names: list[str], *, limit: int = 12) -> str:
    if not names:
        return '-'
    if len(names) <= limit:
        return ','.join(names)
    shown = ','.join(names[:limit])
    return f'{shown},...(+{len(names) - limit})'


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
    build_mappings: bool = True,
    build_sources: bool = True,
    combine: bool = False,
    postgres: bool = False,
    start_stage: str = 'download',
) -> list[TaskDef]:
    tasks: list[TaskDef] = []
    gold_keys: list[str] = []

    if build_mappings and build_sources and start_stage in {'download', 'bronze', 'silver'}:
        tasks.append(TaskDef('resolver_mappings', 'resolver_mappings', None, ()))

    gold_source_set = set(gold_sources if gold_sources is not None else sources)

    if build_sources:
        for source in sources:
            silver_key = f'silver:{source}'
            if start_stage in {'download', 'bronze'}:
                tasks.append(TaskDef(silver_key, 'silver', source, ()))
            if source in gold_source_set and start_stage in {'download', 'bronze', 'silver'}:
                gold_key = f'gold:{source}'
                gold_keys.append(gold_key)
                deps = []
                if start_stage in {'download', 'bronze'}:
                    deps.append(silver_key)
                if build_mappings:
                    deps.append('resolver_mappings')
                tasks.append(TaskDef(gold_key, 'gold', source, tuple(deps)))

    if combine:
        tasks.append(TaskDef('combine', 'combine', None, tuple(gold_keys)))
        if postgres:
            tasks.append(TaskDef('postgres', 'postgres', None, ('combine',)))

    return tasks


def _normalize_start_stage(start_stage: str) -> str:
    normalized = start_stage.lower().strip()
    aliases = {
        'from-download': 'download',
        'from-bronze': 'bronze',
        'from-silver': 'silver',
        'from-gold': 'gold',
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {'download', 'bronze', 'silver', 'gold'}:
        raise ValueError(f'Unsupported pipeline start stage: {start_stage}')
    return normalized


def _combined_latest_exists(output_dir: Path) -> bool:
    latest = output_dir / 'latest'
    if latest.is_symlink():
        latest = latest.resolve()
    return latest.exists()


def _executed_gold_sources(results: dict[str, TaskResult]) -> list[str]:
    return sorted(
        result.source
        for key, result in results.items()
        if (
            key.startswith('gold:')
            and result.task_type == 'gold'
            and result.status == 'executed'
            and result.source is not None
        )
    )


def _numeric_silver_versions_desc(paths: PipelinePaths, source: str) -> list[str]:
    source_dir = source_stage_dir(paths.silver_root, source)
    if not source_dir.exists():
        return []

    versions: list[int] = []
    for child in source_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            versions.append(int(child.name))
        except ValueError:
            continue
    return [str(version) for version in sorted(versions, reverse=True)]


def _matching_completed_silver_version(
    *,
    paths: PipelinePaths,
    source: str,
    current_hash: dict[str, Any],
) -> tuple[str, Path] | None:
    """Find the newest completed silver version matching the current inputs module.

    The latest pointer is a convenience, not the source of truth. A run can be
    interrupted after a source finishes but before the final report/pointer update
    phase, so recovery scans completed numeric versions and repairs/reuses them.
    The inputs hash is written last by ``build_silver_source`` and acts as the
    completion marker.
    """
    latest_version = read_latest_pointer(paths.silver_root, source)
    candidate_versions: list[str] = []
    if latest_version is not None:
        candidate_versions.append(latest_version)
    for version in _numeric_silver_versions_desc(paths, source):
        if version not in candidate_versions:
            candidate_versions.append(version)

    for version in candidate_versions:
        output_dir = source_version_dir(paths.silver_root, source, version)
        if not output_dir.exists():
            continue
        stored_hash = read_inputs_module_hash(output_dir)
        if stored_hash is None:
            continue
        if stored_hash.get('sha256') == current_hash.get('sha256'):
            return version, output_dir
    return None


def _reuse_existing_result(
    task: TaskDef,
    *,
    results: dict[str, TaskResult],
    paths: PipelinePaths,
    inputs_package: str,
    resolver_mapping_dir: Path | None,
    update_pointers: bool = True,
) -> TaskResult | None:
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
        current_hash = hash_inputs_module(inputs_package, task.source)
        reusable = _matching_completed_silver_version(
            paths=paths,
            source=task.source,
            current_hash=current_hash,
        )
        if reusable is None:
            return None
        version, output_dir = reusable
        if update_pointers:
            update_latest_pointer(
                paths.silver_root,
                task.source,
                version,
                {'inputs_module_hash': current_hash},
            )
        return TaskResult(
            task_key=task.key,
            task_type=task.task_type,
            source=task.source,
            status='reused',
            output_dir=str(output_dir),
            metadata={
                'version': version,
                'inputs_module_hash': current_hash,
            },
        )

    if task.task_type == 'gold' and task.source is not None:
        silver_result = results.get(f'silver:{task.source}')
        if silver_result is not None and silver_result.status != 'reused':
            return None
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


def _planned_result_from_reuse(reused: TaskResult) -> TaskResult:
    return TaskResult(
        task_key=reused.task_key,
        task_type=reused.task_type,
        source=reused.source,
        status='reused',
        output_dir=reused.output_dir,
        metadata=reused.metadata,
    )


def _plan_pipeline_tasks(
    *,
    tasks: list[TaskDef],
    paths: PipelinePaths,
    inputs_package: str,
    resolver_mapping_dir: Path | None,
    combined_output_dir: Path,
    changed_sources: list[str],
    start_stage: str,
    postgres_uri: str | None,
    postgres_drop_existing: bool,
) -> list[PlannedTask]:
    planned: list[PlannedTask] = []
    results: dict[str, TaskResult] = {}

    for task in tasks:
        failed_dependencies = [
            dep for dep in task.deps
            if dep in results and results[dep].status in {'failed', 'skipped'}
        ]
        if failed_dependencies and not _can_run_with_failed_dependencies(
            task,
            failed_dependencies,
        ):
            planned.append(PlannedTask(
                task.key,
                task.task_type,
                task.source,
                'skip',
                f'dependency_failed={_format_names(failed_dependencies)}',
                {'failed_dependencies': failed_dependencies},
            ))
            results[task.key] = TaskResult(
                task.key,
                task.task_type,
                task.source,
                'skipped',
                None,
                {'failed_dependencies': failed_dependencies},
            )
            continue

        reused = _reuse_existing_result(
            task,
            results=results,
            paths=paths,
            inputs_package=inputs_package,
            resolver_mapping_dir=resolver_mapping_dir,
            update_pointers=False,
        )
        if reused is not None:
            detail = reused.output_dir or reused.metadata.get('reason', 'ready')
            planned.append(PlannedTask(
                task.key,
                task.task_type,
                task.source,
                'reuse',
                str(detail),
                reused.metadata,
            ))
            results[task.key] = _planned_result_from_reuse(reused)
            continue

        if task.task_type == 'combine':
            actual_changed_sources = _executed_gold_sources(results)
            if not actual_changed_sources and start_stage == 'gold':
                actual_changed_sources = changed_sources
            if not actual_changed_sources and _combined_latest_exists(combined_output_dir):
                metadata = {
                    'reason': 'no_executed_gold_sources',
                    'pipeline_incremental': True,
                    'changed_sources': [],
                    'affected_entity_count': 0,
                    'affected_relation_count': 0,
                }
                planned.append(PlannedTask(
                    task.key,
                    task.task_type,
                    task.source,
                    'reuse',
                    'no executed or requested changed gold sources',
                    metadata,
                ))
                results[task.key] = TaskResult(
                    task.key,
                    task.task_type,
                    task.source,
                    'reused',
                    str(combined_output_dir),
                    metadata,
                )
                continue

            affected = (
                _collect_affected_keys(
                    paths=paths,
                    combined_output_dir=combined_output_dir,
                    changed_sources=actual_changed_sources,
                )
                if actual_changed_sources
                else None
            )
            incremental = affected is not None
            metadata = {
                'pipeline_incremental': incremental,
                'changed_sources': actual_changed_sources,
                'affected_entity_count': len(affected.entity_keys) if affected else None,
                'affected_relation_count': len(affected.relation_keys) if affected else None,
            }
            detail = (
                'incremental '
                f'sources={_format_names(actual_changed_sources)} '
                f'entities={metadata["affected_entity_count"]} '
                f'relations={metadata["affected_relation_count"]}'
                if incremental else
                f'bootstrap output={combined_output_dir}'
            )
            planned.append(PlannedTask(
                task.key,
                task.task_type,
                task.source,
                'run',
                detail,
                metadata,
            ))
            results[task.key] = TaskResult(
                task.key,
                task.task_type,
                task.source,
                'executed',
                str(combined_output_dir),
                metadata,
            )
            continue

        if task.task_type == 'postgres':
            combine_result = results.get('combine')
            incremental = bool(
                combine_result
                and combine_result.metadata.get('pipeline_incremental')
                and not postgres_drop_existing
            )
            action = 'run' if postgres_uri else 'skip'
            detail = (
                f'action={"delta" if incremental else "bootstrap-or-noop"} '
                f'drop_existing={postgres_drop_existing}'
                if postgres_uri else
                'missing_postgres_uri'
            )
            planned.append(PlannedTask(
                task.key,
                task.task_type,
                task.source,
                action,
                detail,
                {'pipeline_incremental': incremental},
            ))
            results[task.key] = TaskResult(
                task.key,
                task.task_type,
                task.source,
                'executed' if postgres_uri else 'skipped',
                str(combined_output_dir),
                {'pipeline_incremental': incremental},
            )
            continue

        planned.append(PlannedTask(
            task.key,
            task.task_type,
            task.source,
            'run',
            _planned_run_detail(task, paths, resolver_mapping_dir),
            {},
        ))
        results[task.key] = TaskResult(
            task.key,
            task.task_type,
            task.source,
            'executed',
            None,
            {},
        )

    return planned


def _planned_run_detail(
    task: TaskDef,
    paths: PipelinePaths,
    resolver_mapping_dir: Path | None,
) -> str:
    if task.task_type == 'resolver_mappings':
        return f'output={resolver_mapping_dir or Path("id_resolver/data")}'
    if task.task_type == 'silver' and task.source is not None:
        version = next_numeric_version(paths.silver_root, task.source)
        output_dir = source_version_dir(paths.silver_root, task.source, version)
        return f'output={output_dir}'
    if task.task_type == 'gold' and task.source is not None:
        return f'output={source_stage_dir(paths.gold_root, task.source)}'
    return '-'


def _print_execution_plan(planned: list[PlannedTask]) -> None:
    print('[plan] execution plan')
    if not planned:
        print('[plan]   no tasks')
        return
    for item in planned:
        print(f'[plan]   {item.action:5} {item.task_key} -> {item.detail}')


def _confirm_execution_plan() -> None:
    try:
        input('[plan] Press Enter to execute this plan, or Ctrl+C to abort. ')
    except EOFError as exc:
        raise RuntimeError(
            'Pipeline execution requires confirmation after the plan. '
            'Run from an interactive terminal, or pass --yes to skip the prompt.'
        ) from exc


def _execute_task(
    *,
    task: TaskDef,
    results: dict[str, TaskResult],
    paths: PipelinePaths,
    inputs_package: str,
    batch_size: int,
    test_mode: bool,
    resolver_mapping_dir: Path | None,
    combined_output_dir: Path,
    combine_entity_batch_size: int,
    combine_relation_batch_size: int,
    start_stage: str,
    changed_sources: list[str],
    postgres_uri: str | None,
    postgres_schema: str,
    postgres_drop_existing: bool,
) -> TaskResult:
    phase_label = task.key
    with phase(phase_label, 'checking reuse'):
        reused = _reuse_existing_result(
            task,
            results=results,
            paths=paths,
            inputs_package=inputs_package,
            resolver_mapping_dir=resolver_mapping_dir,
        )
    if reused is not None:
        return reused

    with phase(phase_label, 'executing'):
        if task.task_type == 'resolver_mappings':
            output_dir = resolver_mapping_dir or Path('id_resolver/data')
            print(
                '[start] resolver_mappings '
                f'-> output={output_dir} test_mode={test_mode}'
            )
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
            print(
                f'[start] silver:{task.source} '
                f'-> from download/cache output={output_dir} '
                f'cache={_download_cache_dir()} test_mode={test_mode}'
            )
            metadata = build_silver_source(
                source=task.source,
                output_dir=output_dir,
                inputs_package=inputs_package,
                batch_size=batch_size,
                test_mode=test_mode,
            )
            metadata = {**metadata, 'version': version}
            pointer_metadata = {}
            inputs_hash = metadata.get('inputs_module_hash')
            if inputs_hash:
                pointer_metadata['inputs_module_hash'] = inputs_hash
            update_latest_pointer(
                paths.silver_root,
                task.source,
                version,
                pointer_metadata,
            )
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
            if silver_result is not None and silver_result.output_dir is not None:
                silver_dir = Path(silver_result.output_dir)
            else:
                silver_dir = resolve_silver_version(
                    source_stage_dir(paths.silver_root, task.source)
                )
            mapping_result = results.get('resolver_mappings')
            mapping_dir = (
                Path(mapping_result.output_dir)
                if mapping_result and mapping_result.output_dir else
                (resolver_mapping_dir or Path('id_resolver/data'))
            )
            output_dir = source_stage_dir(paths.gold_root, task.source)
            print(
                f'[start] gold:{task.source} '
                f'-> silver={silver_dir} mappings={mapping_dir} output={output_dir}'
            )
            metadata = build_gold_source(
                source=task.source,
                silver_dir=silver_dir,
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
            actual_changed_sources = _executed_gold_sources(results)
            if not actual_changed_sources and start_stage == 'gold':
                actual_changed_sources = changed_sources
            if not actual_changed_sources and _combined_latest_exists(combined_output_dir):
                return TaskResult(
                    task_key=task.key,
                    task_type=task.task_type,
                    source=None,
                    status='reused',
                    output_dir=str(combined_output_dir),
                    metadata={
                        'reason': 'no_executed_gold_sources',
                        'pipeline_incremental': True,
                        'changed_sources': [],
                        'affected_entity_keys': [],
                        'affected_relation_keys': [],
                        'affected_entity_count': 0,
                        'affected_relation_count': 0,
                    },
                )
            affected = (
                _collect_affected_keys(
                    paths=paths,
                    combined_output_dir=combined_output_dir,
                    changed_sources=actual_changed_sources,
                )
                if actual_changed_sources
                else None
            )
            if affected is None:
                print(
                    '[start] combine -> bootstrap '
                    f'gold_root={paths.gold_root} output={combined_output_dir}'
                )
            else:
                print(
                    '[start] combine -> incremental '
                    f'sources={_format_names(actual_changed_sources)} '
                    f'entities={len(affected.entity_keys)} '
                    f'relations={len(affected.relation_keys)} '
                    f'output={combined_output_dir}'
                )
            metadata = build_combined(
                gold_root=paths.gold_root,
                output_dir=combined_output_dir,
                affected_entity_keys=affected.entity_keys if affected else None,
                affected_relation_keys=affected.relation_keys if affected else None,
                inputs_package=inputs_package,
                changed_source=(
                    ','.join(actual_changed_sources)
                    if actual_changed_sources else None
                ),
                entity_batch_size=combine_entity_batch_size,
                relation_batch_size=combine_relation_batch_size,
            )
            metadata = {
                **metadata,
                'pipeline_incremental': affected is not None,
                'changed_sources': actual_changed_sources,
                'affected_entity_count': len(affected.entity_keys) if affected else 0,
                'affected_relation_count': len(affected.relation_keys) if affected else 0,
                'affected_entity_keys': (
                    sorted(affected.entity_keys) if affected else None
                ),
                'affected_relation_keys': (
                    sorted(affected.relation_keys) if affected else None
                ),
            }
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

            combine_result = results.get('combine')
            postgres_changed_sources = (
                combine_result.metadata.get('changed_sources', [])
                if combine_result else []
            )
            incremental = bool(
                combine_result
                and combine_result.metadata.get('pipeline_incremental')
                and not postgres_drop_existing
            )
            print(
                '[start] postgres '
                f'-> action={"delta" if incremental else "bootstrap-or-noop"} '
                f'schema={postgres_schema} drop_existing={postgres_drop_existing} '
                f'input={combined_output_dir}'
            )
            load_combined_schema_to_postgres(
                output_dir=combined_output_dir,
                postgres_uri=postgres_uri,
                schema=postgres_schema,
                drop_existing=postgres_drop_existing,
                affected_entity_keys=(
                    combine_result.metadata.get('affected_entity_keys')
                    if incremental and combine_result else None
                ),
                affected_relation_keys=(
                    combine_result.metadata.get('affected_relation_keys')
                    if incremental and combine_result else None
                ),
                changed_source=(
                    ','.join(postgres_changed_sources)
                    if incremental and postgres_changed_sources else None
                ),
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
    combined_output_dir: Path,
    combine_entity_batch_size: int,
    combine_relation_batch_size: int,
    start_stage: str,
    changed_sources: list[str],
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
            combined_output_dir=combined_output_dir,
            combine_entity_batch_size=combine_entity_batch_size,
            combine_relation_batch_size=combine_relation_batch_size,
            start_stage=start_stage,
            changed_sources=changed_sources,
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


@dataclass(frozen=True)
class AffectedKeys:
    entity_keys: set[str]
    relation_keys: set[str]


def _read_string_column(path: Path, column: str, *, source: str | None = None) -> set[str]:
    if not path.exists():
        return set()
    scan = pl.scan_parquet(path)
    if source is not None and 'source' in scan.collect_schema().names():
        scan = scan.filter(pl.col('source') == source)
    if column not in scan.collect_schema().names():
        return set()
    values = (
        scan
        .select(pl.col(column).cast(pl.String))
        .drop_nulls()
        .unique()
        .collect()
        .get_column(column)
        .to_list()
    )
    return {value for value in values if value}


def _read_key_hashes(
    path: Path,
    key_column: str,
    compare_columns: list[str],
    *,
    source: str | None = None,
) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame({
            key_column: pl.Series([], dtype=pl.String),
            '_row_hash': pl.Series([], dtype=pl.UInt64),
        })

    scan = pl.scan_parquet(path)
    schema_names = scan.collect_schema().names()
    if source is not None and 'source' in schema_names:
        scan = scan.filter(pl.col('source') == source)
    if key_column not in schema_names:
        return pl.DataFrame({
            key_column: pl.Series([], dtype=pl.String),
            '_row_hash': pl.Series([], dtype=pl.UInt64),
        })

    struct_columns = [
        pl.col(column) if column in schema_names else pl.lit(None).alias(column)
        for column in compare_columns
    ]
    return (
        scan
        .select([
            pl.col(key_column).cast(pl.String),
            pl.struct(struct_columns).hash().alias('_row_hash'),
        ])
        .drop_nulls(key_column)
        .unique()
        .collect()
    )


def _changed_keys_by_row_hash(
    *,
    previous_path: Path,
    current_path: Path,
    key_column: str,
    compare_columns: list[str],
    source: str,
) -> set[str]:
    previous = _read_key_hashes(
        previous_path,
        key_column,
        compare_columns,
        source=source,
    )
    current = _read_key_hashes(
        current_path,
        key_column,
        compare_columns,
    )
    if previous.is_empty() and current.is_empty():
        return set()

    changed_previous = previous.join(
        current,
        on=[key_column, '_row_hash'],
        how='anti',
    )
    changed_current = current.join(
        previous,
        on=[key_column, '_row_hash'],
        how='anti',
    )
    changed = pl.concat(
        [changed_previous.select(key_column), changed_current.select(key_column)],
        how='vertical_relaxed',
    )
    return set(changed.get_column(key_column).unique().to_list())


def _collect_affected_keys(
    *,
    paths: PipelinePaths,
    combined_output_dir: Path,
    changed_sources: list[str],
) -> AffectedKeys | None:
    latest_dir = combined_output_dir / 'latest'
    if latest_dir.is_symlink():
        latest_dir = latest_dir.resolve()
    if not latest_dir.exists():
        return None

    entity_keys: set[str] = set()
    relation_keys: set[str] = set()
    for source in changed_sources:
        source_gold_dir = source_stage_dir(paths.gold_root, source)
        entity_keys.update(_changed_keys_by_row_hash(
            previous_path=latest_dir / 'entity_evidence.parquet',
            current_path=source_gold_dir / 'entities' / 'entity_evidence.parquet',
            key_column='entity_key',
            source=source,
            compare_columns=[
                'source',
                'entity_key',
                'raw_record_ids',
                'entity_type',
                'taxonomy_id',
                'identifiers',
                'entity_attributes',
            ],
        ))
        relation_keys.update(_changed_keys_by_row_hash(
            previous_path=latest_dir / 'entity_relation_evidence.parquet',
            current_path=source_gold_dir / 'relations' / 'entity_relation_evidence.parquet',
            key_column='relation_key',
            source=source,
            compare_columns=[
                'relation_key',
                'source',
                'raw_record_id',
                'record_attributes',
                'subject_attributes',
                'object_attributes',
                'evidence',
            ],
        ))

    return AffectedKeys(
        entity_keys=entity_keys,
        relation_keys=relation_keys,
    )


def run_pipeline(
    *,
    sources: list[str],
    data_root: str | Path = 'data',
    inputs_package: str = 'pypath.inputs_v2',
    batch_size: int = 10_000,
    test_mode: bool = False,
    jobs: int = 4,
    resolver_mapping_dir: str | Path | None = None,
    start_stage: str = 'download',
    build_mappings: bool = True,
    build_sources: bool = True,
    combine: bool = True,
    combined_output_dir: str | Path | None = None,
    combine_entity_batch_size: int = 50_000,
    combine_relation_batch_size: int = 50_000,
    confirm_plan: bool = True,
    postgres_uri: str | None = None,
    postgres_schema: str = 'public',
    postgres_drop_existing: bool = False,
) -> dict[str, Any]:
    start_stage = _normalize_start_stage(start_stage)

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

    changed_sources = sorted(discovered_gold_sources or [])
    tasks = build_task_graph(
        sources=sources,
        gold_sources=discovered_gold_sources,
        build_mappings=build_mappings,
        build_sources=build_sources,
        combine=combine,
        postgres=postgres_uri is not None,
        start_stage=start_stage,
    )
    print(
        '[pipeline] '
        f'from={start_stage} test_mode={test_mode} jobs={jobs} '
        f'data_root={paths.data_root} combined={final_combined_dir}'
    )
    print(
        '[pipeline] '
        f'sources={len(sources)}:{_format_names(sources)} '
        f'gold_sources={len(changed_sources)}:{_format_names(changed_sources)}'
    )
    print(
        '[pipeline] '
        f'download_cache={_download_cache_dir()} '
        'cache hits appear as "Using existing local file from cache"; '
        'cache misses appear as "No valid version in cache".'
    )
    if test_mode:
        print(
            '[pipeline] test_mode limits selected high-volume resources after '
            'download/cache resolution; it does not bypass raw download lookup.'
        )
    planned = _plan_pipeline_tasks(
        tasks=tasks,
        paths=paths,
        inputs_package=inputs_package,
        resolver_mapping_dir=resolved_mapping_dir,
        combined_output_dir=final_combined_dir,
        changed_sources=changed_sources,
        start_stage=start_stage,
        postgres_uri=postgres_uri,
        postgres_drop_existing=postgres_drop_existing,
    )
    _print_execution_plan(planned)
    if confirm_plan:
        _confirm_execution_plan()
    start_heartbeat()
    try:
        results = _run_dag(
            tasks=tasks,
            paths=paths,
            inputs_package=inputs_package,
            batch_size=batch_size,
            test_mode=test_mode,
            jobs=jobs,
            resolver_mapping_dir=resolved_mapping_dir,
            combined_output_dir=final_combined_dir,
            combine_entity_batch_size=combine_entity_batch_size,
            combine_relation_batch_size=combine_relation_batch_size,
            start_stage=start_stage,
            changed_sources=changed_sources,
            postgres_uri=postgres_uri,
            postgres_schema=postgres_schema,
            postgres_drop_existing=postgres_drop_existing,
        )
    finally:
        stop_heartbeat()

    for source in sources:
        silver_result = results.get(f'silver:{source}')
        if silver_result and silver_result.status in {'executed', 'reused'}:
            version = silver_result.metadata.get('version')
            if version:
                pointer_metadata = {}
                inputs_hash = silver_result.metadata.get('inputs_module_hash')
                if inputs_hash:
                    pointer_metadata['inputs_module_hash'] = inputs_hash
                update_latest_pointer(paths.silver_root, source, str(version), pointer_metadata)
            elif silver_result.output_dir:
                try:
                    resolved_dir = resolve_silver_version(paths.silver_root / source.replace('.', '/'))
                    stored_hash = read_inputs_module_hash(resolved_dir)
                    pointer_metadata = {'inputs_module_hash': stored_hash} if stored_hash else None
                    update_latest_pointer(paths.silver_root, source, resolved_dir.name, pointer_metadata)
                except FileNotFoundError:
                    pass

    report = {
        'run_id': _run_id(),
        'created_at': iso_now(),
        'selected_sources': sources,
        'data_root': str(paths.data_root),
        'combined_output_dir': str(final_combined_dir),
        'inputs_package': inputs_package,
        'from_stage': start_stage,
        'changed_sources': changed_sources,
        'build_mappings': build_mappings,
        'build_sources': build_sources,
        'combine': combine,
        'postgres_enabled': postgres_uri is not None,
        'tasks': {key: asdict(value) for key, value in results.items()},
    }
    _write_report(paths, report)
    return report
