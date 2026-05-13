from __future__ import annotations

from psycopg2 import sql
import psycopg2.extensions


def ensure_schema(
    conn: psycopg2.extensions.connection,
    schema: str,
    drop_existing: bool = False,
    unlogged_tables: bool = False,
    foreign_keys: bool = False,
) -> None:
    table_kind = 'UNLOGGED TABLE' if unlogged_tables else 'TABLE'
    entity_fk = (
        ' REFERENCES {schema}.entity (entity_id)' if foreign_keys else ''
    )
    relation_fk = (
        ' REFERENCES {schema}.entity_relation (relation_id)'
        if foreign_keys
        else ''
    )
    evidence_fk = (
        ' REFERENCES {schema}.entity_relation_evidence (relation_evidence_id)'
        if foreign_keys
        else ''
    )

    with conn.cursor() as cur:
        if drop_existing:
            cur.execute(
                sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(
                    sql.Identifier(schema)
                )
            )

        cur.execute(
            sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(
                sql.Identifier(schema)
            )
        )
        cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

        schema_sql = sql.Identifier(schema).as_string(conn)
        entity_fk = entity_fk.format(schema=schema_sql)
        relation_fk = relation_fk.format(schema=schema_sql)
        evidence_fk = evidence_fk.format(schema=schema_sql)

        cur.execute(
            sql.SQL(
                f"""
                CREATE {table_kind} IF NOT EXISTS {{}}.entity (
                  entity_id bigserial PRIMARY KEY,
                  entity_key text NOT NULL UNIQUE,
                  canonical_identifier text NOT NULL,
                  canonical_identifier_type text NOT NULL,
                  entity_type text,
                  taxonomy_id text,
                  entity_attributes jsonb,
                  sources jsonb
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                f"""
                CREATE {table_kind} IF NOT EXISTS {{}}.entity_identifier (
                  id bigserial PRIMARY KEY,
                  entity_id bigint NOT NULL{entity_fk},
                  identifier text NOT NULL,
                  identifier_type text NOT NULL
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                f"""
                CREATE {table_kind} IF NOT EXISTS {{}}.entity_relation (
                  relation_id bigserial PRIMARY KEY,
                  relation_key text NOT NULL UNIQUE,
                  subject_entity_id bigint NOT NULL{entity_fk},
                  predicate text NOT NULL,
                  object_entity_id bigint NOT NULL{entity_fk},
                  relation_category text NOT NULL,
                  participant_types jsonb,
                  evidence_count bigint NOT NULL,
                  sources jsonb
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                f"""
                CREATE {table_kind} IF NOT EXISTS {{}}.entity_relation_evidence (
                  relation_evidence_id bigserial PRIMARY KEY,
                  relation_id bigint NOT NULL{relation_fk},
                  source text NOT NULL,
                  raw_record_id text,
                  record_attributes jsonb,
                  subject_attributes jsonb,
                  object_attributes jsonb,
                  evidence jsonb
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                f"""
                CREATE {table_kind} IF NOT EXISTS {{}}.entity_evidence (
                  entity_id bigint NOT NULL{entity_fk},
                  source text NOT NULL,
                  raw_record_ids jsonb,
                  entity_type text,
                  taxonomy_id text,
                  identifiers jsonb,
                  entity_attributes jsonb,
                  evidence jsonb,
                  PRIMARY KEY (source, entity_id)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                f"""
                CREATE {table_kind} IF NOT EXISTS {{}}.relation_annotation_term (
                  relation_id bigint NOT NULL{relation_fk},
                  relation_evidence_id bigint NOT NULL{evidence_fk},
                  source text NOT NULL,
                  scope text NOT NULL,
                  term_entity_id bigint NOT NULL{entity_fk},
                  PRIMARY KEY (relation_id, source, scope, term_entity_id)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                f"""
                CREATE {table_kind} IF NOT EXISTS {{}}.resources (
                  resource_id text PRIMARY KEY,
                  resource_name text,
                  description text,
                  homepage_url text,
                  license text,
                  pubmed_id text,
                  resource_kind text,
                  categories jsonb,
                  annotation_ontologies jsonb,
                  entity_count bigint NOT NULL DEFAULT 0,
                  interaction_count bigint NOT NULL DEFAULT 0,
                  association_count bigint NOT NULL DEFAULT 0,
                  identifier_count bigint NOT NULL DEFAULT 0,
                  ontology_term_count bigint NOT NULL DEFAULT 0,
                  total_size_bytes bigint NOT NULL DEFAULT 0,
                  last_downloaded_at timestamptz,
                  last_built_at timestamptz,
                  build_status text
                )
                """
            ).format(sql.Identifier(schema))
        )
    conn.commit()
