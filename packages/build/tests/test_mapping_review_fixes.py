"""Review regressions checked across parser, resolution and serving boundaries."""

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_build.canonical.library import build_library
from omnipath_resolver.canonical.identifiers import normalize_identifier, normalize_id_sql
from writer_fixture import write_observations
from omnipath_resolver import EntityResolver
from omnipath_build.silver import SilverExtractor
from omnipath_build.writer import ParquetWriter
from omnipath_core.measurements import quantity_dict
from omnipath_core.naming import Namespace
from omnipath_core.silver_schema import format_term
from pypath.inputs_v2 import cellchat, cellphonedb, chembl, rampdb, reactome, signor, swisslipids
from pypath.inputs_v2.base import ontology_term_to_entity
from pypath.inputs_v2.parsers import chembl as chembl_parser
from pypath.internals.ontology_schema import OntologyTerm
from library_fixture import write_hubs


def observations(records, source="review"):
    out = SilverExtractor(source, "test")
    for index, record in enumerate(records):
        out.process_record(record, {"index": index}, str(index), index)
    return out


def final(records, path, source="review"):
    out = observations(records, source)
    resolver = EntityResolver(library_dir=path / "absent")
    try:
        return write_observations(resolver, out.entities, out.relations, out.payloads)
    finally:
        resolver.close()


def test_reactome_opposite_controls_survive_separate_parquet_chunks(tmp_path):
    writer = ParquetWriter(tmp_path / "out")
    base = {
        "control_class": "Control",
        "controller_entity_type": "protein",
        "controller_uniprot": "P00533",
        "controlled_entity_type": "reaction",
        "controlled_reactome_stable_id": "R-HSA-123",
    }
    resolver = EntityResolver(library_dir=tmp_path / "absent")
    try:
        for effect in ("ACTIVATION", "INHIBITION"):
            extractor = observations([reactome.controls_schema({**base, "control_type": effect})])
            writer.append_observations(extractor, resolver)
    finally:
        resolver.close()
    writer.close()
    rows = pq.read_table(tmp_path / "out" / "relations.parquet").to_pylist()
    assert len(rows) == 2
    assert {row["sign"] for row in rows} == {-1, 1}
    assert len({row["relation_key"] for row in rows}) == 2


def test_signor_actual_nested_complex_members_are_not_protein_aliases():
    record = signor.complexes_schema(
        {
            "SIGNOR ID": "SIGNOR-C87",
            "COMPLEX NAME": "MLL/SET subcomplex",
            "LIST OF ENTITIES": "Q15291, Q9C005, Q9UPS6, SIGNOR-C352",
        }
    )
    assert len(record.membership) == 4
    assert [[(str(i.type), i.value) for i in m.member.identifiers] for m in record.membership] == [
        [("uniprot", "Q15291")],
        [("uniprot", "Q9C005")],
        [("uniprot", "Q9UPS6")],
        [("signor", "SIGNOR-C352")],
    ]
    assert [format_term(m.member.type) for m in record.membership] == ["protein"] * 3 + [
        "macromolecular_complex"
    ]
    assert len(observations([record]).relations) == 4
    family = signor.protein_families_schema(
        {"SIGNOR ID": "SIGNOR-PF1", "LIST OF ENTITIES": "SIGNOR-PF2,P00533"}
    )
    assert [format_term(m.member.type) for m in family.membership] == ["protein_family", "protein"]


def test_complex_composition_preserves_species_but_merges_sources(tmp_path):
    records = [
        cellchat._protein_group_entity(name="same complex", genes=["A", "B"], taxon_id=t)
        for t in ("9606", "10090")
    ]
    entities, _, _ = final(records, tmp_path, "cellchat")
    groups = [e for e in entities if e["entity_type"] == "macromolecular_complex"]
    assert len(groups) == 2
    assert {g["taxon"] for g in groups} == {"9606", "10090"}
    other, _, _ = final([records[0]], tmp_path, "another_resource")
    assert next(
        e["entity_key"] for e in other if e["entity_type"] == "macromolecular_complex"
    ) == next(g["entity_key"] for g in groups if g["taxon"] == "9606")


def test_cellphonedb_complex_reference_and_definition_join(tmp_path):
    interaction = cellphonedb.interactions_schema({"partner_a": "complex A", "partner_b": "P00533"})
    definition = cellphonedb.complexes_schema({"complex_name": "complex A", "uniprot_1": "P04637"})
    entities, _, _ = final([interaction, definition], tmp_path, "cellphonedb")
    assert len([e for e in entities if e["entity_type"] == "macromolecular_complex"]) == 1


def test_chembl_units_are_selected_from_source_and_reach_quantity():
    con = duckdb.connect()
    try:
        con.execute("CREATE SCHEMA s")
        con.execute(
            "CREATE TABLE s.activities(activity_id BIGINT, assay_id BIGINT, molregno BIGINT, doc_id BIGINT, standard_type VARCHAR, standard_relation VARCHAR, standard_value DOUBLE, standard_units VARCHAR, pchembl_value DOUBLE, data_validity_comment VARCHAR, action_type VARCHAR)"
        )
        con.execute(
            "INSERT INTO s.activities VALUES (1, 1, 1, 1, 'IC50', '=', 25, 'uM', 5.6, NULL, NULL)"
        )
        query = con.execute(chembl_parser.DUCKDB_TABLES["activities"])
        row = dict(zip([d[0] for d in query.description], query.fetchone()))
        assert row["standard_units"] == "uM"
        # Both output backends must select this staging field as well.
        assert "act.standard_units" in chembl_parser.PARQUET_QUERIES["activities"]
        row.update(
            molecule_chembl_id="CHEMBL1", target_chembl_id="CHEMBL2", target_type="SINGLE PROTEIN"
        )
        record = chembl.activities_schema(row)
        quantity = next(
            quantity_dict(a.value)
            for a in record.annotations
            if format_term(a.term) == "BAO:0000190"
        )
        assert quantity["has_unit"] == "uM"
        assert quantity["has_numeric_value"] == 25
        assert observations([record]).relations[-1].upstream_id == "1"
    finally:
        con.close()


def test_swisslipids_multiple_crossrefs_and_formula():
    record = swisslipids.lipids_schema(
        {
            "Lipid ID": "SLM:000000784",
            "CHEBI": "74546 | 82922",
            "LIPID MAPS": "LM1 | LM2",
            "Formula (pH7.3)": "C2H4",
        }
    )
    pairs = {(str(i.type), i.value) for i in record.identifiers}
    assert {
        ("chebi", "74546"),
        ("chebi", "82922"),
        ("lipidmaps", "LM1"),
        ("lipidmaps", "LM2"),
    } <= pairs
    assert ("has_chemical_formula", "C2H4") in {
        (format_term(a.term), a.value) for a in record.annotations
    }


def test_ramp_pathway_name_and_source_domain():
    record = rampdb.pathway_schema(
        {
            "pathwayRampId": "RAMP_P_000000001",
            "pathwayName": "1-Methylhistidine Metabolism",
            "sourceId": "SMP0124716",
            "type": "hmdb",
        }
    )
    assert ("name", "1-Methylhistidine Metabolism") in {
        (str(i.type), i.value) for i in record.identifiers
    }
    assert ("xref", "SMPDB:SMP0124716") in {
        (format_term(a.term), a.value) for a in record.annotations
    }


def test_ontology_imports_keep_their_own_namespace():
    term = OntologyTerm(id="MONDO:0000001", name="example", is_a=["BFO:0000001"])
    record = ontology_term_to_entity(term, ontology_id="mondo", identifier_type=Namespace.MONDO)
    assert str(record.ontology_relations[0].object.identifier_type) == "bfo"
    imported = ontology_term_to_entity(
        term._replace(id="BFO:0000001", is_a=[]),
        ontology_id="mondo",
        identifier_type=Namespace.MONDO,
    )
    assert str(imported.identifiers[0].type) == "bfo"


def test_ensembl_protein_and_transcript_lookup_from_built_library(tmp_path):
    hubs = tmp_path / "hubs"
    write_hubs(hubs)
    old = pq.read_table(hubs / "uniprot.parquet")
    extra = pa.Table.from_pylist(
        [
            {
                "source_type": ns,
                "source_id": ident,
                "hub_id": "P04637",
                "taxonomy_id": "9606",
                "backend": "uniprot",
            }
            for ns, ident in [
                ("ensp", "ENSP00000269305.4"),
                ("enst", "ENST00000269305.8"),
                ("refseq_protein", "NP_000537.3"),
            ]
        ],
        schema=old.schema,
    )
    pq.write_table(pa.concat_tables([old, extra]), hubs / "uniprot.parquet")
    library = tmp_path / "library"
    build_library(hubs, library)
    resolver = EntityResolver(library_dir=library)
    try:
        from omnipath_core.silver_schema import Entity, Identifier
        from biolink_model.datamodel.model import Protein

        records = [
            Entity(type=Protein, identifiers=[Identifier(type=ns, value=ident)])
            for ns, ident in [
                ("ensp", "ENSP00000269305"),
                ("ensembl", "ENSP00000269305.4"),
                ("enst", "ENST00000269305"),
                ("refseq_protein", "NP_000537.3"),
            ]
        ]
        out = observations(records)
        resolved = resolver.resolve_entities(out.entities, progress=False)
        assert len(resolved) == 4
        assert all(
            r.matched and (r.canonical_namespace, r.canonical_identifier) == ("entrez", "7157")
            for r in resolved.values()
        )
        assert all(r.entity_type == "protein" for r in out.entities.values())
        by_namespace = {out.entities[key].namespace: value for key, value in resolved.items()}
        for namespace in ("ensp", "ensembl", "refseq_protein"):
            product = by_namespace[namespace]
            assert (product.protein_namespace, product.protein_identifier) == ("uniprot", "P04637")
            assert product.protein_gene_candidates == ("entrez:7157",)
        transcript = by_namespace["enst"]
        assert transcript.protein_identifier is None
        assert (transcript.transcript_namespace, transcript.transcript_identifier) == (
            "enst",
            "ENST00000269305",
        )
    finally:
        resolver.close()


@pytest.mark.parametrize(
    "ns,value", [("ensg", "ENSG000001.3"), ("ensp", "ENSP000001.2"), ("enst", "ENSMUST000001.4")]
)
def test_ensembl_normalization_matches_library_sql(ns, value):
    con = duckdb.connect()
    try:
        normalized = con.execute(
            "SELECT " + normalize_id_sql("ns", "id") + " FROM (SELECT ? AS ns, ? AS id)",
            [ns, value],
        ).fetchone()[0]
        assert normalize_identifier(ns, value) == [(ns, normalized)]
    finally:
        con.close()
