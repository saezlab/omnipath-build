from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extensions
import pyarrow.parquet as pq
from psycopg2 import sql

logger = logging.getLogger(__name__)

ARTIFACT_NAMES = (
    'entity.parquet',
    'entity_identifiers.parquet',
    'interaction_evidence.parquet',
    'association_evidence.parquet',
    'interaction.parquet',
    'association.parquet',
    'entity_annotation.parquet',
    'interaction_annotation.parquet',
)

DEFAULT_BATCH_SIZE = 10_000


def resolve_combined_dir(output_dir: str | Path) -> Path:
    path = Path(output_dir)
    if not path.exists():
        raise FileNotFoundError(f'Combined output directory does not exist: {path}')
    if not (path / 'entity.parquet').exists():
        raise FileNotFoundError(f'Combined output directory is missing required artifact: entity.parquet at {path}')
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
        create_derived_objects(conn, schema=schema)

    logger.info('Combined PostgreSQL schema load complete')
    return 0


def ensure_schema(
    conn: psycopg2.extensions.connection,
    schema: str,
    drop_existing: bool = False,
) -> None:
    with conn.cursor() as cur:
        cur.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))

        if drop_existing:
            cur.execute(sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.entity_summary CASCADE').format(sql.Identifier(schema)))
            for table_name in (
                'interaction_annotation',
                'entity_annotation',
                'association',
                'interaction',
                'association_evidence',
                'interaction_evidence',
                'entity_identifier',
                'entity',
            ):
                cur.execute(
                    sql.SQL('DROP TABLE IF EXISTS {}.{} CASCADE').format(
                        sql.Identifier(schema),
                        sql.Identifier(table_name),
                    )
                )

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity (
                  entity_id text NOT NULL,
                  entity_id_type text NOT NULL,
                  entity_type text,
                  taxonomy_id text,
                  entity_attributes jsonb,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (entity_id_type, entity_id)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_identifier (
                  entity_id text NOT NULL,
                  entity_id_type text NOT NULL,
                  identifier text NOT NULL,
                  identifier_type text NOT NULL,
                  is_canonical boolean NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (entity_id_type, entity_id, identifier_type, identifier),
                  FOREIGN KEY (entity_id_type, entity_id)
                    REFERENCES {}.entity (entity_id_type, entity_id)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.interaction_evidence (
                  source text NOT NULL,
                  interaction_id bigint NOT NULL,
                  entity_a_id text NOT NULL,
                  entity_a_id_type text NOT NULL,
                  entity_b_id text NOT NULL,
                  entity_b_id_type text NOT NULL,
                  direction bigint,
                  sign bigint,
                  record_attributes jsonb,
                  entity_a_attributes jsonb,
                  entity_b_attributes jsonb,
                  evidence jsonb,
                  PRIMARY KEY (source, interaction_id),
                  FOREIGN KEY (entity_a_id_type, entity_a_id)
                    REFERENCES {}.entity (entity_id_type, entity_id),
                  FOREIGN KEY (entity_b_id_type, entity_b_id)
                    REFERENCES {}.entity (entity_id_type, entity_id)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.interaction (
                  interaction_id text NOT NULL,
                  entity_a_id text NOT NULL,
                  entity_a_id_type text NOT NULL,
                  entity_b_id text NOT NULL,
                  entity_b_id_type text NOT NULL,
                  direction bigint,
                  sign bigint,
                  evidence_count bigint NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (interaction_id)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.association_evidence (
                  source text NOT NULL,
                  association_id bigint NOT NULL,
                  parent_entity_id text NOT NULL,
                  parent_entity_id_type text NOT NULL,
                  member_entity_id text NOT NULL,
                  member_entity_id_type text NOT NULL,
                  role_term_id text,
                  stoichiometry text,
                  record_attributes jsonb,
                  parent_attributes jsonb,
                  member_attributes jsonb,
                  evidence jsonb,
                  PRIMARY KEY (source, association_id),
                  FOREIGN KEY (parent_entity_id_type, parent_entity_id)
                    REFERENCES {}.entity (entity_id_type, entity_id),
                  FOREIGN KEY (member_entity_id_type, member_entity_id)
                    REFERENCES {}.entity (entity_id_type, entity_id)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.association (
                  association_id text NOT NULL,
                  parent_entity_id text NOT NULL,
                  parent_entity_id_type text NOT NULL,
                  member_entity_id text NOT NULL,
                  member_entity_id_type text NOT NULL,
                  role_term_id text,
                  stoichiometry text,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (association_id)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_annotation (
                  entity_id text NOT NULL,
                  entity_id_type text NOT NULL,
                  cv_term text NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (entity_id_type, entity_id, cv_term),
                  FOREIGN KEY (entity_id_type, entity_id)
                    REFERENCES {}.entity (entity_id_type, entity_id)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.interaction_annotation (
                  interaction_id text NOT NULL,
                  cv_term text NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (interaction_id, cv_term),
                  FOREIGN KEY (interaction_id)
                    REFERENCES {}.interaction (interaction_id)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
    conn.commit()


def load_tables(
    conn: psycopg2.extensions.connection,
    schema: str,
    combined_dir: Path,
    batch_size: int,
) -> None:
    _truncate_tables(conn, schema)
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity.parquet',
        schema=schema,
        table='entity',
        columns=('entity_id', 'entity_id_type', 'entity_type', 'taxonomy_id', 'entity_attributes', 'sources'),
        serializers={'entity_attributes': _serialize_json, 'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity_identifiers.parquet',
        schema=schema,
        table='entity_identifier',
        columns=('entity_id', 'entity_id_type', 'identifier', 'identifier_type', 'is_canonical', 'sources'),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction_evidence.parquet',
        schema=schema,
        table='interaction_evidence',
        columns=(
            'source', 'interaction_id', 'entity_a_id', 'entity_a_id_type', 'entity_b_id', 'entity_b_id_type',
            'direction', 'sign', 'record_attributes', 'entity_a_attributes', 'entity_b_attributes', 'evidence',
        ),
        serializers={
            'record_attributes': _serialize_json,
            'entity_a_attributes': _serialize_json,
            'entity_b_attributes': _serialize_json,
            'evidence': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction.parquet',
        schema=schema,
        table='interaction',
        columns=(
            'interaction_id', 'entity_a_id', 'entity_a_id_type', 'entity_b_id', 'entity_b_id_type',
            'direction', 'sign', 'evidence_count', 'sources',
        ),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'association_evidence.parquet',
        schema=schema,
        table='association_evidence',
        columns=(
            'source', 'association_id', 'parent_entity_id', 'parent_entity_id_type', 'member_entity_id', 'member_entity_id_type',
            'role_term_id', 'stoichiometry', 'record_attributes', 'parent_attributes', 'member_attributes', 'evidence',
        ),
        serializers={
            'record_attributes': _serialize_json,
            'parent_attributes': _serialize_json,
            'member_attributes': _serialize_json,
            'evidence': _serialize_json,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'association.parquet',
        schema=schema,
        table='association',
        columns=(
            'association_id', 'parent_entity_id', 'parent_entity_id_type', 'member_entity_id', 'member_entity_id_type',
            'role_term_id', 'stoichiometry', 'sources',
        ),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity_annotation.parquet',
        schema=schema,
        table='entity_annotation',
        columns=('entity_id', 'entity_id_type', 'cv_term', 'sources'),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction_annotation.parquet',
        schema=schema,
        table='interaction_annotation',
        columns=('interaction_id', 'cv_term', 'sources'),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )


def _truncate_tables(conn: psycopg2.extensions.connection, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                'TRUNCATE TABLE {}.interaction_annotation, {}.entity_annotation, {}.association, {}.interaction, '
                '{}.association_evidence, {}.interaction_evidence, {}.entity_identifier, {}.entity CASCADE'
            ).format(
                sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema),
                sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema),
            )
        )
    conn.commit()


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
    parquet_file = pq.ParquetFile(parquet_path)
    total_rows = 0
    copy_sql = sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )

    with conn.cursor() as cur:
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            rows = batch.to_pylist()
            if not rows:
                continue
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator='\n')
            for row in rows:
                writer.writerow([
                    _serialize_copy_value(row.get(column), serializers.get(column))
                    for column in columns
                ])
            buffer.seek(0)
            cur.copy_expert(copy_sql.as_string(conn), buffer)
            total_rows += len(rows)
    conn.commit()
    logger.info('  loaded %s row(s)', total_rows)


def _serialize_copy_value(value: Any, serializer: Any | None) -> str:
    if value is None:
        return '\\N'
    if serializer is not None:
        serialized = serializer(value)
        return '\\N' if serialized is None else serialized
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _serialize_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(',', ':'))


def _serialize_pg_text_array(value: Any) -> str | None:
    if value is None:
        return None
    items = list(value)
    escaped = []
    for item in items:
        if item is None:
            escaped.append('NULL')
            continue
        text = str(item).replace('\\', '\\\\').replace('"', '\\"')
        escaped.append(f'"{text}"')
    return '{' + ','.join(escaped) + '}'


def create_secondary_indexes(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    statements = [
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_taxonomy_idx ON {}.entity (taxonomy_id)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_sources_gin_idx ON {}.entity USING GIN (sources)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_type_idx ON {}.entity_identifier (identifier_type)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_identifier_sources_gin_idx ON {}.entity_identifier USING GIN (sources)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS interaction_sources_gin_idx ON {}.interaction USING GIN (sources)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS association_sources_gin_idx ON {}.association USING GIN (sources)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS entity_annotation_cv_term_idx ON {}.entity_annotation (cv_term)').format(sql.Identifier(schema)),
        sql.SQL('CREATE INDEX IF NOT EXISTS interaction_annotation_cv_term_idx ON {}.interaction_annotation (cv_term)').format(sql.Identifier(schema)),
    ]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def create_derived_objects(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.entity_summary CASCADE').format(sql.Identifier(schema)))
        cur.execute(
            sql.SQL(
                """
                CREATE MATERIALIZED VIEW {}.entity_summary AS
                WITH interaction_counts AS (
                  SELECT entity_id_type, entity_id, COUNT(*)::bigint AS interaction_count
                  FROM (
                    SELECT entity_a_id_type AS entity_id_type, entity_a_id AS entity_id FROM {}.interaction
                    UNION ALL
                    SELECT entity_b_id_type AS entity_id_type, entity_b_id AS entity_id FROM {}.interaction
                  ) endpoints
                  GROUP BY entity_id_type, entity_id
                ),
                identifier_counts AS (
                  SELECT entity_id_type, entity_id, COUNT(*)::bigint AS identifier_count
                  FROM {}.entity_identifier
                  GROUP BY entity_id_type, entity_id
                ),
                annotation_counts AS (
                  SELECT entity_id_type, entity_id, COUNT(*)::bigint AS annotation_count
                  FROM {}.entity_annotation
                  GROUP BY entity_id_type, entity_id
                )
                SELECT
                  e.entity_id,
                  e.entity_id_type,
                  e.entity_type,
                  e.taxonomy_id,
                  e.sources,
                  COALESCE(ic.identifier_count, 0)::bigint AS identifier_count,
                  COALESCE(xc.interaction_count, 0)::bigint AS interaction_count,
                  COALESCE(ac.annotation_count, 0)::bigint AS annotation_count
                FROM {}.entity e
                LEFT JOIN identifier_counts ic
                  ON ic.entity_id_type = e.entity_id_type AND ic.entity_id = e.entity_id
                LEFT JOIN interaction_counts xc
                  ON xc.entity_id_type = e.entity_id_type AND xc.entity_id = e.entity_id
                LEFT JOIN annotation_counts ac
                  ON ac.entity_id_type = e.entity_id_type AND ac.entity_id = e.entity_id
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL('CREATE UNIQUE INDEX entity_summary_pk_idx ON {}.entity_summary (entity_id_type, entity_id)').format(sql.Identifier(schema))
        )
    conn.commit()
