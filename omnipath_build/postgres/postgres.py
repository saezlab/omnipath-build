from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

import polars as pl
import psycopg2
import psycopg2.extensions
import pyarrow.parquet as pq
from psycopg2 import sql

from omnipath_build.postgres.indexes import create_secondary_indexes
from omnipath_build.postgres.materialized_views import refresh_materialized_views
from omnipath_build.postgres.schema import ensure_schema

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 200_000


def resolve_combined_dir(output_dir: str | Path) -> Path:
    path = Path(output_dir)
    if not path.exists():
        raise FileNotFoundError(f'Combined output directory does not exist: {path}')
    if not (path / 'entity.parquet').exists():
        raise FileNotFoundError(
            f'Combined output directory is missing required artifact: entity.parquet at {path}'
        )
    return path


def load_combined_schema_to_postgres(
    output_dir: str | Path,
    postgres_uri: str,
    schema: str = 'public',
    drop_existing: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    combined_dir = resolve_combined_dir(output_dir)
    logger.info('Loading combined parquet artifacts from %s', combined_dir)

    with psycopg2.connect(postgres_uri) as conn:
        ensure_schema(conn, schema=schema, drop_existing=drop_existing)
        load_tables(conn, schema=schema, combined_dir=combined_dir, batch_size=batch_size)
        create_secondary_indexes(conn, schema=schema)

    logger.info('Combined PostgreSQL schema load complete')
    return 0




def load_tables(
    conn: psycopg2.extensions.connection,
    schema: str,
    combined_dir: Path,
    batch_size: int,
) -> None:
    _truncate_tables(conn, schema)

    parquet_path = combined_dir / 'entity.parquet'
    if parquet_path.exists():
        _load_entity_and_identifiers(conn, schema=schema, parquet_path=parquet_path, batch_size=batch_size)

    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity_relation.parquet',
        schema=schema,
        table='entity_relation',
        columns=(
            'relation_pk',
            'subject_entity_pk',
            'predicate',
            'object_entity_pk',
            'relation_category',
            'participant_types',
            'evidence_count',
            'sources',
        ),
        serializers={'participant_types': _serialize_pg_text_array, 'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity_relation_evidence.parquet',
        schema=schema,
        table='entity_relation_evidence',
        columns=(
            'source',
            'relation_evidence_pk',
            'relation_pk',
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
        parquet_path=combined_dir / 'ontology_term.parquet',
        schema=schema,
        table='ontology_term',
        columns=('term_id', 'ontology_prefix', 'label', 'definition', 'synonyms', 'sources'),
        serializers={'synonyms': _serialize_pg_text_array, 'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'relation_annotation_term.parquet',
        schema=schema,
        table='relation_annotation_term',
        columns=('relation_pk', 'relation_evidence_pk', 'source', 'scope', 'term_id'),
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
            'categories',
            'annotation_ontologies',
            'entity_count',
            'interaction_count',
            'membership_count',
            'annotation_count',
            'identifier_count',
            'ontology_term_count',
            'total_size_bytes',
            'last_downloaded_at',
            'last_built_at',
            'build_status',
        ),
        serializers={
            'categories': _serialize_pg_text_array,
            'annotation_ontologies': _serialize_pg_text_array,
        },
        batch_size=batch_size,
    )
    refresh_materialized_views(conn, schema=schema)




def _load_entity_and_identifiers(
    conn: psycopg2.extensions.connection,
    schema: str,
    parquet_path: Path,
    batch_size: int,
) -> None:
    logger.info('COPY entity.parquet -> %s.entity and %s.entity_identifier', schema, schema)

    entity_columns = (
        'entity_pk',
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
        sql.Identifier(schema), sql.SQL(', ').join(sql.Identifier(c) for c in entity_columns)
    )
    identifier_copy = sql.SQL(
        "COPY {}.entity_identifier (entity_pk, identifier, identifier_type) FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
    ).format(sql.Identifier(schema))

    total_entities = 0
    total_identifiers = 0
    parquet_file = pq.ParquetFile(parquet_path)

    with conn.cursor() as cur:
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            df = pl.from_arrow(batch)
            if df.is_empty():
                continue

            # Entity table
            ent_df = df.select(entity_columns)
            ent_df = _apply_serializers(ent_df, {
                'entity_attributes': _serialize_json,
                'sources': _serialize_pg_text_array,
            })

            buffer = io.StringIO()
            ent_df.write_csv(buffer, null_value='\\N', include_header=False)
            buffer.seek(0)
            cur.copy_expert(entity_copy.as_string(conn), buffer)
            total_entities += ent_df.height

            # Identifiers: canonical + exploded additional identifiers
            canon = df.select([
                pl.col('entity_pk'),
                pl.col('canonical_identifier').alias('identifier'),
                pl.col('canonical_identifier_type').alias('identifier_type'),
            ])
            exploded = (
                df.select(['entity_pk', 'identifiers'])
                .explode('identifiers')
                .with_columns([
                    pl.col('identifiers').struct.field('identifier'),
                    pl.col('identifiers').struct.field('identifier_type'),
                ])
                .drop('identifiers')
            )
            id_df = pl.concat([canon, exploded])

            buffer = io.StringIO()
            id_df.write_csv(buffer, null_value='\\N', include_header=False)
            buffer.seek(0)
            cur.copy_expert(identifier_copy.as_string(conn), buffer)
            total_identifiers += id_df.height

    conn.commit()
    logger.info(
        '  loaded %s entity row(s), %s identifier row(s)', total_entities, total_identifiers
    )


def _truncate_tables(conn: psycopg2.extensions.connection, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.ontology_term_annotation_counts').format(
                sql.Identifier(schema)
            )
        )
        cur.execute(
            sql.SQL(
                'TRUNCATE TABLE {}.resources, {}.relation_annotation_term, {}.ontology_term, {}.entity_relation_evidence, '
                '{}.entity_relation, {}.entity_identifier, {}.entity CASCADE'
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


def _apply_serializers(df: pl.DataFrame, serializers: dict[str, Any]) -> pl.DataFrame:
    for col, serializer in serializers.items():
        py_values = df[col].to_list()
        if serializer is _serialize_json:
            serialized = [json.dumps(x, separators=(',', ':')) if x is not None else None for x in py_values]
        elif serializer is _serialize_pg_text_array:
            serialized = [_serialize_pg_text_array(x) if x is not None else None for x in py_values]
        else:
            serialized = [serializer(x) if x is not None else None for x in py_values]
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
) -> None:
    if not parquet_path.exists():
        logger.info('Skipping missing artifact: %s', parquet_path)
        return

    logger.info('COPY %s -> %s.%s', parquet_path.name, schema, table)
    copy_sql = sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )

    total_rows = 0
    parquet_file = pq.ParquetFile(parquet_path)

    with conn.cursor() as cur:
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            df = pl.from_arrow(batch)
            if df.is_empty():
                continue

            df = df.select(list(columns))
            df = _apply_serializers(df, serializers)

            buffer = io.StringIO()
            df.write_csv(buffer, null_value='\\N', include_header=False)
            buffer.seek(0)
            cur.copy_expert(copy_sql.as_string(conn), buffer)
            total_rows += df.height

    conn.commit()
    logger.info('  loaded %s row(s)', total_rows)


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



