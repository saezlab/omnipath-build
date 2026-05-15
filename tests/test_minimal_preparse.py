from __future__ import annotations

import json
from pathlib import Path
import sys

import duckdb

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
    canonical_raw_row_bytes,
    materialize_raw_records,
    raw_record_bucket,
    raw_record_hash,
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
    assert manifest['mode'] == 'incremental'
    assert manifest['delta_rows_by_type'] == {'added': 1, 'removed': 1}
    assert manifest['identity'] == {
        'algorithm': 'sha256',
        'canonicalization_version': 1,
        'type': 'content_hash',
    }
    assert manifest['partitioning'] == {
        'bucket_count': 512,
        'partition_bits': 9,
        'scheme': 'hash_prefix',
    }
    assert manifest['largest_bucket_size'] >= 1
    assert (second.delta_path / 'added').exists()
    assert (second.delta_path / 'removed').exists()
    assert manifest['changed_bucket_count'] >= 1
    assert manifest['skipped_bucket_count'] < manifest['partitioning']['bucket_count']

    added_bucket = raw_record_bucket(raw_record_hash({'id': 'c'}))
    removed_bucket = raw_record_bucket(raw_record_hash({'id': 'b'}))
    assert (
        second.delta_path
        / 'added'
        / f'raw_record_bucket={added_bucket:03d}'
        / 'data.parquet'
    ).exists()
    assert (
        second.delta_path
        / 'removed'
        / f'raw_record_bucket={removed_bucket:03d}'
        / 'data.parquet'
    ).exists()


def test_initial_snapshot_uses_bootstrap_mode_without_added_delta(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'raw-records'

    snapshot = materialize_raw_records(
        records=[{'id': 'a'}, {'id': 'b'}],
        source='demo',
        dataset='items',
        output_root=root,
    )

    manifest = json.loads(snapshot.manifest_path.read_text())
    assert manifest['mode'] == 'bootstrap'
    assert manifest['delta_rows_by_type'] == {}
    assert (snapshot.index_path / 'raw_record_bucket=0000').exists() or any(
        snapshot.index_path.glob('raw_record_bucket=*')
    )


def test_raw_record_identity_is_canonical_sha256_binary(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'raw-records'
    row = {'b': 2, 'a': None}
    same_row = {'a': None, 'b': 2.0}
    changed_row = {'a': None, 'b': 3}

    assert canonical_raw_row_bytes(row) == canonical_raw_row_bytes(same_row)
    assert raw_record_hash(row) == raw_record_hash(same_row)
    assert raw_record_hash(row) != raw_record_hash(changed_row)

    snapshot = materialize_raw_records(
        records=[row],
        source='demo',
        dataset='items',
        output_root=root,
    )
    expected_hash = raw_record_hash(row)
    expected_bucket = raw_record_bucket(expected_hash)

    con = duckdb.connect()
    try:
        raw_id, bucket = con.execute(
            f"""
            SELECT _raw_record_id, raw_record_bucket
            FROM read_parquet('{snapshot.records_path}/**/*.parquet', union_by_name=true)
            """
        ).fetchone()
    finally:
        con.close()

    assert bytes(raw_id) == expected_hash
    assert len(bytes(raw_id)) == 32
    assert int(bucket) == expected_bucket


def test_materialize_raw_records_promotes_all_null_first_batch_columns(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'raw-records'

    snapshot = materialize_raw_records(
        records=[
            {'id': '1', 'optional': None},
            {'id': '2', 'optional': 'later'},
        ],
        source='demo',
        dataset='items',
        output_root=root,
        batch_size=1,
    )

    con = duckdb.connect()
    try:
        values = con.execute(
            f"""
            SELECT optional
            FROM read_parquet('{snapshot.records_path}/**/*.parquet', union_by_name=true)
            ORDER BY id
            """
        ).fetchall()
    finally:
        con.close()

    assert values == [(None,), ('later',)]
