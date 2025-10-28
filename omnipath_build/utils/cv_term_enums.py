"""PSI-MI controlled vocabulary helpers for silver schema fields.

Each enum maps a familiar label to the corresponding PSI-MI accession so
callers can work with descriptive names while we still capture the required
MI identifiers in the data model.
"""
from enum import Enum

__all__ = [
    'EntityTypeCv',
    'IdentifierNamespaceCv',
    'StructureRepresentationCv',
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
    PROTEIN_COMPLEX = "MI:0315"
    SMALL_MOLECULE = "MI:0328"
    PHENOTYPE = "MI:2261"
    STIMULUS = "MI:2260"

    # OmniPath-specific terms
    PROTEIN_FAMILY = "OM:0010"
    LIPID = "OM:0011"


class IdentifierNamespaceCv(str, Enum):
    """Identifier namespace terms backed by PSI-MI accessions."""

    UNIPROT = "MI:1097"
    ENTREZ = "MI:0477"
    ENSEMBL = "MI:0476"
    HGNC = "MI:1095"
    REFSEQ = "MI:0481"
    CHEBI = "MI:0474"
    PUBCHEM = "MI:0730"
    CHEMBL = "MI:1349"
    CHEMBL_COMPOUND = "MI:0967"
    CHEMBL_TARGET = "MI:1348"
    DRUGBANK = "MI:2002"
    KEGG = "MI:0470"
    KEGG_COMPOUND = "MI:2012"
    CAS = "MI:2011"
    PDB = "OM:0101"
    ALPHAFOLDDB = "OM:0102"
    INTACT = "OM:0103"
    BIOGRID = "OM:0104"
    COMPLEXPORTAL = "OM:0105"
    
    REFSEQ_PROTEIN = "OM:0001"
    PUBCHEM_COMPOUND = "OM:0002"
    LIPIDMAPS = "OM:0003"
    HMDB = "OM:0004"
    METANETX = "OM:0005"
    BINDINGDB = "OM:0006"
    SIGNOR = "OM:0007"
    GUIDETOPHARMA = "OM:0008"
    SWISSLIPIDS = "OM:0009"

    # Gene and protein name identifiers
    GENE_NAME_PRIMARY = "OM:0200"
    GENE_NAME_SYNONYM = "OM:0201"

    # Generic name identifiers (for any entity type)
    NAME = "OM:0202"
    SYNONYM = "OM:0203"

    #structure representations
    SMILES = "MI:0239"
    STANDARD_INCHI_KEY = "MI:1101"
    STANDARD_INCHI = "MI:2010"

class BiologicalRoleCv(str, Enum):
    """Example biological role terms for interaction participants."""

    ENZYME = "MI:0501"
    SUBSTRATE = "MI:0502"
    INHIBITOR = "MI:0586"
    STIMULATOR = "MI:0840"
    ALLOSTERIC_EFFECTOR = "MI:1160"
    REGULATOR_TARGET = "MI:2275"


class ExperimentalRoleCv(str, Enum):
    """Example experimental role terms."""

    BAIT = "MI:0496"
    PREY = "MI:0498"
    NEUTRAL_COMPONENT = "MI:0497"
    UNSPECIFIED_ROLE = "MI:0499"


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

    COLOCALIZATION = "MI:0403"
    FUNCTIONAL_ASSOCIATION = "MI:2286"
    PHYSICAL_ASSOCIATION = "MI:0915"
    DIRECT_INTERACTION = "MI:0407"
    PHOSPHORYLATION_REACTION = "MI:0217"
    PHENOTYPE_RESULT = "MI:2283"



class DetectionMethodCv(str, Enum):
    """Example experimental detection method terms."""

    AFFINITY_CHROMATOGRAPHY = "MI:0004"
    COIMMUNOPRECIPITATION = "MI:0019"
    PULL_DOWN = "MI:0096"
    INFERRED_BY_CURATOR = "MI:0364"


class CausalMechanismCv(str, Enum):
    """Example causal mechanism terms."""

    TRANSCRIPTIONAL_REGULATION = "MI:2247"
    TRANSLATION_REGULATION = "MI:2248"
    POST_TRANSLATIONAL_REGULATION = "MI:2249"


class CausalStatementCv(str, Enum):
    """Example causal statement terms."""

    DOWN_REGULATES = "MI:2240"
    DOWN_REGULATES_ACTIVITY = "MI:2241"
    DOWN_REGULATES_QUANTITY = "MI:2242"
    DOWN_REGULATES_QUANTITY_BY_DESTABLIZATION = "MI:2244"
    DOWN_REGULATES_QUANTITY_BY_REPRESSION = "MI:2243"
    UP_REGULATES = "MI:2235"
    UP_REGULATES_ACTIVITY = "MI:2236"
    UP_REGULATES_QUANTITY = "MI:2237"
    UP_REGULATES_QUANTITY_BY_EXPRESSION = "MI:2238"
    UP_REGULATES_QUANTITY_BY_STABILIZATION = "MI:2239"

class ComplexExpansionCv(str, Enum):
    """Example complex expansion strategies."""

    BIPARTITE_EXPANSION = "MI:1062"
    MATRIX_EXPANSION = "MI:1061"
    SPOKE_EXPANSION = "MI:1060"


class ReferenceTypeCv(str, Enum):
    """Example reference source terms."""

    PUBMED = "MI:0446"
    PUBMED_CENTRAL = "MI:1042"
    DOI = "MI:0574"
    BIORXIV = "MI:2347"
