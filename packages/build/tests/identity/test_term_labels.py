"""Ontology hub -> term_labels.parquet -> the resolver names terms cited by id alone."""

import pyarrow.parquet as pq

from omnipath_build.hubs.sources.ontology import emit
from omnipath_build.hubs.writer import HubParquetWriter
from omnipath_build.identity.common import Context
from omnipath_build.identity.decisions import find_term_index, stage_term_labels
from omnipath_build.identity.hubindex import build_hub_index

GO = """format-version: 1.2

[Term]
id: GO:0001675
name: acrosome assembly
alt_id: GO:0000001

[Term]
id: GO:0000001
name: mitochondrion inheritance

[Term]
id: GO:0000002
name: obsolete term
is_obsolete: true
"""
CHEMONT = """[Term]
id: CHEMONTID:0000002
name: Organoheterocyclic compounds
"""


def test_term_labels_from_the_ontology_hub(tmp_path):
    hubs = tmp_path / "hubs"
    hubs.mkdir()
    writer = HubParquetWriter(hubs / "ontology.parquet")
    emit(writer, {"go": GO.splitlines(), "chemont": CHEMONT.splitlines(), "hpo": [], "mondo": [], "psi_mi": []})
    writer.close()
    build_hub_index("ontology", hubs, tmp_path / "index", memory="1GB", threads=1, min_free_gib=0)
    index = find_term_index(tmp_path / "index")
    out = tmp_path / "identity"
    stage_term_labels(Context(out, "1GB", 1, None, 0), out, index, out)
    labels = dict(zip(*pq.read_table(out / "term_labels.parquet").to_pydict().values()))
    # a term's own id wins over another term's alt_id; obsolete terms are not named
    assert labels == {
        "GO:0001675": "acrosome assembly",
        "GO:0000001": "mitochondrion inheritance",
        "CHEMONTID:0000002": "Organoheterocyclic compounds",
    }


def test_resolver_names_terms_cited_by_id_alone():
    from omnipath_resolver.canonical.match import LibraryMatcher
    from omnipath_resolver.contracts import RawEntityObservation

    class Runtime:
        libraries = ()

        @staticmethod
        def term_label(term):
            return {"GO:0001675": "acrosome assembly"}.get(term)

        def close(self):
            pass

    matcher = LibraryMatcher(None)
    matcher.runtime = Runtime()
    observations = {
        "bare": RawEntityObservation(
            entity_key="bare", entity_type="ontology_class", namespace="go", identifier="GO:0001675"
        ),
        "unknown": RawEntityObservation(
            entity_key="unknown", entity_type="ontology_class", namespace="go", identifier="GO:9999999"
        ),
    }
    results = matcher.targets(observations)
    assert results["bare"][0].label == "acrosome assembly"
    assert results["unknown"][0].label == "GO:9999999"
