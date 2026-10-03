import json

import pyarrow.parquet as pq

from omnipath_build.reference.replay_resources import extract


def test_replay_connectomedb_resource_alias(tmp_path):
    """The cached resource slug differs from its inputs_v2 module name."""
    row = {
        "Species": "human",
        "Ligand Symbols": "TGFB1",
        "Ligand Species ID": "HGNC:11766",
        "Ligand ENSEMBL ID": "ENSG00000105329",
        "Receptor Symbols": "TGFBR1",
        "Receptor Species ID": "HGNC:11772",
        "Receptor ENSEMBL ID": "ENSG00000106799",
        "Interaction ID": "CDB25:1",
        "LR Pair": "TGFB1-TGFBR1",
    }
    records, queries = extract(
        ("connectomedb2025", 0, [("interactions:1", json.dumps(row))], tmp_path)
    )
    assert records == 1
    assert queries == 2
    observations = pq.read_table(tmp_path / "o-0.parquet").to_pylist()
    assert {o["source"] for o in observations} == {"connectomedb2025"}
    assert {o["entity_type"] for o in observations} == {"protein"}
    votes = pq.read_table(tmp_path / "v-0.parquet").to_pylist()
    assert {v["identifier"] for v in votes if v["ns"] == "hgnc"} == {"HGNC:11766", "HGNC:11772"}
