"""The manifest records interaction derive cost and the preset inventory (T020a).

The grain amendment splits that cost in two (T020b): the record table
``interaction_fact_resource`` and the collapse ``interaction_fact_combined`` are
reported apart, and every measured query scope reports what materialising it
cost — the scopes this build declined to materialise included (FR-050).

Both are volatile — per-step seconds and row counts, and the registered preset
list — so they are written next to the identity hash, never inside it. These
tests assert the manifest carries them and that two builds over identical inputs
still agree on ``build_id`` when their timings differ (the cycle-007 lesson).

Run against a build database, e.g. on dev4::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_manifest_interactions.py -v

The manifests are written into a throwaway schema, so the build's own
``public.build_manifest`` row is never rewritten. Skipped without DATABASE_URL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

import pytest

from omnipath_build.db.resources import (
    ALL_RESOURCES_SCOPE,
    INTERACTION_COLLAPSE_STEP,
    INTERACTION_RECORD_STEP,
    emit_build_manifest,
)
from omnipath_build.network_views import (
    NetworkDefinition,
    ensure_network_registry,
    register_network,
)

DATABASE_URL = os.environ.get('DATABASE_URL')
TEST_SCHEMA = 'manifest_interactions_test'

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; manifest test needs a database',
)

PRESET = NetworkDefinition(
    name='_manifest_preset',
    kind='ligand_receptor',
    included_sources=('connectomedb2025',),
    interaction_class_scope=('ligand_receptor',),
    default_attributes=('endpoints',),
    mandatory_attributes=('label', 'references', 'evidence'),
)

# Two runs of the same build: identical inputs, different timings and row counts
# observed along the way.
COST_FIRST = {
    'interaction_fact_combined': {'seconds': 12.5, 'rows': 1000},
    'interaction_assay': {'seconds': 3.25, 'rows': 400},
    'interaction_party': {'seconds': 5.0, 'rows': 2000},
    'reaction_projection': {'seconds': 8.75, 'rows': 700},
    'intercell': {'seconds': 1.5, 'rows': 90},
}
COST_SECOND = {
    step: {'seconds': cost['seconds'] * 2 + 0.5, 'rows': cost['rows']}
    for step, cost in COST_FIRST.items()
}


@pytest.fixture(scope='module')
def conn():
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope='module')
def build_schema(conn):
    """A throwaway schema holding the inputs a manifest is derived from."""
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
        cur.execute(f'CREATE SCHEMA {TEST_SCHEMA}')
        cur.execute(
            f"""
            CREATE TABLE {TEST_SCHEMA}.resources (
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
            f"""
            INSERT INTO {TEST_SCHEMA}.resources
              (resource_id, entity_count, interaction_count, input_module_commit)
            VALUES ('signor', 10, 20, 'abc123'), ('connectomedb2025', 5, 7, 'def456')
            """
        )
    conn.commit()
    ensure_network_registry(conn, registry_schema=TEST_SCHEMA)
    register_network(conn, PRESET, registry_schema=TEST_SCHEMA)
    yield TEST_SCHEMA
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
    conn.commit()


def _manifest(conn, schema):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT build_id, package_commits, resources,
                   interactions_derive_cost, network_presets
            FROM {schema}.build_manifest
            """
        )
        return cur.fetchone()


def test_manifest_records_derive_cost_and_preset_inventory(conn, build_schema):
    emit_build_manifest(conn, schema=build_schema, derive_cost=COST_FIRST)
    _build_id, _commits, _resources, cost, presets = _manifest(conn, build_schema)

    assert isinstance(cost, dict), 'no interactions_derive_cost in the manifest'
    by_step = {entry['step']: entry for entry in cost['steps']}
    assert set(by_step) == set(COST_FIRST)
    assert by_step['interaction_fact_combined']['seconds'] == pytest.approx(12.5)
    assert by_step['interaction_fact_combined']['rows'] == 1000

    assert isinstance(presets, list) and presets, 'no network_presets inventory'
    registered = {entry['name']: entry for entry in presets}
    assert PRESET.name in registered
    entry = registered[PRESET.name]
    assert entry['kind'] == PRESET.kind
    assert entry['included_sources'] == list(PRESET.included_sources)
    assert entry['interaction_class_scope'] == list(PRESET.interaction_class_scope)
    assert entry['mandatory_attributes'] == list(PRESET.mandatory_attributes)


def test_build_id_is_stable_across_differing_timings(conn, build_schema):
    """Identical inputs, different timings — the same build identity."""
    first = emit_build_manifest(conn, schema=build_schema, derive_cost=COST_FIRST)
    _id, _c, _r, first_cost, _p = _manifest(conn, build_schema)
    second = emit_build_manifest(conn, schema=build_schema, derive_cost=COST_SECOND)
    _id2, _c2, _r2, second_cost, _p2 = _manifest(conn, build_schema)

    assert first_cost != second_cost, 'the two runs recorded the same timings'
    assert first.build_id == second.build_id


def test_derive_cost_and_presets_stay_out_of_the_hash_payload(conn, build_schema):
    """The identity hash covers the package commits and resources — nothing else."""
    emit_build_manifest(conn, schema=build_schema, derive_cost=COST_SECOND)
    build_id, package_commits, resources, cost, presets = _manifest(
        conn, build_schema
    )
    payload = {'package_commits': package_commits, 'resources': resources}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()[:12]
    assert build_id == expected
    # And the volatile fields really were present while that hash was computed.
    assert cost and presets


def test_manifest_omits_cost_when_no_derive_step_ran(conn, build_schema):
    """A build with no interaction derive steps records no cost, and still hashes."""
    stats = emit_build_manifest(conn, schema=build_schema)
    build_id, _commits, _resources, cost, presets = _manifest(conn, build_schema)
    assert cost is None
    assert build_id == stats.build_id
    assert presets is not None


# --- the grain amendment's cost, named apart (T020b, FR-050) ----------------
#
# The record and the collapse are two tables now, and the manifest has to say
# which of them cost what. A scope that was measured without being materialised
# is the case FR-050 exists for, so it gets a run of its own below.

COST_BOTH_TABLES = {
    'interaction_party': {'seconds': 679.1, 'rows': 28359137},
    INTERACTION_RECORD_STEP: {'seconds': 394.0, 'rows': 14678638},
    INTERACTION_COLLAPSE_STEP: {'seconds': 103.25, 'rows': 14292093},
    'intercell': {'seconds': 1.5, 'rows': 90},
}

# A build from before the amendment: one interaction fact table, no record step.
COST_PRE_AMENDMENT = {
    'interaction_party': {'seconds': 5.0, 'rows': 2000},
    'intercell': {'seconds': 1.5, 'rows': 90},
}

SCOPE_COST = {
    'kinase_extra': {'seconds': 4.0, 'rows': 120},
    ALL_RESOURCES_SCOPE: {
        'table': 'interaction_fact_combined',
        'seconds': 103.25,
        'rows': 14292093,
        'sources': ('signor', 'connectomedb2025'),
    },
    'academic_license': {
        'table': 'interaction_fact_academic',
        'seconds': 51.5,
        'rows': 900,
        'sources': ('signor',),
    },
    'signor_only': {
        'table': 'interaction_fact_signor',
        'seconds': 7.5,
        'rows': 20,
        'sources': ('signor',),
    },
}


def test_interaction_tables_split_record_from_collapse(conn, build_schema):
    """The record and the collapse are reported apart, each with its own cost."""
    emit_build_manifest(conn, schema=build_schema, derive_cost=COST_BOTH_TABLES)
    _id, _c, _r, cost, _p = _manifest(conn, build_schema)

    tables = cost['interaction_tables']
    assert tables['record'] == {
        'table': INTERACTION_RECORD_STEP,
        'seconds': pytest.approx(394.0),
        'rows': 14678638,
    }
    assert tables['collapse'] == {
        'table': INTERACTION_COLLAPSE_STEP,
        'seconds': pytest.approx(103.25),
        'rows': 14292093,
    }
    assert tables['total_seconds'] == pytest.approx(394.0 + 103.25)

    # The split is an extra reading of the same steps, not a replacement: both
    # tables stay in `steps` too, so the whole-derive total is still readable.
    by_step = {entry['step']: entry for entry in cost['steps']}
    assert INTERACTION_RECORD_STEP in by_step
    assert INTERACTION_COLLAPSE_STEP in by_step


def test_unmeasured_table_half_is_null_and_not_zero(conn, build_schema):
    """Absent and free stay distinguishable: nothing reported renders ``null``.

    This is what makes the amendment's cost attributable. A half rendered as
    zero reads as "the collapse was free", which is a claim about the build; a
    half rendered as ``null`` reads as "nobody measured it", which is a claim
    about the measurement. The two must not be confused, so ``total_seconds``
    adds up only the halves that carry a number.
    """
    derive_cost = {
        INTERACTION_RECORD_STEP: {'seconds': 394.0, 'rows': 14678638},
        # The collapse ran but was never timed.
        INTERACTION_COLLAPSE_STEP: {'seconds': None, 'rows': None},
    }
    emit_build_manifest(conn, schema=build_schema, derive_cost=derive_cost)
    _id, _c, _r, cost, _p = _manifest(conn, build_schema)

    collapse = cost['interaction_tables']['collapse']
    assert collapse['table'] == INTERACTION_COLLAPSE_STEP
    assert collapse['seconds'] is None, 'an unmeasured half was rendered as a number'
    assert collapse['rows'] is None, 'an uncounted half was rendered as a number'
    # Only the measured half is summed — not 394.0 + 0.
    assert cost['interaction_tables']['total_seconds'] == pytest.approx(394.0)

    # And a step that did not run at all is absent, not zeroed.
    emit_build_manifest(
        conn,
        schema=build_schema,
        derive_cost={INTERACTION_COLLAPSE_STEP: {'seconds': 103.25, 'rows': 7}},
    )
    _id2, _c2, _r2, cost2, _p2 = _manifest(conn, build_schema)
    assert cost2['interaction_tables']['record'] is None
    assert cost2['interaction_tables']['total_seconds'] == pytest.approx(103.25)

    # Nothing measured on either half: the block is there (a table reported),
    # but it claims no total.
    emit_build_manifest(
        conn,
        schema=build_schema,
        derive_cost={INTERACTION_RECORD_STEP: {'rows': 14678638}},
    )
    _id3, _c3, _r3, cost3, _p3 = _manifest(conn, build_schema)
    assert cost3['interaction_tables']['total_seconds'] is None


def test_interaction_tables_absent_for_a_pre_amendment_build(conn, build_schema):
    """Neither table reported — the block is omitted, not emitted empty.

    A build that predates the amendment writes exactly the manifest it wrote
    before: `steps` and nothing beside it.
    """
    emit_build_manifest(conn, schema=build_schema, derive_cost=COST_PRE_AMENDMENT)
    _id, _c, _r, cost, _p = _manifest(conn, build_schema)

    assert 'interaction_tables' not in cost
    assert set(cost) == {'steps'}


def test_scope_cost_orders_all_resources_first(conn, build_schema):
    """All-resources first, then the materialised scopes, then by name."""
    scope_cost = dict(SCOPE_COST)
    # The all-resources scope leads even when it is the one not materialised.
    scope_cost[ALL_RESOURCES_SCOPE] = {
        **scope_cost[ALL_RESOURCES_SCOPE],
        'table': None,
        'materialised': False,
    }
    emit_build_manifest(
        conn,
        schema=build_schema,
        derive_cost=COST_BOTH_TABLES,
        scope_cost=scope_cost,
    )
    _id, _c, _r, cost, _p = _manifest(conn, build_schema)

    assert [entry['scope'] for entry in cost['scopes']] == [
        ALL_RESOURCES_SCOPE,
        'academic_license',
        'signor_only',
        'kinase_extra',
    ]


def test_scope_measured_without_being_materialised_round_trips(conn, build_schema):
    """FR-050: the scope this build declined to store still reports its cost.

    Declining to materialise a scope has to stay an available answer to a cost
    overrun, and that argument is made from the number for the scope that was
    declined — so ``table: null`` with ``materialised: false`` must survive the
    round trip through the manifest rather than being dropped as "not a table".
    """
    emit_build_manifest(
        conn,
        schema=build_schema,
        derive_cost=COST_BOTH_TABLES,
        scope_cost=SCOPE_COST,
    )
    _id, _c, _r, cost, _p = _manifest(conn, build_schema)
    by_scope = {entry['scope']: entry for entry in cost['scopes']}

    declined = by_scope['kinase_extra']
    assert declined['table'] is None
    assert declined['materialised'] is False
    assert declined['seconds'] == pytest.approx(4.0)
    assert declined['rows'] == 120
    assert declined['sources'] is None

    # A scope that named a table is materialised without having to say so.
    stored = by_scope[ALL_RESOURCES_SCOPE]
    assert stored['table'] == 'interaction_fact_combined'
    assert stored['materialised'] is True
    assert stored['sources'] == ['signor', 'connectomedb2025']


def test_table_and_scope_cost_do_not_move_the_build_id(conn, build_schema):
    """Two builds differing only in these costs share one identity.

    The amendment added fields to the manifest; the cycle-007 lesson says a
    field describing a run may never move the identity of what the run built.
    Asserted here so a later edit cannot fold them into the hashed payload
    unnoticed.
    """
    first = emit_build_manifest(
        conn,
        schema=build_schema,
        derive_cost=COST_BOTH_TABLES,
        scope_cost=SCOPE_COST,
    )
    _id, commits, resources, first_cost, _p = _manifest(conn, build_schema)

    slower_tables = {
        step: {'seconds': cost['seconds'] * 3 + 1.0, 'rows': cost['rows']}
        for step, cost in COST_BOTH_TABLES.items()
    }
    fewer_scopes = {
        ALL_RESOURCES_SCOPE: {
            'table': None,
            'materialised': False,
            'seconds': 9999.0,
            'rows': 1,
        },
    }
    second = emit_build_manifest(
        conn,
        schema=build_schema,
        derive_cost=slower_tables,
        scope_cost=fewer_scopes,
    )
    _id2, _c2, _r2, second_cost, _p2 = _manifest(conn, build_schema)

    assert first_cost['interaction_tables'] != second_cost['interaction_tables']
    assert first_cost['scopes'] != second_cost['scopes']
    assert first.build_id == second.build_id

    # And the identity is still the hash of the two names it always covered.
    payload = {'package_commits': commits, 'resources': resources}
    assert first.build_id == hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()[:12]


def test_scope_record_without_a_name_is_warned_about_and_dropped(
    conn,
    build_schema,
    caplog,
):
    """A malformed scope record costs a warning, never the build."""
    scope_cost = [
        {'seconds': 4.0, 'rows': 120},  # no `scope`: unattributable
        {
            'scope': 'signor_only',
            'table': 'interaction_fact_signor',
            'seconds': 7.5,
            'rows': 20,
        },
    ]
    with caplog.at_level(logging.WARNING, logger='omnipath_build.db.resources'):
        stats = emit_build_manifest(
            conn,
            schema=build_schema,
            derive_cost=COST_BOTH_TABLES,
            scope_cost=scope_cost,
        )
    _id, _c, _r, cost, _p = _manifest(conn, build_schema)

    assert [entry['scope'] for entry in cost['scopes']] == ['signor_only']
    assert stats.build_id
    assert any(
        'scope-cost record without a scope' in record.getMessage()
        for record in caplog.records
    )
