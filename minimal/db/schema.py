from __future__ import annotations

from psycopg2 import sql
import psycopg2.extensions


def ensure_schema(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    drop_existing: bool = False,
) -> None:
    """Create or refresh the minimal evidence and resolution schema."""

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

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.source_row (
                  source_row_id bigserial PRIMARY KEY,
                  source text NOT NULL,
                  dataset text NOT NULL,
                  row_id bigint NOT NULL,
                  snapshot_id text,
                  processed_at timestamptz,
                  UNIQUE (source, dataset, row_id)
                )
                """
            ).format(sql.Identifier(schema))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.identifier (
                  identifier_id bigserial PRIMARY KEY,
                  type text NOT NULL,
                  value text NOT NULL,
                  value_hash text GENERATED ALWAYS AS (md5(value)) STORED
                )
                """
            ).format(sql.Identifier(schema))
        )
        _ensure_identifier_hash_key(cur, schema)
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
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.entity_evidence_identifier (
                  entity_evidence_id bigint NOT NULL
                    REFERENCES {}.entity_evidence(entity_evidence_id),
                  identifier_id bigint NOT NULL
                    REFERENCES {}.identifier(identifier_id),
                  PRIMARY KEY (entity_evidence_id, identifier_id)
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
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.annotation (
                  annotation_id bigserial PRIMARY KEY,
                  term text NOT NULL,
                  value text,
                  unit text,
                  scope text NOT NULL,
                  entity_evidence_id bigint
                    REFERENCES {}.entity_evidence(entity_evidence_id),
                  relation_evidence_id bigint
                    REFERENCES {}.relation_evidence(relation_evidence_id),
                  CHECK (
                    (entity_evidence_id IS NOT NULL)::int
                    + (relation_evidence_id IS NOT NULL)::int
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
        _drop_obsolete_annotation_indexes(cur, schema)
        _ensure_resolution_schema(cur, schema)

    conn.commit()


def _ensure_resolution_schema(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    _drop_obsolete_canonicalization_tables(cur, schema)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity (
              entity_id bigserial PRIMARY KEY,
              entity_type text NOT NULL,
              id_type text NOT NULL,
              id text NOT NULL,
              id_hash text GENERATED ALWAYS AS (md5(id)) STORED,
              taxonomy_id text,
              resolution_status text NOT NULL CHECK (
                resolution_status IN ('resolved', 'unresolved')
              ),
              created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        ).format(schema_id)
    )
    _ensure_entity_hash_key(cur, schema)
    _ensure_entity_annotation_target(cur, schema)
    _ensure_relation_evidence_entity_endpoints(cur, schema)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_resolution_candidate (
              entity_evidence_id bigint NOT NULL
                REFERENCES {}.entity_evidence(entity_evidence_id)
                ON DELETE CASCADE,
              entity_type text NOT NULL,
              id_type text NOT NULL,
              id text NOT NULL,
              id_hash text GENERATED ALWAYS AS (md5(id)) STORED,
              taxonomy_id text,
              support_count integer NOT NULL,
              resolver_sources text[] NOT NULL,
              key_types text[] NOT NULL,
              mapping_types text[],
              created_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (
                entity_evidence_id,
                entity_type,
                id_type,
                id_hash
              )
            )
            """
        ).format(schema_id, schema_id)
    )
    _ensure_candidate_hash_key(cur, schema)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_evidence_resolution (
              entity_evidence_id bigint PRIMARY KEY
                REFERENCES {}.entity_evidence(entity_evidence_id)
                ON DELETE CASCADE,
              status text NOT NULL CHECK (
                status IN (
                  'resolved',
                  'ambiguous',
                  'unresolved',
                  'unsupported'
                )
              ),
              entity_id bigint
                REFERENCES {}.entity(entity_id),
              candidate_count integer NOT NULL DEFAULT 0,
              reason text,
              resolved_at timestamptz NOT NULL DEFAULT now(),
              CHECK (
                (
                  status IN ('resolved', 'unresolved', 'ambiguous')
                  AND entity_id IS NOT NULL
                )
                OR
                (
                  status = 'unsupported'
                  AND entity_id IS NULL
                )
              )
            )
            """
        ).format(schema_id, schema_id, schema_id)
    )
    _ensure_entity_resolution_entity_check(cur, schema)
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
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolver_protein_identifier_lookup (
              source text NOT NULL,
              key_type text NOT NULL,
              key_value text NOT NULL,
              taxonomy_id text,
              primary_uniprot text NOT NULL,
              mapping_type text NOT NULL
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.resolver_chemical_identifier_lookup (
              source text NOT NULL,
              key_type text NOT NULL,
              key_value text NOT NULL,
              standard_inchi_key text NOT NULL,
              standard_inchi text NOT NULL
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.resolver_chemical_identifier_lookup
            ADD COLUMN IF NOT EXISTS standard_inchi_key text
            """
        ).format(schema_id)
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
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.relation_evidence_annotation (
              relation_id bigint NOT NULL
                REFERENCES {}.relation(relation_id)
                ON DELETE CASCADE,
              relation_evidence_id bigint NOT NULL
                REFERENCES {}.relation_evidence(relation_evidence_id)
                ON DELETE CASCADE,
              annotation_id bigint NOT NULL
                REFERENCES {}.annotation(annotation_id)
                ON DELETE CASCADE,
              PRIMARY KEY (relation_id, relation_evidence_id, annotation_id),
              UNIQUE (annotation_id)
            )
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL('DROP TABLE IF EXISTS {}.relation_evidence_resolution').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL('DROP TABLE IF EXISTS {}.relation_annotation_evidence').format(
            schema_id
        )
    )
    _ensure_resolution_indexes(cur, schema)


def _ensure_identifier_hash_key(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.identifier
            ADD COLUMN IF NOT EXISTS value_hash text
            GENERATED ALWAYS AS (md5(value)) STORED
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = %s
          AND rel.relname = 'identifier'
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) = 'UNIQUE (type, value)'
        """,
        [schema],
    )
    for (constraint_name,) in cur.fetchall():
        cur.execute(
            sql.SQL('ALTER TABLE {}.identifier DROP CONSTRAINT {}').format(
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
            """
            CREATE UNIQUE INDEX IF NOT EXISTS identifier_type_value_hash_idx
            ON {}.identifier (type, value_hash)
            """
        ).format(schema_id)
    )


def _ensure_entity_hash_key(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity
            ADD COLUMN IF NOT EXISTS id_hash text
            GENERATED ALWAYS AS (md5(id)) STORED
            """
        ).format(schema_id)
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
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS entity_type_id_hash_idx
            ON {}.entity (entity_type, id_type, id_hash)
            """
        ).format(schema_id)
    )


def _ensure_candidate_hash_key(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.entity_resolution_candidate
            ADD COLUMN IF NOT EXISTS id_hash text
            GENERATED ALWAYS AS (md5(id)) STORED
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = %s
          AND rel.relname = 'entity_resolution_candidate'
          AND c.contype = 'p'
        """,
        [schema],
    )
    for (constraint_name,) in cur.fetchall():
        cur.execute(
            sql.SQL(
                'ALTER TABLE {}.entity_resolution_candidate DROP CONSTRAINT {}'
            ).format(
                schema_id,
                sql.Identifier(constraint_name),
            )
        )
    cur.execute(
        sql.SQL('DROP INDEX IF EXISTS {}.entity_candidate_entity_idx').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS entity_resolution_candidate_unique_idx
            ON {}.entity_resolution_candidate (
              entity_evidence_id,
              entity_type,
              id_type,
              id_hash
            )
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


def _ensure_entity_annotation_target(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    _drop_obsolete_annotation_indexes(cur, schema)
    cur.execute(
        sql.SQL(
            'ALTER TABLE {}.annotation ADD COLUMN IF NOT EXISTS entity_id bigint'
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
                WHERE conname = 'annotation_entity_id_fkey'
                  AND conrelid = '{}.annotation'::regclass
              ) THEN
                ALTER TABLE {}.annotation
                ADD CONSTRAINT annotation_entity_id_fkey
                FOREIGN KEY (entity_id)
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE;
              END IF;
            END
            $$;
            """
        ).format(schema_id, schema_id, schema_id)
    )
    cur.execute(
        """
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = %s
          AND rel.relname = 'annotation'
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) LIKE 'CHECK %%entity_evidence_id%%'
          AND pg_get_constraintdef(c.oid) LIKE '%%relation_evidence_id%%'
        """,
        [schema],
    )
    for (constraint_name,) in cur.fetchall():
        cur.execute(
            sql.SQL('ALTER TABLE {}.annotation DROP CONSTRAINT {}').format(
                schema_id,
                sql.Identifier(constraint_name),
            )
        )
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.annotation
            ADD CONSTRAINT annotation_target_check
            CHECK (
              (entity_evidence_id IS NOT NULL)::int
              + (relation_evidence_id IS NOT NULL)::int
              + (entity_id IS NOT NULL)::int
              = 1
            )
            """
        ).format(schema_id)
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
        """
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = %s
          AND rel.relname = 'entity_evidence_resolution'
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) LIKE 'CHECK %%entity_id%%'
          AND pg_get_constraintdef(c.oid) LIKE '%%status%%'
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
                status IN ('resolved', 'unresolved', 'ambiguous')
                AND entity_id IS NOT NULL
              )
              OR
              (
                status = 'unsupported'
                AND entity_id IS NULL
              )
            )
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
        'entity_resolution_candidate',
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
    specs = [
        (
            'entity_evidence_identifier_entity_idx',
            'entity_evidence_identifier',
            ('entity_evidence_id', 'identifier_id'),
        ),
        (
            'entity_evidence_identifier_identifier_idx',
            'entity_evidence_identifier',
            ('identifier_id', 'entity_evidence_id'),
        ),
        (
            'entity_resolution_status_idx',
            'entity_evidence_resolution',
            ('status',),
        ),
        (
            'entity_evidence_resolution_entity_idx',
            'entity_evidence_resolution',
            ('entity_id',),
        ),
        (
            'entity_candidate_entity_idx',
            'entity_resolution_candidate',
            ('entity_type', 'id_type', 'id_hash'),
        ),
        ('entity_id_hash_lookup_idx', 'entity', ('id_type', 'id_hash')),
        ('entity_status_idx', 'entity', ('resolution_status',)),
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
            'relation_evidence_relation_relation_idx',
            'relation_evidence_relation',
            ('relation_evidence_id',),
        ),
        ('annotation_entity_idx', 'annotation', ('entity_id',)),
        (
            'annotation_entity_evidence_term_idx',
            'annotation',
            ('entity_evidence_id', 'term', 'unit'),
        ),
        (
            'relation_evidence_annotation_annotation_idx',
            'relation_evidence_annotation',
            ('annotation_id',),
        ),
        (
            'relation_evidence_annotation_relation_evidence_idx',
            'relation_evidence_annotation',
            ('relation_evidence_id',),
        ),
        (
            'resolver_protein_lookup_key_tax_idx',
            'resolver_protein_identifier_lookup',
            ('key_type', 'key_value', 'taxonomy_id'),
        ),
        (
            'resolver_protein_lookup_key_idx',
            'resolver_protein_identifier_lookup',
            ('key_type', 'key_value'),
        ),
        (
            'resolver_chemical_lookup_key_idx',
            'resolver_chemical_identifier_lookup',
            ('key_type', 'key_value'),
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
