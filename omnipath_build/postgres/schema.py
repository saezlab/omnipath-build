from __future__ import annotations

import psycopg2.extensions
from psycopg2 import sql


def ensure_schema(
    conn: psycopg2.extensions.connection,
    schema: str,
    drop_existing: bool = False,
) -> None:
    with conn.cursor() as cur:
        cur.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
        cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

        if drop_existing:
            relkind_to_drop_kind = {
                'r': 'TABLE',
                'p': 'TABLE',
                'v': 'VIEW',
                'm': 'MATERIALIZED VIEW',
            }
            for object_name in (
                'resources',
                'relation_annotation_term',
                'entity_relation_evidence',
                'entity_relation',
                'ontology_term',
                'entity_identifier',
                'entity',
            ):
                cur.execute(
                    """
                    SELECT c.relkind
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s
                    LIMIT 1
                    """,
                    (schema, object_name),
                )
                row = cur.fetchone()
                if row is None:
                    continue
                drop_kind = relkind_to_drop_kind.get(row[0])
                if drop_kind is None:
                    continue
                cur.execute(
                    sql.SQL(f'DROP {drop_kind} {{}}.{{}} CASCADE').format(
                        sql.Identifier(schema),
                        sql.Identifier(object_name),
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
                  sources text[] NOT NULL DEFAULT '{{}}'
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_identifier (
                  id bigserial PRIMARY KEY,
                  entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  identifier text NOT NULL,
                  identifier_type text NOT NULL
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_relation (
                  relation_pk bigint PRIMARY KEY,
                  subject_entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  predicate text NOT NULL,
                  object_entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  relation_category text NOT NULL,
                  participant_types text[] NOT NULL DEFAULT '{{}}',
                  evidence_count bigint NOT NULL,
                  sources text[] NOT NULL DEFAULT '{{}}'
                )
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_relation_evidence (
                  source text NOT NULL,
                  relation_evidence_pk bigint PRIMARY KEY,
                  relation_pk bigint NOT NULL REFERENCES {}.entity_relation (relation_pk),
                  record_attributes jsonb,
                  subject_attributes jsonb,
                  object_attributes jsonb,
                  evidence jsonb
                )
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.relation_annotation_term (
                  relation_pk bigint NOT NULL REFERENCES {}.entity_relation (relation_pk),
                  relation_evidence_pk bigint NOT NULL REFERENCES {}.entity_relation_evidence (relation_evidence_pk),
                  source text NOT NULL,
                  scope text NOT NULL,
                  term_entity_pk bigint NOT NULL REFERENCES {}.entity (entity_pk),
                  PRIMARY KEY (relation_evidence_pk, scope, term_entity_pk)
                )
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.resources (
                  resource_id text PRIMARY KEY,
                  resource_name text,
                  description text,
                  homepage_url text,
                  license text,
                  pubmed_id text,
                  resource_kind text,
                  categories text[] NOT NULL DEFAULT '{{}}',
                  annotation_ontologies text[] NOT NULL DEFAULT '{{}}',
                  entity_count bigint NOT NULL DEFAULT 0,
                  interaction_count bigint NOT NULL DEFAULT 0,
                  membership_count bigint NOT NULL DEFAULT 0,
                  annotation_count bigint NOT NULL DEFAULT 0,
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
