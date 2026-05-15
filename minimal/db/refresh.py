from __future__ import annotations

from psycopg2 import sql
import psycopg2.extensions


def delete_source_content(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    source: str,
) -> None:
    """Delete source-scoped evidence and orphaned resolved graph rows."""

    schema_id = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS _refresh_removed_relation')
        cur.execute(
            sql.SQL(
                """
                CREATE TEMP TABLE _refresh_removed_relation ON COMMIT DROP AS
                SELECT DISTINCT rer.relation_id
                FROM {}.relation_evidence_relation rer
                JOIN {}.relation_evidence re
                  ON re.relation_evidence_id = rer.relation_evidence_id
                WHERE re.source = %s
                """
            ).format(schema_id, schema_id),
            [source],
        )
        cur.execute('DROP TABLE IF EXISTS _refresh_removed_entity')
        cur.execute(
            sql.SQL(
                """
                CREATE TEMP TABLE _refresh_removed_entity ON COMMIT DROP AS
                SELECT DISTINCT r.entity_id
                FROM {}.entity_evidence_resolution r
                JOIN {}.entity_evidence ee
                  ON ee.entity_evidence_id = r.entity_evidence_id
                WHERE ee.source = %s
                  AND r.entity_id IS NOT NULL
                UNION
                SELECT DISTINCT re.subject_entity_id AS entity_id
                FROM {}.relation_evidence re
                WHERE re.source = %s
                  AND re.subject_entity_id IS NOT NULL
                UNION
                SELECT DISTINCT re.object_entity_id AS entity_id
                FROM {}.relation_evidence re
                WHERE re.source = %s
                  AND re.object_entity_id IS NOT NULL
                """
            ).format(schema_id, schema_id, schema_id, schema_id),
            [source, source, source],
        )
        cur.execute('DROP TABLE IF EXISTS _refresh_removed_identifier')
        cur.execute(
            sql.SQL(
                """
                CREATE TEMP TABLE _refresh_removed_identifier ON COMMIT DROP AS
                SELECT DISTINCT eei.identifier_id
                FROM {}.entity_evidence_identifier eei
                JOIN {}.entity_evidence ee
                  ON ee.entity_evidence_id = eei.entity_evidence_id
                WHERE ee.source = %s
                """
            ).format(schema_id, schema_id),
            [source],
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.annotation a
                USING {}.relation_evidence re
                WHERE a.relation_evidence_id = re.relation_evidence_id
                  AND re.source = %s
                """
            ).format(schema_id, schema_id),
            [source],
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.annotation a
                USING {}.entity_evidence ee
                WHERE a.entity_evidence_id = ee.entity_evidence_id
                  AND ee.source = %s
                """
            ).format(schema_id, schema_id),
            [source],
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.relation_evidence
                WHERE source = %s
                """
            ).format(schema_id),
            [source],
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.entity_evidence_identifier eei
                USING {}.entity_evidence ee
                WHERE eei.entity_evidence_id = ee.entity_evidence_id
                  AND ee.source = %s
                """
            ).format(schema_id, schema_id),
            [source],
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.identifier i
                USING _refresh_removed_identifier ri
                WHERE i.identifier_id = ri.identifier_id
                  AND NOT EXISTS (
                    SELECT 1
                    FROM {}.entity_evidence_identifier eei
                    WHERE eei.identifier_id = i.identifier_id
                  )
                """
            ).format(schema_id, schema_id)
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.entity_evidence
                WHERE source = %s
                """
            ).format(schema_id),
            [source],
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.relation r
                USING _refresh_removed_relation rr
                WHERE r.relation_id = rr.relation_id
                  AND NOT EXISTS (
                    SELECT 1
                    FROM {}.relation_evidence_relation rer
                    WHERE rer.relation_id = r.relation_id
                  )
                """
            ).format(schema_id, schema_id)
        )
        cur.execute(
            sql.SQL(
                """
                DELETE FROM {}.entity e
                USING _refresh_removed_entity re
                WHERE e.entity_id = re.entity_id
                  AND NOT EXISTS (
                    SELECT 1
                    FROM {}.entity_evidence_resolution r
                    WHERE r.entity_id = e.entity_id
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM {}.relation rel
                    WHERE rel.subject_entity_id = e.entity_id
                       OR rel.object_entity_id = e.entity_id
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM {}.annotation a
                    WHERE a.entity_id = e.entity_id
                  )
                """
            ).format(schema_id, schema_id, schema_id, schema_id)
        )
    conn.commit()
