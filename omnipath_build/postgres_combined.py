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
            for materialized_view_name in (
                'interaction_filter_counts',
                'entity_filter_counts',
                'entity_summary',
            ):
                cur.execute(
                    sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.{} CASCADE').format(
                        sql.Identifier(schema),
                        sql.Identifier(materialized_view_name),
                    )
                )
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
                  entity_pk bigint PRIMARY KEY,
                  canonical_identifier text NOT NULL,
                  canonical_identifier_type text NOT NULL,
                  entity_type text,
                  taxonomy_id text,
                  entity_attributes jsonb,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  identifiers jsonb NOT NULL DEFAULT '[]'::jsonb
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_identifier (
                  entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  identifier text NOT NULL,
                  identifier_type text NOT NULL,
                  PRIMARY KEY (entity_pk, identifier_type, identifier)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.interaction_evidence (
                  source text NOT NULL,
                  interaction_pk bigint NOT NULL,
                  direction bigint,
                  sign bigint,
                  record_attributes jsonb,
                  entity_a_attributes jsonb,
                  entity_b_attributes jsonb,
                  evidence jsonb,
                  PRIMARY KEY (source, interaction_pk)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.interaction (
                  interaction_pk bigint PRIMARY KEY,
                  entity_a_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  entity_b_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  direction bigint,
                  sign bigint,
                  evidence_count bigint NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}'
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.association_evidence (
                  source text NOT NULL,
                  association_pk bigint NOT NULL,
                  role_term_id text,
                  stoichiometry text,
                  record_attributes jsonb,
                  parent_attributes jsonb,
                  member_attributes jsonb,
                  evidence jsonb,
                  PRIMARY KEY (source, association_pk)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.association (
                  association_pk bigint PRIMARY KEY,
                  parent_entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  member_entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  role_term_id text,
                  stoichiometry text,
                  sources text[] NOT NULL DEFAULT '{{}}'
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_annotation (
                  entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  cv_term text NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (entity_pk, cv_term)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.interaction_annotation (
                  interaction_pk bigint NOT NULL REFERENCES {}.interaction (interaction_pk),
                  cv_term text NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}',
                  PRIMARY KEY (interaction_pk, cv_term)
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
    
    # Custom load for entity and entity_identifier
    parquet_path = combined_dir / 'entity.parquet'
    if parquet_path.exists():
        logger.info('COPY entity.parquet -> %s.entity and %s.entity_identifier', schema, schema)
        parquet_file = pq.ParquetFile(parquet_path)
        
        entity_columns = ('entity_pk', 'canonical_identifier', 'canonical_identifier_type', 'entity_type', 'taxonomy_id', 'entity_attributes', 'sources', 'identifiers')
        identifier_columns = ('entity_pk', 'identifier', 'identifier_type')
        
        entity_copy = sql.SQL("COPY {}.entity ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
            sql.Identifier(schema), sql.SQL(', ').join(sql.Identifier(c) for c in entity_columns)
        )
        identifier_copy = sql.SQL("COPY {}.entity_identifier ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
            sql.Identifier(schema), sql.SQL(', ').join(sql.Identifier(c) for c in identifier_columns)
        )
        
        total_entities = 0
        total_identifiers = 0
        with conn.cursor() as cur:
            for batch in parquet_file.iter_batches(batch_size=batch_size):
                rows = batch.to_pylist()
                if not rows: continue
                
                ent_buf = io.StringIO()
                id_buf = io.StringIO()
                ent_writer = csv.writer(ent_buf, lineterminator='\n')
                id_writer = csv.writer(id_buf, lineterminator='\n')
                
                for row in rows:
                    ent_writer.writerow([
                        _serialize_copy_value(
                            row.get(c),
                            _serialize_json if c in {'entity_attributes', 'identifiers'} else _serialize_pg_text_array if c == 'sources' else None,
                        )
                        for c in entity_columns
                    ])
                    total_entities += 1
                    
                    identifiers = row.get('identifiers')
                    if identifiers:
                        for ident in identifiers:
                            id_writer.writerow([
                                row['entity_pk'],
                                _serialize_copy_value(ident.get('identifier'), None),
                                _serialize_copy_value(ident.get('identifier_type'), None),
                            ])
                            total_identifiers += 1
                
                ent_buf.seek(0)
                id_buf.seek(0)
                cur.copy_expert(entity_copy.as_string(conn), ent_buf)
                if id_buf.getvalue():
                    cur.copy_expert(identifier_copy.as_string(conn), id_buf)
        conn.commit()
        logger.info('  loaded %s entity row(s), %s identifier row(s)', total_entities, total_identifiers)

    # Standard loads for other tables
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction.parquet',
        schema=schema,
        table='interaction',
        columns=('interaction_pk', 'entity_a_pk', 'entity_b_pk', 'direction', 'sign', 'evidence_count', 'sources'),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction_evidence.parquet',
        schema=schema,
        table='interaction_evidence',
        columns=('source', 'interaction_pk', 'direction', 'sign', 'record_attributes', 'entity_a_attributes', 'entity_b_attributes', 'evidence'),
        serializers={'record_attributes': _serialize_json, 'entity_a_attributes': _serialize_json, 'entity_b_attributes': _serialize_json, 'evidence': _serialize_json},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'association.parquet',
        schema=schema,
        table='association',
        columns=('association_pk', 'parent_entity_pk', 'member_entity_pk', 'role_term_id', 'stoichiometry', 'sources'),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'association_evidence.parquet',
        schema=schema,
        table='association_evidence',
        columns=('source', 'association_pk', 'role_term_id', 'stoichiometry', 'record_attributes', 'parent_attributes', 'member_attributes', 'evidence'),
        serializers={'record_attributes': _serialize_json, 'parent_attributes': _serialize_json, 'member_attributes': _serialize_json, 'evidence': _serialize_json},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'entity_annotation.parquet',
        schema=schema,
        table='entity_annotation',
        columns=('entity_pk', 'cv_term', 'sources'),
        serializers={'sources': _serialize_pg_text_array},
        batch_size=batch_size,
    )
    _copy_parquet_to_table(
        conn,
        parquet_path=combined_dir / 'interaction_annotation.parquet',
        schema=schema,
        table='interaction_annotation',
        columns=('interaction_pk', 'cv_term', 'sources'),
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
        for materialized_view_name in (
            'interaction_filter_counts',
            'entity_filter_counts',
            'entity_summary',
        ):
            cur.execute(
                sql.SQL('DROP MATERIALIZED VIEW IF EXISTS {}.{} CASCADE').format(
                    sql.Identifier(schema),
                    sql.Identifier(materialized_view_name),
                )
            )

        cur.execute(
            sql.SQL(
                """
                CREATE MATERIALIZED VIEW {}.entity_summary AS
                WITH interaction_counts AS (
                  SELECT entity_pk, COUNT(*)::bigint AS interaction_count
                  FROM (
                    SELECT entity_a_pk AS entity_pk FROM {}.interaction
                    UNION ALL
                    SELECT entity_b_pk AS entity_pk FROM {}.interaction
                  ) endpoints
                  GROUP BY entity_pk
                ),
                identifier_counts AS (
                  SELECT entity_pk, COUNT(*)::bigint AS identifier_count
                  FROM {}.entity_identifier
                  GROUP BY entity_pk
                ),
                annotation_counts AS (
                  SELECT entity_pk, COUNT(*)::bigint AS annotation_count
                  FROM {}.entity_annotation
                  GROUP BY entity_pk
                )
                SELECT
                  e.entity_pk,
                  e.canonical_identifier,
                  e.canonical_identifier_type,
                  e.entity_type,
                  e.taxonomy_id,
                  e.sources,
                  COALESCE(ic.identifier_count, 0)::bigint AS identifier_count,
                  COALESCE(xc.interaction_count, 0)::bigint AS interaction_count,
                  COALESCE(ac.annotation_count, 0)::bigint AS annotation_count
                FROM {}.entity e
                LEFT JOIN identifier_counts ic
                  ON ic.entity_pk = e.entity_pk
                LEFT JOIN interaction_counts xc
                  ON xc.entity_pk = e.entity_pk
                LEFT JOIN annotation_counts ac
                  ON ac.entity_pk = e.entity_pk
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
            sql.SQL('CREATE UNIQUE INDEX entity_summary_pk_idx ON {}.entity_summary (entity_pk)').format(sql.Identifier(schema))
        )

        cur.execute(
            sql.SQL(
                """
                CREATE MATERIALIZED VIEW {}.entity_filter_counts AS
                WITH normalized_entities AS (
                  SELECT
                    e.entity_pk,
                    CASE
                      WHEN e.entity_type IS NULL OR btrim(e.entity_type) = '' THEN NULL
                      ELSE lower(split_part(e.entity_type, ':', 3)) || ':' || split_part(e.entity_type, ':', 1) || ':' || split_part(e.entity_type, ':', 2)
                    END AS entity_type,
                    e.sources
                  FROM {}.entity e
                ),
                entity_type_counts AS (
                  SELECT
                    'entity_type'::text AS filter_key,
                    entity_type AS filter_value,
                    COUNT(*)::bigint AS doc_count
                  FROM normalized_entities
                  WHERE entity_type IS NOT NULL
                  GROUP BY entity_type
                ),
                source_counts AS (
                  SELECT
                    'sources'::text AS filter_key,
                    source AS filter_value,
                    COUNT(DISTINCT entity_pk)::bigint AS doc_count
                  FROM normalized_entities
                  CROSS JOIN LATERAL unnest(sources) AS source
                  WHERE source IS NOT NULL AND btrim(source) <> ''
                  GROUP BY source
                )
                SELECT * FROM entity_type_counts
                UNION ALL
                SELECT * FROM source_counts
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL('CREATE UNIQUE INDEX entity_filter_counts_key_value_idx ON {}.entity_filter_counts (filter_key, filter_value)').format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL('CREATE INDEX entity_filter_counts_key_count_idx ON {}.entity_filter_counts (filter_key, doc_count DESC, filter_value)').format(sql.Identifier(schema))
        )

        cur.execute(
            sql.SQL(
                """
                CREATE MATERIALIZED VIEW {}.interaction_filter_counts AS
                WITH normalized_interactions AS (
                  SELECT
                    i.interaction_pk,
                    CASE
                      WHEN i.direction IS NOT NULL AND i.direction <> 0 THEN 'true'
                      ELSE 'false'
                    END AS is_directed,
                    COALESCE(i.sign, 0)::text AS sign,
                    CASE
                      WHEN ea.entity_type IS NULL OR btrim(ea.entity_type) = '' THEN NULL
                      ELSE lower(split_part(ea.entity_type, ':', 3)) || ':' || split_part(ea.entity_type, ':', 1) || ':' || split_part(ea.entity_type, ':', 2)
                    END AS entity_a_type,
                    CASE
                      WHEN eb.entity_type IS NULL OR btrim(eb.entity_type) = '' THEN NULL
                      ELSE lower(split_part(eb.entity_type, ':', 3)) || ':' || split_part(eb.entity_type, ':', 1) || ':' || split_part(eb.entity_type, ':', 2)
                    END AS entity_b_type
                  FROM {}.interaction i
                  JOIN {}.entity ea ON ea.entity_pk = i.entity_a_pk
                  JOIN {}.entity eb ON eb.entity_pk = i.entity_b_pk
                ),
                direction_counts AS (
                  SELECT
                    'is_directed'::text AS filter_key,
                    is_directed AS filter_value,
                    COUNT(*)::bigint AS doc_count
                  FROM normalized_interactions
                  GROUP BY is_directed
                ),
                sign_counts AS (
                  SELECT
                    'sign'::text AS filter_key,
                    sign AS filter_value,
                    COUNT(*)::bigint AS doc_count
                  FROM normalized_interactions
                  GROUP BY sign
                ),
                interaction_type_counts AS (
                  SELECT
                    'interaction_type'::text AS filter_key,
                    CASE
                      WHEN entity_a_type IS NULL OR entity_b_type IS NULL THEN NULL
                      WHEN entity_a_type <= entity_b_type THEN entity_a_type || '|' || entity_b_type
                      ELSE entity_b_type || '|' || entity_a_type
                    END AS filter_value,
                    COUNT(*)::bigint AS doc_count
                  FROM normalized_interactions
                  GROUP BY 2
                )
                SELECT * FROM direction_counts
                UNION ALL
                SELECT * FROM sign_counts
                UNION ALL
                SELECT filter_key, filter_value, doc_count
                FROM interaction_type_counts
                WHERE filter_value IS NOT NULL
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL('CREATE UNIQUE INDEX interaction_filter_counts_key_value_idx ON {}.interaction_filter_counts (filter_key, filter_value)').format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL('CREATE INDEX interaction_filter_counts_key_count_idx ON {}.interaction_filter_counts (filter_key, doc_count DESC, filter_value)').format(sql.Identifier(schema))
        )
    conn.commit()

