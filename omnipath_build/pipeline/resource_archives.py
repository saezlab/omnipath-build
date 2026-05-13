from __future__ import annotations

from pathlib import Path
import tempfile
import zipfile

import duckdb

RESOURCE_ARCHIVE_SUFFIX = '.zip'

ARCHIVE_TABLES: tuple[tuple[str, str, str], ...] = (
    ('entities', 'entity', 'entities/entity.parquet'),
    ('relations', 'entity_relation', 'relations/entity_relation.parquet'),
    ('relations', 'entity_relation_evidence', 'relations/entity_relation_evidence.parquet'),
)


def resource_archive_name(resource_id: str) -> str:
    return f'{resource_id}{RESOURCE_ARCHIVE_SUFFIX}'


def resource_archive_path(source_dir: Path, resource_id: str) -> Path:
    return source_dir / resource_archive_name(resource_id)


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _read_dataset_sql(path: Path) -> str:
    if path.is_dir():
        return (
            "read_parquet("
            f"'{_sql_path(path / '**' / '*.parquet')}', "
            "union_by_name=true, hive_partitioning=false)"
        )
    return f"read_parquet('{_sql_path(path)}', union_by_name=true)"


def _source_table_path(source_dir: Path, section: str, table: str) -> Path | None:
    for path in (
        source_dir / section / table,
        source_dir / section / f'{table}.parquet',
    ):
        if path.exists():
            return path
    return None


def _dataset_columns(con: duckdb.DuckDBPyConnection, path: Path) -> set[str]:
    rows = con.execute(f"describe select * from {_read_dataset_sql(path)} limit 0").fetchall()
    return {str(row[0]) for row in rows}


def _column_expr(existing_columns: set[str], candidates: tuple[str, ...], alias: str) -> str:
    for column in candidates:
        if column in existing_columns:
            return f'"{column}" as "{alias}"'
    return f'null as "{alias}"'


def _archive_select_sql(table: str, source_path: Path, existing_columns: set[str]) -> str:
    source_sql = _read_dataset_sql(source_path)
    if table == 'entity':
        columns = [
            _column_expr(existing_columns, ('entity_id', 'entity_pk'), 'entity_id'),
            _column_expr(existing_columns, ('canonical_identifier',), 'canonical_identifier'),
            _column_expr(existing_columns, ('canonical_identifier_type',), 'canonical_identifier_type'),
            _column_expr(existing_columns, ('identifiers',), 'identifiers'),
            _column_expr(existing_columns, ('entity_type',), 'entity_type'),
            _column_expr(existing_columns, ('taxonomy_id',), 'taxonomy_id'),
            _column_expr(existing_columns, ('entity_attributes',), 'entity_attributes'),
            _column_expr(existing_columns, ('sources',), 'sources'),
        ]
        return f"select {', '.join(columns)} from {source_sql}"
    if table == 'entity_evidence':
        columns = [
            _column_expr(existing_columns, ('entity_id', 'entity_pk'), 'entity_id'),
            _column_expr(existing_columns, ('source',), 'source'),
            _column_expr(existing_columns, ('raw_record_id',), 'raw_record_id'),
            _column_expr(existing_columns, ('occurrence_id',), 'occurrence_id'),
            _column_expr(existing_columns, ('fingerprint',), 'fingerprint'),
            _column_expr(existing_columns, ('entity_type',), 'entity_type'),
            _column_expr(existing_columns, ('taxonomy_id',), 'taxonomy_id'),
            _column_expr(existing_columns, ('identifiers',), 'identifiers'),
            _column_expr(existing_columns, ('entity_attributes',), 'entity_attributes'),
        ]
        return f"select {', '.join(columns)} from {source_sql}"
    if table == 'entity_relation':
        columns = [
            _column_expr(existing_columns, ('relation_id', 'relation_pk'), 'relation_id'),
            _column_expr(existing_columns, ('subject_entity_id', 'subject_entity_pk'), 'subject_entity_id'),
            _column_expr(existing_columns, ('predicate',), 'predicate'),
            _column_expr(existing_columns, ('object_entity_id', 'object_entity_pk'), 'object_entity_id'),
            _column_expr(existing_columns, ('relation_category',), 'relation_category'),
            _column_expr(existing_columns, ('evidence_count',), 'evidence_count'),
            _column_expr(existing_columns, ('sources',), 'sources'),
        ]
        return f"select {', '.join(columns)} from {source_sql}"
    if table == 'entity_relation_evidence':
        columns = [
            _column_expr(existing_columns, ('relation_evidence_id', 'relation_evidence_pk'), 'relation_evidence_id'),
            _column_expr(existing_columns, ('relation_id', 'relation_pk'), 'relation_id'),
            _column_expr(existing_columns, ('source',), 'source'),
            _column_expr(existing_columns, ('record_attributes',), 'record_attributes'),
            _column_expr(existing_columns, ('subject_attributes',), 'subject_attributes'),
            _column_expr(existing_columns, ('object_attributes',), 'object_attributes'),
            _column_expr(existing_columns, ('evidence',), 'evidence'),
        ]
        return f"select {', '.join(columns)} from {source_sql}"
    raise ValueError(f'Unsupported archive table: {table}')


def _write_archive_table(
    con: duckdb.DuckDBPyConnection,
    *,
    source_path: Path,
    output_path: Path,
    table: str,
) -> None:
    existing_columns = _dataset_columns(con, source_path)
    query = _archive_select_sql(table, source_path, existing_columns)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""
        copy ({query})
        to '{_sql_path(output_path)}' (format parquet)
    """)


def build_resource_archive(source_dir: Path, resource_id: str) -> Path:
    source_dir = Path(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    archive_path = resource_archive_path(source_dir, resource_id)
    if archive_path.exists():
        archive_path.unlink()

    with tempfile.TemporaryDirectory(prefix=f'{resource_id}-archive-') as temp_name:
        temp_dir = Path(temp_name)
        con = duckdb.connect()
        try:
            archive_entries: list[tuple[Path, str]] = []
            for section, table, archive_name in ARCHIVE_TABLES:
                source_path = _source_table_path(source_dir, section, table)
                if source_path is None:
                    continue
                temp_path = temp_dir / archive_name
                _write_archive_table(
                    con,
                    source_path=source_path,
                    output_path=temp_path,
                    table=table,
                )
                if temp_path.exists():
                    archive_entries.append((temp_path, archive_name))
        finally:
            con.close()

        if not archive_entries:
            raise ValueError(
                f'No gold artifacts available to archive for resource {resource_id!r} in {source_dir}'
            )

        with zipfile.ZipFile(archive_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            for source_path, archive_name in archive_entries:
                zf.write(source_path, arcname=archive_name)

    return archive_path
