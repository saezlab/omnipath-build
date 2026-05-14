from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'pypath'))
sys.path.insert(0, str(ROOT / 'download-manager'))
sys.path.insert(0, str(ROOT / 'cache-manager'))
sys.path.insert(0, str(ROOT / '.venv' / 'lib' / 'python3.12' / 'site-packages'))
sys.path.insert(0, str(ROOT / 'pypath' / '.venv' / 'lib' / 'python3.12' / 'site-packages'))

from minimal.ingest.preparse import (
    accept_raw_snapshot,
    materialize_raw_records,
)


def _run_dirs(dataset_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in dataset_dir.iterdir()
        if path.is_dir() and path.name != 'state'
    )


def test_materialize_raw_records_skips_new_snapshot_when_unchanged(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'raw-records'
    dataset_dir = root / 'demo' / 'items'

    first = materialize_raw_records(
        records=[{'id': 'a'}, {'id': 'b'}],
        source='demo',
        dataset='items',
        output_root=root,
    )
    accept_raw_snapshot(first)
    first_run_dirs = _run_dirs(dataset_dir)

    second = materialize_raw_records(
        records=[{'id': 'a'}, {'id': 'b'}],
        source='demo',
        dataset='items',
        output_root=root,
    )

    assert second.snapshot_id == first.snapshot_id
    assert second.records_path == dataset_dir / 'state' / 'records'
    assert _run_dirs(dataset_dir) == first_run_dirs


def test_materialize_raw_records_writes_new_snapshot_when_changed(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'raw-records'
    dataset_dir = root / 'demo' / 'items'

    first = materialize_raw_records(
        records=[{'id': 'a'}, {'id': 'b'}],
        source='demo',
        dataset='items',
        output_root=root,
    )
    accept_raw_snapshot(first)

    second = materialize_raw_records(
        records=[{'id': 'a'}, {'id': 'c'}],
        source='demo',
        dataset='items',
        output_root=root,
    )

    assert second.snapshot_id != first.snapshot_id
    assert len(_run_dirs(dataset_dir)) == 2
    manifest = json.loads(second.manifest_path.read_text())
    assert manifest['delta_rows_by_type'] == {'added': 1, 'removed': 1}
