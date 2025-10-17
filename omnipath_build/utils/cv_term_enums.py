"""PSI-MI controlled vocabulary helpers for silver schema fields.

Each enum maps a familiar label to the corresponding PSI-MI accession so
callers can work with descriptive names while we still capture the required
MI identifiers in the data model.
"""
from enum import Enum

__all__ = [
    'EntityTypeCv',
    'IdentifierNamespaceCv',
    'MemberIdTypeCv',
    'ParentIdentifierTypeCv',
    'BiologicalRoleCv',
    'ExperimentalRoleCv',
    'IdentificationMethodCv',
    'BiologicalEffectCv',
    'InteractionTypeCv',
    'DetectionMethodCv',
    'CausalMechanismCv',
    'CausalStatementCv',
    'ComplexExpansionCv',
    'ReferenceTypeCv',
]


class EntityTypeCv(str, Enum):
    """Common PSI-MI entity type terms."""

    PROTEIN = "MI:0326"
    GENE = "MI:0250"
    RNA = "MI:0320"
    COMPLEX = "MI:0314"
    SMALL_MOLECULE = "MI:0328"


class IdentifierNamespaceCv(str, Enum):
    """Identifier namespace terms backed by PSI-MI accessions."""

    ACCESSION = "MI:0360"
    UNIPROT = "MI:0486"
    ENTREZ = "MI:0477"
    ENSEMBL = "MI:0476"
    HGNC = "MI:1095"
    REFSEQ = "MI:0481"
    REFSEQ_PROTEIN = "MI:0481"
    CHEBI = "MI:0474"
    PUBCHEM = "MI:0730"
    PUBCHEM_COMPOUND = "MI:0730"
    CHEMBL = "MI:0967"
    DRUGBANK = "MI:2002"
    KEGG = "MI:0470"
    CAS = "MI:2011"
    INCHI = "MI:2010"
    INCHIKEY = "MI:0970"
    SMILES = "MI:2039"
    LIPIDMAPS = "MI:0489"
    HMDB = "MI:0489"
    METANETX = "MI:0489"

class BiologicalRoleCv(str, Enum):
    """Example biological role terms for interaction participants."""

    ENZYME = "MI:0501"
    SUBSTRATE = "MI:0502"
    INHIBITOR = "MI:0586"
    NEUTRAL_COMPONENT = "MI:0497"


class ExperimentalRoleCv(str, Enum):
    """Example experimental role terms."""

    BAIT = "MI:0496"
    PREY = "MI:0498"
    NEUTRAL_COMPONENT = "MI:0497"


class IdentificationMethodCv(str, Enum):
    """Participant identification method examples."""

    GENERIC = "MI:0002"
    MASS_SPECTROMETRY = "MI:0427"
    SEQUENCE_TAG = "MI:0102"


class BiologicalEffectCv(str, Enum):
    """Example biological effect terms describing causal outcomes."""

    UP_REGULATES_ACTIVITY = "MI:2236"
    DOWN_REGULATES_ACTIVITY = "MI:2241"
    UP_REGULATES_QUANTITY = "MI:2237"
    DOWN_REGULATES_QUANTITY = "MI:2242"


class InteractionTypeCv(str, Enum):
    """Example interaction type terms."""

    PHYSICAL_ASSOCIATION = "MI:0915"
    DIRECT_INTERACTION = "MI:0407"
    PHOSPHORYLATION = "MI:0217"
    CAUSAL_INTERACTION = "MI:2233"


class DetectionMethodCv(str, Enum):
    """Example experimental detection method terms."""

    AFFINITY_CHROMATOGRAPHY = "MI:0004"
    COIMMUNOPRECIPITATION = "MI:0019"
    PULL_DOWN = "MI:0096"


class CausalMechanismCv(str, Enum):
    """Example causal mechanism terms."""

    CAUSAL_REGULATORY_MECHANISM = "MI:2245"
    INDIRECT_CAUSAL_REGULATION = "MI:2246"
    DIRECT_CAUSAL_REGULATION = "MI:2250"


class CausalStatementCv(str, Enum):
    """Example causal statement terms."""

    CAUSAL_STATEMENT = "MI:2234"


class ComplexExpansionCv(str, Enum):
    """Example complex expansion strategies."""

    COMPLEX_EXPANSION = "MI:1059"
    MATRIX_EXPANSION = "MI:1061"


class ReferenceTypeCv(str, Enum):
    """Example reference source terms."""

    PUBMED = "MI:0446"
    PUBMED_CENTRAL = "MI:1042"
    DOI = "MI:0574"
    IMEX = "MI:0670"
