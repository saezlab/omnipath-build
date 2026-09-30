"""Small release-wide search projections over the exact published record keys."""

from __future__ import annotations

from psycopg import sql


def rebuild_derived(conn, schema: str) -> None:
    """Rebuild identifier lookup and endpoint counts in the caller's transaction.

    The endpoint-count SQL follows the legacy PostgreSQL derivation and counts
    each relation once per entity, including self-loops. These counts describe
    direct graph incidence; ontology expansion belongs to a later milestone.
    """
    namespace = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("""
                CREATE OR REPLACE VIEW {}.entity_identifier_lookup AS
                SELECT DISTINCT
                    entity_key AS entity_id, resource, version, ns, id, is_canonical, source
                FROM {}.identifiers
            """).format(namespace, namespace)
        )
        cur.execute(
            sql.SQL("""
                CREATE OR REPLACE VIEW {}.annotation_quantities AS
                SELECT annotation_id, resource, version, owner_kind, owner_key,
                    evidence_ordinal, ordinal, term, value, quantity,
                    source, dataset, scope,
                    (quantity ->> 'has_numeric_value')::double precision AS has_numeric_value,
                    quantity ->> 'has_unit' AS has_unit,
                    quantity ->> 'has_unit_prefix' AS has_unit_prefix,
                    quantity ->> 'has_binary_relation' AS has_binary_relation,
                    quantity ->> 'source_field' AS source_field,
                    quantity ->> 'comparator' AS comparator
                FROM {}.annotations
                WHERE quantity IS NOT NULL
            """).format(namespace, namespace)
        )
        cur.execute(
            sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.entity_relation_counts (
                    entity_id text PRIMARY KEY,
                    relation_count bigint NOT NULL,
                    search_count bigint NOT NULL
                )
            """).format(namespace)
        )
        cur.execute(sql.SQL("TRUNCATE {}.entity_relation_counts").format(namespace))
        cur.execute(
            sql.SQL("""
                INSERT INTO {}.entity_relation_counts (
                    entity_id, relation_count, search_count
                )
                WITH endpoint_counts AS (
                    SELECT entity_id, COUNT(DISTINCT relation_id)::bigint AS relation_count
                    FROM (
                        SELECT subject_entity_id AS entity_id, relation_id FROM {}.relation
                        UNION ALL
                        SELECT object_entity_id AS entity_id, relation_id FROM {}.relation
                    ) endpoints
                    GROUP BY entity_id
                )
                SELECT e.entity_id, COALESCE(ec.relation_count, 0),
                    COALESCE(ec.relation_count, 0)
                FROM {}.entity e
                LEFT JOIN endpoint_counts ec ON ec.entity_id = e.entity_id
            """).format(namespace, namespace, namespace, namespace)
        )
        cur.execute(
            sql.SQL("""
                CREATE INDEX IF NOT EXISTS entity_relation_counts_search_count_idx
                ON {}.entity_relation_counts (search_count DESC, entity_id ASC)
            """).format(namespace)
        )
