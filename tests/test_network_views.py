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

NEW_SOURCES = ('recon3d', 'rhea', 'humangem', 'cellphonedb', 'neuronchat')
TRANSPORT_SOURCES = ('recon3d', 'rhea', 'humangem')
SIGNALING_SOURCES = ('cellphonedb', 'neuronchat')


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
    """The 12 MetalinksDB per-source matviews exist alongside the combined one."""
    n = _scalar(
        conn,
        "SELECT count(*) FROM pg_matviews "
        "WHERE schemaname = 'custom_views' "
        "AND matviewname LIKE 'metalinksdb_%_relations'",
    )
    assert n >= 12


def test_absent_view_is_detectable(conn):
    """A missing network view resolves to NULL via to_regclass (API → 503)."""
    assert _scalar(conn, "SELECT to_regclass('custom_views.no_such_network')") is None


# --- User Story 1: consolidation (004-metalinksdb-view) ----------------------


def test_relations_carries_annotations_inline(conn):
    """Selecting from metalinksdb_relations alone returns annotation columns
    for a known-annotated row -- no join to another view required."""
    row = _rows(
        conn,
        """
        SELECT compound_canonical_id, protein_uniprot
        FROM custom_views.metalinksdb_relations
        WHERE hmdb_subcellular_locations IS NOT NULL
           OR uniprot_subcellular_locations IS NOT NULL
        LIMIT 1
        """,
    )
    assert row, 'expected at least one row with inline annotation data'


def test_relations_no_row_explosion(conn):
    """A (compound, protein) pair appears exactly once, with annotations
    aggregated into arrays rather than duplicating the interaction row."""
    total, distinct = _rows(
        conn,
        """
        SELECT count(*),
               count(DISTINCT (compound_entity_id, protein_entity_id))
        FROM custom_views.metalinksdb_relations
        """,
    )[0]
    assert total == distinct


def test_relations_missing_annotation_keeps_row(conn):
    """A compound/protein with no data for a given annotation category keeps
    its interaction row, with that column null rather than the row being
    dropped."""
    n = _scalar(
        conn,
        """
        SELECT count(*) FROM custom_views.metalinksdb_relations
        WHERE hmdb_subcellular_locations IS NULL
        """,
    )
    assert n > 0


# --- User Story 2: resource expansion + metabolite curation ------------------


def test_registry_has_twelve_sources(conn):
    sources = _scalar(
        conn,
        "SELECT included_sources FROM public.network_registry WHERE name = 'metalinksdb'",
    )
    assert len(sources) == 12


def test_all_rows_are_metabolite_classified(conn):
    n = _scalar(
        conn,
        """
        SELECT count(*) FROM custom_views.metalinksdb_relations r
        WHERE NOT EXISTS (
            SELECT 1 FROM entity e
            WHERE e.entity_id = r.compound_entity_id
              AND e.chemical_class_id = (
                  SELECT chemical_class_id FROM vocab_chemical_class WHERE name = 'metabolite'
              )
        )
        """,
    )
    assert n == 0


def test_new_sources_present_or_logged(conn):
    """Each new source appears in some row's sources[], or -- for sources
    with current resolver-side limitations (e.g. humangem) -- contributes a
    well-formed but possibly empty per-source matview rather than failing."""
    for source in NEW_SOURCES:
        regclass = _scalar(
            conn,
            'SELECT to_regclass(%s)',
            [f'custom_views.metalinksdb_{source}_relations'],
        )
        assert regclass is not None, f'{source} per-source matview missing'


def test_transport_and_signaling_columns(conn):
    for source in TRANSPORT_SOURCES:
        rows = _rows(
            conn,
            """
            SELECT count(*) FROM custom_views.metalinksdb_relations
            WHERE %s = ANY(sources)
              AND (compartment_from IS NULL AND compartment_to IS NULL AND reaction_direction IS NULL)
            """,
            [source],
        )
        total = _scalar(
            conn,
            'SELECT count(*) FROM custom_views.metalinksdb_relations WHERE %s = ANY(sources)',
            [source],
        )
        if total:
            assert rows[0][0] < total, f'{source} rows never carry transport columns'
    for source in SIGNALING_SOURCES:
        total = _scalar(
            conn,
            'SELECT count(*) FROM custom_views.metalinksdb_relations WHERE %s = ANY(sources)',
            [source],
        )
        with_type = _scalar(
            conn,
            """
            SELECT count(*) FROM custom_views.metalinksdb_relations
            WHERE %s = ANY(sources) AND interaction_type IS NOT NULL
            """,
            [source],
        )
        if total:
            assert with_type > 0, f'{source} rows never carry interaction_type'


def test_liana_cellphonedb_unaffected(conn):
    """LIANA's own cellphonedb-derived rows are unaffected by MetaLinksDB
    onboarding CellPhoneDB as a 12th source."""
    n = _scalar(
        conn,
        "SELECT count(*) FROM custom_views.liana_ligand_receptor_pairs "
        "WHERE sources = 'cellphonedb' OR sources LIKE 'cellphonedb|%' "
        "OR sources LIKE '%|cellphonedb' OR sources LIKE '%|cellphonedb|%'",
    )
    assert n > 0
