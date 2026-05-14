from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

PREPARSE_VERSION = 'minimal_raw_records_v1'
METADATA_COLUMNS = {
    '_source',
    '_dataset',
    '_raw_record_key',
    '_raw_record_id',
}


@dataclass(frozen=True)
class RawSnapshot:
    source: str
    dataset: str
    snapshot_id: str
    records_path: Path
    delta_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class RawRecordProvenance:
    source: str
    dataset: str
    snapshot_id: str
    raw_record_key: str
    raw_record_id: int


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
                raw_record_id=int(raw_row['_raw_record_id']),
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
    delta_path = snapshot_dir / 'delta'
    manifest_path = snapshot_dir / 'manifest.json'

    latest = _read_latest(dataset_dir)
    old_snapshot_id = latest.get('snapshot_id') if latest else None
    old_records = Path(latest['records_path']) if latest else None
    if old_records is not None and not old_records.exists():
        old_records = None
        old_snapshot_id = None

    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(UTC)
    print(
        f'[minimal-preparse:{source}.{dataset}] start '
        f'snapshot={snapshot_id} previous={old_snapshot_id or "-"}',
        flush=True,
    )
    with tempfile.TemporaryDirectory(dir=snapshot_dir.parent) as tmpdir:
        tmp_unassigned_records = Path(tmpdir) / 'records.unassigned'
        tmp_records = Path(tmpdir) / 'records'
        tmp_delta = Path(tmpdir) / 'delta'
        stats = _write_records(
            records,
            output_path=tmp_unassigned_records,
            source=source,
            dataset=dataset,
            batch_size=batch_size,
        )
        id_stats = _write_records_with_ids(
            new_records=tmp_unassigned_records,
            old_records=old_records,
            output_path=tmp_records,
        )
        delta_stats = _write_delta(
            new_records=tmp_records,
            old_records=old_records,
            output_path=tmp_delta,
        )
        shutil.move(str(tmp_records), records_path)
        shutil.move(str(tmp_delta), delta_path)

    manifest = {
        'source': source,
        'dataset': dataset,
        'snapshot_id': snapshot_id,
        'previous_snapshot_id': old_snapshot_id,
        'created_at': started.isoformat(),
        'completed_at': datetime.now(UTC).isoformat(),
        'preparse_version': PREPARSE_VERSION,
        'records_path': str(records_path),
        'delta_path': str(delta_path),
        **stats,
        **id_stats,
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
        delta_path=delta_path,
        manifest_path=manifest_path,
    )


def accept_raw_snapshot(snapshot: RawSnapshot) -> None:
    dataset_dir = snapshot.records_path.parent.parent
    state_dir = dataset_dir / 'state'
    state_records_path = state_dir / 'records'
    state_dir.mkdir(parents=True, exist_ok=True)

    accepted_records_path = snapshot.records_path
    if snapshot.records_path != state_records_path:
        tmp_state_records_path = state_dir / 'records.tmp'
        if tmp_state_records_path.exists():
            shutil.rmtree(tmp_state_records_path)
        shutil.move(str(snapshot.records_path), tmp_state_records_path)
        if state_records_path.exists():
            shutil.rmtree(state_records_path)
        tmp_state_records_path.replace(state_records_path)
        accepted_records_path = state_records_path

    _rewrite_manifest_records_path(snapshot.manifest_path, accepted_records_path)
    _write_json(
        dataset_dir / 'latest.json',
        {
            'source': snapshot.source,
            'dataset': snapshot.dataset,
            'snapshot_id': snapshot.snapshot_id,
            'records_path': str(accepted_records_path),
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
    con = duckdb.connect()
    try:
        reader = con.execute(
            f"""
            SELECT r.*
            FROM {_read_records_sql(records_path)} r
            JOIN {_read_records_sql(delta_path)} d USING (_raw_record_key)
            WHERE d._change_type = 'added'
            ORDER BY r._raw_record_id
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
    source: str,
    dataset: str,
    batch_size: int,
) -> dict[str, Any]:
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / 'data.parquet'
    writer: pq.ParquetWriter | None = None
    batch: list[dict[str, Any]] = []
    schema_names: list[str] | None = None
    schema: pa.Schema | None = None
    rows = 0

    def flush() -> None:
        nonlocal batch, schema_names, writer, schema
        if not batch:
            return
        if schema_names is None:
            seen: dict[str, None] = {}
            for row in batch:
                for name in row:
                    seen.setdefault(name, None)
            schema_names = list(seen)
        normalized = [
            {name: _stringify_if_unsupported(row.get(name)) for name in schema_names}
            for row in batch
        ]
        table = pa.Table.from_pylist(normalized)
        if writer is None:
            schema = table.schema
            writer = pq.ParquetWriter(
                output_file,
                schema,
                compression='zstd',
                use_dictionary=True,
            )
        else:
            table = table.cast(schema, safe=False)
        writer.write_table(table)
        batch = []

    try:
        for record in records:
            clean = _clean_record(record)
            row = {
                '_source': source,
                '_dataset': dataset,
                '_raw_record_key': _record_hash(clean),
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
        if writer is None:
            table = pa.Table.from_pylist(
                [],
                schema=pa.schema(
                    [
                        pa.field('_source', pa.string()),
                        pa.field('_dataset', pa.string()),
                        pa.field('_raw_record_key', pa.string()),
                    ]
                ),
            )
            pq.write_table(table, output_file, compression='zstd')
        else:
            writer.close()

    duplicate_stats = _duplicate_stats(output_path) if rows else {}
    return {'rows': rows, **duplicate_stats}


def _write_records_with_ids(
    *,
    new_records: Path,
    old_records: Path | None,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / 'data.parquet'
    old_map_sql = _old_raw_record_id_map_sql(old_records)
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            COPY (
                WITH
                old_map AS ({old_map_sql}),
                new_keys AS (
                    SELECT DISTINCT _raw_record_key
                    FROM {_read_records_sql(new_records)}
                ),
                max_old AS (
                    SELECT coalesce(max(_raw_record_id), 0) AS max_id
                    FROM old_map
                ),
                added_map AS (
                    SELECT
                        _raw_record_key,
                        (
                            (SELECT max_id FROM max_old)
                            + row_number() OVER (ORDER BY _raw_record_key)
                        )::BIGINT AS _raw_record_id
                    FROM new_keys
                    WHERE _raw_record_key NOT IN (
                        SELECT _raw_record_key FROM old_map
                    )
                ),
                id_map AS (
                    SELECT _raw_record_key, _raw_record_id
                    FROM old_map
                    WHERE _raw_record_key IN (
                        SELECT _raw_record_key FROM new_keys
                    )
                    UNION ALL
                    SELECT _raw_record_key, _raw_record_id
                    FROM added_map
                )
                SELECT
                    n._source,
                    n._dataset,
                    n._raw_record_key,
                    CAST(m._raw_record_id AS BIGINT) AS _raw_record_id,
                    n.* EXCLUDE (_source, _dataset, _raw_record_key)
                FROM {_read_records_sql(new_records)} n
                JOIN id_map m USING (_raw_record_key)
                ORDER BY m._raw_record_id
            ) TO {_sql_literal(str(output_file))} (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        min_id, max_id, distinct_ids = con.execute(
            f"""
            SELECT
                min(_raw_record_id),
                max(_raw_record_id),
                count(DISTINCT _raw_record_id)
            FROM {_read_records_sql(output_path)}
            """
        ).fetchone()
    finally:
        con.close()
    return {
        'min_raw_record_id': int(min_id or 0),
        'max_raw_record_id': int(max_id or 0),
        'distinct_raw_record_ids': int(distinct_ids or 0),
    }


def _write_delta(
    *,
    new_records: Path,
    old_records: Path | None,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / 'data.parquet'
    old_map_sql = _old_raw_record_id_map_sql(old_records)
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            COPY (
                WITH
                old_keys AS ({old_map_sql}),
                new_keys AS (
                    SELECT DISTINCT _raw_record_key, _raw_record_id
                    FROM {_read_records_sql(new_records)}
                )
                SELECT
                    _raw_record_key,
                    _raw_record_id,
                    'added' AS _change_type
                FROM new_keys
                WHERE _raw_record_key NOT IN (
                    SELECT _raw_record_key FROM old_keys
                )
                UNION ALL
                SELECT
                    _raw_record_key,
                    _raw_record_id,
                    'removed' AS _change_type
                FROM old_keys
                WHERE _raw_record_key NOT IN (
                    SELECT _raw_record_key FROM new_keys
                )
                ORDER BY _raw_record_id, _change_type
            ) TO {_sql_literal(str(output_file))} (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        stats = con.execute(
            f"""
            SELECT _change_type, count(*) AS rows
            FROM {_read_records_sql(output_path)}
            GROUP BY _change_type
            ORDER BY _change_type
            """
        ).fetchall()
    finally:
        con.close()
    return {'delta_rows_by_type': dict(stats)}


def _old_raw_record_id_map_sql(old_records: Path | None) -> str:
    if old_records is None:
        return """
            SELECT
                '' AS _raw_record_key,
                CAST(NULL AS BIGINT) AS _raw_record_id
            WHERE false
        """
    return f"""
        SELECT
            _raw_record_key,
            min(_raw_record_id)::BIGINT AS _raw_record_id
        FROM {_read_records_sql(old_records)}
        GROUP BY _raw_record_key
    """


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = str(key) if key is not None else 'column'
        if name in METADATA_COLUMNS:
            name = f'raw{name}'
        out[name] = value
    return out


def _record_hash(record: dict[str, Any]) -> str:
    digest = hashlib.blake2b(digest_size=32)
    for key in sorted(record):
        digest.update(key.encode('utf-8', errors='surrogatepass'))
        digest.update(b'\x1f')
        digest.update(_canonical_bytes(record[key]))
        digest.update(b'\x1e')
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8', errors='surrogatepass')


def _canonical_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(value.items())}
    return str(value)


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


def _rewrite_manifest_records_path(manifest_path: Path, records_path: Path) -> None:
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    manifest['records_path'] = str(records_path)
    manifest['accepted_records_path'] = str(records_path)
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
