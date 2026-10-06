"""IdentityRuntime over a synthetic omnipath-identity-v1 snapshot (spec section 4)."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from identity_snapshot import (
    ASPIRIN,
    ETHANOL,
    FINGERPRINT,
    LIPID,
    NAMELESS,
    ONLY_PC,
    OTHERS,
    SYSTEMATIC,
    WATER,
    build_snapshot,
    part,
)
from omnipath_resolver import RawEntityObservation
from omnipath_resolver import identity_runtime
from omnipath_resolver.canonical import votes_for
from omnipath_resolver.canonical.match import LibraryMatcher
from omnipath_resolver.canonical.policy import CHEMICAL_POLICY
from omnipath_resolver.identity_runtime import (
    IdentityRuntime,
    decode_key,
    entity_num,
    is_identity_snapshot,
    open_runtime,
)
from omnipath_resolver.index import FullRuntime
from omnipath_resolver.observations import CODES, key, observation_bundle

CODE_NAMES = {code: ns for ns, code in CODES.items()}
HUMAN, MOUSE = "9606", "10090"


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    return build_snapshot(tmp_path_factory.mktemp("identity") / "snapshot")


@pytest.fixture
def runtime(snapshot, tmp_path):
    rt = IdentityRuntime(snapshot, cache_dir=tmp_path / "cache")
    yield rt
    rt.close()


def ids(value):
    return [c[1] for c in value["candidates"]]


def look(rt, target, route, ns, identifier, scope=""):
    k = key(target, route, ns, scope, identifier)
    return rt.lookup_many([k])[k]


# ---------------------------------------------------------------------------- keys


def test_key_decoding_unscoped_and_scoped():
    assert decode_key(key(1, 1, "chebi", "", "CHEBI:1"), CODE_NAMES) == (
        1,
        1,
        "chebi",
        None,
        "CHEBI:1",
    )
    assert decode_key(key(2, 2, "entrez", "9606", "7157"), CODE_NAMES) == (
        2,
        2,
        "entrez",
        "9606",
        "7157",
    )
    # an identifier that itself looks like a taxon block must not be mistaken for one
    assert decode_key(key(2, 1, "genesymbol", "10090", "Trp53"), CODE_NAMES)[3:] == (
        "10090",
        "Trp53",
    )
    for bad in (b"", b"\x02\x01\x01\x00\x01\x00x", key(1, 1, "chebi", "", "x")[:6]):
        with pytest.raises(ValueError):
            decode_key(bad, CODE_NAMES)
    with pytest.raises(ValueError):
        decode_key(bytes([1, 1, 1, 0xFF, 0xFF, 0]) + b"x", CODE_NAMES)


def test_manifest_format_selects_runtime(snapshot, tmp_path):
    assert is_identity_snapshot(snapshot)
    rt = open_runtime(snapshot, cache_dir=tmp_path)
    try:
        assert isinstance(rt, IdentityRuntime)
    finally:
        rt.close()
    assert not is_identity_snapshot(tmp_path)
    with pytest.raises(OSError):
        IdentityRuntime(tmp_path)  # no manifest


def test_only_the_hashed_partitions_are_read(snapshot, tmp_path, monkeypatch):
    # Corrupt every access partition no requested key hashes to: a read of any of them fails.
    copy = tmp_path / "copy"
    import shutil

    shutil.copytree(snapshot, copy)
    wanted = {part("CHEBI:15377"), part("HMDB0002111")}
    broken = 0
    for path in (copy / "access" / "target=1").glob("part=*/data.parquet"):
        if path.parent.name.removeprefix("part=") not in wanted:
            path.write_bytes(b"not parquet")
            broken += 1
    assert broken
    rt = IdentityRuntime(copy, cache_dir=tmp_path / "c")
    files = []
    original = rt._query
    monkeypatch.setattr(
        rt, "_query", lambda sql, f, *a, **k: (files.append(f), original(sql, f, *a, **k))[1]
    )
    try:
        k1, k2 = key(1, 1, "chebi", "", "CHEBI:15377"), key(1, 1, "hmdb", "", "HMDB0002111")
        out = rt.lookup_many([k1, k2])
        assert ids(out[k1]) == [f"inchikey:{WATER}"] and ids(out[k2]) == [f"inchikey:{WATER}"]
        (only,) = files  # one query for the batch
        assert {p.split("part=")[1].split("/")[0] for p in only} == wanted
    finally:
        rt.close()


def test_miss_and_batch_shape(runtime):
    k = [key(1, 1, "chebi", "", "CHEBI:404"), key(2, 1, "hgnc", "", "HGNC:0")]
    out = runtime.lookup_many(k)
    assert set(out) == set(k)
    assert out[k[0]] == dict(candidates=[], gene=False, products=False)
    assert out[k[1]] == dict(candidates=[], gene=True, products=False)
    assert runtime.lookup_many([]) == {}


# ------------------------------------------------------------------------ precedence


def test_native_beats_fallback(runtime):
    assert ids(look(runtime, 1, 1, "bigg", "h2o")) == [f"inchikey:{WATER}"]
    assert ids(look(runtime, 1, 1, "kegg", "C00001")) == [f"inchikey:{WATER}"]  # lone fallback


def test_primary_accession_beats_secondary(runtime):
    assert ids(look(runtime, 2, 1, "uniprot", "Q99999")) == ["uniprot:Q99999"]
    # a secondary accession resolves only to the entry it belongs to ...
    assert ids(look(runtime, 2, 1, "uniprot", "Q15086")) == ["uniprot:P04637"]
    # ... and stays plural (ambiguous, no tie-break) when it maps to two entries
    assert sorted(ids(look(runtime, 2, 1, "uniprot", "Q88888"))) == [
        "uniprot:P04637",
        "uniprot:P0DP23",
    ]
    # the same claim under uniprot-sec has nothing to beat
    assert sorted(ids(look(runtime, 2, 1, "uniprot-sec", "Q99999"))) == ["uniprot:P04637"]


def test_exact_symbol_beats_synonym_within_taxon(runtime):
    for ns in ("genesymbol", "genesymbol-syn"):
        assert ids(look(runtime, 2, 1, ns, "ABC1", HUMAN)) == ["entrez:901"]
        assert ids(look(runtime, 2, 1, ns, "XYZ", HUMAN)) == ["entrez:902"]  # synonym only
        # TP53 is exact for human and only a synonym for mouse: each taxon keeps its own
        assert ids(look(runtime, 2, 1, ns, "TP53", HUMAN)) == ["entrez:7157"]
        assert ids(look(runtime, 2, 1, ns, "TP53", MOUSE)) == ["entrez:22059"]


# ---------------------------------------------------------------------------- scope


def test_scope_filters_on_entity_taxon(runtime):
    assert ids(look(runtime, 2, 1, "ensg", "ENSG00000141510")) == ["entrez:7157"]
    assert ids(look(runtime, 2, 1, "ensg", "ENSG00000141510", HUMAN)) == ["entrez:7157"]
    assert ids(look(runtime, 2, 1, "ensg", "ENSG00000141510", MOUSE)) == []
    # entities without a taxon are visible only unscoped
    assert ids(look(runtime, 2, 1, "ensg", "ENSG00000999999")) == ["entrez:555"]
    assert ids(look(runtime, 2, 1, "ensg", "ENSG00000999999", HUMAN)) == []
    assert ids(look(runtime, 2, 2, "entrez", "7157", MOUSE)) == []


def test_symbols_exist_only_scoped(runtime):
    assert look(runtime, 2, 1, "genesymbol", "TP53")["candidates"] == []
    assert look(runtime, 2, 1, "genesymbol-syn", "TP53")["candidates"] == []
    assert ids(look(runtime, 2, 1, "genesymbol", "TP53", HUMAN)) == ["entrez:7157"]
    assert look(runtime, 2, 1, "genesymbol", "TP53", "7227")["candidates"] == []


def test_gene_flag(runtime):
    expect = {
        (1, "ensg", "ENSG00000141510"): True,
        (1, "hgnc", "HGNC:11998"): True,
        (1, "refseq", "NM_000546"): True,
        (1, "genesymbol", "ABC1"): True,
        (1, "uniprot", "P04637"): False,
        (1, "uniprot_entry", "P53_HUMAN"): False,
        (2, "entrez", "7157"): True,  # route 2 is always a gene vote
    }
    for (route, ns, identifier), flag in expect.items():
        value = look(runtime, 2, route, ns, identifier, HUMAN if ns.startswith("gene") else "")
        assert value["candidates"] and value["gene"] is flag and value["products"] is False, ns
    assert look(runtime, 1, 1, "chebi", "CHEBI:15377")["gene"] is False


# --------------------------------------------------------------------- candidate facts


def test_candidate_facts_and_num(runtime):
    (fact,) = look(runtime, 2, 1, "uniprot", "P04637")["candidates"]
    digest = hashlib.sha256(b"uniprot:P04637").digest()
    assert fact == [
        int.from_bytes(digest[:8], "big") >> 1,
        "uniprot:P04637",
        2,
        "uniprot:P04637",
        False,
        True,
        ["7157"],
    ]
    (gene,) = look(runtime, 2, 2, "entrez", "7157")["candidates"]
    assert gene[1:] == ["entrez:7157", 3, None, False, False, ["7157"]]
    (chem,) = look(runtime, 1, 1, "chebi", "CHEBI:15377")["candidates"]
    assert chem[1:4] == [f"inchikey:{WATER}", 1, f"inchikey:{WATER}"]
    # lipid-name entities are unanchored for the kernel, quarantine flows through
    (lipid,) = look(runtime, 1, 1, "goslin", "species:PE 36:2")["candidates"]
    assert lipid[1:4] == ["goslin:species:PE 36:2", 1, None]
    (bad,) = look(runtime, 1, 1, "chebi", "CHEBI:99999")["candidates"]
    assert bad[4] is True


def test_num_is_stable_unique_and_sorts_candidates(runtime):
    value = look(runtime, 2, 1, "uniprot", "Q88888")
    nums = [c[0] for c in value["candidates"]]
    assert nums == sorted(nums) and len(set(nums)) == len(nums) == 2
    k = key(2, 1, "uniprot", "", "Q88888")
    alone = runtime.lookup_many([k])[k]
    many = runtime.lookup_many([k, key(1, 1, "chebi", "", "CHEBI:15377")])[k]
    assert alone == many == value
    assert {c[1]: c[0] for c in value["candidates"]} == {
        eid: entity_num(eid) for eid in ("uniprot:P04637", "uniprot:P0DP23")
    }


def test_num_collision_in_a_batch_raises(runtime, monkeypatch):
    monkeypatch.setattr(identity_runtime, "entity_num", lambda eid: 7)
    ks = [key(1, 1, "chebi", "", "CHEBI:15377"), key(1, 1, "chebi", "", "CHEBI:15365")]
    with pytest.raises(ValueError, match="collision"):
        runtime.lookup_many(ks)


def test_no_candidate_cutoff(runtime):
    value = look(runtime, 1, 1, "cas", "0-0-0")
    assert len(value["candidates"]) == 12
    nums = [c[0] for c in value["candidates"]]
    assert nums == sorted(nums)


def test_lookup_and_record_wrappers(runtime):
    k = key(1, 1, "chebi", "", "CHEBI:15377")
    assert runtime.lookup(k) == runtime.lookup_many([k])[k]
    assert (
        runtime.record(f"inchikey:{WATER}")
        == runtime.record_many([f"inchikey:{WATER}"])[f"inchikey:{WATER}"]
    )


# ------------------------------------------------------------------------------ records


def rec(runtime, eid):
    return runtime.record(eid)


def test_record_shape_and_identifiers(runtime):
    r = rec(runtime, "uniprot:P04637")
    assert set(r) == {"entity_id", "kind", "anchor", "taxon", "label", "identifiers", "gene_ids"}
    assert (r["entity_id"], r["kind"], r["anchor"], r["taxon"]) == (
        "uniprot:P04637",
        2,
        "uniprot:P04637",
        "9606",
    )
    assert r["gene_ids"] == ["7157"]
    pairs = [tuple(p) for p in r["identifiers"]]
    assert pairs == sorted(pairs)
    assert {
        ("uniprot", "P04637"),
        ("uniprot_entry", "P53_HUMAN"),
        ("genesymbol-syn", "P53"),
        ("uniprot-sec", "Q15086"),
        ("hgnc", "HGNC:11998"),
    } <= set(pairs)
    gene = rec(runtime, "entrez:7157")
    assert (gene["kind"], gene["anchor"], gene["taxon"], gene["gene_ids"]) == (3, None, "9606", [])
    chem = rec(runtime, f"inchikey:{WATER}")
    pairs = {tuple(p) for p in chem["identifiers"]}
    assert ("inchikey", WATER) in pairs and ("chebi", "CHEBI:15377") in pairs
    assert ("name", "water") in pairs and ("hmdb", "HMDB0002111") in pairs
    assert not any(ns in ("smiles", "synonym") for ns, _ in pairs)
    assert chem["kind"] == 1 and chem["anchor"] == f"inchikey:{WATER}" and chem["taxon"] is None


def test_missing_entity_raises(runtime):
    with pytest.raises(ValueError, match="absent entity"):
        runtime.record_many(["inchikey:" + "Z" * 14 + "-UHFFFAOYSA-N"])


@pytest.mark.parametrize(
    "entity, label",
    [
        # chemical: ChEBI > HMDB > ChEMBL > PubChem > other hub > systematic
        (f"inchikey:{WATER}", "water"),  # chebi: shortest of two, ties alphabetical
        (f"inchikey:{ASPIRIN}", "ASPIRIN"),  # chebi name is an InChIKey, hmdb's is over 80
        (f"inchikey:{ONLY_PC}", "pc-name"),  # pubchem before an unlisted hub
        (f"inchikey:{OTHERS}", "a"),  # other hubs: shorter then alphabetical
        (f"inchikey:{SYSTEMATIC}", "sys-name"),  # systematic name last
        (f"inchikey:{NAMELESS}", NAMELESS),  # id local part, no CHEBI:/CID: formatting
        (f"inchikey:{LIPID}", "PC 16:0/18:1"),  # most specific Goslin name beats a chemical name
        ("goslin:species:PE 36:2", "PE 36:2"),
        ("chebi:CHEBI:99999", "multi"),
        # gene: NCBI symbol, else the id
        ("entrez:7157", "TP53"),
        ("entrez:555", "555"),
        # protein: primary gene name > entry name > accession
        ("uniprot:P04637", "TP53"),
        ("uniprot:A0A0U1RQF1", "A0A0U1RQF1_HUMAN"),  # only a genesymbol-syn: never a label
        ("uniprot:Q99999", "Q99999"),
    ],
)
def test_labels(runtime, entity, label):
    assert rec(runtime, entity)["label"] == label


def test_cache_hit_path(snapshot, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    first = IdentityRuntime(snapshot, cache_dir=cache)
    try:
        wanted = [f"inchikey:{WATER}", "uniprot:P04637", "entrez:7157"]
        built = first.record_many(wanted)
    finally:
        first.close()
    database = cache / FINGERPRINT / "entities.sqlite"
    assert database.is_file()
    con = sqlite3.connect(database)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert con.execute("SELECT count(*) FROM records").fetchone()[0] == 3
    finally:
        con.close()

    def boom(self, entity_ids):
        raise AssertionError("cache miss: " + ",".join(entity_ids))

    monkeypatch.setattr(IdentityRuntime, "_build_records", boom)
    second = IdentityRuntime(snapshot, cache_dir=cache)
    try:
        assert second.record_many(wanted) == built  # served from sqlite
        with pytest.raises(AssertionError):
            second.record_many([f"inchikey:{ASPIRIN}"])  # a genuine miss still builds
    finally:
        second.close()


def test_cache_location_default_and_env(snapshot, tmp_path, monkeypatch):
    monkeypatch.delenv("OMNIPATH_IDENTITY_CACHE", raising=False)
    assert identity_runtime.default_cache_dir(snapshot) == snapshot.resolve().parent / "cache"
    monkeypatch.setenv("OMNIPATH_IDENTITY_CACHE", str(tmp_path / "elsewhere"))
    assert identity_runtime.default_cache_dir(snapshot) == tmp_path / "elsewhere"


# ----------------------------------------------------------------------------- rule 11


def chemical(identifiers, **kw):
    return RawEntityObservation(
        "e",
        "chemical_entity",
        kw.pop("namespace", "name"),
        kw.pop("identifier", "x"),
        identifiers=[dict(ns=ns, id=i) for ns, i in identifiers],
        **kw,
    )


@pytest.fixture
def structures(tmp_path, monkeypatch):
    from omnipath_resolver.canonical import structures as module

    monkeypatch.setenv("OMNIPATH_STRUCTURE_CACHE", str(tmp_path / "structures"))
    monkeypatch.setenv("OMNIPATH_GOSLIN_CACHE", str(tmp_path / "goslin"))
    module._cached.cache_clear()
    yield module
    module._cached.cache_clear()


def anchors(obs):
    votes, observed = votes_for(obs, CHEMICAL_POLICY)
    _, rows = observation_bundle("e", obs, votes, observed, "chemical")
    return {r["identifier"]: r["anchor"] for r in rows if r["ns"] == "inchikey"}


def test_derived_inchikey_is_not_an_anchor_but_a_stated_one_is(structures):
    assert anchors(chemical([("smiles", "CCO")])) == {ETHANOL: ""}
    assert anchors(chemical([("inchikey", ETHANOL)])) == {ETHANOL: ETHANOL}
    # a stated key suppresses derivation entirely
    assert anchors(chemical([("smiles", "CCO"), ("inchikey", WATER)])) == {WATER: WATER}


# ------------------------------------------------------------------------ end to end


@pytest.fixture
def matcher(snapshot, tmp_path, monkeypatch, structures):
    monkeypatch.setenv("OMNIPATH_IDENTITY_CACHE", str(tmp_path / "cache"))
    m = LibraryMatcher(snapshot)
    yield m
    m.close()


def run(matcher, **observations):
    return matcher.targets(observations)


def test_matcher_chooses_identity_runtime(matcher):
    assert isinstance(matcher.runtime, IdentityRuntime)
    assert matcher.libraries == ["gene_protein", "chemical"]


def test_old_references_keep_the_old_runtime(tmp_path):
    # no manifest of the new format: FullRuntime's own validation answers
    (tmp_path / "manifest.json").write_text('{"format": "omnipath-full-two-index-v1"}')
    with pytest.raises(ValueError, match="two-index"):
        open_runtime(tmp_path)
    assert FullRuntime is not IdentityRuntime


def test_chemical_stated_vs_derived_inchikey(matcher):
    water = f"inchikey:{WATER}"
    out = run(
        matcher,
        # a stated InChIKey decides alone, overriding the conflicting ChEBI id
        stated=chemical([("inchikey", ETHANOL)], namespace="chebi", identifier="CHEBI:15377"),
        # the same structure derived from SMILES is an ordinary vote: it now conflicts with ChEBI
        derived=chemical([("smiles", "CCO")], namespace="chebi", identifier="CHEBI:15377"),
        # alone, a derived key still resolves
        alone=chemical([("smiles", "CCO")]),
        agreeing=chemical([("smiles", "O")], namespace="chebi", identifier="CHEBI:15377"),
        plain=chemical([], namespace="chebi", identifier="CHEBI:15377"),
    )
    (stated,) = out["stated"]
    assert stated.matched and stated.node_id == f"inchikey:{ETHANOL}" and stated.label == "ethanol"
    (derived,) = out["derived"]
    assert not derived.matched
    (alone,) = out["alone"]
    assert alone.node_id == f"inchikey:{ETHANOL}"
    (agreeing,) = out["agreeing"]
    assert agreeing.node_id == water and agreeing.label == "water"
    assert out["plain"][0].node_id == water
    assert "chebi" in out["plain"][0].aliases and "CHEBI:15377" in out["plain"][0].aliases["chebi"]


def test_chemical_ambiguity_has_no_cutoff(matcher):
    (match,) = run(matcher, many=chemical([], namespace="cas", identifier="0-0-0"))["many"]
    assert not match.matched
    k = key(1, 1, "cas", "", "0-0-0")
    assert len(matcher.runtime.lookup(k)["candidates"]) == 12
    resolved, metrics = matcher.runtime.resolve(
        [dict(input_id="q", target=1)],
        [
            dict(
                input_id="q",
                ns="cas",
                identifier="0-0-0",
                scope="",
                anchor="",
                target=1,
                route=1,
                ordinal=0,
                lookup_key=k,
            )
        ],
    )
    assert resolved["results"][0]["outcome"] == "Ambiguous"
    assert (
        resolved["results"][0]["candidate_count"] == 12 and resolved["results"][0]["entities"] == []
    )
    assert set(metrics) == {
        "lookup_seconds",
        "decision_seconds",
        "entity_fetch_seconds",
        "total_seconds",
        "unique_keys",
        "candidate_records",
        "entity_records",
    }


def protein(identifier, identifiers=(), taxon=None, entity_type="protein", namespace="uniprot"):
    return RawEntityObservation(
        "p",
        entity_type,
        namespace,
        identifier,
        taxon=taxon,
        identifiers=[dict(ns=ns, id=i) for ns, i in identifiers],
    )


def test_protein_with_geneid_resolves_to_gene_with_product(matcher):
    out = run(
        matcher,
        both=protein("P04637", [("entrez", "7157")], taxon=HUMAN),
        secondary=protein("Q15086"),
        primary_wins=protein("Q99999"),
        shared=protein("Q88888"),
        conflict=protein("P04637", [("entrez", "801")]),
    )
    (both,) = out["both"]
    assert both.node_id == "entrez:7157" and both.gene_mapping_status == "resolved"
    assert both.protein_identifier == "P04637" and both.protein_label == "TP53"
    assert both.protein_gene_candidates == ("entrez:7157",)
    assert both.reference_library == "gene_protein" and both.taxon == HUMAN
    (secondary,) = out["secondary"]
    assert secondary.node_id == "entrez:7157" and secondary.protein_identifier == "P04637"
    (primary_wins,) = out["primary_wins"]
    assert primary_wins.protein_identifier == "Q99999" and primary_wins.matched
    (shared,) = out["shared"]
    assert not shared.matched  # two entries claim Q88888 as secondary: abstain
    (conflict,) = out["conflict"]
    assert conflict.gene_mapping_status == "conflict" and conflict.node_id == "uniprot:P04637"


def test_gene_symbol_with_taxon(matcher):
    out = run(
        matcher,
        human=protein("TP53", taxon=HUMAN, entity_type="gene", namespace="genesymbol"),
        mouse=protein("TP53", taxon=MOUSE, entity_type="gene", namespace="genesymbol"),
        nothing=protein("TP53", taxon="7227", entity_type="gene", namespace="genesymbol"),
        no_taxon=protein("TP53", entity_type="gene", namespace="genesymbol"),
        scoped=protein("P04637", [("genesymbol", "TP53")], taxon=HUMAN),
        wrong_scope=protein("P04637", [("genesymbol", "TP53")], taxon=MOUSE),
        by_ensg=protein("ENSG00000141510", entity_type="gene", namespace="ensembl"),
    )
    assert [m.node_id for m in out["human"]] == ["entrez:7157"]
    assert out["human"][0].label == "TP53" and out["human"][0].taxon == HUMAN
    assert [m.node_id for m in out["mouse"]] == ["entrez:22059"]
    assert out["mouse"][0].label == "Trp53"
    assert not out["nothing"][0].matched
    assert not out["no_taxon"][0].matched  # symbols never match unscoped
    assert out["scoped"][0].node_id == "entrez:7157"
    assert out["scoped"][0].protein_identifier == "P04637"
    # the symbol scopes the whole observation: the human accession is invisible under mouse
    # (a miss never vetoes), so only the mouse synonym speaks
    assert out["wrong_scope"][0].node_id == "entrez:22059"
    assert out["by_ensg"][0].node_id == "entrez:7157"
