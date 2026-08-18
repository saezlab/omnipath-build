"""The manifest records interaction derive cost and the preset inventory (T020a).

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
import os

import pytest

from omnipath_build.db.resources import emit_build_manifest
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
    'interaction_fact': {'seconds': 12.5, 'rows': 1000},
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
    assert by_step['interaction_fact']['seconds'] == pytest.approx(12.5)
    assert by_step['interaction_fact']['rows'] == 1000

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
