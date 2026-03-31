from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from omnipath_build.loaders.silver import _PROGRESS_PREFIX

from .resolution import ProgressCallback


def _emit_progress(progress: ProgressCallback | None, **event: Any) -> None:
    if progress is not None:
        progress(event)


def silver_dir_ready(silver_dir: Path) -> bool:
    return silver_dir.exists() and any(silver_dir.glob('*.parquet'))


def ensure_silver_dir(
    *,
    silver_dir: Path,
    source_name: str,
    inputs_package: str = 'pypath.inputs_v2',
    progress: ProgressCallback | None = None,
) -> Path:
    silver_dir = Path(silver_dir)
    if silver_dir_ready(silver_dir):
        return silver_dir

    _emit_progress(
        progress,
        stage='silver',
        event='started',
        source=source_name,
        silver_dir=str(silver_dir),
        message='building silver because requested silver_dir is missing or empty',
    )

    project_root = Path(__file__).resolve().parents[2]
    record_counts: dict[str, int] = {}

    with tempfile.TemporaryDirectory(prefix=f'op-package-silver-{source_name}-') as tmp:
        stage = Path(tmp)
        cmd = [
            sys.executable,
            '-m',
            'omnipath_build.cli.commands',
            'silver',
            '--database',
            '.',
            '--base-path',
            str(stage),
            '--source',
            source_name,
            '--inputs-package',
            inputs_package,
            '--override',
        ]
        env = dict(os.environ)
        env['OMNIPATH_PROGRESS_STDOUT'] = '1'

        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip('\n')
            if text.startswith(_PROGRESS_PREFIX):
                try:
                    payload = json.loads(text[len(_PROGRESS_PREFIX):])
                except json.JSONDecodeError:
                    continue
                function = str(payload.get('function', 'unknown'))
                records = int(payload.get('records', 0) or 0)
                record_counts[function] = records
                _emit_progress(
                    progress,
                    stage='silver',
                    event='progress',
                    source=source_name,
                    function=function,
                    function_records=records,
                    total_records=sum(record_counts.values()),
                )
            elif text:
                _emit_progress(
                    progress,
                    stage='silver',
                    event='log',
                    source=source_name,
                    message=text,
                )
        return_code = proc.wait()
        if return_code != 0:
            raise RuntimeError(f'Silver build failed for {source_name} with exit code {return_code}')

        built_source_dir = stage / 'silver' / source_name
        if not built_source_dir.exists():
            raise RuntimeError(f'Silver loader did not produce expected directory: {built_source_dir}')

        silver_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for path in sorted(built_source_dir.iterdir()):
            if path.is_file() and path.suffix == '.parquet':
                shutil.copy2(path, silver_dir / path.name)
                copied += 1

    if copied == 0:
        raise RuntimeError(f'No parquet files were produced for source {source_name} into {silver_dir}')

    _emit_progress(
        progress,
        stage='silver',
        event='finished',
        source=source_name,
        silver_dir=str(silver_dir),
        parquet_files=copied,
        total_records=sum(record_counts.values()),
    )
    return silver_dir
