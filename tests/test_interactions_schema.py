"""Schema-existence and population tests for the interaction model (008).

Constitution III: every new table this cycle adds is asserted to exist and to
carry rows after a build. Four tables make up the model — the header
``interaction`` (data model §1), the participant ``interaction_party`` (§2),
the participant-role vocabulary ``vocab_relation_role`` (§7) and the flat
binary projection ``interaction_fact_combined`` (§3), the hot query target.

The schema half runs against a throwaway scratch schema, so it needs no data.
The population half reads the built schema and needs a build — a capped
``MAX_RECORDS`` build is enough, because the assertion is that the derive step
produced rows at all, not how many.

Run::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_interactions_schema.py -v

Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import pytest

DATABASE_URL = os.environ.get('DATABASE_URL')
SCHEMA = os.environ.get('OMNIPATH_PG_SCHEMA', 'public')
SCRATCH = os.environ.get(
    'OMNIPATH_TEST_SCRATCH_SCHEMA_INTERACTIONS',
    'interactions_schema_test',
)

# The four tables the interaction model adds (data model §1, §2, §3, §7).
INTERACTION_TABLES = (
    'interaction',
    'interaction_party',
    'vocab_relation_role',
    'interaction_fact_combined',
)

# Sign and direction are three-valued (FR-044, research R15): NULL means no
# contributing resource asserts the attribute, which is a different statement
# from an asserted `false`. A NOT NULL or a DEFAULT on any of these three
# destroys the distinction, so the test pins both.
THREE_VALUED_COLUMNS = ('is_directed', 'is_stimulation', 'is_inhibition')

# The ordered key of the fact table: A→B and B→A are two rows (FR-044d).
FACT_KEY_COLUMNS = [
    'subject_entity_id',
    'object_entity_id',
    'interaction_class_id',
]

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; interaction schema test needs a Postgres',
)


@pytest.fixture(scope='module')
def conn():
    """A read-only connection to the built database."""
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    # Read-only queries: autocommit keeps one failing statement from aborting
    # the transaction and masking the rest with InFailedSqlTransaction.
    connection.autocommit = True
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope='module')
def scratch(conn):
    """The schema built into a throwaway namespace, with no data in it."""
    import psycopg2

    from omnipath_build.db import schema as build_schema

    writable = psycopg2.connect(DATABASE_URL)
    try:
        build_schema.ensure_schema(
            writable,
            schema=SCRATCH,
            drop_existing=True,
        )
        writable.commit()
        yield SCRATCH
    finally:
        with writable.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {SCRATCH} CASCADE')
        writable.commit()
        writable.close()


def _columns(conn, schema: str, table: str) -> dict[str, tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
        return {
            name: (data_type, nullable, default)
            for name, data_type, nullable, default in cur.fetchall()
        }


def _row_count(conn, schema: str, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM {schema}.{table}')
        return cur.fetchone()[0]


@pytest.mark.parametrize('table', INTERACTION_TABLES)
def test_table_is_created(conn, scratch, table):
    """Creating the schema creates every table of the interaction model."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT to_regclass('{scratch}.{table}')")
        assert cur.fetchone()[0] is not None, f'{table} was not created'


def test_fact_table_carries_the_hot_columns(conn, scratch):
    """The fact table carries the hot filter columns of data model §3."""
    columns = _columns(conn, scratch, 'interaction_fact_combined')
    expected = {
        'subject_entity_id',
        'object_entity_id',
        'interaction_class_id',
        'subject_organism',
        'object_organism',
        'affinity',
        'pchembl',
        'score',
        'sources',
        'source_count',
        'dataset_tags',
        'reference_count',
        'attributes',
        'interaction_id',
        'sign_source_count',
        'direction_source_count',
    }
    assert expected <= set(columns), (
        f'missing hot columns: {sorted(expected - set(columns))}'
    )


@pytest.mark.parametrize('column', THREE_VALUED_COLUMNS)
def test_sign_and_direction_are_three_valued(conn, scratch, column):
    """NULL means unasserted, so these three are nullable and undefaulted."""
    columns = _columns(conn, scratch, 'interaction_fact_combined')
    assert column in columns, f'{column} is missing from interaction_fact_combined'
    data_type, nullable, default = columns[column]
    assert data_type == 'boolean'
    assert nullable == 'YES', (
        f'{column} is NOT NULL, which erases "no resource asserts it"'
    )
    assert default is None, (
        f'{column} carries a default ({default}); an unasserted attribute '
        f'must stay NULL, never become an asserted false'
    )


def test_fact_key_is_unique_and_ordered(conn, scratch):
    """The key is ordered: A→B and B→A are two rows, never merged."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT array_agg(attname ORDER BY ordinality)
            FROM pg_index idx
            JOIN pg_class rel ON rel.oid = idx.indrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            CROSS JOIN LATERAL unnest(idx.indkey) WITH ORDINALITY AS k(
              attnum, ordinality
            )
            JOIN pg_attribute att
              ON att.attrelid = rel.oid AND att.attnum = k.attnum
            WHERE ns.nspname = %s
              AND rel.relname = 'interaction_fact_combined'
              AND idx.indisunique
            GROUP BY idx.indexrelid
            """,
            (scratch,),
        )
        keys = [row[0] for row in cur.fetchall()]
    assert FACT_KEY_COLUMNS in keys, (
        f'no unique index on the ordered endpoint/class key; found {keys}'
    )


def test_role_vocabulary_is_populated_by_name(conn, scratch):
    """The role vocabulary is seeded with the roles of data model §7."""
    with conn.cursor() as cur:
        cur.execute(f'SELECT name FROM {scratch}.vocab_relation_role')
        names = {row[0] for row in cur.fetchall()}
    assert {
        'subject',
        'object',
        'reactant',
        'product',
        'enzyme',
        'cofactor',
        'regulator',
        'member',
    } <= names


@pytest.mark.parametrize('table', INTERACTION_TABLES)
def test_table_is_populated_by_the_build(conn, table):
    """Every table of the interaction model carries rows after a build."""
    assert _row_count(conn, SCHEMA, table) > 0, (
        f'{SCHEMA}.{table} is empty; the derive step produced no rows'
    )


def test_every_fact_row_links_to_a_header(conn):
    """The projection keeps its link to the endpoint-independent header."""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM {SCHEMA}.interaction_fact_combined '
            f'WHERE interaction_id IS NULL'
        )
        orphans = cur.fetchone()[0]
    assert orphans == 0, f'{orphans} fact rows carry no header link'


def test_the_ordered_key_holds_in_the_built_data(conn):
    """Both directions of a pair are separate rows, and neither is doubled."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*)
            FROM (
              SELECT 1
              FROM {SCHEMA}.interaction_fact_combined
              GROUP BY
                subject_entity_id,
                object_entity_id,
                interaction_class_id
              HAVING count(*) > 1
            ) AS duplicated
            """
        )
        assert cur.fetchone()[0] == 0
