from pypath.inputs_v2 import metatlas
from pypath.inputs_v2.parsers.metatlas import _metabolite_xref_row
from omnipath_core.naming import Namespace
import json
import pyarrow.parquet as pq
from omnipath_build.reference.replay_resources import extract
from pypath.inputs_v2.parsers.metatlas import _metabolite_xrefs


def test_native_smiles_column_and_cached_member_enrichment(monkeypatch):
    assert _metabolite_xref_row({"metSmiles": "C[C@H](O)C(=O)O"})["smiles"] == "C[C@H](O)C(=O)O"
    monkeypatch.setattr(metatlas, "_metabolite_xrefs", lambda: {"MAM1": {"smiles": "CCO"}})
    row = {"human_gem_metabolite_id": "MAM1", "name": "ethanol"}
    entity = metatlas._map_with_structures(metatlas.metabolites_schema, row)
    assert any(i.type == Namespace.SMILES and i.value == "CCO" for i in entity.identifiers)
    assert "smiles" not in row
    reaction = metatlas._map_with_structures(
        metatlas.reactions_schema,
        {"human_gem_reaction_id": "R1", "reactants": "MAM1:c:1", "products": "MAM2:c:1"},
    )
    member = reaction.membership[0].member
    assert any(i.type == Namespace.SMILES and i.value == "CCO" for i in member.identifiers)


def test_cached_replay_uses_selected_auxiliary_structure(tmp_path, monkeypatch):
    auxiliary = tmp_path / "metabolites.tsv"
    auxiliary.write_text("metsNoComp\tmetSmiles\nMAM1\tCCO\n")
    monkeypatch.setenv("OMNIPATH_HUMAN_GEM_XREFS", str(auxiliary))
    monkeypatch.setenv("PYPATH_DOWNLOAD_DATADIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("OMNIPATH_STRUCTURE_CACHE", str(tmp_path / "structures"))
    _metabolite_xrefs.cache_clear()
    try:
        extract(
            (
                "metatlas",
                0,
                [
                    (
                        "metabolites:0",
                        json.dumps({"human_gem_metabolite_id": "MAM1", "name": "ethanol"}),
                    )
                ],
                str(tmp_path),
            )
        )
        assert (
            pq.read_table(tmp_path / "d-0.parquet").to_pylist()[0]["inchikey"]
            == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
        )
    finally:
        _metabolite_xrefs.cache_clear()


def test_human_gem_reaction_and_metabolite_cross_references():
    import io

    from pypath.inputs_v2.parsers.metatlas import _parse_reaction_xrefs

    xrefs = _parse_reaction_xrefs(
        io.StringIO(
            "rxns\trxnKEGGID\trxnBiGGID\trxnMetaNetXID\trxnRheaID\trxnRheaMasterID\n"
            "MAR03905\tR00754\tALCD2x\tMNXR95725\tRHEA:25291\tRHEA:25290\n"
            "MAR03907\tR00746\t\t\tRHEA:12345\t\n"
        )
    )
    # The Rhea master is the identity; a directional ID only when no master is given.
    assert xrefs["MAR03905"]["rhea"] == "25290"
    assert xrefs["MAR03907"] == {"rhea": "12345", "kegg_reaction": "R00746"}
    metabolite = _metabolite_xref_row(
        {"metKEGGID": "C00964", "metBiGGID": "carveol", "metMetaNetXID": "MNXM1371224"}
    )
    assert (metabolite["kegg_compound"], metabolite["bigg"], metabolite["metanetx"]) == (
        "C00964",
        "carveol",
        "MNXM1371224",
    )
    reaction = metatlas.reactions_schema(
        {
            "human_gem_reaction_id": "MAR03905",
            "reactants": "MAM1:c:1",
            "products": "MAM2:c:1",
            "reactant_kegg_compound": "C00964",
            **xrefs["MAR03905"],
        }
    )
    assert {(str(i.type), i.value) for i in reaction.identifiers} >= {
        ("rhea", "25290"),
        ("kegg_reaction", "R00754"),
        ("bigg_reaction", "ALCD2x"),
        ("metanetx_reaction", "MNXR95725"),
    }
    member = reaction.membership[0].member
    assert ("kegg", "C00964") in {(str(i.type), i.value) for i in member.identifiers}


def test_single_protein_chembl_target_carries_its_accession():
    from pypath.inputs_v2 import chembl

    row = {
        "chembl_id": "CHEMBL2799",
        "pref_name": "Sodium-dependent dopamine transporter",
        "target_type": "SINGLE PROTEIN",
        "tax_id": "10090",
        "component_uniprot_accessions": "Q61327",
        "component_types": "PROTEIN",
    }
    single = {(str(i.type), i.value) for i in chembl.targets_schema(row).identifiers}
    assert ("uniprot", "Q61327") in single
    complex_ = chembl.targets_schema(
        dict(
            row,
            target_type="PROTEIN COMPLEX",
            component_uniprot_accessions="P1,P2",
            component_types="PROTEIN,PROTEIN",
        )
    )
    assert "uniprot" not in {str(i.type) for i in complex_.identifiers}
