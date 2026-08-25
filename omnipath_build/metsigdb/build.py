"""Build the MetSigDB membership substrate from the build database.

The step reads what the core build already wrote. It parses no upstream
resource and downloads nothing: `relation_evidence`,
`entity_evidence_resolution` and `entity_ontology_relation` already hold every
membership, resolved against the canonical entity layer.

Each resource runs the same three phases. An extraction query stages one row
per published pair, a shared projection reads the metabolite side off the
canonical layer, and a shared upsert writes the rows under their deterministic
identity. Only the extraction differs per resource.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg2.errors
import psycopg2.extensions
from psycopg2 import sql

from omnipath_build.metsigdb.mapping import (
    CHEMICAL_ENTITY_TYPE,
    ResourceRule,
)

_SQL_DIR = Path(__file__).with_name('sql')

logger = logging.getLogger(__name__)

TABLE = 'metsigdb_membership'

# Temp-table headroom for the extractions. A temp table lives in the backend's
# local buffers, and the default 8 MB is far too small for intermediates of a
# few million rows: Postgres reports "no empty local buffer available" rather
# than spilling. The setting only takes effect before a session touches its
# first temp table, so the build sets it once per connection and lets a later
# call fail quietly.
TEMP_BUFFERS = os.environ.get('METSIGDB_TEMP_BUFFERS', '1GB')


@dataclass(frozen=True)
class ResourceLoadStats:
    """What one resource cost and what it published."""

    resource: str
    rows: int
    sets: int
    metabolites: int
    removed: int
    seconds: float


def _sql_text(name: str) -> str:
    return (_SQL_DIR / name).read_text(encoding='utf-8')


def ensure_membership_table(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> None:
    """Create the membership table and its filter indexes if they are absent.

    The DDL is idempotent. Applying it to a populated table leaves both the
    schema and the rows untouched, so a rebuild can call it first without a
    guard.
    """
    with conn.cursor() as cur:
        # Unqualified names in the DDL land in the target schema. public.*
        # still resolves, so the table can reference the canonical layer.
        cur.execute(
            sql.SQL('SET search_path = {}, public').format(sql.Identifier(schema))
        )
        cur.execute(_sql_text('membership_table.sql'))
        cur.execute('RESET search_path')
    conn.commit()
    logger.info('metsigdb: %s.%s is present', schema, TABLE)


def _widen_temp_buffers(cur) -> None:
    """Give the backend room for the extraction intermediates.

    Postgres refuses the change once the session has touched a temp table, so a
    connection that already loaded one resource keeps the value it got then.
    That is the wanted behavior, and the refusal is not an error.
    """
    try:
        cur.execute(f"SET temp_buffers = '{TEMP_BUFFERS}'")
    except psycopg2.errors.ActiveSqlTransaction:  # pragma: no cover
        logger.debug('metsigdb: temp_buffers already fixed for this session')


def _scalar(cur, query: str, params=None):
    cur.execute(query, params)
    row = cur.fetchone()
    return None if row is None else row[0]


def _source_id(cur, name: str) -> int:
    source_id = _scalar(cur, 'SELECT source_id FROM data_source WHERE name = %s', [name])
    if source_id is None:
        raise LookupError(f'metsigdb: source {name!r} is not loaded in this build')
    return source_id


def _entity_type_id(cur, name: str) -> int:
    type_id = _scalar(
        cur, 'SELECT entity_type_id FROM vocab_entity_type WHERE name = %s', [name]
    )
    if type_id is None:
        raise LookupError(f'metsigdb: entity type {name!r} is absent')
    return type_id


def build_id(conn: psycopg2.extensions.connection) -> str:
    """The build the substrate is stamped with, read from the manifest.

    A membership row records which build produced it, so a consumer can tell a
    stale row from a current one. The manifest holds exactly one row.
    """
    with conn.cursor() as cur:
        stamp = _scalar(cur, 'SELECT build_id FROM build_manifest')
    if stamp is None:
        raise LookupError('metsigdb: build_manifest is empty; run the derive first')
    return stamp


def _provenance_source(cur, source_name: str) -> str:
    """The input module and commit that parsed the resource.

    The MetSigDB step downloads nothing, so it cites the provenance the core
    build recorded rather than restating a URL of its own.
    """
    cur.execute(
        'SELECT input_module, input_module_commit FROM resources WHERE resource_id = %s',
        [source_name],
    )
    row = cur.fetchone()
    if row is None or not row[0]:
        return source_name
    module, commit = row
    return f'{module}@{commit[:12]}' if commit else module


def load_resource(
    conn: psycopg2.extensions.connection,
    rule: ResourceRule,
    *,
    stamp: str,
    max_records: int | None = None,
) -> ResourceLoadStats:
    """Extract, project and publish one resource into the substrate.

    Rows this build did not write are removed for that resource alone, so a
    source that shrinks upstream shrinks the substrate instead of leaving
    memberships behind.
    """
    started = time.monotonic()
    with conn.cursor() as cur:
        _widen_temp_buffers(cur)
        params = {
            'source_id': _source_id(cur, rule.source_name),
            'set_entity_type_id': _entity_type_id(cur, rule.set_entity_type),
            'chemical_entity_type_id': _entity_type_id(cur, CHEMICAL_ENTITY_TYPE),
            'max_records': max_records,
        }
        if rule.hierarchy_source_name:
            params['hierarchy_source_id'] = _source_id(cur, rule.hierarchy_source_name)

        cur.execute(_sql_text(rule.extraction), params)
        cur.execute('CREATE INDEX ON metsigdb_stage (metabolite_entity_id)')
        cur.execute('CREATE INDEX ON metsigdb_stage (set_source_id)')
        cur.execute('ANALYZE metsigdb_stage')
        staged = _scalar(cur, 'SELECT count(*) FROM metsigdb_stage')

        cur.execute(_sql_text('publish_membership.sql'))

        cur.execute(
            sql.SQL(_sql_text('upsert_membership.sql')).format(
                # A SQL expression, not a bind parameter: the organism is
                # derived from the set identifier. The text is a constant of
                # `mapping.py`, never anything a caller supplies.
                organism=sql.SQL(rule.organism_sql),
            ),
            {
                'resource': rule.name,
                'set_type': rule.set_type,
                'provenance_source': _provenance_source(cur, rule.source_name),
                'build_id': stamp,
            },
        )

        cur.execute(
            f'DELETE FROM {TABLE} WHERE resource = %s AND build_id <> %s',
            [rule.name, stamp],
        )
        removed = cur.rowcount

        cur.execute(
            f"""
            SELECT count(*), count(DISTINCT set_source_id),
                   count(DISTINCT metabolite_entity_id)
            FROM {TABLE} WHERE resource = %s
            """,
            [rule.name],
        )
        rows, sets, metabolites = cur.fetchone()

        cur.execute('DROP TABLE IF EXISTS metsigdb_stage')
        cur.execute('DROP TABLE IF EXISTS metsigdb_projection')
    conn.commit()

    stats = ResourceLoadStats(
        resource=rule.name,
        rows=rows,
        sets=sets,
        metabolites=metabolites,
        removed=removed,
        seconds=round(time.monotonic() - started, 1),
    )
    logger.info(
        'metsigdb: %s staged=%s rows=%s sets=%s metabolites=%s removed=%s seconds=%s',
        stats.resource, staged, stats.rows, stats.sets,
        stats.metabolites, stats.removed, stats.seconds,
    )
    return stats
