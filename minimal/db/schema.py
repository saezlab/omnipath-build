from __future__ import annotations

from psycopg2 import sql
import psycopg2.extensions


CONTENT_TABLES: tuple[str, ...] = (
    'annotation_term_entity_bitmap',
    'annotation_term_relation_bitmap',
    'facet_entity_bitmap',
    'facet_relation_bitmap',
    'entity_relation_counts',
    'ontology_terms',
    'relation_annotation',
    'relation_evidence_annotation',
    'entity_annotation',
    'entity_evidence_annotation',
    'relation_evidence_relation',
    'entity_evidence_resolution',
    'annotation',
    'relation',
    'relation_evidence',
    'entity_evidence_identifier',
    'entity_evidence',
    'identifier_evidence',
    'entity',
    'resources',
)


def ensure_schema(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    drop_existing: bool = False,
    progress: bool = False,
    indexes: bool = True,
) -> None:
    """Create or refresh the minimal evidence and resolution schema."""

    def log_step(message: str) -> None:
        if progress:
            print(f'[schema] {message}', flush=True)

    started = None
    if progress:
        import time

        started = time.perf_counter()
        log_step(
            f'ensure start schema={schema} drop_existing={drop_existing} '
            f'indexes={indexes}'
        )

    with conn.cursor() as cur:
        if drop_existing:
            log_step('drop existing schema')
            cur.execute(
                sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(
                    sql.Identifier(schema)
                )
            )

        log_step('create schema')
        cur.execute(
            sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(
                sql.Identifier(schema)
            )
        )

        log_step('create identifier type table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.identifier_type (
                  identifier_type_id bigint PRIMARY KEY,
                  name text NOT NULL UNIQUE
                )
                """
            ).format(sql.Identifier(schema))
        )
        _ensure_static_identifier_types(cur, schema)
        log_step('create identifier evidence table')
        cur.execute(
            sql.SQL(
                """
                DO $$
                BEGIN
                  IF to_regclass({old_table}) IS NOT NULL
                     AND to_regclass({new_table}) IS NULL THEN
                    ALTER TABLE {old_table_sql} RENAME TO identifier_evidence;
                  END IF;
                END
                $$;
                """
            ).format(
                old_table=sql.Literal(f'{schema}.identifier'),
                new_table=sql.Literal(f'{schema}.identifier_evidence'),
                old_table_sql=sql.SQL('{}.identifier').format(
                    sql.Identifier(schema)
                ),
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.identifier_evidence (
                  identifier_id bigserial PRIMARY KEY,
                  identifier_type_id bigint NOT NULL
                    REFERENCES {}.identifier_type(identifier_type_id),
                  value text NOT NULL
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        log_step('ensure identifier evidence shape')
        _ensure_identifier_evidence_key(cur, schema)
        log_step('create entity_evidence table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_evidence (
                  entity_evidence_id bigserial PRIMARY KEY,
                  source text NOT NULL,
                  dataset text NOT NULL,
                  row_id bigint NOT NULL,
                  snapshot_id text,
                  occurrence_id text NOT NULL,
                  parent_entity_evidence_id bigint
                    REFERENCES {}.entity_evidence(entity_evidence_id),
                  entity_role text NOT NULL,
                  entity_type text,
                  taxonomy_id text,
                  UNIQUE (source, dataset, row_id, occurrence_id)
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        log_step('create entity_evidence_identifier table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_evidence_identifier (
                  entity_evidence_id bigint NOT NULL
                    REFERENCES {}.entity_evidence(entity_evidence_id),
                  identifier_id bigint NOT NULL
                    REFERENCES {}.identifier_evidence(identifier_id),
                  PRIMARY KEY (entity_evidence_id, identifier_id)
                )
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        log_step('create relation_evidence table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.relation_evidence (
                  relation_evidence_id bigserial PRIMARY KEY,
                  source text NOT NULL,
                  dataset text NOT NULL,
                  row_id bigint NOT NULL,
                  snapshot_id text,
                  relation_occurrence_id text NOT NULL,
                  subject_entity_evidence_id bigint
                    REFERENCES {}.entity_evidence(entity_evidence_id),
                  subject_entity_id bigint,
                  predicate text NOT NULL,
                  object_entity_evidence_id bigint
                    REFERENCES {}.entity_evidence(entity_evidence_id),
                  object_entity_id bigint,
                  relation_category text NOT NULL,
                  UNIQUE (source, dataset, row_id, relation_occurrence_id),
                  CHECK (
                    (subject_entity_evidence_id IS NOT NULL)::int
                    + (subject_entity_id IS NOT NULL)::int
                    = 1
                  ),
                  CHECK (
                    (object_entity_evidence_id IS NOT NULL)::int
                    + (object_entity_id IS NOT NULL)::int
                    = 1
                  )
                )
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        log_step('create annotation table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.annotation (
                  annotation_key uuid PRIMARY KEY,
                  term text NOT NULL,
                  value text,
                  unit text
                )
                """
            ).format(sql.Identifier(schema))
        )
        log_step('ensure annotation value schema')
        _ensure_annotation_value_schema(cur, schema)
        log_step('create evidence annotation tables')
        _ensure_evidence_annotation_tables(cur, schema)
        _ensure_resolution_schema(cur, schema, progress=progress, indexes=indexes)

    log_step('commit')
    conn.commit()
    if started is not None:
        import time

        log_step(f'ensure done elapsed={time.perf_counter() - started:.1f}s')


def ensure_deferred_indexes(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> None:
    """Create indexes that can be deferred during a scratch content load."""

    def log_step(message: str) -> None:
        if progress:
            print(f'[schema] {message}', flush=True)

    log_step('ensure deferred indexes')
    with conn.cursor() as cur:
        _ensure_resolution_indexes(cur, schema)
    conn.commit()


def reset_content_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> list[str]:
    """Truncate minimal content tables without touching resolver tables."""

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
              AND table_name = ANY(%s)
            """,
            [schema, list(CONTENT_TABLES)],
        )
        existing_tables = {row[0] for row in cur.fetchall()}
        tables_to_truncate = [
            table for table in CONTENT_TABLES if table in existing_tables
        ]
        if tables_to_truncate:
            cur.execute(
                sql.SQL('TRUNCATE {} RESTART IDENTITY').format(
                    sql.SQL(', ').join(
                        sql.SQL('{}.{}').format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        )
                        for table in tables_to_truncate
                    )
                )
            )
    conn.commit()
    return tables_to_truncate


def _ensure_resolution_schema(
    cur: psycopg2.extensions.cursor,
    schema: str,
    *,
    progress: bool = False,
    indexes: bool = True,
) -> None:
    def log_step(message: str) -> None:
        if progress:
            print(f'[schema] {message}', flush=True)

    schema_id = sql.Identifier(schema)
    log_step('drop obsolete canonicalization tables')
    _drop_obsolete_canonicalization_tables(cur, schema)
    log_step('create identifier type table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.identifier_type (
              identifier_type_id bigint PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    log_step('create entity type table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_type (
              entity_type_id bigserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    log_step('create resolution status table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolution_status (
              resolution_status_id smallint PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    _ensure_static_resolution_statuses(cur, schema)
    log_step('create resolution reason table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolution_reason (
              resolution_reason_id smallint PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    _ensure_static_resolution_reasons(cur, schema)
    log_step('create entity table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity (
              entity_id bigserial PRIMARY KEY,
              entity_type_id bigint NOT NULL
                REFERENCES {}.entity_type(entity_type_id),
              taxonomy_id text,
              canonical_identifier_type_id bigint
                REFERENCES {}.identifier_type(identifier_type_id),
              canonical_identifier text NOT NULL,
              identifiers jsonb NOT NULL DEFAULT '[]'::jsonb,
              resolution_status_id smallint NOT NULL
                REFERENCES {}.resolution_status(resolution_status_id),
              created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )
    log_step('ensure identifier types')
    _ensure_static_identifier_types(cur, schema)
    log_step('ensure entity indexes')
    _ensure_entity_canonical_key(cur, schema)
    log_step('ensure annotation value schema')
    _ensure_annotation_value_schema(cur, schema)
    log_step('create evidence annotation tables')
    _ensure_evidence_annotation_tables(cur, schema)
    log_step('ensure relation evidence endpoints')
    _ensure_relation_evidence_entity_endpoints(cur, schema)
    log_step('drop obsolete entity_resolution_candidate table')
    cur.execute(
        sql.SQL('DROP TABLE IF EXISTS {}.entity_resolution_candidate CASCADE').format(
            schema_id
        )
    )
    log_step('create entity_evidence_resolution table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_evidence_resolution (
              entity_evidence_id bigint PRIMARY KEY
                REFERENCES {}.entity_evidence(entity_evidence_id)
                ON DELETE CASCADE,
              status_id smallint NOT NULL
                REFERENCES {}.resolution_status(resolution_status_id),
              entity_id bigint
                REFERENCES {}.entity(entity_id),
              reason_id smallint
                REFERENCES {}.resolution_reason(resolution_reason_id),
              resolved_at timestamptz NOT NULL DEFAULT now(),
              CHECK (
                (
                  status_id IN (1, 2, 3)
                  AND entity_id IS NOT NULL
                )
                OR
                (
                  status_id = 4
                  AND entity_id IS NULL
                )
              )
            )
            """
        ).format(schema_id, schema_id, schema_id, schema_id, schema_id)
    )
    log_step('ensure entity resolution reason')
    _ensure_entity_resolution_reason(cur, schema)
    log_step('ensure entity resolution check')
    _ensure_entity_resolution_entity_check(cur, schema)
    log_step('create resolver_mapping_policy table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolver_mapping_policy (
              entity_family text NOT NULL,
              resolver_source text,
              key_type text NOT NULL,
              mapping_type text,
              action text NOT NULL CHECK (
                action IN ('accept', 'candidate_only', 'ignore')
              ),
              requires_taxonomy boolean NOT NULL DEFAULT false
            )
            """
        ).format(schema_id)
    )
    log_step('create identifier type table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.identifier_type (
              identifier_type_id bigint PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    log_step('create resolver protein lookup table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolver_protein_identifier_lookup (
              key_identifier_type_id bigint NOT NULL
                REFERENCES {}.identifier_type(identifier_type_id),
              key_value text NOT NULL,
              taxonomy_id text,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.identifier_type(identifier_type_id),
              canonical_identifier text NOT NULL
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    log_step('create ambiguous resolver protein lookup table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolver_protein_identifier_lookup_ambiguous (
              key_identifier_type_id bigint NOT NULL
                REFERENCES {}.identifier_type(identifier_type_id),
              key_value text NOT NULL,
              taxonomy_id text,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.identifier_type(identifier_type_id),
              canonical_identifier text NOT NULL
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    log_step('create resolver chemical lookup table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolver_chemical_identifier_lookup (
              key_identifier_type_id bigint NOT NULL
                REFERENCES {}.identifier_type(identifier_type_id),
              key_value text NOT NULL,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.identifier_type(identifier_type_id),
              canonical_identifier text NOT NULL
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    log_step('create resources table')
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
        ).format(schema_id)
    )
    log_step('create resolver policy index')
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS resolver_mapping_policy_unique_idx
            ON {}.resolver_mapping_policy (
              entity_family,
              key_type,
              COALESCE(mapping_type, ''),
              COALESCE(resolver_source, '')
            )
            """
        ).format(schema_id)
    )
    log_step('create relation table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation (
              relation_id bigserial PRIMARY KEY,
              subject_entity_id bigint NOT NULL
                REFERENCES {}.entity(entity_id),
              predicate text NOT NULL,
              object_entity_id bigint NOT NULL
                REFERENCES {}.entity(entity_id),
              relation_category text,
              created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    log_step('create relation unique index')
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS relation_unique_idx
            ON {}.relation (
              subject_entity_id,
              predicate,
              object_entity_id,
              relation_category
            )
            NULLS NOT DISTINCT
            """
        ).format(schema_id)
    )
    log_step('create relation_evidence_relation table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_evidence_relation (
              relation_id bigint NOT NULL
                REFERENCES {}.relation(relation_id)
                ON DELETE CASCADE,
              relation_evidence_id bigint NOT NULL
                REFERENCES {}.relation_evidence(relation_evidence_id)
                ON DELETE CASCADE,
              PRIMARY KEY (relation_id, relation_evidence_id),
              UNIQUE (relation_evidence_id)
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    log_step('create canonical annotation tables')
    _ensure_canonical_annotation_tables(cur, schema)
    log_step('drop relation_evidence_resolution table')
    cur.execute(
        sql.SQL('DROP TABLE IF EXISTS {}.relation_evidence_resolution').format(
            schema_id
        )
    )
    log_step('drop relation_annotation_evidence table')
    cur.execute(
        sql.SQL('DROP TABLE IF EXISTS {}.relation_annotation_evidence').format(
            schema_id
        )
    )
    if indexes:
        log_step('ensure resolution indexes')
        _ensure_resolution_indexes(cur, schema)
    else:
        log_step('defer resolution indexes')


def _ensure_identifier_evidence_key(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.identifier_evidence
            ADD COLUMN IF NOT EXISTS identifier_type_id bigint
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = %s
            AND table_name = 'identifier_evidence'
            AND column_name = 'type'
        )
        """,
        [schema],
    )
    if bool(cur.fetchone()[0]):
        cur.execute(
            sql.SQL(
                """
                WITH missing AS (
                  SELECT DISTINCT i.type AS name
                  FROM {}.identifier_evidence i
                  LEFT JOIN {}.identifier_type it
                    ON it.name = i.type
                  WHERE i.type IS NOT NULL
                    AND it.identifier_type_id IS NULL
                ),
                base AS (
                  SELECT COALESCE(MAX(identifier_type_id), 0) AS max_id
                  FROM {}.identifier_type
                )
                INSERT INTO {}.identifier_type (identifier_type_id, name)
                SELECT
                  base.max_id + row_number() OVER (ORDER BY missing.name),
                  missing.name
                FROM missing
                CROSS JOIN base
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema_id, schema_id, schema_id, schema_id)
        )
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.identifier_evidence i
                SET identifier_type_id = it.identifier_type_id
                FROM {}.identifier_type it
                WHERE i.identifier_type_id IS NULL
                  AND it.name = i.type
                """
            ).format(schema_id, schema_id)
        )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.identifier_evidence
            ALTER COLUMN identifier_type_id SET NOT NULL
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'identifier_evidence_type_id_fkey'
                  AND conrelid = {table_literal}::regclass
              ) THEN
                ALTER TABLE {table_sql}
                ADD CONSTRAINT identifier_evidence_type_id_fkey
                FOREIGN KEY (identifier_type_id)
                REFERENCES {type_table}(identifier_type_id);
              END IF;
            END
            $$;
            """
        ).format(
            table_literal=sql.Literal(f'{schema}.identifier_evidence'),
            table_sql=sql.SQL('{}.identifier_evidence').format(schema_id),
            type_table=sql.SQL('{}.identifier_type').format(schema_id),
        )
    )
    cur.execute(
        """
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = %s
          AND rel.relname = 'identifier_evidence'
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) IN (
            'UNIQUE (type, value)',
            'UNIQUE (type, value_hash)'
          )
        """,
        [schema],
    )
    for (constraint_name,) in cur.fetchall():
        cur.execute(
            sql.SQL(
                'ALTER TABLE {}.identifier_evidence DROP CONSTRAINT {}'
            ).format(
                schema_id,
                sql.Identifier(constraint_name),
            )
        )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.identifier_type_value_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL(
            'DROP INDEX IF EXISTS {}.identifier_type_value_hash_idx'
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            'DROP INDEX IF EXISTS {}.identifier_evidence_type_value_hash_idx'
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS identifier_evidence_type_value_idx
            ON {}.identifier_evidence (identifier_type_id, value)
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.identifier_evidence
            DROP COLUMN IF EXISTS value_hash,
            DROP COLUMN IF EXISTS type
            """
        ).format(schema_id)
    )


def _annotation_legacy_columns(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'annotation'
        """,
        [schema],
    )
    return {row[0] for row in cur.fetchall()}


def _ensure_annotation_value_schema(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    legacy_columns = _annotation_legacy_columns(cur, schema)
    if legacy_columns and (
        'annotation_key' not in legacy_columns
        or legacy_columns
        & {'scope', 'entity_evidence_id', 'relation_evidence_id', 'entity_id'}
    ):
        for table in (
            'relation_annotation',
            'entity_annotation',
            'relation_evidence_annotation',
            'entity_evidence_annotation',
        ):
            cur.execute(
                sql.SQL('DROP TABLE IF EXISTS {}.{} CASCADE').format(
                    schema_id,
                    sql.Identifier(table),
                )
            )
        cur.execute(sql.SQL('DROP TABLE {}.annotation CASCADE').format(schema_id))
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.annotation (
              annotation_key uuid PRIMARY KEY,
              term text NOT NULL,
              value text,
              unit text
            )
            """
        ).format(schema_id)
    )
    _drop_obsolete_annotation_indexes(cur, schema)
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS annotation_value_idx
            ON {}.annotation (term, value, unit)
            NULLS NOT DISTINCT
            """
        ).format(schema_id)
    )


def _ensure_evidence_annotation_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'relation_evidence_annotation'
          AND column_name = 'relation_id'
        """,
        [schema],
    )
    if cur.fetchone() is not None:
        cur.execute(
            sql.SQL(
                'DROP TABLE IF EXISTS {}.relation_evidence_annotation CASCADE'
            ).format(schema_id)
        )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_evidence_annotation (
              entity_evidence_id bigint NOT NULL
                REFERENCES {}.entity_evidence(entity_evidence_id)
                ON DELETE CASCADE,
              annotation_key uuid NOT NULL
                REFERENCES {}.annotation(annotation_key)
                ON DELETE CASCADE,
              scope text NOT NULL,
              PRIMARY KEY (entity_evidence_id, annotation_key, scope)
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_evidence_annotation (
              relation_evidence_id bigint NOT NULL
                REFERENCES {}.relation_evidence(relation_evidence_id)
                ON DELETE CASCADE,
              annotation_key uuid NOT NULL
                REFERENCES {}.annotation(annotation_key)
                ON DELETE CASCADE,
              scope text NOT NULL,
              PRIMARY KEY (relation_evidence_id, annotation_key, scope)
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )


def _ensure_canonical_annotation_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_annotation (
              entity_id bigint NOT NULL
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              annotation_key uuid NOT NULL
                REFERENCES {}.annotation(annotation_key)
                ON DELETE CASCADE,
              scope text NOT NULL,
              PRIMARY KEY (entity_id, annotation_key, scope)
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_annotation (
              relation_id bigint NOT NULL
                REFERENCES {}.relation(relation_id)
                ON DELETE CASCADE,
              annotation_key uuid NOT NULL
                REFERENCES {}.annotation(annotation_key)
                ON DELETE CASCADE,
              scope text NOT NULL,
              PRIMARY KEY (relation_id, annotation_key, scope)
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )


def _ensure_static_identifier_types(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    from minimal.resolver.identifier_types import identifier_type_rows

    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.identifier_type (identifier_type_id, name)
            VALUES (%s, %s)
            ON CONFLICT (identifier_type_id) DO UPDATE
            SET name = EXCLUDED.name
            """
        )
        .format(sql.Identifier(schema))
        .as_string(cur.connection),
        [
            (row['identifier_type_id'], row['name'])
            for row in identifier_type_rows()
        ],
    )


def _ensure_static_resolution_statuses(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    rows = (
        (1, 'resolved'),
        (2, 'unresolved'),
        (3, 'ambiguous'),
        (4, 'unsupported'),
    )
    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.resolution_status (resolution_status_id, name)
            VALUES (%s, %s)
            ON CONFLICT (resolution_status_id) DO UPDATE
            SET name = EXCLUDED.name
            """
        )
        .format(sql.Identifier(schema))
        .as_string(cur.connection),
        rows,
    )


def _ensure_static_resolution_reasons(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    rows = (
        (1, 'missing_entity_type'),
        (2, 'different_taxon'),
        (3, 'no_accepted_resolver_candidate'),
        (4, 'multiple_entity_candidates'),
        (5, 'legacy_unresolved'),
    )
    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.resolution_reason (resolution_reason_id, name)
            VALUES (%s, %s)
            ON CONFLICT (resolution_reason_id) DO UPDATE
            SET name = EXCLUDED.name
            """
        )
        .format(sql.Identifier(schema))
        .as_string(cur.connection),
        rows,
    )


def _ensure_entity_resolution_reason(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity_evidence_resolution
            ADD COLUMN IF NOT EXISTS reason_id smallint
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = %s
            AND table_name = 'entity_evidence_resolution'
            AND column_name = 'reason'
        )
        """,
        [schema],
    )
    if bool(cur.fetchone()[0]):
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.entity_evidence_resolution er
                SET reason_id = rr.resolution_reason_id
                FROM {}.resolution_reason rr
                WHERE er.reason_id IS NULL
                  AND rr.name = er.reason
                """
            ).format(schema_id, schema_id)
        )
    cur.execute(
        sql.SQL(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'entity_evidence_resolution_reason_id_fkey'
                  AND conrelid = {table_literal}::regclass
              ) THEN
                ALTER TABLE {table_sql}
                ADD CONSTRAINT entity_evidence_resolution_reason_id_fkey
                FOREIGN KEY (reason_id)
                REFERENCES {reason_table}(resolution_reason_id);
              END IF;
            END
            $$;
            """
        ).format(
            table_literal=sql.Literal(f'{schema}.entity_evidence_resolution'),
            table_sql=sql.SQL('{}.entity_evidence_resolution').format(
                schema_id
            ),
            reason_table=sql.SQL('{}.resolution_reason').format(schema_id),
        )
    )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity_evidence_resolution
            DROP COLUMN IF EXISTS candidate_count,
            DROP COLUMN IF EXISTS reason
            """
        ).format(schema_id)
    )


def _ensure_entity_canonical_key(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity
            ADD COLUMN IF NOT EXISTS entity_type_id bigint,
            ADD COLUMN IF NOT EXISTS resolution_status_id smallint,
            ADD COLUMN IF NOT EXISTS canonical_identifier_type_id bigint,
            ADD COLUMN IF NOT EXISTS canonical_identifier text,
            ADD COLUMN IF NOT EXISTS identifiers jsonb NOT NULL DEFAULT '[]'::jsonb
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'entity'
          AND column_name IN ('entity_type', 'resolution_status')
        """,
        [schema],
    )
    old_entity_columns = {row[0] for row in cur.fetchall()}
    if 'entity_type' in old_entity_columns:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.entity_type (name)
                SELECT DISTINCT entity_type
                FROM {}.entity
                WHERE entity_type IS NOT NULL
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema_id, schema_id)
        )
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.entity e
                SET entity_type_id = et.entity_type_id
                FROM {}.entity_type et
                WHERE et.name = e.entity_type
                  AND e.entity_type_id IS NULL
                """
            ).format(schema_id, schema_id)
        )
    if 'resolution_status' in old_entity_columns:
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.entity e
                SET resolution_status_id = rs.resolution_status_id
                FROM {}.resolution_status rs
                WHERE rs.name = e.resolution_status
                  AND e.resolution_status_id IS NULL
                """
            ).format(schema_id, schema_id)
        )
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'entity'
          AND column_name IN ('id', 'id_type')
        """,
        [schema],
    )
    old_columns = {row[0] for row in cur.fetchall()}
    if {'id', 'id_type'} <= old_columns:
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.entity e
                SET
                  canonical_identifier_type_id = it.identifier_type_id,
                  canonical_identifier = e.id,
                  identifiers = CASE
                    WHEN COALESCE(rs.name, e.resolution_status) = 'resolved'
                    THEN jsonb_build_array(
                      jsonb_build_object(
                        'identifier_type', e.id_type,
                        'identifier_type_id', it.identifier_type_id,
                        'identifier', e.id
                      )
                    )
                    ELSE jsonb_build_object(
                      'reason', 'legacy_unresolved',
                      'evidence_identifier_set', e.id
                    )
                  END
                FROM {}.identifier_type it
                LEFT JOIN {}.resolution_status rs
                  ON rs.resolution_status_id = e.resolution_status_id
                WHERE e.canonical_identifier IS NULL
                  AND e.id IS NOT NULL
                  AND it.name = e.id_type
                """
            ).format(schema_id, schema_id, schema_id)
        )
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.entity e
                SET
                  canonical_identifier = e.id,
                  identifiers = jsonb_build_object(
                    'reason', 'legacy_unresolved',
                    'evidence_identifier_set', e.id
                  )
                WHERE e.canonical_identifier IS NULL
                  AND e.id IS NOT NULL
                """
            ).format(schema_id)
        )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity
            ALTER COLUMN entity_type_id SET NOT NULL,
            ALTER COLUMN resolution_status_id SET NOT NULL,
            ALTER COLUMN canonical_identifier SET NOT NULL,
            ALTER COLUMN identifiers SET NOT NULL
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'entity_canonical_identifier_type_id_fkey'
                  AND conrelid = {entity_table_literal}::regclass
              ) THEN
                ALTER TABLE {entity_table_sql}
                ADD CONSTRAINT entity_canonical_identifier_type_id_fkey
                FOREIGN KEY (canonical_identifier_type_id)
                REFERENCES {identifier_type_table}(identifier_type_id);
              END IF;
            END
            $$;
            """
        ).format(
            entity_table_literal=sql.Literal(f'{schema}.entity'),
            entity_table_sql=sql.SQL('{}.entity').format(schema_id),
            identifier_type_table=sql.SQL('{}.identifier_type').format(
                schema_id
            ),
        )
    )
    for column, target_table, target_column, constraint_name in (
        (
            'entity_type_id',
            'entity_type',
            'entity_type_id',
            'entity_entity_type_id_fkey',
        ),
        (
            'resolution_status_id',
            'resolution_status',
            'resolution_status_id',
            'entity_resolution_status_id_fkey',
        ),
    ):
        cur.execute(
            sql.SQL(
                """
                DO $$
                BEGIN
                  IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = {constraint_name}
                      AND conrelid = {entity_table_literal}::regclass
                  ) THEN
                    ALTER TABLE {entity_table_sql}
                    ADD CONSTRAINT {constraint_identifier}
                    FOREIGN KEY ({column_identifier})
                    REFERENCES {target_table_sql}({target_column_identifier});
                  END IF;
                END
                $$;
                """
            ).format(
                constraint_name=sql.Literal(constraint_name),
                entity_table_literal=sql.Literal(f'{schema}.entity'),
                entity_table_sql=sql.SQL('{}.entity').format(schema_id),
                constraint_identifier=sql.Identifier(constraint_name),
                column_identifier=sql.Identifier(column),
                target_table_sql=sql.SQL('{}.{}').format(
                    schema_id,
                    sql.Identifier(target_table),
                ),
                target_column_identifier=sql.Identifier(target_column),
            )
        )
    cur.execute(
        """
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = %s
          AND rel.relname = 'entity'
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) = 'UNIQUE (entity_type, id_type, id)'
        """,
        [schema],
    )
    for (constraint_name,) in cur.fetchall():
        cur.execute(
            sql.SQL('ALTER TABLE {}.entity DROP CONSTRAINT {}').format(
                schema_id,
                sql.Identifier(constraint_name),
            )
        )
    cur.execute(sql.SQL('DROP INDEX IF EXISTS {}.entity_id_idx').format(schema_id))
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_type_id_hash_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_id_hash_lookup_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_type_taxonomy_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_status_idx').format(schema_id)
    )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_canonical_key_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS entity_canonical_key_idx
            ON {}.entity (
              entity_type_id,
              taxonomy_id,
              canonical_identifier_type_id,
              canonical_identifier
            )
            NULLS NOT DISTINCT
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity
            DROP COLUMN IF EXISTS id_hash,
            DROP COLUMN IF EXISTS canonical_identifier_hash,
            DROP COLUMN IF EXISTS id_type,
            DROP COLUMN IF EXISTS id,
            DROP COLUMN IF EXISTS entity_type,
            DROP COLUMN IF EXISTS resolution_status
            """
        ).format(schema_id)
    )


def _drop_obsolete_annotation_indexes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.annotation_term_value_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.annotation_dedupe_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL('ALTER TABLE {}.annotation DROP COLUMN IF EXISTS value_hash').format(
            schema_id
        )
    )


def _ensure_relation_evidence_entity_endpoints(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.relation_evidence
            ALTER COLUMN subject_entity_evidence_id DROP NOT NULL,
            ALTER COLUMN object_entity_evidence_id DROP NOT NULL,
            ADD COLUMN IF NOT EXISTS subject_entity_id bigint,
            ADD COLUMN IF NOT EXISTS object_entity_id bigint
            """
        ).format(schema_id)
    )
    for column in ('subject_entity_id', 'object_entity_id'):
        constraint_name = f'relation_evidence_{column}_fkey'
        cur.execute(
            sql.SQL(
                """
                DO $$
                BEGIN
                  IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = {constraint_name}
                      AND conrelid = {relation_table_literal}::regclass
                  ) THEN
                    ALTER TABLE {relation_table_sql}
                    ADD CONSTRAINT {constraint_identifier}
                    FOREIGN KEY ({column_identifier})
                    REFERENCES {entity_table}(entity_id)
                    ON DELETE CASCADE;
                  END IF;
                END
                $$;
                """
            ).format(
                constraint_name=sql.Literal(constraint_name),
                relation_table_literal=sql.Literal(
                    f'{schema}.relation_evidence'
                ),
                relation_table_sql=sql.SQL('{}.relation_evidence').format(
                    schema_id
                ),
                constraint_identifier=sql.Identifier(constraint_name),
                column_identifier=sql.Identifier(column),
                entity_table=sql.SQL('{}.entity').format(schema_id),
            )
        )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.relation_evidence
            DROP CONSTRAINT IF EXISTS relation_evidence_subject_endpoint_check,
            DROP CONSTRAINT IF EXISTS relation_evidence_object_endpoint_check,
            ADD CONSTRAINT relation_evidence_subject_endpoint_check
              CHECK (
                (subject_entity_evidence_id IS NOT NULL)::int
                + (subject_entity_id IS NOT NULL)::int
                = 1
              ),
            ADD CONSTRAINT relation_evidence_object_endpoint_check
              CHECK (
                (object_entity_evidence_id IS NOT NULL)::int
                + (object_entity_id IS NOT NULL)::int
                = 1
              )
            """
        ).format(schema_id)
    )


def _ensure_entity_resolution_entity_check(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity_evidence_resolution
            ADD COLUMN IF NOT EXISTS status_id smallint
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = %s
            AND table_name = 'entity_evidence_resolution'
            AND column_name = 'status'
        )
        """,
        [schema],
    )
    if bool(cur.fetchone()[0]):
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.entity_evidence_resolution er
                SET status_id = rs.resolution_status_id
                FROM {}.resolution_status rs
                WHERE er.status_id IS NULL
                  AND rs.name = er.status
                """
            ).format(schema_id, schema_id)
        )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity_evidence_resolution
            ALTER COLUMN status_id SET NOT NULL
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'entity_evidence_resolution_status_id_fkey'
                  AND conrelid = {table_literal}::regclass
              ) THEN
                ALTER TABLE {table_sql}
                ADD CONSTRAINT entity_evidence_resolution_status_id_fkey
                FOREIGN KEY (status_id)
                REFERENCES {status_table}(resolution_status_id);
              END IF;
            END
            $$;
            """
        ).format(
            table_literal=sql.Literal(f'{schema}.entity_evidence_resolution'),
            table_sql=sql.SQL('{}.entity_evidence_resolution').format(
                schema_id
            ),
            status_table=sql.SQL('{}.resolution_status').format(schema_id),
        )
    )
    cur.execute(
        """
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = %s
          AND rel.relname = 'entity_evidence_resolution'
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) LIKE 'CHECK %%entity_id%%'
          AND (
            pg_get_constraintdef(c.oid) LIKE '%%status%%'
            OR pg_get_constraintdef(c.oid) LIKE '%%status_id%%'
          )
        """,
        [schema],
    )
    for (constraint_name,) in cur.fetchall():
        cur.execute(
            sql.SQL(
                'ALTER TABLE {}.entity_evidence_resolution DROP CONSTRAINT {}'
            ).format(schema_id, sql.Identifier(constraint_name))
        )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity_evidence_resolution
            ADD CONSTRAINT entity_evidence_resolution_entity_check
            CHECK (
              (
                status_id IN (1, 2, 3)
                AND entity_id IS NOT NULL
              )
              OR
              (
                status_id = 4
                AND entity_id IS NULL
              )
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity_evidence_resolution
            DROP COLUMN IF EXISTS status
            """
        ).format(schema_id)
    )


def _drop_obsolete_canonicalization_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.tables
          WHERE table_schema = %s
            AND table_name = 'canonical_entity'
        )
        """,
        [schema],
    )
    has_old_canonical_tables = bool(cur.fetchone()[0])
    if not has_old_canonical_tables:
        return

    schema_id = sql.Identifier(schema)
    for table in (
        'canonical_relation_evidence',
        'canonical_relation',
        'entity_evidence_resolution',
        'canonical_entity',
    ):
        cur.execute(
            sql.SQL('DROP TABLE IF EXISTS {}.{} CASCADE').format(
                schema_id,
                sql.Identifier(table),
            )
        )


def _ensure_resolution_indexes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            'DROP INDEX IF EXISTS {}.entity_evidence_identifier_entity_idx'
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_canonical_lookup_idx').format(
            schema_id
        )
    )
    specs = [
        (
            'entity_evidence_identifier_identifier_idx',
            'entity_evidence_identifier',
            ('identifier_id', 'entity_evidence_id'),
        ),
        (
            'entity_resolution_status_idx',
            'entity_evidence_resolution',
            ('status_id',),
        ),
        (
            'entity_resolution_reason_idx',
            'entity_evidence_resolution',
            ('reason_id',),
        ),
        (
            'entity_evidence_resolution_entity_idx',
            'entity_evidence_resolution',
            ('entity_id',),
        ),
        (
            'entity_canonical_lookup_idx',
            'entity',
            (
                'canonical_identifier_type_id',
                'canonical_identifier',
            ),
        ),
        ('entity_status_idx', 'entity', ('resolution_status_id',)),
        (
            'relation_subject_idx',
            'relation_evidence',
            ('subject_entity_evidence_id',),
        ),
        (
            'relation_object_idx',
            'relation_evidence',
            ('object_entity_evidence_id',),
        ),
        (
            'relation_subject_entity_idx',
            'relation_evidence',
            ('subject_entity_id',),
        ),
        (
            'relation_object_entity_idx',
            'relation_evidence',
            ('object_entity_id',),
        ),
        (
            'relation_source_dataset_idx',
            'relation_evidence',
            ('source', 'dataset'),
        ),
        (
            'entity_evidence_annotation_annotation_key_idx',
            'entity_evidence_annotation',
            ('annotation_key',),
        ),
        (
            'relation_evidence_annotation_relation_evidence_idx',
            'relation_evidence_annotation',
            ('relation_evidence_id',),
        ),
        (
            'relation_evidence_annotation_annotation_key_idx',
            'relation_evidence_annotation',
            ('annotation_key',),
        ),
        ('entity_annotation_annotation_key_idx', 'entity_annotation', ('annotation_key',)),
        (
            'relation_annotation_annotation_key_idx',
            'relation_annotation',
            ('annotation_key',),
        ),
        (
            'resolver_protein_lookup_key_tax_idx',
            'resolver_protein_identifier_lookup',
            ('key_identifier_type_id', 'key_value', 'taxonomy_id'),
        ),
        (
            'resolver_protein_lookup_key_idx',
            'resolver_protein_identifier_lookup',
            ('key_identifier_type_id', 'key_value'),
        ),
        (
            'resolver_protein_ambiguous_key_tax_idx',
            'resolver_protein_identifier_lookup_ambiguous',
            ('key_identifier_type_id', 'key_value', 'taxonomy_id'),
        ),
        (
            'resolver_chemical_lookup_key_idx',
            'resolver_chemical_identifier_lookup',
            ('key_identifier_type_id', 'key_value'),
        ),
        ('resources_build_status_idx', 'resources', ('build_status',)),
    ]
    for name, table, columns in specs:
        cur.execute(
            sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {}.{} ({})').format(
                sql.Identifier(name),
                schema_id,
                sql.Identifier(table),
                sql.SQL(', ').join(
                    sql.Identifier(column) for column in columns
                ),
            )
        )
