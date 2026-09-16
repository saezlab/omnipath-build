"""A reaction becomes substrate and product edges around a per-reaction node.

The causal-reasoning prior knowledge network is a **binary** graph: every edge
is one metabolite and one gene, and the reaction itself survives only as the
node the two sides meet at. Projecting the build's N-ary reaction headers into
it is therefore a rewrite, not a flattening, and this file asserts the four
things the rewrite has to get right.

* **The enzyme node is per reaction.** The same protein catalysing five
  reactions is five nodes, ``Gene1__P00533`` through ``Gene5__P00533``, because
  a reasoning method treats each of them as an independently switchable step.
  The index is a modelling artifact, which is why it lives in its own table and
  never in ``entity``, and it has to be **stable across rebuilds** — a label
  that moved between runs would make every downstream comparison meaningless.
* **Only the two sides of the arrow project.** A substrate reaches the enzyme
  node and the enzyme node reaches a product. Nothing joins a substrate to a
  product, nothing joins the enzyme to itself, and a party row holding a role
  that names no side of the arrow — a cofactor, a regulator — is left alone
  and counted, because a genome-scale model has no such role and reading one as
  a substrate would state something the resource did not.
* **A reaction nobody names an enzyme for keeps its edges.** It is the majority
  shape in a metabolic model. The reaction stands in for the unknown catalyst,
  the row says so in ``orphan``, and no reaction is dropped for want of a
  catalyst.
* **A reversible reaction runs both ways, and only a reversible one does.** The
  resource states the direction on the reaction event, under two spellings per
  value. A reversible conversion gains the mirrored pair — every product into
  the enzyme, every reactant out of it — on a ``_rev`` copy of the gene node
  alone, never of the metabolite. A one-way conversion gains nothing. A
  conversion nobody published a direction for gains nothing either, and says
  so with a null rather than by looking one-way, because a source that is
  silent and a source that says left to right are different claims.

Run::

    DATABASE_URL=postgresql://omnipath:omnipath@localhost:5404/omnipath \
        uv run --with pytest pytest tests/test_cosmos_projection.py -v

Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os
import re

import pytest

from tests.fixtures.interaction_graph import ENTITY, build_interaction_fixture

DATABASE_URL = os.environ.get('DATABASE_URL')
SCRATCH = os.environ.get(
    'OMNIPATH_TEST_SCRATCH_SCHEMA_COSMOS',
    'cosmos_projection_test',
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason='DATABASE_URL not set; the binary projection needs a Postgres',
)

# The node strings the fixture's ordinary conversion has to produce. They are
# written out rather than composed, because a helper that built them from the
# same rule the projection uses would agree with it however wrong the rule is.
MET_A = 'Metab__FIXTURE_cos_met_a_c'
MET_B = 'Metab__FIXTURE_cos_met_b'
MET_C = 'Metab__FIXTURE_cos_met_c_m'
ENZYME_ID = 'FIXTURE_cos_enz'
ORPHAN_REACTION_ID = 'FIXTURE_cos_rxn_orphan'

# The node strings of the reversible conversion, both sides of the mirror.
BACK_IN_A = 'Metab__FIXTURE_cos_back_in_a_c'
BACK_IN_B = 'Metab__FIXTURE_cos_back_in_b_c'
BACK_OUT = 'Metab__FIXTURE_cos_back_out_c'
BACK_ENZYME_ID = 'FIXTURE_cos_enz_back'
ORPHAN_BACK_REACTION_ID = 'FIXTURE_cos_rxn_orphan_back'

GENE_NODE = re.compile(r'^Gene(\d+)__(.+)$')
REVERSE_SUFFIX = '_rev'
ORPHAN_NODE_PREFIX = 'orphanReac'

COLUMNS = (
    'source_label',
    'target_label',
    'source_entity_id',
    'target_entity_id',
    'source_type',
    'target_type',
    'source_compartment',
    'target_compartment',
    'mor',
    'interaction_type',
    'reaction_entity_id',
    'interaction_id',
    'reaction_index',
    'orphan',
    'reverse',
    'direction',
    'source_id_type',
    'target_id_type',
    'sources',
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
        # A failed projection leaves the transaction aborted, and the drop
        # would then leak the schema into the next run.
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {SCRATCH} CASCADE')
        conn.commit()


@pytest.fixture(scope='module')
def projection(conn, scratch):
    """The binary edges, built once, and what the step reported building."""
    from omnipath_build.cosmos import (
        build_cosmos_projection,
        ensure_cosmos_edge_table,
    )

    ensure_cosmos_edge_table(conn, schema=scratch)
    conn.commit()
    try:
        stats = build_cosmos_projection(conn, schema=scratch)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return stats


def _rows(conn, statement: str, parameters: list | None = None):
    with conn.cursor() as cur:
        cur.execute(statement, parameters or [])
        return cur.fetchall()


def _edges(
    conn,
    schema: str,
    where: str = 'true',
    parameters: list | None = None,
) -> list[dict]:
    """The projected edges matching ``where``, as column-named rows."""
    from omnipath_build.cosmos import TABLE

    rows = _rows(
        conn,
        f"""
        SELECT {', '.join(COLUMNS)}
        FROM {schema}.{TABLE}
        WHERE {where}
        """,
        parameters,
    )
    return [dict(zip(COLUMNS, row, strict=True)) for row in rows]


def _reaction_edges(conn, schema: str, name: str) -> list[dict]:
    """The edges the named fixture reaction produced, connectors aside."""
    return _edges(
        conn,
        schema,
        "reaction_entity_id = %s AND interaction_type <> 'connector'",
        [ENTITY[name]],
    )


def _labels(edges: list[dict]) -> set[tuple[str, str]]:
    return {(edge['source_label'], edge['target_label']) for edge in edges}


def _gene_nodes(edges: list[dict]) -> set[str]:
    """Every enzyme node the given edges touch, on either end."""
    return {
        label
        for edge in edges
        for label in (edge['source_label'], edge['target_label'])
        if GENE_NODE.match(label)
    }


def _one_gene_node(edges: list[dict]) -> str:
    nodes = _gene_nodes(edges)
    assert len(nodes) == 1, (
        f'these edges should meet at one enzyme node; found {sorted(nodes)}'
    )
    return nodes.pop()


class TestTheEdgesAreTheSubstrateAndProductPairs:
    """The ordinary conversion: two substrates, one product, one enzyme."""

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        found = _reaction_edges(conn, scratch, 'cos_rxn')
        assert found, 'the catalysed conversion produced no edges at all'
        return found

    @pytest.fixture(scope='class')
    def gene_label(self, edges):
        return _one_gene_node(edges)

    def test_one_edge_per_substrate_and_one_per_product(self, edges):
        """Two substrates in, one product out, and nothing besides."""
        assert len(edges) == 3

    def test_every_substrate_points_at_the_enzyme(self, edges, gene_label):
        incoming = {
            edge['source_label']
            for edge in edges
            if edge['target_label'] == gene_label
        }
        assert incoming == {MET_A, MET_B}

    def test_the_enzyme_points_at_the_product(self, edges, gene_label):
        outgoing = {
            edge['target_label']
            for edge in edges
            if edge['source_label'] == gene_label
        }
        assert outgoing == {MET_C}

    def test_no_substrate_reaches_a_product_directly(self, edges):
        """The enzyme node is the only path across the arrow.

        A substrate-to-product edge would say the conversion happens whether or
        not the gene is expressed, which is the one thing the network exists to
        deny.
        """
        assert (MET_A, MET_C) not in _labels(edges)
        assert (MET_B, MET_C) not in _labels(edges)

    def test_the_enzyme_does_not_point_at_itself(self, edges, gene_label):
        assert (gene_label, gene_label) not in _labels(edges)

    def test_the_edges_carry_no_sign(self, edges):
        """The projection knows the direction of the arrow, not its sign."""
        assert {edge['mor'] for edge in edges} == {1}

    def test_the_edges_are_catalysis(self, edges):
        assert {edge['interaction_type'] for edge in edges} == {'catalysis'}

    def test_the_two_sides_keep_their_entity_and_their_type(
        self,
        edges,
        gene_label,
    ):
        """A metabolite side resolves to an entity, and says it is one."""
        metabolite_sides = [
            (edge['source_entity_id'], edge['source_type'])
            for edge in edges
            if edge['source_label'] != gene_label
        ]
        assert metabolite_sides
        assert {kind for _entity_id, kind in metabolite_sides} == {'metabolite'}
        assert all(entity_id for entity_id, _kind in metabolite_sides)

    def test_the_edges_credit_the_resource_that_published_them(self, edges):
        for edge in edges:
            assert 'fixture_res_a' in edge['sources']


class TestTheNodeStringsAreTheOnesTheNetworkReads:
    """A consumer matches these strings literally. They are the contract."""

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        return _reaction_edges(conn, scratch, 'cos_rxn')

    def test_a_metabolite_states_the_compartment_it_sits_in(self, edges):
        assert MET_A in {edge['source_label'] for edge in edges}
        assert MET_C in {edge['target_label'] for edge in edges}

    def test_a_metabolite_with_no_compartment_carries_no_separator(
        self,
        edges,
    ):
        """``Metab__x_`` is a different node from ``Metab__x``.

        The compartment is joined on with an underscore, so a metabolite whose
        location nobody stated has to end at the identifier rather than at the
        separator that would have introduced one.
        """
        labels = {edge['source_label'] for edge in edges}
        assert MET_B in labels
        assert 'Metab__FIXTURE_cos_met_b_' not in labels

    def test_the_metabolite_prefix_is_five_letters(
        self,
        conn,
        scratch,
        projection,
    ):
        """``Metab__``, not ``Metabo__``.

        The prose contract says the longer one. The pinned consumer tests say
        the shorter, and the consumer is what the strings are for.
        """
        from omnipath_build.cosmos import TABLE

        wrong = _rows(
            conn,
            f"""
            SELECT count(*)
            FROM {scratch}.{TABLE}
            WHERE left(source_label, 8) = 'Metabo__'
               OR left(target_label, 8) = 'Metabo__'
            """,
        )
        assert wrong[0][0] == 0

        right = _rows(
            conn,
            f"""
            SELECT count(*)
            FROM {scratch}.{TABLE}
            WHERE left(source_label, 7) = 'Metab__'
               OR left(target_label, 7) = 'Metab__'
            """,
        )
        assert right[0][0] > 0

    def test_the_enzyme_node_names_the_reaction_it_belongs_to(self, edges):
        """``Gene{N}__<id>``, with the N the row reports in its own column."""
        for edge in edges:
            for side in ('source', 'target'):
                match = GENE_NODE.match(edge[f'{side}_label'])
                if match is None:
                    continue
                assert edge['reaction_index'] is not None
                assert edge[f'{side}_label'] == (
                    f'Gene{edge["reaction_index"]}__{ENZYME_ID}'
                )

    def test_the_suffix_and_the_column_say_the_same_thing(
        self,
        conn,
        scratch,
        projection,
    ):
        """A ``_rev`` label and a ``reverse`` row are one claim, not two.

        A consumer reads the direction off the label and the build reads it off
        the column. A row where the two disagree would be a network whose
        answer depends on which of them the reader trusted.
        """
        from omnipath_build.cosmos import TABLE

        disagreeing = _rows(
            conn,
            f"""
            SELECT source_label, target_label, reverse
            FROM {scratch}.{TABLE}
            WHERE (
                right(source_label, 4) = '_rev'
                OR right(target_label, 4) = '_rev'
              ) <> reverse
              AND interaction_type <> 'connector'
            """,
        )
        assert disagreeing == []

    def test_only_the_enzyme_side_of_a_reverse_row_is_suffixed(
        self,
        conn,
        scratch,
        projection,
    ):
        """The mirrored metabolite is the same metabolite.

        Suffixing it would mint a second node for one molecule, and the
        forward and reverse halves of a reversible reaction would stop meeting
        anywhere in the graph.
        """
        from omnipath_build.cosmos import TABLE

        suffixed = _rows(
            conn,
            f"""
            SELECT source_label, target_label
            FROM {scratch}.{TABLE}
            WHERE (
                left(source_label, 7) = 'Metab__'
                AND right(source_label, 4) = '_rev'
              )
               OR (
                left(target_label, 7) = 'Metab__'
                AND right(target_label, 4) = '_rev'
              )
            """,
        )
        assert suffixed == []


class TestTheCompartmentIsAColumnOfItsOwn:
    """Reading it back out of the label is parsing, not querying."""

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        return _reaction_edges(conn, scratch, 'cos_rxn')

    def test_the_substrate_compartment_is_readable_without_the_label(
        self,
        edges,
    ):
        compartments = {
            edge['source_label']: edge['source_compartment']
            for edge in edges
        }
        assert compartments[MET_A] == 'c'

    def test_the_product_compartment_is_readable_without_the_label(
        self,
        edges,
    ):
        compartments = {
            edge['target_label']: edge['target_compartment']
            for edge in edges
        }
        assert compartments[MET_C] == 'm'

    def test_an_unstated_compartment_stays_unstated(self, edges):
        """NULL says nobody said. An empty string says somebody said nothing."""
        compartments = {
            edge['source_label']: edge['source_compartment']
            for edge in edges
        }
        assert compartments[MET_B] is None

    def test_the_enzyme_node_claims_no_compartment(self, edges):
        for edge in edges:
            for side in ('source', 'target'):
                if GENE_NODE.match(edge[f'{side}_label']):
                    assert edge[f'{side}_compartment'] is None


class TestAReactionWithNoNamedEnzymeKeepsItsEdges:
    """Most reactions in a metabolic model have no catalyst on record."""

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        found = _reaction_edges(conn, scratch, 'cos_rxn_orphan')
        assert found, (
            'the conversion with no named enzyme was dropped; it is the '
            'majority case, not an edge case'
        )
        return found

    def test_both_sides_of_the_arrow_survive(self, edges):
        assert len(edges) == 2

    def test_the_reaction_stands_in_for_the_unknown_catalyst(self, edges):
        labels = {
            label
            for edge in edges
            for label in (edge['source_label'], edge['target_label'])
            if GENE_NODE.match(label)
        }
        assert len(labels) == 1
        label = labels.pop()
        index = {edge['reaction_index'] for edge in edges}
        assert len(index) == 1
        assert label == (
            f'Gene{index.pop()}__orphanReac{ORPHAN_REACTION_ID}'
        )

    def test_the_missing_enzyme_is_stated_in_the_data(self, edges):
        """A consumer has to be able to tell a real gene from a placeholder."""
        assert {edge['orphan'] for edge in edges} == {True}

    def test_the_placeholder_resolves_to_no_entity(self, edges):
        """It is not a protein, so it holds no protein's id."""
        for edge in edges:
            for side in ('source', 'target'):
                if GENE_NODE.match(edge[f'{side}_label']):
                    assert edge[f'{side}_entity_id'] is None

    def test_a_catalysed_reaction_is_not_marked_orphan(
        self,
        conn,
        scratch,
        projection,
    ):
        catalysed = _reaction_edges(conn, scratch, 'cos_rxn')
        assert {edge['orphan'] for edge in catalysed} == {False}


class TestAPartyOnNeitherSideOfTheArrowIsNotProjected:
    """A cofactor and a regulator hold roles a binarised model has no word for.

    Every species in a genome-scale model is a reactant or a product by the
    sign of its coefficient. Reading a regulator as a substrate would put a
    statement into the network that no resource made, so the step leaves those
    party rows alone and reports how many it left.
    """

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        return _reaction_edges(conn, scratch, 'cos_rxn_helper')

    def test_only_the_substrate_and_the_product_project(self, edges):
        assert len(edges) == 2

    def test_the_cofactor_is_not_read_as_a_substrate(
        self,
        conn,
        scratch,
        projection,
    ):
        from omnipath_build.cosmos import TABLE

        rows = _rows(
            conn,
            f"""
            SELECT count(*)
            FROM {scratch}.{TABLE}
            WHERE source_label LIKE %s OR target_label LIKE %s
            """,
            ['%FIXTURE_cos_cofactor%'] * 2,
        )
        assert rows[0][0] == 0

    def test_the_regulator_is_not_read_as_an_enzyme(
        self,
        conn,
        scratch,
        projection,
    ):
        from omnipath_build.cosmos import TABLE

        rows = _rows(
            conn,
            f"""
            SELECT count(*)
            FROM {scratch}.{TABLE}
            WHERE source_label LIKE %s OR target_label LIKE %s
            """,
            ['%FIXTURE_cos_regulator%'] * 2,
        )
        assert rows[0][0] == 0

    def test_the_step_reports_what_it_left_out(self, projection):
        """A silent skip is indistinguishable from a projection that failed."""
        assert projection.skipped_parties >= 2


class TestAReversibleReactionRunsBothWays:
    """A reversible conversion is two reactions sharing one enzyme.

    The resource states the direction on the reaction event. Where it says
    reversible, the network needs the mirrored pair as well, on a node of its
    own: a reasoning method has to be able to switch the two directions
    independently, and one node carrying both would tie them together.
    """

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        found = _reaction_edges(conn, scratch, 'cos_rxn_back')
        assert found, 'the reversible conversion produced no edges at all'
        return found

    @pytest.fixture(scope='class')
    def forward(self, edges):
        return [edge for edge in edges if not edge['reverse']]

    @pytest.fixture(scope='class')
    def backward(self, edges):
        found = [edge for edge in edges if edge['reverse']]
        assert found, (
            'the conversion is published as reversible and gained no '
            'mirrored edges'
        )
        return found

    def test_the_forward_pair_is_what_it_was(self, forward):
        gene = _one_gene_node(forward)
        assert _labels(forward) == {
            (BACK_IN_A, gene),
            (BACK_IN_B, gene),
            (gene, BACK_OUT),
        }

    def test_the_product_gains_an_edge_into_the_enzyme(self, backward):
        gene = _one_gene_node(backward)
        assert (BACK_OUT, gene) in _labels(backward)

    def test_every_reactant_gains_an_edge_out_of_the_enzyme(self, backward):
        gene = _one_gene_node(backward)
        assert (gene, BACK_IN_A) in _labels(backward)
        assert (gene, BACK_IN_B) in _labels(backward)

    def test_the_mirror_holds_nothing_else(self, backward):
        """Run backwards, a product is a substrate and a substrate a product.

        The mirrored side is the forward side with the arrow turned round, not
        a second chance to join every metabolite to every other.
        """
        assert len(backward) == 3

    def test_the_mirrored_node_is_the_forward_node_suffixed(
        self,
        forward,
        backward,
    ):
        assert _one_gene_node(backward) == (
            _one_gene_node(forward) + REVERSE_SUFFIX
        )

    def test_the_mirrored_node_still_names_its_enzyme(self, backward):
        edge = backward[0]
        assert _one_gene_node(backward) == (
            f'Gene{edge["reaction_index"]}__{BACK_ENZYME_ID}{REVERSE_SUFFIX}'
        )

    def test_the_metabolites_are_the_same_molecules(self, backward):
        """Literally the same strings, so the two halves meet in the graph."""
        labels = {
            label
            for edge in backward
            for label in (edge['source_label'], edge['target_label'])
            if not GENE_NODE.match(label)
        }
        assert labels == {BACK_IN_A, BACK_IN_B, BACK_OUT}

    def test_the_forward_rows_are_not_marked_mirrored(self, forward):
        assert {edge['reverse'] for edge in forward} == {False}

    def test_the_direction_is_on_every_row(self, edges):
        assert {edge['direction'] for edge in edges} == {'reversible'}

    def test_both_halves_belong_to_one_reaction(self, edges):
        """The index is what says the two directions are one conversion."""
        assert len({edge['reaction_index'] for edge in edges}) == 1


class TestAOneWayReactionGainsNothing:
    """A resource that says left to right has said which way it runs."""

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        found = _reaction_edges(conn, scratch, 'cos_rxn_fwd')
        assert found, 'the one-way conversion produced no edges at all'
        return found

    def test_it_keeps_only_the_forward_pair(self, edges):
        assert len(edges) == 2
        assert {edge['reverse'] for edge in edges} == {False}

    def test_nothing_it_holds_is_suffixed(self, edges):
        for edge in edges:
            assert not edge['source_label'].endswith(REVERSE_SUFFIX)
            assert not edge['target_label'].endswith(REVERSE_SUFFIX)

    def test_the_direction_says_which_way(self, edges):
        assert {edge['direction'] for edge in edges} == {'left_to_right'}


class TestAnUnpublishedDirectionIsNotAOneWayReaction:
    """Silence and a stated direction are different claims about a reaction.

    One resource states no direction on any of its reactions. Recording that as
    left to right would turn a gap in what the source says into a statement the
    source never made, and nothing downstream could tell the two apart again.
    """

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        return _reaction_edges(conn, scratch, 'cos_rxn')

    def test_nothing_is_mirrored(self, edges):
        assert {edge['reverse'] for edge in edges} == {False}

    def test_the_direction_is_null(self, edges):
        assert {edge['direction'] for edge in edges} == {None}

    def test_a_one_way_reaction_is_still_told_apart_from_a_silent_one(
        self,
        conn,
        scratch,
        edges,
    ):
        one_way = _reaction_edges(conn, scratch, 'cos_rxn_fwd')
        assert {edge['direction'] for edge in one_way} == {'left_to_right'}
        assert {edge['direction'] for edge in edges} == {None}


class TestTheTwoSpellingsOfADirectionAgree:
    """Each direction reaches the build in upper and in lower case.

    The upper-case forms carry most of the build and the lower-case forms the
    rest, so reading one spelling and not the other would silently drop a
    resource rather than fail.
    """

    @pytest.fixture(scope='class')
    def by_reaction(self, conn, scratch, projection):
        return {
            name: _reaction_edges(conn, scratch, name)
            for name in (
                'cos_rxn_back',
                'cos_rxn_back_lower',
                'cos_rxn_fwd',
                'cos_rxn_fwd_lower',
            )
        }

    def test_both_spellings_of_reversible_read_the_same(self, by_reaction):
        for name in ('cos_rxn_back', 'cos_rxn_back_lower'):
            edges = by_reaction[name]
            assert edges, f'{name} produced no edges'
            assert {edge['direction'] for edge in edges} == {'reversible'}
            assert any(edge['reverse'] for edge in edges), (
                f'{name} is published as reversible and gained no mirror'
            )

    def test_both_spellings_of_one_way_read_the_same(self, by_reaction):
        for name in ('cos_rxn_fwd', 'cos_rxn_fwd_lower'):
            edges = by_reaction[name]
            assert edges, f'{name} produced no edges'
            assert {edge['direction'] for edge in edges} == {'left_to_right'}
            assert not any(edge['reverse'] for edge in edges)


class TestAReversibleReactionWithNoNamedEnzyme:
    """The two situations meet: a placeholder node that also mirrors."""

    @pytest.fixture(scope='class')
    def edges(self, conn, scratch, projection):
        found = _reaction_edges(conn, scratch, 'cos_rxn_orphan_back')
        assert found, (
            'a reversible conversion with no named enzyme produced no edges'
        )
        return found

    def test_the_mirror_is_there_as_well(self, edges):
        assert len(edges) == 4
        assert len([edge for edge in edges if edge['reverse']]) == 2

    def test_the_placeholder_takes_the_suffix(self, edges):
        backward = [edge for edge in edges if edge['reverse']]
        index = {edge['reaction_index'] for edge in backward}
        assert len(index) == 1
        assert _one_gene_node(backward) == (
            f'Gene{index.pop()}__orphanReac{ORPHAN_BACK_REACTION_ID}'
            f'{REVERSE_SUFFIX}'
        )

    def test_it_is_still_an_orphan_on_both_sides(self, edges):
        assert {edge['orphan'] for edge in edges} == {True}

    def test_the_direction_is_recorded(self, edges):
        assert {edge['direction'] for edge in edges} == {'reversible'}


class TestEveryEnzymeNodeIsReachableFromItsBareIdentifier:
    """The per-reaction index is the network's, not the caller's.

    A caller holding a UniProt accession cannot know which N the projection
    gave it, so each enzyme node carries a connector from the bare identifier
    to the node. Without it the gene side of the network is unaddressable.
    """

    @pytest.fixture(scope='class')
    def connectors(self, conn, scratch, projection):
        return _edges(conn, scratch, "interaction_type = 'connector'")

    @pytest.fixture(scope='class')
    def gene_nodes(self, conn, scratch, projection):
        return _gene_nodes(
            _edges(conn, scratch, "interaction_type <> 'connector'"),
        )

    @pytest.fixture(scope='class')
    def forward_gene_nodes(self, conn, scratch, projection):
        return _gene_nodes(
            _edges(
                conn,
                scratch,
                "interaction_type <> 'connector' AND NOT reverse",
            ),
        )

    def test_the_connector_joins_the_bare_identifier_to_the_node(
        self,
        connectors,
    ):
        """The identifier a caller holds carries no index and no suffix."""
        for edge in connectors:
            match = GENE_NODE.match(edge['target_label'])
            assert match is not None, (
                f'a connector points at {edge["target_label"]}, which is not '
                'an enzyme node'
            )
            bare = match.group(2)
            if bare.endswith(REVERSE_SUFFIX):
                bare = bare[: -len(REVERSE_SUFFIX)]
            # A reaction standing in for an unknown enzyme is reachable by the
            # identifier the resource published, not by the node string the
            # projection built around it.
            if bare.startswith(ORPHAN_NODE_PREFIX):
                bare = bare[len(ORPHAN_NODE_PREFIX):]
            assert edge['source_label'] == bare

    def test_every_forward_enzyme_node_has_one(
        self,
        connectors,
        forward_gene_nodes,
        gene_nodes,
    ):
        """Whether the mirrored node gets its own connector is the step's call.

        What is not optional is that every enzyme node the forward graph holds
        is reachable, and that a connector never points at something that is
        not an enzyme node at all.
        """
        assert forward_gene_nodes
        targets = {edge['target_label'] for edge in connectors}
        assert forward_gene_nodes <= targets
        assert targets <= gene_nodes

    def test_the_named_enzyme_reaches_its_node(self, connectors):
        joined = {
            (edge['source_label'], edge['target_label'])
            for edge in connectors
        }
        assert any(
            source == ENZYME_ID and target.endswith(f'__{ENZYME_ID}')
            for source, target in joined
        )


class TestASecondRunLandsOnTheSameGraph:
    """The index is embedded in every enzyme label, so it has to be stable.

    Ranking the reactions by their entity id gives the same N on every rebuild.
    Numbering them by the order the rows happened to arrive in does not, and a
    network whose node names move between builds cannot be compared with
    anything, including itself.
    """

    @pytest.fixture(scope='class')
    def rebuilt(self, conn, scratch, projection):
        from omnipath_build.cosmos import build_cosmos_projection

        before = _edges(conn, scratch)
        build_cosmos_projection(conn, schema=scratch)
        conn.commit()
        return before, _edges(conn, scratch)

    def test_the_rows_are_not_written_twice(self, rebuilt):
        before, after = rebuilt
        assert len(after) == len(before)

    def test_every_label_comes_back_the_same(self, rebuilt):
        before, after = rebuilt
        assert _labels(after) == _labels(before)

    def test_the_reaction_index_does_not_move(self, rebuilt):
        before, after = rebuilt
        assert sorted(
            (edge['source_label'], edge['target_label'], edge['reaction_index'])
            for edge in after
        ) == sorted(
            (edge['source_label'], edge['target_label'], edge['reaction_index'])
            for edge in before
        )


def test_the_edges_live_in_their_own_table():
    """The enzyme nodes are modelling artifacts and stay out of the entities.

    One protein in five hundred reactions is five hundred nodes here. Writing
    those into the canonical entity table would move every count the rest of
    the build reports, so the projection owns a table instead.
    """
    from omnipath_build.cosmos import TABLE

    assert TABLE == 'cosmos_edge'
