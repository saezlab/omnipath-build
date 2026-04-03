from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from omnipath_build.silver.build import run_silver_loader


def _emit_progress(progress: Callable[[dict[str, Any]], None] | None, **event: Any) -> None:
    if progress is not None:
        progress(event)


def silver_dir_ready(silver_dir: Path) -> bool:
    return silver_dir.exists() and any(silver_dir.glob('*.parquet'))


def ensure_silver_dir(
    *,
    silver_dir: Path,
    source_name: str,
    inputs_package: str = 'pypath.inputs_v2',
    progress: Callable[[dict[str, Any]], None] | None = None,
    test_mode: bool = False,
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

    record_counts: dict[str, int] = {}

    with tempfile.TemporaryDirectory(prefix=f'op-package-silver-{source_name}-') as tmp:
        stage = Path(tmp)
        _, _, selected_functions, outputs = run_silver_loader(
            database='.',
            base_path=stage,
            source=source_name,
            list_only=False,
            batch_size=10_000,
            dry_run=False,
            override=True,
            test_mode=test_mode,
            inputs_package=inputs_package,
        )
        if selected_functions and outputs:
            for fn, output in zip(selected_functions, outputs, strict=False):
                if output is None:
                    continue
                record_counts[fn.function_name] = record_counts.get(fn.function_name, 0) + 1
                _emit_progress(
                    progress,
                    stage='silver',
                    event='progress',
                    source=source_name,
                    function=fn.function_name,
                    function_records=record_counts[fn.function_name],
                    total_records=sum(record_counts.values()),
                )

        built_source_dir = stage / 'silver' / source_name
        if not built_source_dir.exists():
            built_source_dir = stage / 'silver' / source_name.replace('.', '/')
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
