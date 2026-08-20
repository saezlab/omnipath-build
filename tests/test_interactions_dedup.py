"""The fact table's grain and its provenance folding (008 T013a, FR-006, R1).

``interaction_fact_combined`` carries **one row per ordered**
``(subject_entity_id, object_entity_id, interaction_class_id)``. Ordered means
A→B and B→A stay two rows (FR-044d). One row means every resource that asserts
that endpoint pair in that class folds into it, with ``sources`` and the
reference arrays aggregated across all of them.

The class is what makes the key discriminate, and research R18 (2026-08-18)
settles where it comes from: participant-role evidence first, then
interaction-level annotation, then the predicate, then ``other``. Resolving it
from the predicate alone put 93.5 per cent of the graph in ``other`` and left
five classes empty, so the precedence is asserted here class by class.

Run::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_interactions_dedup.py -v

Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import pytest

from tests.fixtures.interaction_graph import build_interaction_fixture

DATABASE_URL = os.environ.get('DATABASE_URL')
SCRATCH = os.environ.get(
    'OMNIPATH_TEST_SCRATCH_SCHEMA_INTERACTIONS_DEDUP',
    'interactions_dedup_test',
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


def _fact(conn, subject: str, object_: str) -> dict[str, object] | None:
    """The single fact row for a fixture endpoint pair, by canonical name."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT vic.name, f.sources, f.source_count, f.reference_pubmed_ids,
                   f.reference_count, f.interaction_id
            FROM {SCRATCH}.interaction_fact_combined f
            JOIN {SCRATCH}.entity subject
              ON subject.entity_id = f.subject_entity_id
            JOIN {SCRATCH}.entity object
              ON object.entity_id = f.object_entity_id
            LEFT JOIN {SCRATCH}.vocab_interaction_class vic
              ON vic.interaction_class_id = f.interaction_class_id
            WHERE subject.canonical_identifier = %s
              AND object.canonical_identifier = %s
            """,
            (f'FIXTURE_{subject}', f'FIXTURE_{object_}'),
        )
        rows = cur.fetchall()
    assert len(rows) <= 1, f'{subject}->{object_} produced {len(rows)} rows'
    if not rows:
        return None
    name, sources, source_count, pubmed, reference_count, interaction_id = rows[0]
    return {
        'class': name,
        'sources': sorted(sources or []),
        'source_count': source_count,
        'pubmed': sorted(pubmed or []),
        'reference_count': reference_count,
        'interaction_id': interaction_id,
    }


def test_the_ordered_key_is_unique(built):
    """No endpoint pair and class appears twice."""
    conn, _stats = built
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*) FROM (
              SELECT 1 FROM {SCRATCH}.interaction_fact_combined
              GROUP BY subject_entity_id, object_entity_id,
                       interaction_class_id
              HAVING count(*) > 1
            ) duplicated
            """
        )
        assert cur.fetchone()[0] == 0


def test_three_resources_fold_into_one_row(built):
    """One row per endpoint pair and class, with provenance from all of them."""
    conn, _stats = built
    fact = _fact(conn, 'c', 'd')
    assert fact is not None, 'the signalling pair produced no fact row'
    assert fact['class'] == 'signaling'
    assert fact['sources'] == [
        'fixture_res_a',
        'fixture_res_b',
        'fixture_res_c',
    ]
    assert fact['source_count'] == 3


def test_references_aggregate_across_the_contributors(built):
    """Every contributing resource's references reach the folded row."""
    conn, _stats = built
    fact = _fact(conn, 'c', 'd')
    assert fact['pubmed'] == ['11111111', '33333333']
    assert fact['reference_count'] == 2


def test_opposite_directions_stay_two_rows(built):
    """The key is ordered, so A→B and B→A are never merged (FR-044d)."""
    conn, _stats = built
    forward = _fact(conn, 'g', 'h')
    reverse = _fact(conn, 'h', 'g')
    assert forward is not None and reverse is not None
    assert forward['class'] == reverse['class'] == 'signaling'


def test_every_fact_row_links_to_its_header(built):
    """The projection keeps the header link the general API reads (T014)."""
    conn, _stats = built
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM {SCRATCH}.interaction_fact_combined '
            f'WHERE interaction_id IS NULL'
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            f"""
            SELECT count(*)
            FROM {SCRATCH}.interaction_fact_combined f
            LEFT JOIN {SCRATCH}.interaction i
              ON i.interaction_id = f.interaction_id
            WHERE i.interaction_id IS NULL
            """
        )
        assert cur.fetchone()[0] == 0


def test_every_header_has_its_participants(built):
    """A binary interaction records both parties, with its arity (T014, §2)."""
    conn, _stats = built
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*)
            FROM {SCRATCH}.interaction i
            LEFT JOIN (
              SELECT interaction_id, count(*) AS parties
              FROM {SCRATCH}.interaction_party
              GROUP BY interaction_id
            ) p ON p.interaction_id = i.interaction_id
            WHERE p.parties IS DISTINCT FROM i.arity
            """
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize(
    ('subject', 'object_', 'expected'),
    [
        # Tier 1, participant-role evidence: a ligand on one side and a
        # receptor on the other, under the generic `interacts_with` verb the
        # predicate vocabulary cannot classify (research R18).
        ('a', 'b', 'ligand_receptor'),
        # Tier 2, interaction-level annotation, again under `interacts_with`.
        ('k', 'l', 'orthosteric'),
        ('m', 'n', 'allosteric'),
        # Tier 3, the predicate.
        ('c', 'd', 'signaling'),
        ('o', 'p', 'transport'),
        # The fallback, which stays a real class rather than a dumping ground.
        ('q', 'r', 'other'),
    ],
)
def test_the_class_follows_the_r18_precedence(built, subject, object_, expected):
    """The class comes from what the resource asserts, not from the verb alone."""
    conn, _stats = built
    fact = _fact(conn, subject, object_)
    assert fact is not None, f'{subject}->{object_} produced no fact row'
    assert fact['class'] == expected


def test_no_fact_row_is_left_without_a_class(built):
    """Every row reaches at least `other`, so the class key never goes NULL."""
    conn, _stats = built
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM {SCRATCH}.interaction_fact_combined '
            f'WHERE interaction_class_id IS NULL'
        )
        assert cur.fetchone()[0] == 0


def test_the_step_reports_its_per_class_counts(built):
    """R18 asks for per-class row counts on every run, so they are returned."""
    conn, _stats = built
    _connection, stats = built
    assert stats.rows_by_class['ligand_receptor'] == 1
    assert stats.rows_by_class['signaling'] >= 3
    assert set(stats.rows_by_class) >= {
        'ligand_receptor',
        'orthosteric',
        'allosteric',
        'signaling',
        'transport',
        'other',
    }
