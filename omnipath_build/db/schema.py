"""PostgreSQL DDL for source evidence, resolution, and graph tables.

The schema separates refresh-loaded source evidence from canonical graph
materialization. Evidence tables are source-partitioned and keep source row
identity, participant occurrence identity, identifiers, annotations, and
relation endpoints. Resolution tables map those evidence occurrences to
canonical entities, while relation mapping tables connect source relation
evidence to deduplicated graph relations.

Several high-volume secondary indexes are treated as deferred content indexes.
Scratch builds can create the schema without them, stream source evidence
quickly, and then create the indexes once before canonicalization or derivation.
"""

from __future__ import annotations

from psycopg2 import sql
import psycopg2.extensions

CONTENT_TABLES: tuple[str, ...] = (
    'annotation_term_entity_bitmap',
    'annotation_term_relation_bitmap',
    'facet_entity_bitmap',
    'facet_relation_bitmap',
    'entity_bitmap_id',
    'relation_bitmap_id',
    'entity_relation_counts',
    'ontology_terms',
    'relation_annotation',
    'relation_evidence_annotation',
    'entity_annotation_relation',
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
    'dataset',
    'data_source',
)

SOURCE_PARTITIONED_TABLES: tuple[str, ...] = (
    'ontology_terms',
    'entity_evidence',
    'entity_evidence_identifier',
    'relation_evidence',
    'entity_evidence_annotation',
    'relation_evidence_annotation',
    'entity_evidence_resolution',
    'relation_evidence_relation',
    'entity_annotation_relation',
)

SOURCE_PARTITION_DROP_ORDER: tuple[str, ...] = (
    'ontology_terms',
    'relation_evidence_annotation',
    'relation_evidence_relation',
    'entity_annotation_relation',
    'relation_evidence',
    'entity_evidence_annotation',
    'entity_evidence_resolution',
    'entity_evidence_identifier',
    'entity_evidence',
)

CONTENT_PRIMARY_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('identifier_evidence', ('identifier_id',)),
    ('annotation', ('annotation_key',)),
    ('entity', ('entity_id',)),
    ('entity_evidence', ('source_id', 'entity_evidence_id')),
    (
        'entity_evidence_identifier',
        ('source_id', 'entity_evidence_id', 'identifier_id'),
    ),
    (
        'entity_evidence_annotation',
        ('source_id', 'entity_evidence_id', 'annotation_key'),
    ),
    ('relation_evidence', ('source_id', 'relation_evidence_id')),
    (
        'relation_evidence_annotation',
        (
            'source_id',
            'relation_evidence_id',
            'annotation_key',
            'annotation_scope_id',
        ),
    ),
    ('entity_evidence_resolution', ('source_id', 'entity_evidence_id')),
    ('relation', ('relation_id',)),
    (
        'relation_evidence_relation',
        ('source_id', 'relation_evidence_id'),
    ),
    ('ontology_terms', ('source_id', 'term_entity_id')),
)


def ensure_schema(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    drop_existing: bool = False,
    progress: bool = False,
    indexes: bool = True,
) -> None:
    """Create or refresh the omnipath_build evidence and resolution schema."""

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

        log_step('rename legacy vocabulary tables')
        _rename_legacy_vocab_tables(cur, schema)
        log_step('create identifier type table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.vocab_identifier_type (
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
        _drop_legacy_content_tables_if_needed(cur, schema)
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.identifier_evidence (
                  identifier_id uuid PRIMARY KEY,
                  identifier_type_id bigint NOT NULL
                    REFERENCES {}.vocab_identifier_type(identifier_type_id),
                  value text NOT NULL
                )
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        log_step('ensure identifier evidence shape')
        _ensure_identifier_evidence_key(cur, schema)
        log_step('create normalized dimension tables')
        _ensure_evidence_dimension_tables(cur, schema)
        log_step('create entity_evidence table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_evidence (
                  source_id bigint NOT NULL
                    REFERENCES {}.data_source(source_id),
                  entity_evidence_id uuid NOT NULL,
                  dataset_id bigint NOT NULL
                    REFERENCES {}.dataset(dataset_id),
                  row_id bigint NOT NULL,
                  parent_entity_evidence_id uuid,
                  entity_role_id smallint NOT NULL
                    REFERENCES {}.vocab_entity_role(entity_role_id),
                  entity_type_id bigint
                    REFERENCES {}.vocab_entity_type(entity_type_id),
                  taxonomy_id bigint,
                  PRIMARY KEY (source_id, entity_evidence_id),
                  FOREIGN KEY (source_id, parent_entity_evidence_id)
                    REFERENCES {}.entity_evidence(source_id, entity_evidence_id)
                ) PARTITION BY LIST (source_id)
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
                """
                CREATE TABLE IF NOT EXISTS {}.entity_evidence_default
                PARTITION OF {}.entity_evidence DEFAULT
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        log_step('create entity_evidence_identifier table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_evidence_identifier (
                  source_id bigint NOT NULL
                    REFERENCES {}.data_source(source_id),
                  entity_evidence_id uuid NOT NULL,
                  identifier_id uuid NOT NULL
                    REFERENCES {}.identifier_evidence(identifier_id),
                  PRIMARY KEY (source_id, entity_evidence_id, identifier_id),
                  FOREIGN KEY (source_id, entity_evidence_id)
                    REFERENCES {}.entity_evidence(source_id, entity_evidence_id)
                    ON DELETE CASCADE
                ) PARTITION BY LIST (source_id)
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
                CREATE TABLE IF NOT EXISTS {}.entity_evidence_identifier_default
                PARTITION OF {}.entity_evidence_identifier DEFAULT
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        log_step('create relation_evidence table')
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.relation_evidence (
                  source_id bigint NOT NULL
                    REFERENCES {}.data_source(source_id),
                  relation_evidence_id uuid NOT NULL,
                  dataset_id bigint NOT NULL
                    REFERENCES {}.dataset(dataset_id),
                  row_id bigint NOT NULL,
                  subject_entity_evidence_id uuid,
                  subject_entity_id uuid,
                  predicate_id bigint NOT NULL
                    REFERENCES {}.vocab_relation_predicate(relation_predicate_id),
                  object_entity_evidence_id uuid,
                  object_entity_id uuid,
                  relation_category_id bigint NOT NULL
                    REFERENCES {}.vocab_relation_category(relation_category_id),
                  PRIMARY KEY (source_id, relation_evidence_id),
                  CHECK (
                    (subject_entity_evidence_id IS NOT NULL)::int
                    + (subject_entity_id IS NOT NULL)::int
                    = 1
                  ),
                  CHECK (
                    (object_entity_evidence_id IS NOT NULL)::int
                    + (object_entity_id IS NOT NULL)::int
                    = 1
                  ),
                  FOREIGN KEY (source_id, subject_entity_evidence_id)
                    REFERENCES {}.entity_evidence(source_id, entity_evidence_id),
                  FOREIGN KEY (source_id, object_entity_evidence_id)
                    REFERENCES {}.entity_evidence(source_id, entity_evidence_id)
                ) PARTITION BY LIST (source_id)
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
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
                """
                CREATE TABLE IF NOT EXISTS {}.relation_evidence_default
                PARTITION OF {}.relation_evidence DEFAULT
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
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
        _ensure_resolution_schema(
            cur, schema, progress=progress, indexes=indexes
        )

    log_step('commit')
    conn.commit()
    if started is not None:
        import time

        log_step(f'ensure done elapsed={time.perf_counter() - started:.1f}s')


def ensure_content_primary_keys(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> None:
    """Restore content-table primary keys after constraint-free bulk loads."""

    def log_step(message: str) -> None:
        if progress:
            print(f'[schema] {message}', flush=True)

    log_step('ensure content primary keys')
    with conn.cursor() as cur:
        for table, columns in CONTENT_PRIMARY_KEYS:
            cur.execute(
                """
                SELECT to_regclass(%s) IS NOT NULL
                """,
                [f'{schema}.{table}'],
            )
            if not cur.fetchone()[0]:
                continue
            cur.execute(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = %s::regclass
                  AND contype = 'p'
                """,
                [f'{schema}.{table}'],
            )
            if cur.fetchone() is not None:
                continue
            log_step(f'add primary key {table}')
            column_sql = sql.SQL(', ').join(
                sql.Identifier(column) for column in columns
            )
            cur.execute(
                sql.SQL(
                    'ALTER TABLE {}.{} ADD CONSTRAINT {} PRIMARY KEY ({})'
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.Identifier(f'{table}_pkey'),
                    column_sql,
                )
            )
    conn.commit()


def ensure_deferred_indexes(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> None:
    """Create high-volume evidence indexes used after bulk ingest."""

    def log_step(message: str) -> None:
        if progress:
            print(f'[schema] {message}', flush=True)

    log_step('ensure deferred indexes')
    with conn.cursor() as cur:
        _ensure_resolution_indexes(cur, schema)
    conn.commit()


def ensure_source_partitions(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    source: str,
) -> None:
    """Create source-specific partitions for partitioned evidence tables."""

    suffix = _source_partition_suffix(source)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.data_source (name)
                VALUES (%s)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING source_id
                """
            ).format(sql.Identifier(schema)),
            [source],
        )
        source_id = int(cur.fetchone()[0])
        for table in SOURCE_PARTITIONED_TABLES:
            cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.{}
                    PARTITION OF {}.{} FOR VALUES IN ({})
                    """
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(f'{table}_{suffix}'),
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.Literal(source_id),
                )
            )
    conn.commit()


def _source_partition_suffix(source: str) -> str:
    import re
    import hashlib

    slug = re.sub(r'[^a-z0-9]+', '_', source.lower()).strip('_')
    slug = slug[:40] or 'source'
    digest = hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]
    return f'{slug}_{digest}'


def drop_deferred_content_indexes(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> list[str]:
    """Drop deferred evidence indexes to speed up a subsequent bulk reload."""

    names = [
        'entity_evidence_identifier_identifier_idx',
        'entity_evidence_source_dataset_row_idx',
        'entity_evidence_type_taxonomy_idx',
        'entity_resolution_status_idx',
        'entity_resolution_reason_idx',
        'entity_evidence_resolution_entity_idx',
        'entity_canonical_lookup_idx',
        'entity_status_idx',
        'relation_subject_idx',
        'relation_object_idx',
        'relation_subject_entity_idx',
        'relation_object_entity_idx',
        'relation_source_dataset_idx',
        'relation_evidence_predicate_category_idx',
        'relation_evidence_source_dataset_row_idx',
        'entity_evidence_annotation_annotation_key_idx',
        'entity_annotation_relation_relation_id_idx',
        'relation_evidence_annotation_relation_evidence_idx',
        'relation_evidence_annotation_annotation_key_idx',
        'entity_annotation_annotation_key_idx',
        'relation_annotation_annotation_key_idx',
        'resources_build_status_idx',
    ]
    schema_id = sql.Identifier(schema)
    with conn.cursor() as cur:
        for name in names:
            if progress:
                print(f'[schema] drop index {name}', flush=True)
            cur.execute(
                sql.SQL('DROP INDEX IF EXISTS {}.{}').format(
                    schema_id,
                    sql.Identifier(name),
                )
            )
    conn.commit()
    return names


def _rename_legacy_vocab_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    renames = (
        ('identifier_type', 'vocab_identifier_type'),
        ('entity_type', 'vocab_entity_type'),
        ('entity_role', 'vocab_entity_role'),
        ('relation_predicate', 'vocab_relation_predicate'),
        ('relation_category', 'vocab_relation_category'),
        ('annotation_scope', 'vocab_annotation_scope'),
        ('resolution_status', 'vocab_resolution_status'),
        ('resolution_reason', 'vocab_resolution_reason'),
    )
    for old_name, new_name in renames:
        cur.execute(
            """
            SELECT
              to_regclass(%s) IS NOT NULL AS old_exists,
              to_regclass(%s) IS NOT NULL AS new_exists
            """,
            [f'{schema}.{old_name}', f'{schema}.{new_name}'],
        )
        old_exists, new_exists = cur.fetchone()
        if old_exists and not new_exists:
            cur.execute(
                sql.SQL('ALTER TABLE {}.{} RENAME TO {}').format(
                    sql.Identifier(schema),
                    sql.Identifier(old_name),
                    sql.Identifier(new_name),
                )
            )


def _drop_legacy_content_tables_if_needed(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    """Drop empty generated-id content tables before recreating UUID keys."""

    expected_uuid_columns = {
        'identifier_evidence': 'identifier_id',
        'entity_evidence': 'entity_evidence_id',
        'relation_evidence': 'relation_evidence_id',
    }
    cur.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s
          AND (table_name, column_name) IN (
            ('identifier_evidence', 'identifier_id'),
            ('entity_evidence', 'entity_evidence_id'),
            ('relation_evidence', 'relation_evidence_id')
          )
        """,
        [schema],
    )
    column_types = {
        (table_name, column_name): data_type
        for table_name, column_name, data_type in cur.fetchall()
    }
    legacy_tables = [
        table
        for table, column in expected_uuid_columns.items()
        if column_types.get((table, column)) not in (None, 'uuid')
    ]
    cur.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name IN (
            'entity_evidence',
            'relation_evidence',
            'entity_evidence_identifier',
            'entity_evidence_annotation',
            'relation_evidence_annotation',
            'entity_evidence_resolution',
            'relation',
            'relation_evidence_relation'
          )
        """,
        [schema],
    )
    columns_by_table: dict[str, set[str]] = {}
    for table_name, column_name in cur.fetchall():
        columns_by_table.setdefault(table_name, set()).add(column_name)
    incompatible_columns = {
        'entity_evidence': {
            'occurrence_id',
            'snapshot_id',
            'entity_role',
            'entity_type',
        },
        'relation_evidence': {
            'relation_occurrence_id',
            'snapshot_id',
            'predicate',
            'relation_category',
        },
        'entity_evidence_identifier': set(),
        'entity_evidence_annotation': {'scope'},
        'relation_evidence_annotation': {'scope'},
        'entity_evidence_resolution': set(),
        'relation': {'predicate', 'relation_category'},
        'relation_evidence_relation': set(),
    }
    source_partitioned_tables = {
        'entity_evidence',
        'relation_evidence',
        'entity_evidence_identifier',
        'entity_evidence_annotation',
        'relation_evidence_annotation',
        'entity_evidence_resolution',
        'relation_evidence_relation',
    }
    for table, columns in incompatible_columns.items():
        existing_columns = columns_by_table.get(table)
        if not existing_columns:
            continue
        if columns & existing_columns:
            legacy_tables.append(table)
        elif (
            table in source_partitioned_tables
            and 'source_id' not in existing_columns
        ):
            legacy_tables.append(table)
    legacy_tables = sorted(set(legacy_tables))
    if not legacy_tables:
        return

    existing_content_tables = _existing_content_tables(cur, schema)
    non_empty_tables: list[str] = []
    for table in existing_content_tables:
        cur.execute(
            sql.SQL('SELECT EXISTS (SELECT 1 FROM {}.{} LIMIT 1)').format(
                sql.Identifier(schema),
                sql.Identifier(table),
            )
        )
        if bool(cur.fetchone()[0]):
            non_empty_tables.append(table)

    if non_empty_tables:
        raise RuntimeError(
            'Cannot migrate omnipath_build content tables to deterministic UUID keys '
            'while content rows exist. Run `make reset-content` first. '
            f'Non-empty tables: {", ".join(non_empty_tables)}'
        )

    for table in existing_content_tables:
        cur.execute(
            sql.SQL('DROP TABLE IF EXISTS {}.{} CASCADE').format(
                sql.Identifier(schema),
                sql.Identifier(table),
            )
        )


def _existing_content_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> list[str]:
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
    existing = {row[0] for row in cur.fetchall()}
    return [table for table in CONTENT_TABLES if table in existing]


def reset_content_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> list[str]:
    """Truncate refresh-loaded content while keeping resolver tables intact."""

    with conn.cursor() as cur:
        if _table_exists(cur, schema, 'annotation'):
            _drop_obsolete_annotation_indexes(cur, schema)
        tables_to_truncate = _existing_content_tables(cur, schema)
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
        _drop_existing_source_partitions(cur, schema)
    conn.commit()
    return tables_to_truncate


def _drop_existing_source_partitions(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> list[str]:
    """Drop source partitions so source IDs can be reused after reset."""

    dropped: list[str] = []
    for parent_table in SOURCE_PARTITION_DROP_ORDER:
        cur.execute(
            """
            SELECT child.relname
            FROM pg_inherits i
            JOIN pg_class parent ON parent.oid = i.inhparent
            JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
            JOIN pg_class child ON child.oid = i.inhrelid
            JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
            WHERE parent_ns.nspname = %s
              AND parent.relname = %s
              AND child_ns.nspname = %s
              AND child.relname <> %s
            ORDER BY child.relname
            """,
            [schema, parent_table, schema, f'{parent_table}_default'],
        )
        child_tables = [row[0] for row in cur.fetchall()]
        for child_table in child_tables:
            cur.execute(
                sql.SQL('ALTER TABLE {}.{} DETACH PARTITION {}.{}').format(
                    sql.Identifier(schema),
                    sql.Identifier(parent_table),
                    sql.Identifier(schema),
                    sql.Identifier(child_table),
                )
            )
            cur.execute(
                sql.SQL('DROP TABLE {}.{}').format(
                    sql.Identifier(schema),
                    sql.Identifier(child_table),
                )
            )
            dropped.append(child_table)
    return dropped


def _table_exists(
    cur: psycopg2.extensions.cursor,
    schema: str,
    table: str,
) -> bool:
    cur.execute('SELECT to_regclass(%s) IS NOT NULL', [f'{schema}.{table}'])
    return bool(cur.fetchone()[0])


def _is_partitioned_table(
    cur: psycopg2.extensions.cursor,
    schema: str,
    table: str,
) -> bool:
    cur.execute(
        """
        SELECT c.relkind = 'p'
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname = %s
        """,
        [schema, table],
    )
    row = cur.fetchone()
    return bool(row and row[0])


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
            CREATE TABLE IF NOT EXISTS {}.vocab_identifier_type (
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
            CREATE TABLE IF NOT EXISTS {}.data_source (
              source_id bigserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.dataset (
              dataset_id bigserial PRIMARY KEY,
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              name text NOT NULL,
              UNIQUE (source_id, name)
            )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.vocab_entity_type (
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
            CREATE TABLE IF NOT EXISTS {}.vocab_resolution_status (
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
            CREATE TABLE IF NOT EXISTS {}.vocab_resolution_reason (
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
              entity_id uuid PRIMARY KEY,
              entity_type_id bigint NOT NULL
                REFERENCES {}.vocab_entity_type(entity_type_id),
              taxonomy_id bigint,
              canonical_identifier_type_id bigint
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
              canonical_identifier text NOT NULL,
              identifiers jsonb NOT NULL DEFAULT '[]'::jsonb,
              resolution_status_id smallint NOT NULL
                REFERENCES {}.vocab_resolution_status(resolution_status_id),
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
        sql.SQL(
            'DROP TABLE IF EXISTS {}.entity_resolution_candidate CASCADE'
        ).format(schema_id)
    )
    log_step('create entity_evidence_resolution table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_evidence_resolution (
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              entity_evidence_id uuid NOT NULL,
              status_id smallint NOT NULL
                REFERENCES {}.vocab_resolution_status(resolution_status_id),
              entity_id uuid
                REFERENCES {}.entity(entity_id),
              reason_id smallint
                REFERENCES {}.vocab_resolution_reason(resolution_reason_id),
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
              ),
              PRIMARY KEY (source_id, entity_evidence_id),
              FOREIGN KEY (source_id, entity_evidence_id)
                REFERENCES {}.entity_evidence(source_id, entity_evidence_id)
                ON DELETE CASCADE
            ) PARTITION BY LIST (source_id)
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
        )
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_evidence_resolution_default
            PARTITION OF {}.entity_evidence_resolution DEFAULT
            """
        ).format(schema_id, schema_id)
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
            CREATE TABLE IF NOT EXISTS {}.vocab_identifier_type (
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
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
              key_value text NOT NULL,
              taxonomy_id text,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
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
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
              key_value text NOT NULL,
              taxonomy_id text,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
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
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
              key_value text NOT NULL,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
              canonical_identifier text NOT NULL
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    log_step('create ambiguous resolver chemical lookup table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolver_chemical_identifier_lookup_ambiguous (
              key_identifier_type_id bigint NOT NULL
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
              key_value text NOT NULL,
              canonical_identifier_type_id bigint NOT NULL
                REFERENCES {}.vocab_identifier_type(identifier_type_id),
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
              input_module text,
              input_module_commit text,
              input_module_dirty boolean NOT NULL DEFAULT false,
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
    _ensure_resources_table_schema(cur, schema)
    log_step('create ontology terms table')
    _ensure_ontology_terms_table(cur, schema)
    log_step('create resolver policy index')
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS resolver_mapping_policy_unique_idx
            ON {}.resolver_mapping_policy (
              entity_family,
              key_type,
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
              relation_id uuid PRIMARY KEY,
              subject_entity_id uuid NOT NULL
                REFERENCES {}.entity(entity_id),
              predicate_id bigint NOT NULL
                REFERENCES {}.vocab_relation_predicate(relation_predicate_id),
              object_entity_id uuid NOT NULL
                REFERENCES {}.entity(entity_id),
              relation_category_id bigint
                REFERENCES {}.vocab_relation_category(relation_category_id),
              created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        ).format(schema_id, schema_id, schema_id, schema_id, schema_id)
    )
    log_step('create relation unique index')
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS relation_unique_idx
            ON {}.relation (
              subject_entity_id,
              predicate_id,
              object_entity_id
            )
            """
        ).format(schema_id)
    )
    log_step('create relation_evidence_relation table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_evidence_relation (
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              relation_id uuid NOT NULL
                REFERENCES {}.relation(relation_id)
                ON DELETE CASCADE,
              relation_evidence_id uuid NOT NULL,
              PRIMARY KEY (source_id, relation_evidence_id),
              FOREIGN KEY (source_id, relation_evidence_id)
                REFERENCES {}.relation_evidence(source_id, relation_evidence_id)
                ON DELETE CASCADE
            ) PARTITION BY LIST (source_id)
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_evidence_relation_default
            PARTITION OF {}.relation_evidence_relation DEFAULT
            """
        ).format(schema_id, schema_id)
    )
    log_step('create entity_annotation_relation table')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_annotation_relation (
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              entity_evidence_id uuid NOT NULL,
              annotation_key uuid NOT NULL,
              relation_id uuid NOT NULL
                REFERENCES {}.relation(relation_id)
                ON DELETE CASCADE,
              PRIMARY KEY (
                source_id,
                entity_evidence_id,
                annotation_key,
                relation_id
              ),
              FOREIGN KEY (
                source_id,
                entity_evidence_id,
                annotation_key
              )
                REFERENCES {}.entity_evidence_annotation(
                  source_id,
                  entity_evidence_id,
                  annotation_key
                )
                ON DELETE CASCADE
            ) PARTITION BY LIST (source_id)
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_annotation_relation_default
            PARTITION OF {}.entity_annotation_relation DEFAULT
            """
        ).format(schema_id, schema_id)
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
                  LEFT JOIN {}.vocab_identifier_type it
                    ON it.name = i.type
                  WHERE i.type IS NOT NULL
                    AND it.identifier_type_id IS NULL
                ),
                base AS (
                  SELECT COALESCE(MAX(identifier_type_id), 0) AS max_id
                  FROM {}.vocab_identifier_type
                )
                INSERT INTO {}.vocab_identifier_type (identifier_type_id, name)
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
                FROM {}.vocab_identifier_type it
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
            type_table=sql.SQL('{}.vocab_identifier_type').format(schema_id),
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


def _ensure_evidence_dimension_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.data_source (
              source_id bigserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.dataset (
              dataset_id bigserial PRIMARY KEY,
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              name text NOT NULL,
              UNIQUE (source_id, name)
            )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.vocab_entity_type (
              entity_type_id bigserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.vocab_entity_role (
              entity_role_id smallserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.vocab_relation_predicate (
              relation_predicate_id bigserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.vocab_relation_category (
              relation_category_id bigserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.vocab_annotation_scope (
              annotation_scope_id smallserial PRIMARY KEY,
              name text NOT NULL UNIQUE
            )
            """
        ).format(schema_id)
    )
    _ensure_static_entity_roles(cur, schema)
    _ensure_static_annotation_scopes(cur, schema)


def _ensure_resources_table_schema(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.resources
            ADD COLUMN IF NOT EXISTS input_module text,
            ADD COLUMN IF NOT EXISTS input_module_commit text,
            ADD COLUMN IF NOT EXISTS input_module_dirty boolean
              NOT NULL DEFAULT false
            """
        ).format(schema_id)
    )


def _ensure_static_entity_roles(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    rows = ((1, 'parent'), (2, 'member'))
    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.vocab_entity_role (entity_role_id, name)
            VALUES (%s, %s)
            ON CONFLICT (entity_role_id) DO UPDATE
            SET name = EXCLUDED.name
            """
        )
        .format(sql.Identifier(schema))
        .as_string(cur.connection),
        rows,
    )


def _ensure_static_annotation_scopes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    rows = ((1, 'relation'), (2, 'subject'), (3, 'object'))
    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.vocab_annotation_scope (annotation_scope_id, name)
            VALUES (%s, %s)
            ON CONFLICT (annotation_scope_id) DO UPDATE
            SET name = EXCLUDED.name
            """
        )
        .format(sql.Identifier(schema))
        .as_string(cur.connection),
        rows,
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
            'relation_evidence_annotation',
            'entity_evidence_annotation',
        ):
            cur.execute(
                sql.SQL('DROP TABLE IF EXISTS {}.{} CASCADE').format(
                    schema_id,
                    sql.Identifier(table),
                )
            )
        cur.execute(
            sql.SQL('DROP TABLE {}.annotation CASCADE').format(schema_id)
        )
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
            CREATE INDEX IF NOT EXISTS annotation_term_idx
            ON {}.annotation (term)
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
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              entity_evidence_id uuid NOT NULL,
              annotation_key uuid NOT NULL
                REFERENCES {}.annotation(annotation_key)
                ON DELETE CASCADE,
              PRIMARY KEY (source_id, entity_evidence_id, annotation_key),
              FOREIGN KEY (source_id, entity_evidence_id)
                REFERENCES {}.entity_evidence(source_id, entity_evidence_id)
                ON DELETE CASCADE
            ) PARTITION BY LIST (source_id)
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_evidence_annotation_default
            PARTITION OF {}.entity_evidence_annotation DEFAULT
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_evidence_annotation (
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              relation_evidence_id uuid NOT NULL,
              annotation_key uuid NOT NULL
                REFERENCES {}.annotation(annotation_key)
                ON DELETE CASCADE,
              annotation_scope_id smallint NOT NULL
                REFERENCES {}.vocab_annotation_scope(annotation_scope_id),
              PRIMARY KEY (
                source_id,
                relation_evidence_id,
                annotation_key,
                annotation_scope_id
              ),
              FOREIGN KEY (source_id, relation_evidence_id)
                REFERENCES {}.relation_evidence(source_id, relation_evidence_id)
                ON DELETE CASCADE
            ) PARTITION BY LIST (source_id)
            """
        ).format(schema_id, schema_id, schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_evidence_annotation_default
            PARTITION OF {}.relation_evidence_annotation DEFAULT
            """
        ).format(schema_id, schema_id)
    )


def _ensure_canonical_annotation_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL('DROP TABLE IF EXISTS {}.entity_annotation CASCADE').format(
            sql.Identifier(schema)
        )
    )
    cur.execute(
        sql.SQL('DROP TABLE IF EXISTS {}.relation_annotation CASCADE').format(
            sql.Identifier(schema)
        )
    )


def _ensure_ontology_terms_table(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'ontology_terms'
          AND column_name = 'source_id'
        """,
        [schema],
    )
    has_source_id = cur.fetchone() is not None
    if _table_exists(cur, schema, 'ontology_terms') and (
        not has_source_id
        or not _is_partitioned_table(cur, schema, 'ontology_terms')
    ):
        cur.execute(
            sql.SQL('DROP TABLE {}.ontology_terms CASCADE').format(schema_id)
        )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.ontology_terms (
              source_id bigint NOT NULL
                REFERENCES {}.data_source(source_id),
              term_entity_id uuid NOT NULL
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              term_id text NOT NULL,
              ontology_prefix text,
              label text NOT NULL,
              definition text,
              ontology_id text,
              synonyms text[] NOT NULL DEFAULT '{{}}',
              synonyms_text text NOT NULL DEFAULT '',
              sources text[] NOT NULL DEFAULT '{{}}',
              PRIMARY KEY (source_id, term_entity_id)
            ) PARTITION BY LIST (source_id)
            """
        ).format(schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.ontology_terms_default
            PARTITION OF {}.ontology_terms DEFAULT
            """
        ).format(schema_id, schema_id)
    )


def _ensure_static_identifier_types(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    from omnipath_build.resolver.identifier_types import identifier_type_rows

    cur.executemany(
        sql.SQL(
            """
            INSERT INTO {}.vocab_identifier_type (identifier_type_id, name)
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
            INSERT INTO {}.vocab_resolution_status (resolution_status_id, name)
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
            INSERT INTO {}.vocab_resolution_reason (resolution_reason_id, name)
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
                FROM {}.vocab_resolution_reason rr
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
            reason_table=sql.SQL('{}.vocab_resolution_reason').format(
                schema_id
            ),
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
                INSERT INTO {}.vocab_entity_type (name)
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
                FROM {}.vocab_entity_type et
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
                FROM {}.vocab_resolution_status rs
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
                FROM {}.vocab_identifier_type it
                LEFT JOIN {}.vocab_resolution_status rs
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
            identifier_type_table=sql.SQL('{}.vocab_identifier_type').format(
                schema_id
            ),
        )
    )
    for column, target_table, target_column, constraint_name in (
        (
            'entity_type_id',
            'vocab_entity_type',
            'entity_type_id',
            'entity_entity_type_id_fkey',
        ),
        (
            'resolution_status_id',
            'vocab_resolution_status',
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
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_id_idx').format(schema_id)
    )
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
        sql.SQL('DROP INDEX IF EXISTS {}.annotation_value_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL(
            'ALTER TABLE {}.annotation DROP COLUMN IF EXISTS value_hash'
        ).format(schema_id)
    )


def _ensure_relation_evidence_entity_endpoints(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
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
                FROM {}.vocab_resolution_status rs
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
            status_table=sql.SQL('{}.vocab_resolution_status').format(
                schema_id
            ),
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
            ('source_id', 'dataset_id'),
        ),
        (
            'relation_evidence_predicate_category_idx',
            'relation_evidence',
            ('predicate_id', 'relation_category_id'),
        ),
        (
            'entity_evidence_annotation_annotation_key_idx',
            'entity_evidence_annotation',
            ('annotation_key',),
        ),
        (
            'entity_annotation_relation_relation_id_idx',
            'entity_annotation_relation',
            ('relation_id',),
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
        (
            'resolver_chemical_ambiguous_key_idx',
            'resolver_chemical_identifier_lookup_ambiguous',
            ('key_identifier_type_id', 'key_value'),
        ),
        (
            'resolver_chemical_lookup_canonical_idx',
            'resolver_chemical_identifier_lookup',
            ('canonical_identifier_type_id', 'canonical_identifier'),
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
