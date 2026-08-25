"""Build-side tests for the MetSigDB membership substrate (cycle 010).

Run against a built instance::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:55435/omnipath \
        uv run --with pytest pytest tests/test_metsigdb_build.py -v

Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import pytest

DATABASE_URL = os.environ.get('DATABASE_URL')

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; the MetSigDB substrate needs a built database',
)

TABLE = 'metsigdb_membership'

# The shared filter columns the serving layer is allowed to filter on. The
# contract fixes them, and each one carries an index.
FILTER_COLUMNS = (
    'resource',
    'set_type',
    'organism',
    'set_source_id',
    'metabolite_entity_id',
)


@pytest.fixture(scope='module')
def conn():
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    # Autocommit, so one failing assertion query does not poison the
    # connection for every test after it.
    connection.autocommit = True
    try:
        yield connection
    finally:
        connection.close()


def _rows(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def test_membership_table_exists(conn):
    """The build publishes the substrate under its contracted name."""
    rows = _rows(
        conn,
        'SELECT to_regclass(%s) IS NOT NULL',
        [f'public.{TABLE}'],
    )
    assert rows[0][0], f'{TABLE} is absent; run the MetSigDB build step'


def test_membership_table_columns(conn):
    """Every contract field is a column, and the row carries its build stamp."""
    columns = {
        name
        for (name,) in _rows(
            conn,
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_schema = %s AND table_name = %s',
            ['public', TABLE],
        )
    }
    expected = {
        'metabolite_entity_id',
        'metabolite_label',
        'metabolite_entity_type',
        'inchikey',
        'smiles',
        'hmdb',
        'pubchem',
        'chebi',
        'kegg',
        'resource',
        'set_source_id',
        'set_label',
        'set_type',
        'organism',
        'set_size',
        'set_context',
        'provenance_source',
        'provenance_record',
        'build_id',
    }
    assert expected <= columns, f'missing columns: {sorted(expected - columns)}'
    # `inchi` left the contract: no InChI identifier type exists in the schema.
    assert 'inchi' not in columns


def test_membership_row_identity(conn):
    """Row identity is the primary key, so a rebuild refreshes in place."""
    rows = _rows(
        conn,
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        ORDER BY a.attname
        """,
        [f'public.{TABLE}'],
    )
    assert [name for (name,) in rows] == [
        'metabolite_entity_id',
        'resource',
        'set_source_id',
    ]


def test_filter_columns_are_indexed(conn):
    """The serving layer filters on five columns, and each one has an index."""
    indexed = {
        name
        for (name,) in _rows(
            conn,
            """
            SELECT DISTINCT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
            WHERE i.indrelid = %s::regclass
            """,
            [f'public.{TABLE}'],
        )
    }
    missing = set(FILTER_COLUMNS) - indexed
    assert not missing, f'unindexed filter columns: {sorted(missing)}'


def test_set_type_is_constrained(conn):
    """Only the three supported set semantics may be stored."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype = 'c' "
            "AND pg_get_constraintdef(oid) ILIKE %s",
            [f'public.{TABLE}', '%set_type%'],
        )
        assert cur.fetchone()[0] >= 1


def test_ddl_is_idempotent(conn):
    """Applying the DDL twice changes nothing, so a rebuild is safe."""
    from omnipath_build.metsigdb import ensure_membership_table

    before = _rows(
        conn,
        'SELECT column_name, data_type FROM information_schema.columns '
        'WHERE table_schema = %s AND table_name = %s ORDER BY column_name',
        ['public', TABLE],
    )
    ensure_membership_table(conn)
    ensure_membership_table(conn)
    after = _rows(
        conn,
        'SELECT column_name, data_type FROM information_schema.columns '
        'WHERE table_schema = %s AND table_name = %s ORDER BY column_name',
        ['public', TABLE],
    )
    assert before == after
