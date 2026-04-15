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
    'entity_identifier.parquet',
    'interaction_evidence.parquet',
    'association_evidence.parquet',
    'entity_annotation_evidence.parquet',
    'interaction_annotation_evidence.parquet',
)

DEFAULT_BATCH_SIZE = 10_000


def resolve_combined_dir(output_dir: str | Path) -> Path:
    """Resolve a directory containing combined warehouse parquet artifacts."""
    path = Path(output_dir)
    if not path.exists():
        raise FileNotFoundError(f'Combined output directory does not exist: {path}')

    missing = [name for name in ARTIFACT_NAMES if not (path / name).exists()]
    if missing:
        raise FileNotFoundError(
            f'Combined output directory is missing required artifacts: {missing} at {path}'
        )
    return path


def load_combined_schema_to_postgres(
    output_dir: str | Path,
    postgres_uri: str,
    schema: str = 'public',
    drop_existing: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Load combined gold parquet artifacts into the combined PostgreSQL schema."""
    combined_dir = resolve_combined_dir(output_dir)
    logger.info('Loading combined parquet artifacts from %s', combined_dir)

    with psycopg2.connect(postgres_uri) as conn:
        ensure_schema(conn, schema=schema, drop_existing=drop_existing)
        load_base_tables(
            conn,
            schema=schema,
            combined_dir=combined_dir,
            batch_size=batch_size,
        )
        create_secondary_indexes(conn, schema=schema)
        create_materialized_views(conn, schema=schema)

    logger.info('Combined PostgreSQL schema load complete')
    return 0


def ensure_schema(
    conn: psycopg2.extensions.connection,
    schema: str,
    drop_existing: bool = False,
) -> None:
    """Create base tables for the combined warehouse schema."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema))
        )

        if drop_existing:
            for object_name in (
                'mv_interaction_annotation',
                'mv_entity_summary',
                'mv_interaction',
            ):
                cur.execute(
                    sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.{} CASCADE').format(
                        sql.Identifier(schema),
                        sql.Identifier(object_name),
                    )
                )
            for table_name in (
                'interaction_annotation_evidence',
                'entity_annotation_evidence',
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
                  entity_key text GENERATED ALWAYS AS (
                    md5(coalesce(entity_id_type, '') || E'\\x1f' || coalesce(entity_id, ''))
                  ) STORED,
                  entity_type text,
                  taxonomy_id text,
                  entity_attributes jsonb,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (entity_key)
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
                  entity_key text GENERATED ALWAYS AS (
                    md5(coalesce(entity_id_type, '') || E'\\x1f' || coalesce(entity_id, ''))
                  ) STORED,
                  identifier text NOT NULL,
                  identifier_type text NOT NULL,
                  entity_identifier_key text GENERATED ALWAYS AS (
                    md5(
                      coalesce(entity_id_type, '') || E'\\x1f' ||
                      coalesce(entity_id, '') || E'\\x1f' ||
                      coalesce(identifier_type, '') || E'\\x1f' ||
                      coalesce(identifier, '')
                    )
                  ) STORED,
                  is_canonical boolean NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (entity_identifier_key),
                  FOREIGN KEY (entity_key)
                    REFERENCES {}.entity (entity_key)
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
                  entity_a_key text GENERATED ALWAYS AS (
                    md5(coalesce(entity_a_id_type, '') || E'\\x1f' || coalesce(entity_a_id, ''))
                  ) STORED,
                  entity_b_id text NOT NULL,
                  entity_b_id_type text NOT NULL,
                  entity_b_key text GENERATED ALWAYS AS (
                    md5(coalesce(entity_b_id_type, '') || E'\\x1f' || coalesce(entity_b_id, ''))
                  ) STORED,
                  direction bigint,
                  sign bigint,
                  record_attributes jsonb,
                  entity_a_attributes jsonb,
                  entity_b_attributes jsonb,
                  evidence jsonb,
                  PRIMARY KEY (source, interaction_id),
                  FOREIGN KEY (entity_a_key)
                    REFERENCES {}.entity (entity_key),
                  FOREIGN KEY (entity_b_key)
                    REFERENCES {}.entity (entity_key)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.association_evidence (
                  source text NOT NULL,
                  association_id bigint NOT NULL,
                  parent_entity_id text NOT NULL,
                  parent_entity_id_type text NOT NULL,
                  parent_entity_key text GENERATED ALWAYS AS (
                    md5(coalesce(parent_entity_id_type, '') || E'\\x1f' || coalesce(parent_entity_id, ''))
                  ) STORED,
                  member_entity_id text NOT NULL,
                  member_entity_id_type text NOT NULL,
                  member_entity_key text GENERATED ALWAYS AS (
                    md5(coalesce(member_entity_id_type, '') || E'\\x1f' || coalesce(member_entity_id, ''))
                  ) STORED,
                  role_term_id text,
                  stoichiometry text,
                  record_attributes jsonb,
                  parent_attributes jsonb,
                  member_attributes jsonb,
                  evidence jsonb,
                  PRIMARY KEY (source, association_id),
                  FOREIGN KEY (parent_entity_key)
                    REFERENCES {}.entity (entity_key),
                  FOREIGN KEY (member_entity_key)
                    REFERENCES {}.entity (entity_key)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_annotation_evidence (
                  source text NOT NULL,
                  entity_id text NOT NULL,
                  entity_id_type text NOT NULL,
                  entity_key text GENERATED ALWAYS AS (
                    md5(coalesce(entity_id_type, '') || E'\\x1f' || coalesce(entity_id, ''))
                  ) STORED,
                  cv_term text NOT NULL,
                  entity_annotation_key text GENERATED ALWAYS AS (
                    md5(
                      coalesce(source, '') || E'\\x1f' ||
                      coalesce(entity_id_type, '') || E'\\x1f' ||
                      coalesce(entity_id, '') || E'\\x1f' ||
                      coalesce(cv_term, '')
                    )
                  ) STORED,
                  PRIMARY KEY (entity_annotation_key),
                  FOREIGN KEY (entity_key)
                    REFERENCES {}.entity (entity_key)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.interaction_annotation_evidence (
                  source text NOT NULL,
                  interaction_id bigint NOT NULL,
                  cv_term text NOT NULL,
                  interaction_annotation_key text GENERATED ALWAYS AS (
                    md5(
                      coalesce(source, '') || E'\\x1f' ||
                      coalesce(interaction_id::text, '') || E'\\x1f' ||
                      coalesce(cv_term, '')
                    )
                  ) STORED,
                  PRIMARY KEY (interaction_annotation_key),
                  FOREIGN KEY (source, interaction_id)
                    REFERENCES {}.interaction_evidence (source, interaction_id)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
    conn.commit()


def load_base_tables(
    conn: psycopg2.extensions.connection,
    schema: str,
    combined_dir: Path,
    batch_size: int,
) -> None:
    """Bulk load all base tables from combined parquet artifacts via COPY."""
    _truncate_tables(conn, schema)
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity.parquet',
        schema=schema,
        table='entity',
        columns=(
            'entity_id',
            'entity_id_type',
            'entity_type',
            'taxonomy_id',
            'entity_attributes',
            'sources',
        ),
        serializers={
            'entity_attributes': _serialize_json,
            'sources': _serialize_pg_text_array,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity_identifier.parquet',
        schema=schema,
        table='entity_identifier',
        columns=(
            'entity_id',
            'entity_id_type',
            'identifier',
            'identifier_type',
            'is_canonical',
            'sources',
        ),
        serializers={
            'sources': _serialize_pg_text_array,
        },
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction_evidence.parquet',
        schema=schema,
        table='interaction_evidence',
        columns=(
            'source',
            'interaction_id',
            'entity_a_id',
            'entity_a_id_type',
            'entity_b_id',
            'entity_b_id_type',
            'direction',
            'sign',
            'record_attributes',
            'entity_a_attributes',
            'entity_b_attributes',
            'evidence',
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
        parquet_path=combined_dir / 'association_evidence.parquet',
        schema=schema,
        table='association_evidence',
        columns=(
            'source',
            'association_id',
            'parent_entity_id',
            'parent_entity_id_type',
            'member_entity_id',
            'member_entity_id_type',
            'role_term_id',
            'stoichiometry',
            'record_attributes',
            'parent_attributes',
            'member_attributes',
            'evidence',
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
        parquet_path=combined_dir / 'entity_annotation_evidence.parquet',
        schema=schema,
        table='entity_annotation_evidence',
        columns=('source', 'entity_id', 'entity_id_type', 'cv_term'),
        serializers={},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction_annotation_evidence.parquet',
        schema=schema,
        table='interaction_annotation_evidence',
        columns=('source', 'interaction_id', 'cv_term'),
        serializers={},
        batch_size=batch_size,
    )


def _truncate_tables(conn: psycopg2.extensions.connection, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                'TRUNCATE TABLE {}.interaction_annotation_evidence, '
                '{}.entity_annotation_evidence, '
                '{}.association_evidence, '
                '{}.interaction_evidence, '
                '{}.entity_identifier, '
                '{}.entity RESTART IDENTITY CASCADE'
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
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
    copy_sql = sql.SQL(
        "COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
    ).format(
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
    """Create non-PK indexes after bulk load."""
    statements = [
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS entity_taxonomy_idx ON {}.entity (taxonomy_id)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS entity_sources_gin_idx ON {}.entity USING GIN (sources)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS entity_id_type_idx ON {}.entity (entity_id_type)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS entity_identifier_type_idx ON {}.entity_identifier (identifier_type)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS entity_identifier_value_hash_idx ON {}.entity_identifier USING HASH (identifier)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS entity_identifier_sources_gin_idx ON {}.entity_identifier USING GIN (sources)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS interaction_evidence_entity_a_key_idx ON {}.interaction_evidence (entity_a_key)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS interaction_evidence_entity_b_key_idx ON {}.interaction_evidence (entity_b_key)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS association_evidence_parent_key_idx ON {}.association_evidence (parent_entity_key)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS association_evidence_member_key_idx ON {}.association_evidence (member_entity_key)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS entity_annotation_cv_term_idx ON {}.entity_annotation_evidence (cv_term)'
        ).format(sql.Identifier(schema)),
        sql.SQL(
            'CREATE INDEX IF NOT EXISTS interaction_annotation_cv_term_idx ON {}.interaction_annotation_evidence (cv_term)'
        ).format(sql.Identifier(schema)),
    ]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def create_materialized_views(
    conn: psycopg2.extensions.connection,
    schema: str,
) -> None:
    """Create and refresh derived interaction and summary materialized views."""
    normalized = _normalized_interaction_select(schema, table_alias='ie')
    interaction_identity = _interaction_identity_expr(alias='n')

    with conn.cursor() as cur:
        for view_name in (
            'mv_interaction_annotation',
            'mv_entity_summary',
            'mv_interaction',
        ):
            cur.execute(
                sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.{} CASCADE').format(
                    sql.Identifier(schema),
                    sql.Identifier(view_name),
                )
            )

        cur.execute(
            sql.SQL(
                f"""
                CREATE MATERIALIZED VIEW {{}}.mv_interaction AS
                WITH normalized AS (
                  {normalized}
                )
                SELECT
                  md5({interaction_identity}) AS interaction_id,
                  n.entity_a_id,
                  n.entity_a_id_type,
                  n.entity_b_id,
                  n.entity_b_id_type,
                  n.direction,
                  n.sign,
                  COUNT(*)::bigint AS evidence_count,
                  ARRAY_AGG(DISTINCT n.source ORDER BY n.source) AS sources
                FROM normalized n
                GROUP BY
                  n.entity_a_id,
                  n.entity_a_id_type,
                  n.entity_b_id,
                  n.entity_b_id_type,
                  n.direction,
                  n.sign
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                f"""
                CREATE MATERIALIZED VIEW {{}}.mv_entity_summary AS
                WITH interaction_counts AS (
                  SELECT entity_id_type, entity_id, COUNT(*)::bigint AS interaction_count
                  FROM (
                    SELECT entity_a_id_type AS entity_id_type, entity_a_id AS entity_id
                    FROM {{}}.interaction_evidence
                    UNION ALL
                    SELECT entity_b_id_type AS entity_id_type, entity_b_id AS entity_id
                    FROM {{}}.interaction_evidence
                  ) endpoints
                  GROUP BY entity_id_type, entity_id
                ),
                identifier_counts AS (
                  SELECT
                    entity_id_type,
                    entity_id,
                    COUNT(*)::bigint AS identifier_count
                  FROM {{}}.entity_identifier
                  GROUP BY entity_id_type, entity_id
                ),
                annotation_counts AS (
                  SELECT
                    entity_id_type,
                    entity_id,
                    COUNT(*)::bigint AS annotation_count
                  FROM {{}}.entity_annotation_evidence
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
                FROM {{}}.entity e
                LEFT JOIN identifier_counts ic
                  ON ic.entity_id_type = e.entity_id_type
                 AND ic.entity_id = e.entity_id
                LEFT JOIN interaction_counts xc
                  ON xc.entity_id_type = e.entity_id_type
                 AND xc.entity_id = e.entity_id
                LEFT JOIN annotation_counts ac
                  ON ac.entity_id_type = e.entity_id_type
                 AND ac.entity_id = e.entity_id
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
            sql.SQL(
                f"""
                CREATE MATERIALIZED VIEW {{}}.mv_interaction_annotation AS
                WITH normalized_annotations AS (
                  SELECT
                    md5({_interaction_identity_expr(alias='n')}) AS interaction_id,
                    ia.cv_term,
                    ia.source
                  FROM {{}}.interaction_annotation_evidence ia
                  JOIN (
                    {normalized}
                  ) n
                    ON n.source = ia.source
                   AND n.interaction_id = ia.interaction_id
                )
                SELECT
                  interaction_id,
                  cv_term,
                  ARRAY_AGG(DISTINCT source ORDER BY source) AS sources
                FROM normalized_annotations
                GROUP BY interaction_id, cv_term
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                'CREATE UNIQUE INDEX mv_interaction_pk_idx ON {}.mv_interaction (interaction_id)'
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                'CREATE UNIQUE INDEX mv_interaction_annotation_pk_idx '
                'ON {}.mv_interaction_annotation (interaction_id, cv_term)'
            ).format(sql.Identifier(schema))
        )
    conn.commit()
    logger.info('Created materialized views')


def _normalized_interaction_select(schema: str, table_alias: str) -> str:
    endpoint_order = (
        f"({table_alias}.entity_a_id_type < {table_alias}.entity_b_id_type OR "
        f"({table_alias}.entity_a_id_type = {table_alias}.entity_b_id_type "
        f"AND {table_alias}.entity_a_id <= {table_alias}.entity_b_id))"
    )
    return (
        f"SELECT "
        f"{table_alias}.source, "
        f"{table_alias}.interaction_id, "
        f"CASE WHEN {table_alias}.direction IS NULL AND {endpoint_order} "
        f"THEN {table_alias}.entity_a_id ELSE {table_alias}.entity_b_id END AS entity_a_id, "
        f"CASE WHEN {table_alias}.direction IS NULL AND {endpoint_order} "
        f"THEN {table_alias}.entity_a_id_type ELSE {table_alias}.entity_b_id_type END AS entity_a_id_type, "
        f"CASE WHEN {table_alias}.direction IS NULL AND {endpoint_order} "
        f"THEN {table_alias}.entity_b_id ELSE {table_alias}.entity_a_id END AS entity_b_id, "
        f"CASE WHEN {table_alias}.direction IS NULL AND {endpoint_order} "
        f"THEN {table_alias}.entity_b_id_type ELSE {table_alias}.entity_a_id_type END AS entity_b_id_type, "
        f"{table_alias}.direction, "
        f"{table_alias}.sign "
        f"FROM {schema}.interaction_evidence {table_alias}"
    )


def _interaction_identity_expr(alias: str) -> str:
    return (
        f"concat_ws('|', "
        f"{alias}.entity_a_id_type, {alias}.entity_a_id, "
        f"{alias}.entity_b_id_type, {alias}.entity_b_id, "
        f"coalesce({alias}.direction::text, ''), "
        f"coalesce({alias}.sign::text, ''))"
    )
