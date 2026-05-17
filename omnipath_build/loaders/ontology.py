"""Load ontology datasets as canonical CV-term entities and catalog rows.

Ontology datasets bypass ordinary evidence ingest because every term already
has a stable ontology accession. Terms are written directly as canonical
``CV Term`` entities and as source-partitioned ``ontology_terms`` catalog rows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Iterable

from psycopg2 import sql
import psycopg2.extensions

from omnipath_build.cv_terms import CV_TERM_ID_TYPE, CV_TERM_ENTITY_TYPE
from pypath.internals.ontology_schema import OntologyTerm

@dataclass(frozen=True)
class OntologyLoadStats:
    """Loaded ontology term and metadata counts."""

    terms: int = 0
    annotations: int = 0


def load_ontology_terms(
    conn: psycopg2.extensions.connection,
    records: Iterable[OntologyTerm],
    *,
    schema: str = 'public',
    source: str,
    dataset: str,
    ontology_id: str,
    batch_size: int = 5000,
    progress_every: int = 5000,
) -> OntologyLoadStats:
    """Load ontology terms directly into entity and ontology_terms tables."""

    stats = _MutableOntologyStats()
    buffer: list[OntologyTerm] = []
    started_at = time.monotonic()
    for term in records:
        if not isinstance(term, OntologyTerm) or not term.id:
            continue
        buffer.append(term)
        stats.terms += 1
        stats.annotations += len(_term_synonyms(term))
        if batch_size > 0 and len(buffer) >= batch_size:
            _flush(
                conn,
                schema,
                buffer,
                source=source,
                dataset=dataset,
                ontology_id=ontology_id,
            )
            buffer.clear()
        if progress_every > 0 and stats.terms % progress_every == 0:
            elapsed = time.monotonic() - started_at
            rate = stats.terms / elapsed if elapsed else 0.0
            print(
                f'[{ontology_id}] ontology load progress '
                f'terms={stats.terms:,} annotations={stats.annotations:,} '
                f'rate={rate:,.1f}/s',
                flush=True,
            )
    _flush(
        conn,
        schema,
        buffer,
        source=source,
        dataset=dataset,
        ontology_id=ontology_id,
    )
    conn.commit()
    return stats.freeze()


def _flush(
    conn: psycopg2.extensions.connection,
    schema: str,
    rows: list[OntologyTerm],
    *,
    source: str,
    dataset: str,
    ontology_id: str,
) -> None:
    if not rows:
        return
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
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.dataset (source_id, name)
                VALUES (%s, %s)
                ON CONFLICT (source_id, name) DO UPDATE
                SET name = EXCLUDED.name
                """
            ).format(sql.Identifier(schema)),
            [source_id, dataset],
        )
        cur.executemany(
            sql.SQL(
                """
                WITH entity_type_row AS (
                  INSERT INTO {}.vocab_entity_type (name)
                  VALUES (%s)
                  ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                  RETURNING entity_type_id
                )
                INSERT INTO {}.entity (
                  entity_type_id,
                  taxonomy_id,
                  canonical_identifier_type_id,
                  canonical_identifier,
                  identifiers,
                  resolution_status_id
                )
                SELECT
                  entity_type_row.entity_type_id,
                  NULL::bigint,
                  it.identifier_type_id,
                  %s,
                  jsonb_build_array(
                    jsonb_build_object(
                      'identifier_type', it.name,
                      'identifier_type_id', it.identifier_type_id,
                      'identifier', %s
                    )
                  ),
                  1
                FROM entity_type_row
                CROSS JOIN {}.vocab_identifier_type it
                WHERE it.name = %s
                ON CONFLICT (
                  entity_type_id,
                  taxonomy_id,
                  canonical_identifier_type_id,
                  canonical_identifier
                )
                DO UPDATE SET
                  identifiers = EXCLUDED.identifiers,
                  resolution_status_id = 1
                """
            )
            .format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
            .as_string(cur.connection),
            [
                (CV_TERM_ENTITY_TYPE, term.id, term.id, CV_TERM_ID_TYPE)
                for term in rows
            ],
        )
        cur.execute(
            'CREATE TEMP TABLE IF NOT EXISTS _ontology_term_id (term_id text PRIMARY KEY) ON COMMIT DROP'
        )
        cur.executemany(
            'INSERT INTO _ontology_term_id (term_id) VALUES (%s) ON CONFLICT DO NOTHING',
            [(term.id,) for term in rows],
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TEMP TABLE IF NOT EXISTS _ontology_entity_map (
                  term_id text PRIMARY KEY,
                  entity_id bigint NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        cur.execute('TRUNCATE _ontology_entity_map')
        cur.execute(
            sql.SQL(
                """
                INSERT INTO _ontology_entity_map (term_id, entity_id)
                SELECT t.term_id, e.entity_id
                FROM _ontology_term_id t
                JOIN {}.vocab_entity_type et
                  ON et.name = %s
                JOIN {}.entity e
                  ON e.entity_type_id = et.entity_type_id
                JOIN {}.vocab_identifier_type it
                  ON it.name = %s
                 AND e.canonical_identifier_type_id = it.identifier_type_id
                 AND e.canonical_identifier = t.term_id
                ON CONFLICT (term_id) DO UPDATE SET entity_id = EXCLUDED.entity_id
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
                sql.Identifier(schema),
            ),
            [CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE],
        )
        term_rows = [_ontology_term_row(term, ontology_id) for term in rows]
        cur.executemany(
            sql.SQL(
                """
                INSERT INTO {}.ontology_terms (
                  source_id,
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
                SELECT
                  %s,
                  m.entity_id,
                  %s,
                  %s,
                  %s,
                  %s,
                  %s,
                  %s,
                  %s,
                  %s
                FROM _ontology_entity_map m
                WHERE m.term_id = %s
                ON CONFLICT (source_id, term_entity_id) DO UPDATE SET
                  term_id = EXCLUDED.term_id,
                  ontology_prefix = EXCLUDED.ontology_prefix,
                  label = EXCLUDED.label,
                  definition = EXCLUDED.definition,
                  ontology_id = EXCLUDED.ontology_id,
                  synonyms = EXCLUDED.synonyms,
                  synonyms_text = EXCLUDED.synonyms_text,
                  sources = EXCLUDED.sources
                """
            )
            .format(sql.Identifier(schema))
            .as_string(cur.connection),
            [
                (
                    source_id,
                    term_id,
                    ontology_prefix,
                    label,
                    definition,
                    ontology_id_value,
                    synonyms,
                    synonyms_text,
                    sources,
                    term_id,
                )
                for (
                    term_id,
                    ontology_prefix,
                    label,
                    definition,
                    ontology_id_value,
                    synonyms,
                    synonyms_text,
                    sources,
                ) in term_rows
            ],
        )
        cur.execute('TRUNCATE _ontology_term_id')


def _ontology_term_row(
    term: OntologyTerm,
    ontology_id: str,
) -> tuple[
    str,
    str | None,
    str,
    str | None,
    str,
    list[str],
    str,
    list[str],
]:
    synonyms = _term_synonyms(term)
    return (
        term.id,
        _ontology_prefix(term.id),
        term.name or term.id,
        term.definition,
        ontology_id,
        synonyms,
        ' '.join(synonyms),
        [ontology_id],
    )


def _ontology_prefix(term_id: str) -> str | None:
    if term_id.upper().startswith('KW-'):
        return 'kw'
    if ':' in term_id:
        return term_id.split(':', 1)[0].lower()
    return None


def _term_synonyms(term: OntologyTerm) -> list[str]:
    values = [
        *(term.synonyms or []),
        *(
            alt_id
            for alt_id in term.alt_ids or []
            if alt_id and alt_id != term.id
        ),
    ]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if not value or value == term.name or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


@dataclass
class _MutableOntologyStats:
    terms: int = 0
    annotations: int = 0

    def freeze(self) -> OntologyLoadStats:
        """Return an immutable public stats snapshot."""

        return OntologyLoadStats(
            terms=self.terms,
            annotations=self.annotations,
        )
