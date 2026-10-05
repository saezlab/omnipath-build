"""Supported genes survive unrelated product fanout without product equivalence."""

from contextlib import closing
import json
import functools
import os
import shutil
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_resolver.canonical.match import LibraryMatcher
from omnipath_build.extract.observations import RawEntityObservation
from omnipath_build.provenance import reference_provenance
from omnipath_build.reference.full_index import FORMAT, Writer, partition
from omnipath_resolver.index import FullRuntime
from omnipath_build.reference.gene_role_index import (
    NAME,
    PARTS,
    build_gene_role_index,
    component_identity,
)
from omnipath_build.reference.replay_resources import CODES, key


def fixture(root):
    """Independent copy of one fixture reference built once per test process.

    Writing the 256 LMDB partitions costs seconds; tests that corrupt, resume or
    rebuild the index mutate only their own copy.
    """
    _, template_reference, template_base = _fixture_template()
    reference, base = root / "assigned", root / "base"
    shutil.copytree(template_reference, reference)
    shutil.copytree(template_base, base)
    return reference, base


@functools.lru_cache(maxsize=1)
def _fixture_template():
    directory = tempfile.TemporaryDirectory(prefix="gene-role-fixture-")
    return (directory, *_build_fixture(Path(directory.name)))


def _build_fixture(root):
    base, reference = root / "base", root / "assigned"
    base.mkdir(parents=True)
    reference.mkdir()
    genes = (
        [("1", "9606"), ("2", "10090"), ("3", "9606")]
        + [(str(i), "9606") for i in range(4, 15)]
        + [("15", None)]
    )
    records = {}
    for num, (gene, taxon) in enumerate(genes, 1):
        eid = "entrez:" + gene
        records[eid] = dict(
            record=dict(
                entity_id=eid,
                kind=3,
                anchor=None,
                taxon=taxon,
                label=gene,
                identifiers=[["entrez", gene]],
                gene_ids=[],
            ),
            meta=dict(
                id=30 - num, entity_id=eid, kind=3, anchor=None, quarantined=False, reviewed=False
            ),
        )
    product = "uniprot:P00001"
    records[product] = dict(
        record=dict(
            entity_id=product,
            kind=2,
            anchor=product,
            taxon="9606",
            label="Original product",
            identifiers=[["uniprot", "P00001"]],
            gene_ids=["1"],
        ),
        meta=dict(
            id=100, entity_id=product, kind=2, anchor=product, quarantined=False, reviewed=True
        ),
    )
    chemical = "inchikey:AAAAAAAAAAAAAA-BBBBBBBBBB-C"
    records[chemical] = dict(
        record=dict(
            entity_id=chemical,
            kind=1,
            anchor=chemical,
            taxon=None,
            label="Original chemical",
            identifiers=[["inchikey", chemical[9:]]],
            gene_ids=[],
        ),
        meta=dict(
            id=200, entity_id=chemical, kind=1, anchor=chemical, quarantined=False, reviewed=False
        ),
    )
    ident = {
        key(2, 1, "uniprot", "", "P00001"): dict(
            gene=False, products=False, candidates=[[100, product, 2, product, False, True, ["1"]]]
        ),
        key(1, 1, "inchikey", "", chemical[9:]): dict(
            gene=False, products=False, candidates=[[200, chemical, 1, chemical, False, False, []]]
        ),
    }
    files = {}
    for kind, rows in [
        ("entities", {k.encode(): v for k, v in records.items()}),
        ("identifiers", ident),
    ]:
        for part in PARTS:
            writer = Writer(base / kind / part)
            selected = [
                (k, json.dumps(v).encode())
                for k, v in rows.items()
                if partition(k.decode() if kind == "entities" else k[6:].decode()) == part
            ]
            writer.put(selected)
            cp = writer.close()
            files[f"{kind}/{part}/data.mdb"] = cp["bytes"]
    (base / "manifest.json").write_text(
        json.dumps(
            dict(
                format=FORMAT,
                complete=True,
                reference_fingerprint="fixture",
                namespace_codes=CODES,
                files=files,
            )
        )
    )
    (reference / "manifest.json").write_text(
        json.dumps(dict(status="complete", fingerprint="fixture"))
    )

    def parquet(relative, rows, schema):
        path = reference / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(schema)), path)

    claims, links = [], []
    for n in range(1, 36):
        eid = f"uniprot:P{n:05d}"
        claims.extend(
            [
                dict(entity_id=eid, namespace="genesymbol", identifier="GENE1"),
                dict(entity_id=eid, namespace="ensg", identifier="ENSG000001"),
            ]
        )
        if n <= 23:
            links.append(dict(protein_entity_id=eid, entrez_id="1", taxon="9606"))
    for eid, gene, taxon, ns, text in [
        ("uniprot:P20001", "1", "9606", "genesymbol", "MULTI"),
        ("uniprot:P20002", "3", "9606", "genesymbol", "MULTI"),
        ("uniprot:P20003", "2", "10090", "genesymbol", "GENE1"),
        ("uniprot:P20004", "2", "9606", "genesymbol", "CROSS"),
        ("uniprot:P20005", "1", "9606", "genesymbol", "EXACT"),
        ("uniprot:P20006", "3", "9606", "genesymbol-syn", "EXACT"),
        ("uniprot:P20008", "1", "9606", "genesymbol", "BAD_PRODUCT_TAXON"),
        ("uniprot:P20009", "1", "9606", "genesymbol", "QUARANTINED_PRODUCT"),
        ("uniprot:P20010", "15", "0", "genesymbol", "KNOWN_PRODUCT_TAXON"),
    ]:
        claims.append(dict(entity_id=eid, namespace=ns, identifier=text))
        links.append(dict(protein_entity_id=eid, entrez_id=gene, taxon=taxon))
    # A product linked to two genes cannot uniquely attribute its alias to either.
    claims.append(dict(entity_id="uniprot:P20007", namespace="genesymbol", identifier="NONUNIQUE"))
    links.extend(
        dict(protein_entity_id="uniprot:P20007", entrez_id=g, taxon="9606") for g in ("1", "3")
    )
    entities = [
        dict(entity_id="entrez:" + g, kind="gene", taxon=t, quarantined=False) for g, t in genes
    ]
    entities.extend(
        dict(
            entity_id=eid,
            kind="protein",
            taxon="10090" if eid in {"uniprot:P20003", "uniprot:P20008"} else "9606",
            quarantined=eid == "uniprot:P20009",
        )
        for eid in {c["entity_id"] for c in claims}
    )
    parquet(
        "gene_protein-entities/entities.parquet",
        entities,
        [
            ("entity_id", pa.string()),
            ("kind", pa.string()),
            ("taxon", pa.string()),
            ("quarantined", pa.bool_()),
        ],
    )
    parquet(
        "claims-uniprot/identifier_claims.parquet",
        claims,
        [("entity_id", pa.string()), ("namespace", pa.string()), ("identifier", pa.string())],
    )
    parquet(
        "gene-products/gene_products.parquet",
        links,
        [("protein_entity_id", pa.string()), ("entrez_id", pa.string()), ("taxon", pa.string())],
    )
    direct = [
        dict(record_id="entrez:1", namespace="ensg", identifier="ENSG000001"),
        dict(record_id="entrez:1", namespace="enst", identifier="ENST000001.7"),
        dict(record_id="entrez:1", namespace="refseq", identifier="NM_000001.7"),
    ]
    direct.extend(
        dict(record_id="entrez:" + str(n), namespace="genesymbol", identifier="TOO_MANY")
        for n in range(4, 16)
    )
    parquet(
        "claims-entrez/identifier_claims.parquet",
        direct,
        [("record_id", pa.string()), ("namespace", pa.string()), ("identifier", pa.string())],
    )
    return reference, base


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    reference, base = fixture(tmp_path_factory.mktemp("gene-role-fixture"))
    prior = reference_provenance(base)
    identity = build_gene_role_index(reference, base, memory="256MB", min_free_gib=0)
    assert identity == component_identity(base, required=True)
    assert reference_provenance(base) != prior
    return reference, base


def test_mixed_products_direct_gene_and_more_than_ten_products(compiled):
    _, base = compiled
    with closing(FullRuntime(base)) as r:
        for ns, ident, scope in [
            ("ensg", "ENSG000001", ""),
            ("ensg", "ENSG000001", "9606"),
            ("genesymbol", "GENE1", "9606"),
        ]:
            value = r.lookup(key(2, 1, ns, scope, ident))
            assert [c[1] for c in value["candidates"]] == ["entrez:1"]
            assert value["gene"] and not value["products"]
        rec = r.record("entrez:1")
        assert rec["label"] in {"GENE1", "EXACT"}
        assert ["genesymbol", "GENE1"] in rec["identifiers"]
        assert ["ensg", "ENSG000001"] in rec["identifiers"]
        assert not any(ns in {"enst", "refseq"} for ns, _ in rec["identifiers"])


def test_supported_ambiguity_and_gene_cap(compiled):
    _, base = compiled
    with closing(FullRuntime(base)) as r:
        multi = r.lookup(key(2, 1, "genesymbol", "9606", "MULTI"))
        assert {c[1] for c in multi["candidates"]} == {"entrez:1", "entrez:3"}
        assert [c[0] for c in multi["candidates"]] == [27, 29]
        assert [c[1] for c in multi["candidates"]] == ["entrez:3", "entrez:1"]
        assert r.lookup(key(2, 1, "genesymbol", "9606", "TOO_MANY"))["candidates"] == []
        assert ["genesymbol", "TOO_MANY"] not in r.record("entrez:4")["identifiers"]
        assert not r.lookup(key(2, 1, "genesymbol", "9606", "NONUNIQUE"))["candidates"]
    with closing(LibraryMatcher(base)) as matcher:
        raw = RawEntityObservation("g", "gene", "genesymbol", "MULTI", "9606")
        result = matcher.match({"g": raw})["g"]
        assert result.gene_mapping_status == "ambiguous"
        assert result.protein_identifier is None


def test_taxon_guards_and_exact_synonym_precedence(compiled):
    _, base = compiled
    with closing(FullRuntime(base)) as r:
        for scope, gene in [("9606", "1"), ("10090", "2")]:
            assert [
                c[1] for c in r.lookup(key(2, 1, "genesymbol", scope, "GENE1"))["candidates"]
            ] == ["entrez:" + gene]
        assert not r.lookup(key(2, 1, "genesymbol", "", "GENE1"))["candidates"]
        assert not r.lookup(key(2, 1, "genesymbol", "9606", "CROSS"))["candidates"]
        for text in ("BAD_PRODUCT_TAXON", "QUARANTINED_PRODUCT"):
            assert not r.lookup(key(2, 1, "genesymbol", "9606", text))["candidates"]
        assert [
            c[1]
            for c in r.lookup(key(2, 1, "genesymbol", "9606", "KNOWN_PRODUCT_TAXON"))["candidates"]
        ] == ["entrez:15"]
        for ns in ("genesymbol", "genesymbol-syn"):
            assert [c[1] for c in r.lookup(key(2, 1, ns, "9606", "EXACT"))["candidates"]] == [
                "entrez:1"
            ]
        assert ["genesymbol-syn", "EXACT"] not in r.record("entrez:3")["identifiers"]


def test_product_chemical_and_sequence_specificity_unchanged(compiled):
    _, base = compiled
    with (
        closing(FullRuntime(base, _load_gene_roles=False)) as old,
        closing(FullRuntime(base)) as new,
    ):
        for ns, ident, target in [
            ("uniprot", "P00001", 2),
            ("inchikey", "AAAAAAAAAAAAAA-BBBBBBBBBB-C", 1),
        ]:
            lookup = key(target, 1, ns, "", ident)
            assert old.lookup(lookup) == new.lookup(lookup)
        assert old.record("uniprot:P00001") == new.record("uniprot:P00001")
    with closing(LibraryMatcher(base)) as matcher:
        obs = RawEntityObservation("t", "transcript", "enst", "ENST000001.99", "9606")
        result = matcher.match({"t": obs})["t"]
        assert result.canonical_identifier == "1"
        assert result.transcript_identifier == "ENST000001.99"
        assert result.protein_identifier is None


def test_every_partition_has_exact_durable_round_trip_proof(compiled):
    _, base = compiled
    for kind in ("identifiers", "records"):
        checkpoints = sorted((base / NAME / "checkpoints" / kind).glob("*.json"))
        assert len(checkpoints) == 256
        for path in checkpoints:
            checkpoint = json.loads(path.read_text())
            assert (
                checkpoint["records"]
                == checkpoint["exact_records"]
                == checkpoint["exact_round_trip_records"]
            )


@pytest.mark.parametrize("damage", ["incomplete", "wrong_base", "checksum"])
def test_component_integrity_rejects_incomplete_wrong_base_or_corruption(
    compiled, tmp_path, damage
):
    _, source = compiled
    base = tmp_path / "base"
    shutil.copytree(source, base)
    if damage == "incomplete":
        (base / NAME / "manifest.json").unlink()
    elif damage == "wrong_base":
        manifest = json.loads((base / "manifest.json").read_text())
        manifest["changed"] = True
        (base / "manifest.json").write_text(json.dumps(manifest))
    else:
        path = base / NAME / "identifiers/00/data.mdb"
        with path.open("r+b") as handle:
            handle.write(b"corrupt")
    with pytest.raises(ValueError, match="Incomplete|different base|checksum"):
        FullRuntime(base)


def test_required_component_and_resumable_checkpoint_failure(tmp_path, monkeypatch):
    import omnipath_build.reference.gene_role_index as module

    reference, base = fixture(tmp_path)
    monkeypatch.setenv("OMNIPATH_REQUIRE_GENE_ROLE_INDEX", "1")
    with pytest.raises(ValueError, match="missing"):
        FullRuntime(base)
    with pytest.raises(ValueError, match="missing"):
        reference_provenance(base)
    original = module._partition
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("simulated interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_partition", fail)
    with pytest.raises(RuntimeError, match="interruption"):
        build_gene_role_index(reference, base, memory="256MB", min_free_gib=0)
    cp = base / ("." + NAME + ".building/checkpoints/identifiers/00.json")
    before = cp.read_bytes()
    monkeypatch.setattr(module, "_partition", original)
    build_gene_role_index(reference, base, memory="256MB", min_free_gib=0)
    assert cp.exists() is False  # Its unchanged checkpoint moved with publication.
    assert (base / NAME / "checkpoints/identifiers/00.json").read_bytes() == before
    with closing(FullRuntime(base)) as r:
        assert r.gene_roles.identity


def test_checksum_cache_reuses_verified_files_and_detects_same_size_changes(
    compiled, tmp_path, monkeypatch
):
    import omnipath_resolver.gene_role_index as module

    _, source = compiled
    base = tmp_path / "base"
    shutil.copytree(source, base)
    original = module.sha256
    calls = []

    def count(path):
        if str(path).endswith("data.mdb"):
            calls.append(str(path))
        return original(path)

    monkeypatch.setattr(module, "sha256", count)
    component_identity(base, required=True)
    assert len(calls) == 512
    calls.clear()
    component_identity(base, required=True)
    assert calls == []
    path = base / NAME / "identifiers/00/data.mdb"
    before = path.stat()
    with path.open("r+b") as handle:
        handle.write(b"corrupt")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="checksum"):
        component_identity(base, required=True)
    assert calls  # ctime/inode identity invalidates the previously verified proof.


def test_resume_after_final_manifest_before_publication(tmp_path, monkeypatch):
    import omnipath_build.reference.gene_role_index as module

    reference, base = fixture(tmp_path)
    original = module._publish

    def interrupt(staging, final):
        shutil.rmtree(staging / "assertions")
        raise RuntimeError("publication interrupted")

    monkeypatch.setattr(module, "_publish", interrupt)
    with pytest.raises(RuntimeError, match="publication interrupted"):
        build_gene_role_index(reference, base, memory="256MB", min_free_gib=0)
    monkeypatch.setattr(module, "_publish", original)
    build_gene_role_index(reference, base, memory="256MB", min_free_gib=0)
    assert component_identity(base, required=True)


def test_future_direct_build_requires_component_before_cleanup(tmp_path, monkeypatch):
    import omnipath_build.reference.candidate_limit as policy
    import omnipath_build.reference.direct_compact_index as direct
    import omnipath_build.reference.full_index_assertions as assertions

    reference, base = fixture(tmp_path)
    events = []

    class Compiler:
        def __init__(self, *args, **kwargs):
            pass

        def prepare_bulk(self):
            pass

        def publish(self):
            manifest = json.loads((base / "manifest.json").read_text())
            manifest["gene_role_component_required"] = True
            (base / "manifest.json").write_text(json.dumps(manifest))
            with pytest.raises(ValueError, match="missing"):
                FullRuntime(base)
            events.append("base")

        def cleanup_scratch(self):
            with closing(FullRuntime(base)) as runtime:
                assert runtime.gene_roles.identity
            events.append("cleanup")

    monkeypatch.setattr(direct, "DirectCompiler", Compiler)
    monkeypatch.setattr(direct, "_partition_worker", lambda job: None)
    monkeypatch.setattr(assertions, "stage_assertions", lambda compiler: None)
    monkeypatch.setattr(policy, "apply_limit", lambda *args: events.append("policy"))
    result = direct.build_direct_compact(reference, base, workers=1, threads=1, memory="256MB")
    assert result["gene_role_component_required"]
    assert events == ["base", "policy", "cleanup"]
    assert component_identity(base, required=True)
