"""Direct SQL validation of the published MetSigDB substrate (cycle 010).

These are the V-queries of `specs/010-metsigdb-subset/sql-validation.md`, run as
tests. They run after every resource load, not only at the end. Run against a
built instance::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:55435/omnipath \
        uv run --with pytest --with psycopg2-binary \
        pytest tests/test_metsigdb_sql_validation.py -v
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

# Measured on 2026-08-25 and frozen into contracts/row-contract.md. A build that
# departs from these needs an explanation before it is accepted.
EXPECTED = {
    'Reactome': {'rows': 24235, 'sets': 2239, 'metabolites': 2191},
    'WikiPathways': {'rows': 5431, 'sets': 818, 'metabolites': 2789},
    'KEGG': {'rows': 4969, 'sets': 176, 'metabolites': 1799},
    'MACdb': {'rows': 20291, 'sets': 269, 'metabolites': 5389},
    'ClassyFire': {'rows': 3453875, 'sets': 2863, 'metabolites': 145937},
}

TOLERANCE = 0.02


@pytest.fixture(scope='module')
def conn():
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    connection.autocommit = True
    try:
        yield connection
    finally:
        connection.close()


def _rows(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _loaded_resources(conn):
    return {name for (name,) in _rows(conn, f'SELECT DISTINCT resource FROM {TABLE}')}


def test_v1_every_row_has_a_canonical_metabolite(conn):
    rows = _rows(
        conn,
        f"""
        SELECT count(*) FROM {TABLE} m
        LEFT JOIN entity e
          ON e.entity_id = m.metabolite_entity_id AND e.entity_type_id = 5
        WHERE e.entity_id IS NULL
        """,
    )
    assert rows[0][0] == 0


def test_v2_no_duplicate_memberships(conn):
    rows = _rows(
        conn,
        f"""
        SELECT resource, set_source_id, metabolite_entity_id, count(*)
        FROM {TABLE} GROUP BY 1, 2, 3 HAVING count(*) > 1
        """,
    )
    assert rows == []


def test_v3_mandatory_fields_are_present(conn):
    rows = _rows(
        conn,
        f"""
        SELECT count(*) FROM {TABLE}
        WHERE metabolite_label IS NULL
           OR metabolite_entity_type IS NULL
           OR set_type IS NULL
           OR set_size IS NULL
           OR provenance_source IS NULL
           OR build_id IS NULL
        """,
    )
    assert rows[0][0] == 0


def test_v4_set_type_matches_the_resource_rule(conn):
    rows = _rows(
        conn,
        f"""
        SELECT resource, set_type, count(*) FROM {TABLE}
        WHERE (resource, set_type) NOT IN (
          ('KEGG', 'pathway'), ('Reactome', 'pathway'),
          ('WikiPathways', 'pathway'), ('MACdb', 'disease'),
          ('ClassyFire', 'chemical_class'))
        GROUP BY 1, 2
        """,
    )
    assert rows == []


def test_v5_set_size_matches_the_published_population(conn):
    rows = _rows(
        conn,
        f"""
        SELECT resource, set_source_id, min(set_size), count(*)
        FROM {TABLE} GROUP BY 1, 2
        HAVING min(set_size) <> count(*) OR min(set_size) <> max(set_size)
        """,
    )
    assert rows == []


def test_v6_only_in_scope_resources_appear(conn):
    assert _loaded_resources(conn) <= set(EXPECTED) | {
        'KEGG',
        'WikiPathways',
        'MACdb',
        'ClassyFire',
    }


def test_v7_organism_comes_from_the_source(conn):
    """Reactome is human; WikiPathways spans many species; the rest are null.

    An earlier version derived the organism from an `R-HSA-` prefix, which
    published null for every WikiPathways pathway even though the source
    records a species for most of them.
    """
    rows = _rows(
        conn,
        f"""
        SELECT resource, count(organism), count(DISTINCT organism)
        FROM {TABLE} GROUP BY 1
        """,
    )
    seen = {resource: (n, taxa) for resource, n, taxa in rows}
    if 'Reactome' in seen:
        assert seen['Reactome'][1] == 1
        assert _rows(
            conn, f"SELECT DISTINCT organism FROM {TABLE} WHERE resource='Reactome'"
        ) == [(9606,)]
    if 'WikiPathways' in seen:
        assert seen['WikiPathways'][0] > 0
        assert seen['WikiPathways'][1] > 10, 'WikiPathways is many-species'
    for resource in ('KEGG', 'MACdb', 'ClassyFire'):
        if resource in seen:
            assert seen[resource][0] == 0, resource


def test_v8_row_counts_match_the_frozen_population(conn):
    loaded = _loaded_resources(conn)
    measured = {
        resource: {'rows': rows, 'sets': sets, 'metabolites': metabolites}
        for resource, rows, sets, metabolites in _rows(
            conn,
            f"""
            SELECT resource, count(*), count(DISTINCT set_source_id),
                   count(DISTINCT metabolite_entity_id)
            FROM {TABLE} GROUP BY 1
            """,
        )
    }
    for resource, expected in EXPECTED.items():
        if resource not in loaded:
            pytest.skip(f'{resource} is not loaded yet')
        for key, want in expected.items():
            got = measured[resource][key]
            assert abs(got - want) <= want * TOLERANCE, (
                f'{resource}.{key}: {got} against a frozen {want}'
            )


def test_v9_every_row_carries_one_build_stamp(conn):
    if not _loaded_resources(conn):
        pytest.skip('the substrate is empty')
    stamps = _rows(conn, f'SELECT DISTINCT build_id FROM {TABLE}')
    assert len(stamps) == 1
    manifest = _rows(conn, 'SELECT build_id FROM build_manifest')
    assert stamps[0][0] == manifest[0][0]


def test_set_labels_come_from_the_ontology_terms(conn):
    """Three resources name every set. Two carry no name in this build.

    The first published substrate had no name anywhere, because the freeze
    checked `ontology_terms`, which is empty, instead of
    `entity_ontology_term`, which holds 711,731 rows.
    """
    rows = _rows(
        conn,
        f'SELECT resource, count(*), count(set_label) FROM {TABLE} GROUP BY 1',
    )
    seen = {resource: (total, named) for resource, total, named in rows}
    for resource in ('Reactome', 'MACdb', 'ClassyFire'):
        if resource in seen:
            total, named = seen[resource]
            assert named == total, f'{resource}: {named} of {total} named'
    for resource in ('KEGG', 'WikiPathways'):
        if resource in seen:
            assert seen[resource][1] == 0, resource


def test_a_named_set_reads_as_a_name(conn):
    """A spot check, because a non-null column can still hold the identifier."""
    rows = _rows(
        conn,
        f"""
        SELECT DISTINCT set_source_id, set_label FROM {TABLE}
        WHERE set_source_id IN ('R-HSA-1059683', 'CHEMONTID:0000118', '46')
          AND set_label IS NOT NULL
        """,
    )
    labels = dict(rows)
    assert labels.get('R-HSA-1059683') == 'Interleukin-6 signaling'
    assert labels.get('CHEMONTID:0000118') == 'Ketones'
    assert labels.get('46') == 'Colorectal Cancer (CRC)'
