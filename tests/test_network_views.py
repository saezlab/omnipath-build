"""Integration tests for Milestone G: the network-view framework (build side).

Run against a built instance after `make network-views`, e.g. on beauty::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_network_views.py -v

Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import pytest

DATABASE_URL = os.environ.get('DATABASE_URL')

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; network-view test needs a built database',
)


@pytest.fixture(scope='module')
def conn():
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.close()


def _rows(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _scalar(conn, query, params=None):
    return _rows(conn, query, params)[0][0]


def test_registry_lists_both_networks(conn):
    names = {row for (row,) in _rows(conn, 'SELECT name FROM public.network_registry')}
    assert {'metalinksdb', 'liana'} <= names


def test_registry_metadata_well_formed(conn):
    for name, kind, schema, combined, sources in _rows(
        conn,
        'SELECT name, kind, schema_name, combined_relation, included_sources '
        'FROM public.network_registry WHERE name IN (%s, %s)',
        ['metalinksdb', 'liana'],
    ):
        assert kind and schema and combined
        assert isinstance(sources, list) and len(sources) >= 1


def test_combined_contracts_populated(conn):
    """Each network's combined contract exists and the API can read it."""
    for name in ('metalinksdb', 'liana'):
        schema, relation = _rows(
            conn,
            'SELECT schema_name, combined_relation FROM public.network_registry '
            'WHERE name = %s',
            [name],
        )[0]
        regclass = _scalar(conn, 'SELECT to_regclass(%s)', [f'{schema}.{relation}'])
        assert regclass is not None, f'{name} combined view missing'
        count = _scalar(conn, f'SELECT count(*) FROM "{schema}"."{relation}"')
        assert count > 0, f'{name} combined view empty'


def test_metalinksdb_per_source_views_present(conn):
    """The 7 MetalinksDB per-source matviews exist alongside the combined one."""
    n = _scalar(
        conn,
        "SELECT count(*) FROM pg_matviews "
        "WHERE schemaname = 'custom_views' "
        "AND matviewname LIKE 'metalinksdb_%_relations'",
    )
    assert n >= 7


def test_absent_view_is_detectable(conn):
    """A missing network view resolves to NULL via to_regclass (API → 503)."""
    assert _scalar(conn, "SELECT to_regclass('custom_views.no_such_network')") is None
