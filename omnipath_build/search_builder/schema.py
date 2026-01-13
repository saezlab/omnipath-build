"""Schema definitions and CV term mappings for Meilisearch entity documents.

This module defines:
1. CV term accession constants (frozen sets) for categorizing identifiers
2. Helper functions to build CV term accession -> entity_id mappings
3. Meilisearch document field specifications
"""
from __future__ import annotations

import polars as pl
from pathlib import Path

# Import CV term enums for accession values
from pypath.internals.cv_terms.entity_types import EntityTypeCv
from pypath.internals.cv_terms.identifiers import IdentifierNamespaceCv
from pypath.internals.cv_terms.annotations import (
    MoleculeAnnotationsCv,
    InteractionTypeCv,
    DetectionMethodCv,
    BiologicalEffectCv,
    OntologyAnnotationCv,
    CausalMechanismCv,
    CausalStatementCv,
    PharmacologicalActionCv,
    BiologicalRoleCv,
    InteractionParameterCv,
    ParticipantMetadataCv,
    LigandTypeCv,
)


# =============================================================================
# CV Term Accession Sets (for filtering/categorization)
# =============================================================================

# Identifier types that should be displayed as "Names"
NAME_IDENTIFIER_ACCESSIONS = frozenset({
    IdentifierNamespaceCv.NAME.value,
})

# Identifier types that should be displayed as "Synonyms"
SYNONYM_IDENTIFIER_ACCESSIONS = frozenset({
    IdentifierNamespaceCv.SYNONYM.value,
    IdentifierNamespaceCv.GENE_NAME_SYNONYM.value,
    IdentifierNamespaceCv.SYSTEMATIC_NAME.value,
    IdentifierNamespaceCv.ABBREVIATED_NAME.value,
})

# Identifier types that should be displayed as "Gene Symbols"
GENE_SYMBOL_IDENTIFIER_ACCESSIONS = frozenset({
    IdentifierNamespaceCv.GENE_NAME_PRIMARY.value,
})

# Annotation term accessions that represent descriptions
DESCRIPTION_ANNOTATION_ACCESSIONS = frozenset({
    MoleculeAnnotationsCv.DESCRIPTION.value,
    OntologyAnnotationCv.DEFINITION.value,
    MoleculeAnnotationsCv.FUNCTION.value,
    MoleculeAnnotationsCv.SUBCELLULAR_LOCATION.value,
    MoleculeAnnotationsCv.DISEASE_INVOLVEMENT.value,
    MoleculeAnnotationsCv.PATHWAY_PARTICIPATION.value,
    MoleculeAnnotationsCv.ACTIVITY_REGULATION.value,
})

# Identifier type accessions that represent references/citations
REFERENCE_IDENTIFIER_ACCESSIONS = frozenset({
    IdentifierNamespaceCv.PUBMED.value,
    IdentifierNamespaceCv.PUBMED_CENTRAL.value,
    IdentifierNamespaceCv.DOI.value,
    IdentifierNamespaceCv.BIORXIV.value,
    IdentifierNamespaceCv.PATENT_NUMBER.value,
})

# Entity type accessions
INTERACTION_TYPE_ACCESSION = EntityTypeCv.INTERACTION.value
CV_TERM_TYPE_ACCESSION = EntityTypeCv.CV_TERM.value
COMPLEX_TYPE_ACCESSION = EntityTypeCv.COMPLEX.value
PROTEIN_TYPE_ACCESSION = EntityTypeCv.PROTEIN.value
GENE_TYPE_ACCESSION = EntityTypeCv.GENE.value
SMALL_MOLECULE_TYPE_ACCESSION = EntityTypeCv.SMALL_MOLECULE.value
PATHWAY_TYPE_ACCESSION = EntityTypeCv.PATHWAY.value
REACTION_TYPE_ACCESSION = EntityTypeCv.REACTION.value

# CV_TERM_ACCESSION identifier type (used for identifying CV terms)
CV_TERM_ACCESSION_TYPE = IdentifierNamespaceCv.CV_TERM_ACCESSION.value

# =============================================================================
# Reactome / Pathway / Reaction specific sets
# =============================================================================

REACTANT_ROLE_ACCESSIONS = frozenset({
    BiologicalRoleCv.REACTANT.value,
})

PRODUCT_ROLE_ACCESSIONS = frozenset({
    BiologicalRoleCv.PRODUCT.value,
})

STOICHIOMETRY_ANNOTATION_ACCESSIONS = frozenset({
    ParticipantMetadataCv.STOICHIOMETRY.value,
})

STEP_ORDER_ANNOTATION_ACCESSIONS = frozenset({
    ParticipantMetadataCv.STEP_ORDER.value,
})

# =============================================================================
# Interaction Annotation CV Term Sets (for categorization)
# =============================================================================

# Positive sign indicators (for sign detection)
POSITIVE_SIGN_ACCESSIONS = frozenset({
    CausalStatementCv.UP_REGULATES.value,
    CausalStatementCv.UP_REGULATES_ACTIVITY.value,
    CausalStatementCv.UP_REGULATES_QUANTITY.value,
    CausalStatementCv.UP_REGULATES_QUANTITY_BY_EXPRESSION.value,
    CausalStatementCv.UP_REGULATES_QUANTITY_BY_STABILIZATION.value,
    BiologicalEffectCv.UP_REGULATES_ACTIVITY.value,
    BiologicalEffectCv.UP_REGULATES_QUANTITY.value,
    BiologicalRoleCv.STIMULATOR.value,
    PharmacologicalActionCv.AGONIST.value,
    PharmacologicalActionCv.FULL_AGONIST.value,
    PharmacologicalActionCv.PARTIAL_AGONIST.value,
    PharmacologicalActionCv.BIASED_AGONIST.value,
    PharmacologicalActionCv.ACTIVATION.value,
    PharmacologicalActionCv.POSITIVE.value,
    PharmacologicalActionCv.POTENTIATION.value,
    LigandTypeCv.ACTIVATOR.value,
    LigandTypeCv.AGONIST.value,
})

# Negative sign indicators (for sign detection)
NEGATIVE_SIGN_ACCESSIONS = frozenset({
    CausalStatementCv.DOWN_REGULATES.value,
    CausalStatementCv.DOWN_REGULATES_ACTIVITY.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_DESTABLIZATION.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_REPRESSION.value,
    BiologicalEffectCv.DOWN_REGULATES_ACTIVITY.value,
    BiologicalEffectCv.DOWN_REGULATES_QUANTITY.value,
    BiologicalRoleCv.INHIBITOR.value,
    PharmacologicalActionCv.ANTAGONIST.value,
    PharmacologicalActionCv.INVERSE_AGONIST.value,
    PharmacologicalActionCv.INHIBITION.value,
    PharmacologicalActionCv.IRREVERSIBLE_INHIBITION.value,
    PharmacologicalActionCv.FEEDBACK_INHIBITION.value,
    PharmacologicalActionCv.NEGATIVE.value,
    PharmacologicalActionCv.PORE_BLOCKER.value,
    LigandTypeCv.INHIBITOR.value,
    LigandTypeCv.ANTAGONIST.value,
    LigandTypeCv.CHANNEL_BLOCKER.value,
    LigandTypeCv.GATING_INHIBITOR.value,
})

# Source/actor roles (imply the member is the source/actor in the interaction)
SOURCE_ROLE_ACCESSIONS = frozenset({
    BiologicalRoleCv.ENZYME.value,
    BiologicalRoleCv.REGULATOR.value,
    BiologicalRoleCv.INHIBITOR.value,
    BiologicalRoleCv.STIMULATOR.value,
    BiologicalRoleCv.ALLOSTERIC_EFFECTOR.value,
    LigandTypeCv.INHIBITOR.value,
    LigandTypeCv.ACTIVATOR.value,
    LigandTypeCv.AGONIST.value,
    LigandTypeCv.ANTAGONIST.value,
})

# Target roles (imply the member is the target in the interaction)
TARGET_ROLE_ACCESSIONS = frozenset({
    BiologicalRoleCv.SUBSTRATE.value,
    BiologicalRoleCv.REGULATOR_TARGET.value,
})

# Interaction parameters indicating small molecule -> protein directionality
INHIBITORY_PARAMETER_ACCESSIONS = frozenset({
    InteractionParameterCv.KI.value,
    InteractionParameterCv.IC50.value,
})

ACTIVATORY_PARAMETER_ACCESSIONS = frozenset({
    InteractionParameterCv.EC50.value,
})



# =============================================================================
# CV Term Mapping Builder
# =============================================================================


# (CV term mapping logic removed)



def get_cv_term_accession_sets() -> dict[str, frozenset[str] | str | None]:
    """Return dictionary of CV term accession sets for filtering.
    
    Returns:
        Dictionary mapping category name to frozenset of accessions strings:
    """
    return {
        'names': NAME_IDENTIFIER_ACCESSIONS,
        'synonyms': SYNONYM_IDENTIFIER_ACCESSIONS,
        'gene_symbols': GENE_SYMBOL_IDENTIFIER_ACCESSIONS,
        'descriptions': DESCRIPTION_ANNOTATION_ACCESSIONS,
        'references': REFERENCE_IDENTIFIER_ACCESSIONS,
        'ncbi_tax_id': frozenset([IdentifierNamespaceCv.NCBI_TAX_ID.value]),
        'interaction_type': INTERACTION_TYPE_ACCESSION,
        'cv_term_type': CV_TERM_TYPE_ACCESSION,
        'complex_type': COMPLEX_TYPE_ACCESSION,
        'pathway_type': PATHWAY_TYPE_ACCESSION,
        'reaction_type': REACTION_TYPE_ACCESSION,
        'reactants': REACTANT_ROLE_ACCESSIONS,
        'products': PRODUCT_ROLE_ACCESSIONS,
        'stoichiometry': STOICHIOMETRY_ANNOTATION_ACCESSIONS,
        'pathway_steps': STEP_ORDER_ANNOTATION_ACCESSIONS,
    }


# =============================================================================
# Entity Type Formatting
# =============================================================================

def build_entity_type_label_mapping() -> dict[str, str]:
    """Build mapping from entity type accessions to their display names.
    
    Since CV terms are no longer in the DB, this logic is simplified or placeholder.
    TODO: Use ontograph to fetch real labels.
    """
    # Placeholder mapping
    return {}
