"""Bounded, resolved reaction fixtures exercise the migrated COSMOS contract."""

import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres import loader
from omnipath_postgres.reactions import rebuild_reactions
from omnipath_subsets.cosmos import project_context, published_label, rebuild
from omnipath_core.source_attributes import SOURCE_RECORD_REFERENCE, SOURCE_RECORD_SHA256_PREFIX
from release_fixture import (
    annotation,
    entity,
    payload,
    relation,
    source_context_annotations,
    write_release,
    write_resource,
)


def party(key="chem", role="reactant", compartment="c", **kwargs):
    return dict(
        entity_id=key,
        entity_type="small_molecule",
        namespace="chebi",
        identifier="123",
        role=role,
        compartment=compartment,
        aliases=[],
        **kwargs,
    )


def context(**kwargs):
    return dict(
        context_id="ctx",
        reaction_entity_id="activity:key",
        identifier="RX1",
        namespace="rhea",
        direction="reversible",
        transport=False,
        sources=["rhea"],
        **kwargs,
    )


def test_projection_reversible_orphan_and_explicit_catalyst():
    parties = [party(), dict(party(), entity_id="product", identifier="456", role="product")]
    edges = list(project_context(context(), parties, "release", 3))
    assert len(edges) == 6
    assert all(edge["orphan"] and edge["mor"] == 1 for edge in edges)
    assert {(edge["source_label"], edge["target_label"]) for edge in edges} == {
        ("Metab__CHEBI:123_c", "Gene3__orphanReacRX1"),
        ("Gene3__orphanReacRX1", "Metab__CHEBI:456_c"),
        ("RX1", "Gene3__orphanReacRX1"),
        ("Gene3__orphanReacRX1_rev", "Metab__CHEBI:123_c"),
        ("Metab__CHEBI:456_c", "Gene3__orphanReacRX1_rev"),
        ("RX1", "Gene3__orphanReacRX1_rev"),
    }
    assert all(
        edge["source_entity_id"] is None or edge["target_entity_id"] is None for edge in edges
    )
    enzyme = dict(
        party(),
        entity_id="enzyme",
        entity_type="protein",
        namespace="uniprot",
        identifier="P12345",
        role="enzyme",
        compartment=None,
    )
    enzymes = list(
        project_context(dict(context(), direction=None), parties + [enzyme], "release", 8)
    )
    assert len(enzymes) == 3
    assert not any(edge["orphan"] or edge["reverse"] for edge in enzymes)
    assert enzymes[-1]["source_label"] == "P12345"
    assert enzymes[-1]["target_label"] == "Gene8__P12345"


def test_alias_projection_only_uses_unambiguous_published_identifiers():
    raw = dict(
        entity_id="key",
        namespace="inchikey",
        identifier="CANONICAL",
        aliases=[dict(ns="chebi", id="42", ambiguous=False)],
    )
    assert published_label(raw, "chebi").identifier == "CHEBI:42"
    assert published_label(raw, "chebi").status == "mapped"
    assert (
        published_label(
            dict(raw, aliases=raw["aliases"] + [dict(ns="chebi", id="43")]), "chebi"
        ).status
        == "ambiguous"
    )
    assert (
        published_label(
            dict(raw, aliases=[dict(ns="chebi", id="42", ambiguous=True)]), "chebi"
        ).identifier
        == "CANONICAL"
    )
    assert published_label(dict(raw, aliases=[]), "chebi").namespace == "inchikey"
    assert (
        published_label(dict(raw, namespace="chebi", identifier="CHEBI:42"), "chebi").identifier
        == "CHEBI:42"
    )
    assert (
        published_label(dict(raw, namespace="entrez", identifier="123"), "uniprot").namespace
        == "entrez"
    )


def fixture(root):
    molecule = entity("123", aliases=[("bigg_metabolite", "x")])
    catalyst = entity("P12345", "protein", "uniprot")
    event = entity("RX1", "molecular_activity", "rhea")
    gene = entity("1", "gene", "entrez")
    # Same canonical event in three source rows. Row3 GPR is associated_with,
    # never enabled_by. Sources deliberately disagree about compartments and
    # direction, so a source-pooled reaction would fail this fixture.
    relations = []
    payloads = []
    for row_id, compartment, direction, enzyme in (
        ("reactions:1", "c", "REVERSIBLE", True),
        ("reactions:2", "m", "LEFT-TO-RIGHT", False),
        ("reactions:3", "n", None, False),
    ):
        raw = dict(reactants=f"x:{compartment}:1", products="x:e:2", direction=direction)
        for ordinal, (predicate, obj) in enumerate(
            (
                ("has_input", molecule),
                ("has_output", molecule),
                ("enabled_by" if enzyme else "associated_with", catalyst if enzyme else gene),
            )
        ):
            item = relation(
                event,
                predicate,
                obj,
                source="recon3d",
                dataset="reactions",
                row_id=row_id,
                upstream_id=f"{row_id}:member:{ordinal}",
                annotations=[
                    *source_context_annotations(
                        raw,
                        source="recon3d",
                        dataset="reactions",
                        compartment=compartment if ordinal == 0 else "e" if ordinal == 1 else None,
                    ),
                    *(
                        [annotation("stoichiometry", str(ordinal + 1), source="recon3d")]
                        if ordinal < 2
                        else []
                    ),
                ],
            )
            duplicate = next(
                (
                    existing
                    for existing in relations
                    if existing["relation_key"] == item["relation_key"]
                ),
                None,
            )
            if duplicate:
                duplicate["evidence"].extend(item["evidence"])
                duplicate["evidence_count"] += 1
            else:
                relations.append(item)
            payloads.append(payload(item, raw, source="recon3d", row_id=row_id))
    write_resource(root, "recon3d", [molecule, catalyst, event, gene], relations, payloads)
    return write_release(root, ["recon3d"]), event, molecule, catalyst, gene


@pytest.mark.integration
def test_rebuild_preserves_event_scopes_unknown_enzymes_and_transaction(tmp_path, postgres_dsn):
    manifest, event, molecule, catalyst, gene = fixture(tmp_path)
    schema = "cosmos_" + uuid.uuid4().hex
    loader.load_release(tmp_path, manifest, postgres_dsn, schema=schema)
    (tmp_path / "resources").rename(tmp_path / "unavailable_resources")
    namespace = sql.Identifier(schema)
    with psycopg.connect(postgres_dsn) as conn:
        stats = rebuild_reactions(conn, schema)
        assert stats["contexts"] == 3 and stats["participants"] == 7  # GPR excluded
        contexts = conn.execute(
            sql.SQL(
                "SELECT row_id,direction,transport FROM {}.reaction_context ORDER BY row_id"
            ).format(namespace)
        ).fetchall()
        assert contexts == [
            ("reactions:1", "reversible", True),
            ("reactions:2", "left_to_right", True),
            ("reactions:3", None, True),
        ]
        compartments = conn.execute(
            sql.SQL(
                "SELECT c.row_id,p.role,p.compartment FROM {s}.reaction_participant p JOIN {s}.reaction_context c USING(context_id) ORDER BY c.row_id,p.role"
            ).format(s=namespace)
        ).fetchall()
        assert compartments == [
            ("reactions:1", "enzyme", None),
            ("reactions:1", "product", "e"),
            ("reactions:1", "reactant", "c"),
            ("reactions:2", "product", "e"),
            ("reactions:2", "reactant", "m"),
            ("reactions:3", "product", "e"),
            ("reactions:3", "reactant", "n"),
        ]
        first = rebuild(conn, schema)
        assert first["reactions"] == 3 and first["orphan_reactions"] == 2
        assert first["edges"] == 12 and first["connectors"] == 4 and first["reverse_edges"] == 3
        assert first["gene_nodes"] == 4 and first["metabolite_nodes"] == 4
        assert (
            first["identifier_translation"] is False
            and first["identifier_translation_method"] == "published_aliases"
        )
        query = sql.SQL("SELECT {} FROM {}.cosmos_edge ORDER BY cosmos_edge_id").format(
            sql.SQL(",").join(
                map(
                    sql.Identifier,
                    (
                        "source_label",
                        "target_label",
                        "source_entity_id",
                        "target_entity_id",
                        "interaction_id",
                        "direction",
                    ),
                )
            ),
            namespace,
        )
        before = conn.execute(query).fetchall()
        assert all(gene["entity_key"] not in row for row in before)
        assert any(catalyst["entity_key"] in row for row in before)
        assert not conn.execute(
            sql.SQL("SELECT entity_key FROM {}.entities WHERE entity_key LIKE 'Gene%%'").format(
                namespace
            )
        ).fetchall()
        rebuild_reactions(conn, schema)
        second = rebuild(conn, schema)
        assert first.keys() == second.keys() and {
            k: v for k, v in first.items() if k != "seconds"
        } == {k: v for k, v in second.items() if k != "seconds"}
        assert conn.execute(query).fetchall() == before
        conn.rollback()
    with psycopg.connect(postgres_dsn) as conn:
        # Helpers never commit DDL or rows independently.
        assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.cosmos_edge",)).fetchone() == (
            None,
        )


@pytest.mark.integration
def test_alias_ambiguity_annotation_context_and_ontology_membership_gate(tmp_path, postgres_dsn):
    source = "rhea"
    first = entity("AAAA", "small_molecule", "inchikey", aliases=[("chebi", "42")])
    second = entity("BBBB", "small_molecule", "inchikey", aliases=[("chebi", "42")])
    product = entity("43")
    event = entity("RX2", "molecular_activity", "rhea")
    raw = dict(
        participant_role="reactant||reactant||product",
        participant_chebi="CHEBI:42||CHEBI:42||CHEBI:43",
        participant_compartment="c||c||e",
        direction="UNKNOWN",
    )
    quantity = dict(
        has_numeric_value=1.25,
        has_unit="UO:0000000",
        source_field="source_coefficient",
        comparator="<=",
        has_unit_prefix="milli",
        has_binary_relation="less_than",
    )
    annotation_value = annotation("stoichiometry", "1.25", source=source, quantity=quantity)
    relations = [
        relation(
            event,
            "has_input",
            first,
            source=source,
            row_id="reactions:1",
            upstream_id="reactions:1:member:0",
            annotations=[
                annotation_value,
                *source_context_annotations(
                    raw,
                    source=source,
                    compartment="c",
                ),
            ],
        ),
        relation(
            event,
            "has_input",
            second,
            source=source,
            row_id="reactions:1",
            upstream_id="reactions:1:member:1",
            annotations=source_context_annotations(raw, source=source, compartment="c"),
        ),
        relation(
            event,
            "has_output",
            product,
            source=source,
            row_id="reactions:1",
            upstream_id="reactions:1:member:2",
            annotations=source_context_annotations(raw, source=source, compartment="e"),
        ),
    ]
    ontology = relation(
        event, "has_input", product, source=source, row_id="ontology:1", statement_kind="ontology"
    )
    raw_payloads = [payload(item, raw, source=source, row_id="reactions:1") for item in relations]
    write_resource(
        tmp_path, source, [first, second, product, event], relations + [ontology], raw_payloads
    )
    schema = "cosmos_" + uuid.uuid4().hex
    loaded = loader.load_release(
        tmp_path, write_release(tmp_path, [source]), postgres_dsn, schema=schema
    )
    (tmp_path / "resources").rename(tmp_path / "unavailable_resources")
    namespace = sql.Identifier(schema)
    with psycopg.connect(postgres_dsn) as conn:
        assert rebuild_reactions(conn, schema)["participants"] == 3
        stored = conn.execute(
            sql.SQL(
                "SELECT stoichiometry FROM {}.reaction_participant WHERE stoichiometry IS NOT NULL"
            ).format(namespace)
        ).fetchone()[0]
        assert stored == {"annotations": [annotation_value]}
        context_row = conn.execute(
            sql.SQL(
                "SELECT direction,raw_direction,diagnostics,source_record_sha256 FROM {}.reaction_context"
            ).format(namespace)
        ).fetchone()
        assert context_row[:3] == (None, "UNKNOWN", ["unsupported_direction_assertion"])
        expected_hash = source_context_annotations(raw)[0]["value"].removeprefix(
            SOURCE_RECORD_SHA256_PREFIX
        )
        assert context_row[3] == expected_hash
        assert conn.execute("SELECT to_regclass(%s)", (schema + ".payloads",)).fetchone() == (None,)
        stats = rebuild(conn, schema)
        assert stats["build_id"] == loaded.manifest_sha256
        assert stats["release_id"] == "2026.09"
        assert stats["chemical_labels_ambiguous"] == 2 and stats["chemical_labels_fallback"] == 2
        assert stats["chemical_labels_translated"] == 1 and stats["chemical_labels_mapped"] == 0
        assert stats["reverse_edges"] == 0
        labels = conn.execute(
            sql.SQL("SELECT source_label,target_label FROM {}.cosmos_edge").format(namespace)
        ).fetchall()
        assert any("Metab__AAAA_c" in row for row in labels)
        assert any("Metab__BBBB_c" in row for row in labels)
        assert not any("Metab__CHEBI:42_c" in row for row in labels)
        assert all(
            row == ("protein", "protein")
            for row in conn.execute(
                sql.SQL(
                    "SELECT source_type,target_type FROM {}.cosmos_edge WHERE interaction_type='connector'"
                ).format(namespace)
            ).fetchall()
        )


@pytest.mark.integration
def test_missing_row_ids_are_not_combined_and_unapproved_sources_stay_out(tmp_path, postgres_dsn):
    source = "reactome"
    molecule = entity("42")
    product = entity("43")
    event = entity("RX3", "molecular_activity", "reactome")
    relations = [
        relation(
            event,
            "has_input",
            molecule,
            source=source,
            row_id=None,
            annotations=source_context_annotations({}, source=source),
        ),
        relation(
            event,
            "has_output",
            product,
            source=source,
            row_id=None,
            annotations=source_context_annotations({}, source=source),
        ),
    ]
    write_resource(tmp_path, source, [molecule, product, event], relations)
    schema = "cosmos_" + uuid.uuid4().hex
    loader.load_release(tmp_path, write_release(tmp_path, [source]), postgres_dsn, schema=schema)
    with psycopg.connect(postgres_dsn) as conn:
        stats = rebuild_reactions(conn, schema)
        assert stats == dict(contexts=2, participants=2, contexts_with_diagnostics=2)
        assert rebuild(conn, schema)["edges"] == 0


@pytest.mark.integration
def test_conflicting_source_record_hashes_fail_atomically(tmp_path, postgres_dsn):
    manifest, *_ = fixture(tmp_path)
    schema = "cosmos_" + uuid.uuid4().hex
    loader.load_release(tmp_path, manifest, postgres_dsn, schema=schema)
    namespace = sql.Identifier(schema)
    with psycopg.connect(postgres_dsn) as conn:
        rebuild_reactions(conn, schema)
        rebuild(conn, schema)
    with psycopg.connect(postgres_dsn) as conn:
        expected = conn.execute(
            sql.SQL("SELECT COUNT(*) FROM {}.reaction_context").format(namespace)
        ).fetchone()
        with pytest.raises(ValueError, match="Conflicting source record"):
            with conn.transaction():
                conn.execute(
                    sql.SQL("""UPDATE {s}.annotations SET value=%s
                        WHERE annotation_id=(SELECT MIN(annotation_id) FROM {s}.annotations
                            WHERE owner_kind='evidence' AND term=%s)""").format(s=namespace),
                    (SOURCE_RECORD_SHA256_PREFIX + "0" * 64, SOURCE_RECORD_REFERENCE),
                )
                rebuild_reactions(conn, schema)
        assert (
            conn.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.reaction_context").format(namespace)
            ).fetchone()
            == expected
        )
        assert rebuild(conn, schema)["edges"] == 12
