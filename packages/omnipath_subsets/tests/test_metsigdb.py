"""The existing five MetSigDB products over small published, resolved releases."""

from collections import Counter
from copy import deepcopy
import json
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres import loader
from omnipath_subsets.metsigdb import build as metsigdb_build, rebuild
from omnipath_subsets.metsigdb.mapping import RESOURCES, KEGG_OVERVIEW_MAPS
from omnipath_core.source_attributes import TRAIT_TYPE
from release_fixture import (
    annotation,
    entity,
    payload,
    relation,
    source_context_annotations,
    write_release,
    write_resource,
)


def test_five_existing_products_and_native_identifier_domains():
    assert [(rule.name, rule.source, rule.set_type) for rule in RESOURCES] == [
        ("Reactome", "reactome", "pathway"),
        ("WikiPathways", "wikipathways", "pathway"),
        ("KEGG", "kegg", "pathway"),
        ("MACdb", "macdb", "disease"),
        ("ClassyFire", "hmdb", "chemical_class"),
    ]
    assert len(KEGG_OVERVIEW_MAPS) == 11
    assert all(value.startswith("rn") for value in KEGG_OVERVIEW_MAPS)


@pytest.mark.parametrize("policy", ["legacy_matched", "guess", None])
def test_unsupported_eligibility_is_rejected_before_database_access(policy):
    class NoDatabase:
        def __getattribute__(self, name):
            raise AssertionError("No database access allowed")

    with pytest.raises(ValueError, match="cannot be recovered"):
        rebuild(NoDatabase(), "unused", eligibility_policy=policy)


@pytest.fixture
def metsig_release(tmp_path, postgres_dsn):
    # Every source has fewer than 20 original fixture records. No parser,
    # resolver, reference preparation or source download participates.
    chemical = entity(
        "CHEBI:1",
        "chemical_entity",
        label="Metabolite A",
        aliases=[
            ("inchikey", "AAAAAAAAAAAAAA-BBBBBBBBBB-C"),
            ("inchikey", "ZZ-invalid-key"),
            ("hmdb", "HMDB00008"),
            ("pubchem", "123"),
            ("smiles", "CCO"),
            ("kegg", "C00001"),
        ],
    )
    fallback = entity(
        "CCCCCCCCCCCCCC-DDDDDDDDDD-E",
        "small_molecule",
        "inchikey",
        label="Source chemical B",
        aliases=[("inchikey", "ZZZZZZZZZZZZZZ-YYYYYYYYYY-Z"), ("inchikey", "ZZ-invalid-key")],
    )
    protein = entity("P00001", "protein", "uniprot", label="Excluded protein")
    reactome = entity("R-HSA-1", "pathway", "reactome", label="Reactome pathway", taxon="9606")
    ontology_pathway = entity("R-HSA-99", "pathway", "reactome", label="Not a membership")
    wiki = entity("WP1", "pathway", "wikipathways", label="Mouse pathway", taxon="NCBITaxon:10090")
    overview = entity("rn01100", "pathway", "kegg_pathway", label="Overview")
    reaction_entity = entity("R00001", "molecular_activity", "kegg_reaction", label="Reaction")
    trait = entity(
        "1",
        "ontology_class",
        "macdb_trait",
        label="Trait label",
        annotations=[
            annotation(TRAIT_TYPE, value, source="macdb")
            for value in (None, "", "cancer", "z-other")
        ],
    )
    leaf = entity("CHEMONT:0002", "ontology_class", "chemont")
    root = entity("CHEMONT:0001", "ontology_class", "chemont", label="Superclass")
    part_only = entity("CHEMONT:0003", "ontology_class", "chemont", label="Not a superclass")

    first = relation(chemical, "part_of", reactome, source="reactome", row_id="10")
    second = deepcopy(first)
    second["evidence"][0]["row_id"] = "2"
    # One published relation contains both source observations. Lowest numeric
    # source row, rather than text ordering, controls the chosen provenance.
    first["evidence"].extend(second["evidence"])
    first["evidence_count"] = 2
    write_resource(
        tmp_path,
        "reactome",
        [chemical, fallback, protein, reactome, ontology_pathway],
        [
            first,
            relation(reactome, "has_part", fallback, source="reactome", row_id="3"),
            relation(protein, "part_of", reactome, source="reactome", row_id="4"),
            relation(
                ontology_pathway,
                "has_part",
                chemical,
                source="reactome",
                row_id="5",
                statement_kind="ontology",
            ),
        ],
    )
    write_resource(
        tmp_path,
        "wikipathways",
        [chemical, wiki],
        [
            relation(wiki, "associated_with", chemical, source="wikipathways"),
        ],
    )
    write_resource(
        tmp_path,
        "kegg",
        [chemical, fallback, protein, overview, reaction_entity],
        [
            relation(overview, "has_part", reaction_entity, source="kegg", row_id="1"),
            relation(
                reaction_entity,
                "has_input",
                chemical,
                source="kegg",
                row_id="2",
                annotations=source_context_annotations({}, source="kegg"),
            ),
            relation(
                reaction_entity,
                "has_output",
                fallback,
                source="kegg",
                row_id="3",
                annotations=source_context_annotations({}, source="kegg"),
            ),
            relation(reaction_entity, "has_participant", protein, source="kegg", row_id="4"),
        ],
    )
    # A bare trait ID deliberately collides with an unrelated chemical ID.
    collision = entity("1", "chemical_entity", "chebi", label="Wrong label")
    write_resource(
        tmp_path,
        "macdb",
        [chemical, fallback, trait, collision],
        [
            relation(
                chemical,
                "associated_with",
                trait,
                source="macdb",
                row_id="1",
                annotations=[annotation(TRAIT_TYPE, "a-relation-only", source="macdb")],
            ),
            relation(trait, "associated_with", fallback, source="macdb", row_id="2"),
        ],
        [payload(trait, {"Trait_Type": "raw value must not be used"}, source="macdb")],
    )
    write_resource(
        tmp_path,
        "hmdb",
        [chemical, fallback, leaf, root],
        [
            relation(chemical, "associated_with", leaf, source="hmdb", row_id="1"),
            relation(chemical, "associated_with", root, source="hmdb", row_id="2"),
            relation(root, "associated_with", fallback, source="hmdb", row_id="3"),
        ],
    )
    labelled_leaf = deepcopy(leaf)
    labelled_leaf["label"] = "Leaf class"
    write_resource(
        tmp_path,
        "chemont",
        [labelled_leaf, root, part_only],
        [
            relation(leaf, "subclass_of", root, source="chemont", statement_kind="ontology"),
            relation(
                leaf, "part_of", part_only, source="chemont", row_id="2", statement_kind="ontology"
            ),
        ],
    )
    sources = [rule.source for rule in RESOURCES] + ["chemont"]
    schema = "met_" + uuid.uuid4().hex
    loaded = loader.load_release(
        tmp_path, write_release(tmp_path, sources), postgres_dsn, schema=schema
    )
    (tmp_path / "resources").rename(tmp_path / "unavailable_resources")
    return dict(
        dsn=postgres_dsn,
        schema=schema,
        loaded=loaded,
        chemical=chemical,
        fallback=fallback,
        protein=protein,
        reaction=reaction_entity,
        trait=trait,
        leaf=leaf,
        root=root,
        part_only=part_only,
    )


def run(fixture):
    with psycopg.connect(fixture["dsn"]) as conn:
        result = rebuild(conn, fixture["schema"])
        json.dumps(result)  # Public statistics must be JSON serializable.
    return result


def rows(fixture, statement, params=None):
    with psycopg.connect(fixture["dsn"]) as conn:
        return conn.execute(
            sql.SQL(statement).format(s=sql.Identifier(fixture["schema"])), params
        ).fetchall()


@pytest.mark.integration
def test_all_products_projection_published_eligibility_and_provenance(metsig_release):
    fixture = metsig_release
    result = run(fixture)
    assert result["eligibility_policy"] == "published_entities"
    assert result["release_id"] == "2026.09"
    assert result["build_id"] == fixture["loaded"].manifest_sha256
    assert {name: stat["memberships"] for name, stat in result["resources"].items()} == {
        "Reactome": 2,
        "WikiPathways": 1,
        "KEGG": 2,
        "MACdb": 2,
        "ClassyFire": 3,
    }
    actual = rows(fixture, "SELECT DISTINCT metabolite_entity_id FROM {s}.metsigdb_membership")
    assert {row[0] for row in actual} == {
        fixture["chemical"]["entity_key"],
        fixture["fallback"]["entity_key"],
    }
    projected = rows(
        fixture,
        "SELECT DISTINCT metabolite_label, metabolite_entity_type, inchikey, metabolite_structure_key, hmdb, pubchem, chebi, kegg, smiles FROM {s}.metsigdb_membership WHERE metabolite_entity_id=%s",
        (fixture["chemical"]["entity_key"],),
    )
    assert projected == [
        (
            "Metabolite A",
            "chemical_entity",
            "AAAAAAAAAAAAAA-BBBBBBBBBB-C",
            "AAAAAAAAAAAAAA",
            "HMDB0000008",
            "123",
            "CHEBI:1",
            "C00001",
            "CCO",
        )
    ]
    # Canonical valid key wins over a different valid alias, and the connectivity
    # block must describe that same selected key. Invalid aliases remain in the
    # base table, but neither become the projection nor influence its block.
    assert rows(
        fixture,
        "SELECT DISTINCT inchikey, metabolite_structure_key FROM {s}.metsigdb_membership WHERE metabolite_entity_id=%s",
        (fixture["fallback"]["entity_key"],),
    ) == [("CCCCCCCCCCCCCC-DDDDDDDDDD-E", "CCCCCCCCCCCCCC")]
    assert (
        rows(
            fixture,
            "SELECT COUNT(*) FROM {s}.identifiers WHERE ns='inchikey' AND id='ZZ-invalid-key'",
        )[0][0]
        > 0
    )
    assert rows(fixture, "SELECT DISTINCT build_id FROM {s}.metsigdb_membership") == [
        (result["build_id"],)
    ]
    provenance = rows(
        fixture,
        "SELECT provenance_source, provenance_record FROM {s}.metsigdb_membership WHERE resource='Reactome' AND metabolite_entity_id=%s",
        (fixture["chemical"]["entity_key"],),
    )[0]
    assert provenance[0] == "parquet:reactome@1.0.0"
    assert provenance[1]["row_id"] == "2"
    assert provenance[1]["evidence_ordinal"] == 1
    assert provenance[1]["version"] == "1.0.0"


@pytest.mark.integration
def test_materialized_projection_preserves_complete_results_for_all_rules(metsig_release):
    fixture = metsig_release
    # Removing only the optimization gives the exact previous SELECT, including
    # all provenance, window counts, contexts and identifier expressions.
    publication_select = metsigdb_build._PUBLISH[
        metsigdb_build._PUBLISH.index("SELECT %(resource)s") :
    ]
    namespace = sql.Identifier(fixture["schema"])
    counts = {}
    with psycopg.connect(fixture["dsn"]) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        manifests = conn.execute(
            sql.SQL("SELECT manifest_json FROM {s}.resource_versions").format(s=namespace)
        ).fetchall()
        assert all(
            sum(item["rows"] for item in manifest["files"].values()) <= 20
            for (manifest,) in manifests
        )
        for rule in RESOURCES:
            selected_source = metsigdb_build._source_for_rule(rule)
            inline_source = selected_source.replace("projected AS MATERIALIZED (", "projected AS (")
            params = metsigdb_build._params(
                rule, fixture["loaded"].resources, fixture["loaded"].manifest_sha256
            )
            before = conn.execute(
                sql.SQL(inline_source + publication_select).format(s=namespace), params
            ).fetchall()
            after = conn.execute(
                sql.SQL(selected_source + publication_select).format(s=namespace), params
            ).fetchall()
            # A multiset comparison includes every returned column and retains
            # duplicate multiplicity; equal counts alone would miss regressions.
            assert Counter(json.dumps(row, sort_keys=True) for row in before) == Counter(
                json.dumps(row, sort_keys=True) for row in after
            ), rule.name
            counts[rule.name] = len(after)
    assert counts == {"Reactome": 2, "WikiPathways": 1, "KEGG": 2, "MACdb": 2, "ClassyFire": 3}


@pytest.mark.integration
def test_materialized_projection_aggregates_once_under_nested_loop_rescans(metsig_release):
    fixture = metsig_release
    namespace = sql.Identifier(fixture["schema"])
    params = metsigdb_build._params(
        RESOURCES[0], fixture["loaded"].resources, fixture["loaded"].manifest_sha256
    )
    # This correlated SELECT forces the projection to be rescanned for each
    # chosen membership. OFFSET 0 keeps the lateral join from being pulled up, so the
    # regression does not depend on the planner's preferred join order.
    probe = """
        SELECT c.set_source_id, p.* FROM chosen c
        CROSS JOIN LATERAL (
            SELECT * FROM complete_projection WHERE entity_id=c.metabolite_entity_id OFFSET 0
        ) p
    """

    def plan_nodes(node):
        yield node
        for child in node.get("Plans", ()):
            yield from plan_nodes(child)

    with psycopg.connect(fixture["dsn"]) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL enable_hashjoin = off")
        conn.execute("SET LOCAL enable_mergejoin = off")
        conn.execute("SET LOCAL enable_material = off")
        conn.execute("SET LOCAL enable_memoize = off")
        conn.execute("SET LOCAL max_parallel_workers_per_gather = 0")
        explain = "EXPLAIN (ANALYZE, VERBOSE, FORMAT JSON, COSTS OFF, TIMING OFF, SUMMARY OFF) "
        inline_source = metsigdb_build._SOURCE.replace(
            "projected AS MATERIALIZED (", "projected AS ("
        )
        before = conn.execute(
            sql.SQL(explain + inline_source + probe).format(s=namespace), params
        ).fetchone()[0][0]["Plan"]
        after = conn.execute(
            sql.SQL(explain + metsigdb_build._SOURCE + probe).format(s=namespace), params
        ).fetchone()[0][0]["Plan"]
    before_nodes = list(plan_nodes(before))
    after_nodes = list(plan_nodes(after))
    assert before["Node Type"] == after["Node Type"] == "Nested Loop"
    inline_aggregates = [
        node
        for node in before_nodes
        if node["Node Type"] == "Aggregate"
        and any("max(" in output.lower() for output in node.get("Output", ()))
    ]
    assert len(inline_aggregates) == 1, inline_aggregates
    assert inline_aggregates[0]["Actual Loops"] == 2
    producers = [node for node in after_nodes if node.get("Subplan Name") == "CTE projected"]
    assert len(producers) == 1
    assert producers[0]["Node Type"] == "Aggregate"
    assert producers[0]["Actual Loops"] == 1
    assert producers[0]["Actual Rows"] == 2
    scans = [node for node in after_nodes if node.get("CTE Name") == "projected"]
    assert len(scans) == 1
    assert scans[0]["Actual Loops"] == 2


@pytest.mark.integration
def test_kegg_only_inlining_preserves_complete_results_for_all_rules(metsig_release):
    fixture = metsig_release
    namespace = sql.Identifier(fixture["schema"])
    publication_select = metsigdb_build._PUBLISH[
        metsigdb_build._PUBLISH.index("SELECT %(resource)s") :
    ]
    counts = {}
    with psycopg.connect(fixture["dsn"]) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        manifests = conn.execute(
            sql.SQL("SELECT manifest_json FROM {s}.resource_versions").format(s=namespace)
        ).fetchall()
        assert all(
            sum(item["rows"] for item in manifest["files"].values()) <= 20
            for (manifest,) in manifests
        )
        for rule in RESOURCES:
            selected_source = metsigdb_build._source_for_rule(rule)
            if rule.extraction == "kegg":
                assert selected_source != metsigdb_build._SOURCE
            else:
                assert selected_source == metsigdb_build._SOURCE
            params = metsigdb_build._params(
                rule, fixture["loaded"].resources, fixture["loaded"].manifest_sha256
            )
            before = conn.execute(
                sql.SQL(metsigdb_build._SOURCE + publication_select).format(s=namespace), params
            ).fetchall()
            after = conn.execute(
                sql.SQL(selected_source + publication_select).format(s=namespace), params
            ).fetchall()
            assert Counter(json.dumps(row, sort_keys=True) for row in before) == Counter(
                json.dumps(row, sort_keys=True) for row in after
            ), rule.name
            counts[rule.name] = len(after)
    assert counts == {"Reactome": 2, "WikiPathways": 1, "KEGG": 2, "MACdb": 2, "ClassyFire": 3}


@pytest.mark.integration
def test_kegg_inlining_preserves_candidate_paths_and_chosen_provenance(tmp_path, postgres_dsn):
    first_chemical = entity("CHEBI:1", "chemical_entity", label="Chemical one")
    second_chemical = entity("CHEBI:2", "small_molecule", label="Chemical two")
    protein = entity("P00001", "protein", "uniprot", label="Excluded protein")
    first_reaction = entity("R00001", "molecular_activity", "kegg_reaction")
    second_reaction = entity("R00002", "molecular_activity", "kegg_reaction")
    first_pathway = entity("rn00001", "pathway", "kegg_pathway", label="First pathway")
    second_pathway = entity("rn00002", "pathway", "kegg_pathway", label="Second pathway")
    entities = [
        first_chemical,
        second_chemical,
        protein,
        first_reaction,
        second_reaction,
        first_pathway,
        second_pathway,
    ]
    # Both pathway orientations, multiple reactions and input/output paths yield
    # duplicate membership candidates with distinct, retained provenance.
    forward = relation(first_reaction, "part_of", first_pathway, source="kegg", row_id="101")
    reverse = relation(second_pathway, "has_part", first_reaction, source="kegg", row_id="102")
    other_path = relation(second_reaction, "part_of", first_pathway, source="kegg", row_id="103")
    chemical_input = relation(
        first_reaction,
        "has_input",
        first_chemical,
        source="kegg",
        row_id="10",
        annotations=source_context_annotations({}, source="kegg"),
    )
    chemical_output = relation(
        first_reaction,
        "has_output",
        first_chemical,
        source="kegg",
        row_id="2",
        annotations=source_context_annotations({}, source="kegg"),
    )
    participant = relation(
        first_reaction,
        "has_participant",
        second_chemical,
        source="kegg",
        row_id="3",
        annotations=source_context_annotations({}, source="kegg"),
    )
    other_output = relation(
        second_reaction,
        "has_output",
        first_chemical,
        source="kegg",
        row_id="4",
        annotations=source_context_annotations({}, source="kegg"),
    )
    excluded = relation(
        first_reaction,
        "has_input",
        protein,
        source="kegg",
        row_id="5",
        annotations=source_context_annotations({}, source="kegg"),
    )
    relations = [
        forward,
        reverse,
        other_path,
        chemical_input,
        chemical_output,
        participant,
        other_output,
        excluded,
    ]
    assert len(entities) + len(relations) == 15
    write_resource(tmp_path, "kegg", entities, relations)
    schema = "met_" + uuid.uuid4().hex
    loaded = loader.load_release(
        tmp_path, write_release(tmp_path, ["kegg"]), postgres_dsn, schema=schema
    )
    fixture = dict(dsn=postgres_dsn, schema=schema)
    namespace = sql.Identifier(schema)
    rule = next(rule for rule in RESOURCES if rule.extraction == "kegg")
    params = metsigdb_build._params(rule, loaded.resources, loaded.manifest_sha256)
    publication_select = metsigdb_build._PUBLISH[
        metsigdb_build._PUBLISH.index("SELECT %(resource)s") :
    ]
    with psycopg.connect(postgres_dsn) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        before = conn.execute(
            sql.SQL(metsigdb_build._SOURCE + "SELECT * FROM kegg_pairs").format(s=namespace), params
        ).fetchall()
        after = conn.execute(
            sql.SQL(metsigdb_build._source_for_rule(rule) + "SELECT * FROM kegg_pairs").format(
                s=namespace
            ),
            params,
        ).fetchall()
        before_publication = conn.execute(
            sql.SQL(metsigdb_build._SOURCE + publication_select).format(s=namespace), params
        ).fetchall()
        after_publication = conn.execute(
            sql.SQL(metsigdb_build._source_for_rule(rule) + publication_select).format(s=namespace),
            params,
        ).fetchall()
    first_key = first_chemical["entity_key"]
    second_key = second_chemical["entity_key"]
    first_set = first_pathway["entity_key"]
    second_set = second_pathway["entity_key"]
    first_reaction_key = first_reaction["entity_key"]
    expected = [
        (
            first_set,
            first_key,
            chemical_input["relation_key"],
            first_reaction_key,
            forward["relation_key"],
        ),
        (
            first_set,
            first_key,
            chemical_output["relation_key"],
            first_reaction_key,
            forward["relation_key"],
        ),
        (
            first_set,
            second_key,
            participant["relation_key"],
            first_reaction_key,
            forward["relation_key"],
        ),
        (
            second_set,
            first_key,
            chemical_input["relation_key"],
            first_reaction_key,
            reverse["relation_key"],
        ),
        (
            second_set,
            first_key,
            chemical_output["relation_key"],
            first_reaction_key,
            reverse["relation_key"],
        ),
        (
            second_set,
            second_key,
            participant["relation_key"],
            first_reaction_key,
            reverse["relation_key"],
        ),
        (
            first_set,
            first_key,
            other_output["relation_key"],
            second_reaction["entity_key"],
            other_path["relation_key"],
        ),
    ]
    assert Counter(before) == Counter(after) == Counter(expected)
    assert Counter((row[0], row[1]) for row in after) == {
        (first_set, first_key): 3,
        (second_set, first_key): 2,
        (first_set, second_key): 1,
        (second_set, second_key): 1,
    }
    assert Counter(json.dumps(row, sort_keys=True) for row in before_publication) == Counter(
        json.dumps(row, sort_keys=True) for row in after_publication
    )
    assert len(after_publication) == 4
    result = run(fixture)  # Exercise rebuild's query selection, not only private SQL constants.
    assert result["resources"]["KEGG"]["memberships"] == 4
    chosen = rows(
        fixture,
        """SELECT set_source_id, set_size, provenance_record FROM {s}.metsigdb_membership
            WHERE metabolite_entity_id=%s ORDER BY set_source_id""",
        (first_key,),
    )
    assert [(set_id, size) for set_id, size, _ in chosen] == [("rn00001", 2), ("rn00002", 2)]
    for (set_id, _, provenance), set_relation in zip(chosen, (forward, reverse), strict=True):
        assert provenance["row_id"] == "2", set_id
        assert provenance["relation_key"] == chemical_output["relation_key"]
        assert provenance["via_reaction"] == first_reaction_key
        assert provenance["via_relation"] == set_relation["relation_key"]


@pytest.mark.integration
def test_projection_uses_canonical_winner_and_all_pinned_aliases(metsig_release):
    fixture = metsig_release
    chemical_key = fixture["chemical"]["entity_key"]
    fallback_key = fixture["fallback"]["entity_key"]
    namespace = sql.Identifier(fixture["schema"])
    with psycopg.connect(fixture["dsn"]) as conn:
        # ChemOnt sorts before all membership sources. Its row is the canonical
        # winner even for products extracted from other resources.
        conn.execute(
            sql.SQL("""
                INSERT INTO {s}.entities
                SELECT 'chemont', version, entity_key, entity_type, namespace, identifier,
                    taxon, 'Cross-source canonical label', has_hierarchy, parent_count,
                    child_count, record_json
                FROM {s}.entities WHERE resource='hmdb' AND entity_key=%s
            """).format(s=namespace),
            (chemical_key,),
        )
        # Two identical raw aliases have distinct ordinals. Neither duplicates
        # nor pins without aliases may multiply memberships or set sizes.
        conn.execute(
            sql.SQL("""
                INSERT INTO {s}.identifiers
                    (resource, version, entity_key, ordinal, ns, id, is_canonical, source)
                VALUES ('chemont', '1.0.0', %s, 0, 'pubchem', '999999', false, 'fixture'),
                    ('chemont', '1.0.0', %s, 1, 'pubchem', '999999', false, 'fixture')
            """).format(s=namespace),
            (chemical_key, chemical_key),
        )
        conn.execute(
            sql.SQL("""
                INSERT INTO {s}.identifiers
                SELECT resource, version, entity_key,
                    (SELECT MAX(ordinal) + 1 FROM {s}.identifiers duplicate
                        WHERE duplicate.resource=i.resource AND duplicate.version=i.version
                            AND duplicate.entity_key=i.entity_key),
                    ns, id, is_canonical, source
                FROM {s}.identifiers i
                WHERE i.resource='hmdb' AND i.entity_key=%s AND i.is_canonical
                ORDER BY i.ordinal LIMIT 1
            """).format(s=namespace),
            (fallback_key,),
        )
        conn.execute(
            sql.SQL("""
                UPDATE {s}.identifiers SET is_canonical=true
                WHERE entity_key=%s AND ns='inchikey' AND id='ZZ-invalid-key'
            """).format(s=namespace),
            (fallback_key,),
        )
    result = run(fixture)
    assert {name: stat["memberships"] for name, stat in result["resources"].items()} == {
        "Reactome": 2,
        "WikiPathways": 1,
        "KEGG": 2,
        "MACdb": 2,
        "ClassyFire": 3,
    }
    assert rows(
        fixture,
        """SELECT DISTINCT metabolite_label, metabolite_entity_type, inchikey,
            metabolite_structure_key, hmdb, pubchem, chebi, kegg, smiles
            FROM {s}.metsigdb_membership WHERE metabolite_entity_id=%s""",
        (chemical_key,),
    ) == [
        (
            "Cross-source canonical label",
            "chemical_entity",
            "AAAAAAAAAAAAAA-BBBBBBBBBB-C",
            "AAAAAAAAAAAAAA",
            "HMDB0000008",
            "999999",
            "CHEBI:1",
            "C00001",
            "CCO",
        )
    ]
    assert rows(
        fixture,
        """SELECT DISTINCT inchikey, metabolite_structure_key
            FROM {s}.metsigdb_membership WHERE metabolite_entity_id=%s""",
        (fallback_key,),
    ) == [("CCCCCCCCCCCCCC-DDDDDDDDDD-E", "CCCCCCCCCCCCCC")]
    assert rows(
        fixture,
        """SELECT DISTINCT resource, set_size FROM {s}.metsigdb_membership
            WHERE resource <> 'ClassyFire' ORDER BY resource""",
    ) == [("KEGG", 2), ("MACdb", 2), ("Reactome", 2), ("WikiPathways", 1)]


@pytest.mark.integration
@pytest.mark.parametrize("remaining_namespace", [None, "name"])
def test_chemicals_without_projection_aliases_keep_memberships(metsig_release, remaining_namespace):
    fixture = metsig_release
    chemical_key = fixture["chemical"]["entity_key"]
    namespace = sql.Identifier(fixture["schema"])
    with psycopg.connect(fixture["dsn"]) as conn:
        conn.execute(
            sql.SQL("DELETE FROM {s}.identifiers WHERE entity_key=%s").format(s=namespace),
            (chemical_key,),
        )
        if remaining_namespace is not None:
            conn.execute(
                sql.SQL("""
                    INSERT INTO {s}.identifiers
                        (resource, version, entity_key, ordinal, ns, id, is_canonical, source)
                    VALUES ('hmdb', '1.0.0', %s, 0, %s, 'Ignored projection alias', false, 'fixture')
                """).format(s=namespace),
                (chemical_key, remaining_namespace),
            )
    run(fixture)
    assert rows(
        fixture,
        """SELECT DISTINCT resource FROM {s}.metsigdb_membership
            WHERE metabolite_entity_id=%s ORDER BY resource""",
        (chemical_key,),
    ) == [("ClassyFire",), ("KEGG",), ("MACdb",), ("Reactome",), ("WikiPathways",)]
    assert rows(
        fixture,
        """SELECT DISTINCT metabolite_label, metabolite_entity_type, inchikey,
            metabolite_structure_key, smiles, hmdb, pubchem, chebi, kegg
            FROM {s}.metsigdb_membership WHERE metabolite_entity_id=%s""",
        (chemical_key,),
    ) == [("Metabolite A", "chemical_entity", None, None, None, None, None, None, None)]


@pytest.mark.integration
def test_set_label_fallback_preserves_source_taxon_and_scoped_names(metsig_release):
    fixture = metsig_release
    namespace = sql.Identifier(fixture["schema"])
    with psycopg.connect(fixture["dsn"]) as conn:
        # The canonical fallback label comes from ChemOnt, but organism must
        # still come from the Reactome entity used by the membership relation.
        conn.execute(
            sql.SQL("""
                INSERT INTO {s}.entities
                SELECT 'chemont', version, entity_key, entity_type, namespace, identifier,
                    '10090', 'Canonical fallback pathway', has_hierarchy, parent_count,
                    child_count, record_json
                FROM {s}.entities WHERE resource='reactome' AND identifier='R-HSA-1'
            """).format(s=namespace)
        )
        conn.execute(
            sql.SQL("""
                UPDATE {s}.entities SET label=identifier
                WHERE resource='reactome' AND identifier='R-HSA-1'
                    OR resource='wikipathways' AND identifier='WP1'
            """).format(s=namespace)
        )
        # Both canonical rows have a label equal to the native identifier, so
        # WikiPathways must use its own smallest name alias. A smaller alias
        # from another pin must not participate in this set-label fallback.
        conn.execute(
            sql.SQL("""
                INSERT INTO {s}.entities
                SELECT 'chemont', version, entity_key, entity_type, namespace, identifier,
                    '9606', label, has_hierarchy, parent_count, child_count, record_json
                FROM {s}.entities WHERE resource='wikipathways' AND identifier='WP1'
            """).format(s=namespace)
        )
        conn.execute(
            sql.SQL("""
                INSERT INTO {s}.identifiers
                    (resource, version, entity_key, ordinal, ns, id, is_canonical, source)
                SELECT resource, version, entity_key, next_ordinal + name.ordinal,
                    'name', name.id, false, 'fixture'
                FROM (
                    SELECT e.resource, e.version, e.entity_key,
                        COALESCE((SELECT MAX(i.ordinal) + 1 FROM {s}.identifiers i
                            WHERE i.resource=e.resource AND i.version=e.version
                                AND i.entity_key=e.entity_key), 0) AS next_ordinal
                    FROM {s}.entities e
                    WHERE e.resource IN ('chemont', 'wikipathways') AND e.identifier='WP1'
                ) entity_names
                CROSS JOIN (VALUES (0, 'A scoped pathway name'),
                    (1, 'Z scoped pathway name')) AS name(ordinal, id)
            """).format(s=namespace)
        )
        conn.execute(
            sql.SQL("""
                UPDATE {s}.identifiers SET id='0 wrong-resource name'
                WHERE resource='chemont' AND ns='name'
            """).format(s=namespace)
        )
    run(fixture)
    assert rows(
        fixture,
        """SELECT DISTINCT resource, set_label, organism, set_size
            FROM {s}.metsigdb_membership
            WHERE resource IN ('Reactome', 'WikiPathways') ORDER BY resource""",
    ) == [
        ("Reactome", "Canonical fallback pathway", 9606, 2),
        ("WikiPathways", "A scoped pathway name", 10090, 1),
    ]


@pytest.mark.integration
def test_native_sets_taxon_and_structured_subtypes(metsig_release):
    fixture = metsig_release
    run(fixture)
    assert rows(
        fixture,
        "SELECT DISTINCT resource, set_source_id, set_label, set_sub_type, organism, set_size FROM {s}.metsigdb_membership WHERE resource <> 'ClassyFire' ORDER BY resource",
    ) == [
        ("KEGG", "rn01100", "Overview", "overview_map", None, 2),
        ("MACdb", "1", "Trait label", "cancer", None, 2),
        ("Reactome", "R-HSA-1", "Reactome pathway", None, 9606, 2),
        ("WikiPathways", "WP1", "Mouse pathway", None, 10090, 1),
    ]
    provenance = rows(
        fixture,
        "SELECT provenance_record FROM {s}.metsigdb_membership WHERE resource='KEGG' LIMIT 1",
    )[0][0]
    assert provenance["via_reaction"] == fixture["reaction"]["entity_key"]
    assert provenance["via_relation"]
    assert rows(
        fixture, "SELECT DISTINCT set_entity_id FROM {s}.metsigdb_membership WHERE resource='MACdb'"
    ) == [(fixture["trait"]["entity_key"],)]


@pytest.mark.integration
def test_classyfire_direct_wins_and_part_hierarchy_is_excluded(metsig_release):
    fixture = metsig_release
    run(fixture)
    actual = rows(
        fixture,
        "SELECT set_source_id, set_label, set_context, set_size FROM {s}.metsigdb_membership WHERE resource='ClassyFire' AND metabolite_entity_id=%s ORDER BY set_source_id",
        (fixture["chemical"]["entity_key"],),
    )
    assert actual == [
        ("CHEMONT:0001", "Superclass", {"assignment": "direct", "depth": 0}, 2),
        ("CHEMONT:0002", "Leaf class", {"assignment": "direct", "depth": 0}, 1),
    ]
    assert rows(
        fixture,
        "SELECT COUNT(*) FROM {s}.metsigdb_membership WHERE set_entity_id=%s",
        (fixture["part_only"]["entity_key"],),
    ) == [(0,)]


@pytest.mark.integration
def test_classyfire_ancestor_context_and_rebuild_replaces_disappeared_members(metsig_release):
    fixture = metsig_release
    run(fixture)
    with psycopg.connect(fixture["dsn"]) as conn:
        # Remove the direct root assignment, leaving its ancestor membership.
        conn.execute(
            sql.SQL(
                "DELETE FROM {s}.evidence WHERE resource='hmdb' AND relation_key IN (SELECT relation_key FROM {s}.relations WHERE resource='hmdb' AND subject_entity_key=%s AND object_entity_key=%s)"
            ).format(s=sql.Identifier(fixture["schema"])),
            (fixture["chemical"]["entity_key"], fixture["root"]["entity_key"]),
        )
        conn.execute(
            sql.SQL(
                "DELETE FROM {s}.relations WHERE resource='hmdb' AND subject_entity_key=%s AND object_entity_key=%s"
            ).format(s=sql.Identifier(fixture["schema"])),
            (fixture["chemical"]["entity_key"], fixture["root"]["entity_key"]),
        )
        result = rebuild(conn, fixture["schema"])
    assert result["resources"]["ClassyFire"]["memberships"] == 3
    assert rows(
        fixture,
        "SELECT set_context FROM {s}.metsigdb_membership WHERE resource='ClassyFire' AND set_source_id='CHEMONT:0001' AND metabolite_entity_id=%s",
        (fixture["chemical"]["entity_key"],),
    ) == [({"assignment": "ancestor", "depth": 1, "via": "CHEMONT:0002"},)]
    before = rows(
        fixture,
        "SELECT * FROM {s}.metsigdb_membership ORDER BY resource, set_source_id, metabolite_entity_id",
    )
    run(fixture)
    assert (
        rows(
            fixture,
            "SELECT * FROM {s}.metsigdb_membership ORDER BY resource, set_source_id, metabolite_entity_id",
        )
        == before
    )
    with psycopg.connect(fixture["dsn"]) as conn:
        conn.execute(
            sql.SQL("DELETE FROM {s}.evidence WHERE resource='wikipathways'").format(
                s=sql.Identifier(fixture["schema"])
            )
        )
        conn.execute(
            sql.SQL("DELETE FROM {s}.relations WHERE resource='wikipathways'").format(
                s=sql.Identifier(fixture["schema"])
            )
        )
        rebuild(conn, fixture["schema"])
    assert rows(
        fixture, "SELECT COUNT(*) FROM {s}.metsigdb_membership WHERE resource='WikiPathways'"
    ) == [(0,)]


@pytest.mark.integration
def test_missing_sources_reported_and_rebuild_stays_in_callers_transaction(tmp_path, postgres_dsn):
    chemical = entity("CHEBI:8")
    write_resource(tmp_path, "other", [chemical])
    schema = "met_" + uuid.uuid4().hex
    loader.load_release(tmp_path, write_release(tmp_path, ["other"]), postgres_dsn, schema=schema)
    with psycopg.connect(postgres_dsn) as conn:
        result = rebuild(conn, schema)
        assert all(stat["status"] == "skipped" for stat in result["resources"].values())
        conn.rollback()
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute(
            "SELECT to_regclass(%s)", (f"{schema}.metsigdb_membership",)
        ).fetchone() == (None,)


@pytest.mark.integration
def test_autocommit_without_transaction_is_rejected(metsig_release):
    fixture = metsig_release
    with psycopg.connect(fixture["dsn"], autocommit=True) as conn:
        with pytest.raises(ValueError, match="caller-owned transaction"):
            rebuild(conn, fixture["schema"])
