from __future__ import annotations

import io
import json
from typing import Any
import logging
from pathlib import Path
import time

import polars as pl
import psycopg2
from psycopg2 import sql
import pyarrow.parquet as pq
import psycopg2.extensions

from omnipath_build.postgres.schema import ensure_schema
from omnipath_build.postgres.bitmaps import (
    _add_to_facet_bitmaps,
    _remove_from_facet_bitmaps,
    create_bitmap_tables,
    populate_bitmap_tables,
    refresh_bitmap_tables_incremental,
)
from omnipath_build.postgres.indexes import create_secondary_indexes
from omnipath_build.postgres.materialized_views import (
    create_entity_relation_counts_materialized_view,
    create_ontology_terms_materialized_view,
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
    if (path / 'entity.parquet').exists():
        return path
    # Try following a 'latest' symlink or directory for combined outputs
    latest = path / 'latest'
    if latest.is_symlink() or latest.is_dir():
        resolved = latest.resolve()
        if (resolved / 'entity.parquet').exists():
            return resolved
    raise FileNotFoundError(
        f'Combined output directory is missing required artifact: entity.parquet at {path}'
    )


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
    mode: str = 'full',
    affected_entity_keys: list[str] | None = None,
    affected_relation_keys: list[str] | None = None,
    changed_source: str | None = None,
) -> int:
    combined_dir = resolve_combined_dir(output_dir)
    overall_started_at = _log_step_start(
        'Loading combined parquet artifacts from %s into schema %s (mode=%s)',
        combined_dir,
        schema,
        mode,
    )
    logger.info(
        'Enabled steps: tables=%s indexes=%s views=%s bitmaps=%s; batch_size=%s; unlogged_tables=%s; foreign_keys=%s; mode=%s',
        tables,
        indexes,
        views,
        bitmaps,
        f'{batch_size:,}',
        unlogged_tables,
        foreign_keys,
        mode,
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

    affected_entity_ids: list[int] = []
    affected_relation_ids: list[int] = []
    if tables:
        if mode == 'incremental':
            # Phase 0: Query affected IDs before base table update
            affected_entity_ids, affected_relation_ids = _query_affected_ids(
                conn,
                schema=schema,
                affected_entity_keys=affected_entity_keys or [],
                affected_relation_keys=affected_relation_keys or [],
            )

            # Phase 0b: Remove affected IDs from bitmaps BEFORE base tables change
            if bitmaps and (affected_entity_ids or affected_relation_ids):
                started_at = _log_step_start('Removing affected IDs from bitmaps')
                _remove_from_facet_bitmaps(
                    conn,
                    schema=schema,
                    affected_entity_ids=affected_entity_ids,
                    affected_relation_ids=affected_relation_ids,
                )
                _log_step_done(started_at, 'Affected IDs removed from bitmaps')

            # Phase 1: Update base tables
            started_at = _log_step_start('Loading base tables incrementally')
            load_tables_incremental(
                conn,
                schema=schema,
                combined_dir=combined_dir,
                affected_entity_keys=affected_entity_keys or [],
                affected_relation_keys=affected_relation_keys or [],
                changed_source=changed_source,
                batch_size=batch_size,
            )
            _log_step_done(started_at, 'Base tables loaded incrementally')

            # Phase 2: Add affected IDs to new bitmaps AFTER base tables updated
            if bitmaps and (affected_entity_ids or affected_relation_ids):
                started_at = _log_step_start('Adding affected IDs to bitmaps')
                _add_to_facet_bitmaps(
                    conn,
                    schema=schema,
                    affected_entity_ids=affected_entity_ids,
                    affected_relation_ids=affected_relation_ids,
                )
                _log_step_done(started_at, 'Affected IDs added to bitmaps')
        else:
            started_at = _log_step_start('Loading base tables')
            _load_tables_full(
                conn,
                schema=schema,
                combined_dir=combined_dir,
                batch_size=batch_size,
            )
            _log_step_done(started_at, 'Base tables loaded')
        if indexes:
            started_at = _log_step_start('Creating secondary indexes')
            create_secondary_indexes(conn, schema=schema)
            _log_step_done(started_at, 'Secondary indexes created')
        if views:
            started_at = _log_step_start('Creating materialized views')
            create_entity_relation_counts_materialized_view(conn, schema=schema)
            create_ontology_terms_materialized_view(conn, schema=schema)
            _log_step_done(started_at, 'Materialized views created')
        if bitmaps and mode != 'incremental':
            started_at = _log_step_start('Creating and populating bitmap tables')
            create_bitmap_tables(conn, schema=schema)
            populate_bitmap_tables(conn, schema=schema)
            _log_step_done(started_at, 'Bitmap tables populated')

    _log_step_done(overall_started_at, 'Combined PostgreSQL schema load complete')
    return 0


def _query_affected_ids(
    conn: psycopg2.extensions.connection,
    schema: str,
    affected_entity_keys: list[str],
    affected_relation_keys: list[str],
) -> tuple[list[int], list[int]]:
    """Query affected entity/relation IDs from the database before update."""
    affected_entity_ids: list[int] = []
    affected_relation_ids: list[int] = []
    with conn.cursor() as cur:
        if affected_entity_keys:
            cur.execute(
                sql.SQL(
                    'SELECT entity_id FROM {}.entity WHERE entity_key = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_entity_keys,),
            )
            affected_entity_ids = [row[0] for row in cur.fetchall()]
        if affected_relation_keys:
            cur.execute(
                sql.SQL(
                    'SELECT relation_id FROM {}.entity_relation WHERE relation_key = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_relation_keys,),
            )
            affected_relation_ids = [row[0] for row in cur.fetchall()]
    return affected_entity_ids, affected_relation_ids


def _load_tables_full(
    conn: psycopg2.extensions.connection,
    schema: str,
    combined_dir: Path,
    batch_size: int,
) -> None:
    started_at = _log_step_start('Truncating existing tables')
    _truncate_tables(conn, schema)
    _log_step_done(started_at, 'Existing tables truncated')

    parquet_path = combined_dir / 'entity.parquet'
    if parquet_path.exists():
        _load_entity_and_identifiers(
            conn,
            schema=schema,
            parquet_path=parquet_path,
            batch_size=batch_size,
        )

    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity_relation.parquet',
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
        parquet_path=combined_dir / 'entity_relation_evidence.parquet',
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
        parquet_path=combined_dir / 'entity_evidence.parquet',
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
        parquet_path=combined_dir / 'relation_annotation_term.parquet',
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
        parquet_path=combined_dir / 'resources.parquet',
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
    parquet_path: Path,
    batch_size: int,
    filter_keys: list[str] | None = None,
    key_column: str = 'entity_key',
) -> None:
    label = 'filtered entity' if filter_keys is not None else 'entity'
    logger.info(
        'COPY %s.parquet -> %s.entity and %s.entity_identifier',
        label,
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
    parquet_file = pq.ParquetFile(parquet_path)
    expected_entities = parquet_file.metadata.num_rows
    started_at = time.monotonic()
    next_log_at = batch_size

    with conn.cursor() as cur:
        for batch_index, batch in enumerate(
            parquet_file.iter_batches(batch_size=batch_size), start=1
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


def load_tables_incremental(
    conn: psycopg2.extensions.connection,
    schema: str,
    combined_dir: Path,
    affected_entity_keys: list[str],
    affected_relation_keys: list[str],
    changed_source: str | None,
    batch_size: int,
) -> tuple[list[int], list[int]]:
    started_at = _log_step_start('Deleting old rows for affected keys')

    affected_entity_ids: list[int] = []
    affected_relation_ids: list[int] = []
    with conn.cursor() as cur:
        if affected_entity_keys:
            cur.execute(
                sql.SQL(
                    'SELECT entity_id FROM {}.entity WHERE entity_key = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_entity_keys,),
            )
            affected_entity_ids = [row[0] for row in cur.fetchall()]
        if affected_relation_keys:
            cur.execute(
                sql.SQL(
                    'SELECT relation_id FROM {}.entity_relation WHERE relation_key = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_relation_keys,),
            )
            affected_relation_ids = [row[0] for row in cur.fetchall()]

        if affected_relation_ids:
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.relation_annotation_term WHERE relation_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_relation_ids,),
            )
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity_relation_evidence WHERE relation_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_relation_ids,),
            )
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity_relation WHERE relation_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_relation_ids,),
            )
        if affected_entity_ids:
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity_identifier WHERE entity_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_entity_ids,),
            )
            cur.execute(
                sql.SQL(
                    'DELETE FROM {}.entity WHERE entity_id = ANY(%s)'
                ).format(sql.Identifier(schema)),
                (affected_entity_ids,),
            )
        if changed_source:
            cur.execute(
                sql.SQL(
                    "DELETE FROM {}.entity_evidence WHERE source = %s"
                ).format(sql.Identifier(schema)),
                (changed_source,),
            )
    conn.commit()
    _log_step_done(
        started_at,
        'Deleted %s entities, %s relations',
        len(affected_entity_ids),
        len(affected_relation_ids),
    )

    # Load new rows from parquets filtered to affected keys
    parquet_path = combined_dir / 'entity.parquet'
    if parquet_path.exists() and affected_entity_keys:
        _load_entity_and_identifiers(
            conn,
            schema=schema,
            parquet_path=parquet_path,
            batch_size=batch_size,
            filter_keys=affected_entity_keys,
            key_column='entity_key',
        )

    if affected_relation_keys:
        _copy_parquet_to_table(
            conn,
            parquet_path=combined_dir / 'entity_relation.parquet',
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
            filter_column='relation_key',
            filter_keys=affected_relation_keys,
        )
        _copy_parquet_to_table(
            conn,
            parquet_path=combined_dir / 'entity_relation_evidence.parquet',
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
            filter_column='relation_key',
            filter_keys=affected_relation_keys,
        )

    # Entity evidence: reload for changed source, or all if no specific source
    if changed_source or not affected_entity_keys:
        _copy_parquet_to_table(
            conn,
            parquet_path=combined_dir / 'entity_evidence.parquet',
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
            filter_column='source',
            filter_keys=[changed_source] if changed_source else None,
        )

    # Rebuild relation_annotation_term for affected relations via SQL
    if affected_relation_keys:
        started_at = _log_step_start('Rebuilding relation_annotation_term')
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.relation_annotation_term (
                        relation_id, relation_evidence_id, source, scope, term_entity_id
                    )
                    SELECT DISTINCT
                        e.relation_id,
                        e.relation_evidence_id,
                        e.source,
                        'record' AS scope,
                        term.entity_id AS term_entity_id
                    FROM {}.entity_relation_evidence e
                    JOIN {}.entity term
                        ON term.canonical_identifier = (e.record_attributes->>'term_id')
                    WHERE e.relation_key = ANY(%s)
                        AND term.entity_type = 'OM:0012:Cv Term'
                    """
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                    sql.Identifier(schema),
                ),
                (affected_relation_keys,),
            )
        conn.commit()
        _log_step_done(started_at, 'relation_annotation_term rebuilt')

    # Resources are not updated incrementally; skip
    return affected_entity_ids, affected_relation_ids


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
    parquet_path: Path,
    schema: str,
    table: str,
    columns: tuple[str, ...],
    serializers: dict[str, Any],
    batch_size: int,
    filter_column: str | None = None,
    filter_keys: list[str] | None = None,
) -> None:
    if not parquet_path.exists():
        logger.info('Skipping missing artifact: %s', parquet_path)
        return

    if filter_keys is not None and not filter_keys:
        logger.info('Skipping %s: no filter keys', table)
        return

    label = 'filtered' if filter_keys is not None else ''
    logger.info('COPY %s %s -> %s.%s', label, parquet_path.name, schema, table)
    copy_sql = sql.SQL(
        "COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
    ).format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )

    total_rows = 0
    parquet_file = pq.ParquetFile(parquet_path)
    expected_rows = parquet_file.metadata.num_rows
    started_at = time.monotonic()
    next_log_at = batch_size

    with conn.cursor() as cur:
        for batch_index, batch in enumerate(
            parquet_file.iter_batches(batch_size=batch_size), start=1
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
