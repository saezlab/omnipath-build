from pypath.inputs_v2.wikipathways import map_interaction
from omnipath_build.silver import SilverExtractor


def test_native_gene_uri_is_not_overruled_by_enriched_protein_list():
    row = dict(
        source_uri="https://identifiers.org/ncbigene/20778",
        source_entrez="20778",
        source_uniprot="D3Z5U8;F7C5U2;Q61009",
        source_entity_type="protein",
        source_label="Scarb1",
        target_uri="https://identifiers.org/hmdb/HMDB0000067",
        target_hmdb="HMDB0000067",
        target_pubchem_compound="5997",
        target_entity_type="chemical",
        target_label="Cholesterol",
        taxon_id="10090",
        interaction_types="DirectedInteraction;Interaction",
        interaction_local_id="edge1",
        pathway_term_accession="WP1",
    )
    ex = SilverExtractor("wikipathways", "interactions")
    ex.process_record(map_interaction(row), row, "interactions:0", 0)
    protein = next(o for o in ex.entities.values() if o.entity_type == "protein")
    assert protein.namespace == "entrez" and protein.identifier == "20778"
    assert row["source_uniprot"] == "D3Z5U8;F7C5U2;Q61009"
    assert all(x["ns"] != "uniprot" for x in protein.identifiers)


def test_replay_scopes_ids_corroborating_a_taxon_qualified_symbol(tmp_path):
    import json
    import pyarrow.parquet as pq
    from omnipath_build.reference.replay_resources import extract

    row = {
        "taxon_id": "10090",
        "source_entity_type": "protein",
        "source_uri": "urn:test:source",
        "source_hgnc": "WNT1",
        "source_uniprot": "P04628",
        "source_label": "WNT1",
        "target_entity_type": "protein",
        "target_uri": "urn:test:target",
        "target_hgnc": "Wnt3a",
        "target_uniprot": "P12669",
        "target_label": "Wnt3a",
    }
    extract(("wikipathways", 0, [("interactions:0", json.dumps(row))], str(tmp_path)))
    votes = pq.read_table(tmp_path / "v-0.parquet").to_pylist()
    protein_ids = [v for v in votes if v["ns"] == "uniprot"]
    assert protein_ids
    assert {v["scope"] for v in protein_ids} == {"10090"}


def test_symbol_without_taxon_is_not_a_lookup_vote():
    from types import SimpleNamespace
    from omnipath_resolver.canonical.match import votes_for
    from omnipath_resolver.canonical.policy import get_policy

    obs = SimpleNamespace(namespace="genesymbol", identifier="WNT1", identifiers=[], taxon=None)
    votes, _ = votes_for(obs, get_policy("protein"))
    assert votes == []
