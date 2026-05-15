from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import unicodedata

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

PREPARSE_VERSION = 'minimal_raw_records_v1'
CANONICALIZATION_VERSION = 1
RAW_RECORD_HASH_ALGORITHM = 'sha256'
RAW_RECORD_PARTITION_BITS = 9
RAW_RECORD_BUCKET_COUNT = 2**RAW_RECORD_PARTITION_BITS
METADATA_COLUMNS = {
    '_source',
    '_dataset',
    '_raw_record_key',
    '_raw_record_id',
    'raw_record_bucket',
    'raw_record_part',
}


@dataclass(frozen=True)
class RawSnapshot:
    source: str
    dataset: str
    snapshot_id: str
    records_path: Path
    index_path: Path
    delta_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class RawRecordProvenance:
    source: str
    dataset: str
    snapshot_id: str
    raw_record_key: str
    raw_record_id: bytes
    raw_record_bucket: int


@dataclass(frozen=True)
class ProvenancedRecord:
    record: Any
    provenance: RawRecordProvenance


def default_raw_records_root() -> Path:
    return Path(
        os.environ.get(
            'OMNIPATH_MINIMAL_RAW_RECORDS_ROOT',
            os.environ.get('OMNIPATH_RAW_RECORDS_ROOT', 'data'),
        )
    )


def preparse_dataset(
    dataset_obj: object,
    *,
    source: str,
    dataset: str,
    raw_records_root: str | Path | None = None,
    force_refresh: bool = False,
) -> RawSnapshot:
    raw_parser = getattr(dataset_obj, '_raw_parser')
    download = getattr(dataset_obj, 'download', None)
    opener = (
        download.open(force_refresh=force_refresh)
        if download is not None
        else None
    )
    records = raw_parser(opener, force_refresh=force_refresh)
    snapshot = materialize_raw_records(
        records=records,
        source=source,
        dataset=dataset,
        output_root=(
            Path(raw_records_root)
            if raw_records_root
            else default_raw_records_root()
        ),
    )
    setattr(dataset_obj, '_minimal_raw_snapshot', snapshot)
    return snapshot


def load_latest_raw_snapshot(
    *,
    source: str,
    dataset: str,
    raw_records_root: str | Path | None = None,
) -> RawSnapshot:
    root = Path(raw_records_root) if raw_records_root else default_raw_records_root()
    dataset_dir = root / source / dataset
    latest = _read_latest(dataset_dir)
    if latest is None:
        raise FileNotFoundError(
            f'No accepted preparse snapshot found for {source}.{dataset} '
            f'under {dataset_dir}. Run preparse first.'
        )
    return RawSnapshot(
        source=str(latest.get('source') or source),
        dataset=str(latest.get('dataset') or dataset),
        snapshot_id=str(latest['snapshot_id']),
        records_path=Path(latest['records_path']),
        index_path=Path(
            latest.get('index_path')
            or Path(latest['records_path']).parent / 'index'
        ),
        delta_path=Path(latest['delta_path']),
        manifest_path=Path(latest['manifest_path']),
    )


def iter_mapped_records(
    dataset_obj: object,
    snapshot: RawSnapshot,
    *,
    changed_only: bool = False,
) -> Iterator[ProvenancedRecord]:
    mapper = getattr(dataset_obj, 'mapper')
    raw_rows = (
        iter_changed_raw_record_dicts(snapshot.records_path, snapshot.delta_path)
        if changed_only
        else iter_raw_record_dicts(snapshot.records_path)
    )
    for raw_row in raw_rows:
        record = {
            key: value
            for key, value in raw_row.items()
            if key not in METADATA_COLUMNS
        }
        mapped = mapper(record)
        if mapped is None:
            continue
        yield ProvenancedRecord(
            record=mapped,
            provenance=RawRecordProvenance(
                source=str(raw_row.get('_source') or snapshot.source),
                dataset=str(raw_row.get('_dataset') or snapshot.dataset),
                snapshot_id=snapshot.snapshot_id,
                raw_record_key=str(raw_row['_raw_record_key']),
                raw_record_id=_raw_record_id_bytes(raw_row['_raw_record_id']),
                raw_record_bucket=int(raw_row.get('raw_record_bucket') or 0),
            ),
        )


def accept_dataset_preparse(dataset_obj: object) -> None:
    snapshot = getattr(dataset_obj, '_minimal_raw_snapshot', None)
    if snapshot is not None:
        accept_raw_snapshot(snapshot)
        delattr(dataset_obj, '_minimal_raw_snapshot')


def materialize_raw_records(
    *,
    records: Iterable[dict[str, Any]],
    source: str,
    dataset: str,
    output_root: Path | None = None,
    batch_size: int = 50_000,
) -> RawSnapshot:
    output_root = output_root or default_raw_records_root()
    dataset_dir = output_root / source / dataset
    snapshot_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    snapshot_dir = dataset_dir / snapshot_id
    records_path = snapshot_dir / 'records'
    index_path = snapshot_dir / 'index'
    delta_path = snapshot_dir / 'delta'
    manifest_path = snapshot_dir / 'manifest.json'

    latest = _read_latest(dataset_dir)
    old_snapshot_id = latest.get('snapshot_id') if latest else None
    old_records = Path(latest['records_path']) if latest else None
    old_index = (
        Path(str(latest['index_path']))
        if latest and latest.get('index_path')
        else (
            Path(latest['records_path']).parent / 'index'
            if latest and latest.get('records_path')
            else None
        )
    )
    if old_records is not None and not old_records.exists():
        old_records = None
        old_index = None
        old_snapshot_id = None
    if old_index is not None and not old_index.exists():
        old_index = None

    partitioning = _partitioning_config_from_latest(latest)
    _assert_supported_partitioning(partitioning)

    dataset_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(UTC)
    print(
        f'[minimal-preparse:{source}.{dataset}] start '
        f'snapshot={snapshot_id} previous={old_snapshot_id or "-"}',
        flush=True,
    )
    with tempfile.TemporaryDirectory(dir=snapshot_dir.parent) as tmpdir:
        tmp_records = Path(tmpdir) / 'records'
        tmp_index = Path(tmpdir) / 'index'
        tmp_delta = Path(tmpdir) / 'delta'
        stats = _write_records(
            records,
            output_path=tmp_records,
            index_path=tmp_index,
            source=source,
            dataset=dataset,
            batch_size=batch_size,
            partition_bits=int(partitioning['partition_bits']),
            bucket_count=int(partitioning['bucket_count']),
        )
        delta_stats = _write_delta(
            new_index=tmp_index,
            old_index=old_index,
            output_path=tmp_delta,
            partitioning=partitioning,
        )
        if old_records is not None and not delta_stats['delta_rows_by_type']:
            print(
                f'[minimal-preparse:{source}.{dataset}] unchanged '
                f'latest={old_snapshot_id}',
                flush=True,
            )
            return RawSnapshot(
                source=str(latest.get('source') or source),
                dataset=str(latest.get('dataset') or dataset),
                snapshot_id=str(latest['snapshot_id']),
                records_path=Path(latest['records_path']),
                index_path=Path(
                    latest.get('index_path')
                    or Path(latest['records_path']).parent / 'index'
                ),
                delta_path=Path(latest['delta_path']),
                manifest_path=Path(latest['manifest_path']),
            )

        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_records), records_path)
        shutil.move(str(tmp_index), index_path)
        shutil.move(str(tmp_delta), delta_path)

    manifest = {
        'source': source,
        'dataset': dataset,
        'snapshot_id': snapshot_id,
        'previous_snapshot_id': old_snapshot_id,
        'mode': 'incremental' if old_records is not None else 'bootstrap',
        'created_at': started.isoformat(),
        'completed_at': datetime.now(UTC).isoformat(),
        'preparse_version': PREPARSE_VERSION,
        'identity': {
            'type': 'content_hash',
            'algorithm': RAW_RECORD_HASH_ALGORITHM,
            'canonicalization_version': CANONICALIZATION_VERSION,
        },
        'partitioning': partitioning,
        'records_path': str(records_path),
        'index_path': str(index_path),
        'delta_path': str(delta_path),
        **stats,
        **delta_stats,
    }
    _write_json(manifest_path, manifest)
    print(
        f'[minimal-preparse:{source}.{dataset}] done '
        f'rows={manifest.get("rows", 0):,} '
        f'delta={manifest.get("delta_rows_by_type", {})}',
        flush=True,
    )
    return RawSnapshot(
        source=source,
        dataset=dataset,
        snapshot_id=snapshot_id,
        records_path=records_path,
        index_path=index_path,
        delta_path=delta_path,
        manifest_path=manifest_path,
    )


def accept_raw_snapshot(snapshot: RawSnapshot) -> None:
    dataset_dir = snapshot.records_path.parent.parent
    state_dir = dataset_dir / 'state'
    state_records_path = state_dir / 'records'
    state_index_path = state_dir / 'index'
    state_dir.mkdir(parents=True, exist_ok=True)

    accepted_records_path = snapshot.records_path
    accepted_index_path = snapshot.index_path
    if snapshot.records_path != state_records_path:
        tmp_state_records_path = state_dir / 'records.tmp'
        if tmp_state_records_path.exists():
            shutil.rmtree(tmp_state_records_path)
        shutil.move(str(snapshot.records_path), tmp_state_records_path)
        if state_records_path.exists():
            shutil.rmtree(state_records_path)
        tmp_state_records_path.replace(state_records_path)
        accepted_records_path = state_records_path

    if snapshot.index_path.exists() and snapshot.index_path != state_index_path:
        tmp_state_index_path = state_dir / 'index.tmp'
        if tmp_state_index_path.exists():
            shutil.rmtree(tmp_state_index_path)
        shutil.move(str(snapshot.index_path), tmp_state_index_path)
        if state_index_path.exists():
            shutil.rmtree(state_index_path)
        tmp_state_index_path.replace(state_index_path)
        accepted_index_path = state_index_path

    _rewrite_manifest_paths(
        snapshot.manifest_path,
        records_path=accepted_records_path,
        index_path=accepted_index_path,
    )
    _write_json(
        dataset_dir / 'latest.json',
        {
            'source': snapshot.source,
            'dataset': snapshot.dataset,
            'snapshot_id': snapshot.snapshot_id,
            'records_path': str(accepted_records_path),
            'index_path': str(accepted_index_path),
            'delta_path': str(snapshot.delta_path),
            'manifest_path': str(snapshot.manifest_path),
            'updated_at': datetime.now(UTC).isoformat(),
        },
    )


def iter_raw_record_dicts(records_path: Path) -> Iterator[dict[str, Any]]:
    dataset = ds.dataset(records_path, format='parquet')
    for batch in dataset.to_batches(batch_size=10_000):
        table = pa.Table.from_batches([batch], schema=batch.schema)
        yield from table.to_pylist()


def iter_changed_raw_record_dicts(
    records_path: Path,
    delta_path: Path,
) -> Iterator[dict[str, Any]]:
    added_path = delta_path / 'added'
    if not _has_parquet_files(added_path):
        return
    con = duckdb.connect()
    try:
        reader = con.execute(
            f"""
            SELECT r.*
            FROM {_read_records_sql(records_path)} r
            JOIN {_read_records_sql(added_path)} d USING (_raw_record_id)
            """
        ).fetch_record_batch(rows_per_batch=10_000)
        while True:
            try:
                batch = reader.read_next_batch()
            except StopIteration:
                break
            table = pa.Table.from_batches([batch])
            yield from table.to_pylist()
    finally:
        con.close()


def _write_records(
    records: Iterable[dict[str, Any]],
    *,
    output_path: Path,
    index_path: Path,
    source: str,
    dataset: str,
    batch_size: int,
    partition_bits: int,
    bucket_count: int,
) -> dict[str, Any]:
    if output_path.exists():
        shutil.rmtree(output_path)
    if index_path.exists():
        shutil.rmtree(index_path)
    output_path.mkdir(parents=True, exist_ok=True)
    index_path.mkdir(parents=True, exist_ok=True)
    record_writers: dict[int, pq.ParquetWriter] = {}
    index_writers: dict[int, pq.ParquetWriter] = {}
    batch: list[dict[str, Any]] = []
    schema_names: list[str] | None = None
    schema: pa.Schema | None = None
    index_schema = pa.schema(
        [
            pa.field('_raw_record_id', pa.binary(32)),
            pa.field('raw_record_bucket', pa.int64()),
        ]
    )
    rows = 0
    bucket_counts: dict[int, int] = {}
    bucket_width = _bucket_width(bucket_count)

    def flush() -> None:
        nonlocal batch, schema_names, schema
        if not batch:
            return
        if schema_names is None:
            seen: dict[str, None] = {}
            for row in batch:
                for name in row:
                    seen.setdefault(name, None)
            schema_names = list(seen)
        normalized = [
            {
                name: (
                    _raw_record_id_bytes(row.get(name))
                    if name == '_raw_record_id' and row.get(name) is not None
                    else _stringify_if_unsupported(row.get(name))
                )
                for name in schema_names
            }
            for row in batch
        ]
        table = pa.Table.from_pylist(normalized)
        if schema is None:
            schema = _schema_with_storable_nulls(table.schema)
        table = table.cast(schema, safe=False)
        for bucket in sorted(set(table.column('raw_record_bucket').to_pylist())):
            if bucket is None:
                continue
            bucket_int = int(bucket)
            bucket_table = table.filter(
                pc.equal(
                    table.column('raw_record_bucket'),
                    pa.scalar(bucket_int, type=pa.int64()),
                )
            )
            writer = record_writers.get(bucket_int)
            if writer is None:
                bucket_dir = output_path / _bucket_dir_name(
                    bucket_int,
                    bucket_count=bucket_count,
                )
                bucket_dir.mkdir(parents=True, exist_ok=True)
                writer = pq.ParquetWriter(
                    bucket_dir / 'data.parquet',
                    schema,
                    compression='zstd',
                    use_dictionary=True,
                )
                record_writers[bucket_int] = writer
            writer.write_table(bucket_table)

            index_writer = index_writers.get(bucket_int)
            if index_writer is None:
                index_bucket_dir = index_path / _bucket_dir_name(
                    bucket_int,
                    bucket_count=bucket_count,
                )
                index_bucket_dir.mkdir(parents=True, exist_ok=True)
                index_writer = pq.ParquetWriter(
                    index_bucket_dir / 'data.parquet',
                    index_schema,
                    compression='zstd',
                    use_dictionary=True,
                )
                index_writers[bucket_int] = index_writer
            index_writer.write_table(
                bucket_table
                .select(['_raw_record_id', 'raw_record_bucket'])
                .cast(index_schema, safe=False)
            )
            bucket_counts[bucket_int] = (
                bucket_counts.get(bucket_int, 0) + bucket_table.num_rows
            )
        batch = []

    try:
        for record in records:
            clean = _clean_record(record)
            raw_record_id = raw_record_hash(clean)
            key = raw_record_id.hex()
            bucket = raw_record_bucket(raw_record_id, partition_bits=partition_bits)
            row = {
                '_source': source,
                '_dataset': dataset,
                '_raw_record_key': key,
                '_raw_record_id': raw_record_id,
                'raw_record_bucket': bucket,
                **clean,
            }
            if schema_names is not None:
                missing = set(row) - set(schema_names)
                if missing:
                    raise ValueError(
                        'Raw parser emitted new columns after first batch: '
                        f'{sorted(missing)}. Use a stable parser schema.'
                    )
            batch.append(row)
            rows += 1
            if len(batch) >= batch_size:
                flush()
        flush()
    finally:
        if schema is None:
            table = pa.Table.from_pylist(
                [],
                schema=pa.schema(
                    [
                        pa.field('_source', pa.string()),
                        pa.field('_dataset', pa.string()),
                        pa.field('_raw_record_key', pa.string()),
                        pa.field('_raw_record_id', pa.binary(32)),
                        pa.field('raw_record_bucket', pa.int64()),
                    ]
                ),
            )
            bucket_dir = output_path / f'raw_record_bucket={0:0{bucket_width}d}'
            bucket_dir.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, bucket_dir / 'data.parquet', compression='zstd')
            index_bucket_dir = index_path / f'raw_record_bucket={0:0{bucket_width}d}'
            index_bucket_dir.mkdir(parents=True, exist_ok=True)
            pq.write_table(
                table.select(['_raw_record_id', 'raw_record_bucket']).cast(
                    index_schema,
                    safe=False,
                ),
                index_bucket_dir / 'data.parquet',
                compression='zstd',
            )
        for writer in record_writers.values():
            writer.close()
        for writer in index_writers.values():
            writer.close()

    duplicate_stats = _duplicate_stats(output_path) if rows else {}
    bucket_fingerprints = _bucket_fingerprints(
        index_path,
        bucket_count=bucket_count,
    )
    return {
        'rows': rows,
        'bucket_count_with_rows': len(bucket_counts),
        'largest_bucket_size': max(bucket_counts.values(), default=0),
        'bucket_fingerprints': bucket_fingerprints,
        **duplicate_stats,
    }


def _write_delta(
    *,
    new_index: Path,
    old_index: Path | None,
    output_path: Path,
    partitioning: dict[str, int | str],
) -> dict[str, Any]:
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    added_root = output_path / 'added'
    removed_root = output_path / 'removed'
    added_root.mkdir(parents=True, exist_ok=True)
    removed_root.mkdir(parents=True, exist_ok=True)

    bucket_count = int(partitioning['bucket_count'])
    if old_index is None:
        return {
            'delta_rows_by_type': {},
            'delta_bucket_counts': {'added': 0, 'removed': 0, 'skipped': 0},
            'changed_bucket_count': 0,
            'skipped_bucket_count': 0,
        }

    new_fingerprints = _bucket_fingerprints(new_index, bucket_count=bucket_count)
    old_fingerprints = _bucket_fingerprints(old_index, bucket_count=bucket_count)
    delta_rows_by_type = {'added': 0, 'removed': 0}
    changed_bucket_count = 0
    skipped_bucket_count = 0
    con = duckdb.connect()
    try:
        for bucket in range(bucket_count):
            old_fp = old_fingerprints.get(str(bucket))
            new_fp = new_fingerprints.get(str(bucket))
            if old_fp == new_fp:
                skipped_bucket_count += 1
                continue
            changed_bucket_count += 1
            new_sql = _bucket_index_sql(new_index, bucket, bucket_count=bucket_count)
            old_sql = _bucket_index_sql(old_index, bucket, bucket_count=bucket_count)
            added_count = _write_delta_bucket(
                con,
                output_root=added_root,
                bucket=bucket,
                bucket_count=bucket_count,
                select_sql=f"""
                    SELECT n._raw_record_id, n.raw_record_bucket
                    FROM ({new_sql}) n
                    WHERE n._raw_record_id NOT IN (
                        SELECT _raw_record_id FROM ({old_sql})
                    )
                    ORDER BY n._raw_record_id
                """,
            )
            removed_count = _write_delta_bucket(
                con,
                output_root=removed_root,
                bucket=bucket,
                bucket_count=bucket_count,
                select_sql=f"""
                    SELECT o._raw_record_id, o.raw_record_bucket
                    FROM ({old_sql}) o
                    WHERE o._raw_record_id NOT IN (
                        SELECT _raw_record_id FROM ({new_sql})
                    )
                    ORDER BY o._raw_record_id
                """,
            )
            delta_rows_by_type['added'] += added_count
            delta_rows_by_type['removed'] += removed_count
    finally:
        con.close()
    delta_rows_by_type = {
        change_type: count
        for change_type, count in delta_rows_by_type.items()
        if count
    }
    return {
        'delta_rows_by_type': delta_rows_by_type,
        'delta_bucket_counts': {
            'added': _partition_dir_count(added_root),
            'removed': _partition_dir_count(removed_root),
            'skipped': skipped_bucket_count,
        },
        'changed_bucket_count': changed_bucket_count,
        'skipped_bucket_count': skipped_bucket_count,
    }


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = str(key) if key is not None else 'column'
        if name in METADATA_COLUMNS:
            name = f'raw{name}'
        out[name] = value
    return out


def canonical_raw_row_bytes(row: dict[str, Any]) -> bytes:
    """Return deterministic bytes for a raw parser row."""
    return json.dumps(
        {
            'canonicalization_version': CANONICALIZATION_VERSION,
            'row': _canonical_value(row),
        },
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8', errors='surrogatepass')


def raw_record_hash(row: dict[str, Any]) -> bytes:
    return hashlib.sha256(canonical_raw_row_bytes(row)).digest()


def raw_record_bucket(
    raw_record_id: bytes,
    *,
    partition_bits: int = RAW_RECORD_PARTITION_BITS,
) -> int:
    if partition_bits <= 0:
        return 0
    return int.from_bytes(raw_record_id, 'big') >> (256 - partition_bits)


def _canonical_value(value: Any) -> Any:
    if value is None:
        return {'type': 'null'}
    if isinstance(value, bool):
        return {'type': 'bool', 'value': value}
    if isinstance(value, int):
        return {'type': 'number', 'value': str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            return {'type': 'nan'}
        if math.isinf(value):
            return {'type': 'number', 'value': 'Infinity' if value > 0 else '-Infinity'}
        return {'type': 'number', 'value': _canonical_decimal(Decimal(str(value)))}
    if isinstance(value, Decimal):
        if value.is_nan():
            return {'type': 'nan'}
        if value.is_infinite():
            return {'type': 'number', 'value': 'Infinity' if value > 0 else '-Infinity'}
        return {'type': 'number', 'value': _canonical_decimal(value)}
    if isinstance(value, str):
        return {
            'type': 'string',
            'value': unicodedata.normalize('NFC', value),
        }
    if isinstance(value, bytes):
        return {
            'type': 'string',
            'value': unicodedata.normalize(
                'NFC',
                value.decode('utf-8', errors='replace'),
            ),
        }
    if isinstance(value, datetime):
        timestamp = value
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(UTC)
        return {'type': 'timestamp', 'value': timestamp.isoformat()}
    if isinstance(value, (date, time)):
        return {'type': 'timestamp', 'value': value.isoformat()}
    if isinstance(value, (list, tuple)):
        return {
            'type': 'array',
            'value': [_canonical_value(item) for item in value],
        }
    if isinstance(value, dict):
        return {
            'type': 'object',
            'value': {
                unicodedata.normalize('NFC', str(k)): _canonical_value(v)
                for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            },
        }
    return {
        'type': 'string',
        'value': unicodedata.normalize('NFC', str(value)),
    }


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == 0:
        return '0'
    return format(normalized, 'f')


def _stringify_if_unsupported(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, (list, tuple)):
        return [_stringify_if_unsupported(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _stringify_if_unsupported(v) for k, v in value.items()}
    return str(value)


def _schema_with_storable_nulls(schema: pa.Schema) -> pa.Schema:
    fields = [
        pa.field(field.name, pa.string() if pa.types.is_null(field.type) else field.type)
        for field in schema
    ]
    return pa.schema(fields)


def _duplicate_stats(records_path: Path) -> dict[str, Any]:
    con = duckdb.connect()
    try:
        duplicate_key_count, duplicate_row_count = con.execute(
            f"""
            WITH counts AS (
                SELECT _raw_record_key, count(*) AS n
                FROM {_read_records_sql(records_path)}
                GROUP BY _raw_record_key
            )
            SELECT
                count(*) FILTER (WHERE n > 1) AS duplicate_key_count,
                coalesce(sum(n - 1) FILTER (WHERE n > 1), 0) AS duplicate_row_count
            FROM counts
            """
        ).fetchone()
    finally:
        con.close()
    return {
        'duplicate_key_count': int(duplicate_key_count or 0),
        'duplicate_row_count': int(duplicate_row_count or 0),
    }


def _bucket_fingerprints(
    index_path: Path,
    *,
    bucket_count: int,
) -> dict[str, dict[str, Any]]:
    fingerprints: dict[str, dict[str, Any]] = {}
    for bucket in range(bucket_count):
        bucket_path = index_path / _bucket_dir_name(
            bucket,
            bucket_count=bucket_count,
        )
        if not _has_parquet_files(bucket_path):
            continue
        raw_ids: list[bytes] = []
        dataset = ds.dataset(bucket_path, format='parquet')
        for batch in dataset.to_batches(
            columns=['_raw_record_id'],
            batch_size=100_000,
        ):
            for value in batch.column('_raw_record_id').to_pylist():
                if value is not None:
                    raw_ids.append(_raw_record_id_bytes(value))
        digest = hashlib.sha256()
        for raw_id in sorted(raw_ids):
            digest.update(raw_id)
        fingerprints[str(bucket)] = {
            'count': len(raw_ids),
            'digest': digest.hexdigest(),
        }
    return fingerprints


def _write_delta_bucket(
    con: duckdb.DuckDBPyConnection,
    *,
    output_root: Path,
    bucket: int,
    bucket_count: int,
    select_sql: str,
) -> int:
    count = int(
        con.execute(f'SELECT count(*) FROM ({select_sql})').fetchone()[0] or 0
    )
    if count == 0:
        return 0
    bucket_dir = output_root / _bucket_dir_name(bucket, bucket_count=bucket_count)
    bucket_dir.mkdir(parents=True, exist_ok=True)
    output_file = bucket_dir / 'data.parquet'
    con.execute(
        f"""
        COPY (
            SELECT
                _raw_record_id,
                raw_record_bucket,
                {_sql_literal(output_root.name)} AS _change_type
            FROM ({select_sql})
        ) TO {_sql_literal(str(output_file))} (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    return count


def _bucket_index_sql(
    index_path: Path,
    bucket: int,
    *,
    bucket_count: int,
) -> str:
    bucket_path = index_path / _bucket_dir_name(bucket, bucket_count=bucket_count)
    if not _has_parquet_files(bucket_path):
        return """
            SELECT
                CAST(NULL AS BLOB) AS _raw_record_id,
                CAST(NULL AS BIGINT) AS raw_record_bucket
            WHERE false
        """
    return f"""
        SELECT
            _raw_record_id,
            try_cast(raw_record_bucket AS BIGINT) AS raw_record_bucket
        FROM {_read_records_sql(bucket_path)}
    """


def _partitioning_config_from_latest(
    latest: dict[str, Any] | None,
) -> dict[str, int | str]:
    if latest:
        manifest_path = latest.get('manifest_path')
        if manifest_path:
            try:
                manifest = json.loads(Path(str(manifest_path)).read_text())
            except (OSError, json.JSONDecodeError):
                manifest = {}
            partitioning = manifest.get('partitioning')
            if isinstance(partitioning, dict):
                return {
                    'scheme': str(partitioning.get('scheme') or 'hash_prefix'),
                    'partition_bits': int(
                        partitioning.get('partition_bits')
                        or RAW_RECORD_PARTITION_BITS
                    ),
                    'bucket_count': int(
                        partitioning.get('bucket_count')
                        or RAW_RECORD_BUCKET_COUNT
                    ),
                }
    return {
        'scheme': 'hash_prefix',
        'partition_bits': RAW_RECORD_PARTITION_BITS,
        'bucket_count': RAW_RECORD_BUCKET_COUNT,
    }


def _assert_supported_partitioning(partitioning: dict[str, int | str]) -> None:
    if partitioning.get('scheme') != 'hash_prefix':
        raise ValueError(f'Unsupported raw record partitioning: {partitioning!r}')
    partition_bits = int(partitioning['partition_bits'])
    bucket_count = int(partitioning['bucket_count'])
    if bucket_count != 2**partition_bits:
        raise ValueError(
            'Raw record bucket_count must equal 2 ** partition_bits: '
            f'{partitioning!r}'
        )


def _raw_record_id_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, bytearray):
        raw = bytes(value)
    elif isinstance(value, memoryview):
        raw = value.tobytes()
    elif isinstance(value, str):
        raw = bytes.fromhex(value)
    else:
        raw = bytes(value)
    if len(raw) != 32:
        raise ValueError(f'Expected 32-byte raw record hash, got {len(raw)} bytes')
    return raw


def _bucket_dir_name(bucket: int, *, bucket_count: int) -> str:
    return f'raw_record_bucket={bucket:0{_bucket_width(bucket_count)}d}'


def _bucket_width(bucket_count: int) -> int:
    return max(3, len(str(bucket_count - 1)))


def _partition_dir_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for child in path.iterdir() if child.is_dir())


def _has_parquet_files(path: Path) -> bool:
    return path.exists() and any(path.rglob('*.parquet'))


def _rewrite_manifest_paths(
    manifest_path: Path,
    *,
    records_path: Path,
    index_path: Path,
) -> None:
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    manifest['records_path'] = str(records_path)
    manifest['index_path'] = str(index_path)
    manifest['accepted_records_path'] = str(records_path)
    manifest['accepted_index_path'] = str(index_path)
    manifest['accepted_at'] = datetime.now(UTC).isoformat()
    _write_json(manifest_path, manifest)


def _read_latest(dataset_dir: Path) -> dict[str, Any] | None:
    path = dataset_dir / 'latest.json'
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_records_sql(path: Path) -> str:
    return (
        "read_parquet("
        f"{_sql_literal(str(path / '**' / '*.parquet'))}, "
        "union_by_name=true, hive_partitioning=true)"
    )
