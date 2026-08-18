"""The preset registry: ``network_registry`` carries a full preset spec (T018a).

A dataset is a preset — a ``network_registry`` row that filters the interaction
fact table — not a bespoke matview. These tests assert the registry round-trips
every field of that spec (class scope, evidence scope, default and mandatory
attributes, labels, curation, attribute sources) and that the live table carries
the columns to hold them.

Run against a build database, e.g. on dev4::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_preset_registry.py -v

The round-trip runs in a throwaway schema, so it never touches the presets the
build registered in ``public``. Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import pytest

from omnipath_build.network_views import (
    NetworkDefinition,
    ensure_network_registry,
    register_network,
)

DATABASE_URL = os.environ.get('DATABASE_URL')
SCHEMA = os.environ.get('OMNIPATH_PG_SCHEMA', 'public')
TEST_SCHEMA = 'preset_registry_roundtrip_test'

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; preset-registry test needs a database',
)

# A preset spec with every field populated — the shape data-model §9 asks the
# registry to carry. `interaction_class_scope` holds class slugs (§8), never
# legacy dataset names; the class derivation itself (R18) is another task.
PRESET = NetworkDefinition(
    name='_roundtrip_preset',
    kind='ligand_receptor',
    included_sources=('connectomedb2025', 'cellphonedb'),
    interaction_class_scope=('ligand_receptor', 'transport'),
    evidence_scope={
        'evidence_type': ['experimental', 'curated'],
        'predicate': ['binds'],
        'min_confidence': 2,
    },
    default_attributes=('endpoints', 'references'),
    mandatory_attributes=('label', 'evidence'),
    labels={
        'preset': 'Round-trip preset',
        'columns': {'label': 'Interaction label'},
    },
    curation={
        'moa_only': True,
        'affinity_cutoff': 6.0,
        'metabolite_class_gate': True,
    },
    attribute_sources={
        'protein_localization': {'stage': 'interim', 'source': 'uniprot'},
    },
)

PRESET_COLUMNS = {
    'interaction_class_scope': '_text',
    'evidence_scope': 'jsonb',
    'default_attributes': '_text',
    'mandatory_attributes': '_text',
    'labels': 'jsonb',
    'curation': 'jsonb',
    'attribute_sources': 'jsonb',
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
def registry(conn):
    """A throwaway registry schema, so the build's own presets stay untouched."""
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
        cur.execute(f'CREATE SCHEMA {TEST_SCHEMA}')
    conn.commit()
    ensure_network_registry(conn, registry_schema=TEST_SCHEMA)
    yield TEST_SCHEMA
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE')
    conn.commit()


def _preset_row(conn, schema, name):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT kind, included_sources, interaction_class_scope,
                   evidence_scope, default_attributes, mandatory_attributes,
                   labels, curation, attribute_sources
            FROM {schema}.network_registry WHERE name = %s
            """,
            [name],
        )
        return cur.fetchone()


def test_registry_round_trips_the_full_preset_spec(conn, registry):
    """Every field of the preset spec survives registration unchanged."""
    register_network(conn, PRESET, registry_schema=registry)
    row = _preset_row(conn, registry, PRESET.name)
    assert row is not None, 'the preset was not registered'
    (
        kind,
        included_sources,
        class_scope,
        evidence_scope,
        default_attributes,
        mandatory_attributes,
        labels,
        curation,
        attribute_sources,
    ) = row
    assert kind == PRESET.kind
    assert tuple(included_sources) == PRESET.included_sources
    assert tuple(class_scope) == PRESET.interaction_class_scope
    assert evidence_scope == PRESET.evidence_scope
    assert tuple(default_attributes) == PRESET.default_attributes
    assert tuple(mandatory_attributes) == PRESET.mandatory_attributes
    assert labels == PRESET.labels
    assert curation == PRESET.curation
    assert attribute_sources == PRESET.attribute_sources


def test_re_registration_upserts_the_preset(conn, registry):
    """``register_network`` keeps its upsert: one row per preset, spec replaced."""
    register_network(conn, PRESET, registry_schema=registry)
    revised = NetworkDefinition(
        name=PRESET.name,
        kind=PRESET.kind,
        included_sources=('connectomedb2025',),
        interaction_class_scope=('ligand_receptor',),
        evidence_scope={'predicate': ['binds']},
        default_attributes=('endpoints',),
        mandatory_attributes=('label',),
        labels={'preset': 'Revised'},
        curation={'moa_only': False},
        attribute_sources={'protein_localization': {'stage': 'intercell'}},
    )
    register_network(conn, revised, registry_schema=registry)
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM {registry}.network_registry WHERE name = %s',
            [PRESET.name],
        )
        assert cur.fetchone()[0] == 1
    row = _preset_row(conn, registry, PRESET.name)
    assert tuple(row[2]) == revised.interaction_class_scope
    assert row[3] == revised.evidence_scope
    assert row[6] == revised.labels


def test_preset_without_matview_registers(conn, registry):
    """A preset is metadata over the fact table — it needs no schema/matview."""
    bare = NetworkDefinition(
        name='_roundtrip_preset_bare',
        kind='signaling',
        included_sources=('signor',),
        interaction_class_scope=('signaling',),
    )
    register_network(conn, bare, registry_schema=registry)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT schema_name, combined_relation
            FROM {registry}.network_registry WHERE name = %s
            """,
            [bare.name],
        )
        schema_name, combined_relation = cur.fetchone()
    # The matview-era columns stay empty for a fact-table preset (T046 drops them).
    assert schema_name is None
    assert combined_relation is None


def test_live_registry_carries_the_preset_columns(conn):
    """The build database's own ``network_registry`` holds the preset columns."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'network_registry'
            """,
            [SCHEMA],
        )
        columns = {name: (udt, nullable) for name, udt, nullable in cur.fetchall()}
    for column, udt in PRESET_COLUMNS.items():
        assert column in columns, f'{SCHEMA}.network_registry lacks {column}'
        assert columns[column][0] == udt, f'{column} is {columns[column][0]}, want {udt}'
    # Matview-era columns: nullable through the transition, dropped at T046.
    assert columns['schema_name'][1] == 'YES'
    assert columns['combined_relation'][1] == 'YES'
