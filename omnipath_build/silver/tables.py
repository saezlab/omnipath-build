from __future__ import annotations

"""Canonical silver parquet schemas and streaming writer.

The silver tables are the physical representation consumed by gold builders.
They are produced while Entity objects are already in memory during raw ->
silver, so later gold builders can consume columnar tables without
reconstructing nested parquet rows into Python dictionaries.
"""

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

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
])

ENTITY_IDENTIFIER_SCHEMA = pa.schema([
    pa.field('occurrence_id', pa.string(), nullable=False),
    pa.field('identifier_type', pa.string()),
    pa.field('identifier', pa.string()),
    pa.field('source', pa.string(), nullable=False),
    pa.field('dataset', pa.string(), nullable=False),
    pa.field('_raw_record_id', pa.int64()),
])

ENTITY_ANNOTATION_SCHEMA = pa.schema([
    pa.field('occurrence_id', pa.string(), nullable=False),
    pa.field('term', pa.string()),
    pa.field('value', pa.string()),
    pa.field('unit', pa.string()),
    pa.field('source', pa.string(), nullable=False),
    pa.field('dataset', pa.string(), nullable=False),
    pa.field('_raw_record_id', pa.int64()),
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
    return all((base / f'{name}.parquet').exists() for name in SILVER_TABLE_SCHEMAS)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, 'value'):
        value = value.value
    text = str(value).strip()
    return text or None


class _BufferedWriter:
    def __init__(self, path: Path, schema: pa.Schema, batch_size: int) -> None:
        self.path = path
        self.schema = schema
        self.batch_size = batch_size
        self.rows: list[dict[str, Any]] = []
        self.writer: pq.ParquetWriter | None = None

    def write(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.path, self.schema)
        self.writer.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is not None:
            self.writer.close()
        else:
            pq.write_table(pa.Table.from_pylist([], schema=self.schema), self.path)


class SilverTableWriter:
    """Streaming writer for canonical silver tables for one source directory."""

    def __init__(self, output_dir: str | Path, source: str, *, batch_size: int = 10_000) -> None:
        self.output_dir = Path(output_dir)
        self.source = source
        self.batch_size = batch_size
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._writers = {
            name: _BufferedWriter(self.output_dir / f'{name}.parquet', schema, batch_size)
            for name, schema in SILVER_TABLE_SCHEMAS.items()
        }
        self._dataset_counts: dict[str, int] = {}
        self._membership_counts: dict[str, int] = {}

    def write_entity(
        self,
        entity: Entity,
        *,
        dataset: str,
        raw_record_id: int | None = None,
    ) -> str:
        return self._write_entity(
            entity,
            dataset=dataset,
            parent_occurrence_id=None,
            entity_role='parent',
            raw_record_id=raw_record_id,
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
        })

        for identifier in getattr(entity, 'identifiers', None) or []:
            self._writers['entity_identifier'].write({
                'occurrence_id': occurrence_id,
                'identifier_type': _text(getattr(identifier, 'type', None)),
                'identifier': _text(getattr(identifier, 'value', None)),
                'source': self.source,
                'dataset': dataset,
                '_raw_record_id': raw_record_id,
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
                })

        return occurrence_id

    def close(self) -> None:
        for writer in self._writers.values():
            writer.close()
