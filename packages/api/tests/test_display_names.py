import duckdb
import pytest
from omnipath_api.shape.display_name import name_rank, name_rank_sql, preferred_name
from omnipath_api.shape.entity import shape_entity_summary
from omnipath_api.entity_details import page_details


@pytest.mark.parametrize(
    "name",
    [
        "Alanine",
        "alanine-d7",
        "L-Alanine",
        "ALA",
        "CID_1234",
        "https://example.org",
        "InChI=123",
        "Long " * 30,
        "Ala",
        "ABCD",
    ],
)
def test_sql_and_python_preferred_name_ranking_agree(name):
    with duckdb.connect() as db:
        rank = db.execute(
            f"SELECT {name_rank_sql('name')} FROM (SELECT ? AS name)", [name]
        ).fetchone()[0]
    assert (rank["score"], rank["length"], rank["name"]) == name_rank(name)


def test_server_name_survives_identifier_pagination():
    entity = shape_entity_summary(
        dict(
            entity_key="a",
            entity_type="chemical_entity",
            namespace="inchikey",
            identifier="QNAYBMKLOCPYGJ-UQEXSWPGSA-N",
            label="alanine-d7",
            identifiers=[dict(ns="name", id=f"Long chemical name {i}") for i in range(25)]
            + [dict(ns="name", id="Alanine")],
        )
    )
    first = page_details(entity)
    assert first["displayName"] == "Alanine"
    assert first["identifiersNextCursor"] == "20"
    assert page_details(entity, offset=20)["displayName"] == "Alanine"
    assert preferred_name(["Alanine", "alanine-d7", "CID_1234"]) == "Alanine"


def test_source_name_columns_are_isolated_and_rebuildable(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from omnipath_api.engine import ParquetServingEngine
    from omnipath_api.name_index import build_indexes, lookup_names
    from omnipath_api.serving_index import index_path
    from test_connectivity import write_source, chemical

    first = chemical("same", "QNAYBMKLOCPYGJ-UQEXSWPGSA-N", label="alanine-d7")
    second = chemical("same", "QNAYBMKLOCPYGJ-UQEXSWPGSA-N", label="Alanine")
    write_source(tmp_path, "one", [first])
    write_source(tmp_path, "two", [second])
    engine = ParquetServingEngine(tmp_path)
    paths = [str(tmp_path / f"resources/{source}/1/entities.parquet") for source in ["one", "two"]]
    originals = [open(path, "rb").read() for path in paths]
    build_indexes(engine)
    for path, expected in zip(paths, ["alanine-d7", "Alanine"]):
        projection = index_path(tmp_path, "entity_labels", [path])
        assert pq.read_table(projection).to_pylist() == [{"entity_key": "same", "label": expected}]
        assert lookup_names(engine, [path], ["same"]) == {"same": expected}
    assert [open(path, "rb").read() for path in paths] == originals
    assert lookup_names(engine, paths, ["same"]) == {"same": "Alanine"}
    # A changed source cannot reuse its old projection.
    pq.write_table(pa.Table.from_pylist([dict(first, label="New name")]), paths[0])
    assert not index_path(tmp_path, "entity_labels", [paths[0]]).exists()
    assert lookup_names(engine, [paths[0]], ["same"]) == {"same": "New name"}
