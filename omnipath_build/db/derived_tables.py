"""Derived query tables built from the canonical graph.

These tables are not primary evidence. They summarize canonical relations and
ontology-term entities into shapes that are cheaper for search, filtering, and
resource summaries. They are rebuilt after selected sources have been ingested
and canonicalized.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

from psycopg2 import sql
import psycopg2.extensions

from omnipath_build.db.schema import _ensure_ontology_terms_table
from pypath.internals.cv_terms import OntologyAnnotationCv, cv_term_label_accession


ONTOLOGY_DEFINITION_TERM = cv_term_label_accession(OntologyAnnotationCv.DEFINITION)

@dataclass(frozen=True)
class DerivedTableStats:
    """Summary counts from derived table population."""

    entity_identifier_lookup: int = 0
    entity_relation_counts: int = 0
    ontology_terms: int = 0
    entity_ontology_terms: int = 0
    entity_source_count: int = 0


def rebuild_derived_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
    progress: bool = False,
) -> DerivedTableStats:
    """Create and fully rebuild derived search/count tables."""

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
    _log(progress, 'all', 'done', seconds=f'{time.perf_counter() - started:.3f}')
    return DerivedTableStats(
        entity_identifier_lookup=entity_identifier_lookup,
        entity_relation_counts=relation_counts,
        ontology_terms=ontology_terms,
        entity_ontology_terms=entity_ontology_terms,
        entity_source_count=entity_source_count,
    )


def _log(progress: bool, step: str, event: str, **fields: object) -> None:
    if not progress:
        return
    details = ' '.join(f'{key}={value}' for key, value in fields.items())
    print(
        f'[derive-tables] step={step} event={event}'
        + (f' {details}' if details else ''),
        flush=True,
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
