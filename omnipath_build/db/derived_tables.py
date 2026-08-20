"""Derived query tables built from the canonical graph.

These tables are not primary evidence. They summarize canonical relations and
ontology-term entities into shapes that are cheaper for search, filtering, and
resource summaries. They are rebuilt after selected sources have been ingested
and canonicalized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
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

    ``interactions`` also rebuilds the interaction projection (008 T013/T014).
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
    T013c's sign-conflict figures and T020's cost report are read back out of
    this output. Only the sink is the logger rather than ``print`` (research
    R17) — pre-existing ``print`` call sites elsewhere in the build keep what
    they have.
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


# --- The interaction projection (008 T012-T014) ------------------------------
#
# `interaction_fact_combined` is a denormalised precomputed projection over the canonical
# graph, not a new store of evidence (research R0): `relation` supplies the
# deduped endpoints, `relation_evidence` and its annotations the provenance, and
# `relation_evidence_relation` links the two. The evidence table's own endpoint
# columns are unusable for this — `object_entity_id` is NULL on every row and
# `subject_entity_id` set on about a fifth — so endpoints come from `relation`
# and never from the evidence rows.


# Participant-role terms, the first tier of the class derivation (research R18).
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
# resources did not choose per interaction (R18 makes the same point about
# class), so a symmetric verb is not the resource saying "this interaction has
# no direction". FR-044a governs — an unasserted attribute never becomes an
# asserted false, and 8.2M rows rested on that reading.
_DIRECTED_PREDICATES = (
    'controls',
    'regulates',
    'positively_regulates',
    'negatively_regulates',
    'transports',
)

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

    ``rows_by_class`` is the per-class row count research R18 asks every run to
    report: a class collapsing back to zero has to be visible in the build
    output, not discovered a phase later. ``sign_conflict`` is the T013c
    measurement — how often both sign flags land on one row, and whether that
    is one resource asserting both or resources genuinely disagreeing.
    """

    interactions: int = 0
    parties: int = 0
    facts: int = 0
    rows_by_class: dict[str, int] = field(default_factory=dict)
    sign_conflict: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0


def interaction_content_uuid_sql(
    participants: str,
    interaction_class: str,
) -> str:
    """SQL for the header's content-addressed id (data model §1, FR-002).

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


def rebuild_interaction_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> InteractionDeriveStats:
    """Project the canonical graph into the interaction model (T013, T014).

    Writes ``interaction`` (one endpoint-independent header per participant set
    and class), ``interaction_party`` (its participants, in role) and
    ``interaction_fact_combined`` (one row per **ordered** subject/object/class triple,
    with provenance folded across every contributing resource). The three tables
    are pure projections of `relation`, so they are rebuilt whole rather than
    sliced: nothing in them is evidence that a partial rebuild could lose.

    Runs after ``classify_interaction_class``, which fills the predicate→class
    map the third derivation tier reads. When that map is still empty the step
    seeds it itself, so the projection does not silently fall back to `other`
    just because the derive steps ran in the wrong order.
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

        _log(progress, 'interaction_class_evidence', 'start')
        step_started = time.perf_counter()
        _stage_interaction_class_evidence(cur, schema, classes)
        _log(
            progress,
            'interaction_class_evidence',
            'done',
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'interaction_evidence_fold', 'start')
        step_started = time.perf_counter()
        _stage_interaction_evidence_fold(cur, schema)
        _log(
            progress,
            'interaction_evidence_fold',
            'done',
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'interaction_header', 'start')
        step_started = time.perf_counter()
        interactions, parties = _populate_interaction_header(cur, schema)
        _log(
            progress,
            'interaction_header',
            'done',
            rows=interactions,
            parties=parties,
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        _log(progress, 'interaction_fact_combined', 'start')
        step_started = time.perf_counter()
        facts = _populate_interaction_fact_combined(cur, schema)
        _log(
            progress,
            'interaction_fact_combined',
            'done',
            rows=facts,
            seconds=f'{time.perf_counter() - step_started:.3f}',
        )

        rows_by_class = _interaction_rows_by_class(cur, schema)
        # R18: the per-class counts go into the build output on every run, so a
        # class collapsing back to zero is visible here rather than a phase later.
        _log(
            progress,
            'interaction_fact_combined',
            'rows_by_class',
            **{name: count for name, count in sorted(rows_by_class.items())},
        )
        sign_conflict = _record_sign_conflict_summary(cur, schema)
        _log(progress, 'interaction_fact_combined', 'sign_conflict', **sign_conflict)
        _drop_interaction_staging(cur)
    conn.commit()
    seconds = time.perf_counter() - started
    _log(
        progress,
        'interactions',
        'done',
        rows=facts,
        seconds=f'{seconds:.3f}',
    )
    return InteractionDeriveStats(
        interactions=interactions,
        parties=parties,
        facts=facts,
        rows_by_class=rows_by_class,
        sign_conflict=sign_conflict,
        seconds=seconds,
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
    """Resolve every relation's interaction class, by the R18 precedence.

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


def _stage_interaction_evidence_fold(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    """Fold the evidence onto the ordered fact key.

    Everything here groups by ``(subject_entity_id, object_entity_id,
    interaction_class_id)`` — the ordered key of data model §3, so A→B and B→A
    fold separately and are never merged (FR-044d).
    """
    schema_id = sql.Identifier(schema)
    positive = sorted(POSITIVE_SIGN_ACCESSIONS)
    negative = sorted(NEGATIVE_SIGN_ACCESSIONS)

    # Sign, per contributing resource first. The per-resource step is what lets
    # T013c tell one resource asserting both signs (under two predicates that
    # share a class) apart from resources genuinely disagreeing.
    cur.execute('DROP TABLE IF EXISTS _if_sign_source')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_sign_source AS
            SELECT
              ir.subject_entity_id,
              ir.object_entity_id,
              ir.interaction_class_id,
              rer.source_id,
              nullif(bool_or(a.term = ANY(%s)), false) AS asserts_positive,
              nullif(bool_or(a.term = ANY(%s)), false) AS asserts_negative
            FROM {}.relation_evidence_annotation rea
            JOIN {}.annotation a ON a.annotation_key = rea.annotation_key
            JOIN {}.relation_evidence_relation rer
              ON rer.source_id = rea.source_id
             AND rer.relation_evidence_id = rea.relation_evidence_id
            JOIN _if_relation ir ON ir.relation_id = rer.relation_id
            WHERE a.term = ANY(%s)
            GROUP BY 1, 2, 3, 4
            """
        ).format(schema_id, schema_id, schema_id),
        [positive, negative, sorted(set(positive) | set(negative))],
    )

    # The summary the fact row carries. `nullif(..., false)` is the whole point
    # of FR-044a: a resource that asserts no sign leaves the column NULL, which
    # is a different statement from an asserted false. Both flags stay true
    # where resources disagree — no silent winner.
    cur.execute('DROP TABLE IF EXISTS _if_sign')
    cur.execute(
        """
        CREATE UNLOGGED TABLE _if_sign AS
        SELECT
          subject_entity_id,
          object_entity_id,
          interaction_class_id,
          nullif(bool_or(asserts_positive), false) AS is_stimulation,
          nullif(bool_or(asserts_negative), false) AS is_inhibition,
          count(*)::smallint AS sign_source_count,
          bool_or(asserts_positive AND asserts_negative)
            AS single_resource_conflict
        FROM _if_sign_source
        GROUP BY 1, 2, 3
        """
    )
    cur.execute(
        'CREATE INDEX _if_sign_idx ON _if_sign '
        '(subject_entity_id, object_entity_id, interaction_class_id)'
    )
    cur.execute('ANALYZE _if_sign')

    # References and the annotation-borne hot columns. Every contributor's
    # references are aggregated, including resources that assert no sign and no
    # direction (FR-044c) — attribute-poor evidence is still evidence.
    cur.execute('DROP TABLE IF EXISTS _if_annotation')
    cur.execute(
        sql.SQL(
            """
            CREATE UNLOGGED TABLE _if_annotation AS
            SELECT
              ir.subject_entity_id,
              ir.object_entity_id,
              ir.interaction_class_id,
              array_agg(DISTINCT a.value) FILTER (
                WHERE a.term = %(pubmed)s
              ) AS reference_pubmed_ids,
              array_agg(DISTINCT a.value) FILTER (
                WHERE a.term = %(doi)s
              ) AS reference_dois,
              min(a.value::double precision) FILTER (
                WHERE a.term = ANY(%(affinity)s)
              ) AS affinity,
              max(a.value::double precision) FILTER (
                WHERE a.term = %(pchembl)s
              ) AS pchembl,
              max(a.value::double precision) FILTER (
                WHERE a.term = %(score)s
              ) AS score,
              array_agg(DISTINCT a.value) FILTER (
                WHERE a.term = %(curation)s
              ) AS curation_flags
            FROM {}.relation_evidence_annotation rea
            JOIN {}.annotation a ON a.annotation_key = rea.annotation_key
            JOIN {}.relation_evidence_relation rer
              ON rer.source_id = rea.source_id
             AND rer.relation_evidence_id = rea.relation_evidence_id
            JOIN _if_relation ir ON ir.relation_id = rer.relation_id
            WHERE a.value IS NOT NULL
              AND a.value <> ''
              AND (
                a.term IN (%(pubmed)s, %(doi)s, %(curation)s)
                OR (
                  a.term = ANY(%(numeric_terms)s)
                  AND a.value ~ %(numeric)s
                )
              )
            GROUP BY 1, 2, 3
            """
        ).format(schema_id, schema_id, schema_id),
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
        'CREATE INDEX _if_annotation_idx ON _if_annotation '
        '(subject_entity_id, object_entity_id, interaction_class_id)'
    )
    cur.execute('ANALYZE _if_annotation')

    # Provenance and direction over **every** contributing evidence row. The
    # join to the link table is left-outer so a canonical relation with no
    # evidence link still reaches the projection, with an empty source set,
    # rather than disappearing from the graph the API serves.
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
              count(DISTINCT ds.name)::int AS source_count,
              bool_or(ir.asserts_directed) AS is_directed,
              count(DISTINCT ds.name) FILTER (
                WHERE ir.asserts_directed IS NOT NULL
              )::smallint AS direction_source_count,
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
    """Write ``interaction`` and ``interaction_party`` (T014, data model §1-§2).

    The header is endpoint-independent: its participants are the unordered set
    of the fact row's endpoints, so both directions of a pair share one header
    and a caller can reach the interaction from either side. A participant's
    role records how it appears across the contributing facts — ``subject``,
    ``object``, or ``member`` when it appears as both — and ``role_flag`` carries
    the ligand/receptor role that R18's first derivation tier read.
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
    # them together keeps the header's dependants from tripping over the FK.
    cur.execute(
        sql.SQL(
            'TRUNCATE {}.interaction_fact_combined, {}.interaction_party, '
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


def _populate_interaction_fact_combined(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    """Write the flat binary projection (T013, data model §3)."""
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {schema}.interaction_fact_combined (
              subject_entity_id, object_entity_id, interaction_class_id,
              subject_organism, object_organism,
              is_directed, is_stimulation, is_inhibition,
              sign_source_count, direction_source_count,
              affinity, pchembl, score,
              sources, source_count, curation_flags,
              reference_pubmed_ids, reference_dois, reference_count,
              interaction_id
            )
            SELECT
              f.subject_entity_id,
              f.object_entity_id,
              f.interaction_class_id,
              subject_entity.taxonomy_id,
              object_entity.taxonomy_id,
              f.is_directed,
              sign.is_stimulation,
              sign.is_inhibition,
              coalesce(sign.sign_source_count, 0),
              f.direction_source_count,
              annotation.affinity,
              annotation.pchembl,
              annotation.score,
              f.sources,
              f.source_count,
              annotation.curation_flags,
              annotation.reference_pubmed_ids,
              annotation.reference_dois,
              coalesce(
                cardinality(annotation.reference_pubmed_ids), 0
              ) + coalesce(cardinality(annotation.reference_dois), 0),
              h.interaction_id
            FROM _if_fact f
            LEFT JOIN _if_sign sign
              ON sign.subject_entity_id = f.subject_entity_id
             AND sign.object_entity_id = f.object_entity_id
             AND sign.interaction_class_id = f.interaction_class_id
            LEFT JOIN _if_annotation annotation
              ON annotation.subject_entity_id = f.subject_entity_id
             AND annotation.object_entity_id = f.object_entity_id
             AND annotation.interaction_class_id = f.interaction_class_id
            JOIN _if_header h
              ON h.entity_low
                   = least(f.subject_entity_id, f.object_entity_id)
             AND h.entity_high
                   = greatest(f.subject_entity_id, f.object_entity_id)
             AND h.interaction_class_id = f.interaction_class_id
            LEFT JOIN {schema}.entity subject_entity
              ON subject_entity.entity_id = f.subject_entity_id
            LEFT JOIN {schema}.entity object_entity
              ON object_entity.entity_id = f.object_entity_id
            """
        ).format(schema=schema_id)
    )
    return int(cur.rowcount)


def _interaction_rows_by_class(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[str, int]:
    """Fact rows per interaction class, every class named even at zero."""
    cur.execute(
        sql.SQL(
            """
            SELECT vic.name, count(f.*)::bigint
            FROM {}.vocab_interaction_class vic
            LEFT JOIN {}.interaction_fact_combined f
              ON f.interaction_class_id = vic.interaction_class_id
            GROUP BY vic.name
            ORDER BY vic.name
            """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
    )
    return {name: int(count) for name, count in cur.fetchall()}


def _record_sign_conflict_summary(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> dict[str, float]:
    """Measure and store the sign-conflict rate (T013c, FR-044b).

    The rate is set by the **grain**, not by the biology: merging the four
    predicates that share the `signaling` class flattens an agonist and an
    antagonist relation between the same endpoints into one row. It was 2.0 per
    cent of signed rows on dev4 at the old class grain and is under-sampled, so
    it is re-measured on every build rather than trusted once. A figure
    substantially above ~2 per cent reopens whether the sign-bearing predicate
    belongs in the fact-table key.
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
    cur.execute(
        """
        SELECT
          count(*) FILTER (
            WHERE is_stimulation IS NOT NULL OR is_inhibition IS NOT NULL
          )::bigint,
          count(*) FILTER (WHERE is_stimulation AND is_inhibition)::bigint,
          count(*) FILTER (
            WHERE is_stimulation AND is_inhibition AND single_resource_conflict
          )::bigint,
          count(*) FILTER (
            WHERE is_stimulation AND is_inhibition
              AND NOT coalesce(single_resource_conflict, false)
          )::bigint
        FROM _if_sign
        """
    )
    signed, both, single, cross = cur.fetchone()
    cur.execute(
        sql.SQL('SELECT count(*)::bigint FROM {}.interaction_fact_combined').format(
            schema_id
        )
    )
    fact_rows = int(cur.fetchone()[0])
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
        '_if_sign_source',
        '_if_sign',
        '_if_annotation',
        '_if_fact',
        '_if_party',
        '_if_header',
        '_if_header_source',
    ):
        cur.execute(f'DROP TABLE IF EXISTS {table}')
