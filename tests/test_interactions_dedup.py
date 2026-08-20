"""The interaction grain and its collapse (008 T013a, FR-006, FR-044d, R1, R19).

**Amended 2026-08-20 by research R19.** The dedup claim moved. One row per
ordered ``(subject_entity_id, object_entity_id, interaction_class_id)`` is the
contract of the **collapsed output for a stated scope**, not the stored grain.
So this file asserts two things that used to be one:

* ``interaction_fact_resource`` is the **record** (data-model §3a): one row per
  ``(subject_entity_id, object_entity_id, interaction_class_id, source_id)``
  **plus the assertion signature** ``(is_directed, is_stimulation,
  is_inhibition)`` that resource states. A resource asserting two contradicting
  signs for the same endpoints under different predicates therefore keeps
  **two** rows, because the signature is part of the key.
* ``interaction_fact_combined`` is the **all-resources scope materialised**
  (data-model §3b): collapsing the record over **every** resource must
  reproduce it exactly — the same keys, the same row count, and the same values
  on every recomputed column. A materialisation that can differ from the
  collapse has already drifted from the scoped query path, which is what the
  scope rule exists to prevent.

Endpoints stay ordered on both grains, so A→B and B→A remain distinct rows
(FR-044d).

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

import psycopg2
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

# The columns a collapse recomputes for its scope (data-model §3b). Reading
# them under any scope but the one they were built for is the error the
# amendment exists to stop, so the collapse must reproduce every one of them.
RECOMPUTED_COLUMNS = (
    'sources',
    'source_count',
    'is_directed',
    'is_stimulation',
    'is_inhibition',
    'sign_source_count',
    'direction_source_count',
    'reference_pubmed_ids',
    'reference_dois',
    'reference_count',
)

# Which of those are arrays, and so compare order-independently.
ARRAY_COLUMNS = frozenset(
    {'sources', 'reference_pubmed_ids', 'reference_dois'}
)

# The collapse of the record over every resource, written once. The derive
# materialises this scope into `interaction_fact_combined`; a query restricting
# the resource set runs the same shape over a smaller input (T013e). The two
# must agree, which is what the `test_the_collapse_reproduces_*` pair checks.
COLLAPSE_SQL = f"""
WITH refs AS (
  SELECT r.subject_entity_id,
         r.object_entity_id,
         r.interaction_class_id,
         array_agg(DISTINCT pm.pubmed) FILTER (WHERE pm.pubmed IS NOT NULL)
           AS reference_pubmed_ids,
         array_agg(DISTINCT dd.doi) FILTER (WHERE dd.doi IS NOT NULL)
           AS reference_dois
  FROM {SCRATCH}.interaction_fact_resource r
  LEFT JOIN LATERAL unnest(r.reference_pubmed_ids) AS pm(pubmed) ON true
  LEFT JOIN LATERAL unnest(r.reference_dois) AS dd(doi) ON true
  GROUP BY 1, 2, 3
)
SELECT r.subject_entity_id,
       r.object_entity_id,
       r.interaction_class_id,
       array_agg(DISTINCT ds.name) AS sources,
       count(DISTINCT r.source_id)::int AS source_count,
       bool_or(r.is_directed) AS is_directed,
       bool_or(r.is_stimulation) AS is_stimulation,
       bool_or(r.is_inhibition) AS is_inhibition,
       count(DISTINCT r.source_id) FILTER (
         WHERE r.is_stimulation IS NOT NULL OR r.is_inhibition IS NOT NULL
       )::int AS sign_source_count,
       count(DISTINCT r.source_id) FILTER (
         WHERE r.is_directed IS NOT NULL
       )::int AS direction_source_count,
       refs.reference_pubmed_ids,
       refs.reference_dois,
       (coalesce(cardinality(refs.reference_pubmed_ids), 0)
        + coalesce(cardinality(refs.reference_dois), 0))::int
         AS reference_count
FROM {SCRATCH}.interaction_fact_resource r
JOIN {SCRATCH}.data_source ds ON ds.source_id = r.source_id
LEFT JOIN refs
  ON refs.subject_entity_id = r.subject_entity_id
 AND refs.object_entity_id = r.object_entity_id
 AND refs.interaction_class_id = r.interaction_class_id
GROUP BY r.subject_entity_id, r.object_entity_id, r.interaction_class_id,
         refs.reference_pubmed_ids, refs.reference_dois
"""


@pytest.fixture(scope='module')
def built():
    """The fixture graph, projected by the derive step, in a scratch schema."""
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


def _query(conn, statement: str, params=None) -> list[tuple]:
    """Run a read query, keeping a failure from poisoning the next test.

    The fixture is module-scoped and hands every test the same connection, so
    one statement erroring — over a table this cycle has not built yet, say —
    would leave the transaction aborted and make every later test fail for a
    reason that is not its own. Roll back and re-raise instead.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall()
    except psycopg2.Error:
        conn.rollback()
        raise


def _fact(conn, subject: str, object_: str) -> dict[str, object] | None:
    """The single collapsed row for a fixture endpoint pair, by name."""
    rows = _query(
        conn,
        f"""
        SELECT vic.name, f.sources, f.source_count, f.reference_pubmed_ids,
               f.reference_count, f.interaction_id, f.is_stimulation,
               f.is_inhibition, f.sign_source_count
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
    assert len(rows) <= 1, f'{subject}->{object_} produced {len(rows)} rows'
    if not rows:
        return None
    (
        name,
        sources,
        source_count,
        pubmed,
        reference_count,
        interaction_id,
        is_stimulation,
        is_inhibition,
        sign_source_count,
    ) = rows[0]
    return {
        'class': name,
        'sources': sorted(sources or []),
        'source_count': source_count,
        'pubmed': sorted(pubmed or []),
        'reference_count': reference_count,
        'interaction_id': interaction_id,
        'is_stimulation': is_stimulation,
        'is_inhibition': is_inhibition,
        'sign_source_count': sign_source_count,
    }


def _records(conn, subject: str, object_: str) -> list[dict[str, object]]:
    """Every record row for a fixture endpoint pair.

    One per contributing resource **and** assertion signature (data-model §3a).
    """
    rows = _query(
        conn,
        f"""
        SELECT ds.name, vic.name, r.is_directed, r.is_stimulation,
               r.is_inhibition, r.reference_pubmed_ids,
               r.interaction_fact_resource_id, r.interaction_id
        FROM {SCRATCH}.interaction_fact_resource r
        JOIN {SCRATCH}.entity subject
          ON subject.entity_id = r.subject_entity_id
        JOIN {SCRATCH}.entity object
          ON object.entity_id = r.object_entity_id
        JOIN {SCRATCH}.data_source ds ON ds.source_id = r.source_id
        LEFT JOIN {SCRATCH}.vocab_interaction_class vic
          ON vic.interaction_class_id = r.interaction_class_id
        WHERE subject.canonical_identifier = %s
          AND object.canonical_identifier = %s
        ORDER BY ds.name, r.is_stimulation, r.is_inhibition
        """,
        (f'FIXTURE_{subject}', f'FIXTURE_{object_}'),
    )
    return [
        {
            'source': source,
            'class': class_name,
            'is_directed': is_directed,
            'is_stimulation': is_stimulation,
            'is_inhibition': is_inhibition,
            'pubmed': sorted(pubmed or []),
            'record_id': record_id,
            'interaction_id': interaction_id,
        }
        for (
            source,
            class_name,
            is_directed,
            is_stimulation,
            is_inhibition,
            pubmed,
            record_id,
            interaction_id,
        ) in rows
    ]


# ---------------------------------------------------------------------------
# The record grain — data-model §3a
# ---------------------------------------------------------------------------


def test_the_record_key_is_unique(built):
    """One row per endpoint pair, class, resource **and** assertion signature.

    Postgres `GROUP BY` treats NULLs as equal, which is the comparison this key
    needs: the signature columns are nullable, and two silent rows from one
    resource are a duplicate rather than two distinct assertions.
    """
    conn, _stats = built
    rows = _query(
        conn,
        f"""
        SELECT count(*) FROM (
          SELECT 1 FROM {SCRATCH}.interaction_fact_resource
          GROUP BY subject_entity_id, object_entity_id, interaction_class_id,
                   source_id, is_directed, is_stimulation, is_inhibition
          HAVING count(*) > 1
        ) duplicated
        """,
    )
    assert rows[0][0] == 0


def test_the_record_keeps_one_row_per_contributing_resource(built):
    """Three resources on one endpoint pair are three record rows, not one.

    Each carries what *that* resource asserts, so the silent third contributor
    keeps NULL sign columns instead of inheriting a neighbour's assertion.
    """
    conn, _stats = built
    records = _records(conn, 'c', 'd')
    assert [record['source'] for record in records] == [
        'fixture_res_a',
        'fixture_res_b',
        'fixture_res_c',
    ], 'the signalling trio did not keep one record row per resource'
    assert {record['class'] for record in records} == {'signaling'}
    by_source = {record['source']: record for record in records}
    assert by_source['fixture_res_a']['is_stimulation'] is True
    assert by_source['fixture_res_a']['is_inhibition'] is None
    assert by_source['fixture_res_b']['is_inhibition'] is True
    assert by_source['fixture_res_b']['is_stimulation'] is None
    assert by_source['fixture_res_c']['is_stimulation'] is None
    assert by_source['fixture_res_c']['is_inhibition'] is None


def test_contradicting_signs_from_one_resource_keep_two_rows(built):
    """The assertion signature is part of the key, so one resource stating both
    signs under two predicates keeps **two** record rows (data-model §3a)."""
    conn, _stats = built
    records = _records(conn, 'i', 'j')
    assert len(records) == 2, (
        'one resource asserting both signs must keep two record rows; got '
        f'{len(records)}'
    )
    assert {record['source'] for record in records} == {'fixture_res_a'}
    signatures = {
        (record['is_stimulation'], record['is_inhibition'])
        for record in records
    }
    assert signatures == {(True, None), (None, True)}
    assert len({record['record_id'] for record in records}) == 2, (
        'the two rows must carry distinct surrogate keys'
    )


def test_the_contradiction_collapses_to_one_row_with_both_flags(built):
    """Collapsed over the whole resource set the same pair is one row, with
    both sign flags true and a `sign_source_count` of one (FR-044, R15)."""
    conn, _stats = built
    fact = _fact(conn, 'i', 'j')
    assert fact is not None, 'the single-resource conflict produced no row'
    assert fact['is_stimulation'] is True
    assert fact['is_inhibition'] is True
    assert fact['source_count'] == 1
    assert fact['sign_source_count'] == 1


def test_the_record_keeps_each_resources_references_to_itself(built):
    """A record row carries **this** resource's references, not the pool.

    Pooling them at record grain is the defect the amendment names: the value
    would then describe resources a scoped query excluded.
    """
    conn, _stats = built
    by_source = {
        record['source']: record for record in _records(conn, 'c', 'd')
    }
    assert by_source['fixture_res_a']['pubmed'] == ['11111111']
    assert by_source['fixture_res_b']['pubmed'] == []
    assert by_source['fixture_res_c']['pubmed'] == ['33333333']


def test_the_record_has_a_unique_surrogate_key(built):
    """`interaction_fact_resource_id` is present on every row and unique.

    The composite key includes nullable signature columns and Postgres defaults
    to `MATCH SIMPLE`, under which a foreign key with any NULL column is not
    checked at all — so the detail tables can only anchor on the surrogate.
    """
    conn, _stats = built
    rows = _query(
        conn,
        f"""
        SELECT count(*),
               count(interaction_fact_resource_id),
               count(DISTINCT interaction_fact_resource_id)
        FROM {SCRATCH}.interaction_fact_resource
        """,
    )
    total, present, distinct = rows[0]
    assert total > 0, 'the record table is empty'
    assert present == total, 'some record rows carry no surrogate key'
    assert distinct == total, 'the surrogate key is not unique'


def test_the_surrogate_key_is_deterministic_across_rebuilds(built):
    """A re-run reproduces the same ids, so a child row's key survives a build.

    The id is a `content_uuid` over the full key rather than a serial, which is
    what makes it stable; a rebuild that reshuffled it would turn every detail
    table into a migration.
    """
    conn, _stats = built
    from omnipath_build.db.derived_tables import rebuild_interaction_tables

    def keyed_ids() -> dict[tuple, str]:
        rows = _query(
            conn,
            f"""
            SELECT subject_entity_id, object_entity_id, interaction_class_id,
                   source_id, is_directed, is_stimulation, is_inhibition,
                   interaction_fact_resource_id
            FROM {SCRATCH}.interaction_fact_resource
            """,
        )
        return {row[:-1]: row[-1] for row in rows}

    before = keyed_ids()
    rebuild_interaction_tables(conn, schema=SCRATCH)
    assert keyed_ids() == before, (
        'the record surrogate key changed on a rebuild of the same content'
    )


def test_opposite_directions_stay_two_record_rows(built):
    """The record key is ordered too, so A→B and B→A never merge (FR-044d)."""
    conn, _stats = built
    forward = _records(conn, 'g', 'h')
    reverse = _records(conn, 'h', 'g')
    assert len(forward) == 1 and len(reverse) == 1
    assert forward[0]['record_id'] != reverse[0]['record_id']


def test_every_record_row_links_to_its_header(built):
    """The record carries the header FK; the materialisation inherits it."""
    conn, _stats = built
    rows = _query(
        conn,
        f'SELECT count(*) FROM {SCRATCH}.interaction_fact_resource '
        f'WHERE interaction_id IS NULL',
    )
    assert rows[0][0] == 0
    rows = _query(
        conn,
        f"""
        SELECT count(*)
        FROM {SCRATCH}.interaction_fact_resource r
        LEFT JOIN {SCRATCH}.interaction i
          ON i.interaction_id = r.interaction_id
        WHERE i.interaction_id IS NULL
        """,
    )
    assert rows[0][0] == 0


def test_no_record_row_is_left_without_a_class(built):
    """Every record row reaches at least `other`, so the key never goes NULL."""
    conn, _stats = built
    rows = _query(
        conn,
        f'SELECT count(*) FROM {SCRATCH}.interaction_fact_resource '
        f'WHERE interaction_class_id IS NULL',
    )
    assert rows[0][0] == 0


# ---------------------------------------------------------------------------
# The all-resources collapse — data-model §3b
# ---------------------------------------------------------------------------


def test_the_collapse_reproduces_the_materialisation_row_count(built):
    """Collapsing the record over every resource yields exactly the rows the
    materialisation holds — no key in one and missing from the other."""
    conn, _stats = built
    rows = _query(
        conn,
        f"""
        WITH collapsed AS ({COLLAPSE_SQL})
        SELECT
          (SELECT count(*) FROM collapsed),
          (SELECT count(*) FROM {SCRATCH}.interaction_fact_combined),
          (SELECT count(*) FROM (
             SELECT subject_entity_id, object_entity_id, interaction_class_id
             FROM collapsed
             EXCEPT
             SELECT subject_entity_id, object_entity_id, interaction_class_id
             FROM {SCRATCH}.interaction_fact_combined
           ) missing),
          (SELECT count(*) FROM (
             SELECT subject_entity_id, object_entity_id, interaction_class_id
             FROM {SCRATCH}.interaction_fact_combined
             EXCEPT
             SELECT subject_entity_id, object_entity_id, interaction_class_id
             FROM collapsed
           ) extra)
        """,
    )
    collapsed, materialised, missing, extra = rows[0]
    assert collapsed > 0, 'the collapse of the record produced no rows'
    assert missing == 0, (
        f'{missing} collapsed keys are absent from interaction_fact_combined'
    )
    assert extra == 0, (
        f'{extra} materialised keys are not produced by the collapse'
    )
    assert collapsed == materialised


def _mismatch_expression(column: str) -> str:
    """Count the rows where the collapse and the materialisation disagree."""
    if column in ARRAY_COLUMNS:
        return f"""
        count(*) FILTER (
          WHERE (SELECT array_agg(v ORDER BY v)
                 FROM unnest(coalesce(c.{column}, '{{}}'::text[])) v)
            IS DISTINCT FROM
                (SELECT array_agg(v ORDER BY v)
                 FROM unnest(coalesce(f.{column}, '{{}}'::text[])) v)
        )
        """
    return f'count(*) FILTER (WHERE c.{column} IS DISTINCT FROM f.{column})'


def test_the_collapse_reproduces_the_materialisation_values(built):
    """Every recomputed column matches, not merely the shape.

    A materialisation agreeing on the keys and disagreeing on the numbers is
    the failure the scope rule exists to prevent: the right interactions
    carrying figures that describe a different resource set.
    """
    conn, _stats = built
    comparisons = ',\n'.join(
        _mismatch_expression(column) for column in RECOMPUTED_COLUMNS
    )
    rows = _query(
        conn,
        f"""
        WITH collapsed AS ({COLLAPSE_SQL})
        SELECT {comparisons}
        FROM collapsed c
        JOIN {SCRATCH}.interaction_fact_combined f
          ON f.subject_entity_id = c.subject_entity_id
         AND f.object_entity_id = c.object_entity_id
         AND f.interaction_class_id = c.interaction_class_id
        """,
    )
    mismatches = {
        column: count
        for column, count in zip(RECOMPUTED_COLUMNS, rows[0])
        if count
    }
    assert not mismatches, (
        'the collapse of the record disagrees with the materialised '
        f'all-resources scope on: {mismatches}'
    )


def test_the_carried_columns_come_through_the_collapse_unchanged(built):
    """`subject_organism`, `object_organism` and `interaction_id` are carried
    rather than recomputed, so a record row must state each (data-model §3b)."""
    conn, _stats = built
    rows = _query(
        conn,
        f"""
        SELECT count(*)
        FROM {SCRATCH}.interaction_fact_combined f
        WHERE NOT EXISTS (
          SELECT 1 FROM {SCRATCH}.interaction_fact_resource r
          WHERE r.subject_entity_id = f.subject_entity_id
            AND r.object_entity_id = f.object_entity_id
            AND r.interaction_class_id = f.interaction_class_id
            AND r.subject_organism IS NOT DISTINCT FROM f.subject_organism
            AND r.object_organism IS NOT DISTINCT FROM f.object_organism
            AND r.interaction_id IS NOT DISTINCT FROM f.interaction_id
        )
        """,
    )
    assert rows[0][0] == 0, (
        'a collapsed row carries a carried-through value no record row states'
    )


def test_the_collapse_invents_no_summary_value(built):
    """Values the collapse summarises without a fixed rule — affinity, pchembl,
    score, curation flags — must still come from a contributing record row."""
    conn, _stats = built
    for column in ('affinity', 'pchembl', 'score'):
        rows = _query(
            conn,
            f"""
            SELECT count(*)
            FROM {SCRATCH}.interaction_fact_combined f
            WHERE f.{column} IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM {SCRATCH}.interaction_fact_resource r
                WHERE r.subject_entity_id = f.subject_entity_id
                  AND r.object_entity_id = f.object_entity_id
                  AND r.interaction_class_id = f.interaction_class_id
                  AND r.{column} = f.{column}
              )
            """,
        )
        assert rows[0][0] == 0, f'{column} on a collapsed row has no record row'
    rows = _query(
        conn,
        f"""
        SELECT count(*)
        FROM {SCRATCH}.interaction_fact_combined f
        CROSS JOIN LATERAL unnest(coalesce(f.curation_flags, '{{}}'::text[]))
          AS c(flag)
        WHERE NOT EXISTS (
          SELECT 1 FROM {SCRATCH}.interaction_fact_resource r
          WHERE r.subject_entity_id = f.subject_entity_id
            AND r.object_entity_id = f.object_entity_id
            AND r.interaction_class_id = f.interaction_class_id
            AND c.flag = ANY (r.curation_flags)
        )
        """,
    )
    assert rows[0][0] == 0, 'a curation flag on a collapsed row has no record'


def test_the_ordered_key_is_unique(built):
    """The collapsed output holds one row per ordered endpoint pair and class."""
    conn, _stats = built
    rows = _query(
        conn,
        f"""
        SELECT count(*) FROM (
          SELECT 1 FROM {SCRATCH}.interaction_fact_combined
          GROUP BY subject_entity_id, object_entity_id,
                   interaction_class_id
          HAVING count(*) > 1
        ) duplicated
        """,
    )
    assert rows[0][0] == 0


def test_three_resources_fold_into_one_row(built):
    """One collapsed row per endpoint pair and class, with all provenance."""
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
    """Every contributing resource's references reach the collapsed row."""
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
    rows = _query(
        conn,
        f'SELECT count(*) FROM {SCRATCH}.interaction_fact_combined '
        f'WHERE interaction_id IS NULL',
    )
    assert rows[0][0] == 0
    rows = _query(
        conn,
        f"""
        SELECT count(*)
        FROM {SCRATCH}.interaction_fact_combined f
        LEFT JOIN {SCRATCH}.interaction i
          ON i.interaction_id = f.interaction_id
        WHERE i.interaction_id IS NULL
        """,
    )
    assert rows[0][0] == 0


def test_every_header_has_its_participants(built):
    """A binary interaction records both parties, with its arity (T014, §2)."""
    conn, _stats = built
    rows = _query(
        conn,
        f"""
        SELECT count(*)
        FROM {SCRATCH}.interaction i
        LEFT JOIN (
          SELECT interaction_id, count(*) AS parties
          FROM {SCRATCH}.interaction_party
          GROUP BY interaction_id
        ) p ON p.interaction_id = i.interaction_id
        WHERE p.parties IS DISTINCT FROM i.arity
        """,
    )
    assert rows[0][0] == 0


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
    rows = _query(
        conn,
        f'SELECT count(*) FROM {SCRATCH}.interaction_fact_combined '
        f'WHERE interaction_class_id IS NULL',
    )
    assert rows[0][0] == 0


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
