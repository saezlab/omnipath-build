"""Schema-existence and population tests for the interaction model (008).

Constitution III: every new table this cycle adds is asserted to exist and to
carry rows after a build. Six tables make up the model — the header
``interaction`` (data model §1), the participant ``interaction_party`` (§2),
the participant-role vocabulary ``vocab_relation_role`` (§7), the interaction
record ``interaction_fact_resource`` (§3a), the all-resources collapse of that
record ``interaction_fact_combined`` (§3b) and the resource license terms
``data_source_license`` (§8a).

**Amended 2026-08-20 by research R19/R20.** ``interaction_fact_combined`` was
the projection; it is now one scope's materialisation of a record kept per
contributing resource. The tests follow: the record and the license table are
asserted alongside the rest, ``interaction_fact_combined`` is asserted to be
*derivable* from the record rather than merely present, and the anchoring rule
of §3b — foreign keys point at the record, never at a materialisation — is
asserted against the Postgres catalogue.

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

# The tables the interaction model adds (data model §1, §2, §3a, §3b, §7, §8a).
# `interaction_fact_resource` and `data_source_license` joined the list with the
# R19/R20 amendment: the record is what the projection now stores, and a license
# filter resolves to a resource set over the ordinal levels of §8a.
INTERACTION_TABLES = (
    'interaction',
    'interaction_party',
    'vocab_relation_role',
    'interaction_fact_resource',
    'interaction_fact_combined',
    'data_source_license',
)

# The detail tables of data model §4 and §6. They hang off the record, not off
# the collapse — see the anchoring rule below.
DETAIL_TABLES = ('interaction_assay', 'interaction_ptm')

# The record table (§3a) is keyed per contributing resource and carries a
# deterministic uuid surrogate, because the rest of the key is nullable and
# Postgres `MATCH SIMPLE` does not check a foreign key with any NULL column.
RECORD_KEY_COLUMNS = ('source_id', 'interaction_fact_resource_id')

# `data_source_license` (§8a) stores ordinal levels, not a name: `enables` is
# `self >= other`, so a license question is a range predicate over three
# smallints. `is_known` is the exclusion flag of FR-049.
LICENSE_LEVEL_COLUMNS = ('purpose_level', 'sharing_level', 'attrib_level')

# The columns a collapse carries through from §3a unchanged (data model §3b).
# Everything else on `interaction_fact_combined` is recomputed per scope and is
# therefore not comparable across scopes at all.
CARRIED_THROUGH_COLUMNS = (
    'subject_organism',
    'object_organism',
    'interaction_id',
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


def _table_exists(conn, schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute('SELECT to_regclass(%s)', (f'{schema}.{table}',))
        return cur.fetchone()[0] is not None


def _require_table(conn, schema: str, table: str) -> None:
    """Fail with the missing table named, rather than raising UndefinedTable.

    A test that errors out of the driver has not been made to fail as designed:
    the message has to say which table the build owes.
    """
    assert _table_exists(conn, schema, table), (
        f'{schema}.{table} does not exist; the build never created it'
    )


def _referencing_foreign_keys(conn, table: str) -> list[tuple[str, str, str]]:
    """Every foreign key in the catalogue that points *at* ``table``.

    Catalogue, not source: a grep over the schema module proves what the code
    says, and this rule is about what the database ends up enforcing.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              src_ns.nspname,
              src.relname,
              con.conname
            FROM pg_constraint con
            JOIN pg_class tgt ON tgt.oid = con.confrelid
            JOIN pg_namespace tgt_ns ON tgt_ns.oid = tgt.relnamespace
            JOIN pg_class src ON src.oid = con.conrelid
            JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
            WHERE con.contype = 'f'
              AND tgt.relname = %s
              AND tgt_ns.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY 1, 2, 3
            """,
            (table,),
        )
        return list(cur.fetchall())


def _foreign_key_targets(conn, schema: str, table: str) -> set[str]:
    """The table names every foreign key *on* ``table`` points at."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tgt.relname
            FROM pg_constraint con
            JOIN pg_class src ON src.oid = con.conrelid
            JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
            JOIN pg_class tgt ON tgt.oid = con.confrelid
            WHERE con.contype = 'f'
              AND src_ns.nspname = %s
              AND src.relname = %s
            """,
            (schema, table),
        )
        return {row[0] for row in cur.fetchall()}


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
    _require_table(conn, SCHEMA, table)
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


# ---------------------------------------------------------------------------
# T007 (reopened) — the record, the license table, and a derivable collapse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('column', RECORD_KEY_COLUMNS)
def test_record_table_is_keyed_per_resource(conn, scratch, column):
    """The record keeps the resource, which is what makes summaries decomposable.

    ``interaction_fact_resource`` (§3a) is one row per ordered endpoint pair,
    class and **source**, plus the assertion signature that source states. Drop
    ``source_id`` and the table is the collapse again, with no way to recompute
    a summary for a subset of resources.
    """
    columns = _columns(conn, scratch, 'interaction_fact_resource')
    assert columns, 'interaction_fact_resource was not created'
    assert column in columns, (
        f'{column} is missing from interaction_fact_resource; '
        f'found {sorted(columns)}'
    )


def test_record_surrogate_key_is_the_primary_key(conn, scratch):
    """The surrogate is the PK, because the composite key is not FK-able.

    The rest of the key includes the nullable assertion signature, and Postgres
    defaults to ``MATCH SIMPLE``, under which a foreign key with any NULL column
    is not checked at all. Drug-target rows assert neither sign nor direction,
    so a composite FK would pass silently on nearly every row while looking
    enforced. The uuid surrogate is a ``content_uuid`` over the full key, so it
    is stable across builds.
    """
    columns = _columns(conn, scratch, 'interaction_fact_resource')
    assert columns, 'interaction_fact_resource was not created'
    assert columns['interaction_fact_resource_id'][0] == 'uuid'
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT array_agg(att.attname ORDER BY k.ordinality)
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(
              attnum, ordinality
            )
            JOIN pg_attribute att
              ON att.attrelid = rel.oid AND att.attnum = k.attnum
            WHERE ns.nspname = %s
              AND rel.relname = 'interaction_fact_resource'
              AND con.contype = 'p'
            GROUP BY con.oid
            """,
            (scratch,),
        )
        primary = [row[0] for row in cur.fetchall()]
    assert primary == [['interaction_fact_resource_id']], (
        f'the primary key of interaction_fact_resource is {primary}, not the '
        f'surrogate; a composite key over nullable signature columns cannot be '
        f'referenced under MATCH SIMPLE'
    )


def test_license_table_stores_ordinal_levels(conn, scratch):
    """A license filter is a comparison, so §8a stores levels and not a name.

    ``License.enables(other)`` is ``self >= other``, so "everything usable
    commercially" is ``WHERE purpose_level >= 15`` over a 44-row table. A name
    answers no question on its own: two names can permit the same use, and one
    name under two versions can not.
    """
    columns = _columns(conn, scratch, 'data_source_license')
    assert columns, 'data_source_license was not created'
    for column in LICENSE_LEVEL_COLUMNS:
        assert column in columns, (
            f'{column} is missing from data_source_license; a license filter '
            f'has nothing to compare'
        )
        assert columns[column][0] == 'smallint', (
            f'{column} is {columns[column][0]}, not an ordinal smallint'
        )
    assert 'source_id' in columns, (
        'data_source_license does not name the resource it describes'
    )


def test_license_table_can_exclude_unknown_terms(conn, scratch):
    """Unknown terms are exclusions, never defaults (FR-049).

    A resource whose license could not be mapped must not appear in a
    license-filtered result. Without ``is_known`` the only way to express "no
    record" is a NULL level, which a range predicate silently drops — the same
    answer for the wrong reason — or a permissive default, which is the one
    failure mode of this table that cannot be detected downstream.
    """
    columns = _columns(conn, scratch, 'data_source_license')
    assert columns, 'data_source_license was not created'
    assert 'is_known' in columns, (
        'data_source_license carries no is_known flag; an unmapped license '
        'would have to be admitted under a permissive default (FR-049)'
    )
    assert columns['is_known'][0] == 'boolean'


def test_combined_is_the_collapse_of_the_record(conn):
    """The materialisation is derivable from the record, not merely present.

    ``interaction_fact_combined`` (§3b) is defined as the collapse of §3a over
    **every** resource. If it holds a row the record cannot produce, or misses
    one the record does produce, then it is a second projection rather than one
    scope of the first, and the scope rule has nothing to stand on.
    """
    _require_table(conn, SCHEMA, 'interaction_fact_resource')
    _require_table(conn, SCHEMA, 'interaction_fact_combined')
    with conn.cursor() as cur:
        cur.execute('SET statement_timeout = %s', ('20min',))
        cur.execute(
            f"""
            SELECT
              (
                SELECT count(*)
                FROM (
                  SELECT
                    subject_entity_id,
                    object_entity_id,
                    interaction_class_id
                  FROM {SCHEMA}.interaction_fact_resource
                  GROUP BY 1, 2, 3
                ) AS collapse
              ),
              (SELECT count(*) FROM {SCHEMA}.interaction_fact_combined)
            """
        )
        collapsed, materialised = cur.fetchone()
    assert collapsed == materialised, (
        f'collapsing interaction_fact_resource over every resource yields '
        f'{collapsed} rows, while interaction_fact_combined holds '
        f'{materialised}; the materialisation is not the collapse'
    )


def test_the_collapse_reproduces_the_carried_through_columns(conn):
    """Every group of the record agrees with the row it collapses into.

    §3b splits the fact columns in two: the carried-through ones, which every
    contributing resource shares, and the recomputed ones, which describe the
    scope and are meaningless outside it. The carried-through half is the half
    a collapse must reproduce exactly, and a mismatch here says the two tables
    were derived independently.
    """
    _require_table(conn, SCHEMA, 'interaction_fact_resource')
    _require_table(conn, SCHEMA, 'interaction_fact_combined')
    with conn.cursor() as cur:
        cur.execute('SET statement_timeout = %s', ('20min',))
        cur.execute(
            f"""
            WITH collapse AS (
              SELECT
                subject_entity_id,
                object_entity_id,
                interaction_class_id,
                min(subject_organism) AS subject_organism,
                min(object_organism) AS object_organism,
                -- Postgres has no min(uuid); the text form orders the
                -- same way for a canonical uuid rendering.
                min(interaction_id::text)::uuid AS interaction_id
              FROM {SCHEMA}.interaction_fact_resource
              GROUP BY 1, 2, 3
            )
            SELECT
              count(*) FILTER (WHERE m.subject_entity_id IS NULL),
              count(*) FILTER (WHERE c.subject_entity_id IS NULL),
              count(*) FILTER (
                WHERE c.subject_entity_id IS NOT NULL
                  AND m.subject_entity_id IS NOT NULL
                  AND (
                    c.subject_organism IS DISTINCT FROM m.subject_organism
                    OR c.object_organism IS DISTINCT FROM m.object_organism
                    OR c.interaction_id IS DISTINCT FROM m.interaction_id
                  )
              )
            FROM collapse c
            FULL OUTER JOIN {SCHEMA}.interaction_fact_combined m
              ON m.subject_entity_id = c.subject_entity_id
             AND m.object_entity_id = c.object_entity_id
             AND m.interaction_class_id = c.interaction_class_id
            """
        )
        missing, extra, disagreeing = cur.fetchone()
    assert (missing, extra, disagreeing) == (0, 0, 0), (
        f'the collapse of interaction_fact_resource does not reproduce '
        f'interaction_fact_combined: {missing} collapsed groups are absent '
        f'from the materialisation, {extra} materialised rows the record '
        f'cannot produce, {disagreeing} rows disagree on '
        f'{", ".join(CARRIED_THROUGH_COLUMNS)}'
    )


# ---------------------------------------------------------------------------
# T011b — the anchoring rule (data model §3b)
# ---------------------------------------------------------------------------


def test_no_foreign_key_points_at_the_materialisation(conn, scratch):
    """Foreign keys point at the record, never at a materialisation.

    A key into a materialisation turns the decision not to materialise a scope
    into a migration. ``interaction_fact_combined`` is one scope's collapse and
    is droppable by policy; it also has no stable identity to offer, because a
    serial reshuffles every build and a deterministic id would only re-encode
    the endpoint/class triple a child row can carry directly.

    The check reads ``pg_constraint``, not the schema module: what matters is
    what the database ends up enforcing. It is guarded by the detail tables,
    because a schema with nothing to anchor satisfies the rule for free.
    """
    present = [
        table for table in DETAIL_TABLES if _table_exists(conn, scratch, table)
    ]
    assert present == list(DETAIL_TABLES), (
        f'no detail table to anchor: {sorted(set(DETAIL_TABLES) - set(present))}'
        f' missing from {scratch}, so "nothing references the materialisation" '
        f'holds vacuously and proves nothing'
    )
    referencing = _referencing_foreign_keys(conn, 'interaction_fact_combined')
    assert referencing == [], (
        'foreign keys point at interaction_fact_combined: '
        + '; '.join(
            f'{ns}.{table}.{name}' for ns, table, name in referencing
        )
        + ' — a key into a materialisation turns the decision not to '
        'materialise a scope into a migration'
    )


@pytest.mark.parametrize('table', DETAIL_TABLES)
def test_detail_table_references_the_record(conn, scratch, table):
    """A detail row belongs to a resource, so it keys into §3a.

    An assay is ChEMBL's measurement and a PTM site is SIGNOR's or
    PhosphoSitePlus's. The record is keyed by resource, so the foreign key
    carries that meaning rather than merely satisfying a constraint. The header
    was considered and rejected: it is deduped across resources, so it drops the
    one dimension the detail actually has.
    """
    assert _table_exists(conn, scratch, table), (
        f'{scratch}.{table} was not created'
    )
    columns = _columns(conn, scratch, table)
    assert 'interaction_fact_resource_id' in columns, (
        f'{table} carries no interaction_fact_resource_id; it cannot name the '
        f'resource record its rows belong to'
    )
    targets = _foreign_key_targets(conn, scratch, table)
    assert 'interaction_fact_resource' in targets, (
        f'{table} declares no foreign key to interaction_fact_resource; '
        f'it references {sorted(targets)}'
    )


@pytest.mark.parametrize('table', DETAIL_TABLES)
def test_detail_table_carries_the_denormalised_join_key(conn, scratch, table):
    """Navigation to a collapse is a join, not a key.

    The three denormalised columns reach **any** scope's collapse — a preset's,
    a license-filtered one — and not only ``interaction_fact_combined``. That is
    both cheaper and more general than the foreign key it replaces, and it costs
    the two largest planned tables no per-row key validation, which matters
    where the derive already measures 1,205 seconds under a 20-minute ceiling.
    """
    assert _table_exists(conn, scratch, table), (
        f'{scratch}.{table} was not created'
    )
    columns = _columns(conn, scratch, table)
    missing = [c for c in FACT_KEY_COLUMNS if c not in columns]
    assert not missing, (
        f'{table} is missing the denormalised join key {missing}; without it a '
        f'scoped collapse is unreachable except through the materialisation'
    )
