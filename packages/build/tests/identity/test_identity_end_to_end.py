"""Builder output read by the runtime: fixture hubs -> hub indexes -> hub kv -> decisions -> decisions kv
-> LibraryMatcher (which reads LMDB only)."""

from __future__ import annotations

import pytest

from identity_fixtures import key, write_hubs
from omnipath_build.identity import build_hub_index, build_hub_kv, build_identity, build_identity_kv
from omnipath_build.identity.common import HUBS
from omnipath_resolver.canonical.match import LibraryMatcher
from omnipath_resolver.contracts import RawEntityObservation

ARGS = dict(memory="1GB", threads=2, min_free_gib=0.01)


@pytest.fixture(scope="module")
def matcher(tmp_path_factory, monkeypatch_module):
    root = tmp_path_factory.mktemp("identity-e2e")
    hubs = write_hubs(root / "hubs")
    for hub in HUBS:
        if (hubs / f"{hub}.parquet").exists():
            build_hub_index(hub, hubs, root / "hubindex", goslin_cache=root / "goslin", **ARGS)
            build_hub_kv(hub, root / "hubindex", memory="512MB", threads=2, min_free_gib=0.01)
    snapshot = build_identity(root / "hubindex", root / "identity", **ARGS)
    build_identity_kv(root / "identity" / snapshot["fingerprint"], min_free_gib=0.001)
    monkeypatch_module.setenv("OMNIPATH_IDENTITY_CACHE", str(root / "cache"))
    m = LibraryMatcher(root / "identity" / snapshot["fingerprint"])
    yield m
    m.close()


@pytest.fixture(scope="module")
def monkeypatch_module():
    with pytest.MonkeyPatch.context() as mp:
        yield mp


def resolve(matcher, entity_type, namespace, identifier, *identifiers, taxon=None):
    obs = RawEntityObservation(
        "k",
        entity_type,
        namespace,
        identifier,
        taxon=taxon,
        identifiers=[dict(ns=ns, id=i) for ns, i in identifiers],
    )
    return matcher.targets({"k": obs})["k"]


def chemical(matcher, namespace, identifier):
    (match,) = resolve(matcher, "small_molecule", namespace, identifier)
    return match


def test_anchored_and_attached_chemicals(matcher):
    assert chemical(matcher, "chebi", "CHEBI:1").node_id == f"inchikey:{key('A')}"
    assert chemical(matcher, "chebi", "CHEBI:1").label == "Alpha"
    # One-step attach from an outgoing cross-reference.
    assert chemical(matcher, "chembl", "CHEMBL1").node_id == f"inchikey:{key('A')}"
    # Incoming cross-reference attaches too; a chain does not.
    assert chemical(matcher, "metanetx", "MNXM3").node_id == f"inchikey:{key('D')}"
    assert chemical(matcher, "metanetx", "MNXM4").node_id == "metanetx:MNXM4"
    # Two different anchors: the record stays its own entity.
    assert chemical(matcher, "bigg", "b1").node_id == "bigg:b1"


def test_lipid_name_anchor(matcher):
    a = chemical(matcher, "swisslipids", "SLM:1").node_id
    b = chemical(matcher, "lipidmaps", "LM1").node_id
    assert a == b and a.startswith("goslin:") and a.endswith("PC 16:0/18:1")


def test_structureless_groups(matcher):
    # Accepted group: one record per hub, named by the preferred hub (metanetx before bigg).
    assert chemical(matcher, "bigg", "g10").node_id == "metanetx:MNXM10"
    assert chemical(matcher, "metanetx", "MNXM10").node_id == "metanetx:MNXM10"
    # Rejected group: two metanetx records, so each record stays alone.
    assert chemical(matcher, "metanetx", "MNXM20").node_id == "metanetx:MNXM20"
    assert chemical(matcher, "metanetx", "MNXM21").node_id == "metanetx:MNXM21"


def test_protein_with_gene(matcher):
    (match,) = resolve(matcher, "protein", "uniprot", "P04637", ("ncbigene", "7157"))
    assert match.node_id == "entrez:7157"
    assert match.gene_mapping_status == "resolved"
    assert match.protein_node_id == "uniprot:P04637"
    assert match.label == "TP53"


def test_secondary_accessions(matcher):
    # Q15086 is a secondary accession of exactly one entry.
    (match,) = resolve(matcher, "protein", "uniprot", "Q15086")
    assert match.protein_node_id == "uniprot:P04637"
    # Q16535 is a secondary accession of two entries: no product is chosen.
    (match,) = resolve(matcher, "protein", "uniprot", "Q16535")
    assert match.protein_node_id is None


def test_symbol_with_many_genes_is_ambiguous(matcher):
    (match,) = resolve(matcher, "gene", "genesymbol", "MANYSYM", taxon="9606")
    assert match.node_id is None
    assert match.gene_mapping_status == "ambiguous"
    assert len(match.gene_candidates) == 12
