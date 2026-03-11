"""Concrete task execution implementations for the DAG pipeline."""

from __future__ import annotations

import importlib
import inspect
import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

import polars as pl

from cache_manager import _freshness as cm_freshness
from pypath.share import downloads as pypath_downloads

from omnipath_build.pipeline.io_state import resolve_output_ref

if TYPE_CHECKING:
    from omnipath_build.pipeline.dag_core import TaskDef, TaskResult


_PROGRESS_PREFIX = '__OMNIPATH_PROGRESS__'


def _emit_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    **payload: Any,
) -> None:
    if callback is not None:
        callback(payload)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace('+00:00', 'Z')


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _collect_source_downloads(source: str, inputs_package: str) -> list[dict[str, Any]]:
    module = importlib.import_module(f'{inputs_package}.{source}')
    from pypath.inputs_v2.base import Dataset, Resource

    datasets: dict[str, Dataset] = {}

    resources = [obj for _, obj in inspect.getmembers(module) if isinstance(obj, Resource)]
    for resource in resources:
        for name, ds in resource.datasets().items():
            datasets.setdefault(name, ds)

    for name, obj in inspect.getmembers(module):
        if isinstance(obj, Dataset):
            datasets.setdefault(name, obj)

    out: list[dict[str, Any]] = []
    for dataset_name, dataset in sorted(datasets.items()):
        download = dataset.download
        if download is None:
            continue

        def _resolve(value: object) -> str:
            if callable(value):
                return str(value())
            return str(value)

        out.append(
            {
                'resource_id': dataset_name,
                'url': _resolve(download.url),
                'filename': _resolve(download.filename),
                'subfolder': str(download.subfolder),
                'download_kwargs': dict(download.download_kwargs or {}),
            }
        )

    return out


def _sha256_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    return _sha256_file(path)


def _freshness_scan_source(source: str, inputs_package: str) -> dict[str, Any]:
    dm = pypath_downloads.get_download_manager()
    pypath_data = pypath_downloads.DATA_DIR

    resources = _collect_source_downloads(source, inputs_package)
    if not resources:
        return {'status': 'unchanged', 'method': 'no_downloads', 'resources': []}

    changed = False
    rows: list[dict[str, Any]] = []

    for resource in resources:
        url = resource['url']
        filename = resource['filename']
        subfolder = resource['subfolder']
        target = pypath_data / subfolder / filename
        target.parent.mkdir(parents=True, exist_ok=True)

        existed_before = target.exists()
        before_sha = _sha256_or_none(target)

        remote_headers = cm_freshness.get_remote_headers(url, timeout=30)
        if not remote_headers:
            raise RuntimeError(f'Freshness headers unavailable for {source}:{resource["resource_id"]}')

        _, item, _, _ = dm._download(
            url=url,
            dest=str(target),
            check_freshness=True,
            keep_old=False,
            **resource['download_kwargs'],
        )

        after_sha = _sha256_or_none(target)
        local_meta = cm_freshness.metadata_from_item(item) if item is not None else {}
        was_changed = (not existed_before) or (before_sha != after_sha)
        changed = changed or was_changed

        rows.append(
            {
                'resource_id': resource['resource_id'],
                'url': url,
                'status': 'changed' if was_changed else 'unchanged',
                'method': 'redownload_hash_compare',
                'local': {
                    'etag': local_meta.get('etag'),
                    'last_modified': local_meta.get('last_modified'),
                    'size': local_meta.get('size'),
                    'sha256': after_sha,
                },
                'remote': {
                    'etag': remote_headers.get('ETag') or remote_headers.get('etag'),
                    'last_modified': remote_headers.get('Last-Modified') or remote_headers.get('last-modified'),
                    'size': int(remote_headers['Content-Length']) if remote_headers.get('Content-Length') else None,
                    'sha256': None,
                },
            }
        )

    return {
        'status': 'changed' if changed else 'unchanged',
        'method': 'redownload_hash_compare',
        'resources': rows,
    }


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    for path in sorted(src.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _expected_om_accessions() -> set[str]:
    from pypath.internals import cv_terms as cv_terms_module
    from pypath.internals.cv_terms import CvEnum

    terms: set[str] = set()
    for _, obj in inspect.getmembers(cv_terms_module):
        if not (inspect.isclass(obj) and issubclass(obj, CvEnum) and obj is not CvEnum):
            continue
        for member in obj:
            value = getattr(member, 'value', None)
            if isinstance(value, str) and value.startswith('OM:'):
                terms.add(value)
    return terms


def _obo_om_accessions(path: Path) -> set[str]:
    if not path.exists():
        return set()

    out: set[str] = set()
    with path.open('r', encoding='utf-8', errors='ignore') as handle:
        for line in handle:
            if line.startswith('id: OM:'):
                out.add(line.split('id: ', 1)[1].strip())
    return out


def _run_subprocess(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
) -> None:
    if log_path is None and on_stdout_line is None:
        subprocess.run(command, check=True, cwd=cwd, env=env)
        return

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    tails: dict[str, deque[str]] = {'stdout': deque(maxlen=20), 'stderr': deque(maxlen=20)}
    log_lock = threading.Lock()

    def _write_log(prefix: str, line: str) -> None:
        if log_path is None:
            return
        with log_lock:
            with log_path.open('a', encoding='utf-8') as handle:
                handle.write(f'[{prefix}] {line}\n')

    def _reader(stream, stream_name: str, callback: Callable[[str], None] | None) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ''):
                stripped = line.rstrip('\n')
                tails[stream_name].append(stripped)
                _write_log(stream_name.upper(), stripped)
                if callback is not None:
                    callback(stripped)
        finally:
            stream.close()

    if log_path is not None:
        with log_path.open('a', encoding='utf-8') as handle:
            handle.write(f"\n$ {' '.join(command)}\n")

    out_thread = threading.Thread(
        target=_reader,
        args=(proc.stdout, 'stdout', on_stdout_line),
        daemon=True,
    )
    err_thread = threading.Thread(
        target=_reader,
        args=(proc.stderr, 'stderr', None),
        daemon=True,
    )
    out_thread.start()
    err_thread.start()

    return_code = proc.wait()
    out_thread.join()
    err_thread.join()

    if return_code != 0:
        tail_lines = list(tails['stderr']) + list(tails['stdout'])
        tail = '\n'.join([t for t in tail_lines if t]).strip()
        if not tail:
            tail = f'command failed with exit code {return_code}'
        raise RuntimeError(tail)


def _ensure_project_obo(project_root: Path, *, log_path: Path | None = None) -> Path:
    obo_path = project_root / 'omnipath_build' / 'data' / 'omnipath_mi.obo'
    expected = _expected_om_accessions()
    existing = _obo_om_accessions(obo_path)

    missing = sorted(expected - existing)
    if missing:
        preview = ', '.join(missing[:5])
        suffix = '' if len(missing) <= 5 else f' ... (+{len(missing) - 5} more)'
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open('a', encoding='utf-8') as handle:
                handle.write(f'[OBO] Regenerating {obo_path} (missing OM terms: {preview}{suffix})\n')
        _run_subprocess(
            ['uv', 'run', 'python', 'pypath/scripts/export_omnipath_obo.py', str(obo_path)],
            cwd=project_root,
            log_path=log_path,
        )

        refreshed = _obo_om_accessions(obo_path)
        still_missing = sorted(expected - refreshed)
        if still_missing:
            preview = ', '.join(still_missing[:5])
            suffix = '' if len(still_missing) <= 5 else f' ... (+{len(still_missing) - 5} more)'
            raise RuntimeError(f'Regenerated OBO still missing OM terms: {preview}{suffix}')

    return obo_path


def execute_task(
    *,
    task: TaskDef,
    tmp_output: Path,
    project_root: Path,
    sources: list[str],
    task_results: dict[str, TaskResult],
    previous_state: dict[str, Any] | None,
    inputs_package: str,
    test_mode: bool,
    run_freshness_checks: bool,
    full_reindex: bool,
    log_path: Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    if task.task_type == 'freshness_scan':
        assert task.source is not None
        if run_freshness_checks:
            _emit_progress(progress_callback, stage='freshness_scan', message='checking remote freshness')
            payload = _freshness_scan_source(task.source, inputs_package)
        else:
            _emit_progress(progress_callback, stage='freshness_scan', message='freshness checks skipped')
            resources = _collect_source_downloads(task.source, inputs_package)
            payload = {
                'status': 'unchanged',
                'method': 'skipped_by_default',
                'resources': [
                    {
                        'resource_id': r['resource_id'],
                        'url': r['url'],
                        'status': 'unchanged',
                        'method': 'skipped_by_default',
                        'local': {'etag': None, 'last_modified': None, 'size': None, 'sha256': None},
                        'remote': {'etag': None, 'last_modified': None, 'size': None, 'sha256': None},
                    }
                    for r in resources
                ],
            }
        tmp_output.parent.mkdir(parents=True, exist_ok=True)
        tmp_output.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        return

    if task.task_type == 'silver':
        assert task.source is not None
        _emit_progress(progress_callback, stage='silver', message='starting silver extraction')
        with tempfile.TemporaryDirectory(prefix='op-silver-') as tmp:
            stage = Path(tmp)
            cmd = [
                'uv',
                'run',
                'python',
                '-m',
                'omnipath_build.cli.commands',
                'silver',
                '--database',
                '.',
                '--base-path',
                str(stage),
                '--source',
                task.source,
                '--inputs-package',
                inputs_package,
                '--override',
            ]
            if test_mode:
                cmd.append('--test-mode')

            env = dict(os.environ)
            env['OMNIPATH_PROGRESS_STDOUT'] = '1'

            def _on_silver_stdout(line: str) -> None:
                if not line.startswith(_PROGRESS_PREFIX):
                    return
                payload_raw = line[len(_PROGRESS_PREFIX):]
                try:
                    payload = json.loads(payload_raw)
                except json.JSONDecodeError:
                    return
                function = str(payload.get('function', 'unknown'))
                output = payload.get('output')
                key = f'{function}:{output}' if output else function
                records = int(payload.get('records', 0))
                if progress_callback is not None:
                    progress_callback(
                        {
                            'stage': 'silver',
                            'function': key,
                            'records': records,
                        }
                    )

            _run_subprocess(
                cmd,
                cwd=project_root,
                env=env,
                log_path=log_path,
                on_stdout_line=_on_silver_stdout,
            )

            _emit_progress(progress_callback, stage='silver', message='copying silver parquet outputs')
            out_root = stage / 'silver'
            tmp_output.mkdir(parents=True, exist_ok=True)
            for parquet in sorted(out_root.rglob('*.parquet')):
                shutil.copy2(parquet, tmp_output / parquet.name)
        return

    if task.task_type == 'local_gold':
        assert task.source is not None
        dep_dir = resolve_output_ref(task_results[task.deps[0]].output_ref)

        _emit_progress(progress_callback, stage='local_gold', message='preparing local gold inputs')
        with tempfile.TemporaryDirectory(prefix='op-local-gold-') as tmp:
            stage = Path(tmp)
            silver_dir = stage / 'silver'
            silver_dir.mkdir(parents=True, exist_ok=True)
            for parquet in sorted(dep_dir.glob('*.parquet')):
                shutil.copy2(parquet, silver_dir / parquet.name)

            gold_dir = stage / 'gold'
            cmd = [
                'uv',
                'run',
                'python',
                '-m',
                'omnipath_build.cli.commands',
                'gold',
                '--data-root',
                str(silver_dir),
                '--output-dir',
                str(gold_dir),
                '--step',
                'local_tables',
                '--source',
                task.source,
            ]
            _emit_progress(progress_callback, stage='local_gold', message='building local gold tables')
            _run_subprocess(cmd, cwd=project_root, log_path=log_path)

            _emit_progress(progress_callback, stage='local_gold', message='writing local gold report')
            tmp_output.mkdir(parents=True, exist_ok=True)
            _copy_tree(gold_dir / 'local_tables', tmp_output)

            function_records: dict[str, int] = {}
            for parquet in sorted(silver_dir.glob('*.parquet')):
                function_records[parquet.stem] = int(pl.scan_parquet(parquet).select(pl.len()).collect().item())
            (tmp_output / 'report.json').write_text(
                json.dumps(
                    {
                        'source': task.source,
                        'silver': {'status': 'ok', 'function_records': function_records},
                        'local_tables': {'status': 'ok'},
                        'overall_status': 'ok',
                        'finished_at': _iso(_utc_now()),
                    },
                    indent=2,
                )
                + '\n',
                encoding='utf-8',
            )
        return

    if task.task_type == 'combined_gold':
        _emit_progress(progress_callback, stage='combined_gold', message='collecting per-source local tables')
        with tempfile.TemporaryDirectory(prefix='op-combined-gold-') as tmp:
            stage = Path(tmp)
            local_tables_dir = stage / 'local_tables'
            local_tables_dir.mkdir(parents=True, exist_ok=True)

            for dep_key in task.deps:
                dep_dir = resolve_output_ref(task_results[dep_key].output_ref)
                for parquet in sorted(dep_dir.glob('local_*.parquet')):
                    shutil.copy2(parquet, local_tables_dir / parquet.name)

            _emit_progress(progress_callback, stage='combined_gold', message='ensuring OmniPath ontology (OBO)')
            project_obo = _ensure_project_obo(project_root, log_path=log_path)

            out_dir = stage / 'gold'
            out_dir.mkdir(parents=True, exist_ok=True)
            cmd_entity = [
                'uv',
                'run',
                'python',
                '-m',
                'omnipath_build.cli.commands',
                'gold',
                '--step',
                'entity_identifiers',
                '--output-dir',
                str(out_dir),
                '--local-tables-dir',
                str(local_tables_dir),
            ]
            _emit_progress(progress_callback, stage='combined_gold', message='building entity identifiers')
            _run_subprocess(cmd_entity, cwd=project_root, log_path=log_path)

            cmd_global = [
                'uv',
                'run',
                'python',
                '-m',
                'omnipath_build.cli.commands',
                'gold',
                '--step',
                'global_tables',
                '--output-dir',
                str(out_dir),
                '--local-tables-dir',
                str(local_tables_dir),
            ]
            _emit_progress(progress_callback, stage='combined_gold', message='building global tables')
            _run_subprocess(cmd_global, cwd=project_root, log_path=log_path)

            _emit_progress(progress_callback, stage='combined_gold', message='copying combined gold outputs')
            tmp_output.mkdir(parents=True, exist_ok=True)
            _copy_tree(out_dir, tmp_output)
            shutil.copy2(project_obo, tmp_output / 'omnipath_mi.obo')
        return

    if task.task_type in {'search_entities', 'search_interactions', 'search_associations'}:
        dep_dir = resolve_output_ref(task_results[task.deps[0]].output_ref)
        dataset = task.task_type.split('_', 1)[1]
        _emit_progress(progress_callback, stage=task.task_type, message=f'building search parquet: {dataset}')

        with tempfile.TemporaryDirectory(prefix='op-search-') as tmp:
            stage = Path(tmp)
            gold_dir = stage / 'gold'
            _copy_tree(dep_dir, gold_dir)
            (gold_dir / 'omnipath_mi.obo').unlink(missing_ok=True)

            out_file = stage / f'search_{dataset}.parquet'
            module = f'omnipath_build.search_builder.build_search_{dataset}'
            _run_subprocess(
                [
                    'uv',
                    'run',
                    'python',
                    '-m',
                    module,
                    '--global-tables-dir',
                    str(gold_dir),
                    '--output',
                    str(out_file),
                ],
                cwd=project_root,
                log_path=log_path,
            )
            tmp_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_file, tmp_output)
        return

    if task.task_type == 'search_sources':
        _emit_progress(progress_callback, stage='search_sources', message='building search parquet: sources')
        with tempfile.TemporaryDirectory(prefix='op-search-sources-') as tmp:
            stage = Path(tmp)
            per_source = stage / 'per_source'
            reports = per_source / 'reports'
            combined_gold = stage / 'combined' / 'gold'
            reports.mkdir(parents=True, exist_ok=True)
            combined_gold.mkdir(parents=True, exist_ok=True)

            combined_dep = resolve_output_ref(task_results['combined_gold'].output_ref)
            _copy_tree(combined_dep, combined_gold)

            for source in sources:
                source_dir = per_source / source / 'silver'
                source_dir.mkdir(parents=True, exist_ok=True)
                silver_dep = resolve_output_ref(task_results[f'silver:{source}'].output_ref)
                local_dep = resolve_output_ref(task_results[f'local_gold:{source}'].output_ref)
                _copy_tree(silver_dep, source_dir)
                report_src = local_dep / 'report.json'
                if report_src.exists():
                    shutil.copy2(report_src, reports / f'{source}.json')

            out_file = stage / 'search_sources.parquet'
            _run_subprocess(
                [
                    'uv',
                    'run',
                    'python',
                    '-m',
                    'omnipath_build.search_builder.build_sources',
                    '--per-source-root',
                    str(per_source),
                    '--output',
                    str(out_file),
                ],
                cwd=project_root,
                log_path=log_path,
            )
            tmp_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_file, tmp_output)
        return

    if task.task_type == 'index_import':
        assert task.source is not None
        dataset = task.source
        _emit_progress(progress_callback, stage='index_import', message=f'importing Meilisearch dataset: {dataset}')

        search_dep = resolve_output_ref(task_results[task.deps[0]].output_ref)
        parquet = search_dep

        previous_parquet: Path | None = None
        if previous_state is not None:
            prev_search = previous_state['tasks'].get(task.deps[0])
            if prev_search:
                candidate = resolve_output_ref(prev_search['output_ref'])
                if candidate.exists():
                    previous_parquet = candidate

        meili_url = os.environ.get('MEILI_URL') or os.environ.get('MEILISEARCH_URL') or 'http://localhost:7700'
        api_key = os.environ.get('MEILI_API_KEY') or os.environ.get('MEILISEARCH_API_KEY')

        dataset_arg_by_name = {
            'entities': '--entities-parquet-path',
            'interactions': '--interactions-parquet-path',
            'associations': '--associations-parquet-path',
            'sources': '--sources-parquet-path',
        }
        previous_arg_by_name = {
            'entities': '--previous-entities-parquet-path',
            'interactions': '--previous-interactions-parquet-path',
            'associations': '--previous-associations-parquet-path',
            'sources': '--previous-sources-parquet-path',
        }

        cmd = [
            'uv',
            'run',
            'python',
            '-m',
            'omnipath_build.search.importer',
            '--dataset',
            dataset,
            '--meili-url',
            meili_url,
            '--importer-path',
            str(project_root / 'omnipath_build' / 'meilisearch-importer'),
            '--batch-size',
            '100MB',
            '--format',
            'parquet',
            '--delete-batch-size',
            '10000',
            dataset_arg_by_name[dataset],
            str(parquet),
        ]
        if previous_parquet is not None:
            cmd.extend([previous_arg_by_name[dataset], str(previous_parquet)])
        if full_reindex:
            cmd.append('--full-reindex')
        if api_key:
            cmd.extend(['--api-key', api_key])

        _run_subprocess(cmd, cwd=project_root, log_path=log_path)

        tmp_output.parent.mkdir(parents=True, exist_ok=True)
        tmp_output.write_text(
            json.dumps({'dataset': dataset, 'status': 'imported'}, indent=2) + '\n',
            encoding='utf-8',
        )
        return

    raise ValueError(f'Unsupported task type: {task.task_type}')
