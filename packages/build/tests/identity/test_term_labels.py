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
BTO = """[Term]
id: BTO:0000007
name: HEK-293 cell

[Term]
id: BTO:0001416
name: umbilical vein endothelium
alt_id: BTO:0001414
"""
# a merged edition carries the terms of the ontologies it imports: they are not CL's
CL = """[Term]
id: CL:0002518
name: kidney epithelial cell

[Term]
id: UBERON:0002113
name: kidney (as CL imports it)

[Term]
id: CL:0000002
name: obsolete immortal cell line cell
is_obsolete: true
"""
# APO names its former ids (YPO:) as alt_ids; the file holds no foreign terms but the test pins the own-prefix rule
APO = """[Term]
id: APO:0000063
name: colony size
alt_id: YPO:0000063

[Term]
id: GO:0008150
name: biological_process

[Term]
id: APO:0000064
name: liquid culture appearance
is_obsolete: true
"""
UBERON = """[Term]
id: UBERON:0002113
name: kidney
"""


def test_term_labels_from_the_ontology_hub(tmp_path):
    hubs = tmp_path / "hubs"
    hubs.mkdir()
    writer = HubParquetWriter(hubs / "ontology.parquet")
    # streamed lines keep their line ends
    emit(
        writer,
        {
            "go": GO.splitlines(keepends=True),
            "chemont": CHEMONT.splitlines(keepends=True),
            "hpo": [],
            "mondo": [],
            "psi_mi": [],
            "bto": BTO.splitlines(keepends=True),
            "cl": CL.splitlines(keepends=True),
            "uberon": UBERON.splitlines(keepends=True),
            "apo": APO.splitlines(keepends=True),
        },
    )
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
        "BTO:0000007": "HEK-293 cell",
        "BTO:0001416": "umbilical vein endothelium",
        "BTO:0001414": "umbilical vein endothelium",
        "CL:0002518": "kidney epithelial cell",
        # only Uberon names Uberon terms, not the CL file that imports them
        "UBERON:0002113": "kidney",
        "APO:0000063": "colony size",
        "YPO:0000063": "colony size",
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
