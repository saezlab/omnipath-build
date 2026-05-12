from __future__ import annotations

import glob
import io
import json
import time
from typing import Any
import logging
from pathlib import Path

import polars as pl
import psycopg2
from psycopg2 import sql
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import psycopg2.extensions

from omnipath_build.postgres.schema import ensure_schema
from omnipath_build.postgres.bitmaps import (
    create_bitmap_tables,
    _add_to_facet_bitmaps,
    populate_bitmap_tables,
    _remove_from_facet_bitmaps,
)
from omnipath_build.postgres.indexes import create_secondary_indexes
from omnipath_build.postgres.materialized_views import (
    create_ontology_terms_materialized_view,
    create_entity_relation_counts_materialized_view,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 200_000


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f'{int(minutes)}m {rem:.0f}s'
    hours, rem_minutes = divmod(minutes, 60)
    return f'{int(hours)}h {int(rem_minutes)}m'


def _log_step_start(message: str, *args: Any) -> float:
    logger.info('▶ ' + message, *args)
    return time.monotonic()


def _log_step_done(started_at: float, message: str, *args: Any) -> None:
    logger.info(
        '✓ ' + message + ' in %s',
        *args,
        _format_duration(time.monotonic() - started_at),
    )


def resolve_combined_dir(output_dir: str | Path) -> Path:
    path = Path(output_dir)
    if not path.exists():
        raise FileNotFoundError(
            f'Combined output directory does not exist: {path}'
        )
    if resolve_parquet_artifact(path, 'entity', required=False) is not None:
        return path
    # Try following a 'latest' symlink or directory for combined outputs
    latest = path / 'latest'
    if latest.is_symlink() or latest.is_dir():
        resolved = latest.resolve()
        if (
            resolve_parquet_artifact(resolved, 'entity', required=False)
            is not None
        ):
            return resolved
    raise FileNotFoundError(
        'Combined output directory is missing required artifact: '
        f'entity dataset or entity.parquet at {path}'
    )


def resolve_parquet_artifact(
    base_dir: str | Path,
    table_name: str,
    *,
    required: bool = True,
) -> Path | None:
    base_path = Path(base_dir)
    table_dir = base_path / table_name
    if _directory_contains_parquet(table_dir):
        return table_dir

    table_file = base_path / f'{table_name}.parquet'
    if table_file.exists():
        return table_file

    if required:
        raise FileNotFoundError(
            f'Missing parquet artifact for {table_name}: '
            f'expected {table_dir} or {table_file}'
        )
    return None


def _directory_contains_parquet(path: Path) -> bool:
    return path.is_dir() and any(
        child.is_file() for child in path.rglob('*.parquet')
    )


def resolve_combine_run_dir(
    output_dir: str | Path,
    combine_run_dir: str | Path | None = None,
) -> Path | None:
    if combine_run_dir is not None:
        run_dir = Path(combine_run_dir)
        return run_dir if (run_dir / 'manifest.json').exists() else None

    root = Path(output_dir)
    latest_path = root / 'runs' / 'latest.json'
    if not latest_path.exists():
        return None
    try:
        latest = json.loads(latest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    path = latest.get('path')
    if not path:
        return None
    run_dir = Path(path)
    if not run_dir.is_absolute():
        run_dir = root / 'runs' / str(latest.get('run_id', ''))
    return run_dir if (run_dir / 'manifest.json').exists() else None


def load_combined_schema_to_postgres(
    output_dir: str | Path,
    postgres_uri: str,
    schema: str = 'public',
    drop_existing: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    unlogged_tables: bool = False,
    foreign_keys: bool = False,
    tables: bool = True,
    indexes: bool = True,
    bitmaps: bool = True,
    views: bool = True,
    combine_run_dir: str | Path | None = None,
) -> int:
    combined_dir = resolve_combined_dir(output_dir)
    run_dir = resolve_combine_run_dir(output_dir, combine_run_dir)
    overall_started_at = _log_step_start(
        'Loading combined parquet artifacts from %s into schema %s',
        combined_dir,
        schema,
    )
    logger.info(
        'Enabled steps: tables=%s indexes=%s views=%s bitmaps=%s; batch_size=%s; unlogged_tables=%s; foreign_keys=%s',
        tables,
        indexes,
        views,
        bitmaps,
        f'{batch_size:,}',
        unlogged_tables,
        foreign_keys,
    )
    with psycopg2.connect(postgres_uri) as conn:
        started_at = _log_step_start('Ensuring PostgreSQL schema')
        ensure_schema(
            conn,
            schema=schema,
            drop_existing=drop_existing and tables,
            unlogged_tables=unlogged_tables and tables,
            foreign_keys=foreign_keys and tables,
        )
        _log_step_done(started_at, 'Schema ready')

        affected_entity_ids_before: list[int] = []
        affected_relation_ids_before: list[int] = []
        if tables:
            target_has_data = _base_tables_have_data(conn, schema)
            has_run_delta = (
                run_dir is not None
                and _run_delta_is_incremental(run_dir)
                and _run_delta_has_work(run_dir)
            )
            if has_run_delta and target_has_data and not drop_existing:
                logger.info(
                    'PostgreSQL load action: combine run delta run_dir=%s',
                    run_dir,
                )
                if bitmaps:
                    started_at = _log_step_start('Ensuring bitmap tables')
                    create_bitmap_tables(conn, schema=schema)
                    _log_step_done(started_at, 'Bitmap tables ready')

                affected_entity_ids_before, affected_relation_ids_before = _run_delta_delete_ids(run_dir)
                if bitmaps and (affected_entity_ids_before or affected_relation_ids_before):
                    started_at = _log_step_start('Removing affected IDs from bitmaps')
                    _remove_from_facet_bitmaps(
                        conn,
                        schema=schema,
                        affected_entity_ids=affected_entity_ids_before,
                        affected_relation_ids=affected_relation_ids_before,
                    )
                    _log_step_done(started_at, 'Affected IDs removed from bitmaps')

                started_at = _log_step_start('Applying combine run delta')
                load_tables_from_run_delta(
                    conn,
                    schema=schema,
                    run_dir=run_dir,
                    batch_size=batch_size,
                )
                _log_step_done(started_at, 'Combine run delta applied')

                affected_entity_ids_after, affected_relation_ids_after = _run_delta_upsert_ids(run_dir)
                if bitmaps and (affected_entity_ids_after or affected_relation_ids_after):
                    started_at = _log_step_start('Adding affected IDs to bitmaps')
                    _add_to_facet_bitmaps(
                        conn,
                        schema=schema,
                        affected_entity_ids=affected_entity_ids_after,
                        affected_relation_ids=affected_relation_ids_after,
                    )
                    _log_step_done(started_at, 'Affected IDs added to bitmaps')
            elif not target_has_data or drop_existing:
                logger.info(
                    'PostgreSQL load action: bootstrap '
                    '(target_has_data=%s drop_existing=%s)',
                    target_has_data,
                    drop_existing,
                )
                started_at = _log_step_start('Bootstrapping base tables')
                _load_tables_full(
                    conn,
                    schema=schema,
                    combined_dir=combined_dir,
                    batch_size=batch_size,
                )
                _log_step_done(started_at, 'Base tables bootstrapped')
            else:
                logger.info(
                    'PostgreSQL load action: no table delta supplied; '
                    'leaving existing base tables unchanged'
                )
            if indexes:
                started_at = _log_step_start('Creating secondary indexes')
                create_secondary_indexes(conn, schema=schema)
                _log_step_done(started_at, 'Secondary indexes created')
            if views:
                started_at = _log_step_start('Creating materialized views')
                create_entity_relation_counts_materialized_view(conn, schema=schema)
                create_ontology_terms_materialized_view(conn, schema=schema)
                _log_step_done(started_at, 'Materialized views created')
            if bitmaps and (not target_has_data or drop_existing):
                started_at = _log_step_start('Creating and populating bitmap tables')
                create_bitmap_tables(conn, schema=schema)
                populate_bitmap_tables(conn, schema=schema)
                _log_step_done(started_at, 'Bitmap tables populated')

    _log_step_done(overall_started_at, 'Combined PostgreSQL schema load complete')
    return 0


def _base_tables_have_data(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT
                    EXISTS (SELECT 1 FROM {}.entity LIMIT 1)
                    OR EXISTS (SELECT 1 FROM {}.entity_relation LIMIT 1)
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        return bool(cur.fetchone()[0])


def _load_tables_full(
    conn: psycopg2.extensions.connection,
    schema: str,
    combined_dir: Path,
    batch_size: int,
) -> None:
    started_at = _log_step_start('Truncating existing tables')
    _truncate_tables(conn, schema)
    _log_step_done(started_at, 'Existing tables truncated')

    parquet_path = resolve_parquet_artifact(combined_dir, 'entity', required=False)
    if parquet_path is not None:
        _load_entity_and_identifiers(
            conn,
            schema=schema,
            parquet_path=parquet_path,
            batch_size=batch_size,
        )

    _copy_parquet_to_table(
        conn,
        parquet_path=resolve_parquet_artifact(
            combined_dir,
            'entity_relation',
            required=False,
        ),
        schema=schema,
        table='entity_relation',
        columns=(
            'relation_id',
            'relation_key',
            'subject_entity_id',
            'subject_entity_key',
            'predicate',
            'object_entity_id',
            'object_entity_key',
            'relation_category',
            'participant_types',
            'evidence_count',
            'sources',
        ),
        serializers={
            'participant_types': _serialize_json,
            'sources': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=resolve_parquet_artifact(
            combined_dir,
            'entity_relation_evidence',
            required=False,
        ),
        schema=schema,
        table='entity_relation_evidence',
        columns=(
            'relation_evidence_id',
            'relation_id',
            'relation_key',
            'source',
            'raw_record_id',
            'record_attributes',
            'subject_attributes',
            'object_attributes',
            'evidence',
        ),
        serializers={
            'record_attributes': _serialize_json,
            'subject_attributes': _serialize_json,
            'object_attributes': _serialize_json,
            'evidence': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=resolve_parquet_artifact(
            combined_dir,
            'entity_evidence',
            required=False,
        ),
        schema=schema,
        table='entity_evidence',
        columns=(
            'source',
            'entity_key',
            'raw_record_ids',
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
        ),
        serializers={
            'raw_record_ids': _serialize_json,
            'identifiers': _serialize_json,
            'entity_attributes': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=resolve_parquet_artifact(
            combined_dir,
            'relation_annotation_term',
            required=False,
        ),
        schema=schema,
        table='relation_annotation_term',
        columns=(
            'relation_id',
            'relation_evidence_id',
            'source',
            'scope',
            'term_entity_id',
        ),
        serializers={},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=resolve_parquet_artifact(
            combined_dir,
            'resources',
            required=False,
        ),
        schema=schema,
        table='resources',
        columns=(
            'resource_id',
            'resource_name',
            'description',
            'homepage_url',
            'license',
            'pubmed_id',
            'resource_kind',
            'categories',
            'annotation_ontologies',
            'entity_count',
            'interaction_count',
            'association_count',
            'identifier_count',
            'ontology_term_count',
            'total_size_bytes',
            'last_downloaded_at',
            'last_built_at',
            'build_status',
        ),
        serializers={
            'categories': _serialize_json,
            'annotation_ontologies': _serialize_json,
        },
        batch_size=batch_size,
    )


def _load_entity_and_identifiers(
    conn: psycopg2.extensions.connection,
    schema: str,
    parquet_path: str | Path,
    batch_size: int,
    filter_keys: list[Any] | None = None,
    key_column: str = 'entity_key',
) -> None:
    label = 'filtered entity' if filter_keys is not None else 'entity'
    logger.info(
        'COPY %s %s -> %s.entity and %s.entity_identifier',
        label,
        _parquet_source_label(parquet_path),
        schema,
        schema,
    )

    entity_columns = (
        'entity_id',
        'entity_key',
        'canonical_identifier',
        'canonical_identifier_type',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
    )
    entity_copy = sql.SQL(
        "COPY {}.entity ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
    ).format(
        sql.Identifier(schema),
        sql.SQL(', ').join(sql.Identifier(c) for c in entity_columns),
    )
    identifier_copy = sql.SQL(
        "COPY {}.entity_identifier (entity_id, identifier, identifier_type) FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
    ).format(sql.Identifier(schema))

    total_entities = 0
    total_identifiers = 0
    parquet_dataset = _open_parquet_dataset(parquet_path)
    expected_entities = parquet_dataset.count_rows()
    started_at = time.monotonic()
    next_log_at = batch_size
    source_columns = list(dict.fromkeys([*entity_columns, 'identifiers']))

    with conn.cursor() as cur:
        for batch_index, batch in enumerate(
            parquet_dataset.scanner(
                batch_size=batch_size,
                columns=source_columns,
            ).to_batches(),
            start=1,
        ):
            df = pl.from_arrow(batch)
            if df.is_empty():
                continue

            if filter_keys is not None:
                df = df.filter(pl.col(key_column).is_in(filter_keys))
                if df.is_empty():
                    continue

            # Entity table
            ent_df = df.select(entity_columns)
            ent_df = _apply_serializers(
                ent_df,
                {
                    'entity_attributes': _serialize_json,
                    'sources': _serialize_json,
                },
            )

            buffer = io.StringIO()
            ent_df.write_csv(buffer, null_value='\\N', include_header=False)
            buffer.seek(0)
            cur.copy_expert(entity_copy.as_string(conn), buffer)
            total_entities += ent_df.height

            # Identifiers: canonical + exploded additional identifiers
            canon = df.select(
                [
                    pl.col('entity_id'),
                    pl.col('canonical_identifier').alias('identifier'),
                    pl.col('canonical_identifier_type').alias(
                        'identifier_type'
                    ),
                ]
            )
            exploded = (
                df.select(['entity_id', 'identifiers'])
                .explode('identifiers')
                .with_columns(
                    [
                        pl.col('identifiers').struct.field('identifier'),
                        pl.col('identifiers').struct.field('identifier_type'),
                    ]
                )
                .drop('identifiers')
            )
            id_df = pl.concat([canon, exploded])

            buffer = io.StringIO()
            id_df.write_csv(buffer, null_value='\\N', include_header=False)
            buffer.seek(0)
            cur.copy_expert(identifier_copy.as_string(conn), buffer)
            total_identifiers += id_df.height

            if total_entities >= next_log_at or total_entities >= expected_entities:
                elapsed = max(time.monotonic() - started_at, 0.001)
                rate = total_entities / elapsed
                percent = (
                    total_entities / expected_entities * 100
                    if expected_entities
                    else 100.0
                )
                logger.info(
                    '  entity batch %s: %s/%s entities (%.1f%%), %s identifiers, %.0f entities/s',
                    batch_index,
                    f'{total_entities:,}',
                    f'{expected_entities:,}',
                    percent,
                    f'{total_identifiers:,}',
                    rate,
                )
                next_log_at = total_entities + batch_size

    conn.commit()
    logger.info(
        '  loaded %s entity row(s), %s identifier row(s) in %s',
        f'{total_entities:,}',
        f'{total_identifiers:,}',
        _format_duration(time.monotonic() - started_at),
    )


def load_tables_from_run_delta(
    conn: psycopg2.extensions.connection,
    schema: str,
    run_dir: Path,
    batch_size: int,
) -> None:
    delta_dir = run_dir / 'delta'
    started_at = _log_step_start('Deleting rows from combine delta')
    entity_ids = _read_int_column(delta_dir / 'entity_delete.parquet', 'entity_id')
    relation_ids = _read_int_column(
        delta_dir / 'entity_relation_delete.parquet',
        'relation_id',
    )
    entity_evidence_delete = _read_optional_frame(
        delta_dir / 'entity_evidence_delete.parquet',
        ['source', 'entity_key'],
    )
    relation_annotation_ids = _read_int_column(
        delta_dir / 'relation_annotation_term_delete.parquet',
        'relation_id',
    )
    relation_ids_for_annotation = sorted(set(relation_ids) | set(relation_annotation_ids))

    with conn.cursor() as cur:
        if relation_ids_for_annotation:
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.relation_annotation_term WHERE relation_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (relation_ids_for_annotation,),
            )
        if relation_ids:
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity_relation_evidence WHERE relation_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (relation_ids,),
            )
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity_relation WHERE relation_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (relation_ids,),
            )
        if not entity_evidence_delete.is_empty():
            source_rows = entity_evidence_delete.filter(pl.col('source').is_not_null())
            if not source_rows.is_empty():
                pairs = [
                    (row['source'], row['entity_key'])
                    for row in source_rows.to_dicts()
                    if row['entity_key'] is not None
                ]
                if pairs:
                    cur.execute(
                        sql.SQL(
                            'DELETE FROM {}.entity_evidence e '
                            'USING (SELECT * FROM unnest(%s::text[], %s::text[]) AS t(source, entity_key)) d '
                            'WHERE e.source = d.source AND e.entity_key = d.entity_key'
                        ).format(sql.Identifier(schema)),
                        ([source for source, _ in pairs], [key for _, key in pairs]),
                    )
            null_source_keys = (
                entity_evidence_delete
                .filter(pl.col('source').is_null())
                .get_column('entity_key')
                .drop_nulls()
                .unique()
                .to_list()
            )
            if null_source_keys:
                cur.execute(
                    sql.SQL(
                        'DELETE FROM {}.entity_evidence WHERE entity_key = ANY(%s)'
                    ).format(sql.Identifier(schema)),
                    (null_source_keys,),
                )
        if entity_ids:
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity_identifier WHERE entity_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (entity_ids,),
            )
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity WHERE entity_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (entity_ids,),
            )
    conn.commit()
    _log_step_done(
        started_at,
        'Deleted delta rows entities=%s relations=%s entity_evidence_keys=%s',
        len(entity_ids),
        len(relation_ids),
        0 if entity_evidence_delete.is_empty() else entity_evidence_delete.height,
    )

    entity_upsert = delta_dir / 'entity_upsert.parquet'
    if entity_upsert.exists():
        _load_entity_and_identifiers(
            conn,
            schema=schema,
            parquet_path=entity_upsert,
            batch_size=batch_size,
        )
    _copy_parquet_to_table(
        conn,
        parquet_path=delta_dir / 'entity_relation_upsert.parquet',
        schema=schema,
        table='entity_relation',
        columns=(
            'relation_id',
            'relation_key',
            'subject_entity_id',
            'subject_entity_key',
            'predicate',
            'object_entity_id',
            'object_entity_key',
            'relation_category',
            'participant_types',
            'evidence_count',
            'sources',
        ),
        serializers={
            'participant_types': _serialize_json,
            'sources': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=delta_dir / 'entity_relation_evidence_upsert.parquet',
        schema=schema,
        table='entity_relation_evidence',
        columns=(
            'relation_evidence_id',
            'relation_id',
            'relation_key',
            'source',
            'raw_record_id',
            'record_attributes',
            'subject_attributes',
            'object_attributes',
            'evidence',
        ),
        serializers={
            'record_attributes': _serialize_json,
            'subject_attributes': _serialize_json,
            'object_attributes': _serialize_json,
            'evidence': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=delta_dir / 'entity_evidence_upsert.parquet',
        schema=schema,
        table='entity_evidence',
        columns=(
            'source',
            'entity_key',
            'raw_record_ids',
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
        ),
        serializers={
            'raw_record_ids': _serialize_json,
            'identifiers': _serialize_json,
            'entity_attributes': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=delta_dir / 'relation_annotation_term_upsert.parquet',
        schema=schema,
        table='relation_annotation_term',
        columns=(
            'relation_id',
            'relation_evidence_id',
            'source',
            'scope',
            'term_entity_id',
        ),
        serializers={},
        batch_size=batch_size,
    )


def _run_delta_has_work(run_dir: Path) -> bool:
    manifest_path = run_dir / 'manifest.json'
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            manifest = {}
        delta_counts = manifest.get('delta_counts')
        if isinstance(delta_counts, dict):
            return any(int(value or 0) > 0 for value in delta_counts.values())
    return any(
        path.exists() and pq.ParquetFile(path).metadata.num_rows > 0
        for path in (run_dir / 'delta').glob('*.parquet')
    )


def _run_delta_is_incremental(run_dir: Path) -> bool:
    manifest_path = run_dir / 'manifest.json'
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return False
    return manifest.get('mode') == 'incremental'


def _run_delta_delete_ids(run_dir: Path) -> tuple[list[int], list[int]]:
    delta_dir = run_dir / 'delta'
    return (
        _read_int_column(delta_dir / 'entity_delete.parquet', 'entity_id'),
        _read_int_column(delta_dir / 'entity_relation_delete.parquet', 'relation_id'),
    )


def _run_delta_upsert_ids(run_dir: Path) -> tuple[list[int], list[int]]:
    delta_dir = run_dir / 'delta'
    return (
        _read_int_column(delta_dir / 'entity_upsert.parquet', 'entity_id'),
        _read_int_column(delta_dir / 'entity_relation_upsert.parquet', 'relation_id'),
    )


def _read_int_column(path: Path, column: str) -> list[int]:
    if not path.exists():
        return []
    scan = pl.scan_parquet(path)
    if column not in scan.collect_schema().names():
        return []
    frame = scan.select(column).collect()
    return [
        int(value)
        for value in frame.get_column(column).drop_nulls().unique().to_list()
    ]


def _read_optional_frame(path: Path, columns: list[str]) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame({column: pl.Series([], dtype=pl.String) for column in columns})
    frame = pl.read_parquet(path)
    for column in columns:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=pl.String).alias(column))
    return frame.select(columns)


def _truncate_tables(conn: psycopg2.extensions.connection, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                'TRUNCATE TABLE {}.resources, {}.relation_annotation_term, {}.entity_evidence, '
                '{}.entity_relation_evidence, {}.entity_relation, {}.entity_identifier, {}.entity CASCADE'
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
    conn.commit()


def _apply_serializers(
    df: pl.DataFrame, serializers: dict[str, Any]
) -> pl.DataFrame:
    for col, serializer in serializers.items():
        py_values = df[col].to_list()
        if serializer is _serialize_json:
            serialized = [
                json.dumps(x, separators=(',', ':')) if x is not None else None
                for x in py_values
            ]
        elif serializer is _serialize_pg_text_array:
            serialized = [
                _serialize_pg_text_array(x) if x is not None else None
                for x in py_values
            ]
        else:
            serialized = [
                serializer(x) if x is not None else None for x in py_values
            ]
        df = df.with_columns(pl.Series(name=col, values=serialized))
    return df


def _copy_parquet_to_table(
    conn: psycopg2.extensions.connection,
    *,
    parquet_path: str | Path | None,
    schema: str,
    table: str,
    columns: tuple[str, ...],
    serializers: dict[str, Any],
    batch_size: int,
    filter_column: str | None = None,
    filter_keys: list[str] | None = None,
) -> None:
    if parquet_path is None:
        logger.info('Skipping missing artifact for table: %s', table)
        return

    if not _parquet_source_exists(parquet_path):
        logger.info('Skipping missing artifact: %s', parquet_path)
        return

    if filter_keys is not None and not filter_keys:
        logger.info('Skipping %s: no filter keys', table)
        return

    label = 'filtered' if filter_keys is not None else ''
    logger.info(
        'COPY %s %s -> %s.%s',
        label,
        _parquet_source_label(parquet_path),
        schema,
        table,
    )
    copy_sql = sql.SQL(
        "COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
    ).format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )

    total_rows = 0
    parquet_dataset = _open_parquet_dataset(parquet_path)
    expected_rows = parquet_dataset.count_rows()
    started_at = time.monotonic()
    next_log_at = batch_size
    source_columns = list(
        dict.fromkeys([*columns, *([filter_column] if filter_column else [])])
    )

    with conn.cursor() as cur:
        for batch_index, batch in enumerate(
            parquet_dataset.scanner(
                batch_size=batch_size,
                columns=source_columns,
            ).to_batches(),
            start=1,
        ):
            df = pl.from_arrow(batch)
            if df.is_empty():
                continue

            if filter_keys is not None:
                df = df.filter(pl.col(filter_column).is_in(filter_keys))
                if df.is_empty():
                    continue

            df = df.select(list(columns))
            df = _apply_serializers(df, serializers)

            buffer = io.StringIO()
            df.write_csv(buffer, null_value='\\N', include_header=False)
            buffer.seek(0)
            cur.copy_expert(copy_sql.as_string(conn), buffer)
            total_rows += df.height

            if total_rows >= next_log_at or total_rows >= expected_rows:
                elapsed = max(time.monotonic() - started_at, 0.001)
                rate = total_rows / elapsed
                percent = (
                    total_rows / expected_rows * 100
                    if expected_rows
                    else 100.0
                )
                logger.info(
                    '  %s batch %s: %s/%s rows (%.1f%%), %.0f rows/s',
                    table,
                    batch_index,
                    f'{total_rows:,}',
                    f'{expected_rows:,}',
                    percent,
                    rate,
                )
                next_log_at = total_rows + batch_size

    conn.commit()
    logger.info(
        '  loaded %s row(s) into %s in %s',
        f'{total_rows:,}',
        table,
        _format_duration(time.monotonic() - started_at),
    )


def _parquet_source_exists(source: str | Path) -> bool:
    if isinstance(source, Path):
        return source.exists()
    if glob.has_magic(source):
        return bool(glob.glob(source, recursive=True))
    return Path(source).exists()


def _parquet_source_label(source: str | Path) -> str:
    return source.name if isinstance(source, Path) else source


def _open_parquet_dataset(source: str | Path) -> ds.Dataset:
    if isinstance(source, str) and glob.has_magic(source):
        paths = glob.glob(source, recursive=True)
        if not paths:
            raise FileNotFoundError(f'No parquet files matched: {source}')
        return ds.dataset(paths, format='parquet', partitioning='hive')
    return ds.dataset(str(source), format='parquet', partitioning='hive')


def _serialize_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(',', ':'))


def _serialize_pg_text_array(value: Any) -> str:
    if value is None:
        return '{}'
    items = list(value)
    escaped = []
    for item in items:
        if item is None:
            escaped.append('NULL')
            continue
        text = str(item).replace('\\', '\\\\').replace('"', '\\"')
        escaped.append(f'"{text}"')
    return '{' + ','.join(escaped) + '}'
