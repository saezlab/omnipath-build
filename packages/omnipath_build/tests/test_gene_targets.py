"""Gene expansion preserves protein identities, relation occurrences and payloads."""

import pytest

from library_fixture import build_fixture_library
from omnipath_build.resolver import EntityResolver
from test_resolver import obs
from test_partition_contract import run_rows


@pytest.fixture
def library(tmp_path):
    return build_fixture_library(tmp_path / "reference")


def test_gene_targets_and_accession_identity(library):
    resolver = EntityResolver(library_dir=library, defer_aliases=True)
    try:
        observations = {
            "gene": obs("gene", "gene", "entrez", "7157"),
            "shared": obs("shared", "gene", "entrez", "801"),
            "shared2": obs("shared2", "gene", "entrez", "805"),
            "orphan": obs("orphan", "gene", "entrez", "55"),
            "protein": obs("protein", "protein", "uniprot", "A0A0U1RQF1"),
            "secondary": obs("secondary", "protein", "uniprot", "Q15086"),
        }
        targets = resolver.resolve_entity_targets(observations)

        def ids(key):
            return {i.canonical_identifier for i in targets[key]}

        assert ids("gene") == {"P04637"}
        assert ids("protein") == {"A0A0U1RQF1"}
        assert ids("secondary") == {"P04637"}
        assert ids("shared") == ids("shared2") == {"P0DP23"}
        assert ids("orphan") == {"55"}
        assert targets["orphan"][0].canonical_namespace == "entrez"
        assert all(t.entity_type == "protein" for t in targets["gene"])
        assert resolver.resolution_stats()["resolved_entities"] == len(observations)
        assert resolver.resolution_stats()["input_entities"] == len(observations)
    finally:
        resolver.close()


def entity(kind, ns, value):
    return {"type": kind, "identifiers": [{"type": ns, "value": value}]}


def test_writer_expansion_is_partition_invariant(library, tmp_path):
    gene = entity("gene", "entrez", "7157")
    other_gene = entity("gene", "entrez", "999")
    phenotype = entity("ontology_class", "hpo", "HP:0000001")
    rows = [
        (
            "hpo",
            {
                "subject": gene,
                "predicate": "associated_with",
                "object": phenotype,
                "annotations": [{"term": "description", "value": "gene evidence"}],
            },
        ),
        ("network", {"subject": gene, "predicate": "affects", "object": other_gene}),
        (
            "protein",
            {
                "subject": entity("protein", "uniprot", "P04637"),
                "predicate": "associated_with",
                "object": phenotype,
            },
        ),
    ]
    expected = run_rows(tmp_path / "all", rows, 3, library)
    assert run_rows(tmp_path / "split", rows[::-1], 1, library) == expected
    entities, relations, payloads = expected
    assert {e["identifier"] for e in entities if e["entity_type"] == "protein"} == {
        "P04637",
        "P11111",
        "P22222",
    }
    assert not any(e["entity_type"] == "gene" for e in entities)
    assert len(relations) == 3
    assert len(payloads) == 4
    assert sum(r["evidence_count"] for r in relations) == 4
    for r in relations:
        for ev in r["evidence"]:
            if ev["row_id"] == "hpo":
                assert len(ev["annotations"]) == 1
    for e in entities:
        if e["identifier"] == "P04637":
            assert not any(
                i["ns"] == "uniprot" and i["id"] == "A0A0U1RQF1" for i in e["identifiers"]
            )


def test_symmetric_expansion_deduplicates_each_occurrence(library, tmp_path):
    gene = entity("gene", "entrez", "999")
    rows = [
        (
            "symmetric",
            {
                "subject": gene,
                "predicate": "interacts_with",
                "object": gene,
                "annotations": [{"term": "description", "value": "once"}],
            },
        )
    ]
    entities, relations, payloads = run_rows(tmp_path / "symmetric", rows, 1, library)
    assert len(entities) == 2
    assert len(relations) == 3  # P1/P1, P1/P2, P2/P2; reverse pair is the same statement.
    assert len(payloads) == 3
    assert all(r["evidence_count"] == 1 for r in relations)
    assert all(len(r["evidence"][0]["annotations"]) == 1 for r in relations)


def test_incomplete_compact_library_requires_rebuild(library):
    (library / "identifiers/00/data.mdb").unlink()
    with pytest.raises(FileNotFoundError):
        EntityResolver(library_dir=library)


def test_shared_protein_and_unmapped_gene_keep_all_evidence(library, tmp_path):
    phenotype = entity("ontology_class", "hpo", "HP:0000001")
    rows = [
        (
            gene,
            {
                "subject": entity("gene", "entrez", gene),
                "predicate": "associated_with",
                "object": phenotype,
            },
        )
        for gene in ["801", "805", "55"]
    ]
    entities, relations, payloads = run_rows(tmp_path / "shared", rows, 3, library)
    assert {(e["entity_type"], e["identifier"]) for e in entities} == {
        ("protein", "P0DP23"),
        ("gene", "55"),
        ("ontology_class", "HP:0000001"),
    }
    assert sorted(r["evidence_count"] for r in relations) == [1, 2]
    assert len(payloads) == 3


def test_gene_identifier_prefers_reviewed_product(library):
    resolver = EntityResolver(library_dir=library)
    try:
        targets = resolver.resolve_entity_targets({"p": obs("p", "protein", "entrez", "7157")})["p"]
        assert len(targets) == 1
        assert targets[0].matched
        assert targets[0].canonical_identifier == "P04637"
    finally:
        resolver.close()


def test_gene_falls_back_to_unreviewed_only_without_reviewed_mapping(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from library_fixture import write_hubs
    from omnipath_build.canonical import build_library

    hubs = tmp_path / "hubs"
    write_hubs(hubs)
    table = pq.read_table(hubs / "uniprot.parquet")
    rows = table.to_pylist()
    for row in rows:
        if row["hub_id"] == "P04637" and row["source_type"] == "uniprot_entry":
            row["source_id"] = "P04637_HUMAN"  # Make both gene 7157 entries unreviewed.
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), hubs / "uniprot.parquet")
    library = tmp_path / "library"
    build_library(hubs, library)
    resolver = EntityResolver(library)
    try:
        targets = resolver.resolve_entity_targets({"g": obs("g", "gene", "entrez", "7157")})["g"]
        assert {t.canonical_identifier for t in targets} == {"P04637", "A0A0U1RQF1"}
    finally:
        resolver.close()
