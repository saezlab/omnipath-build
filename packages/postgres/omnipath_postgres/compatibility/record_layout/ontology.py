"""Ontology search projections over published axioms, without entity resolution."""

from uuid import uuid4

from psycopg import sql

from omnipath_core.biolink import hierarchy_direction, is_descendant


def _closure(cur, schema, table, edge_query, *, scoped):
    """Breadth-first shortest pairs, expanding only the previous round's delta."""
    namespace = sql.Identifier(schema)
    dimensions = ["resource", "version"] if scoped else []
    keys = [*dimensions, "hierarchy_kind", "descendant_entity_id", "ancestor_entity_id"]
    columns = sql.SQL(", ").join(map(sql.Identifier, [*keys, "depth"]))
    key_columns = sql.SQL(", ").join(map(sql.Identifier, keys))
    adjacency_columns = [*dimensions, "hierarchy_kind", "child_entity_id", "parent_entity_id"]
    # Unique names plus explicit pg_temp references avoid search_path collisions,
    # including rebuilds of multiple destination schemas in the same transaction.
    token = uuid4().hex
    names = [f"omnipath_ontology_{role}_{token}" for role in ("adjacency", "frontier", "next")]
    adjacency, frontier, following = [sql.Identifier("pg_temp", name) for name in names]
    cur.execute(
        sql.SQL(
            "CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT DISTINCT {} "
            "FROM ({}) edges WHERE child_entity_id <> parent_entity_id"
        ).format(
            sql.Identifier(names[0]),
            sql.SQL(", ").join(map(sql.Identifier, adjacency_columns)),
            edge_query,
        )
    )
    adjacency_index = [*dimensions, "hierarchy_kind", "child_entity_id"]
    cur.execute(
        sql.SQL("CREATE INDEX ON {} ({})").format(
            adjacency, sql.SQL(", ").join(map(sql.Identifier, adjacency_index))
        )
    )
    cur.execute(sql.SQL("ANALYZE {}").format(adjacency))
    for name, temporary in zip(names[1:], (frontier, following)):
        cur.execute(
            sql.SQL("CREATE TEMP TABLE {} (LIKE {}.{}) ON COMMIT DROP").format(
                sql.Identifier(name), namespace, sql.Identifier(table)
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX ON {} ({})").format(
                temporary,
                sql.SQL(", ").join(
                    map(sql.Identifier, ["ancestor_entity_id", "hierarchy_kind", *dimensions])
                ),
            )
        )
    cur.execute(sql.SQL("TRUNCATE {}.{}").format(namespace, sql.Identifier(table)))
    cur.execute(
        sql.SQL("""
        WITH inserted AS (
            INSERT INTO {s}.{table} ({columns})
            SELECT {dimensions}hierarchy_kind, child_entity_id, parent_entity_id, 1
            FROM {adjacency}
            ON CONFLICT ({key_columns}) DO NOTHING
            RETURNING {columns}
        )
        INSERT INTO {frontier} ({columns}) SELECT {columns} FROM inserted
    """).format(
            s=namespace,
            table=sql.Identifier(table),
            columns=columns,
            key_columns=key_columns,
            dimensions=sql.SQL("".join(f"{name}, " for name in dimensions)),
            adjacency=adjacency,
            frontier=frontier,
        )
    )
    pending = cur.rowcount
    # Every edge has length one. A frontier contains one distance level, so the
    # first insertion of a scoped/kind/pair is its minimum depth. The output PK
    # is the visited set; RETURNING carries only genuinely new pairs forward.
    # Finite nonself pairs terminate cycles without an arbitrary depth limit.
    while pending:
        cur.execute(sql.SQL("ANALYZE {}").format(frontier))
        cur.execute(sql.SQL("TRUNCATE {}").format(following))
        cur.execute(
            sql.SQL("""
            WITH inserted AS (
                INSERT INTO {s}.{table} ({columns})
                SELECT {selected_dimensions}c.hierarchy_kind, c.descendant_entity_id,
                    e.parent_entity_id, MIN(c.depth + 1)
                FROM {frontier} c
                JOIN {adjacency} e ON e.child_entity_id = c.ancestor_entity_id
                    AND e.hierarchy_kind = c.hierarchy_kind {scope_join}
                WHERE c.descendant_entity_id <> e.parent_entity_id
                GROUP BY {selected_dimensions}c.hierarchy_kind, c.descendant_entity_id,
                    e.parent_entity_id
                ON CONFLICT ({key_columns}) DO NOTHING
                RETURNING {columns}
            )
            INSERT INTO {following} ({columns}) SELECT {columns} FROM inserted
        """).format(
                s=namespace,
                table=sql.Identifier(table),
                columns=columns,
                adjacency=adjacency,
                frontier=frontier,
                following=following,
                selected_dimensions=sql.SQL("".join(f"c.{name}, " for name in dimensions)),
                scope_join=sql.SQL("".join(f" AND e.{name} = c.{name}" for name in dimensions)),
                key_columns=key_columns,
            )
        )
        pending = cur.rowcount
        frontier, following = following, frontier
    # Consumers need final statistics, but traversal never rescans or analyzes
    # the growing full closure on each round. Drop working tables before the
    # next scoped/global build; failures are covered by transaction rollback.
    cur.execute(sql.SQL("ANALYZE {}.{}").format(namespace, sql.Identifier(table)))
    cur.execute(sql.SQL("DROP TABLE {}, {}, {}").format(adjacency, frontier, following))


def rebuild_ontology(conn, schema):
    """Rebuild source-scoped and release-wide hierarchy/search tables; never commit."""
    namespace = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT DISTINCT predicate FROM {}.relations WHERE statement_kind='ontology'"
            ).format(namespace)
        )
        directions = []
        for (predicate,) in cur.fetchall():
            reverse = hierarchy_direction(predicate)
            if reverse is None:
                continue
            subclass = (
                predicate == "is_a"
                or is_descendant(predicate, "subclass_of")
                or is_descendant(predicate, "superclass_of")
            )
            directions.append((predicate, reverse, "subclass" if subclass else "part_of"))
        cur.execute("""
            CREATE TEMP TABLE IF NOT EXISTS omnipath_hierarchy_predicates (
                predicate text PRIMARY KEY, reverse boolean, hierarchy_kind text
            ) ON COMMIT DROP
        """)
        cur.execute("TRUNCATE omnipath_hierarchy_predicates")
        if directions:
            cur.executemany(
                "INSERT INTO omnipath_hierarchy_predicates VALUES (%s,%s,%s)", directions
            )
        cur.execute("ANALYZE omnipath_hierarchy_predicates")
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {s}.entity_ontology_relation (
                resource text NOT NULL, version text NOT NULL, relation_id text NOT NULL,
                subject_entity_id text NOT NULL, object_entity_id text NOT NULL,
                predicate text NOT NULL, child_entity_id text NOT NULL,
                parent_entity_id text NOT NULL, hierarchy_kind text NOT NULL,
                PRIMARY KEY (resource, version, relation_id)
            )
        """).format(s=namespace)
        )
        cur.execute(sql.SQL("TRUNCATE {}.entity_ontology_relation").format(namespace))
        cur.execute(
            sql.SQL("""
            INSERT INTO {s}.entity_ontology_relation
            SELECT r.resource, r.version, r.relation_key,
                r.subject_entity_key, r.object_entity_key, r.predicate,
                CASE WHEN p.reverse THEN r.object_entity_key ELSE r.subject_entity_key END,
                CASE WHEN p.reverse THEN r.subject_entity_key ELSE r.object_entity_key END,
                p.hierarchy_kind
            FROM {s}.relations r JOIN omnipath_hierarchy_predicates p USING(predicate)
            WHERE r.statement_kind='ontology'
        """).format(s=namespace)
        )
        for name, dimensions, primary in (
            (
                "ontology_closure",
                "resource text NOT NULL, version text NOT NULL,",
                "resource, version,",
            ),
            ("ontology_ancestor", "", ""),
        ):
            cur.execute(
                sql.SQL("""
                CREATE TABLE IF NOT EXISTS {s}.{name} (
                    {dimensions}
                    hierarchy_kind text NOT NULL, descendant_entity_id text NOT NULL,
                    ancestor_entity_id text NOT NULL, depth integer NOT NULL,
                    PRIMARY KEY ({primary}hierarchy_kind, descendant_entity_id, ancestor_entity_id),
                    CHECK (depth > 0)
                )
            """).format(
                    s=namespace,
                    name=sql.Identifier(name),
                    dimensions=sql.SQL(dimensions),
                    primary=sql.SQL(primary),
                )
            )
        # Traversal must have usable indexes on the first build, not only
        # after a previous closure has already completed.
        for table, name, expression in (
            ("entity_ontology_relation", "ontology_edge_parent_idx", "parent_entity_id"),
            ("entity_ontology_relation", "ontology_edge_child_idx", "child_entity_id"),
            (
                "ontology_closure",
                "ontology_closure_ancestor_idx",
                "ancestor_entity_id, hierarchy_kind",
            ),
            (
                "ontology_ancestor",
                "ontology_ancestor_ancestor_idx",
                "ancestor_entity_id, hierarchy_kind",
            ),
        ):
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.{} ({})").format(
                    sql.Identifier(name),
                    namespace,
                    sql.Identifier(table),
                    sql.SQL(expression),
                )
            )
        cur.execute(sql.SQL("ANALYZE {}.entity_ontology_relation").format(namespace))
        edges = sql.SQL("SELECT * FROM {}.entity_ontology_relation").format(namespace)
        _closure(cur, schema, "ontology_closure", edges, scoped=True)
        _closure(cur, schema, "ontology_ancestor", edges, scoped=False)
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {s}.entity_ontology_term (
                term_entity_id text PRIMARY KEY, term_id text, ontology_prefix text,
                label text, definition text, synonyms text[], synonyms_text text,
                term_aliases text[], identifiers_text text, ontology_id text,
                sources text[], child_count bigint, parent_count bigint
            )
        """).format(s=namespace)
        )
        cur.execute(sql.SQL("TRUNCATE {}.entity_ontology_term").format(namespace))
        cur.execute(
            sql.SQL("""
            INSERT INTO {s}.entity_ontology_term
            WITH nodes AS (
                SELECT child_entity_id AS entity_id FROM {s}.entity_ontology_relation
                UNION SELECT parent_entity_id FROM {s}.entity_ontology_relation
            ), aliases AS (
                SELECT entity_key,
                    ARRAY_AGG(DISTINCT id ORDER BY id) FILTER (WHERE ns='synonym') AS synonyms,
                    ARRAY_AGG(DISTINCT id ORDER BY id) AS term_aliases,
                    STRING_AGG(DISTINCT id, ' ' ORDER BY id) AS identifiers_text
                FROM {s}.identifiers WHERE id IS NOT NULL GROUP BY entity_key
            ), definitions AS (
                SELECT owner_key, MIN(value) AS definition FROM {s}.annotations
                WHERE owner_kind='entity' AND term IN ('description','definition')
                GROUP BY owner_key
            ), sources AS (
                SELECT entity_id, ARRAY_AGG(DISTINCT resource ORDER BY resource) AS sources
                FROM (
                    SELECT child_entity_id AS entity_id, resource FROM {s}.entity_ontology_relation
                    UNION SELECT parent_entity_id, resource FROM {s}.entity_ontology_relation
                ) items GROUP BY entity_id
            ), children AS (
                SELECT parent_entity_id, COUNT(DISTINCT child_entity_id) AS n
                FROM {s}.entity_ontology_relation GROUP BY parent_entity_id
            ), parents AS (
                SELECT child_entity_id, COUNT(DISTINCT parent_entity_id) AS n
                FROM {s}.entity_ontology_relation GROUP BY child_entity_id
            )
            SELECT e.entity_id,
                CASE WHEN e.namespace='chebi' AND e.identifier !~* '^CHEBI:'
                    THEN 'CHEBI:' || e.identifier ELSE e.identifier END,
                CASE WHEN position(':' in e.identifier)>0
                    THEN lower(split_part(e.identifier,':',1)) ELSE e.namespace END,
                e.label, d.definition, COALESCE(a.synonyms,ARRAY[]::text[]),
                COALESCE(array_to_string(a.synonyms,' '),''),
                COALESCE(a.term_aliases,ARRAY[e.identifier]),
                COALESCE(a.identifiers_text,e.identifier), e.namespace, s.sources,
                COALESCE(c.n,0), COALESCE(p.n,0)
            FROM nodes n JOIN {s}.entity e ON e.entity_id=n.entity_id
            LEFT JOIN aliases a ON a.entity_key=e.entity_id
            LEFT JOIN definitions d ON d.owner_key=e.entity_id
            LEFT JOIN sources s ON s.entity_id=e.entity_id
            LEFT JOIN children c ON c.parent_entity_id=e.entity_id
            LEFT JOIN parents p ON p.child_entity_id=e.entity_id
        """).format(s=namespace)
        )
        cur.execute(
            sql.SQL("""
            CREATE OR REPLACE VIEW {s}.ontology_terms AS SELECT * FROM {s}.entity_ontology_term
        """).format(s=namespace)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS ontology_term_search_idx "
                "ON {}.entity_ontology_term (lower(term_id) text_pattern_ops)"
            ).format(namespace)
        )
