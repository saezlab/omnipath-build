from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from omnipath_build.rewrite.bronze import source_state_path
from omnipath_build.silver.tables import SILVER_TABLE_SCHEMAS
from omnipath_build.silver.validate import validate_entity_identifier_shapes
from pypath.inputs_v2.raw_records import METADATA_COLUMNS, RawRecordProvenance
from pypath.internals.ontology_schema import OntologyTerm
from pypath.internals.silver_schema import Entity


SILVER_TABLE_PREFIX = 'silver_'
SILVER_TABLE_NAMES = tuple(SILVER_TABLE_SCHEMAS)


@dataclass(frozen=True)
class SilverRewriteResult:
    source: str
    source_state_path: Path
    mapped_raw_record_count: int
    deleted_raw_record_count: int
    rows_by_table: dict[str, int]


def materialize_silver_duckdb(
    *,
    source: str,
    resource_functions: Iterable[Any],
    data_root: str | Path = 'data_rewrite',
    batch_size: int = 10_000,
) -> SilverRewriteResult:
    """Map bronze DuckDB raw records into source-local silver DuckDB tables."""
    state_path = source_state_path(data_root, source)
    if not state_path.exists():
        raise FileNotFoundError(f'rewrite source state does not exist: {state_path}')

    con = duckdb.connect(str(state_path))
    writer = _DuckDBSilverTableWriter(con, source=source, batch_size=batch_size)
    mapped_raw_record_count = 0
    deleted_raw_record_ids: set[int] = set()
    try:
        _ensure_silver_schema(con)
        for fn in resource_functions:
            if fn.source != source or fn.function_name == 'resource':
                continue
            raw_dataset = getattr(fn.call, '_raw_dataset', None)
            if raw_dataset is None or fn.output_kind not in {'entity', 'ontology'}:
                continue

            snapshot = _latest_bronze_snapshot(con, source, fn.function_name)
            if snapshot is None:
                continue
            source_run_id = snapshot['snapshot_id']
            typed_table = snapshot['typed_current_table']
            affected_ids = _affected_raw_record_ids(
                con,
                source=source,
                dataset=fn.function_name,
                source_run_id=source_run_id,
            )
            full_dataset_bootstrap = False
            if not affected_ids and not _has_current_silver_rows(
                con,
                source=source,
                dataset=fn.function_name,
            ):
                affected_ids = _current_raw_record_ids(
                    con,
                    source=source,
                    dataset=fn.function_name,
                )
                full_dataset_bootstrap = bool(affected_ids)
            if not affected_ids:
                continue
            _delete_silver_rows_for_raw_ids(
                con,
                source=source,
                dataset=fn.function_name,
                raw_record_ids=affected_ids,
            )
            deleted_raw_record_ids.update(affected_ids)

            raw_rows = (
                _iter_current_rows(
                    con,
                    source=source,
                    dataset=fn.function_name,
                    typed_table=typed_table,
                )
                if full_dataset_bootstrap
                else _iter_added_current_rows(
                    con,
                    source=source,
                    dataset=fn.function_name,
                    source_run_id=source_run_id,
                    typed_table=typed_table,
                )
            )
            for raw_row in raw_rows:
                provenance = RawRecordProvenance(
                    source=source,
                    dataset=fn.function_name,
                    snapshot_id=str(raw_row['snapshot_id']),
                    raw_record_key=str(raw_row['_raw_record_key']),
                    raw_record_id=int(raw_row['_raw_record_id']),
                    raw_record_bucket=int(raw_row['raw_record_bucket']),
                    raw_record_part=int(raw_row['raw_record_part']),
                )
                mapper_input = _mapper_input(raw_row)
                mapped = raw_dataset.mapper(mapper_input)
                for dataset_name, entity in _iter_mapped_entities(
                    mapped,
                    output_kind=fn.output_kind,
                    ontology_id=fn.ontology_id,
                    context=f'{source}.{fn.function_name}',
                ):
                    validate_entity_identifier_shapes(entity, context=f'{source}.{dataset_name}')
                    writer.write_entity(
                        entity,
                        dataset=dataset_name or fn.function_name,
                        raw_record_id=provenance.raw_record_id,
                        raw_record_key=provenance.raw_record_key,
                        raw_record_bucket=provenance.raw_record_bucket,
                        raw_record_part=provenance.raw_record_part,
                        snapshot_id=provenance.snapshot_id,
                        source_run_id=source_run_id,
                    )
                mapped_raw_record_count += 1
        writer.close()
        rows_by_table = {
            name: int(
                con.execute(f'select count(*) from {_quote_identifier(SILVER_TABLE_PREFIX + name)}').fetchone()[0]
                or 0
            )
            for name in SILVER_TABLE_NAMES
        }
    finally:
        con.close()

    return SilverRewriteResult(
        source=source,
        source_state_path=state_path,
        mapped_raw_record_count=mapped_raw_record_count,
        deleted_raw_record_count=len(deleted_raw_record_ids),
        rows_by_table=rows_by_table,
    )


class _DuckDBSilverTableWriter:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        source: str,
        batch_size: int,
    ) -> None:
        self.con = con
        self.source = source
        self.batch_size = batch_size
        self.rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SILVER_TABLE_NAMES}

    def write_entity(
        self,
        entity: Entity,
        *,
        dataset: str,
        raw_record_id: int | None,
        raw_record_key: str | None,
        raw_record_bucket: int | None,
        raw_record_part: int | None,
        snapshot_id: str | None,
        source_run_id: str,
    ) -> str:
        occurrence_id = (
            f'{dataset}:{raw_record_id}:parent'
            if raw_record_id is not None
            else f'{dataset}:unkeyed:parent'
        )
        self._write_entity(
            entity,
            dataset=dataset,
            occurrence_id=occurrence_id,
            parent_occurrence_id=None,
            entity_role='parent',
            raw_record_id=raw_record_id,
            raw_record_key=raw_record_key,
            raw_record_bucket=raw_record_bucket,
            raw_record_part=raw_record_part,
            snapshot_id=snapshot_id,
            source_run_id=source_run_id,
            occurrence_suffix='parent',
        )
        return occurrence_id

    def _write_entity(
        self,
        entity: Entity,
        *,
        dataset: str,
        occurrence_id: str,
        parent_occurrence_id: str | None,
        entity_role: str,
        raw_record_id: int | None,
        raw_record_key: str | None,
        raw_record_bucket: int | None,
        raw_record_part: int | None,
        snapshot_id: str | None,
        source_run_id: str,
        occurrence_suffix: str,
    ) -> None:
        lineage = {
            'source': self.source,
            'dataset': dataset,
            '_raw_record_id': raw_record_id,
            '_raw_record_key': raw_record_key,
            'raw_record_bucket': raw_record_bucket,
            'raw_record_part': raw_record_part,
            '_snapshot_id': snapshot_id,
            'source_run_id': source_run_id,
        }
        self._append('entity_occurrence', {
            'occurrence_id': occurrence_id,
            'record_id': str(raw_record_id) if raw_record_id is not None else None,
            'parent_occurrence_id': parent_occurrence_id,
            'entity_role': entity_role,
            'entity_type': _text(getattr(entity, 'type', None)),
            **lineage,
        })
        for identifier in getattr(entity, 'identifiers', None) or []:
            self._append('entity_identifier', {
                'occurrence_id': occurrence_id,
                'identifier_type': _text(getattr(identifier, 'type', None)),
                'identifier': _text(getattr(identifier, 'value', None)),
                **lineage,
            })
        for annotation in getattr(entity, 'annotations', None) or []:
            self._append('entity_annotation', {
                'occurrence_id': occurrence_id,
                'term': _text(getattr(annotation, 'term', None)),
                'value': _text(getattr(annotation, 'value', None)),
                'unit': _text(getattr(annotation, 'units', None)),
                **lineage,
            })
        for index, membership in enumerate(getattr(entity, 'membership', None) or []):
            member = getattr(membership, 'member', None)
            if member is None:
                continue
            member_occurrence_id = f'{occurrence_id}:member:{index}'
            self._write_entity(
                member,
                dataset=dataset,
                occurrence_id=member_occurrence_id,
                parent_occurrence_id=occurrence_id,
                entity_role='member',
                raw_record_id=raw_record_id,
                raw_record_key=raw_record_key,
                raw_record_bucket=raw_record_bucket,
                raw_record_part=raw_record_part,
                snapshot_id=snapshot_id,
                source_run_id=source_run_id,
                occurrence_suffix=f'{occurrence_suffix}:member:{index}',
            )
            membership_id = f'{dataset}:{raw_record_id}:{occurrence_suffix}:membership:{index}'
            is_parent = getattr(membership, 'is_parent', None)
            self._append('membership', {
                'membership_id': membership_id,
                'parent_occurrence_id': occurrence_id,
                'member_occurrence_id': member_occurrence_id,
                'is_parent': bool(is_parent) if is_parent is not None else None,
                'membership_role': None,
                **lineage,
            })
            for annotation in getattr(membership, 'annotations', None) or []:
                self._append('membership_annotation', {
                    'membership_id': membership_id,
                    'parent_occurrence_id': occurrence_id,
                    'member_occurrence_id': member_occurrence_id,
                    'term': _text(getattr(annotation, 'term', None)),
                    'value': _text(getattr(annotation, 'value', None)),
                    'unit': _text(getattr(annotation, 'units', None)),
                    **lineage,
                })

    def _append(self, table_name: str, row: dict[str, Any]) -> None:
        rows = self.rows[table_name]
        rows.append(row)
        if len(rows) >= self.batch_size:
            self._flush(table_name)

    def close(self) -> None:
        for table_name in SILVER_TABLE_NAMES:
            self._flush(table_name)

    def _flush(self, table_name: str) -> None:
        rows = self.rows[table_name]
        if not rows:
            return
        schema = _schema_with_source_run(SILVER_TABLE_SCHEMAS[table_name])
        table = pa.Table.from_pylist(rows, schema=schema)
        relation = f'silver_batch_{table_name}'
        self.con.register(relation, table)
        try:
            target = _quote_identifier(SILVER_TABLE_PREFIX + table_name)
            columns = ', '.join(_quote_identifier(field.name) for field in schema)
            self.con.execute(f'insert into {target} ({columns}) select {columns} from {relation}')
        finally:
            self.con.unregister(relation)
            rows.clear()


def _ensure_silver_schema(con: duckdb.DuckDBPyConnection) -> None:
    for name, schema in SILVER_TABLE_SCHEMAS.items():
        fields = []
        for field in _schema_with_source_run(schema):
            fields.append(f'{_quote_identifier(field.name)} {_duckdb_type(field.type)}')
        con.execute(
            f'create table if not exists {_quote_identifier(SILVER_TABLE_PREFIX + name)} '
            f'({", ".join(fields)})'
        )


def _latest_bronze_snapshot(
    con: duckdb.DuckDBPyConnection,
    source: str,
    dataset: str,
) -> dict[str, Any] | None:
    row = con.execute(
        """
        select snapshot_id, manifest_json
        from bronze_dataset_snapshot
        where source = ? and dataset = ? and status = 'accepted'
        order by created_at desc
        limit 1
        """,
        [source, dataset],
    ).fetchone()
    if row is None:
        return None
    manifest = json.loads(row[1])
    return {
        'snapshot_id': str(row[0]),
        'typed_current_table': str(manifest['typed_current_table']),
    }


def _affected_raw_record_ids(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
    source_run_id: str,
) -> set[int]:
    rows = con.execute(
        """
        select distinct raw_record_id
        from bronze_raw_record_change
        where source_run_id = ?
          and source = ?
          and dataset = ?
          and raw_record_id is not null
        """,
        [source_run_id, source, dataset],
    ).fetchall()
    return {int(row[0]) for row in rows}


def _current_raw_record_ids(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
) -> set[int]:
    rows = con.execute(
        """
        select distinct raw_record_id
        from bronze_raw_record_current
        where source = ?
          and dataset = ?
          and raw_record_id is not null
        """,
        [source, dataset],
    ).fetchall()
    return {int(row[0]) for row in rows}


def _has_current_silver_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
) -> bool:
    table = _quote_identifier(SILVER_TABLE_PREFIX + 'entity_occurrence')
    count = con.execute(
        f"""
        select count(*)
        from {table}
        where source = ?
          and dataset = ?
        """,
        [source, dataset],
    ).fetchone()[0]
    return bool(count)


def _delete_silver_rows_for_raw_ids(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
    raw_record_ids: set[int],
) -> None:
    if not raw_record_ids:
        return
    id_table = pa.Table.from_arrays(
        [pa.array(sorted(raw_record_ids), type=pa.int64())],
        names=['_raw_record_id'],
    )
    con.register('silver_deleted_raw_ids', id_table)
    try:
        for name in SILVER_TABLE_NAMES:
            table = _quote_identifier(SILVER_TABLE_PREFIX + name)
            con.execute(
                f"""
                delete from {table}
                where source = ?
                  and dataset = ?
                  and _raw_record_id in (
                    select _raw_record_id from silver_deleted_raw_ids
                  )
                """,
                [source, dataset],
            )
    finally:
        con.unregister('silver_deleted_raw_ids')


def _iter_current_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
    typed_table: str,
) -> Iterable[dict[str, Any]]:
    offset = 0
    batch_size = 10_000
    while True:
        batch = con.execute(
            f"""
            select t.*
            from {_quote_identifier(typed_table)} t
            where t._source = ?
              and t._dataset = ?
            order by t.raw_record_bucket, t._raw_record_key
            limit {batch_size} offset {offset}
            """,
            [source, dataset],
        ).fetch_arrow_table()
        if batch.num_rows == 0:
            break
        for row in batch.to_pylist():
            yield row
        if batch.num_rows < batch_size:
            break
        offset += batch_size


def _iter_added_current_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    dataset: str,
    source_run_id: str,
    typed_table: str,
) -> Iterable[dict[str, Any]]:
    offset = 0
    batch_size = 10_000
    while True:
        batch = con.execute(
            f"""
            select t.*
            from {_quote_identifier(typed_table)} t
            join bronze_raw_record_change c
              on c.source = t._source
             and c.dataset = t._dataset
             and c.raw_record_key = t._raw_record_key
            where c.source_run_id = ?
              and c.source = ?
              and c.dataset = ?
              and c.change_type = 'added'
            order by t.raw_record_bucket, t._raw_record_key
            limit {batch_size} offset {offset}
            """,
            [source_run_id, source, dataset],
        ).fetch_arrow_table()
        if batch.num_rows == 0:
            break
        for row in batch.to_pylist():
            yield row
        if batch.num_rows < batch_size:
            break
        offset += batch_size


def _iter_mapped_entities(
    mapped: Any,
    *,
    output_kind: str,
    ontology_id: str | None,
    context: str,
) -> Iterable[tuple[str | None, Entity]]:
    if output_kind == 'ontology':
        if mapped is None:
            return
        if not isinstance(mapped, OntologyTerm):
            raise ValueError(f'{context} mapper returned {type(mapped)!r}; expected OntologyTerm')
        if not ontology_id:
            raise ValueError(f'{context} is missing ontology_id')
        from pypath.inputs_v2.base import ontology_term_to_entity

        yield None, ontology_term_to_entity(mapped, ontology_id=ontology_id)
        return
    if isinstance(mapped, Entity):
        yield None, mapped
        return
    if isinstance(mapped, dict):
        for output_name, value in mapped.items():
            if value is None:
                continue
            if not isinstance(value, Entity):
                raise ValueError(
                    f'{context}:{output_name} mapper returned {type(value)!r}; expected Entity'
                )
            yield str(output_name), value
        return
    raise ValueError(f'{context} mapper returned {type(mapped)!r}; expected Entity')


def _mapper_input(row: dict[str, Any]) -> dict[str, Any]:
    reserved = set(METADATA_COLUMNS) | {'snapshot_id'}
    return {key: value for key, value in row.items() if key not in reserved}


def _schema_with_source_run(schema: pa.Schema) -> pa.Schema:
    if 'source_run_id' in schema.names:
        return schema
    return schema.append(pa.field('source_run_id', pa.string()))


def _duckdb_type(data_type: pa.DataType) -> str:
    if pa.types.is_string(data_type):
        return 'varchar'
    if pa.types.is_int64(data_type):
        return 'bigint'
    if pa.types.is_boolean(data_type):
        return 'boolean'
    raise TypeError(f'Unsupported silver Arrow type for DuckDB rewrite: {data_type}')


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, 'value'):
        value = value.value
    text = str(value).strip()
    return text or None


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
