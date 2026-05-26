"""Maintain the API-facing resource summary table.

The resource table joins static pypath resource configuration with the content
that is actually present in PostgreSQL. Counts can come either from direct
evidence/graph tables or from bitmap tables when they have been refreshed.
"""

from __future__ import annotations

import os
import json
from typing import Any
from pathlib import Path
from datetime import UTC, datetime
from functools import lru_cache
import subprocess
from dataclasses import dataclass
import importlib.util
from collections.abc import Iterable

from psycopg2 import sql
from psycopg2.extras import Json
import psycopg2.extensions

from omnipath_build.cv_terms import CV_TERM_ENTITY_TYPE

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
    """Upsert discovered resource metadata and omnipath_build source-level counts."""

    with conn.cursor() as cur:
        cur.execute('SET LOCAL max_parallel_workers_per_gather = 0')
        _ensure_resources_metadata_columns(cur, schema)
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
                  license_label,
                  pubmed_id,
                  resource_kind,
                  input_module,
                  input_module_commit,
                  input_module_dirty,
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
                  %(license_label)s,
                  %(pubmed_id)s,
                  %(resource_kind)s,
                  %(input_module)s,
                  %(input_module_commit)s,
                  %(input_module_dirty)s,
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
                  license_label = EXCLUDED.license_label,
                  pubmed_id = EXCLUDED.pubmed_id,
                  resource_kind = EXCLUDED.resource_kind,
                  input_module = EXCLUDED.input_module,
                  input_module_commit = EXCLUDED.input_module_commit,
                  input_module_dirty = EXCLUDED.input_module_dirty,
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


def _ensure_resources_metadata_columns(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}.resources
            ADD COLUMN IF NOT EXISTS input_module text,
            ADD COLUMN IF NOT EXISTS input_module_commit text,
            ADD COLUMN IF NOT EXISTS input_module_dirty boolean
              NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS license_label text
            """
        ).format(sql.Identifier(schema))
    )


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
    module_metadata = _input_module_metadata(functions)
    snapshot_metadata = _resource_snapshot_metadata(source, functions)
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
    last_built_at = (
        counts['last_built_at'] or snapshot_metadata['last_built_at']
        if counts['has_rows']
        else None
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
        'license_label': _cv_label(getattr(config, 'license', None)),
        'pubmed_id': getattr(config, 'pubmed', None),
        'resource_kind': getattr(config, 'resource_kind', 'data_resource'),
        'input_module': module_metadata['module'],
        'input_module_commit': module_metadata['commit'],
        'input_module_dirty': module_metadata['dirty'],
        'categories': Json(categories),
        'annotation_ontologies': Json(_ontology_labels(config)),
        'entity_count': counts['entity_count'],
        'interaction_count': counts['interaction_count'],
        'association_count': counts['association_count'],
        'identifier_count': counts['identifier_count'],
        'ontology_term_count': counts['ontology_term_count'],
        'total_size_bytes': snapshot_metadata['total_size_bytes'],
        'last_downloaded_at': snapshot_metadata['last_downloaded_at'],
        'last_built_at': last_built_at,
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
            SELECT ds.name
            FROM {}.entity_evidence ee
            JOIN {}.data_source ds
              ON ds.source_id = ee.source_id
            UNION
            SELECT ds.name
            FROM {}.relation_evidence re
            JOIN {}.data_source ds
              ON ds.source_id = re.source_id
            UNION
            SELECT ds.name
            FROM {}.ontology_terms ot
            JOIN {}.data_source ds
              ON ds.source_id = ot.source_id
            """
        ).format(
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
            sql.Identifier(schema),
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


def _input_module_metadata(functions: list[object]) -> dict[str, Any]:
    fn = next(
        (
            fn
            for fn in functions
            if getattr(fn, 'function_name', None) == 'resource'
        ),
        functions[0] if functions else None,
    )
    module = getattr(fn, 'qualified_module', None) if fn is not None else None
    metadata = _module_git_metadata(module) if module else {}
    return {
        'module': module,
        'commit': metadata.get('commit'),
        'dirty': bool(metadata.get('dirty', False)),
    }


@lru_cache(maxsize=512)
def _module_git_metadata(module: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(module)
    origin = getattr(spec, 'origin', None)
    if not origin or origin == 'built-in':
        return {}
    path = Path(origin)
    try:
        commit = subprocess.check_output(
            ['git', '-C', str(path.parent), 'rev-parse', 'HEAD'],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        status = subprocess.check_output(
            [
                'git',
                '-C',
                str(path.parent),
                'status',
                '--porcelain',
                '--',
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return {}
    return {
        'commit': commit or None,
        'dirty': bool(status.strip()),
    }


def _resource_snapshot_metadata(
    source: str,
    functions: list[object],
) -> dict[str, Any]:
    latest_paths = [
        (_raw_records_root() / source / str(fn.function_name) / 'latest.json')
        for fn in functions
        if getattr(fn, 'function_name', None) != 'resource'
        and getattr(fn, 'output_kind', None) in {'entity', 'ontology'}
    ]
    manifests = [
        manifest
        for latest_path in latest_paths
        if (manifest := _read_latest_manifest(latest_path)) is not None
    ]
    total_size = sum(
        _manifest_snapshot_size(manifest) for manifest in manifests
    )
    last_downloaded_at = _max_datetime(
        _manifest_downloaded_at(manifest) for manifest in manifests
    )
    last_built_at = _max_datetime(
        _parse_datetime(
            manifest.get('accepted_at')
            or manifest.get('completed_at')
            or manifest.get('created_at')
        )
        for manifest in manifests
    )
    return {
        'total_size_bytes': total_size,
        'last_downloaded_at': last_downloaded_at,
        'last_built_at': last_built_at,
    }


def _raw_records_root() -> Path:
    return Path(
        os.environ.get(
            'OMNIPATH_BRONZE_ROOT',
            os.environ.get('OMNIPATH_RAW_RECORDS_ROOT', 'data/bronze'),
        )
    )


def _read_latest_manifest(latest_path: Path) -> dict[str, Any] | None:
    if not latest_path.exists():
        return None
    try:
        latest = json.loads(latest_path.read_text())
        manifest_path = Path(latest['manifest_path'])
        if not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text())
        manifest['_manifest_path'] = str(manifest_path)
        return manifest
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        return None


def _manifest_snapshot_size(manifest: dict[str, Any]) -> int:
    paths = [
        manifest.get('accepted_records_path') or manifest.get('records_path'),
        manifest.get('delta_path'),
        manifest.get('_manifest_path'),
    ]
    return sum(_path_size_bytes(Path(path)) for path in paths if path)


def _path_size_bytes(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            return sum(
                child.stat().st_size
                for child in path.rglob('*')
                if child.is_file()
            )
    except OSError:
        return 0
    return 0


def _manifest_downloaded_at(manifest: dict[str, Any]) -> datetime | None:
    fingerprint = manifest.get('download_fingerprint') or {}
    mtime_ns = fingerprint.get('mtime_ns')
    if mtime_ns is not None:
        try:
            return datetime.fromtimestamp(int(mtime_ns) / 1_000_000_000, UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    return None


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith('Z'):
        text = f'{text[:-1]}+00:00'
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _max_datetime(values: Iterable[datetime | None]) -> datetime | None:
    datetimes = [value for value in values if value is not None]
    return max(datetimes) if datetimes else None


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
            WITH selected_source AS (
              SELECT source_id
              FROM {}.data_source
              WHERE name = %s
            ),
            source_relations AS (
              SELECT DISTINCT rer.relation_id
              FROM {}.relation_evidence_relation rer
              JOIN {}.relation_evidence re
                ON re.source_id = rer.source_id
               AND re.relation_evidence_id = rer.relation_evidence_id
              WHERE re.source_id = (SELECT source_id FROM selected_source)
              UNION
              SELECT DISTINCT ear.relation_id
              FROM {}.entity_annotation_relation ear
              WHERE ear.source_id = (SELECT source_id FROM selected_source)
            )
            SELECT
              (
                SELECT COUNT(DISTINCT r.entity_id)::bigint
                FROM {}.entity_evidence_resolution r
                JOIN {}.entity_evidence ee
                  ON ee.source_id = r.source_id
                 AND ee.entity_evidence_id = r.entity_evidence_id
                WHERE ee.source_id = (SELECT source_id FROM selected_source)
                  AND r.entity_id IS NOT NULL
              ) AS entity_count,
              (
                SELECT 0::bigint
              ) AS identifier_count,
              (
                SELECT COUNT(DISTINCT rel.relation_id)::bigint
                FROM source_relations sr
                JOIN {}.relation rel
                  ON rel.relation_id = sr.relation_id
                JOIN {}.vocab_relation_category rc
                  ON rc.relation_category_id = rel.relation_category_id
                WHERE rc.name = 'interaction'
              ) AS interaction_count,
              (
                SELECT COUNT(DISTINCT rel.relation_id)::bigint
                FROM source_relations sr
                JOIN {}.relation rel
                  ON rel.relation_id = sr.relation_id
                JOIN {}.vocab_relation_category rc
                  ON rc.relation_category_id = rel.relation_category_id
                WHERE rc.name = 'association'
              ) AS association_count,
              (
                SELECT COUNT(*)::bigint
                FROM {}.ontology_terms ot
                WHERE ot.source_id = (SELECT source_id FROM selected_source)
              ) AS ontology_term_count,
              (
                SELECT NULL::timestamptz
              ) AS last_built_at,
              (
                SELECT EXISTS (
                  SELECT 1
                  FROM {}.entity_evidence
                  WHERE source_id = (
                    SELECT source_id FROM selected_source
                  )
                  UNION ALL
                  SELECT 1
                  FROM {}.ontology_terms
                  WHERE source_id = (
                    SELECT source_id FROM selected_source
                  )
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
        [source],
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


def _cv_label(value: object) -> str | None:
    label = getattr(value, 'definition', None) or getattr(value, 'label', None)
    if label:
        return str(label)
    return _text_or_none(value)
