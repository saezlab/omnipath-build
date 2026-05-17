from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg2 import sql
from psycopg2.extras import Json
import psycopg2.extensions

from minimal.cv_terms import CV_TERM_ENTITY_TYPE


@dataclass(frozen=True)
class ResourceTableStats:
    """Summary counts from resource metadata sync."""

    resources: int = 0


def sync_resources_table(
    conn: psycopg2.extensions.connection,
    discovered: dict[str, list[object]],
    *,
    schema: str = 'public',
    prefer_bitmaps: bool = False,
) -> ResourceTableStats:
    """Upsert discovered resource metadata and minimal source-level counts."""

    with conn.cursor() as cur:
        cur.execute('SET LOCAL max_parallel_workers_per_gather = 0')
        present_sources = _present_sources(
            cur,
            schema=schema,
            prefer_bitmaps=prefer_bitmaps,
        )
        rows = [
            _resource_row(
                cur,
                schema=schema,
                source=source,
                functions=functions,
                present_sources=present_sources,
                prefer_bitmaps=prefer_bitmaps,
            )
            for source, functions in sorted(discovered.items())
        ]
        cur.executemany(
            sql.SQL(
                """
                INSERT INTO {}.resources (
                  resource_id,
                  resource_name,
                  description,
                  homepage_url,
                  license,
                  pubmed_id,
                  resource_kind,
                  categories,
                  annotation_ontologies,
                  entity_count,
                  interaction_count,
                  association_count,
                  identifier_count,
                  ontology_term_count,
                  total_size_bytes,
                  last_downloaded_at,
                  last_built_at,
                  build_status
                )
                VALUES (
                  %(resource_id)s,
                  %(resource_name)s,
                  %(description)s,
                  %(homepage_url)s,
                  %(license)s,
                  %(pubmed_id)s,
                  %(resource_kind)s,
                  %(categories)s,
                  %(annotation_ontologies)s,
                  %(entity_count)s,
                  %(interaction_count)s,
                  %(association_count)s,
                  %(identifier_count)s,
                  %(ontology_term_count)s,
                  %(total_size_bytes)s,
                  %(last_downloaded_at)s,
                  %(last_built_at)s,
                  %(build_status)s
                )
                ON CONFLICT (resource_id) DO UPDATE SET
                  resource_name = EXCLUDED.resource_name,
                  description = EXCLUDED.description,
                  homepage_url = EXCLUDED.homepage_url,
                  license = EXCLUDED.license,
                  pubmed_id = EXCLUDED.pubmed_id,
                  resource_kind = EXCLUDED.resource_kind,
                  categories = EXCLUDED.categories,
                  annotation_ontologies = EXCLUDED.annotation_ontologies,
                  entity_count = EXCLUDED.entity_count,
                  interaction_count = EXCLUDED.interaction_count,
                  association_count = EXCLUDED.association_count,
                  identifier_count = EXCLUDED.identifier_count,
                  ontology_term_count = EXCLUDED.ontology_term_count,
                  total_size_bytes = EXCLUDED.total_size_bytes,
                  last_downloaded_at = EXCLUDED.last_downloaded_at,
                  last_built_at = EXCLUDED.last_built_at,
                  build_status = EXCLUDED.build_status
                """
            )
            .format(sql.Identifier(schema))
            .as_string(cur.connection),
            rows,
        )
    conn.commit()
    return ResourceTableStats(resources=len(rows))


def _resource_row(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
    source: str,
    functions: list[object],
    present_sources: set[str],
    prefer_bitmaps: bool,
) -> dict[str, Any]:
    config = _resource_config(functions)
    counts = (
        _source_counts(
            cur,
            schema=schema,
            source=source,
            prefer_bitmaps=prefer_bitmaps,
        )
        if source in present_sources
        else _empty_source_counts()
    )
    categories = _resource_categories(
        primary_category=getattr(config, 'primary_category', None),
        interaction_count=counts['interaction_count'],
        association_count=counts['association_count'],
        ontology_term_count=counts['ontology_term_count'],
    )
    return {
        'resource_id': source,
        'resource_name': getattr(config, 'name', source),
        'description': getattr(config, 'description', None),
        'homepage_url': getattr(config, 'url', None),
        'license': _text_or_none(getattr(config, 'license', None)),
        'pubmed_id': getattr(config, 'pubmed', None),
        'resource_kind': getattr(config, 'resource_kind', 'data_resource'),
        'categories': Json(categories),
        'annotation_ontologies': Json(_ontology_labels(config)),
        'entity_count': counts['entity_count'],
        'interaction_count': counts['interaction_count'],
        'association_count': counts['association_count'],
        'identifier_count': counts['identifier_count'],
        'ontology_term_count': counts['ontology_term_count'],
        'total_size_bytes': 0,
        'last_downloaded_at': None,
        'last_built_at': counts['last_built_at'],
        'build_status': 'success' if counts['has_rows'] else 'not_built',
    }


def _present_sources(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
    prefer_bitmaps: bool = False,
) -> set[str]:
    if prefer_bitmaps:
        cur.execute(
            sql.SQL(
                """
                SELECT facet_value
                FROM {}.facet_entity_bitmap
                WHERE facet_name = 'source'
                UNION
                SELECT facet_value
                FROM {}.facet_relation_bitmap
                WHERE facet_name = 'source'
                """
            ).format(
                sql.Identifier(schema),
                sql.Identifier(schema),
            )
        )
        return {row[0] for row in cur.fetchall()}

    cur.execute(
        sql.SQL(
            """
            SELECT source
            FROM {}.entity_evidence
            UNION
            SELECT source
            FROM {}.relation_evidence
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
        )
    )
    return {row[0] for row in cur.fetchall()}


def _empty_source_counts() -> dict[str, Any]:
    return {
        'entity_count': 0,
        'identifier_count': 0,
        'interaction_count': 0,
        'association_count': 0,
        'ontology_term_count': 0,
        'last_built_at': None,
        'has_rows': False,
    }


def _resource_config(functions: list[object]) -> object | None:
    for fn in functions:
        if getattr(fn, 'function_name', None) != 'resource':
            continue
        config = getattr(getattr(fn, 'call', None), 'config', None)
        if config is not None:
            return config
    return None


def _source_counts(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
    source: str,
    prefer_bitmaps: bool = False,
) -> dict[str, Any]:
    if prefer_bitmaps:
        bitmap_counts = _bitmap_source_counts(
            cur,
            schema=schema,
            source=source,
        )
        if bitmap_counts is not None:
            return bitmap_counts

    cur.execute(
        sql.SQL(
            """
            SELECT
              (
                SELECT COUNT(DISTINCT r.entity_id)::bigint
                FROM {}.entity_evidence_resolution r
                JOIN {}.entity_evidence ee
                  ON ee.entity_evidence_id = r.entity_evidence_id
                WHERE ee.source = %s
                  AND r.entity_id IS NOT NULL
              ) AS entity_count,
              (
                SELECT 0::bigint
              ) AS identifier_count,
              (
                SELECT COUNT(DISTINCT rel.relation_id)::bigint
                FROM {}.relation_evidence_relation rer
                JOIN {}.relation_evidence re
                  ON re.relation_evidence_id = rer.relation_evidence_id
                JOIN {}.relation rel
                  ON rel.relation_id = rer.relation_id
                WHERE re.source = %s
                  AND rel.relation_category = 'interaction'
              ) AS interaction_count,
              (
                SELECT COUNT(DISTINCT rel.relation_id)::bigint
                FROM {}.relation_evidence_relation rer
                JOIN {}.relation_evidence re
                  ON re.relation_evidence_id = rer.relation_evidence_id
                JOIN {}.relation rel
                  ON rel.relation_id = rer.relation_id
                WHERE re.source = %s
                  AND rel.relation_category = 'association'
              ) AS association_count,
              (
                SELECT COUNT(DISTINCT r.entity_id)::bigint
                FROM {}.entity_evidence_resolution r
                JOIN {}.entity_evidence ee
                  ON ee.entity_evidence_id = r.entity_evidence_id
                JOIN {}.entity e
                  ON e.entity_id = r.entity_id
                JOIN {}.entity_type et
                  ON et.entity_type_id = e.entity_type_id
                WHERE ee.source = %s
                  AND et.name = %s
              ) AS ontology_term_count,
              (
                SELECT NULL::timestamptz
              ) AS last_built_at,
              (
                SELECT EXISTS (
                  SELECT 1
                  FROM {}.entity_evidence
                  WHERE source = %s
                )
              ) AS has_rows
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [
            source,
            source,
            source,
            source,
            CV_TERM_ENTITY_TYPE,
            source,
        ],
    )
    row = cur.fetchone()
    return {
        'entity_count': int(row[0] or 0),
        'identifier_count': int(row[1] or 0),
        'interaction_count': int(row[2] or 0),
        'association_count': int(row[3] or 0),
        'ontology_term_count': int(row[4] or 0),
        'last_built_at': row[5],
        'has_rows': bool(row[6]),
    }


def _bitmap_source_counts(
    cur: psycopg2.extensions.cursor,
    *,
    schema: str,
    source: str,
) -> dict[str, Any] | None:
    cur.execute(
        sql.SQL(
            """
            WITH
            source_entity AS (
              SELECT entity_bitmap, entity_count
              FROM {}.facet_entity_bitmap
              WHERE facet_name = 'source'
                AND facet_value = %s
            ),
            ontology_entity AS (
              SELECT entity_bitmap
              FROM {}.facet_entity_bitmap
              WHERE facet_name = 'entity_type'
                AND facet_value = %s
            ),
            source_relation AS (
              SELECT relation_bitmap, relation_count
              FROM {}.facet_relation_bitmap
              WHERE facet_name = 'source'
                AND facet_value = %s
            ),
            interaction_relation AS (
              SELECT rb_or_agg(relation_bitmap) AS relation_bitmap
              FROM {}.facet_relation_bitmap
              WHERE facet_name = 'predicate'
                AND facet_category = 'interaction'
            ),
            association_relation AS (
              SELECT rb_or_agg(relation_bitmap) AS relation_bitmap
              FROM {}.facet_relation_bitmap
              WHERE facet_name = 'predicate'
                AND facet_category = 'association'
            )
            SELECT
              COALESCE(
                (SELECT entity_count FROM source_entity),
                0
              )::bigint AS entity_count,
              (
                SELECT 0::bigint
              ) AS identifier_count,
              COALESCE(
                (
                  SELECT rb_and_cardinality(
                    source_relation.relation_bitmap,
                    interaction_relation.relation_bitmap
                  )
                  FROM source_relation
                  CROSS JOIN interaction_relation
                ),
                0
              )::bigint AS interaction_count,
              COALESCE(
                (
                  SELECT rb_and_cardinality(
                    source_relation.relation_bitmap,
                    association_relation.relation_bitmap
                  )
                  FROM source_relation
                  CROSS JOIN association_relation
                ),
                0
              )::bigint AS association_count,
              COALESCE(
                (
                  SELECT rb_and_cardinality(
                    source_entity.entity_bitmap,
                    ontology_entity.entity_bitmap
                  )
                  FROM source_entity
                  CROSS JOIN ontology_entity
                ),
                0
              )::bigint AS ontology_term_count,
              NULL::timestamptz AS last_built_at,
              EXISTS (SELECT 1 FROM source_entity)
                OR EXISTS (SELECT 1 FROM source_relation) AS has_rows
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
        ),
        [
            source,
            CV_TERM_ENTITY_TYPE,
            source,
        ],
    )
    row = cur.fetchone()
    if row is None or not row[6]:
        return None
    return {
        'entity_count': int(row[0] or 0),
        'identifier_count': int(row[1] or 0),
        'interaction_count': int(row[2] or 0),
        'association_count': int(row[3] or 0),
        'ontology_term_count': int(row[4] or 0),
        'last_built_at': row[5],
        'has_rows': bool(row[6]),
    }


def _resource_categories(
    *,
    primary_category: object,
    interaction_count: int,
    association_count: int,
    ontology_term_count: int,
) -> list[str]:
    categories = []
    primary = _text_or_none(primary_category)
    if primary:
        categories.append(primary)
    if interaction_count:
        categories.append('interaction')
    if association_count:
        categories.append('association')
    if ontology_term_count:
        categories.append('ontology')
    return sorted(set(categories))


def _ontology_labels(config: object | None) -> list[str]:
    values = []
    for ontology in getattr(config, 'annotation_ontologies', ()) or ():
        label = getattr(ontology, 'definition', None) or str(ontology)
        values.append(str(label))
    return values


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
