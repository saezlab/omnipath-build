"""Supported network recipes retain source evidence and report semantic gaps."""

from copy import deepcopy
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.loader import load_release
from omnipath_subsets import network_views
from omnipath_subsets.network_views._query import _binary_record, _explicit_roles, _transports
from omnipath_core.source_attributes import PARTICIPANT_ROLE
from release_fixture import (
    annotation,
    entity,
    payload,
    relation,
    source_context_annotations,
    write_release,
    write_resource,
)


def test_mechanism_selection_keeps_quantities_duplicates_and_qualifiers():
    chemical = entity("CHEBI:1")
    target = entity("P1", "protein", "uniprot", taxon="9606")
    quantity = dict(
        has_numeric_value=12.5,
        has_unit="UO:0000061",
        has_unit_prefix="nano",
        has_binary_relation="less_than",
        source_field="IC50",
        comparator="<=",
    )
    qualified = annotation(
        "object_direction_qualifier", "decreased", source="chembl", dataset="activities"
    )
    measurement = annotation(
        "BAO:0000190", quantity=quantity, source="chembl", dataset="mechanisms"
    )
    row = relation(
        chemical,
        "affects",
        target,
        source="chembl",
        dataset="mechanisms",
        annotations=[qualified, measurement],
    )
    row["evidence"].append(
        dict(source="assay-only", dataset="activities", row_id="2", upstream_id="a", annotations=[])
    )
    row["evidence"].append(deepcopy(row["evidence"][0]))
    row["evidence_count"] = 3
    row["sources"].append("assay-only")
    result = _binary_record(
        ("chembl", "1.0.0", row["relation_key"], row, chemical, target, []), "metalinksdb"
    )
    assert result["statement"]["evidence_count"] == 2
    assert result["statement"]["evidence"][0] == result["statement"]["evidence"][1]
    assert result["statement"]["sources"] == ["chembl"]
    assert result["statement"]["annotations"] == [qualified, measurement]
    assert result["statement"]["annotations"][1]["quantity"] == quantity
    assert result["evidence_ordinals"] == [0, 2]
    assert "payloads" not in result
    assert row["evidence_count"] == 3  # Adapter selection does not mutate the publication.


def test_liana_orientation_requires_complete_unambiguous_roles_per_evidence():
    ligand = entity("P1", "protein", "uniprot", aliases=[("genesymbol", "LIG"), ("hgnc", "1")])
    receptor = entity("P2", "protein", "uniprot", aliases=[("genesymbol", "REC"), ("hgnc", "2")])
    roles_data = [
        dict(evidence_ordinal=0, scope="subject", value="receptor"),
        dict(evidence_ordinal=0, scope="object", value="ligand"),
    ]
    roles = _explicit_roles(receptor, ligand, roles_data)
    assert roles == {
        "ligand_entity_id": ligand["entity_key"],
        "receptor_entity_id": receptor["entity_key"],
        "evidence_ordinals": [0],
    }
    assert _explicit_roles(ligand, receptor, []) is None
    assert _explicit_roles(ligand, receptor, roles_data[:1]) is None
    # Complementary annotations in different evidence rows never form a pair.
    split = [roles_data[0], dict(roles_data[1], evidence_ordinal=1)]
    assert _explicit_roles(receptor, ligand, split) is None
    opposite = [
        dict(evidence_ordinal=1, scope="subject", value="ligand"),
        dict(evidence_ordinal=1, scope="object", value="receptor"),
    ]
    assert _explicit_roles(receptor, ligand, [*roles_data, *opposite]) is None
    ambiguous = [*roles_data, dict(evidence_ordinal=0, scope="subject", value="ligand")]
    assert _explicit_roles(receptor, ligand, ambiguous) is None
    assert _explicit_roles(receptor, ligand, roles_data * 2) == roles


def test_transport_requires_explicit_catalysis_and_two_known_compartments():
    event = entity("RX1", "molecular_activity", "rhea")
    chemical = entity("CHEBI:1")
    enzyme = entity("P1", "protein", "uniprot")
    context = dict(context_id="context-1", resource="metatlas", version="1.0.0")
    members = []
    for ordinal, (role, predicate, participant, compartment) in enumerate(
        (
            ("enzyme", "enabled_by", enzyme, None),
            ("reactant", "has_input", chemical, "cytosol"),
            ("product", "has_output", chemical, "extracellular"),
        )
    ):
        statement = relation(event, predicate, participant)
        members.append(
            dict(
                member=dict(
                    role=role,
                    relation_key=statement["relation_key"],
                    participant_entity_id=participant["entity_key"],
                    ordinal=ordinal,
                    compartment=compartment,
                    evidence_ordinal=0,
                ),
                entity=participant,
                statement=statement,
            )
        )
    results = list(_transports(context, event, members))
    assert len(results) == 1
    assert results[0]["supporting_participants"] == members
    assert results[0]["compartment_from"] == "cytosol"
    duplicate = deepcopy(members[1])
    duplicate["member"]["ordinal"] = 3
    duplicated = list(_transports(context, event, [*members, duplicate]))
    assert len(duplicated) == 1
    assert len(duplicated[0]["supporting_participants"]) == 4
    missing = deepcopy(members)
    missing[2]["member"]["compartment"] = None
    assert list(_transports(context, event, missing)) == []
    gpr = deepcopy(members)
    gpr[0]["statement"]["predicate"] = "associated_with"
    assert list(_transports(context, event, gpr)) == []
    same = deepcopy(members)
    same[2]["member"]["compartment"] = "cytosol "
    assert list(_transports(context, event, same)) == []


@pytest.mark.integration
def test_registry_and_liana_query_preserve_statement_scope_and_transaction(tmp_path, postgres_dsn):
    a = entity("P1", "protein", "uniprot", taxon="9606", aliases=[("genesymbol", "LIG")])
    b = entity("P2", "protein", "uniprot", taxon="9606", aliases=[("genesymbol", "REC")])
    first = relation(
        b,
        "interacts_with",
        a,
        source="connectomedb2025",
        row_id="1",
        annotations=[
            annotation(PARTICIPANT_ROLE, "receptor", scope="subject", source="connectomedb2025"),
            annotation(PARTICIPANT_ROLE, "ligand", scope="object", source="connectomedb2025"),
        ],
    )
    second = relation(a, "affects", b, source="connectomedb2025", row_id="2")
    raw = {"Ligand Symbols": "LIG", "Receptor Symbols": "REC"}
    write_resource(
        tmp_path,
        "connectomedb2025",
        [a, b],
        [first, second],
        [payload(first, raw, source="connectomedb2025")],
    )
    schema = "network_" + uuid.uuid4().hex
    load_release(
        tmp_path, write_release(tmp_path, ["connectomedb2025"]), postgres_dsn, schema=schema
    )
    # Neither the product adapter nor queries can read any published artifacts.
    (tmp_path / "resources").rename(tmp_path / "unavailable_resources")
    with psycopg.connect(postgres_dsn) as conn:
        stats = network_views.rebuild(conn, schema)
        assert stats["registered"] == ["metalinksdb", "liana", "reactions"]
        assert stats["missing_resources"]["liana"] == []
        assert network_views.rebuild(conn, schema)["presets"] == 3
        result = network_views.query(conn, schema, "liana", organism="NCBITaxon:9606")
        assert result["source_records"] == 1
        assert result["records"][0]["relation_key"] == first["relation_key"]
        source_record = result["records"][0]["resource_records"][0]
        assert source_record["statement"] == first
        assert source_record["roles"]["ligand_entity_id"] == a["entity_key"]
        assert source_record["roles"]["receptor_entity_id"] == b["entity_key"]
        assert source_record["roles"]["evidence_ordinals"] == [0]
        assert "payloads" not in source_record
        assert conn.execute("SELECT to_regclass(%s)", (schema + ".payloads",)).fetchone() == (None,)
        assert result["limitations"]
        assert network_views.query(conn, schema, "liana", organism="10090")["records"] == []
        columns = conn.execute(
            sql.SQL(
                "SELECT schema_name, combined_relation, composition FROM {}.network_registry WHERE name='metalinksdb'"
            ).format(sql.Identifier(schema))
        ).fetchone()
        assert columns[:2] == (None, None)
        assert [step["operation"] for step in columns[2]["steps"]] == [
            "exclude",
            "collapse",
            "annotate",
        ]
        conn.rollback()
        assert conn.execute(
            "SELECT to_regclass(%s)", (schema + ".network_registry",)
        ).fetchone() == (None,)


@pytest.mark.integration
def test_liana_roles_ignore_global_annotations_and_never_pool_evidence(tmp_path, postgres_dsn):
    source = "connectomedb2025"
    ligand = entity(
        "P1", "protein", "uniprot", annotations=[annotation(PARTICIPANT_ROLE, "ligand")]
    )
    receptor = entity(
        "P2", "protein", "uniprot", annotations=[annotation(PARTICIPANT_ROLE, "receptor")]
    )
    roles = [
        annotation(PARTICIPANT_ROLE, "ligand", scope="subject", source=source),
        annotation(PARTICIPANT_ROLE, "receptor", scope="object", source=source),
    ]
    statements = []
    for row_id, aspect in enumerate(("activity", "abundance", "activity_or_abundance"), 1):
        item = relation(
            ligand,
            "interacts_with",
            receptor,
            source=source,
            row_id=str(row_id),
            annotations=[annotation("object_aspect_qualifier", aspect), *roles],
        )
        statements.append(item)
    # Relation-level and global entity annotations are not evidence of a role pair.
    statements[0]["evidence"][0]["annotations"] = []
    # Two incomplete occurrences cannot complement each other.
    statements[1]["evidence"][0]["annotations"] = roles[:1]
    partial = deepcopy(statements[1]["evidence"][0])
    partial.update(row_id="4", annotations=roles[1:])
    statements[1]["evidence"].append(partial)
    statements[1]["evidence_count"] = 2
    # Two complete but opposing occurrences are ambiguous.
    opposite = deepcopy(statements[2]["evidence"][0])
    opposite.update(
        row_id="5", annotations=[dict(roles[0], value="receptor"), dict(roles[1], value="ligand")]
    )
    statements[2]["evidence"].append(opposite)
    statements[2]["evidence_count"] = 2
    write_resource(tmp_path, source, [ligand, receptor], statements)
    schema = "network_" + uuid.uuid4().hex
    load_release(tmp_path, write_release(tmp_path, [source]), postgres_dsn, schema=schema)
    (tmp_path / "resources").rename(tmp_path / "unavailable_resources")
    with psycopg.connect(postgres_dsn) as conn:
        records = list(network_views.iter_records(conn, schema, "liana"))
        assert len(records) == 3
        assert all(record["roles"] is None for record in records)
        assert all("payloads" not in record for record in records)


@pytest.mark.integration
def test_metalinks_queries_only_curated_mechanisms_and_preserves_qualified_pairs(
    tmp_path, postgres_dsn
):
    chemical = entity("CHEBI:1")
    target = entity("P1", "protein", "uniprot", taxon="9606")
    q = annotation("object_direction_qualifier", "decreased", source="chembl", dataset="mechanisms")
    mechanism = relation(
        chemical, "affects", target, source="chembl", dataset="mechanisms", annotations=[q], sign=-1
    )
    assay = deepcopy(mechanism["evidence"][0])
    assay.update(dataset="activities", row_id="2", annotations=[])
    mechanism["evidence"].append(assay)
    mechanism["evidence_count"] = 2
    opposite_q = annotation(
        "object_direction_qualifier", "increased", source="chembl", dataset="mechanisms"
    )
    opposite = relation(
        chemical,
        "affects",
        target,
        source="chembl",
        dataset="mechanisms",
        annotations=[opposite_q],
        row_id="3",
        sign=1,
    )
    write_resource(tmp_path, "chembl", [chemical, target], [mechanism, opposite])
    excluded = relation(chemical, "interacts_with", target, source="bindingdb")
    write_resource(tmp_path, "bindingdb", [chemical, target], [excluded])
    schema = "network_" + uuid.uuid4().hex
    load_release(
        tmp_path, write_release(tmp_path, ["chembl", "bindingdb"]), postgres_dsn, schema=schema
    )
    # Empty reaction context has the shared derivation's real DDL, not mock query rows.
    from omnipath_postgres.reactions import rebuild_reactions

    with psycopg.connect(postgres_dsn) as conn:
        rebuild_reactions(conn, schema)
        network_views.rebuild(conn, schema)
        records = list(network_views.iter_records(conn, schema, "metalinksdb"))
        assert {record["relation_key"] for record in records} == {
            mechanism["relation_key"],
            opposite["relation_key"],
        }
        assert all(record["resource"] == "chembl" for record in records)
        assert all(record["statement"]["evidence_count"] == 1 for record in records)
        result = network_views.query(conn, schema, "metalinksdb", limit=1)
        assert len(result["records"]) == 1 and result["has_more"]
        assert result["records"][0]["evidence_count"] == 1
        assert network_views.query(conn, schema, "metalinksdb", offset=1)["source_records"] == 1


@pytest.mark.integration
def test_reactions_query_and_transport_respect_each_source_context(tmp_path, postgres_dsn):
    from omnipath_postgres.reactions import rebuild_reactions

    cargo = entity("CHEBI:1", aliases=[("human_gem_metabolite", "x")])
    enzyme = entity("P1", "protein", "uniprot")
    gene = entity("1", "gene", "entrez")
    event = entity("RX1", "molecular_activity", "human_gem_reaction")
    statements, payloads = [], []
    for row_id, catalyst_predicate, catalyst in (
        ("reactions:1", "enabled_by", enzyme),
        ("reactions:2", "associated_with", gene),
    ):
        raw = {"reactants": "x:c:1", "products": "x:e:2", "direction": "REVERSIBLE"}
        for ordinal, (predicate, participant) in enumerate(
            (
                ("has_input", cargo),
                ("has_output", cargo),
                (catalyst_predicate, catalyst),
            )
        ):
            item = relation(
                event,
                predicate,
                participant,
                source="metatlas",
                dataset="reactions",
                row_id=row_id,
                upstream_id=f"{row_id}:member:{ordinal}",
                annotations=source_context_annotations(
                    raw,
                    source="metatlas",
                    dataset="reactions",
                    compartment="c" if ordinal == 0 else "e" if ordinal == 1 else None,
                ),
            )
            previous = next(
                (record for record in statements if record["relation_key"] == item["relation_key"]),
                None,
            )
            if previous is None:
                statements.append(item)
            else:
                previous["evidence"].extend(item["evidence"])
                previous["evidence_count"] += 1
            payloads.append(payload(item, raw, source="metatlas", row_id=row_id))
    write_resource(tmp_path, "metatlas", [cargo, enzyme, gene, event], statements, payloads)
    schema = "network_" + uuid.uuid4().hex
    load_release(tmp_path, write_release(tmp_path, ["metatlas"]), postgres_dsn, schema=schema)
    (tmp_path / "resources").rename(tmp_path / "unavailable_resources")
    with psycopg.connect(postgres_dsn) as conn:
        rebuild_reactions(conn, schema)
        network_views.rebuild(conn, schema)
        reactions = network_views.query(conn, schema, "reactions")
        assert reactions["source_records"] == 2
        assert all("payload_json" not in record["context"] for record in reactions["records"])
        assert {record["context"]["row_id"] for record in reactions["records"]} == {
            "reactions:1",
            "reactions:2",
        }
        assert sorted(len(record["participants"]) for record in reactions["records"]) == [2, 3]
        transport = network_views.query(conn, schema, "metalinksdb")["records"]
        assert len(transport) == 1
        assert transport[0]["context"]["row_id"] == "reactions:1"
        assert transport[0]["transporter"]["entity_key"] == enzyme["entity_key"]
        assert transport[0]["compartment_from"] == "c"
        assert transport[0]["compartment_to"] == "e"
        assert {member["member"]["role"] for member in transport[0]["supporting_participants"]} == {
            "enzyme",
            "reactant",
            "product",
        }
        assert all(
            member["statement"]["relation_key"] == member["member"]["relation_key"]
            for member in transport[0]["supporting_participants"]
        )
        assert all(
            member["member"]["evidence_ordinal"] == 0
            for member in transport[0]["supporting_participants"]
        )
