"""Sign and direction on the projection (008 T013b/T013d, FR-044, R15).

``is_directed``, ``is_stimulation`` and ``is_inhibition`` are **three-valued**.
NULL means *no contributing resource asserts the attribute*, which is a
different statement from an asserted ``false`` — 97.4 per cent of the graph's
edges carry no sign at all, so a design that defaulted them to ``false`` would
mislabel 13.9 million edges as "known not stimulatory". Where resources
disagree, **both** sign flags are true: the disagreement is represented, never
resolved by a silent winner. And ``sources``/``references`` list **every**
contributor, including the ones asserting neither sign nor direction, so
``sign_source_count <= cardinality(sources)`` is a real inequality.

The second half covers T013d: the sign-conflict summary T013c measures on every
build — signed rows, both-flags-true rows and their percentage, and the
single-resource versus cross-resource split — is produced and reaches the build
manifest **without** entering the ``build_id`` hash, because it is a
measurement of the build, not part of its identity.

Run::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_interactions_sign_direction.py -v

Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from tests.fixtures.interaction_graph import build_interaction_fixture

DATABASE_URL = os.environ.get('DATABASE_URL')
SCRATCH = os.environ.get(
    'OMNIPATH_TEST_SCRATCH_SCHEMA_INTERACTIONS_SIGN',
    'interactions_sign_test',
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; the projection test needs a Postgres',
)


@pytest.fixture(scope='module')
def built():
    """The fixture graph, projected by the derive step, in a scratch schema."""
    import psycopg2

    from omnipath_build.db import schema as build_schema
    from omnipath_build.db.derived_tables import rebuild_interaction_tables

    connection = psycopg2.connect(DATABASE_URL)
    try:
        build_schema.ensure_schema(
            connection,
            schema=SCRATCH,
            drop_existing=True,
        )
        connection.commit()
        build_interaction_fixture(connection, SCRATCH)
        stats = rebuild_interaction_tables(connection, schema=SCRATCH)
        yield connection, stats
    finally:
        with connection.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {SCRATCH} CASCADE')
        connection.commit()
        connection.close()


def _row(conn, subject: str, object_: str) -> dict[str, object]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT f.is_directed, f.is_stimulation, f.is_inhibition,
                   f.sign_source_count, f.direction_source_count,
                   f.sources, f.source_count
            FROM {SCRATCH}.interaction_fact f
            JOIN {SCRATCH}.entity subject
              ON subject.entity_id = f.subject_entity_id
            JOIN {SCRATCH}.entity object
              ON object.entity_id = f.object_entity_id
            WHERE subject.canonical_identifier = %s
              AND object.canonical_identifier = %s
            """,
            (f'FIXTURE_{subject}', f'FIXTURE_{object_}'),
        )
        rows = cur.fetchall()
    assert len(rows) == 1, f'{subject}->{object_} produced {len(rows)} rows'
    keys = (
        'is_directed',
        'is_stimulation',
        'is_inhibition',
        'sign_source_count',
        'direction_source_count',
        'sources',
        'source_count',
    )
    return dict(zip(keys, rows[0]))


def test_an_unasserted_sign_stays_null(built):
    """No resource asserts a sign, so the columns are NULL — never `false`."""
    conn, _stats = built
    row = _row(conn, 'e', 'f')
    assert row['is_stimulation'] is None, (
        'an unasserted sign became an asserted value'
    )
    assert row['is_inhibition'] is None
    assert row['sign_source_count'] == 0


def test_disagreeing_resources_set_both_flags(built):
    """One resource says stimulation, another inhibition: both surface."""
    conn, _stats = built
    row = _row(conn, 'c', 'd')
    assert row['is_stimulation'] is True
    assert row['is_inhibition'] is True


def test_a_contributor_asserting_no_sign_still_counts_as_provenance(built):
    """FR-044c: attribute-poor evidence stays in `sources`."""
    conn, _stats = built
    row = _row(conn, 'c', 'd')
    assert 'fixture_res_c' in row['sources'], (
        'the resource asserting neither sign nor direction was dropped'
    )
    assert row['sign_source_count'] == 2
    assert row['sign_source_count'] <= len(row['sources'])
    assert row['source_count'] == len(row['sources'])


def test_the_sign_source_count_never_exceeds_the_sources(built):
    """The inequality holds on every row, not only the constructed one."""
    conn, _stats = built
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*) FROM {SCRATCH}.interaction_fact
            WHERE sign_source_count > cardinality(sources)
               OR direction_source_count > cardinality(sources)
            """
        )
        assert cur.fetchone()[0] == 0


def test_an_opposite_direction_pair_is_two_rows(built):
    """A→B and B→A are never merged into one bidirectional record (FR-044d)."""
    conn, _stats = built
    forward = _row(conn, 'g', 'h')
    reverse = _row(conn, 'h', 'g')
    assert forward['is_directed'] is True
    assert reverse['is_directed'] is True


def test_direction_is_null_where_no_predicate_speaks_to_it(built):
    """`has_member` says nothing about direction, so the column stays NULL."""
    conn, _stats = built
    row = _row(conn, 'q', 'r')
    assert row['is_directed'] is None
    assert row['direction_source_count'] == 0



def test_a_symmetric_predicate_does_not_assert_undirectedness(built):
    """`interacts_with` leaves `is_directed` NULL, it does not write false.

    Decided 2026-08-18, reverting a first pass that read a symmetric predicate as
    an assertion of undirectedness and wrote false on 8.2M rows. The predicate
    vocabulary is a coarse ontology layer the resources did not choose per
    interaction, so a symmetric verb is not a resource saying "this interaction
    has no direction". FR-044a: an unasserted attribute never becomes an asserted
    false.
    """
    conn, _stats = built
    row = _row(conn, 'a', 'b')
    assert row['is_directed'] is None
    assert row['direction_source_count'] == 0


def test_no_fact_row_ever_asserts_a_false_direction(built):
    """The whole projection, not one case: `is_directed` is true or NULL."""
    conn, _stats = built
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM {SCRATCH}.interaction_fact WHERE is_directed IS FALSE'
        )
        assert cur.fetchone()[0] == 0

# --- T013d: the sign-conflict summary reaches the manifest -------------------


def test_the_step_measures_the_sign_conflict_rate(built):
    """T013c: every build reports how often both sign flags land on one row."""
    _conn, stats = built
    conflict = stats.sign_conflict
    # Three rows carry a sign: c->d and i->j, plus the orthosteric k->l, whose
    # `Agonist` annotation is both a class and a positive sign.
    assert conflict['signed_rows'] == 3
    assert conflict['both_flags_rows'] == 2
    assert conflict['both_flags_percent'] == pytest.approx(100.0 * 2 / 3, abs=0.01)
    # c->d is two resources disagreeing; i->j is one resource asserting both
    # under two predicates that share the `signaling` class.
    assert conflict['single_resource_rows'] == 1
    assert conflict['cross_resource_rows'] == 1


def test_the_summary_is_recorded_next_to_the_derive_cost(built):
    """The measurement reaches the manifest, outside the identity hash."""
    from omnipath_build.db.resources import emit_build_manifest

    conn, _stats = built
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCRATCH}.resources (
              resource_id text PRIMARY KEY,
              entity_count bigint NOT NULL DEFAULT 0,
              interaction_count bigint NOT NULL DEFAULT 0,
              association_count bigint NOT NULL DEFAULT 0,
              identifier_count bigint NOT NULL DEFAULT 0,
              ontology_term_count bigint NOT NULL DEFAULT 0,
              input_module_commit text,
              input_module_dirty boolean NOT NULL DEFAULT false
            )
            """
        )
        cur.execute(
            f"INSERT INTO {SCRATCH}.resources (resource_id) VALUES ('fixture') "
            f'ON CONFLICT DO NOTHING'
        )
    conn.commit()

    stats = emit_build_manifest(
        conn,
        schema=SCRATCH,
        derive_cost={'interaction_fact': {'seconds': 1.0, 'rows': 12}},
    )
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT build_id, package_commits, resources, '
            f'interactions_derive_cost FROM {SCRATCH}.build_manifest'
        )
        build_id, commits, resources, cost = cur.fetchone()

    conflict = cost['sign_conflict']
    assert set(conflict) >= {
        'signed_rows',
        'both_flags_rows',
        'both_flags_percent',
        'single_resource_rows',
        'cross_resource_rows',
    }
    assert conflict['signed_rows'] == 3

    # And it stayed out of the identity: the hash covers the package commits
    # and the resource inventory, nothing else.
    payload = {'package_commits': commits, 'resources': resources}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()[:12]
    assert build_id == expected == stats.build_id
