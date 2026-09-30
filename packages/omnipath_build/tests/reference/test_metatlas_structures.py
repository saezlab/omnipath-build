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
