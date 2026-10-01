"""Lossless PostgreSQL projection of already resolved resource Parquet files."""

from __future__ import annotations

from psycopg import sql


_TABLE_COLUMNS = {
    "resource_versions": """
        resource text NOT NULL,
        version text NOT NULL,
        manifest_json jsonb NOT NULL,
        manifest_text text NOT NULL,
        manifest_sha256 text NOT NULL,
        parquet_checksums jsonb NOT NULL,
        PRIMARY KEY (resource, version)
    """,
    "release_metadata": """
        release_id text PRIMARY KEY,
        manifest_json jsonb NOT NULL,
        manifest_text text NOT NULL,
        manifest_sha256 text NOT NULL,
        input_manifest_sha256 text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    """,
    "base_checkpoint": """
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        format_version integer NOT NULL CHECK (format_version = 1),
        release_id text NOT NULL,
        manifest_json jsonb NOT NULL,
        manifest_text text NOT NULL,
        manifest_sha256 text NOT NULL,
        input_manifest_sha256 text NOT NULL,
        resources jsonb NOT NULL,
        resource_metadata jsonb NOT NULL,
        counts jsonb NOT NULL,
        base_indexes jsonb NOT NULL,
        validate_source_records boolean NOT NULL,
        validated_payload_rows jsonb NOT NULL,
        phase_seconds jsonb NOT NULL,
        base_completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        completed_at timestamptz
    """,
    "entity_ontology_relation": """
        resource text NOT NULL, version text NOT NULL, relation_id text NOT NULL,
        subject_entity_id text NOT NULL, object_entity_id text NOT NULL,
        predicate text NOT NULL, child_entity_id text NOT NULL,
        parent_entity_id text NOT NULL, hierarchy_kind text NOT NULL,
        PRIMARY KEY (resource, version, relation_id)
    """,
    "entities": """
        resource text NOT NULL,
        version text NOT NULL,
        entity_key text NOT NULL,
        entity_type text,
        namespace text,
        identifier text,
        taxon text,
        label text,
        has_hierarchy boolean,
        parent_count bigint,
        child_count bigint,
        record_json jsonb NOT NULL
    """,
    "relations": """
        resource text NOT NULL,
        version text NOT NULL,
        relation_key text NOT NULL,
        statement_kind text,
        subject_entity_key text,
        subject_label text,
        subject_type text,
        predicate text,
        object_entity_key text,
        object_label text,
        object_type text,
        taxon text,
        is_directed boolean,
        sign integer,
        category text,
        interaction_class text,
        sources jsonb,
        evidence_count bigint,
        record_json jsonb NOT NULL
    """,
    "identifiers": """
        resource text NOT NULL,
        version text NOT NULL,
        entity_key text NOT NULL,
        ordinal bigint NOT NULL CHECK (ordinal >= 0),
        ns text,
        id text,
        is_canonical boolean,
        source text
    """,
    "evidence": """
        resource text NOT NULL,
        version text NOT NULL,
        relation_key text NOT NULL,
        ordinal bigint NOT NULL CHECK (ordinal >= 0),
        source text,
        dataset text,
        row_id text,
        upstream_id text,
        record_json jsonb NOT NULL
    """,
    "annotations": """
        annotation_id bigserial,
        resource text NOT NULL,
        version text NOT NULL,
        owner_kind text NOT NULL CHECK (owner_kind IN ('entity', 'relation', 'evidence')),
        owner_key text NOT NULL,
        evidence_ordinal bigint,
        ordinal bigint NOT NULL CHECK (ordinal >= 0),
        term text,
        value text,
        quantity jsonb,
        source text,
        dataset text,
        scope text,
        CHECK (
            (owner_kind = 'evidence' AND evidence_ordinal IS NOT NULL AND evidence_ordinal >= 0)
            OR (owner_kind IN ('entity', 'relation') AND evidence_ordinal IS NULL)
        )
    """,
}


_BASE_PRIMARY_KEYS = {
    "entities": "PRIMARY KEY (resource, version, entity_key)",
    "relations": "PRIMARY KEY (resource, version, relation_key)",
    "identifiers": "PRIMARY KEY (resource, version, entity_key, ordinal)",
    "evidence": "PRIMARY KEY (resource, version, relation_key, ordinal)",
    "annotations": "PRIMARY KEY (annotation_id)",
}
_BASE_FOREIGN_KEYS = {
    "entities": (
        (
            "entities_resource_version_fkey",
            "FOREIGN KEY (resource, version) REFERENCES {s}.resource_versions (resource, version) DEFERRABLE INITIALLY DEFERRED",
        ),
    ),
    "relations": (
        (
            "relations_resource_version_fkey",
            "FOREIGN KEY (resource, version) REFERENCES {s}.resource_versions (resource, version) DEFERRABLE INITIALLY DEFERRED",
        ),
        (
            "relations_resource_version_subject_entity_key_fkey",
            "FOREIGN KEY (resource, version, subject_entity_key) REFERENCES {s}.entities (resource, version, entity_key) DEFERRABLE INITIALLY DEFERRED",
        ),
        (
            "relations_resource_version_object_entity_key_fkey",
            "FOREIGN KEY (resource, version, object_entity_key) REFERENCES {s}.entities (resource, version, entity_key) DEFERRABLE INITIALLY DEFERRED",
        ),
    ),
    "identifiers": (
        (
            "identifiers_resource_version_entity_key_fkey",
            "FOREIGN KEY (resource, version, entity_key) REFERENCES {s}.entities (resource, version, entity_key) DEFERRABLE INITIALLY DEFERRED",
        ),
    ),
    "evidence": (
        (
            "evidence_resource_version_relation_key_fkey",
            "FOREIGN KEY (resource, version, relation_key) REFERENCES {s}.relations (resource, version, relation_key) DEFERRABLE INITIALLY DEFERRED",
        ),
    ),
    "annotations": (
        (
            "annotations_resource_version_fkey",
            "FOREIGN KEY (resource, version) REFERENCES {s}.resource_versions (resource, version) DEFERRABLE INITIALLY DEFERRED",
        ),
    ),
}
# Keep the complete default definitions available to existing contract consumers.
_TABLES = {
    name: definition
    + (
        ",\n"
        + ",\n".join(
            [
                f"CONSTRAINT {name}_pkey {_BASE_PRIMARY_KEYS[name]}",
                *[
                    f"CONSTRAINT {constraint} {foreign_key}"
                    for constraint, foreign_key in _BASE_FOREIGN_KEYS[name]
                ],
            ]
        )
        if name in _BASE_PRIMARY_KEYS
        else ""
    )
    for name, definition in _TABLE_COLUMNS.items()
}


def _annotation_owner_index(cur, namespace):
    cur.execute(
        sql.SQL("""
            CREATE UNIQUE INDEX annotations_owner_ordinal_idx
            ON {}.annotations (
                resource, version, owner_kind, owner_key,
                COALESCE(evidence_ordinal, -1), ordinal
            )
        """).format(namespace)
    )


def add_base_constraints(conn, schema: str) -> None:
    """Install and validate the exact base keys/FKs after COPY; never commit.

    Referenced keys exist before any FK is added. NOT VALID avoids row trigger
    queues for existing data; explicit VALIDATE checks every FK before checkpoint
    or derivation. Column NOT NULL/CHECK constraints remained active during COPY.
    """
    namespace = sql.Identifier(schema)
    with conn.cursor() as cur:
        for table, definition in _BASE_PRIMARY_KEYS.items():
            cur.execute(
                sql.SQL("ALTER TABLE {}.{} ADD CONSTRAINT {} {}").format(
                    namespace,
                    sql.Identifier(table),
                    sql.Identifier(table + "_pkey"),
                    sql.SQL(definition),
                )
            )
        _annotation_owner_index(cur, namespace)
        for table, constraints in _BASE_FOREIGN_KEYS.items():
            for name, definition in constraints:
                cur.execute(
                    sql.SQL("ALTER TABLE {}.{} ADD CONSTRAINT {} {} NOT VALID").format(
                        namespace,
                        sql.Identifier(table),
                        sql.Identifier(name),
                        sql.SQL(definition).format(s=namespace),
                    )
                )
        # COPY and index creation do not provide column distributions, and
        # autovacuum cannot see this transaction's rows. Validate using current
        # narrow join-key statistics; leave wide JSON/quantity to later ANALYZE.
        validation_columns = {
            "resource_versions": ("resource", "version"),
            "entities": ("resource", "version", "entity_key"),
            "relations": (
                "resource",
                "version",
                "relation_key",
                "subject_entity_key",
                "object_entity_key",
            ),
            "identifiers": ("resource", "version", "entity_key"),
            "evidence": ("resource", "version", "relation_key"),
            "annotations": ("resource", "version"),
        }
        for table, columns in validation_columns.items():
            cur.execute(
                sql.SQL("ANALYZE {}.{} ({})").format(
                    namespace,
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                )
            )
        for table, constraints in _BASE_FOREIGN_KEYS.items():
            for name, _ in constraints:
                cur.execute(
                    sql.SQL("ALTER TABLE {}.{} VALIDATE CONSTRAINT {}").format(
                        namespace, sql.Identifier(table), sql.Identifier(name)
                    )
                )


def create_schema(conn, schema: str, *, defer_constraints: bool = False) -> None:
    """Create a fresh release schema inside the caller's transaction.

    An existing schema is an error. Full resource rows remain authoritative;
    canonical views choose deterministic display fields and consensus taxon
    without modifying source records.
    The caller validates generic annotation owners before loading.
    """
    if type(defer_constraints) is not bool:
        raise ValueError("defer_constraints must be a boolean")
    namespace = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(namespace))
        for name, definition in _TABLES.items():
            if defer_constraints and name in _BASE_PRIMARY_KEYS:
                definition = _TABLE_COLUMNS[name]
            columns = sql.SQL(definition).format(s=namespace)
            cur.execute(
                sql.SQL("CREATE TABLE {}.{} ({})").format(namespace, sql.Identifier(name), columns)
            )
        if not defer_constraints:
            _annotation_owner_index(cur, namespace)
        cur.execute(
            sql.SQL("""
                CREATE VIEW {s}.entity AS
                WITH chosen AS (
                    SELECT DISTINCT ON (entity_key) *
                    FROM {s}.entities
                    ORDER BY entity_key, resource, version
                ), resources AS (
                    SELECT entity_key,
                        CASE WHEN COUNT(DISTINCT taxon) = 1
                            AND BOOL_AND(taxon IS NOT NULL AND BTRIM(taxon) <> '')
                            THEN MIN(taxon) ELSE '' END AS taxon,
                        jsonb_agg(jsonb_build_object(
                            'resource', resource, 'version', version, 'record', record_json
                        ) ORDER BY resource, version) AS resource_records
                    FROM {s}.entities
                    GROUP BY entity_key
                )
                , hierarchy AS (
                    SELECT entity_id, SUM(parents)::bigint AS parents, SUM(children)::bigint AS children
                    FROM (
                        SELECT child_entity_id AS entity_id, COUNT(DISTINCT parent_entity_id) AS parents,
                            0::bigint AS children FROM {s}.entity_ontology_relation GROUP BY child_entity_id
                        UNION ALL
                        SELECT parent_entity_id, 0::bigint, COUNT(DISTINCT child_entity_id)
                        FROM {s}.entity_ontology_relation GROUP BY parent_entity_id
                    ) counts GROUP BY entity_id
                )
                SELECT c.entity_key AS entity_id, c.entity_key, c.entity_type,
                    c.namespace, c.identifier, r.taxon, c.label,
                    (COALESCE(h.parents,0)+COALESCE(h.children,0)>0) AS has_hierarchy,
                    COALESCE(h.parents,0) AS parent_count, COALESCE(h.children,0) AS child_count,
                    r.resource_records
                FROM chosen c
                JOIN resources r USING (entity_key)
                LEFT JOIN hierarchy h ON h.entity_id=c.entity_key
            """).format(s=namespace)
        )
        cur.execute(
            sql.SQL("""
                CREATE VIEW {s}.statement AS
                WITH chosen AS (
                    SELECT DISTINCT ON (relation_key) *
                    FROM {s}.relations
                    ORDER BY relation_key, resource, version
                ), resources AS (
                    SELECT relation_key, SUM(COALESCE(evidence_count, 0))::bigint AS evidence_count,
                        CASE WHEN COUNT(DISTINCT taxon) = 1
                            AND BOOL_AND(taxon IS NOT NULL AND BTRIM(taxon) <> '')
                            THEN MIN(taxon) ELSE '' END AS taxon,
                        jsonb_agg(jsonb_build_object(
                            'resource', resource, 'version', version, 'record', record_json
                        ) ORDER BY resource, version) AS resource_records
                    FROM {s}.relations
                    GROUP BY relation_key
                ), source_sets AS (
                    SELECT relation_key, jsonb_agg(DISTINCT item ORDER BY item) AS sources
                    FROM {s}.relations,
                        LATERAL jsonb_array_elements(
                            CASE WHEN jsonb_typeof(sources) = 'array' THEN sources ELSE '[]'::jsonb END
                        ) AS items(item)
                    GROUP BY relation_key
                )
                SELECT c.relation_key AS relation_id, c.relation_key, c.statement_kind,
                    c.subject_entity_key AS subject_entity_id,
                    c.object_entity_key AS object_entity_id,
                    c.subject_entity_key, c.subject_label, c.subject_type, c.predicate,
                    c.object_entity_key, c.object_label, c.object_type, r.taxon,
                    c.is_directed, c.sign, c.category, c.interaction_class,
                    COALESCE(ss.sources, '[]'::jsonb) AS sources,
                    r.evidence_count, r.resource_records
                FROM chosen c
                JOIN resources r USING (relation_key)
                LEFT JOIN source_sets ss USING (relation_key)
            """).format(s=namespace)
        )
        cur.execute(
            sql.SQL("""
                CREATE VIEW {s}.relation AS
                SELECT * FROM {s}.statement WHERE statement_kind='relation'
            """).format(s=namespace)
        )
