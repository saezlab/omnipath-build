"""The existing five MetSigDB products over small published, resolved releases."""

from copy import deepcopy
import json
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres import loader
from omnipath_subsets.metsigdb import rebuild
from omnipath_subsets.metsigdb.mapping import RESOURCES, KEGG_OVERVIEW_MAPS
from release_fixture import entity, payload, relation, write_release, write_resource


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
    trait = entity("1", "ontology_class", "macdb_trait", label="Trait label")
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
            relation(reaction_entity, "has_input", chemical, source="kegg", row_id="2"),
            relation(reaction_entity, "has_output", fallback, source="kegg", row_id="3"),
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
            relation(chemical, "associated_with", trait, source="macdb", row_id="1"),
            relation(trait, "associated_with", fallback, source="macdb", row_id="2"),
        ],
        [payload(trait, {"Trait_Type": "cancer"}, source="macdb")],
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
