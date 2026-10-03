import gzip
import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.reference.prepare_sequence_mappings import build


def test_sequence_columns_and_versions(tmp_path):
    fields = [""] * 22
    fields[0], fields[1], fields[3], fields[12], fields[16], fields[17] = (
        "P12345",
        "TEST_HUMAN",
        "NP_001.2; XP_002.3",
        "9606",
        "NUCLEOTIDE",
        "ABC12345.6",
    )
    selected = tmp_path / "selected.tsv.gz"
    with gzip.open(selected, "wt") as f:
        f.write("\t".join(fields) + "\n")
    base = tmp_path / "base.parquet"
    pq.write_table(
        pa.table(
            {
                k: pa.array([], type=pa.string())
                for k in ["source_type", "source_id", "hub_id", "taxonomy_id", "backend"]
            }
        ),
        base,
    )
    build(selected, base, tmp_path / "out", 1, "256MB")
    rows = pq.read_table(tmp_path / "out/uniprot.parquet").to_pylist()
    pairs = {(r["source_type"], r["source_id"]) for r in rows}
    assert ("refseq_protein", "NP_001.2") in pairs
    assert ("refseq_protein", "XP_002.3") in pairs
    assert ("genbank", "ABC12345.6") in pairs
    assert ("uniprot", "P12345") in pairs
    assert not any(r["source_id"] == "NUCLEOTIDE" for r in rows)
