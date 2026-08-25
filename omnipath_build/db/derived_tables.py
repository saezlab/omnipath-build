"""Derived query tables built from the canonical graph.

These tables are not primary evidence. They summarize canonical relations and
ontology-term entities into shapes that are cheaper for search, filtering, and
resource summaries. They are rebuilt after selected sources have been ingested
and canonicalized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import time

from psycopg2 import sql
import psycopg2.extensions

from omnipath_build.db.schema import _ensure_ontology_terms_table
from omnipath_build.shared_interaction_schema import (
    NEGATIVE_SIGN_ACCESSIONS,
    POSITIVE_SIGN_ACCESSIONS,
)
from pypath.internals.cv_terms import OntologyAnnotationCv, cv_term_label_accession

_logger = logging.getLogger(__name__)


ONTOLOGY_DEFINITION_TERM = cv_term_label_accession(OntologyAnnotationCv.DEFINITION)

@dataclass(frozen=True)
class DerivedTableStats:
    """Summary counts from derived table population."""

    entity_identifier_lookup: int = 0
    entity_relation_counts: int = 0
    ontology_terms: int = 0
    entity_ontology_terms: int = 0
    entity_source_count: int = 0
    interactions: InteractionDeriveStats | None = None


def rebuild_derived_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
    interactions: bool = True,
) -> DerivedTableStats:
    """Create and fully rebuild derived search/count tables.

    ``interactions`` also rebuilds the interaction projection.
    Pass ``interactions=False`` where the derive orchestration registers
    :func:`rebuild_interaction_tables` as a step of its own, so the projection
    runs once per build rather than twice.
    """

    started = time.perf_counter()
    with conn.cursor() as cur:
        _log(progress, 'create_tables', 'start', schema=schema)
        step_started = time.perf_counter()
        _create_derived_tables(cur, schema)
        _log(
            progress,
            'create_tables',
            'done',
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'entity_identifier_lookup', 'start')
        step_started = time.perf_counter()
        entity_identifier_lookup = _populate_entity_identifier_lookup(
            cur,
            schema,
        )
        _log(
            progress,
            'entity_identifier_lookup',
            'done',
            rows=entity_identifier_lookup,
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'entity_relation_counts', 'start')
        step_started = time.perf_counter()
        relation_counts = _populate_entity_relation_counts(cur, schema)
        _log(
            progress,
            'entity_relation_counts',
            'done',
            rows=relation_counts,
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'entity_source_count', 'start')
        step_started = time.perf_counter()
        entity_source_count = _populate_entity_source_count(cur, schema)
        _log(
            progress,
            'entity_source_count',
            'done',
            rows=entity_source_count,
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'entity_ontology_term', 'start')
        step_started = time.perf_counter()
        entity_ontology_terms = _populate_entity_ontology_terms(cur, schema)
        _log(
            progress,
            'entity_ontology_term',
            'done',
            rows=entity_ontology_terms,
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'ontology_terms', 'count_start')
        step_started = time.perf_counter()
        ontology_terms = _count_ontology_terms(cur, schema)
        _log(
            progress,
            'ontology_terms',
            'count_done',
            rows=ontology_terms,
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'indexes', 'start')
        step_started = time.perf_counter()
        _create_derived_indexes(cur, schema)
        _log(
            progress,
            'indexes',
            'done',
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )
    conn.commit()
    interaction_stats = (
        rebuild_interaction_tables(conn, schema=schema, progress=progress)
        if interactions
        else None
    )
    _log(progress, 'all', 'done', seconds=f'{time.perf_counter() - started:.3f}')
    return DerivedTableStats(
        entity_identifier_lookup=entity_identifier_lookup,
        entity_relation_counts=relation_counts,
        ontology_terms=ontology_terms,
        entity_ontology_terms=entity_ontology_terms,
        entity_source_count=entity_source_count,
        interactions=interaction_stats,
    )


def _log(progress: bool, step: str, event: str, **fields: object) -> None:
    """One structured derive-progress line.

    The ``step=… event=… key=value`` shape is a contract, not a preference:
    the sign-conflict figures and the per-step cost report are read back out of
    this output. Only the sink is the logger rather than ``print`` —
    pre-existing ``print`` call sites elsewhere in the build keep what they
    have.
    """
    if not progress:
        return
    details = ' '.join(f'{key}={value}' for key, value in fields.items())
    _logger.info(
        '[derive-tables] step=%s event=%s%s',
        step,
        event,
        f' {details}' if details else '',
    )


def _create_derived_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    cur.execute(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'entity_relation_counts'
          AND column_name = 'entity_id'
        """,
        [schema],
    )
    row = cur.fetchone()
    if row is not None and row[0] != 'uuid':
        cur.execute(
            sql.SQL('DROP TABLE {}.entity_relation_counts').format(schema_id)
        )
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = %s
            AND table_name = 'entity_identifier_lookup'
            AND column_name IN ('identifier', 'identifier_type_id')
        )
        """,
        [schema],
    )
    if bool(cur.fetchone()[0]):
        cur.execute(
            sql.SQL('DROP TABLE {}.entity_identifier_lookup').format(
                schema_id
            )
        )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_identifier_lookup (
              entity_id uuid NOT NULL,
              identifier_id uuid NOT NULL,
              PRIMARY KEY (entity_id, identifier_id)
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_relation_counts (
              entity_id uuid PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              relation_count bigint NOT NULL,
              ontology_annotated_entity_count bigint NOT NULL,
              ontology_annotated_relation_count bigint NOT NULL,
              search_count bigint NOT NULL
            )
            """
        ).format(schema_id, schema_id)
    )
    for column_name in (
        'ontology_annotated_entity_count',
        'ontology_annotated_relation_count',
        'search_count',
    ):
        cur.execute(
            sql.SQL(
                'ALTER TABLE {}.entity_relation_counts '
                'ADD COLUMN IF NOT EXISTS {} bigint NOT NULL DEFAULT 0'
            ).format(schema_id, sql.Identifier(column_name))
        )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_ontology_term (
              term_entity_id uuid NOT NULL
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              term_id text NOT NULL,
              ontology_prefix text,
              label text,
              definition text,
              synonyms text[] NOT NULL DEFAULT '{{}}'::text[],
              synonyms_text text NOT NULL DEFAULT '',
              term_aliases text[] NOT NULL DEFAULT '{{}}'::text[],
              identifiers_text text NOT NULL DEFAULT '',
              ontology_id text,
              sources text[] NOT NULL DEFAULT '{{}}'::text[],
              child_count bigint NOT NULL DEFAULT 0,
              PRIMARY KEY (term_entity_id, ontology_id)
            )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_source_count (
              entity_id uuid PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              source_count integer NOT NULL,
              source_list bigint[] NOT NULL
            )
            """
        ).format(schema_id, schema_id)
    )
    _ensure_ontology_terms_table(cur, schema)


def _populate_entity_identifier_lookup(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.entity_identifier_lookup').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_identifier_lookup (
              entity_id,
              identifier_id
            )
            SELECT DISTINCT entity_id, identifier_id
            FROM {}.entity_identifier
            """
        ).format(
            schema_id,
            schema_id,
        )
    )
    return int(cur.rowcount)


def _populate_entity_relation_counts(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.entity_relation_counts').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_relation_counts (
              entity_id,
              relation_count,
              ontology_annotated_entity_count,
              ontology_annotated_relation_count,
              search_count
            )
            WITH endpoint_counts AS (
              SELECT entity_id, COUNT(DISTINCT relation_id)::bigint AS relation_count
              FROM (
                SELECT subject_entity_id AS entity_id, relation_id
                FROM {}.relation
                UNION ALL
                SELECT object_entity_id AS entity_id, relation_id
                FROM {}.relation
              ) relation_endpoints
              GROUP BY entity_id
            )
            SELECT
              e.entity_id,
              COALESCE(endpoint_counts.relation_count, 0)::bigint,
              0::bigint,
              0::bigint,
              COALESCE(endpoint_counts.relation_count, 0)::bigint
            FROM {}.entity e
            LEFT JOIN endpoint_counts
              ON endpoint_counts.entity_id = e.entity_id
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )
    return int(cur.rowcount)


def _populate_entity_source_count(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    """Per real entity, the number and sorted set of contributing sources.

    Powers "items present in >= N resources" (coverage profile) and
    shared/unique splits without a full evidence scan. Excludes CV-term
    entities and unresolved resolutions (status 2).
    """
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.entity_source_count').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_source_count (
              entity_id,
              source_count,
              source_list
            )
            SELECT
              er.entity_id,
              COUNT(DISTINCT er.source_id)::integer,
              array_agg(DISTINCT er.source_id ORDER BY er.source_id)
            FROM {}.entity_evidence_resolution er
            JOIN {}.entity e
              ON e.entity_id = er.entity_id
            WHERE er.entity_id IS NOT NULL
              AND er.status_id <> 2
              AND e.entity_type_id IS DISTINCT FROM (
                SELECT entity_type_id
                FROM {}.vocab_entity_type
                WHERE name = 'Cv Term:OM:0012'
              )
            GROUP BY er.entity_id
            """
        ).format(schema_id, schema_id, schema_id, schema_id)
    )
    return int(cur.rowcount)


def _populate_entity_ontology_terms(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL('TRUNCATE {}.entity_ontology_term').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_ontology_term (
              term_entity_id,
              term_id,
              ontology_prefix,
              label,
              definition,
              synonyms,
              synonyms_text,
              term_aliases,
              identifiers_text,
              ontology_id,
              sources,
              child_count
            )
            WITH ontology_edge_entity AS MATERIALIZED (
              SELECT
                eor.source_id,
                eor.ontology_id,
                eor.subject_entity_id AS term_entity_id
              FROM {}.entity_ontology_relation eor
              UNION
              SELECT
                eor.source_id,
                eor.ontology_id,
                eor.object_entity_id AS term_entity_id
              FROM {}.entity_ontology_relation eor
            ),
            term_base AS MATERIALIZED (
              SELECT
                oee.term_entity_id,
                oee.ontology_id,
                e.canonical_identifier,
                cit.name AS canonical_identifier_type,
                ARRAY_AGG(DISTINCT ds.name ORDER BY ds.name) AS sources
              FROM ontology_edge_entity oee
              JOIN {}.entity e
                ON e.entity_id = oee.term_entity_id
              JOIN {}.vocab_identifier_type cit
                ON cit.identifier_type_id = e.canonical_identifier_type_id
              JOIN {}.data_source ds
                ON ds.source_id = oee.source_id
              GROUP BY
                oee.term_entity_id,
                oee.ontology_id,
                e.canonical_identifier,
                cit.name
            ),
            identifier_rows AS MATERIALIZED (
              SELECT DISTINCT
                tb.term_entity_id,
                it.name AS identifier_type,
                i.value
              FROM term_base tb
              JOIN {}.entity_identifier_lookup eil
                ON eil.entity_id = tb.term_entity_id
              JOIN {}.identifier_evidence i
                ON i.identifier_id = eil.identifier_id
              JOIN {}.vocab_identifier_type it
                ON it.identifier_type_id = i.identifier_type_id
              WHERE i.value IS NOT NULL
                AND i.value <> ''
            ),
            term_id_candidates AS MATERIALIZED (
              SELECT
                tb.term_entity_id,
                tb.canonical_identifier_type AS identifier_type,
                tb.canonical_identifier AS value,
                0 AS priority
              FROM term_base tb
              WHERE tb.canonical_identifier <> ''
                AND (
                  tb.canonical_identifier_type = 'Chebi:MI:0474'
                  OR lower(tb.canonical_identifier_type) LIKE '%cv term%'
                  OR lower(tb.canonical_identifier_type) LIKE '%reactome%'
                  OR lower(tb.canonical_identifier_type) LIKE '%wikipathways%'
                  OR tb.canonical_identifier
                     ~ '^[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]+$'
                )
              UNION ALL
              SELECT
                ir.term_entity_id,
                ir.identifier_type,
                ir.value,
                CASE
                  WHEN ir.identifier_type = 'Chebi:MI:0474' THEN 1
                  WHEN lower(ir.identifier_type) LIKE '%cv term%' THEN 2
                  WHEN lower(ir.identifier_type) LIKE '%reactome%' THEN 3
                  WHEN lower(ir.identifier_type) LIKE '%wikipathways%' THEN 4
                  WHEN ir.value
                       ~ '^[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]+$'
                    THEN 10
                  ELSE 50
                END AS priority
              FROM identifier_rows ir
              WHERE ir.identifier_type = 'Chebi:MI:0474'
                OR lower(ir.identifier_type) LIKE '%cv term%'
                OR lower(ir.identifier_type) LIKE '%reactome%'
                OR lower(ir.identifier_type) LIKE '%wikipathways%'
                OR ir.value
                   ~ '^[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]+$'
            ),
            selected_term_ids AS MATERIALIZED (
              SELECT DISTINCT ON (tic.term_entity_id)
                tic.term_entity_id,
                CASE
                  WHEN tic.identifier_type = 'Chebi:MI:0474'
                   AND tic.value !~* '^CHEBI:'
                    THEN 'CHEBI:' || tic.value
                  ELSE tic.value
                END AS term_id
              FROM term_id_candidates tic
              ORDER BY tic.term_entity_id, tic.priority, tic.value
            ),
            definition_candidates AS MATERIALIZED (
              SELECT DISTINCT
                tb.term_entity_id,
                CASE
                  WHEN it.name = 'Chebi:MI:0474'
                   AND ie.value !~* '^CHEBI:'
                    THEN 'CHEBI:' || ie.value
                  ELSE ie.value
                END AS term_id,
                a.value AS definition
              FROM term_base tb
              JOIN {}.entity_evidence_resolution eer
                ON eer.entity_id = tb.term_entity_id
              JOIN {}.entity_evidence_identifier eei
                ON eei.source_id = eer.source_id
               AND eei.entity_evidence_id = eer.entity_evidence_id
              JOIN {}.identifier_evidence ie
                ON ie.identifier_id = eei.identifier_id
              JOIN {}.vocab_identifier_type it
                ON it.identifier_type_id = ie.identifier_type_id
              JOIN {}.entity_evidence_annotation eea
                ON eea.source_id = eer.source_id
               AND eea.entity_evidence_id = eer.entity_evidence_id
              JOIN {}.annotation a
                ON a.annotation_key = eea.annotation_key
              WHERE a.term = {}
                AND a.value IS NOT NULL
                AND a.value <> ''
                AND (
                  it.name = 'Chebi:MI:0474'
                  OR lower(it.name) LIKE '%cv term%'
                  OR lower(it.name) LIKE '%reactome%'
                  OR lower(it.name) LIKE '%wikipathways%'
                  OR ie.value
                     ~ '^[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]+$'
                )
            ),
            term_definitions AS MATERIALIZED (
              SELECT
                sti.term_entity_id,
                COALESCE(
                  MIN(dc.definition)
                    FILTER (WHERE dc.term_id = sti.term_id),
                  MIN(dc.definition)
                ) AS definition
              FROM selected_term_ids sti
              LEFT JOIN definition_candidates dc
                ON dc.term_entity_id = sti.term_entity_id
              GROUP BY sti.term_entity_id
            ),
            term_labels AS MATERIALIZED (
              SELECT
                ir.term_entity_id,
                COALESCE(
                  MIN(ir.value) FILTER (WHERE ir.identifier_type = 'Name:OM:0202'),
                  MIN(ir.value) FILTER (WHERE ir.identifier_type = 'Synonym:OM:0203')
                ) AS label,
                COALESCE(
                  ARRAY_AGG(DISTINCT ir.value)
                    FILTER (WHERE ir.identifier_type = 'Synonym:OM:0203'),
                  '{{}}'::text[]
                ) AS synonyms,
                COALESCE(
                  STRING_AGG(DISTINCT ir.value, ' ')
                    FILTER (WHERE ir.identifier_type = 'Synonym:OM:0203'),
                  ''
                ) AS synonyms_text
              FROM identifier_rows ir
              GROUP BY ir.term_entity_id
            ),
            term_aliases AS MATERIALIZED (
              SELECT
                tic.term_entity_id,
                ARRAY_AGG(DISTINCT
                  CASE
                    WHEN tic.identifier_type = 'Chebi:MI:0474'
                     AND tic.value !~* '^CHEBI:'
                      THEN 'CHEBI:' || tic.value
                    ELSE tic.value
                  END
                ) AS term_aliases,
                STRING_AGG(DISTINCT
                  CASE
                    WHEN tic.identifier_type = 'Chebi:MI:0474'
                     AND tic.value !~* '^CHEBI:'
                      THEN tic.value || ' CHEBI:' || tic.value
                    ELSE tic.value
                  END,
                  ' '
                ) AS identifiers_text
              FROM term_id_candidates tic
              WHERE tic.priority < 50
              GROUP BY tic.term_entity_id
            ),
            child_counts AS MATERIALIZED (
              SELECT
                eor.object_entity_id AS term_entity_id,
                COUNT(DISTINCT eor.subject_entity_id) AS child_count
              FROM {}.entity_ontology_relation eor
              GROUP BY eor.object_entity_id
            )
            SELECT
              tb.term_entity_id,
              sti.term_id,
              lower(split_part(sti.term_id, ':', 1)) AS ontology_prefix,
              COALESCE(tl.label, sti.term_id) AS label,
              td.definition,
              COALESCE(tl.synonyms, '{{}}'::text[]) AS synonyms,
              COALESCE(tl.synonyms_text, '') AS synonyms_text,
              COALESCE(ta.term_aliases, ARRAY[sti.term_id]::text[]) AS term_aliases,
              COALESCE(ta.identifiers_text, sti.term_id) AS identifiers_text,
              tb.ontology_id,
              COALESCE(tb.sources, '{{}}'::text[]) AS sources,
              COALESCE(cc.child_count, 0)::bigint AS child_count
            FROM term_base tb
            JOIN selected_term_ids sti
              ON sti.term_entity_id = tb.term_entity_id
            LEFT JOIN term_labels tl
              ON tl.term_entity_id = tb.term_entity_id
            LEFT JOIN term_definitions td
              ON td.term_entity_id = tb.term_entity_id
            LEFT JOIN term_aliases ta
              ON ta.term_entity_id = tb.term_entity_id
            LEFT JOIN child_counts cc
              ON cc.term_entity_id = tb.term_entity_id
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            sql.Literal(ONTOLOGY_DEFINITION_TERM),
            schema_id,
        )
    )
    return int(cur.rowcount)


def _count_ontology_terms(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    cur.execute(
        sql.SQL('SELECT COUNT(*) FROM {}.ontology_terms').format(
            sql.Identifier(schema)
        )
    )
    return int(cur.fetchone()[0])


def _create_derived_indexes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    statements = [
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_identifier_lookup_identifier_id_idx
            ON {}.entity_identifier_lookup (identifier_id, entity_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_canonical_identifier_lower_idx
            ON {}.entity (lower(canonical_identifier))
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_canonical_identifier_lower_trgm_idx
            ON {}.entity USING GIN (lower(canonical_identifier) gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS identifier_evidence_value_lower_trgm_idx
            ON {}.identifier_evidence USING GIN (lower(value) gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_relation_counts_search_count_idx
            ON {}.entity_relation_counts (search_count DESC, entity_id ASC)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_term_id_idx
            ON {}.ontology_terms (term_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_ontology_id_idx
            ON {}.ontology_terms (ontology_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_ontology_prefix_idx
            ON {}.ontology_terms (ontology_prefix)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_sources_gin_idx
            ON {}.ontology_terms USING GIN (sources)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_term_id_trgm_idx
            ON {}.ontology_terms USING GIN (term_id gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_label_trgm_idx
            ON {}.ontology_terms USING GIN (label gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_definition_trgm_idx
            ON {}.ontology_terms USING GIN (definition gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS ontology_terms_synonyms_text_trgm_idx
            ON {}.ontology_terms USING GIN (synonyms_text gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_child_count_idx
            ON {}.entity_ontology_term (child_count DESC, term_id ASC)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_term_id_idx
            ON {}.entity_ontology_term (term_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_ontology_id_idx
            ON {}.entity_ontology_term (ontology_id)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_ontology_prefix_idx
            ON {}.entity_ontology_term (ontology_prefix)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_sources_gin_idx
            ON {}.entity_ontology_term USING GIN (sources)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_aliases_gin_idx
            ON {}.entity_ontology_term USING GIN (term_aliases)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_term_id_trgm_idx
            ON {}.entity_ontology_term USING GIN (term_id gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_label_trgm_idx
            ON {}.entity_ontology_term USING GIN (label gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_definition_trgm_idx
            ON {}.entity_ontology_term USING GIN (definition gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_ontology_prefix_trgm_idx
            ON {}.entity_ontology_term USING GIN (ontology_prefix gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_synonyms_text_trgm_idx
            ON {}.entity_ontology_term USING GIN (synonyms_text gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_ontology_term_identifiers_text_trgm_idx
            ON {}.entity_ontology_term USING GIN (identifiers_text gin_trgm_ops)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_source_count_source_count_idx
            ON {}.entity_source_count (source_count DESC, entity_id ASC)
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_source_count_source_list_gin_idx
            ON {}.entity_source_count USING GIN (source_list)
            """
        ).format(schema_id),
    ]
    for statement in statements:
        cur.execute(statement)


def rebuild_resource_overlap_summary(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> int:
    """Per (source_a, source_b, content_kind), the number of shared items.

    Computed from the precomputed ``source`` facet bitmaps (pg_roaringbitmap),
    so it is bounded (<= N*N per content kind, N = number of sources) and fast,
    replacing a quadratic evidence self-join. Each unordered source pair is
    stored once (source_a_id < source_b_id). content_kind is 'entity' or
    'relation'. MUST run AFTER the facet bitmaps are rebuilt.
    """
    schema_id = sql.Identifier(schema)
    started = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.resource_overlap_summary (
                  source_a_id bigint NOT NULL
                    REFERENCES {}.data_source(source_id) ON DELETE CASCADE,
                  source_b_id bigint NOT NULL
                    REFERENCES {}.data_source(source_id) ON DELETE CASCADE,
                  content_kind text NOT NULL,
                  overlap bigint NOT NULL,
                  PRIMARY KEY (source_a_id, source_b_id, content_kind)
                )
                """
            ).format(schema_id, schema_id, schema_id)
        )
        cur.execute(
            sql.SQL('TRUNCATE {}.resource_overlap_summary').format(schema_id)
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.resource_overlap_summary (
                  source_a_id, source_b_id, content_kind, overlap
                )
                WITH src AS (
                  SELECT facet_value, rb_or_agg(entity_bitmap) AS bm
                  FROM {}.facet_entity_bitmap
                  WHERE facet_name = 'source'
                  GROUP BY facet_value
                )
                SELECT da.source_id, db.source_id, 'entity',
                       rb_cardinality(rb_and(a.bm, b.bm))::bigint
                FROM src a
                JOIN src b ON a.facet_value < b.facet_value
                JOIN {}.data_source da ON da.name = a.facet_value
                JOIN {}.data_source db ON db.name = b.facet_value
                WHERE rb_cardinality(rb_and(a.bm, b.bm)) > 0
                """
            ).format(schema_id, schema_id, schema_id, schema_id)
        )
        entity_pairs = int(cur.rowcount)
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.resource_overlap_summary (
                  source_a_id, source_b_id, content_kind, overlap
                )
                WITH src AS (
                  SELECT facet_value, rb_or_agg(relation_bitmap) AS bm
                  FROM {}.facet_relation_bitmap
                  WHERE facet_name = 'source'
                  GROUP BY facet_value
                )
                SELECT da.source_id, db.source_id, 'relation',
                       rb_cardinality(rb_and(a.bm, b.bm))::bigint
                FROM src a
                JOIN src b ON a.facet_value < b.facet_value
                JOIN {}.data_source da ON da.name = a.facet_value
                JOIN {}.data_source db ON db.name = b.facet_value
                WHERE rb_cardinality(rb_and(a.bm, b.bm)) > 0
                """
            ).format(schema_id, schema_id, schema_id, schema_id)
        )
        relation_pairs = int(cur.rowcount)
        cur.execute(
            sql.SQL(
                """
                CREATE INDEX IF NOT EXISTS resource_overlap_summary_kind_overlap_idx
                ON {}.resource_overlap_summary (content_kind, overlap DESC)
                """
            ).format(schema_id)
        )
    conn.commit()
    _log(
        progress,
        'resource_overlap_summary',
        'done',
        entity_pairs=entity_pairs,
        relation_pairs=relation_pairs,
        seconds=f'{time.perf_counter() - started:.3f}',
    )
    return entity_pairs + relation_pairs


def sweep_staging_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> int:
    """Drop leftover ``*_source_<N>_staging`` tables from completed loads.

    Run at the END of derive, after the derived tables that depend on the
    staged data have succeeded, so a half-built database keeps its staging
    tables for retry. Only drops staging tables that are NOT currently attached
    as a partition (``pg_inherits``), so a live partition is never dropped.
    """
    schema_id = sql.Identifier(schema)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relkind = 'r'
              AND c.relname ~ '_source_[0-9]+_staging$'
              AND NOT EXISTS (
                SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid
              )
            ORDER BY c.relname
            """,
            [schema],
        )
        names = [row[0] for row in cur.fetchall()]
        for name in names:
            cur.execute(
                sql.SQL('DROP TABLE IF EXISTS {}.{}').format(
                    schema_id, sql.Identifier(name)
                )
            )
    conn.commit()
    _log(progress, 'sweep_staging', 'done', dropped=len(names))
    return len(names)


# --- The interaction projection ----------------------------------------------
#
# `interaction_fact_resource` is a denormalised precomputed projection over the
# canonical graph, not a new store of evidence: `relation`
# supplies the deduped endpoints, `relation_evidence` and its annotations the
# provenance, and `relation_evidence_relation` links the two. The evidence
# table's own endpoint columns are unusable for this — `object_entity_id` is
# NULL on every row and `subject_entity_id` set on about a fifth — so endpoints
# come from `relation` and never from the evidence rows.


# Participant-role terms, the first tier of the class derivation.
# Ligand-receptor is a property of the roles the two participants hold, not of
# the verb between them: all 45,768 ConnectomeDB2025 rows say `interacts_with`.
_LIGAND_TERM = 'Ligand:OM:7777'
_RECEPTOR_TERM = 'Receptor:OM:7778'
_TRANSPORT_SUBSTRATE_TERM = 'Transport Substrate:OM:0693'
_PARTICIPANT_ROLE_TERMS = (
    _LIGAND_TERM,
    _RECEPTOR_TERM,
    _TRANSPORT_SUBSTRATE_TERM,
)

# Interaction-level annotation, the second tier: what the resource says the
# interaction *is*. The precedence column orders the tier internally, so a
# relation annotated both allosteric and orthosteric resolves to `allosteric`.
_ANNOTATION_CLASS_TERMS = (
    ('Allosteric Modulator:OM:1005', 'allosteric', 1),
    ('Agonist:OM:1001', 'orthosteric', 2),
    ('Antagonist:OM:1002', 'orthosteric', 2),
    ('Activator:OM:1003', 'orthosteric', 2),
    ('Inhibitor:OM:1004', 'orthosteric', 2),
)

# The third tier is the predicate, read from
# `vocab_relation_predicate.interaction_class_id` — the curated map in
# `classify/interaction_class.yaml`. Its default (`other`) is treated as "the
# predicate has nothing to say", so the fallback shows through as the fallback
# rather than as a predicate answer.
_FALLBACK_CLASS = 'other'

# Direction, per predicate. A resource that records `A positively_regulates B`
# asserts a direction, so those rows carry `is_directed` true.
#
# Every other verb leaves `is_directed` NULL, symmetric ones included. A first
# pass wrote false for `interacts_with` and `associated_with`, reading a
# symmetric predicate as an assertion of undirectedness. Decided 2026-08-18 that
# it stays NULL: the predicate vocabulary is a coarse ontology layer that the
# resources did not choose per interaction — the same reason the interaction
# class is derived from the resource annotations rather than from the verb — so
# a symmetric verb is not the resource saying "this interaction has no
# direction". The rule is that an unasserted attribute never becomes an
# asserted false, and 8.2M rows rested on that reading.
_DIRECTED_PREDICATES = (
    'controls',
    'regulates',
    'positively_regulates',
    'negatively_regulates',
    'transports',
)

# Direction, per interaction class. A class whose definition names the two
# endpoints asymmetrically fixes their order, so every row of that class is
# directed however coarse its predicate is. `ligand_receptor` is such a class:
# the role evidence says which participant is the ligand and which the
# receptor, and the projection stores the ordered pair ligand first. Verified
# on dev4 across all five resources that publish the roles: of 70,921
# relation-and-resource pairs, 67,593 place the ligand on the subject and not
# one places the receptor there, so the order is a property of the projection
# rather than an accident of one resource. The remainder assert one role only
# and settle nothing either way.
#
# `transport` deliberately stays out. Its role term marks the *substrate*
# alone, on either endpoint, so the class leaves the order open and the
# `transports` predicate is what asserts it where a resource chose that verb.
#
# This is not the predicate rule in disguise. There the verb is an ingest-time
# label the resource did not choose per interaction, which is why a symmetric
# verb stays NULL (see above). Here the asymmetry is in the class the resource's
# own participant annotations produced, and it holds for every row that reaches
# the class.
_DIRECTED_CLASSES = ('ligand_receptor',)

# The direction expression, shared by the record key and the column it keys, so
# the two cannot drift apart.
_DIRECTION_SQL = """CASE
                WHEN predicate.name = ANY(%(directed)s) THEN true
                WHEN vic.name = ANY(%(directed_classes)s) THEN true
              END"""

# Reference and hot-column annotation terms.
_PUBMED_TERM = 'Pubmed:MI:0446'
_DOI_TERM = 'Doi:MI:0574'
_AFFINITY_TERMS = ('Ki:MI:0643', 'Ic50:MI:0641', 'Kd:MI:0646', 'Ec50:MI:0642')
_PCHEMBL_TERM = 'Pchembl Value:OM:0708'
_SCORE_TERM = 'Confidence Value:OM:1201'
_CURATION_TERM = 'Interaction Directness:OM:1216'

# A value is lifted into a numeric hot column only when it reads as a number;
# the CV carries free text in the same slot often enough to matter.
_NUMERIC_VALUE = r'^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$'


@dataclass(frozen=True)
class InteractionDeriveStats:
    """What the interaction projection produced, and what it cost.

    ``records`` counts ``interaction_fact_resource``, the projection's one fact
    output. There is no second count beside it: no collapse is materialised, so
    the derive writes the record and stops, and the manifest names that table
    apart from the step list because it is the number the build-cost ceiling is
    argued against.

    ``rows_by_class`` is the per-class row count every run reports: a class
    collapsing back to zero has to be visible in the build output, not
    discovered a phase later. ``sign_conflict`` measures how often both sign
    flags land on one row, and whether that is one resource asserting both or
    resources genuinely disagreeing.
    ``step_seconds`` carries the per-step wall clock the manifest splits, and
    ``deferral`` what running the load with its foreign keys and secondary
    indexes dropped bought and cost — the seconds saved against a recorded
    undeferred baseline, the drop, the restore, the revalidation, how many
    objects each covered, and whether the catalogue round trip closed. The
    manifest records it under ``interactions_deferral_cost``, where a field
    nobody measured stays ``null`` rather than becoming a zero.

    ``fallback_predicates`` breaks the fallback class down by the verb its
    relations arrived under. A per-class count alone cannot separate the two
    things ``other`` holds — the interactions no resource characterises, and
    the ones a resource characterises under a predicate no rule maps — so a
    whole class published under an unrecognised verb reads as more of the same
    large number. Broken down per predicate it reads as a verb with a class-sized
    row count beside it, which is what asks to be curated.

    ``source_count_histogram`` counts how many collapse keys carry each
    ``source_count``, returned for the build log. Its real consumer reads
    ``interaction_source_count_histogram`` from the database, because it is
    the api-service's guardrail and not this process.
    """

    interactions: int = 0
    parties: int = 0
    records: int = 0
    rows_by_class: dict[str, int] = field(default_factory=dict)
    fallback_predicates: dict[str, int] = field(default_factory=dict)
    sign_conflict: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0
    step_seconds: dict[str, float] = field(default_factory=dict)
    deferral: dict[str, object] = field(default_factory=dict)
    source_count_histogram: dict[int, int] = field(default_factory=dict)


def interaction_content_uuid_sql(
    participants: str,
    interaction_class: str,
) -> str:
    """SQL for the ``interaction`` header's content-addressed id.

    ``participants`` is any SQL expression yielding a uuid array,
    ``interaction_class`` one yielding the class slug. The payload is the class
    followed by the participant ids sorted as lowercase text, JSON-encoded — the
    same bytes the DuckDB macro ``interaction_content_uuid`` builds
    (``duckdb_load.py``), so the load side and the derive side mint the same
    uuid for the same content. Sorting is what makes the id endpoint-independent:
    A→B and B→A are two facts of one interaction.
    """
    return (
        "md5(to_json(ARRAY[{interaction_class}]::text[] || ("
        'SELECT coalesce('
        'array_agg(lower(participant::text) ORDER BY lower(participant::text)),'
        "ARRAY[]::text[]) FROM unnest({participants}) AS parts(participant)"
        '))::text)::uuid'
    ).format(
        interaction_class=interaction_class,
        participants=participants,
    )


def interaction_record_uuid_sql(
    *,
    subject_entity_id: str,
    object_entity_id: str,
    interaction_class: str,
    source: str,
    is_directed: str,
    is_stimulation: str,
    is_inhibition: str,
) -> str:
    """SQL for the ``interaction_fact_resource`` surrogate primary key.

    Every argument is a SQL expression. The payload is the **full key** of
    ``interaction_fact_resource`` — the ordered endpoints, the interaction
    class, the contributing resource and the assertion signature that resource
    states — JSON-encoded and hashed by the same ``md5(to_json(...))::uuid``
    scheme :func:`interaction_content_uuid_sql` and the DuckDB ``content_uuid``
    macro use. One scheme across the load side, the header and the record.

    Two choices in the payload are deliberate. The class and the resource enter
    as their **names** rather than as their surrogate ids, so the id is
    content-addressed all the way down and survives a vocabulary or
    ``data_source`` reload that renumbers them. And a NULL signature column
    encodes as JSON ``null``, which is distinct from the string ``"false"`` — a
    resource that is silent and a resource that asserts a negative are two keys,
    which the grain has to keep apart.

    The surrogate is not a convenience. The rest of the key is nullable and
    Postgres foreign keys default to ``MATCH SIMPLE``, under which a key with
    any NULL column is not checked at all, so the detail tables
    ``interaction_assay`` and ``interaction_ptm`` can only anchor here.
    """
    return (
        'md5(to_json(ARRAY['
        'lower({subject_entity_id}::text),'
        'lower({object_entity_id}::text),'
        '{interaction_class}::text,'
        '{source}::text,'
        '{is_directed}::text,'
        '{is_stimulation}::text,'
        '{is_inhibition}::text'
        ']::text[])::text)::uuid'
    ).format(
        subject_entity_id=subject_entity_id,
        object_entity_id=object_entity_id,
        interaction_class=interaction_class,
        source=source,
        is_directed=is_directed,
        is_stimulation=is_stimulation,
        is_inhibition=is_inhibition,
    )


#: The one fact table the projection writes. The collapse of it for a resource
#: scope is a query-time shape and has no table, so there is no second name
#: here and no column list for one.
INTERACTION_RECORD_TABLE = 'interaction_fact_resource'


#: The ``attributes`` GIN on each interaction table, by table name.
#: Both are ``jsonb_path_ops``: the long tail is queried with containment and
#: nothing else, and ``jsonb_path_ops`` indexes the hash of a whole path rather
#: than every key and every value separately, so it is the smaller and the
#: faster of the two operator classes for exactly that query. It cannot serve
#: key-existence (``?``, ``?|``, ``?&``), and that is the trade the gate is
#: about.
#:
#: **There is one of them because the derive stores one table.** The record
#: holds one row per contributing resource with that resource's long tail
#: unfolded, which is the larger column and the real sizing question; the merge
#: of it belonged to the materialisation, and the materialisation is gone.
INTERACTION_ATTRIBUTES_GIN_INDEXES = {
    INTERACTION_RECORD_TABLE: 'interaction_fact_resource_attributes_gin_idx',
}

#: The environment variable the benchmark toggles the gate with.
INTERACTION_ATTRIBUTES_GIN_ENV = 'OMNIPATH_BUILD_ATTRIBUTES_GIN'

#: Whether a build creates the index when nothing says otherwise. It is
#: ``False`` until the benchmark decides, because the gate is about build
#: cost and on-disk size, and a default that pays them before they are measured
#: would answer the question by shipping it.
INTERACTION_ATTRIBUTES_GIN_DEFAULT = False


#: The three tables the load writes, and the deferral therefore covers.
#: Ordered as the load writes them, which is also the order a reader of the
#: build log meets them in.
INTERACTION_LOADED_TABLES = (
    'interaction',
    'interaction_party',
    INTERACTION_RECORD_TABLE,
)

#: The environment variable that turns the deferral off, for the A/B arm the
#: saving is measured against. There is no reason to turn it off in a build.
INTERACTION_DEFER_ENV = 'OMNIPATH_BUILD_DEFER_INTERACTION_CONSTRAINTS'

#: Whether a build defers when nothing says otherwise. ``True``, because the
#: decision rests on a measurement — 709.7 s against 1,814.7 s, with the
#: catalogue on the far side identical to the catalogue on the near side — and
#: a default that did not take it would leave the measured design unshipped.
INTERACTION_DEFER_DEFAULT = True


def defer_constraints_enabled(override: bool | None = None) -> bool:
    """Whether this load drops its foreign keys and secondary indexes first.

    ``override`` wins when it is not ``None``, so a caller — a benchmark, the
    catalogue round-trip test — states the answer directly. Otherwise
    :data:`INTERACTION_DEFER_ENV` decides and an unset value falls back to
    :data:`INTERACTION_DEFER_DEFAULT`, the same shape
    :func:`attributes_gin_enabled` follows.
    """
    if override is not None:
        return bool(override)
    raw = os.environ.get(INTERACTION_DEFER_ENV)
    if raw is None:
        return INTERACTION_DEFER_DEFAULT
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def interaction_catalogue(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[str, dict[str, object]]:
    """The constraints and indexes the three loaded tables carry, right now.

    ``{'constraints': {name: definition}, 'indexes': {name: definition}}``,
    with every definition as Postgres renders it. Taken on both sides of the
    load so the step can say whether the round trip closed, which is what the
    deferral rests on: it is only a saving if the catalogue after it is the
    catalogue before it. A foreign key that came back ``NOT VALID`` renders
    differently — ``pg_get_constraintdef`` appends ``NOT VALID`` — so the
    comparison catches the failure that looks most like success.

    Only foreign and primary keys are read. Postgres 17 and later also list
    every ``NOT NULL`` in ``pg_constraint``, and those are column properties
    that no load can drop or restore.
    """
    catalogue: dict[str, dict[str, object]] = {}
    cur.execute(
        """
        SELECT con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = %s
          AND cls.relname = ANY(%s)
          AND con.contype IN ('f', 'p')
        """,
        [schema, list(INTERACTION_LOADED_TABLES)],
    )
    catalogue['constraints'] = dict(cur.fetchall())
    cur.execute(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s AND tablename = ANY(%s)
        """,
        [schema, list(INTERACTION_LOADED_TABLES)],
    )
    catalogue['indexes'] = dict(cur.fetchall())
    return catalogue


def _defer_interaction_constraints(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Drop the foreign keys and the secondary indexes, and say how to restore.

    Returns ``(constraints, indexes)``: the constraints as
    ``(table, name, definition)`` and the indexes as their ``CREATE INDEX``
    statements, both taken from the catalogue rather than from a list kept in
    step with ``schema.py`` by hand. An object added to the schema is therefore
    deferred by the next build without anybody remembering to add it here, and
    an object the restore cannot reproduce is one the catalogue could not
    describe.

    **The two primary keys stay.** The header insert deduplicates with
    ``ON CONFLICT (interaction_id) DO NOTHING``, which needs its unique index
    while the insert runs. Every index backing a constraint **on the same
    table** is left alone with it, which is what ``con.conrelid =
    idx.indrelid`` says — an index a *foreign* key merely points at is not
    backing a constraint on this table and is dropped like any other.

    The record's own unique key is not a constraint and does go, because the
    record's insert names no conflict target. It is rebuilt with the rest, and
    a load that produced a duplicate fails there rather than passing quietly:
    the rebuild is a ``CREATE UNIQUE INDEX`` inside the step's transaction, so
    the whole projection rolls back with it.

    Foreign keys go first: an index a key depends on cannot be dropped under
    it.
    """
    cur.execute(
        """
        SELECT cls.relname, con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = %s
          AND cls.relname = ANY(%s)
          AND con.contype = 'f'
        ORDER BY cls.relname, con.conname
        """,
        [schema, list(INTERACTION_LOADED_TABLES)],
    )
    constraints = [(table, name, definition) for table, name, definition in cur]
    cur.execute(
        """
        SELECT pg_get_indexdef(idx.indexrelid), i.relname
        FROM pg_index idx
        JOIN pg_class i ON i.oid = idx.indexrelid
        JOIN pg_class cls ON cls.oid = idx.indrelid
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = %s
          AND cls.relname = ANY(%s)
          AND NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            WHERE con.conindid = idx.indexrelid
              AND con.conrelid = idx.indrelid
          )
        ORDER BY i.relname
        """,
        [schema, list(INTERACTION_LOADED_TABLES)],
    )
    rows = cur.fetchall()
    indexes = [definition for definition, _name in rows]
    schema_id = sql.Identifier(schema)
    for table, name, _definition in constraints:
        cur.execute(
            sql.SQL('ALTER TABLE {}.{} DROP CONSTRAINT {}').format(
                schema_id,
                sql.Identifier(table),
                sql.Identifier(name),
            )
        )
    for _definition, name in rows:
        cur.execute(
            sql.SQL('DROP INDEX {}.{}').format(schema_id, sql.Identifier(name))
        )
    return constraints, indexes


def _restore_interaction_constraints(
    cur: psycopg2.extensions.cursor,
    schema: str,
    constraints: list[tuple[str, str, str]],
    indexes: list[str],
) -> tuple[float, float]:
    """Put them back, validated, and return ``(index seconds, key seconds)``.

    Indexes first, then the foreign keys, which is the order the drop ran in
    reversed.

    **The keys come back with a plain ``ADD CONSTRAINT``, and that is the whole
    point.** Postgres validates such a key with one set-based join over the
    table — 44.6 s for all 229.9 million row checks, against 726.3 s to fire
    the same checks one row at a time through the load. ``ADD ... NOT VALID``
    would return in no time and leave a constraint that describes only rows
    written after it, which is not the constraint the schema declares. The
    seconds are returned apart from the index build because that is the half a
    future change could quietly drop, and the manifest records it.
    """
    schema_id = sql.Identifier(schema)
    started = time.perf_counter()
    for definition in indexes:
        cur.execute(definition)
    restore_seconds = time.perf_counter() - started
    started = time.perf_counter()
    for table, name, definition in constraints:
        cur.execute(
            sql.SQL('ALTER TABLE {}.{} ADD CONSTRAINT {} {}').format(
                schema_id,
                sql.Identifier(table),
                sql.Identifier(name),
                sql.SQL(definition),
            )
        )
    revalidate_seconds = time.perf_counter() - started
    return restore_seconds, revalidate_seconds


def _previous_undeferred_load_seconds(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> float | None:
    """What the load cost on the last build that ran **without** the deferral.

    This is the baseline ``seconds_saved`` is measured against, and it cannot
    come from this run: a build runs one arm, not both. It comes from the
    manifest of the other arm, which records per-step seconds
    (``interactions_derive_cost``) beside whether that build deferred
    (``interactions_deferral_cost``). So the A/B is: run once with
    :data:`INTERACTION_DEFER_ENV` off, then run normally, and the second build
    reports what the first one cost it.

    ``None`` whenever the answer would be a guess — no manifest, no such
    column, no undeferred build recorded, or one whose ``partial_build`` flag
    differs from this one's, since a capped load and a full load are not each
    other's baseline. A missing baseline leaves ``seconds_saved`` unreported,
    which the manifest keeps distinct from a measured zero.
    """
    cur.execute('SAVEPOINT interaction_deferral_baseline')
    try:
        cur.execute(
            sql.SQL(
                """
                SELECT interactions_derive_cost, interactions_deferral_cost,
                       partial_build
                FROM {}.build_manifest
                """
            ).format(sql.Identifier(schema))
        )
        rows = cur.fetchall()
    except psycopg2.Error:
        cur.execute('ROLLBACK TO SAVEPOINT interaction_deferral_baseline')
        return None
    finally:
        cur.execute('RELEASE SAVEPOINT interaction_deferral_baseline')
    for derive_cost, deferral_cost, partial in rows:
        if not derive_cost or not deferral_cost:
            continue
        if deferral_cost.get('deferred') is not False:
            continue
        if bool(partial) != _is_capped_load():
            continue
        steps = {
            entry.get('step'): entry for entry in derive_cost.get('steps') or ()
        }
        seconds = [
            steps[step].get('seconds')
            for step in ('interaction_header', INTERACTION_RECORD_TABLE)
            if step in steps
        ]
        measured = [value for value in seconds if value is not None]
        if len(measured) == 2:
            return float(sum(measured))
    return None


def _is_capped_load() -> bool:
    """Whether this process is running a ``MAX_RECORDS``-capped build.

    Read from the environment, which is where the CLI's ``--max-records``
    default comes from, so the two agree unless a caller passes the flag and
    unsets the variable. It gates nothing but the choice of baseline.
    """
    raw = (os.environ.get('MAX_RECORDS') or '').strip()
    if not raw:
        return False
    try:
        return int(raw) > 0
    except ValueError:
        return False


def attributes_gin_enabled(override: bool | None = None) -> bool:
    """Whether this build indexes ``attributes`` on the interaction tables.

    ``override`` wins when it is not ``None``, so a caller — the benchmark,
    a test — states the answer directly. Otherwise the environment variable
    :data:`INTERACTION_ATTRIBUTES_GIN_ENV` decides, and an unset or unreadable
    value falls back to :data:`INTERACTION_ATTRIBUTES_GIN_DEFAULT`.
    """
    if override is not None:
        return bool(override)
    raw = os.environ.get(INTERACTION_ATTRIBUTES_GIN_ENV)
    if raw is None:
        return INTERACTION_ATTRIBUTES_GIN_DEFAULT
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def ensure_interaction_attributes_gin(
    cur: psycopg2.extensions.cursor,
    schema: str,
    *,
    enabled: bool,
) -> dict[str, float]:
    """Create or drop the record's ``attributes`` GIN index.

    Returns the wall seconds each index took, keyed by table name, so the
    benchmark and the build manifest can attribute the cost rather than watch
    it disappear into the projection's total. A dropped index reports the drop's
    seconds, which are near zero and are still reported rather than omitted.

    **The toggle drops as well as creates**, and it has to. The gate is a
    measurement of what these indexes cost, so a run with the toggle off must
    leave a table that carries none — otherwise the second half of an A/B pair
    measures the first half's index. The derive owns these two index names and
    nothing else creates them: ``schema.py`` deliberately leaves ``attributes``
    unindexed on the record and says so where the other GIN indexes are made.

    It is built **after** the table is filled, never maintained during the
    insert. A GIN maintained per row through a 14.7-million-row load pays the
    pending-list flush over and over; built once at the end it is a single
    sorted build that ``maintenance_work_mem`` and the parallel maintenance
    workers both apply to.
    """
    schema_id = sql.Identifier(schema)
    seconds: dict[str, float] = {}
    for table, index_name in INTERACTION_ATTRIBUTES_GIN_INDEXES.items():
        started = time.perf_counter()
        if enabled:
            cur.execute(
                sql.SQL(
                    'CREATE INDEX IF NOT EXISTS {} ON {}.{} '
                    'USING gin (attributes jsonb_path_ops)'
                ).format(
                    sql.Identifier(index_name),
                    schema_id,
                    sql.Identifier(table),
                )
            )
        else:
            cur.execute(
                sql.SQL('DROP INDEX IF EXISTS {}.{}').format(
                    schema_id,
                    sql.Identifier(index_name),
                )
            )
        seconds[table] = time.perf_counter() - started
    return seconds


def rebuild_interaction_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
    attributes_gin: bool | None = None,
    defer_constraints: bool | None = None,
) -> InteractionDeriveStats:
    """Project the canonical graph into the interaction model.

    Writes three tables. ``interaction`` is one endpoint-independent header per
    participant set and class, ``interaction_party`` its participants in role,
    and ``interaction_fact_resource`` the **record**. All three are pure
    projections of `relation`, so they are rebuilt whole rather than sliced:
    nothing in them is evidence a partial rebuild could lose.

    **The projection ends when the record lands.** ``interaction_fact_resource``
    holds one row per ordered ``(subject, object, class)``, contributing
    ``source_id`` **and** the assertion signature that resource states, which is
    what makes every summary on it decomposable. The collapse of it for a
    resource scope is what a **query** produces, at request time, for every
    scope including the empty one — no scope is precomputed here, the
    all-resources scope included, because a page-first fold costs the page
    rather than the scope and leaves a materialisation nothing to save.

    Runs after ``classify_interaction_class``, which fills the predicate→class
    map the third derivation tier reads. When that map is still empty the step
    seeds it itself, so the projection does not silently fall back to `other`
    just because the derive steps ran in the wrong order.

    ``defer_constraints`` states whether the load runs with the three tables'
    foreign keys and secondary indexes **dropped**, restoring them validated
    before the step ends. ``None`` leaves the answer to
    :func:`defer_constraints_enabled`, which says yes: the projection was
    measured at 709.7 s deferred against 1,814.7 s undeferred, with the largest
    step falling from 674.0 s to 272.2 s — sixty per cent of it was
    constraint and index maintenance rather than the work of building a header
    — and the tables landing 0.97 GiB smaller, because an index built once over
    sorted input is denser than the same index grown through fourteen million
    inserts. ``False`` is the A/B arm the saving is measured against.

    **The unconstrained window is visible to nobody.** ``DROP CONSTRAINT``,
    ``DROP INDEX``, ``ADD CONSTRAINT`` and ``CREATE INDEX`` are transactional
    in Postgres, and this step already refuses an autocommit connection, so the
    drop, the load and the restore commit together or roll back together. A
    failed build leaves the catalogue as it found it. ``CREATE INDEX
    CONCURRENTLY`` is the one form that could not join that transaction, and
    the deferral does not need it: the step holds an exclusive lock on tables
    it is rewriting anyway.

    ``attributes_gin`` states whether this build carries the ``attributes`` GIN
    on the record. ``None`` leaves the answer to
    :func:`attributes_gin_enabled`, which reads the environment and otherwise
    says no, because the index is a benchmark gate and its cost is the thing
    being measured. Either answer drops the index before the load and rebuilds
    it after it, so a leftover from the previous build neither slows the insert
    down nor makes an A/B pair measure the wrong thing.
    """
    if conn.autocommit:
        # The staging tables are placed by a `SET LOCAL search_path`, which an
        # autocommit connection discards after every statement — they would
        # land in whichever schema the session default names. Fail loudly
        # rather than build the projection into the wrong schema.
        raise ValueError(
            'rebuild_interaction_tables needs a transactional connection; '
            'the connection is in autocommit mode'
        )
    started = time.perf_counter()
    step_seconds: dict[str, float] = {}
    _ensure_interaction_class_map(conn, schema=schema, progress=progress)
    with conn.cursor() as cur:
        # The projection is one large grouped scan of the evidence link table;
        # give it room to hash rather than spilling. Parallel hash joins are off
        # for the duration: they allocate their hash tables in shared memory,
        # which is small in a container, and a 14-million-row join fills it and
        # fails the build with `DiskFull` before it fills any disk.
        cur.execute("SET LOCAL work_mem = '512MB'")
        cur.execute('SET LOCAL max_parallel_workers_per_gather = 0')
        # The staging tables are UNLOGGED rather than TEMP, and this puts them
        # in the build schema: at 14 million rows each they exhaust a session's
        # local buffers ("no empty local buffer available"), while unlogged
        # tables use the shared buffer pool and spill like any other. They are
        # dropped at the end of the step, and re-dropped at the start of the
        # next one, so a failed run leaves nothing behind for long.
        cur.execute(
            sql.SQL('SET LOCAL search_path = {}, pg_catalog').format(
                sql.Identifier(schema)
            )
        )
        classes = _interaction_class_ids(cur, schema)

        # Drop the `attributes` GIN index before anything writes to the
        # tables, and rebuild it at the end. `TRUNCATE` keeps a table's
        # indexes, so an index left in place from the previous build is
        # maintained row by row through the load. Measured on the record's
        # column: the same 14.7-million-row insert costs 6.6 s with no index,
        # 33.0 s with the GIN in place, and 6.6 s plus a 7.2 s build afterwards.
        # Maintaining it through the load therefore costs about three and a half
        # times what building it once does.
        ensure_interaction_attributes_gin(cur, schema, enabled=False)

        _log(progress, 'interaction_class_evidence', 'start')
        step_started = time.perf_counter()
        _stage_interaction_class_evidence(cur, schema, classes)
        step_seconds['interaction_class_evidence'] = (
            time.perf_counter() - step_started
        )
        _log(
            progress,
            'interaction_class_evidence',
            'done',
            seconds=f'{step_seconds["interaction_class_evidence"]:.3f}',
        )

        _log(progress, 'interaction_evidence_fold', 'start')
        step_started = time.perf_counter()
        _stage_interaction_record(cur, schema)
        step_seconds['interaction_evidence_fold'] = (
            time.perf_counter() - step_started
        )
        _log(
            progress,
            'interaction_evidence_fold',
            'done',
            seconds=f'{step_seconds["interaction_evidence_fold"]:.3f}',
        )

        # The deferral opens here, immediately before the first statement that
        # writes one of the three tables, and closes after the last one.
        # The staging steps above touch none of them, so nothing is loaded
        # through a constraint this drops.
        deferring = defer_constraints_enabled(defer_constraints)
        catalogue_before = interaction_catalogue(cur, schema)
        baseline_seconds = _previous_undeferred_load_seconds(cur, schema)
        deferred_constraints: list[tuple[str, str, str]] = []
        deferred_indexes: list[str] = []
        drop_seconds = 0.0
        if deferring:
            _log(progress, 'interaction_defer', 'start')
            step_started = time.perf_counter()
            deferred_constraints, deferred_indexes = (
                _defer_interaction_constraints(cur, schema)
            )
            drop_seconds = time.perf_counter() - step_started
            step_seconds['interaction_defer'] = drop_seconds
            _log(
                progress,
                'interaction_defer',
                'done',
                constraints=len(deferred_constraints),
                indexes=len(deferred_indexes),
                seconds=f'{drop_seconds:.3f}',
            )
        load_started = time.perf_counter()

        _log(progress, 'interaction_header', 'start')
        step_started = time.perf_counter()
        interactions, parties = _populate_interaction_header(cur, schema)
        step_seconds['interaction_header'] = time.perf_counter() - step_started
        _log(
            progress,
            'interaction_header',
            'done',
            rows=interactions,
            parties=parties,
            seconds=f'{step_seconds["interaction_header"]:.3f}',
        )

        _log(progress, 'interaction_fact_resource', 'start')
        step_started = time.perf_counter()
        records = _populate_interaction_fact_resource(cur, schema)
        # Fresh statistics on a table that was truncated and refilled in this
        # same transaction. Nothing inside this step reads the record any more
        # — the collapse pass that did was deleted — but the histogram below
        # groups it, the restored constraints validate against it, and a query
        # arriving after the commit plans against whatever this leaves behind.
        cur.execute(
            sql.SQL('ANALYZE {}.interaction_fact_resource').format(
                sql.Identifier(schema)
            )
        )
        step_seconds['interaction_fact_resource'] = (
            time.perf_counter() - step_started
        )
        _log(
            progress,
            'interaction_fact_resource',
            'done',
            rows=records,
            seconds=f'{step_seconds["interaction_fact_resource"]:.3f}',
        )

        load_seconds = time.perf_counter() - load_started

        # The restore, before anything reads the record again: the histogram
        # and the sign-conflict summary below both group it on
        # `interaction_fact_resource_collapse_idx`, and a query arriving after
        # the commit plans against whatever this leaves behind.
        restore_seconds = 0.0
        revalidate_seconds = 0.0
        if deferring:
            _log(progress, 'interaction_restore', 'start')
            step_started = time.perf_counter()
            restore_seconds, revalidate_seconds = (
                _restore_interaction_constraints(
                    cur,
                    schema,
                    deferred_constraints,
                    deferred_indexes,
                )
            )
            step_seconds['interaction_restore'] = (
                time.perf_counter() - step_started
            )
            _log(
                progress,
                'interaction_restore',
                'done',
                indexes=f'{restore_seconds:.3f}',
                revalidate=f'{revalidate_seconds:.3f}',
                seconds=f'{step_seconds["interaction_restore"]:.3f}',
            )
        # Asserted here rather than only in a test: the catalogue after
        # the step must equal the catalogue before it, and a build that cannot
        # say so has not earned the seconds it saved.
        catalogue_unchanged = (
            interaction_catalogue(cur, schema) == catalogue_before
        )
        deferral = {
            'deferred': deferring,
            'seconds_saved': (
                baseline_seconds - load_seconds
                if baseline_seconds is not None
                else None
            ),
            'load_seconds': load_seconds,
            'drop_seconds': drop_seconds if deferring else None,
            'restore_seconds': restore_seconds if deferring else None,
            'revalidate_seconds': revalidate_seconds if deferring else None,
            'constraints_deferred': len(deferred_constraints),
            'indexes_deferred': len(deferred_indexes),
            'catalogue_unchanged': catalogue_unchanged,
        }
        _log(
            progress,
            'interaction_defer',
            'catalogue',
            unchanged=catalogue_unchanged,
            constraints=len(catalogue_before['constraints']),
            indexes=len(catalogue_before['indexes']),
        )

        # The `attributes` GIN on the record, after it is filled. The
        # gate is off by default, so this is a second drop on an ordinary build
        # and a build on a benchmark one. Either way the table leaves this step
        # in the state the toggle names, which is what makes an A/B pair mean
        # anything.
        gin_enabled = attributes_gin_enabled(attributes_gin)
        _log(
            progress,
            'interaction_attributes_gin',
            'start',
            enabled=gin_enabled,
        )
        step_started = time.perf_counter()
        gin_seconds = ensure_interaction_attributes_gin(
            cur,
            schema,
            enabled=gin_enabled,
        )
        step_seconds['interaction_attributes_gin'] = (
            time.perf_counter() - step_started
        )
        _log(
            progress,
            'interaction_attributes_gin',
            'done',
            enabled=gin_enabled,
            seconds=f'{step_seconds["interaction_attributes_gin"]:.3f}',
            **{
                table: f'{value:.3f}'
                for table, value in sorted(gin_seconds.items())
            },
        )

        # The three measurements the build takes off the record it has just
        # written — the class distribution, the sign-conflict rate and the
        # `source_count` histogram — timed together, because they are one cost
        # centre: each is a grouped scan of the same table, and they are the
        # only folds left in the derive.
        _log(progress, 'interaction_measurements', 'start')
        measurements_started = time.perf_counter()
        rows_by_class = _interaction_rows_by_class(cur, schema)
        # The per-class counts go into the build output on every run, so a
        # class collapsing back to zero is visible here rather than a phase later.
        _log(
            progress,
            'interaction_fact_resource',
            'rows_by_class',
            **{name: count for name, count in sorted(rows_by_class.items())},
        )
        fallback_predicates = _interaction_fallback_predicates(
            cur,
            schema,
            classes[_FALLBACK_CLASS],
        )
        # And what the fallback is made of, so that a class arriving under a
        # verb no rule maps cannot hide inside the one large number. The verbs
        # go into one field rather than one field each: a predicate name is
        # free text from the loader, and a resource introducing one called
        # `event` or `step` would collide with the line's own keys.
        _log(
            progress,
            'interaction_fact_resource',
            'fallback_predicates',
            predicates=','.join(
                f'{name}:{count}'
                for name, count in fallback_predicates.items()
            ),
        )
        sign_conflict = _record_sign_conflict_summary(cur, schema)
        _log(progress, 'interaction_fact_resource', 'sign_conflict', **sign_conflict)
        source_count_histogram = _record_source_count_histogram(cur, schema)
        step_seconds['interaction_measurements'] = (
            time.perf_counter() - measurements_started
        )
        # Logged per run like the class counts: the distribution is what the
        # guardrail prices from, and a shift in it is what the next round of
        # cost benchmarking is waiting for.
        _log(
            progress,
            'interaction_source_count_histogram',
            'done',
            levels=len(source_count_histogram),
            seconds=f'{step_seconds["interaction_measurements"]:.3f}',
            **{
                f'n{level}': keys
                for level, keys in sorted(source_count_histogram.items())
            },
        )
        _drop_interaction_staging(cur)
    conn.commit()
    seconds = time.perf_counter() - started
    _log(
        progress,
        'interactions',
        'done',
        records=records,
        seconds=f'{seconds:.3f}',
    )
    return InteractionDeriveStats(
        interactions=interactions,
        parties=parties,
        records=records,
        deferral=deferral,
        rows_by_class=rows_by_class,
        fallback_predicates=fallback_predicates,
        source_count_histogram=source_count_histogram,
        sign_conflict=sign_conflict,
        seconds=seconds,
        step_seconds=step_seconds,
    )


def _ensure_interaction_class_map(
    conn: psycopg2.extensions.connection,
    *,
    schema: str,
    progress: bool,
) -> None:
    """Seed the class vocabulary and predicate map when they are still empty."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT
                  (SELECT count(*) FROM {}.vocab_interaction_class),
                  (SELECT count(*) FROM {}.vocab_relation_predicate
                   WHERE interaction_class_id IS NOT NULL)
                """
            ).format(sql.Identifier(schema), sql.Identifier(schema))
        )
        class_rows, mapped_predicates = cur.fetchone()
    if class_rows and mapped_predicates:
        return
    from omnipath_build.classify import classify_interaction_class

    _log(progress, 'interaction_class_map', 'seed')
    classify_interaction_class(conn, schema=schema)


def _interaction_class_ids(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[str, int]:
    cur.execute(
        sql.SQL(
            'SELECT name, interaction_class_id FROM {}.vocab_interaction_class'
        ).format(sql.Identifier(schema))
    )
    return dict(cur.fetchall())


def _stage_interaction_class_evidence(
    cur: psycopg2.extensions.cursor,
    schema: str,
    classes: dict[str, int],
) -> None:
    """Resolve every relation's interaction class, in tier precedence order.

    Three staging tables, one per tier that can override the fallback, and then
    ``_if_relation``: relation id, its endpoints, its class and whether its
    predicate asserts a direction.
    """
    schema_id = sql.Identifier(schema)

    # Tier 1, participant-role evidence. The role terms hang off the
    # entity-evidence grain, so they are read through the evidence endpoints of
    # `relation_evidence` — never through the `entity_evidence_resolution`
    # bridge, which is far too expensive at this cardinality.
    cur.execute('DROP TABLE IF EXISTS _if_role_evidence')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_role_evidence AS
            SELECT
              eea.source_id,
              eea.entity_evidence_id,
              bool_or(a.term = %s) AS is_ligand,
              bool_or(a.term = %s) AS is_receptor,
              bool_or(a.term = %s) AS is_transport_substrate
            FROM {}.entity_evidence_annotation eea
            JOIN {}.annotation a ON a.annotation_key = eea.annotation_key
            WHERE a.term = ANY(%s)
            GROUP BY eea.source_id, eea.entity_evidence_id
            """
        ).format(schema_id, schema_id),
        [
            _LIGAND_TERM,
            _RECEPTOR_TERM,
            _TRANSPORT_SUBSTRATE_TERM,
            list(_PARTICIPANT_ROLE_TERMS),
        ],
    )
    cur.execute(
        'CREATE INDEX _if_role_evidence_idx '
        'ON _if_role_evidence (source_id, entity_evidence_id)'
    )
    cur.execute('ANALYZE _if_role_evidence')

    cur.execute('DROP TABLE IF EXISTS _if_participant_class')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_participant_class AS
            SELECT
              rer.relation_id,
              bool_or(
                (subject_role.is_ligand AND object_role.is_receptor)
                OR (subject_role.is_receptor AND object_role.is_ligand)
              ) AS is_ligand_receptor,
              bool_or(
                coalesce(subject_role.is_transport_substrate, false)
                OR coalesce(object_role.is_transport_substrate, false)
              ) AS is_transport,
              bool_or(coalesce(subject_role.is_ligand, false)) AS subject_ligand,
              bool_or(coalesce(subject_role.is_receptor, false))
                AS subject_receptor,
              bool_or(coalesce(object_role.is_ligand, false)) AS object_ligand,
              bool_or(coalesce(object_role.is_receptor, false))
                AS object_receptor
            FROM {}.relation_evidence re
            JOIN {}.relation_evidence_relation rer
              ON rer.source_id = re.source_id
             AND rer.relation_evidence_id = re.relation_evidence_id
            LEFT JOIN _if_role_evidence subject_role
              ON subject_role.source_id = re.source_id
             AND subject_role.entity_evidence_id = re.subject_entity_evidence_id
            LEFT JOIN _if_role_evidence object_role
              ON object_role.source_id = re.source_id
             AND object_role.entity_evidence_id = re.object_entity_evidence_id
            WHERE subject_role.entity_evidence_id IS NOT NULL
               OR object_role.entity_evidence_id IS NOT NULL
            GROUP BY rer.relation_id
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        'CREATE INDEX _if_participant_class_idx '
        'ON _if_participant_class (relation_id)'
    )
    cur.execute('ANALYZE _if_participant_class')

    # Tier 2, interaction-level annotation.
    cur.execute('DROP TABLE IF EXISTS _if_annotation_class')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_annotation_class AS
            SELECT
              rer.relation_id,
              (array_agg(
                term_class.class_name ORDER BY term_class.precedence
              ))[1] AS class_name
            FROM {}.relation_evidence_annotation rea
            JOIN {}.annotation a ON a.annotation_key = rea.annotation_key
            JOIN unnest(%s::text[], %s::text[], %s::int[])
              AS term_class(term, class_name, precedence)
              ON term_class.term = a.term
            JOIN {}.relation_evidence_relation rer
              ON rer.source_id = rea.source_id
             AND rer.relation_evidence_id = rea.relation_evidence_id
            GROUP BY rer.relation_id
            """
        ).format(schema_id, schema_id, schema_id),
        [
            [term for term, _name, _precedence in _ANNOTATION_CLASS_TERMS],
            [name for _term, name, _precedence in _ANNOTATION_CLASS_TERMS],
            [
                precedence
                for _term, _name, precedence in _ANNOTATION_CLASS_TERMS
            ],
        ],
    )
    cur.execute(
        'CREATE INDEX _if_annotation_class_idx '
        'ON _if_annotation_class (relation_id)'
    )
    cur.execute('ANALYZE _if_annotation_class')

    # The precedence itself: participant roles, then interaction annotation,
    # then the predicate, then `other`.
    cur.execute('DROP TABLE IF EXISTS _if_relation')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_relation AS
            SELECT
              r.relation_id,
              r.subject_entity_id,
              r.object_entity_id,
              r.predicate_id,
              coalesce(
                CASE
                  WHEN participant.is_ligand_receptor THEN %(ligand_receptor)s
                  WHEN participant.is_transport THEN %(transport)s
                END,
                annotated.interaction_class_id,
                nullif(predicate.interaction_class_id, %(fallback)s),
                %(fallback)s
              )::smallint AS interaction_class_id,
              CASE
                WHEN predicate.name = ANY(%(directed)s) THEN true
              END AS asserts_directed,
              participant.subject_ligand,
              participant.subject_receptor,
              participant.object_ligand,
              participant.object_receptor
            FROM {}.relation r
            JOIN {}.vocab_relation_predicate predicate
              ON predicate.relation_predicate_id = r.predicate_id
            LEFT JOIN _if_participant_class participant
              ON participant.relation_id = r.relation_id
            LEFT JOIN (
              SELECT ac.relation_id, vic.interaction_class_id
              FROM _if_annotation_class ac
              JOIN {}.vocab_interaction_class vic ON vic.name = ac.class_name
            ) annotated ON annotated.relation_id = r.relation_id
            """
        ).format(schema_id, schema_id, schema_id),
        {
            'ligand_receptor': classes['ligand_receptor'],
            'transport': classes['transport'],
            'fallback': classes[_FALLBACK_CLASS],
            'directed': list(_DIRECTED_PREDICATES),
        },
    )
    cur.execute('CREATE INDEX _if_relation_idx ON _if_relation (relation_id)')
    cur.execute('ANALYZE _if_relation')


def _stage_interaction_record(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    """Stage the interaction record, one row per resource assertion.

    Four staging tables, and the shape of them is the per-resource record grain
    in miniature. ``_if_evidence_sign`` reads what each **evidence row**
    asserts about sign. ``_if_evidence`` puts that beside the ordered
    endpoints, the class and the resource, and mints the record's surrogate id.
    ``_if_record`` groups the evidence rows onto the
    ``interaction_fact_resource`` key — the endpoints, the class, the
    ``source_id`` **and** the assertion signature — aggregating only that
    key's own annotations. ``_if_fact`` stays at the triple grain, because the
    header and the participant table are keyed by the unordered endpoint pair
    and need the union over both directions.

    **The assertion is read per evidence row, not per canonical relation.**
    ``relation_evidence`` carries its own ``predicate_id``, and that is
    the resource's own statement; the canonical relation's predicate is the
    graph's summary of every resource that reported the pair. Reading direction
    off the relation made ``direction_source_count`` equal ``source_count``
    whenever the predicate was directed and zero otherwise — measured across all
    8,505 multi-resource signed rows on dev4 — so the column read as consensus
    in every case and could never say "one resource of twelve", which is the
    whole point of counting the resources that asserted a direction.

    Nothing here ever writes an asserted ``false``. A resource that publishes no
    sign leaves NULL on its own row, and a verb that says nothing about
    direction leaves NULL too — including the symmetric ones, which are the
    ingest layer's vocabulary rather than a resource's per-interaction claim
    (the 8,243,981 rows an August pass wrote ``false`` on were reverted for
    exactly that reason). Silence is never inherited from a neighbour either:
    the grouping key holds the resource, so ``fixture_res_c`` reporting a pair
    its neighbours signed keeps its own NULLs.
    """
    schema_id = sql.Identifier(schema)
    positive = sorted(POSITIVE_SIGN_ACCESSIONS)
    negative = sorted(NEGATIVE_SIGN_ACCESSIONS)

    # Sign, per **evidence row**. `nullif(..., false)` keeps an unasserted
    # sign unasserted: an evidence row carrying no sign annotation leaves the
    # column NULL, which is a different statement from an asserted false. One
    # resource asserting both signs under two predicates therefore arrives as
    # two rows here and stays two rows on the record, because the signature is
    # part of its key — the 7,803-row case measured on dev4, which is real
    # pharmacology rather than noise.
    cur.execute('DROP TABLE IF EXISTS _if_evidence_sign')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_evidence_sign AS
            SELECT
              rea.source_id,
              rea.relation_evidence_id,
              nullif(bool_or(a.term = ANY(%s)), false) AS is_stimulation,
              nullif(bool_or(a.term = ANY(%s)), false) AS is_inhibition
            FROM {}.relation_evidence_annotation rea
            JOIN {}.annotation a ON a.annotation_key = rea.annotation_key
            WHERE a.term = ANY(%s)
            GROUP BY 1, 2
            """
        ).format(schema_id, schema_id),
        [positive, negative, sorted(set(positive) | set(negative))],
    )
    cur.execute(
        'CREATE INDEX _if_evidence_sign_idx ON _if_evidence_sign '
        '(source_id, relation_evidence_id)'
    )
    cur.execute('ANALYZE _if_evidence_sign')

    # The record key, per evidence row, with its surrogate already minted. The
    # id is computed once here rather than at insert time so that the grouping
    # below can hash a single uuid instead of seven columns, two of which are
    # uuids themselves: `array_agg(DISTINCT ...)` forces a sorted aggregation,
    # and the sort key is what that costs.
    record_identity = interaction_record_uuid_sql(
        subject_entity_id='ir.subject_entity_id',
        object_entity_id='ir.object_entity_id',
        interaction_class='vic.name',
        source='ds.name',
        is_directed=_DIRECTION_SQL,
        is_stimulation='sign.is_stimulation',
        is_inhibition='sign.is_inhibition',
    )
    cur.execute('DROP TABLE IF EXISTS _if_evidence')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_evidence AS
            SELECT
              {identity} AS interaction_fact_resource_id,
              ir.subject_entity_id,
              ir.object_entity_id,
              ir.interaction_class_id,
              rer.source_id,
              rer.relation_evidence_id,
              {direction} AS is_directed,
              sign.is_stimulation,
              sign.is_inhibition
            FROM _if_relation ir
            JOIN {schema}.relation_evidence_relation rer
              ON rer.relation_id = ir.relation_id
            JOIN {schema}.relation_evidence re
              ON re.source_id = rer.source_id
             AND re.relation_evidence_id = rer.relation_evidence_id
            JOIN {schema}.vocab_relation_predicate predicate
              ON predicate.relation_predicate_id = re.predicate_id
            JOIN {schema}.vocab_interaction_class vic
              ON vic.interaction_class_id = ir.interaction_class_id
            JOIN {schema}.data_source ds ON ds.source_id = rer.source_id
            LEFT JOIN _if_evidence_sign sign
              ON sign.source_id = rer.source_id
             AND sign.relation_evidence_id = rer.relation_evidence_id
            """
        ).format(
            identity=sql.SQL(record_identity),
            direction=sql.SQL(_DIRECTION_SQL),
            schema=schema_id,
        ),
        {
            'directed': list(_DIRECTED_PREDICATES),
            'directed_classes': list(_DIRECTED_CLASSES),
        },
    )
    cur.execute(
        'CREATE INDEX _if_evidence_idx ON _if_evidence '
        '(source_id, relation_evidence_id)'
    )
    cur.execute('ANALYZE _if_evidence')

    # The record itself. Everything aggregated here is aggregated **within one
    # resource's assertion**, which is the whole point of the grain: a
    # reference, an affinity or a curation flag belongs to the resource that
    # published it, so a scoped collapse can recompute the summary from the
    # rows the scope kept instead of reading numbers folded over resources the
    # caller excluded.
    cur.execute('DROP TABLE IF EXISTS _if_record_annotation')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_record_annotation AS
            SELECT
              ev.interaction_fact_resource_id,
              array_agg(DISTINCT ann.value) FILTER (
                WHERE ann.term = %(pubmed)s
              ) AS reference_pubmed_ids,
              array_agg(DISTINCT ann.value) FILTER (
                WHERE ann.term = %(doi)s
              ) AS reference_dois,
              min(ann.value::double precision) FILTER (
                WHERE ann.term = ANY(%(affinity)s)
              ) AS affinity,
              max(ann.value::double precision) FILTER (
                WHERE ann.term = %(pchembl)s
              ) AS pchembl,
              max(ann.value::double precision) FILTER (
                WHERE ann.term = %(score)s
              ) AS score,
              array_agg(DISTINCT ann.value) FILTER (
                WHERE ann.term = %(curation)s
              ) AS curation_flags
            FROM {}.relation_evidence_annotation rea
            JOIN {}.annotation ann
              ON ann.annotation_key = rea.annotation_key
            JOIN _if_evidence ev
              ON ev.source_id = rea.source_id
             AND ev.relation_evidence_id = rea.relation_evidence_id
            WHERE ann.value IS NOT NULL
              AND ann.value <> ''
              AND (
                ann.term IN (%(pubmed)s, %(doi)s, %(curation)s)
                OR (
                  ann.term = ANY(%(numeric_terms)s)
                  AND ann.value ~ %(numeric)s
                )
              )
            GROUP BY 1
            """
        ).format(schema_id, schema_id),
        {
            'pubmed': _PUBMED_TERM,
            'doi': _DOI_TERM,
            'affinity': list(_AFFINITY_TERMS),
            'pchembl': _PCHEMBL_TERM,
            'score': _SCORE_TERM,
            'curation': _CURATION_TERM,
            'numeric_terms': [
                *_AFFINITY_TERMS,
                _PCHEMBL_TERM,
                _SCORE_TERM,
            ],
            'numeric': _NUMERIC_VALUE,
        },
    )
    cur.execute(
        'CREATE INDEX _if_record_annotation_idx ON _if_record_annotation '
        '(interaction_fact_resource_id)'
    )
    cur.execute('ANALYZE _if_record_annotation')

    # One row per record key. The distinct step is a plain grouped scan — the
    # surrogate determines the key, so grouping by it and carrying the key
    # columns along costs a hash rather than the sort the annotation
    # aggregation above needs.
    cur.execute('DROP TABLE IF EXISTS _if_record')
    cur.execute(
        """
        CREATE UNLOGGED TABLE _if_record AS
        SELECT
          key.interaction_fact_resource_id,
          key.subject_entity_id,
          key.object_entity_id,
          key.interaction_class_id,
          key.source_id,
          key.is_directed,
          key.is_stimulation,
          key.is_inhibition,
          annotation.reference_pubmed_ids,
          annotation.reference_dois,
          annotation.affinity,
          annotation.pchembl,
          annotation.score,
          annotation.curation_flags
        FROM (
          SELECT
            interaction_fact_resource_id,
            subject_entity_id,
            object_entity_id,
            interaction_class_id,
            source_id,
            is_directed,
            is_stimulation,
            is_inhibition
          FROM _if_evidence
          GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
        ) key
        LEFT JOIN _if_record_annotation annotation
          ON annotation.interaction_fact_resource_id
               = key.interaction_fact_resource_id
        """
    )
    cur.execute(
        'CREATE INDEX _if_record_idx ON _if_record '
        '(subject_entity_id, object_entity_id, interaction_class_id)'
    )
    cur.execute('ANALYZE _if_record')

    # The triple grain, for the header and the participant table alone. Those
    # two are keyed by the **unordered** endpoint pair, so they need the union
    # over both directions and over every resource, which the record does not
    # carry. The join to the evidence link is left-outer so a canonical
    # relation with no evidence still reaches a header rather than disappearing
    # from the graph; it reaches no fact row, because a record row without a
    # contributing resource is not a thing the grain can express. Measured on
    # dev4: no such relation exists.
    cur.execute('DROP TABLE IF EXISTS _if_fact')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_fact AS
            SELECT
              ir.subject_entity_id,
              ir.object_entity_id,
              ir.interaction_class_id,
              array_remove(array_agg(DISTINCT ds.name), NULL) AS sources,
              bool_or(coalesce(ir.subject_ligand, false)) AS subject_ligand,
              bool_or(coalesce(ir.subject_receptor, false))
                AS subject_receptor,
              bool_or(coalesce(ir.object_ligand, false)) AS object_ligand,
              bool_or(coalesce(ir.object_receptor, false)) AS object_receptor
            FROM _if_relation ir
            LEFT JOIN {}.relation_evidence_relation rer
              ON rer.relation_id = ir.relation_id
            LEFT JOIN {}.data_source ds ON ds.source_id = rer.source_id
            GROUP BY 1, 2, 3
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        'CREATE INDEX _if_fact_idx ON _if_fact '
        '(subject_entity_id, object_entity_id, interaction_class_id)'
    )
    cur.execute('ANALYZE _if_fact')



def _populate_interaction_header(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> tuple[int, int]:
    """Write ``interaction`` and ``interaction_party``.

    The header is endpoint-independent: its participants are the unordered set
    of the fact row's endpoints, so both directions of a pair share one header
    and a caller can reach the interaction from either side. A participant's
    role records how it appears across the contributing facts — ``subject``,
    ``object``, or ``member`` when it appears as both — and ``role_flag`` carries
    the ligand/receptor role that the class derivation's first tier read.
    """
    schema_id = sql.Identifier(schema)

    cur.execute('DROP TABLE IF EXISTS _if_party')
    cur.execute(
        """
        CREATE UNLOGGED TABLE _if_party AS
        WITH endpoint AS (
          SELECT
            least(f.subject_entity_id, f.object_entity_id) AS entity_low,
            greatest(f.subject_entity_id, f.object_entity_id) AS entity_high,
            f.interaction_class_id,
            side.entity_id,
            side.as_subject,
            side.as_object,
            side.is_ligand,
            side.is_receptor
          FROM _if_fact f
          CROSS JOIN LATERAL (
            VALUES
              (
                f.subject_entity_id, true, false,
                f.subject_ligand, f.subject_receptor
              ),
              (
                f.object_entity_id, false, true,
                f.object_ligand, f.object_receptor
              )
          ) AS side(entity_id, as_subject, as_object, is_ligand, is_receptor)
        )
        SELECT
          entity_low,
          entity_high,
          interaction_class_id,
          entity_id,
          bool_or(as_subject) AS as_subject,
          bool_or(as_object) AS as_object,
          bool_or(is_ligand) AS is_ligand,
          bool_or(is_receptor) AS is_receptor
        FROM endpoint
        GROUP BY entity_low, entity_high, interaction_class_id, entity_id
        """
    )
    cur.execute(
        'CREATE INDEX _if_party_idx ON _if_party '
        '(entity_low, entity_high, interaction_class_id)'
    )
    cur.execute('ANALYZE _if_party')

    identity = interaction_content_uuid_sql(
        participants='grouped.participants',
        interaction_class='vic.name',
    )
    cur.execute('DROP TABLE IF EXISTS _if_header')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_header AS
            SELECT
              grouped.entity_low,
              grouped.entity_high,
              grouped.interaction_class_id,
              grouped.arity,
              {identity} AS interaction_id
            FROM (
              SELECT
                entity_low,
                entity_high,
                interaction_class_id,
                array_agg(entity_id) AS participants,
                count(*)::smallint AS arity
              FROM _if_party
              GROUP BY entity_low, entity_high, interaction_class_id
            ) grouped
            JOIN {schema}.vocab_interaction_class vic
              ON vic.interaction_class_id = grouped.interaction_class_id
            """
        ).format(identity=sql.SQL(identity), schema=schema_id)
    )
    cur.execute(
        'CREATE INDEX _if_header_idx ON _if_header '
        '(entity_low, entity_high, interaction_class_id)'
    )
    cur.execute('ANALYZE _if_header')

    # The three tables are projections, so they are rebuilt whole. Truncating
    # them together keeps the header's dependants from tripping over the FK:
    # `TRUNCATE` refuses when a table outside the statement references one
    # inside it, whatever the row counts are, so `interaction_fact_resource`
    # has to be named here from the moment it carries a key to `interaction`,
    # and not only once the derive starts filling it. The removed
    # materialisation is not named because it no longer exists —
    # `_drop_legacy_interaction_fact_combined` clears one an older database
    # still carries, and it has to, since a dependant this statement does not
    # name blocks the truncate whatever the row counts are.
    cur.execute(
        sql.SQL(
            'TRUNCATE {}.interaction_fact_resource, {}.interaction_party, '
            '{}.interaction'
        ).format(schema_id, schema_id, schema_id)
    )
    # The header's provenance is the union over both directions of the pair, so
    # it is aggregated once here rather than looked up per header: a correlated
    # subquery over 14 million headers has no index to stand on and does not
    # finish.
    cur.execute('DROP TABLE IF EXISTS _if_header_source')
    cur.execute(
        """
        CREATE UNLOGGED TABLE _if_header_source AS
        SELECT
          least(f.subject_entity_id, f.object_entity_id) AS entity_low,
          greatest(f.subject_entity_id, f.object_entity_id) AS entity_high,
          f.interaction_class_id,
          array_agg(DISTINCT contributor.source) AS sources
        FROM _if_fact f
        CROSS JOIN LATERAL unnest(f.sources) AS contributor(source)
        GROUP BY 1, 2, 3
        """
    )
    cur.execute(
        'CREATE INDEX _if_header_source_idx ON _if_header_source '
        '(entity_low, entity_high, interaction_class_id)'
    )
    cur.execute('ANALYZE _if_header_source')
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.interaction
              (interaction_id, interaction_class_id, arity, sources)
            SELECT
              h.interaction_id,
              h.interaction_class_id,
              h.arity,
              contributor.sources
            FROM _if_header h
            LEFT JOIN _if_header_source contributor
              ON contributor.entity_low = h.entity_low
             AND contributor.entity_high = h.entity_high
             AND contributor.interaction_class_id = h.interaction_class_id
            ON CONFLICT (interaction_id) DO NOTHING
            """
        ).format(schema_id)
    )
    interactions = int(cur.rowcount)
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.interaction_party
              (interaction_id, entity_id, role_id, side, ordinal, organism,
               role_flag)
            SELECT
              h.interaction_id,
              p.entity_id,
              role.relation_role_id,
              CASE WHEN p.entity_id = p.entity_low THEN 1 ELSE 2 END::smallint,
              CASE WHEN p.entity_id = p.entity_low THEN 1 ELSE 2 END::smallint,
              e.taxonomy_id,
              CASE
                WHEN p.is_ligand THEN 1::smallint
                WHEN p.is_receptor THEN 2::smallint
              END
            FROM _if_party p
            JOIN _if_header h
              ON h.entity_low = p.entity_low
             AND h.entity_high = p.entity_high
             AND h.interaction_class_id = p.interaction_class_id
            JOIN {}.vocab_relation_role role
              ON role.name = CASE
                WHEN p.as_subject AND p.as_object THEN 'member'
                WHEN p.as_subject THEN 'subject'
                ELSE 'object'
              END
            LEFT JOIN {}.entity e ON e.entity_id = p.entity_id
            """
        ).format(schema_id, schema_id, schema_id)
    )
    parties = int(cur.rowcount)
    return interactions, parties


def _populate_interaction_fact_resource(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    """Write ``interaction_fact_resource``, the interaction record.

    The staged key goes in as it stands, with its surrogate already minted, the
    two organisms read off the endpoints and the header id joined from
    ``_if_header``. Joining the header rather than recomputing it is what keeps
    the foreign key satisfiable by construction: the record is written after the
    header, and every record row therefore points at a header row that exists.

    ``attributes`` stays NULL. The long tail is gated on the benchmark that
    prices the hot-column split against the JSONB store, and ``dataset_tags``
    belongs to the preset registry; neither is a value this step has.
    """
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {schema}.interaction_fact_resource (
              interaction_fact_resource_id,
              subject_entity_id, object_entity_id, interaction_class_id,
              source_id,
              is_directed, is_stimulation, is_inhibition,
              subject_organism, object_organism,
              affinity, pchembl, score,
              curation_flags, reference_pubmed_ids, reference_dois,
              attributes, interaction_id
            )
            SELECT
              rec.interaction_fact_resource_id,
              rec.subject_entity_id,
              rec.object_entity_id,
              rec.interaction_class_id,
              rec.source_id,
              rec.is_directed,
              rec.is_stimulation,
              rec.is_inhibition,
              subject_entity.taxonomy_id,
              object_entity.taxonomy_id,
              rec.affinity,
              rec.pchembl,
              rec.score,
              rec.curation_flags,
              rec.reference_pubmed_ids,
              rec.reference_dois,
              NULL::jsonb,
              h.interaction_id
            FROM _if_record rec
            JOIN _if_header h
              ON h.entity_low
                   = least(rec.subject_entity_id, rec.object_entity_id)
             AND h.entity_high
                   = greatest(rec.subject_entity_id, rec.object_entity_id)
             AND h.interaction_class_id = rec.interaction_class_id
            LEFT JOIN {schema}.entity subject_entity
              ON subject_entity.entity_id = rec.subject_entity_id
            LEFT JOIN {schema}.entity object_entity
              ON object_entity.entity_id = rec.object_entity_id
            """
        ).format(schema=schema_id)
    )
    return int(cur.rowcount)


def _interaction_rows_by_class(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[str, int]:
    """Record rows per interaction class, every class named even at zero.

    Every run reports this, so that a class collapsing back to zero is visible
    in the build output rather than discovered a phase later. The count is
    taken at the **record** grain — one row per contributing resource —
    because that is the only grain the build stores. It therefore runs a little
    above the collapsed counts, by the same 2.7 per cent the record runs above
    the fold, and the shape of the distribution is what the check is about.
    """
    cur.execute(
        sql.SQL(
            """
            SELECT vic.name, count(r.*)::bigint
            FROM {}.vocab_interaction_class vic
            LEFT JOIN {}.interaction_fact_resource r
              ON r.interaction_class_id = vic.interaction_class_id
            GROUP BY vic.name
            ORDER BY vic.name
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
    )
    return {name: int(count) for name, count in cur.fetchall()}


def _interaction_fallback_predicates(
    cur: psycopg2.extensions.cursor,
    schema: str,
    fallback_class_id: int,
) -> dict[str, int]:
    """What the fallback class is made of, by the verb its relations arrived under.

    ``other`` is a real class and also the place every relation lands that no
    rule characterises, and the per-class counts cannot tell those two apart.
    A resource publishing an entire class under a predicate the curated map has
    no entry for therefore shows up as nothing at all: its rows join the
    largest number in the report and the class it belongs to reads as empty.
    That is not hypothetical — miRBase's 53,316 maturation relations sat in the
    fallback for exactly this reason, under a predicate stored as a bare
    accession while the rule was keyed on the label.

    Counted per **relation** rather than per record row, because the question
    it answers is about the verb the graph holds and the map that reads it, not
    about how many resources reported each edge. Read off the staging table the
    class derivation already built, so it costs one grouped scan of a column
    that is in memory.
    """
    cur.execute(
        sql.SQL(
            """
            SELECT predicate.name, count(*)::bigint
            FROM _if_relation ir
            JOIN {}.vocab_relation_predicate predicate
              ON predicate.relation_predicate_id = ir.predicate_id
            WHERE ir.interaction_class_id = %s
            GROUP BY 1
            ORDER BY 2 DESC, 1
            """
        ).format(sql.Identifier(schema)),
        [fallback_class_id],
    )
    return {name: int(count) for name, count in cur.fetchall()}


def _record_source_count_histogram(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[int, int]:
    """Record how many collapse keys carry each ``source_count``.

    Nine rows on dev4, one per observed level from 1 to 9, each with the number
    of keys at it. Returned as ``{source_count: keys}`` and stored in
    ``interaction_source_count_histogram``, because the consumer is the
    api-service's guardrail and it reads the database rather than this
    build's return value.

    **What it is for.** A ``HAVING`` filter on a folded value costs the page
    size divided by the selectivity, so the guardrail can price a request
    before running it — but only if it knows the selectivity. This is that
    number. It is nine rows rather than a statistic worth estimating, and a
    grouped scan of a table the step has just written is the cheapest moment in
    the whole cycle to take it.

    **It is an estimator, not a gate.** Measured on dev4: `source_count >= 2`
    returns a page in 1.354 ms, `>= 3` in 7.781 ms and `>= 5` in 379 ms, every
    one streaming through `GroupAggregate` and every one well inside the
    one-second interactive latency target. So the histogram tells a caller what
    a request costs, and today the answer is always "affordable". It becomes a
    gate when `interaction_assay` multiplies the grain, and the cost benchmark
    is re-run against it then.

    The level is `count(DISTINCT source_id)` per key and never `count(*)`: a
    resource asserting two signatures for the same endpoints keeps two record
    rows, and it is still one resource contributing.
    """
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.interaction_source_count_histogram (
              source_count integer PRIMARY KEY,
              keys bigint NOT NULL,
              measured_at timestamptz NOT NULL DEFAULT now()
            )
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL('TRUNCATE {}.interaction_source_count_histogram').format(
            schema_id
        )
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.interaction_source_count_histogram
              (source_count, keys)
            SELECT source_count, count(*)::bigint
            FROM (
              SELECT count(DISTINCT source_id)::int AS source_count
              FROM {}.interaction_fact_resource
              GROUP BY
                subject_entity_id, object_entity_id, interaction_class_id
            ) folded
            GROUP BY source_count
            ORDER BY source_count
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            'SELECT source_count, keys '
            'FROM {}.interaction_source_count_histogram '
            'ORDER BY source_count'
        ).format(schema_id)
    )
    return {int(level): int(keys) for level, keys in cur.fetchall()}


def _record_sign_conflict_summary(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[str, float]:
    """Measure and store the sign-conflict rate.

    The rate is set by the **grain**, not by the biology: merging the four
    predicates that share the `signaling` class flattens an agonist and an
    antagonist relation between the same endpoints into one row. It was 2.0 per
    cent of signed rows on dev4 at the old class grain and is under-sampled, so
    it is re-measured on every build rather than trusted once. A figure
    substantially above ~2 per cent reopens whether the sign-bearing predicate
    belongs in the fact-table key.

    Under the per-resource record grain the two halves of the split are
    readable rather than inferred: one resource asserting both signs keeps
    **two** record rows, so the split asks the record which resources asserted
    what instead of carrying a `single_resource_conflict` flag through a fold.

    **Both halves fold the record here, since the collapsed table this used to
    read was removed.** The conflict is a property of the collapse key and not
    of a record row: a resource asserting a positive and a negative sign under
    two predicates leaves two record rows, neither of which carries both flags,
    and the row that carries both is the folded one. So the summary groups the
    record by the endpoint/class triple and asks the group, which is the same
    question the removed table answered and gives the same numbers. It is two
    grouped scans of a table the step has just written and analysed — a
    build-time measurement rather than a query-time fold, and, with the
    ``interaction_source_count_histogram`` beside it, the only place in the
    derive that folds anything at all.
    """
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.interaction_sign_conflict (
              measured_at timestamptz NOT NULL DEFAULT now(),
              fact_rows bigint NOT NULL,
              signed_rows bigint NOT NULL,
              both_flags_rows bigint NOT NULL,
              both_flags_percent double precision NOT NULL,
              single_resource_rows bigint NOT NULL,
              cross_resource_rows bigint NOT NULL
            )
            """
        ).format(schema_id)
    )
    # `fact_rows` counts **collapse keys**, which is what the removed table
    # held one row of. The three figures come from one grouped scan of the
    # record over the endpoint/class triple, walking
    # `interaction_fact_resource_collapse_idx` on the statistics the step
    # ANALYZEd a moment ago.
    cur.execute(
        sql.SQL(
            """
            WITH folded AS (
              SELECT
                bool_or(is_stimulation) AS is_stimulation,
                bool_or(is_inhibition) AS is_inhibition
              FROM {}.interaction_fact_resource
              GROUP BY
                subject_entity_id, object_entity_id, interaction_class_id
            )
            SELECT
              count(*)::bigint,
              count(*) FILTER (
                WHERE is_stimulation IS NOT NULL OR is_inhibition IS NOT NULL
              )::bigint,
              count(*) FILTER (WHERE is_stimulation AND is_inhibition)::bigint
            FROM folded
            """
        ).format(schema_id)
    )
    fact_rows, signed, both = (int(value) for value in cur.fetchone())
    # The split between a single resource asserting both signs and resources
    # disagreeing is asked only of the keys that carry both flags — 7,925 of
    # 14.3 million on dev4 — so the second pass folds the record again and
    # keeps only those keys, rather than carrying every group through.
    cur.execute(
        sql.SQL(
            """
            WITH conflicted AS (
              SELECT subject_entity_id, object_entity_id, interaction_class_id
              FROM {}.interaction_fact_resource
              GROUP BY 1, 2, 3
              HAVING bool_or(is_stimulation) AND bool_or(is_inhibition)
            ), per_resource AS (
              SELECT
                r.subject_entity_id,
                r.object_entity_id,
                r.interaction_class_id,
                r.source_id,
                bool_or(r.is_stimulation) AS asserts_positive,
                bool_or(r.is_inhibition) AS asserts_negative
              FROM {}.interaction_fact_resource r
              JOIN conflicted c
                ON c.subject_entity_id = r.subject_entity_id
               AND c.object_entity_id = r.object_entity_id
               AND c.interaction_class_id = r.interaction_class_id
              GROUP BY 1, 2, 3, 4
            ), per_row AS (
              SELECT coalesce(
                       bool_or(asserts_positive AND asserts_negative),
                       false
                     ) AS single_resource_conflict
              FROM per_resource
              GROUP BY subject_entity_id, object_entity_id,
                       interaction_class_id
            )
            SELECT
              count(*) FILTER (WHERE single_resource_conflict)::bigint,
              count(*) FILTER (WHERE NOT single_resource_conflict)::bigint
            FROM per_row
            """
        ).format(schema_id, schema_id)
    )
    single, cross = cur.fetchone()
    summary = {
        'fact_rows': int(fact_rows),
        'signed_rows': int(signed),
        'both_flags_rows': int(both),
        'both_flags_percent': (
            round(100.0 * both / signed, 4) if signed else 0.0
        ),
        'single_resource_rows': int(single),
        'cross_resource_rows': int(cross),
    }
    cur.execute(
        sql.SQL('TRUNCATE {}.interaction_sign_conflict').format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.interaction_sign_conflict
              (fact_rows, signed_rows, both_flags_rows, both_flags_percent,
               single_resource_rows, cross_resource_rows)
            VALUES (%s, %s, %s, %s, %s, %s)
            """
        ).format(schema_id),
        [
            summary['fact_rows'],
            summary['signed_rows'],
            summary['both_flags_rows'],
            summary['both_flags_percent'],
            summary['single_resource_rows'],
            summary['cross_resource_rows'],
        ],
    )
    return summary


def _drop_interaction_staging(cur: psycopg2.extensions.cursor) -> None:
    for table in (
        '_if_role_evidence',
        '_if_participant_class',
        '_if_annotation_class',
        '_if_relation',
        '_if_evidence_sign',
        '_if_evidence',
        '_if_record_annotation',
        '_if_record',
        '_if_fact',
        '_if_party',
        '_if_header',
        '_if_header_source',
        # Left by the pre-amendment fold; dropped here so a database that ran
        # the old derive does not keep 14-million-row staging tables around.
        '_if_sign_source',
        '_if_sign',
        '_if_annotation',
    ):
        cur.execute(f'DROP TABLE IF EXISTS {table}')
