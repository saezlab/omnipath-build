"""Derived query tables built from the canonical graph.

These tables are not primary evidence. They summarize canonical relations and
ontology-term entities into shapes that are cheaper for search, filtering, and
resource summaries. They are rebuilt after selected sources have been ingested
and canonicalized.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg2 import sql
import psycopg2.extensions
from pypath.internals.cv_terms import (
    IdentifierNamespaceCv,
    OntologyAnnotationCv,
    cv_term_label_accession,
)

from omnipath_build.cv_terms import CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE

NAME_TERM = cv_term_label_accession(IdentifierNamespaceCv.NAME)
SYNONYM_TERM = cv_term_label_accession(IdentifierNamespaceCv.SYNONYM)
CV_TERM_ACCESSION_TERM = cv_term_label_accession(
    IdentifierNamespaceCv.CV_TERM_ACCESSION
)
DEFINITION_TERM = cv_term_label_accession(OntologyAnnotationCv.DEFINITION)
ONTOLOGY_ID_TERM = cv_term_label_accession(OntologyAnnotationCv.ONTOLOGY_ID)


@dataclass(frozen=True)
class DerivedTableStats:
    """Summary counts from derived table population."""

    entity_identifiers: int = 0
    entity_relation_counts: int = 0
    ontology_terms: int = 0


def rebuild_derived_tables(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> DerivedTableStats:
    """Create and fully rebuild derived search/count tables."""

    with conn.cursor() as cur:
        _create_derived_tables(cur, schema)
        entity_identifiers = _refresh_entity_identifiers(cur, schema)
        relation_counts = _populate_entity_relation_counts(cur, schema)
        ontology_terms = _populate_ontology_terms(cur, schema)
        _create_derived_indexes(cur, schema)
    conn.commit()
    return DerivedTableStats(
        entity_identifiers=entity_identifiers,
        entity_relation_counts=relation_counts,
        ontology_terms=ontology_terms,
    )


def _create_derived_tables(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.entity_relation_counts (
              entity_id bigint PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              relation_count bigint NOT NULL
            )
            """
        ).format(schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.ontology_terms (
              term_entity_id bigint PRIMARY KEY
                REFERENCES {}.entity(entity_id)
                ON DELETE CASCADE,
              term_id text NOT NULL,
              ontology_prefix text,
              label text NOT NULL,
              definition text,
              ontology_id text,
              synonyms text[] NOT NULL DEFAULT '{{}}',
              synonyms_text text NOT NULL DEFAULT '',
              sources text[] NOT NULL DEFAULT '{{}}'
            )
            """
        ).format(schema_id, schema_id)
    )


def _refresh_entity_identifiers(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    for statement in (
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS resolver_protein_lookup_canonical_idx
            ON {}.resolver_protein_identifier_lookup (
              canonical_identifier_type_id,
              canonical_identifier
            )
            """
        ).format(schema_id),
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS resolver_chemical_lookup_canonical_idx
            ON {}.resolver_chemical_identifier_lookup (
              canonical_identifier_type_id,
              canonical_identifier
            )
            """
        ).format(schema_id),
    ):
        cur.execute(statement)
    cur.execute('DROP TABLE IF EXISTS _derived_entity_identifier')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _derived_entity_identifier (
              entity_id bigint PRIMARY KEY,
              identifiers jsonb NOT NULL
            ) ON COMMIT DROP
            """
        )
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO _derived_entity_identifier (entity_id, identifiers)
            WITH resolved_entities AS (
              SELECT DISTINCT
                e.entity_id,
                e.canonical_identifier_type_id,
                e.canonical_identifier
              FROM {}.entity e
              JOIN {}.entity_evidence_resolution er
                ON er.entity_id = e.entity_id
              JOIN {}.vocab_resolution_status rs
                ON rs.resolution_status_id = er.status_id
               AND rs.name = 'resolved'
              WHERE e.canonical_identifier_type_id IS NOT NULL
                AND e.canonical_identifier IS NOT NULL
                AND e.canonical_identifier <> ''
            ),
            resolver_identifiers AS (
              SELECT
                entity_id,
                canonical_identifier_type_id AS identifier_type_id,
                canonical_identifier AS identifier
              FROM resolved_entities
              UNION
              SELECT
                re.entity_id,
                p.key_identifier_type_id,
                p.key_value
              FROM resolved_entities re
              JOIN {}.resolver_protein_identifier_lookup p
                ON p.canonical_identifier_type_id =
                   re.canonical_identifier_type_id
               AND p.canonical_identifier = re.canonical_identifier
              WHERE p.key_value IS NOT NULL
                AND p.key_value <> ''
              UNION
              SELECT
                re.entity_id,
                c.key_identifier_type_id,
                c.key_value
              FROM resolved_entities re
              JOIN {}.resolver_chemical_identifier_lookup c
                ON c.canonical_identifier_type_id =
                   re.canonical_identifier_type_id
               AND c.canonical_identifier = re.canonical_identifier
              WHERE c.key_value IS NOT NULL
                AND c.key_value <> ''
            ),
            identifier_rows AS (
              SELECT DISTINCT
                ri.entity_id,
                it.name AS identifier_type,
                ri.identifier_type_id,
                ri.identifier
              FROM resolver_identifiers ri
              JOIN {}.vocab_identifier_type it
                ON it.identifier_type_id = ri.identifier_type_id
              WHERE ri.identifier IS NOT NULL
                AND ri.identifier <> ''
            )
            SELECT
              entity_id,
              jsonb_agg(
                jsonb_build_object(
                  'identifier_type', identifier_type,
                  'identifier_type_id', identifier_type_id,
                  'identifier', identifier
                )
                ORDER BY identifier_type, identifier
              ) AS identifiers
            FROM identifier_rows
            GROUP BY entity_id
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
            INSERT INTO _derived_entity_identifier (entity_id, identifiers)
            WITH evidence_identifiers AS (
              SELECT DISTINCT
                er.entity_id,
                it.name AS identifier_type,
                i.identifier_type_id,
                i.value AS identifier
              FROM {}.entity_evidence_resolution er
              JOIN {}.vocab_resolution_status rs
                ON rs.resolution_status_id = er.status_id
               AND rs.name IN ('unresolved', 'ambiguous')
              JOIN {}.entity_evidence_identifier eei
                ON eei.source_id = er.source_id
               AND eei.entity_evidence_id = er.entity_evidence_id
              JOIN {}.identifier_evidence i
                ON i.identifier_id = eei.identifier_id
              JOIN {}.vocab_identifier_type it
                ON it.identifier_type_id = i.identifier_type_id
              WHERE er.entity_id IS NOT NULL
                AND i.value IS NOT NULL
                AND i.value <> ''
            )
            SELECT
              entity_id,
              jsonb_agg(
                jsonb_build_object(
                  'identifier_type', identifier_type,
                  'identifier_type_id', identifier_type_id,
                  'identifier', identifier
                )
                ORDER BY identifier_type, identifier
              ) AS identifiers
            FROM evidence_identifiers
            GROUP BY entity_id
            ON CONFLICT (entity_id) DO UPDATE SET
              identifiers = EXCLUDED.identifiers
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            schema_id,
            schema_id,
        )
    )
    cur.execute('ANALYZE _derived_entity_identifier')
    cur.execute(
        sql.SQL(
            """
            UPDATE {}.entity e
            SET identifiers = dei.identifiers
            FROM _derived_entity_identifier dei
            WHERE dei.entity_id = e.entity_id
              AND e.identifiers IS DISTINCT FROM dei.identifiers
            """
        ).format(schema_id)
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
              relation_count
            )
            SELECT entity_id, COUNT(DISTINCT relation_id)::bigint
            FROM (
              SELECT subject_entity_id AS entity_id, relation_id
              FROM {}.relation
              UNION ALL
              SELECT object_entity_id AS entity_id, relation_id
              FROM {}.relation
            ) relation_endpoints
            GROUP BY entity_id
            """
        ).format(schema_id, schema_id, schema_id)
    )
    return int(cur.rowcount)


def _populate_ontology_terms(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> int:
    schema_id = sql.Identifier(schema)
    cur.execute(sql.SQL('TRUNCATE {}.ontology_terms').format(schema_id))
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.ontology_terms (
              term_entity_id,
              term_id,
              ontology_prefix,
              label,
              definition,
              ontology_id,
              synonyms,
              synonyms_text,
              sources
            )
            WITH term_entities AS (
              SELECT e.entity_id, e.canonical_identifier AS term_id
              FROM {}.entity e
              JOIN {}.vocab_entity_type et
                ON et.entity_type_id = e.entity_type_id
               AND et.name = {}
              JOIN {}.vocab_identifier_type it
                ON it.name = {}
               AND e.canonical_identifier_type_id = it.identifier_type_id
            ),
            annotation_values AS (
              SELECT
                te.entity_id,
                (
                  ARRAY_AGG(a.value ORDER BY a.annotation_key)
                  FILTER (
                    WHERE a.term = {}
                      AND COALESCE(a.value, '') <> ''
                  )
                )[1] AS label,
                (
                  ARRAY_AGG(a.value ORDER BY a.annotation_key)
                  FILTER (
                    WHERE a.term = {}
                      AND COALESCE(a.value, '') <> ''
                  )
                )[1] AS definition,
                (
                  ARRAY_AGG(a.value ORDER BY a.annotation_key)
                  FILTER (
                    WHERE a.term = {}
                      AND COALESCE(a.value, '') <> ''
                  )
                )[1] AS ontology_id,
                ARRAY_AGG(DISTINCT a.value ORDER BY a.value)
                  FILTER (
                    WHERE (
                      a.term = {}
                      OR (a.term = {} AND a.unit = 'alt_id')
                    )
                    AND COALESCE(a.value, '') <> ''
                  ) AS synonyms
              FROM term_entities te
              LEFT JOIN {}.entity_evidence_resolution er
                ON er.entity_id = te.entity_id
              LEFT JOIN {}.entity_evidence_annotation ea
                ON ea.source_id = er.source_id
               AND ea.entity_evidence_id = er.entity_evidence_id
              LEFT JOIN {}.annotation a
                ON a.annotation_key = ea.annotation_key
              GROUP BY te.entity_id
            )
            SELECT
              te.entity_id AS term_entity_id,
              te.term_id,
              CASE
                WHEN te.term_id ~* '^KW-[0-9]+$' THEN 'kw'
                WHEN position(':' in te.term_id) > 0
                  THEN lower(split_part(te.term_id, ':', 1))
                ELSE NULL
              END AS ontology_prefix,
              COALESCE(av.label, te.term_id) AS label,
              av.definition,
              av.ontology_id,
              COALESCE(av.synonyms, '{{}}'::text[]) AS synonyms,
              array_to_string(COALESCE(av.synonyms, '{{}}'::text[]), ' ')
                AS synonyms_text,
              CASE
                WHEN av.ontology_id IS NULL THEN '{{}}'::text[]
                ELSE ARRAY[av.ontology_id]
              END AS sources
            FROM term_entities te
            LEFT JOIN annotation_values av
              ON av.entity_id = te.entity_id
            """
        ).format(
            schema_id,
            schema_id,
            schema_id,
            sql.Literal(CV_TERM_ENTITY_TYPE),
            schema_id,
            sql.Literal(CV_TERM_ID_TYPE),
            sql.Literal(NAME_TERM),
            sql.Literal(DEFINITION_TERM),
            sql.Literal(ONTOLOGY_ID_TERM),
            sql.Literal(SYNONYM_TERM),
            sql.Literal(CV_TERM_ACCESSION_TERM),
            schema_id,
            schema_id,
            schema_id,
        )
    )
    return int(cur.rowcount)


def _create_derived_indexes(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    statements = [
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS entity_relation_counts_count_idx
            ON {}.entity_relation_counts (relation_count DESC, entity_id ASC)
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
    ]
    for statement in statements:
        cur.execute(statement)
