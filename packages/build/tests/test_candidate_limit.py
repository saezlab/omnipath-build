import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_build.reference.compact_index import FORMAT, CompactWriter, atomic_json
from omnipath_build.reference.full_index import partition
from omnipath_resolver.index import FullRuntime
from omnipath_resolver.observations import CODES, key
from omnipath_build.reference.candidate_limit import apply_limit


@pytest.mark.parametrize("interrupted_kind", ["identifiers", "entities"])
def test_candidate_limit_archives_complete_sets_updates_inverse_and_resumes(
    tmp_path, monkeypatch, interrupted_kind
):
    index, reference = tmp_path / "index", tmp_path / "reference"
    index.mkdir()
    reference.mkdir()
    atomic_json(reference / "manifest.json", dict(fingerprint="limit-fixture"))
    dictionaries = {}
    for kind in ["entities", "identifiers"]:
        raw = b"entity identifier candidates uniprot"
        path = index / "dictionaries" / (kind + ".zstd")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        dictionaries[kind] = dict(
            file="dictionaries/" + kind + ".zstd",
            bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    contract = dict(
        format=FORMAT,
        reference_fingerprint="limit-fixture",
        dictionaries=dictionaries,
        checkpoint_layout="compiler",
        builder="direct-compact-v1",
    )
    atomic_json(index / "build-contract.json", contract)
    entities, facts, aliases = {}, [], []
    for n in range(12):
        eid = f"uniprot:P{n:05}"
        taxon = "9606" if n < 11 else "10090"
        ids = [["uniprot", eid.split(":")[1]], ["refseq_protein", "SHARED"], ["genesymbol", "MANY"]]
        if n < 10:
            ids.append(["genbank", "EXACT10"])
        if n == 0:
            ids.append(["genbank", "-"])
        entities[eid] = dict(
            record=dict(
                entity_id=eid, kind=2, anchor=eid, taxon=taxon, label="MANY", identifiers=ids
            ),
            meta=dict(id=n, entity_id=eid, kind=2, anchor=eid, quarantined=False, reviewed=True),
        )
        facts.append([n, eid, 2, eid, False, True])
        aliases.extend(dict(entity_id=eid, namespace=ns, identifier=ident) for ns, ident in ids)
    folder = reference / "gene_protein-entities"
    folder.mkdir()
    pq.write_table(pa.Table.from_pylist(aliases), folder / "identity_identifiers.parquet")
    mappings = [
        ("refseq_protein", "SHARED", "", facts),
        ("refseq_protein", "SHARED", "9606", facts[:11]),
        ("refseq_protein", "SHARED", "10090", facts[11:]),
        ("genesymbol", "MANY", "9606", facts[:11]),
        ("genesymbol", "MANY", "10090", facts[11:]),
        ("genbank", "EXACT10", "", facts[:10]),
        ("genbank", "-", "", facts[:1]),
    ]
    identifiers = [
        (
            key(2, 1, ns, scope, ident),
            ident,
            dict(candidates=cs, gene=ns == "genesymbol", products=False),
        )
        for ns, ident, scope, cs in mappings
    ]
    files = {}
    counts = {}
    for kind in ["entities", "identifiers"]:
        total = 0
        for n in range(256):
            part = f"{n:02x}"
            writer = CompactWriter(
                index / kind / part,
                kind,
                b"entity identifier candidates uniprot",
                audit_limit=10 if kind == "identifiers" else None,
            )
            rows = (
                [(eid.encode(), obj) for eid, obj in entities.items() if partition(eid) == part]
                if kind == "entities"
                else [(k, obj) for k, ident, obj in identifiers if partition(ident) == part]
            )
            writer.put_objects(rows)
            report = writer.close()
            total += report["records"]
            cp = "entity-checkpoints" if kind == "entities" else "identifiers-checkpoints"
            atomic_json(index / cp / (part + ".json"), report)
            files[f"{kind}/{part}/data.mdb"] = report["bytes"]
        counts[kind] = total
    atomic_json(
        index / "manifest.json",
        dict(contract, complete=True, namespace_codes=CODES, files=files, counts=counts),
    )
    # Interrupted after committing a partition and its index checkpoint, before
    # the policy checkpoint: resume must not double-subtract removals.
    from omnipath_build.reference import index_rewrite

    original_atomic = index_rewrite.atomic_json
    failed = False

    def interrupted(path, value):
        nonlocal failed
        if not failed and f"candidate-policy/checkpoints/{interrupted_kind}" in str(path):
            failed = True
            raise RuntimeError("simulated interruption")
        return original_atomic(path, value)

    monkeypatch.setattr(index_rewrite, "atomic_json", interrupted)
    with pytest.raises(RuntimeError, match="simulated"):
        apply_limit(index, reference)
    with pytest.raises(FileNotFoundError):
        FullRuntime(index)
    report = apply_limit(index, reference)
    assert report["removed_lookup_keys"] == 3
    assert report["changed_entity_records"] == 11
    assert apply_limit(index, reference) == report
    archived = pq.read_table(index / "ambiguous.parquet").to_pylist()
    assert sorted(r["candidate_count"] for r in archived) == [11, 11, 12]
    for row in archived:
        assert len(row["candidate_entity_ids"]) == row["candidate_count"]
        assert row["reason"] == "candidate_limit"
    runtime = FullRuntime(index)
    try:
        for ns, ident, scope, cs in mappings:
            actual = runtime.lookup(key(2, 1, ns, scope, ident))["candidates"]
            assert actual == ([] if len(cs) > 10 else cs)
        human = runtime.record(facts[0][1])
        assert ["refseq_protein", "SHARED"] not in human["identifiers"]
        assert ["genesymbol", "MANY"] not in human["identifiers"]
        assert ["genbank", "EXACT10"] in human["identifiers"]
        assert ["genbank", "-"] in human["identifiers"]  # No separate dash policy.
        assert human["label"] == "P00000"
        mouse = runtime.record(facts[11][1])
        assert ["refseq_protein", "SHARED"] in mouse["identifiers"]
        assert ["genesymbol", "MANY"] in mouse["identifiers"]
        assert mouse["label"] == "MANY"
    finally:
        runtime.close()
    manifest = json.loads((index / "manifest.json").read_text())
    assert manifest["candidate_limit"] == 10
    assert manifest["counts"]["identifiers"] == len(mappings) - 3
