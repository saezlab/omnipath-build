#!/usr/bin/env python3
"""Build a one-off current-pipeline baseline for a deterministic 1/10 IntAct slice."""

from __future__ import annotations

import os
import sys
import json
import time
import shutil
from typing import TextIO
import hashlib
from pathlib import Path
import argparse
import threading
import subprocess
from collections.abc import Iterator

import polars as pl
import psutil

DEFAULT_OUTPUT_ROOT = Path('baseline_outputs/intact_10th_current')
WORKER_MARKER = '--worker'


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            'Run the current pipeline on a deterministic one-tenth IntAct slice '
            'and store outputs plus performance metrics in one folder.'
        ),
    )
    parser.add_argument(
        '--output-root',
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f'Baseline output directory (default: {DEFAULT_OUTPUT_ROOT})',
    )
    parser.add_argument(
        '--modulus',
        type=int,
        default=10,
        help='Keep one raw row out of this many rows (default: 10).',
    )
    parser.add_argument(
        '--remainder',
        type=int,
        default=0,
        help='Keep rows where the zero-based raw row ordinal modulo --modulus equals this value.',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=10_000,
        help='Pipeline silver batch size.',
    )
    parser.add_argument(
        '--jobs',
        type=int,
        default=1,
        help='Pipeline job count.',
    )
    parser.add_argument(
        '--combine',
        action='store_true',
        help='Also build combined artifacts under the output root.',
    )
    parser.add_argument(
        '--sample-interval-seconds',
        type=float,
        default=0.5,
        help='Parent-process memory sampling interval.',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Delete an existing output root before running.',
    )
    parser.add_argument(
        WORKER_MARKER,
        action='store_true',
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the baseline setup."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(args)
    return _run_worker(args) if args.worker else _run_parent(args)


def _validate_args(args: argparse.Namespace) -> None:
    if args.modulus <= 0:
        raise SystemExit('--modulus must be positive')
    if args.remainder < 0 or args.remainder >= args.modulus:
        raise SystemExit('--remainder must be in [0, modulus)')
    if args.batch_size <= 0:
        raise SystemExit('--batch-size must be positive')
    if args.jobs <= 0:
        raise SystemExit('--jobs must be positive')
    if args.sample_interval_seconds <= 0:
        raise SystemExit('--sample-interval-seconds must be positive')


def _run_parent(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    if output_root.exists():
        if not args.force:
            raise SystemExit(
                f'Output root already exists: {output_root}\n'
                'Re-run with --force to replace this one-off baseline folder.'
            )
        _assert_safe_to_replace(output_root)
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    log_path = output_root / 'run.log'
    metrics_path = output_root / 'metrics.json'
    worker_summary_path = output_root / 'worker_summary.json'

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        WORKER_MARKER,
        '--output-root',
        str(output_root),
        '--modulus',
        str(args.modulus),
        '--remainder',
        str(args.remainder),
        '--batch-size',
        str(args.batch_size),
        '--jobs',
        str(args.jobs),
        '--sample-interval-seconds',
        str(args.sample_interval_seconds),
    ]
    if args.combine:
        cmd.append('--combine')

    started = time.perf_counter()
    peak_rss = 0
    peak_children = 0
    samples = 0
    process = subprocess.Popen(
        cmd,
        cwd=Path.cwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    ps_process = psutil.Process(process.pid)
    with log_path.open('w', encoding='utf-8') as log_handle:
        output_thread = threading.Thread(
            target=_tee_process_output,
            args=(process, log_handle),
            daemon=True,
        )
        output_thread.start()
        while process.poll() is None:
            rss, child_count = _rss_with_children(ps_process)
            peak_rss = max(peak_rss, rss)
            peak_children = max(peak_children, child_count)
            samples += 1
            time.sleep(args.sample_interval_seconds)
        output_thread.join(timeout=30)

    elapsed = time.perf_counter() - started
    worker_summary = _read_json(worker_summary_path)
    metrics = {
        'status': 'success' if process.returncode == 0 else 'failed',
        'returncode': process.returncode,
        'elapsed_seconds': elapsed,
        'peak_rss_bytes': peak_rss,
        'peak_rss_mebibytes': peak_rss / 1024 / 1024,
        'peak_descendant_process_count': peak_children,
        'memory_sample_count': samples,
        'memory_sample_interval_seconds': args.sample_interval_seconds,
        'command': cmd,
        'output_root': str(output_root),
        'log_path': str(log_path),
        'worker_summary_path': str(worker_summary_path),
        'worker_summary': worker_summary,
        'artifacts': _collect_artifact_summary(
            output_root,
            exclude={metrics_path},
        ),
    }
    _write_json(metrics_path, metrics)
    print(f'[baseline] metrics written to {metrics_path}', flush=True)
    return int(process.returncode or 0)


def _run_worker(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    data_root = output_root / 'data'
    resolver_mapping_dir = output_root / 'id_resolver' / 'data'
    summary_path = output_root / 'worker_summary.json'
    os.environ['OMNIPATH_BRONZE_ROOT'] = str(data_root / 'bronze')

    _patch_intact_raw_parser(args.modulus, args.remainder)

    from omnipath_build.pipeline.dag import run_pipeline

    started = time.perf_counter()
    report = run_pipeline(
        sources=['intact'],
        data_root=data_root,
        inputs_package='pypath.inputs_v2',
        batch_size=args.batch_size,
        test_mode=False,
        jobs=args.jobs,
        resolver_mapping_dir=resolver_mapping_dir,
        start_stage='download',
        build_mappings=True,
        build_sources=True,
        combine=args.combine,
        combined_output_dir=data_root / 'combined',
        confirm_plan=False,
    )
    elapsed = time.perf_counter() - started
    summary = {
        'source': 'intact',
        'slice': {
            'type': 'raw_row_ordinal_modulo',
            'modulus': args.modulus,
            'remainder': args.remainder,
            'description': (
                'Rows are kept when zero_based_raw_row_ordinal % modulus == remainder.'
            ),
        },
        'elapsed_seconds_inside_worker': elapsed,
        'data_root': str(data_root),
        'resolver_mapping_dir': str(resolver_mapping_dir),
        'pipeline_report': report,
    }
    _write_json(summary_path, summary)
    print(f'[baseline] worker summary written to {summary_path}', flush=True)
    return 0


def _assert_safe_to_replace(path: Path) -> None:
    forbidden = {
        Path('/').resolve(),
        Path.cwd().resolve(),
        Path.home().resolve(),
    }
    if path in forbidden or len(path.parts) < 3:
        raise SystemExit(f'Refusing to replace unsafe output root: {path}')


def _patch_intact_raw_parser(modulus: int, remainder: int) -> None:
    import pypath.inputs_v2.intact as intact

    dataset = intact.resource.interactions
    original_parser = dataset._raw_parser

    def one_tenth_raw_parser(
        opener: object,
        **kwargs: object,
    ) -> Iterator[dict[str, object]]:
        kept = 0
        total = 0
        for total, row in enumerate(original_parser(opener, **kwargs), start=1):
            ordinal = total - 1
            if ordinal % modulus == remainder:
                kept += 1
                yield row
        print(
            '[baseline:intact] raw slice complete '
            f'kept={kept:,} total={total:,} modulus={modulus} remainder={remainder}',
            flush=True,
        )

    one_tenth_raw_parser.__name__ = (
        f'intact_raw_mod_{modulus}_remainder_{remainder}'
    )
    one_tenth_raw_parser.__qualname__ = one_tenth_raw_parser.__name__
    dataset._raw_parser = one_tenth_raw_parser


def _collect_artifact_summary(
    output_root: Path,
    *,
    exclude: set[Path] | None = None,
) -> dict[str, object]:
    data_root = output_root / 'data'
    excluded = {path.resolve() for path in exclude or set()}
    summary: dict[str, object] = {
        'parquet': {},
        'json': {},
        'files': {},
        'total_bytes': _directory_size(output_root),
    }
    for path in sorted(output_root.rglob('*')):
        if not path.is_file():
            continue
        if path.resolve() in excluded:
            continue
        rel = str(path.relative_to(output_root))
        stat = path.stat()
        summary['files'][rel] = {
            'bytes': stat.st_size,
            'sha256': _file_sha256(path),
        }
        if path.suffix == '.json':
            summary['json'][rel] = _read_json(path)
        if path.suffix == '.parquet':
            summary['parquet'][rel] = _parquet_summary(path)
    summary['stage_roots'] = {
        'bronze': str(data_root / 'bronze'),
        'silver': str(data_root / 'silver'),
        'gold': str(data_root / 'gold'),
        'combined': str(data_root / 'combined'),
    }
    return summary


def _parquet_summary(path: Path) -> dict[str, object]:
    scan = pl.scan_parquet(path)
    schema = scan.collect_schema()
    row_count = scan.select(pl.len()).collect().item()
    return {
        'rows': int(row_count),
        'columns': schema.names(),
        'schema': {name: str(dtype) for name, dtype in schema.items()},
        'bytes': path.stat().st_size,
        'sha256': _file_sha256(path),
    }


def _rss_with_children(process: psutil.Process) -> tuple[int, int]:
    processes = [process]
    try:
        processes.extend(process.children(recursive=True))
    except psutil.Error:
        pass
    total = 0
    alive = 0
    for proc in processes:
        try:
            total += proc.memory_info().rss
            alive += 1
        except psutil.Error:
            continue
    return total, alive


def _tee_process_output(
    process: subprocess.Popen[str],
    log_handle: TextIO,
) -> None:
    if process.stdout is None:
        return
    for line in process.stdout:
        print(line, end='')
        log_handle.write(line)
    log_handle.flush()


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
