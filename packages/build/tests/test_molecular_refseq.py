"""Real three-product TP53 RefSeq ambiguity through bounded reference lookups.

Only disk I/O is replaced: FullRuntime.resolve, LibraryMatcher and both Rust
decision kernels run normally. The three primary accessions are independent
anchors, not secondary aliases; reviewed status must not select a product.
"""

from contextlib import closing

import pytest

from omnipath_resolver.canonical.match import LibraryMatcher
from omnipath_build.extract.observations import RawEntityObservation
from omnipath_resolver.index import FullRuntime
from omnipath_build.reference.replay_resources import key
from omnipath_resolver import resolve_precomputed_batch


PRODUCT_FACTS = [
    (50027779746663, "uniprot:Q53GA5", 2, "uniprot:Q53GA5", False, False, ["7157"]),
    (101704826243580, "uniprot:K7PPA8", 2, "uniprot:K7PPA8", False, False, ["7157"]),
    (231447198325441, "uniprot:P04637", 2, "uniprot:P04637", False, True, ["7157"]),
]
GENE_FACT = (7157, "entrez:7157", 3, None, False, False, ["7157"])


class RefSeqRuntime(FullRuntime):
    """A four-record reference with the real full-reference batch resolver."""

    def __init__(self):
        self.postings = {}
        self.records = {}
        self.looked_up = []
        for scope in ("", "9606"):
            for identifier in ("NP_000537", "NP_000537.3"):
                self.postings[key(2, 1, "refseq_protein", scope, identifier)] = dict(
                    candidates=PRODUCT_FACTS, gene=False, products=False
                )
            for fact in PRODUCT_FACTS:
                accession = fact[1].split(":", 1)[1]
                self.postings[key(2, 1, "uniprot", scope, accession)] = dict(
                    candidates=[fact], gene=False, products=False
                )
            self.postings[key(2, 2, "entrez", scope, "7157")] = dict(
                candidates=[GENE_FACT], gene=True, products=False
            )
        for fact in PRODUCT_FACTS:
            accession = fact[1].split(":", 1)[1]
            self.records[fact[1]] = dict(
                entity_id=fact[1],
                kind=2,
                anchor=fact[3],
                taxon="9606",
                label=accession,
                identifiers=[
                    ["uniprot", accession],
                    ["refseq_protein", "NP_000537"],
                    ["refseq_protein", "NP_000537.3"],
                ],
                gene_ids=["7157"],
            )
        self.records["entrez:7157"] = dict(
            entity_id="entrez:7157",
            kind=3,
            anchor=None,
            taxon="9606",
            label="TP53",
            identifiers=[["entrez", "7157"], ["genesymbol", "TP53"]],
            gene_ids=["7157"],
        )

    def lookup(self, lookup_key):
        self.looked_up.append(lookup_key)
        # Unexpected namespace/version routing fails instead of silently missing.
        return self.postings[lookup_key]

    def record(self, entity_id):
        return self.records[entity_id]

    def close(self):
        pass


@pytest.fixture
def matcher():
    with closing(LibraryMatcher(None)) as instance:
        instance.runtime = RefSeqRuntime()
        instance.libraries = ["gene_protein"]
        yield instance


def protein(namespace, identifier, extra=()):
    return RawEntityObservation(
        entity_key="reported-protein",
        entity_type="protein",
        namespace=namespace,
        identifier=identifier,
        taxon="9606",
        identifiers=[{"ns": ns, "id": value} for ns, value in extra],
    )


def refseq_vote(identifier, scope):
    return dict(
        input_id="input",
        ns="refseq_protein",
        lookup_key=key(2, 1, "refseq_protein", scope, identifier),
        anchor="",
        route=1,
        ordinal=0,
    )


@pytest.mark.parametrize("scope", ["", "9606"], ids=["global", "human"])
@pytest.mark.parametrize("identifier", ["NP_000537", "NP_000537.3"])
@pytest.mark.parametrize("explicit_gene", [False, True])
def test_three_primary_products_resolve_gene_without_selecting_reviewed_product(
    scope, identifier, explicit_gene
):
    runtime = RefSeqRuntime()
    votes = [refseq_vote(identifier, scope)]
    if explicit_gene:
        votes.append(
            dict(
                input_id="input",
                ns="entrez",
                lookup_key=key(2, 2, "entrez", scope, "7157"),
                anchor="entrez:7157",
                route=2,
                ordinal=1,
            )
        )
    resolved, _ = runtime.resolve([dict(input_id="input", target=2)], votes)
    result = resolved["results"][0]
    assert result["entities"] == ["entrez:7157"]
    assert result["gene_mapping_status"] == "resolved"
    assert result["gene_candidates"] == ["entrez:7157"]
    assert result["protein_entity_id"] is None
    assert result["candidate_count"] == 3 + explicit_gene
    assert set(resolved["records"]) == {"entrez:7157"}


@pytest.mark.parametrize("namespace", ["refseq", "refseq_protein"])
@pytest.mark.parametrize("identifier", ["NP_000537", "NP_000537.3"])
@pytest.mark.parametrize("explicit_gene", [False, True])
def test_matcher_normalizes_protein_prefix_but_retains_exact_reported_refseq(
    matcher, namespace, identifier, explicit_gene
):
    extra = [("entrez", "7157")] if explicit_gene else []
    observation = protein(namespace, identifier, extra)
    result = matcher.match({"input": observation})["input"]
    assert result.entity_type == "protein"
    assert result.node_id == "entrez:7157"
    assert (result.canonical_namespace, result.canonical_identifier) == ("entrez", "7157")
    assert result.gene_mapping_status == "resolved"
    assert result.gene_candidates == ("entrez:7157",)
    assert (result.protein_namespace, result.protein_identifier) == (namespace, identifier)
    assert result.protein_node_id is None
    assert result.protein_gene_candidates == ()
    assert result.protein_aliases == {namespace: [identifier]}
    assert key(2, 1, "refseq_protein", "", "NP_000537") in matcher.runtime.looked_up
    assert observation.identifier == identifier


@pytest.mark.parametrize("namespace", ["refseq", "refseq_protein"])
@pytest.mark.parametrize("accession", ["P04637", "K7PPA8", "Q53GA5"])
def test_explicit_primary_uniprot_disambiguates_including_unreviewed_products(
    matcher, namespace, accession
):
    observation = protein(namespace, "NP_000537.3", [("uniprot", accession), ("entrez", "7157")])
    result = matcher.match({"input": observation})["input"]
    assert result.node_id == "entrez:7157"
    assert result.gene_mapping_status == "resolved"
    assert result.entity_type == "protein"
    assert result.protein_node_id == "uniprot:" + accession
    assert (result.protein_namespace, result.protein_identifier) == ("uniprot", accession)
    assert result.protein_gene_candidates == ("entrez:7157",)
    assert result.protein_aliases["uniprot"] == [accession]
    assert "NP_000537.3" in result.protein_aliases["refseq_protein"]
    assert observation.identifier == "NP_000537.3"


def test_refseq_form_assertion_on_gene_does_not_choose_catalogue_product(matcher):
    observation = protein("entrez", "7157")
    observation.molecular_form = dict(sequence_identifiers=[dict(ns="refseq", id="NP_000537.3")])
    result = matcher.match({"input": observation})["input"]
    assert result.node_id == "entrez:7157"
    assert result.gene_mapping_status == "resolved"
    assert (result.protein_namespace, result.protein_identifier) == ("refseq", "NP_000537.3")
    assert result.protein_node_id is None
    assert observation.molecular_form["sequence_identifiers"] == [
        dict(ns="refseq", id="NP_000537.3")
    ]


@pytest.mark.parametrize("scope", ["", "9606"], ids=["global", "human"])
@pytest.mark.parametrize("identifier", ["NP_000537", "NP_000537.3"])
def test_legacy_refseq_identity_also_stays_ambiguous(scope, identifier):
    lookup_key = key(2, 1, "refseq_protein", scope, identifier)
    candidates = RefSeqRuntime().lookup(lookup_key)["candidates"]
    result = resolve_precomputed_batch(
        [("input", 2, [(lookup_key, "", 1, 0, False, False)])],
        [(lookup_key, [fact[0] for fact in candidates])],
        [fact[:6] for fact in candidates],
    )[0]
    assert result[1] == "Ambiguous"
    assert result[2] == []
    assert result[3] == 3
