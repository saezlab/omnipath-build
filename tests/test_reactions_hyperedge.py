"""A metabolic reaction is one interaction of N participants, not N pairs.

A resource publishing a reaction publishes a **star**: a ``Reaction`` or
``Transport`` entity and one ``has_participant`` relation per member, with the
catalyst arriving as a sibling ``controls`` edge that points at the parent. Read
as ordered endpoint pairs, that star flattens into one arity-2 interaction per
member — ``A + B -> C`` becomes three unrelated pairs, each of them between a
metabolite and an abstract event node, and the stoichiometry, the compartment
and the reactant/product roles the resource stated go nowhere at all.

This file asserts the other reading. The star projects to **one** header whose
``arity`` is the number of participants and to one ``interaction_party`` row per
participant, each in its real role, on its side of the arrow, with the
stoichiometry and the compartment the resource published.

Three things the projection has to get right beyond the row counts:

* **The parent's entity type is the gate, not the predicate.** A pathway is
  published under the same ``has_participant`` verb, and a pathway is not a
  reaction.
* **The catalyst is a participant.** It never appears as a ``has_participant``
  row, so a projection reading that verb alone produces a reaction with no
  enzyme, which is not the reaction.
* **The merge is across resources.** Two resources describing the same
  chemistry produce two reaction entities whenever the load side's reaction
  hash cannot identify them, and the header they project to has to be one.

Run::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_reactions_hyperedge.py -v

Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import pytest

from tests.fixtures.interaction_graph import ENTITY, build_interaction_fixture

DATABASE_URL = os.environ.get('DATABASE_URL')
SCRATCH = os.environ.get(
    'OMNIPATH_TEST_SCRATCH_SCHEMA_REACTION_HYPEREDGE',
    'reaction_hyperedge_test',
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; the hyperedge projection needs a Postgres',
)


@pytest.fixture(scope='module')
def conn():
    """A connection to the database the scratch schema is built in."""
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope='module')
def scratch(conn):
    """The fixture graph, projected, in a throwaway namespace."""
    from omnipath_build.db import schema as build_schema
    from omnipath_build.db.derived_tables import rebuild_interaction_tables

    build_schema.ensure_schema(conn, schema=SCRATCH, drop_existing=True)
    conn.commit()
    build_interaction_fixture(conn, SCRATCH)
    rebuild_interaction_tables(conn, schema=SCRATCH)
    conn.commit()
    try:
        yield SCRATCH
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {SCRATCH} CASCADE')
        conn.commit()


def _rows(conn, statement: str, parameters: list | None = None):
    with conn.cursor() as cur:
        cur.execute(statement, parameters or [])
        return cur.fetchall()


def _header_of(conn, schema: str, entity_names: tuple[str, ...]) -> list:
    """The headers whose participant set is exactly ``entity_names``."""
    return _rows(
        conn,
        f"""
        SELECT i.interaction_id, i.arity, i.sources
        FROM {schema}.interaction i
        JOIN (
          SELECT interaction_id,
                 array_agg(DISTINCT entity_id::text ORDER BY entity_id::text)
                   AS participants
          FROM {schema}.interaction_party
          GROUP BY interaction_id
        ) party ON party.interaction_id = i.interaction_id
        WHERE party.participants = (
          SELECT array_agg(DISTINCT wanted ORDER BY wanted)
          FROM unnest(%s::text[]) AS w(wanted)
        )
        """,
        [sorted(ENTITY[name] for name in entity_names)],
    )


def _parties(conn, schema: str, interaction_id) -> dict[str, tuple]:
    rows = _rows(
        conn,
        f"""
        SELECT e.canonical_identifier, role.name, p.side, p.ordinal,
               p.stoichiometry, p.compartment
        FROM {schema}.interaction_party p
        JOIN {schema}.vocab_relation_role role
          ON role.relation_role_id = p.role_id
        JOIN {schema}.entity e ON e.entity_id = p.entity_id
        WHERE p.interaction_id = %s
        """,
        [interaction_id],
    )
    return {row[0]: row[1:] for row in rows}


class TestTheReactionIsOneInteraction:
    """The three-member reaction the fixture publishes twice."""

    REACTION_PARTICIPANTS = ('met_x', 'met_y', 'met_z', 'enz_p')

    @pytest.fixture(scope='class')
    def header(self, conn, scratch):
        found = _header_of(conn, scratch, self.REACTION_PARTICIPANTS)
        assert len(found) == 1, (
            'the reaction projects to one header per participant set; '
            f'found {len(found)}'
        )
        return found[0]

    def test_the_header_has_the_arity_of_the_reaction(self, header):
        """Three members and their enzyme are four participants, not four pairs."""
        _interaction_id, arity, _sources = header
        assert arity == 4

    def test_one_party_row_per_participant(self, conn, scratch, header):
        interaction_id, arity, _sources = header
        parties = _parties(conn, scratch, interaction_id)
        assert len(parties) == arity
        assert set(parties) == {
            f'FIXTURE_{name}' for name in self.REACTION_PARTICIPANTS
        }

    def test_the_roles_are_reaction_roles(self, conn, scratch, header):
        """`reactant`, `product` and `enzyme` — never the endpoint tiebreak."""
        interaction_id, _arity, _sources = header
        parties = _parties(conn, scratch, interaction_id)
        roles = {name: row[0] for name, row in parties.items()}
        assert roles == {
            'FIXTURE_met_x': 'reactant',
            'FIXTURE_met_y': 'reactant',
            'FIXTURE_met_z': 'product',
            'FIXTURE_enz_p': 'enzyme',
        }

    def test_the_sides_are_the_sides_of_the_arrow(self, conn, scratch, header):
        """Substrates left, products right, and the catalyst on neither."""
        interaction_id, _arity, _sources = header
        parties = _parties(conn, scratch, interaction_id)
        sides = {name: row[1] for name, row in parties.items()}
        assert sides['FIXTURE_met_x'] == 1
        assert sides['FIXTURE_met_y'] == 1
        assert sides['FIXTURE_met_z'] == 2
        assert sides['FIXTURE_enz_p'] is None

    def test_the_ordinal_separates_the_two_substrates(
        self,
        conn,
        scratch,
        header,
    ):
        """Two members on one side need two ordinals, or one of them is lost."""
        interaction_id, _arity, _sources = header
        parties = _parties(conn, scratch, interaction_id)
        substrates = sorted(
            row[2]
            for name, row in parties.items()
            if row[1] == 1
        )
        assert substrates == [1, 2]

    def test_the_stoichiometry_survives(self, conn, scratch, header):
        interaction_id, _arity, _sources = header
        parties = _parties(conn, scratch, interaction_id)
        stoichiometry = {name: row[3] for name, row in parties.items()}
        assert stoichiometry['FIXTURE_met_x'] == 2
        assert stoichiometry['FIXTURE_met_z'] == 1
        assert stoichiometry['FIXTURE_enz_p'] is None

    def test_the_compartment_survives(self, conn, scratch, header):
        interaction_id, _arity, _sources = header
        parties = _parties(conn, scratch, interaction_id)
        compartment = {name: row[4] for name, row in parties.items()}
        assert compartment['FIXTURE_met_x'] == 'c'
        assert compartment['FIXTURE_met_z'] == 'm'

    def test_the_two_resources_merge(self, conn, scratch, header):
        """One header, and it credits both resources that published it."""
        _interaction_id, _arity, sources = header
        assert sorted(sources) == ['fixture_res_a', 'fixture_res_b']

    def test_a_silent_resource_does_not_erase_the_other(
        self,
        conn,
        scratch,
        header,
    ):
        """One resource states this member's stoichiometry. The other does not.

        The merged party keeps what was stated. A merge that folded the two
        resources by taking whatever the last one said would lose it.
        """
        interaction_id, _arity, _sources = header
        parties = _parties(conn, scratch, interaction_id)
        assert parties['FIXTURE_met_y'][3] == 1
        assert parties['FIXTURE_met_y'][4] == 'c'

    def test_no_pairwise_header_survives_for_the_star(self, conn, scratch):
        """The reaction replaces its pairs. It does not sit beside them.

        A header over `(reaction parent, member)` is the flattened reading, and
        leaving one behind would double every count the reaction contributes.
        """
        remaining = _rows(
            conn,
            f"""
            SELECT count(*)
            FROM {scratch}.interaction_party parent
            JOIN {scratch}.interaction_party member
              ON member.interaction_id = parent.interaction_id
            JOIN {scratch}.entity parent_entity
              ON parent_entity.entity_id = parent.entity_id
            JOIN {scratch}.entity member_entity
              ON member_entity.entity_id = member.entity_id
            WHERE parent_entity.canonical_identifier
                    IN ('FIXTURE_rxn_a', 'FIXTURE_rxn_b')
              AND member_entity.canonical_identifier
                    IN ('FIXTURE_met_x', 'FIXTURE_met_y', 'FIXTURE_met_z')
            """,
        )
        assert remaining[0][0] == 0

    def test_the_header_id_is_the_hash_of_its_participants(
        self,
        conn,
        scratch,
        header,
    ):
        """The N-ary header uses the one identity scheme, not a parallel one."""
        from omnipath_build.db.derived_tables import (
            interaction_content_uuid_sql,
        )

        interaction_id, _arity, _sources = header
        expression = interaction_content_uuid_sql(
            participants='party.participants',
            interaction_class='vic.name',
        )
        minted = _rows(
            conn,
            f"""
            SELECT {expression}
            FROM {scratch}.interaction i
            JOIN {scratch}.vocab_interaction_class vic
              ON vic.interaction_class_id = i.interaction_class_id
            JOIN (
              SELECT interaction_id, array_agg(entity_id) AS participants
              FROM {scratch}.interaction_party
              GROUP BY interaction_id
            ) party ON party.interaction_id = i.interaction_id
            WHERE i.interaction_id = %s
            """,
            [interaction_id],
        )
        assert str(minted[0][0]) == str(interaction_id)


class TestTheTransportStar:
    """A transport states a membrane side where a reaction states an organelle."""

    def test_the_transporter_joins_its_own_transport(self, conn, scratch):
        found = _header_of(conn, scratch, ('met_in', 'met_out', 'enz_t'))
        assert len(found) == 1
        interaction_id, arity, _sources = found[0]
        assert arity == 3
        parties = _parties(conn, scratch, interaction_id)
        assert parties['FIXTURE_enz_t'][0] == 'enzyme'
        assert parties['FIXTURE_met_in'][4] == 'in'
        assert parties['FIXTURE_met_out'][4] == 'out'


class TestTheGateIsTheEntityType:
    """`has_participant` is also how a pathway lists its members."""

    def test_a_pathway_does_not_become_a_hyperedge(self, conn, scratch):
        """Every pathway membership stays the pair it has always been."""
        arities = _rows(
            conn,
            f"""
            SELECT DISTINCT i.arity
            FROM {scratch}.interaction i
            JOIN {scratch}.interaction_party p
              ON p.interaction_id = i.interaction_id
            JOIN {scratch}.entity e ON e.entity_id = p.entity_id
            WHERE e.canonical_identifier = 'FIXTURE_pathway_a'
            """,
        )
        assert [row[0] for row in arities] == [2]

    def test_the_pathway_keeps_one_header_per_member(self, conn, scratch):
        headers = _rows(
            conn,
            f"""
            SELECT count(DISTINCT p.interaction_id)
            FROM {scratch}.interaction_party p
            JOIN {scratch}.entity e ON e.entity_id = p.entity_id
            WHERE e.canonical_identifier = 'FIXTURE_pathway_a'
            """,
        )
        assert headers[0][0] == 3


class TestTheRecordKeepsTheApiWhole:
    """`interaction_fact_resource` is binary and stays binary.

    The api-service reads that table and nothing else, so the star's record
    rows are kept rather than deleted: one row per `(parent, member)` pair, as
    before. What changes is where they point — at the N-ary header, so a caller
    that follows `interaction_id` from a record row reaches the reaction and its
    participants instead of a two-row group.
    """

    def test_the_star_still_has_record_rows(self, conn, scratch):
        rows = _rows(
            conn,
            f"""
            SELECT count(*)
            FROM {scratch}.interaction_fact_resource r
            JOIN {scratch}.entity subject
              ON subject.entity_id = r.subject_entity_id
            WHERE subject.canonical_identifier
                    IN ('FIXTURE_rxn_a', 'FIXTURE_rxn_b')
            """,
        )
        assert rows[0][0] == 6

    def test_the_record_points_at_the_n_ary_header(self, conn, scratch):
        arities = _rows(
            conn,
            f"""
            SELECT DISTINCT i.arity
            FROM {scratch}.interaction_fact_resource r
            JOIN {scratch}.interaction i
              ON i.interaction_id = r.interaction_id
            JOIN {scratch}.entity subject
              ON subject.entity_id = r.subject_entity_id
            WHERE subject.canonical_identifier
                    IN ('FIXTURE_rxn_a', 'FIXTURE_rxn_b')
            """,
        )
        assert [row[0] for row in arities] == [4]


class TestTheSharedIdentityNamespace:
    """A two-participant reaction can hash to the id of a plain pair.

    The header id is the hash of the class and the sorted participants, and
    nothing in it says which reading produced it. A reaction whose whole
    participant set is ``{q, r}`` therefore lands on the same id as a pair
    between ``q`` and ``r`` in the same class. The pair wins — its header is
    already carrying record rows keyed on it — and the reaction contributes no
    participants rather than adding two more under an unchanged ``arity``.
    """

    @pytest.fixture(scope='class')
    def shared(self, conn, scratch):
        found = _header_of(conn, scratch, ('q', 'r'))
        assert len(found) == 1
        return found[0]

    def test_the_header_keeps_the_pair_arity(self, shared):
        _interaction_id, arity, _sources = shared
        assert arity == 2

    def test_the_reaction_adds_no_participants(self, conn, scratch, shared):
        """Four party rows under an arity of two is the failure being ruled out."""
        interaction_id, _arity, _sources = shared
        parties = _parties(conn, scratch, interaction_id)
        assert len(parties) == 2
        assert {row[0] for row in parties.values()} == {'subject', 'object'}

    def test_the_reaction_keeps_its_record_rows(self, conn, scratch, shared):
        """The rows survive and resolve. Only the detail is lost."""
        interaction_id, _arity, _sources = shared
        rows = _rows(
            conn,
            f"""
            SELECT count(*)
            FROM {scratch}.interaction_fact_resource r
            JOIN {scratch}.entity subject
              ON subject.entity_id = r.subject_entity_id
            WHERE subject.canonical_identifier = 'FIXTURE_rxn_c'
              AND r.interaction_id = %s
            """,
            [interaction_id],
        )
        assert rows[0][0] == 2
