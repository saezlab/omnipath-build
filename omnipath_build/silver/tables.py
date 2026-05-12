"""Canonical silver parquet schemas and streaming writer.

The silver tables are the physical representation consumed by gold builders.
They are produced while Entity objects are already in memory during raw ->
silver, so later gold builders can consume columnar tables without
reconstructing nested parquet rows into Python dictionaries.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from pypath.inputs_v2.raw_records import RAW_RECORD_BUCKET_COUNT

from pypath.internals.silver_schema import Entity

__all__ = [
    'ENTITY_OCCURRENCE_SCHEMA',
    'ENTITY_IDENTIFIER_SCHEMA',
    'ENTITY_ANNOTATION_SCHEMA',
    'MEMBERSHIP_SCHEMA',
    'MEMBERSHIP_ANNOTATION_SCHEMA',
    'SILVER_TABLE_SCHEMAS',
    'SilverTableWriter',
    'silver_table_dir',
    'has_silver_tables',
    'has_raw_keyed_silver_tables',
]


ENTITY_OCCURRENCE_SCHEMA = pa.schema([
    pa.field('occurrence_id', pa.string(), nullable=False),
    pa.field('record_id', pa.string()),
    pa.field('parent_occurrence_id', pa.string()),
    pa.field('entity_role', pa.string(), nullable=False),
    pa.field('entity_type', pa.string()),
    pa.field('source', pa.string(), nullable=False),
    pa.field('dataset', pa.string(), nullable=False),
    pa.field('_raw_record_id', pa.int64()),
    pa.field('_raw_record_key', pa.string()),
    pa.field('raw_record_bucket', pa.int64()),
    pa.field('raw_record_part', pa.int64()),
    pa.field('_snapshot_id', pa.string()),
])

ENTITY_IDENTIFIER_SCHEMA = pa.schema([
    pa.field('occurrence_id', pa.string(), nullable=False),
    pa.field('identifier_type', pa.string()),
    pa.field('identifier', pa.string()),
    pa.field('source', pa.string(), nullable=False),
    pa.field('dataset', pa.string(), nullable=False),
    pa.field('_raw_record_id', pa.int64()),
    pa.field('_raw_record_key', pa.string()),
    pa.field('raw_record_bucket', pa.int64()),
    pa.field('raw_record_part', pa.int64()),
    pa.field('_snapshot_id', pa.string()),
])

ENTITY_ANNOTATION_SCHEMA = pa.schema([
    pa.field('occurrence_id', pa.string(), nullable=False),
    pa.field('term', pa.string()),
    pa.field('value', pa.string()),
    pa.field('unit', pa.string()),
    pa.field('source', pa.string(), nullable=False),
    pa.field('dataset', pa.string(), nullable=False),
    pa.field('_raw_record_id', pa.int64()),
    pa.field('_raw_record_key', pa.string()),
    pa.field('raw_record_bucket', pa.int64()),
    pa.field('raw_record_part', pa.int64()),
    pa.field('_snapshot_id', pa.string()),
])

MEMBERSHIP_SCHEMA = pa.schema([
    pa.field('membership_id', pa.string(), nullable=False),
    pa.field('parent_occurrence_id', pa.string(), nullable=False),
    pa.field('member_occurrence_id', pa.string(), nullable=False),
    pa.field('is_parent', pa.bool_()),
    pa.field('membership_role', pa.string()),
    pa.field('source', pa.string(), nullable=False),
    pa.field('dataset', pa.string(), nullable=False),
    pa.field('_raw_record_id', pa.int64()),
    pa.field('_raw_record_key', pa.string()),
    pa.field('raw_record_bucket', pa.int64()),
    pa.field('raw_record_part', pa.int64()),
    pa.field('_snapshot_id', pa.string()),
])

MEMBERSHIP_ANNOTATION_SCHEMA = pa.schema([
    pa.field('membership_id', pa.string(), nullable=False),
    pa.field('parent_occurrence_id', pa.string(), nullable=False),
    pa.field('member_occurrence_id', pa.string(), nullable=False),
    pa.field('term', pa.string()),
    pa.field('value', pa.string()),
    pa.field('unit', pa.string()),
    pa.field('source', pa.string(), nullable=False),
    pa.field('dataset', pa.string(), nullable=False),
    pa.field('_raw_record_id', pa.int64()),
    pa.field('_raw_record_key', pa.string()),
    pa.field('raw_record_bucket', pa.int64()),
    pa.field('raw_record_part', pa.int64()),
    pa.field('_snapshot_id', pa.string()),
])

SILVER_TABLE_SCHEMAS = {
    'entity_occurrence': ENTITY_OCCURRENCE_SCHEMA,
    'entity_identifier': ENTITY_IDENTIFIER_SCHEMA,
    'entity_annotation': ENTITY_ANNOTATION_SCHEMA,
    'membership': MEMBERSHIP_SCHEMA,
    'membership_annotation': MEMBERSHIP_ANNOTATION_SCHEMA,
}


def silver_table_dir(source_dir: str | Path) -> Path:
    return Path(source_dir)


def has_silver_tables(source_dir: str | Path) -> bool:
    base = silver_table_dir(source_dir)
    return all((base / name).exists() for name in SILVER_TABLE_SCHEMAS)


def has_raw_keyed_silver_tables(source_dir: str | Path) -> bool:
    base = silver_table_dir(source_dir)
    for name in SILVER_TABLE_SCHEMAS:
        path = base / name
        if not path.exists():
            return False
        schema = ds.dataset(path, format='parquet').schema
        if '_raw_record_key' not in schema.names:
            return False
    return True


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, 'value'):
        value = value.value
    text = str(value).strip()
    return text or None


class _BufferedWriter:
    def __init__(self, root: Path, schema: pa.Schema, batch_size: int) -> None:
        self.root = root
        self.schema = schema
        self.batch_size = batch_size
        self.rows: list[dict[str, Any]] = []
        self.writers: dict[int, pq.ParquetWriter] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        self.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))
        self.rows.clear()

    def write_table(self, table: pa.Table) -> None:
        table = table.cast(self.schema, safe=False)
        parts = sorted({
            int(part)
            for part in table.column('raw_record_part').to_pylist()
            if part is not None
        })
        if not parts and table.num_rows:
            table = table.set_column(
                table.schema.get_field_index('raw_record_part'),
                'raw_record_part',
                pa.array([0] * table.num_rows, type=pa.int64()),
            )
            parts = [0]
        for part_int in parts:
            part_table = table.filter(
                pc.equal(table.column('raw_record_part'), pa.scalar(part_int, type=pa.int64()))
            )
            writer = self.writers.get(part_int)
            if writer is None:
                part_dir = self.root / f'part={part_int:05d}'
                part_dir.mkdir(parents=True, exist_ok=True)
                writer = pq.ParquetWriter(part_dir / 'data.parquet', self.schema)
                self.writers[part_int] = writer
            writer.write_table(part_table)

    def close(self) -> None:
        self.flush()
        empty = pa.Table.from_pylist([], schema=self.schema)
        if not self.writers:
            part_dir = self.root / 'part=00000'
            part_dir.mkdir(parents=True, exist_ok=True)
            pq.write_table(empty, part_dir / 'data.parquet')
        for writer in self.writers.values():
            writer.close()


class SilverTableWriter:
    """Streaming writer for canonical silver tables for one source directory."""

    def __init__(
        self,
        output_dir: str | Path,
        source: str,
        *,
        batch_size: int = 10_000,
        seed_from_dir: str | Path | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.source = source
        self.batch_size = batch_size
        self.seed_from_dir = Path(seed_from_dir) if seed_from_dir is not None else None
        self._excluded_raw_record_keys: set[str] = set()
        self._excluded_raw_record_delta_path: Path | None = None
        self._excluded_datasets: set[str] = set()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._writers = {
            name: _BufferedWriter(self.output_dir / name, schema, batch_size)
            for name, schema in SILVER_TABLE_SCHEMAS.items()
        }
        self._dataset_counts: dict[str, int] = {}
        self._membership_counts: dict[str, int] = {}

    def exclude_raw_record_keys(self, keys: set[str]) -> None:
        self._excluded_raw_record_keys.update(key for key in keys if key)

    def exclude_raw_record_delta(self, delta_path: str | Path) -> None:
        self._excluded_raw_record_delta_path = Path(delta_path)

    def exclude_dataset(self, dataset: str) -> None:
        self._excluded_datasets.add(dataset)

    def write_entity(
        self,
        entity: Entity,
        *,
        dataset: str,
        raw_record_id: int | None = None,
        raw_record_key: str | None = None,
        raw_record_bucket: int | None = None,
        raw_record_part: int | None = None,
        snapshot_id: str | None = None,
    ) -> str:
        raw_record_bucket, raw_record_part = _raw_record_partition(
            raw_record_key,
            raw_record_bucket,
            raw_record_part,
        )
        return self._write_entity(
            entity,
            dataset=dataset,
            parent_occurrence_id=None,
            entity_role='parent',
            raw_record_id=raw_record_id,
            raw_record_key=raw_record_key,
            raw_record_bucket=raw_record_bucket,
            raw_record_part=raw_record_part,
            snapshot_id=snapshot_id,
            occurrence_suffix='parent',
        )

    def _next_occurrence_id(self, dataset: str) -> str:
        next_id = self._dataset_counts.get(dataset, 0) + 1
        self._dataset_counts[dataset] = next_id
        return f'{dataset}:{next_id}'

    def _next_membership_id(self, dataset: str) -> str:
        next_id = self._membership_counts.get(dataset, 0) + 1
        self._membership_counts[dataset] = next_id
        return f'{dataset}:membership:{next_id}'

    def _write_entity(
        self,
        entity: Entity,
        *,
        dataset: str,
        parent_occurrence_id: str | None,
        entity_role: str,
        raw_record_id: int | None,
        raw_record_key: str | None,
        raw_record_bucket: int | None,
        raw_record_part: int | None,
        snapshot_id: str | None,
        occurrence_suffix: str,
    ) -> str:
        if raw_record_id is not None:
            occurrence_id = f'{dataset}:{raw_record_id}:{occurrence_suffix}'
        else:
            occurrence_id = self._next_occurrence_id(dataset)
        self._writers['entity_occurrence'].write({
            'occurrence_id': occurrence_id,
            'record_id': str(raw_record_id) if raw_record_id is not None else None,
            'parent_occurrence_id': parent_occurrence_id,
            'entity_role': entity_role,
            'entity_type': _text(getattr(entity, 'type', None)),
            'source': self.source,
            'dataset': dataset,
            '_raw_record_id': raw_record_id,
            '_raw_record_key': raw_record_key,
            'raw_record_bucket': raw_record_bucket,
            'raw_record_part': raw_record_part,
            '_snapshot_id': snapshot_id,
        })

        for identifier in getattr(entity, 'identifiers', None) or []:
            self._writers['entity_identifier'].write({
                'occurrence_id': occurrence_id,
                'identifier_type': _text(getattr(identifier, 'type', None)),
                'identifier': _text(getattr(identifier, 'value', None)),
                'source': self.source,
                'dataset': dataset,
                '_raw_record_id': raw_record_id,
                '_raw_record_key': raw_record_key,
                'raw_record_bucket': raw_record_bucket,
                'raw_record_part': raw_record_part,
                '_snapshot_id': snapshot_id,
            })

        for annotation in getattr(entity, 'annotations', None) or []:
            self._writers['entity_annotation'].write({
                'occurrence_id': occurrence_id,
                'term': _text(getattr(annotation, 'term', None)),
                'value': _text(getattr(annotation, 'value', None)),
                'unit': _text(getattr(annotation, 'units', None)),
                'source': self.source,
                'dataset': dataset,
                '_raw_record_id': raw_record_id,
                '_raw_record_key': raw_record_key,
                'raw_record_bucket': raw_record_bucket,
                'raw_record_part': raw_record_part,
                '_snapshot_id': snapshot_id,
            })

        for membership_index, membership in enumerate(getattr(entity, 'membership', None) or []):
            member = getattr(membership, 'member', None)
            if member is None:
                continue
            member_occurrence_id = self._write_entity(
                member,
                dataset=dataset,
                parent_occurrence_id=occurrence_id,
                entity_role='member',
                raw_record_id=raw_record_id,
                raw_record_key=raw_record_key,
                raw_record_bucket=raw_record_bucket,
                raw_record_part=raw_record_part,
                snapshot_id=snapshot_id,
                occurrence_suffix=f'{occurrence_suffix}:member:{membership_index}',
            )
            if raw_record_id is not None:
                membership_id = f'{dataset}:{raw_record_id}:{occurrence_suffix}:membership:{membership_index}'
            else:
                membership_id = self._next_membership_id(dataset)
            is_parent = getattr(membership, 'is_parent', None)
            self._writers['membership'].write({
                'membership_id': membership_id,
                'parent_occurrence_id': occurrence_id,
                'member_occurrence_id': member_occurrence_id,
                'is_parent': bool(is_parent) if is_parent is not None else None,
                'membership_role': None,
                'source': self.source,
                'dataset': dataset,
                '_raw_record_id': raw_record_id,
                '_raw_record_key': raw_record_key,
                'raw_record_bucket': raw_record_bucket,
                'raw_record_part': raw_record_part,
                '_snapshot_id': snapshot_id,
            })
            for annotation in getattr(membership, 'annotations', None) or []:
                self._writers['membership_annotation'].write({
                    'membership_id': membership_id,
                    'parent_occurrence_id': occurrence_id,
                    'member_occurrence_id': member_occurrence_id,
                    'term': _text(getattr(annotation, 'term', None)),
                    'value': _text(getattr(annotation, 'value', None)),
                    'unit': _text(getattr(annotation, 'units', None)),
                    'source': self.source,
                    'dataset': dataset,
                    '_raw_record_id': raw_record_id,
                    '_raw_record_key': raw_record_key,
                    'raw_record_bucket': raw_record_bucket,
                    'raw_record_part': raw_record_part,
                    '_snapshot_id': snapshot_id,
                })

        return occurrence_id

    def close(self) -> None:
        if self.seed_from_dir is not None:
            self._write_seed_rows()
        for writer in self._writers.values():
            writer.close()

    def _write_seed_rows(self) -> None:
        for name, schema in SILVER_TABLE_SCHEMAS.items():
            path = self.seed_from_dir / name
            if not path.exists():
                continue
            if self._excluded_raw_record_delta_path is not None:
                self._write_seed_rows_with_delta_filter(name, path, schema)
                continue
            dataset = ds.dataset(path, format='parquet')
            for batch in dataset.to_batches(batch_size=self.batch_size):
                table = pa.Table.from_batches([batch], schema=batch.schema)
                table = self._filter_seed_table(table)
                if table.num_rows == 0:
                    continue
                self._writers[name].write_table(_align_table_to_schema(table, schema))

    def _write_seed_rows_with_delta_filter(
        self,
        name: str,
        path: Path,
        schema: pa.Schema,
    ) -> None:
        filters = ["d._raw_record_key is null"]
        if self._excluded_datasets:
            excluded = ', '.join(_sql_literal(dataset) for dataset in sorted(self._excluded_datasets))
            filters.append(f"s.dataset not in ({excluded})")
        con = duckdb.connect()
        try:
            reader = con.execute(f"""
                select s.*
                from {_read_dataset_sql(path)} s
                left join (
                    select distinct _raw_record_key
                    from {_read_dataset_sql(self._excluded_raw_record_delta_path)}
                    where _change_type = 'removed'
                      and _raw_record_key is not null
                ) d using (_raw_record_key)
                where {' and '.join(filters)}
                order by s.raw_record_part, s._raw_record_key
            """).fetch_record_batch(rows_per_batch=self.batch_size)
            while True:
                try:
                    batch = reader.read_next_batch()
                except StopIteration:
                    break
                if batch.num_rows == 0:
                    continue
                table = pa.Table.from_batches([batch])
                self._writers[name].write_table(_align_table_to_schema(table, schema))
        finally:
            con.close()

    def _filter_seed_table(self, table: pa.Table) -> pa.Table:
        mask = pa.array([True] * table.num_rows)
        if self._excluded_raw_record_keys:
            raw_keys = table.column('_raw_record_key')
            removed = pc.is_in(
                raw_keys,
                value_set=pa.array(sorted(self._excluded_raw_record_keys), type=pa.string()),
            )
            mask = pc.and_(mask, pc.invert(removed))
        if self._excluded_datasets:
            datasets = table.column('dataset')
            excluded = pc.is_in(
                datasets,
                value_set=pa.array(sorted(self._excluded_datasets), type=pa.string()),
            )
            mask = pc.and_(mask, pc.invert(excluded))
        return table.filter(mask)


def _align_table_to_schema(table: pa.Table, schema: pa.Schema) -> pa.Table:
    columns = []
    for field in schema:
        if field.name in table.column_names:
            columns.append(table.column(field.name).cast(field.type, safe=False))
        else:
            columns.append(pa.nulls(table.num_rows, type=field.type))
    return pa.Table.from_arrays(columns, schema=schema)


def _raw_record_partition(
    raw_record_key: str | None,
    raw_record_bucket: int | None,
    raw_record_part: int | None,
) -> tuple[int | None, int | None]:
    if raw_record_bucket is not None and raw_record_part is not None:
        return raw_record_bucket, raw_record_part
    if raw_record_key is None:
        return None, None
    import hashlib

    digest = hashlib.sha256(raw_record_key.encode('utf-8')).digest()
    bucket = int.from_bytes(digest[:8], 'big', signed=False) % RAW_RECORD_BUCKET_COUNT
    part = 0
    return bucket, part


def _read_dataset_sql(path: Path) -> str:
    return (
        "read_parquet("
        f"{_sql_literal(str(path / '**' / '*.parquet'))}, "
        "union_by_name=true, hive_partitioning=true)"
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
