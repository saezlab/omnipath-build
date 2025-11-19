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
    CausalMechanismCv,
    CausalStatementCv,
    PharmacologicalActionCv,
    BiologicalRoleCv,
    InteractionParameterCv,
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
COMPLEX_TYPE_ACCESSION = EntityTypeCv.PROTEIN_COMPLEX.value
PROTEIN_TYPE_ACCESSION = EntityTypeCv.PROTEIN.value
GENE_TYPE_ACCESSION = EntityTypeCv.GENE.value
SMALL_MOLECULE_TYPE_ACCESSION = EntityTypeCv.SMALL_MOLECULE.value

# CV_TERM_ACCESSION identifier type (used for identifying CV terms)
CV_TERM_ACCESSION_TYPE = IdentifierNamespaceCv.CV_TERM_ACCESSION.value

# =============================================================================
# Interaction Annotation CV Term Sets (for categorization)
# =============================================================================

# Interaction type terms
INTERACTION_TYPE_ACCESSIONS = frozenset({
    InteractionTypeCv.COLOCALIZATION.value,
    InteractionTypeCv.FUNCTIONAL_ASSOCIATION.value,
    InteractionTypeCv.PHYSICAL_ASSOCIATION.value,
    InteractionTypeCv.DIRECT_INTERACTION.value,
    InteractionTypeCv.PHOSPHORYLATION_REACTION.value,
    InteractionTypeCv.PHENOTYPE_RESULT.value,
})

# Detection method terms
DETECTION_METHOD_ACCESSIONS = frozenset({
    DetectionMethodCv.AFFINITY_CHROMATOGRAPHY.value,
    DetectionMethodCv.COIMMUNOPRECIPITATION.value,
    DetectionMethodCv.PULL_DOWN.value,
    DetectionMethodCv.INFERRED_BY_CURATOR.value,
})

# Biological effect terms
BIOLOGICAL_EFFECT_ACCESSIONS = frozenset({
    BiologicalEffectCv.UP_REGULATES_ACTIVITY.value,
    BiologicalEffectCv.DOWN_REGULATES_ACTIVITY.value,
    BiologicalEffectCv.UP_REGULATES_QUANTITY.value,
    BiologicalEffectCv.DOWN_REGULATES_QUANTITY.value,
})

# Causal statement terms
CAUSAL_STATEMENT_ACCESSIONS = frozenset({
    CausalStatementCv.DOWN_REGULATES.value,
    CausalStatementCv.DOWN_REGULATES_ACTIVITY.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_DESTABLIZATION.value,
    CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_REPRESSION.value,
    CausalStatementCv.UP_REGULATES.value,
    CausalStatementCv.UP_REGULATES_ACTIVITY.value,
    CausalStatementCv.UP_REGULATES_QUANTITY.value,
    CausalStatementCv.UP_REGULATES_QUANTITY_BY_EXPRESSION.value,
    CausalStatementCv.UP_REGULATES_QUANTITY_BY_STABILIZATION.value,
})

# Causal mechanism terms
CAUSAL_MECHANISM_ACCESSIONS = frozenset({
    CausalMechanismCv.TRANSCRIPTIONAL_REGULATION.value,
    CausalMechanismCv.TRANSLATION_REGULATION.value,
    CausalMechanismCv.POST_TRANSLATIONAL_REGULATION.value,
})

# Pharmacological action terms
PHARMACOLOGICAL_ACTION_ACCESSIONS = frozenset({
    PharmacologicalActionCv.AGONIST.value,
    PharmacologicalActionCv.FULL_AGONIST.value,
    PharmacologicalActionCv.PARTIAL_AGONIST.value,
    PharmacologicalActionCv.INVERSE_AGONIST.value,
    PharmacologicalActionCv.BIASED_AGONIST.value,
    PharmacologicalActionCv.IRREVERSIBLE_AGONIST.value,
    PharmacologicalActionCv.ANTAGONIST.value,
    PharmacologicalActionCv.COMPETITIVE.value,
    PharmacologicalActionCv.NON_COMPETITIVE.value,
    PharmacologicalActionCv.ACTIVATION.value,
    PharmacologicalActionCv.INHIBITION.value,
    PharmacologicalActionCv.IRREVERSIBLE_INHIBITION.value,
    PharmacologicalActionCv.FEEDBACK_INHIBITION.value,
    PharmacologicalActionCv.POSITIVE.value,
    PharmacologicalActionCv.NEGATIVE.value,
    PharmacologicalActionCv.POTENTIATION.value,
    PharmacologicalActionCv.NEUTRAL.value,
    PharmacologicalActionCv.PORE_BLOCKER.value,
    PharmacologicalActionCv.SLOWS_INACTIVATION.value,
    PharmacologicalActionCv.VOLTAGE_DEPENDENT_INHIBITION.value,
    PharmacologicalActionCv.BINDING.value,
    PharmacologicalActionCv.BIPHASIC.value,
    PharmacologicalActionCv.MIXED.value,
    PharmacologicalActionCv.UNKNOWN.value,
    PharmacologicalActionCv.NONE.value,
})

# Biological role terms (excluding experimental roles like BAIT/PREY)
BIOLOGICAL_ROLE_ACCESSIONS = frozenset({
    BiologicalRoleCv.ENZYME.value,
    BiologicalRoleCv.SUBSTRATE.value,
    BiologicalRoleCv.INHIBITOR.value,
    BiologicalRoleCv.STIMULATOR.value,
    BiologicalRoleCv.ALLOSTERIC_EFFECTOR.value,
    BiologicalRoleCv.REGULATOR_TARGET.value,
})

# Affinity measurement annotation terms
AFFINITY_ANNOTATION_ACCESSIONS = frozenset({
    MoleculeAnnotationsCv.AFFINITY_HIGH.value,
    MoleculeAnnotationsCv.AFFINITY_LOW.value,
    MoleculeAnnotationsCv.AFFINITY_MEDIAN.value,
})

# Interaction parameter terms
INTERACTION_PARAMETER_ACCESSIONS = frozenset({
    InteractionParameterCv.KI.value,
    InteractionParameterCv.KD.value,
    InteractionParameterCv.IC50.value,
    InteractionParameterCv.EC50.value,
    InteractionParameterCv.KON.value,
    InteractionParameterCv.KOFF.value,
    InteractionParameterCv.PH.value,
    InteractionParameterCv.TEMPERATURE.value,
    InteractionParameterCv.TEMPERATURE_CELSIUS.value,
})

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
})

# Source/actor roles (imply the member is the source/actor in the interaction)
SOURCE_ROLE_ACCESSIONS = frozenset({
    BiologicalRoleCv.ENZYME.value,
    BiologicalRoleCv.INHIBITOR.value,
    BiologicalRoleCv.STIMULATOR.value,
    BiologicalRoleCv.ALLOSTERIC_EFFECTOR.value,
})

# Target roles (imply the member is the target in the interaction)
TARGET_ROLE_ACCESSIONS = frozenset({
    BiologicalRoleCv.SUBSTRATE.value,
    BiologicalRoleCv.REGULATOR_TARGET.value,
})


# =============================================================================
# CV Term Mapping Builder
# =============================================================================

def build_cv_term_mapping(entity_identifiers_path: Path) -> pl.DataFrame:
    """Build mapping from CV term accessions to entity_ids.

    Args:
        entity_identifiers_path: Path to entity_identifier.parquet

    Returns:
        DataFrame with columns [accession, entity_id] where:
        - accession: CV term accession string (e.g., "MI:0326", "OM:0202")
        - entity_id: Corresponding entity_id in the global tables
    """
    identifiers = pl.read_parquet(entity_identifiers_path)
    type_dtype = identifiers.schema.get('type_id')

    if type_dtype is None:
        raise ValueError("entity_identifier table is missing 'type_id' column")

    # Determine the filter expression depending on whether type_id stores string accessions
    # or the already-mapped integer entity IDs.
    if type_dtype == pl.Utf8:
        filter_expr = pl.col('type_id') == CV_TERM_ACCESSION_TYPE
    elif type_dtype in {pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64}:
        cv_term_accession_entity = identifiers.filter(pl.col('identifier') == CV_TERM_ACCESSION_TYPE)
        if cv_term_accession_entity.is_empty():
            raise ValueError(
                f"CV_TERM_ACCESSION identifier ({CV_TERM_ACCESSION_TYPE}) not found "
                f"in {entity_identifiers_path}"
            )
        cv_term_accession_entity_id = cv_term_accession_entity['entity_id'][0]
        filter_expr = pl.col('type_id') == cv_term_accession_entity_id
    else:
        raise TypeError(f"Unsupported type_id dtype '{type_dtype}' in entity_identifier table")

    cv_term_rows = identifiers.filter(filter_expr)

    if cv_term_rows.is_empty():
        raise ValueError(
            f"No identifiers found for CV_TERM_ACCESSION ({CV_TERM_ACCESSION_TYPE}) "
            f"in {entity_identifiers_path}"
        )

    cv_terms = (
        cv_term_rows
        .select([
            pl.col('identifier').alias('accession'),
            'entity_id',
        ])
        .unique(subset=['accession'])
    )

    return cv_terms


def build_accession_to_entity_id_sets(cv_term_mapping: pl.DataFrame) -> dict[str, frozenset[int]]:
    """Convert accession sets to entity_id sets for filtering.

    Args:
        cv_term_mapping: DataFrame with [accession, entity_id] from build_cv_term_mapping()

    Returns:
        Dictionary mapping category name to frozenset of entity_ids:
        - 'names': Entity IDs for name identifier types
        - 'synonyms': Entity IDs for synonym identifier types
        - 'gene_symbols': Entity IDs for gene symbol identifier types
        - 'descriptions': Entity IDs for description annotation types
        - 'references': Entity IDs for reference identifier types
        - 'ncbi_tax_id': Entity ID for NCBI taxonomy ID
        - 'interaction_type': Entity ID for interaction type
        - 'cv_term_type': Entity ID for CV term type
        - 'complex_type': Entity ID for complex type
    """
    mapping_dict = cv_term_mapping.select(['accession', 'entity_id']).to_dict(as_series=False)
    accession_to_id = {acc: eid for acc, eid in zip(mapping_dict['accession'], mapping_dict['entity_id'])}

    def _accessions_to_ids(accessions: frozenset[str]) -> frozenset[int]:
        """Convert set of accessions to set of entity_ids."""
        return frozenset(accession_to_id[acc] for acc in accessions if acc in accession_to_id)

    return {
        'names': _accessions_to_ids(NAME_IDENTIFIER_ACCESSIONS),
        'synonyms': _accessions_to_ids(SYNONYM_IDENTIFIER_ACCESSIONS),
        'gene_symbols': _accessions_to_ids(GENE_SYMBOL_IDENTIFIER_ACCESSIONS),
        'descriptions': _accessions_to_ids(DESCRIPTION_ANNOTATION_ACCESSIONS),
        'references': _accessions_to_ids(REFERENCE_IDENTIFIER_ACCESSIONS),
        'ncbi_tax_id': frozenset([accession_to_id.get(IdentifierNamespaceCv.NCBI_TAX_ID.value)]),
        'interaction_type': accession_to_id.get(INTERACTION_TYPE_ACCESSION),
        'cv_term_type': accession_to_id.get(CV_TERM_TYPE_ACCESSION),
        'complex_type': accession_to_id.get(COMPLEX_TYPE_ACCESSION),
    }


# =============================================================================
# Entity Type Formatting
# =============================================================================

def build_entity_type_label_mapping(
    entity_identifiers_path: Path,
    cv_term_mapping: pl.DataFrame,
) -> dict[str, str]:
    """Build mapping from entity type accessions to their display names.

    This dynamically fetches the NAME identifier for each CV term entity
    instead of using hardcoded labels.

    Args:
        entity_identifiers_path: Path to entity_identifier.parquet
        cv_term_mapping: DataFrame with [accession, entity_id] from build_cv_term_mapping()

    Returns:
        Dictionary mapping accession -> display name (e.g., "MI:0326" -> "protein")
    """
    identifiers = pl.read_parquet(entity_identifiers_path)

    # Get the NAME identifier type entity_id
    name_type_rows = identifiers.filter(
        pl.col('identifier') == IdentifierNamespaceCv.NAME.value
    )

    if name_type_rows.is_empty():
        # Fallback to accession if NAME type not found
        return {row['accession']: row['accession'] for row in cv_term_mapping.iter_rows(named=True)}

    name_type_entity_id = name_type_rows['entity_id'][0]

    # Get all entity_type_ids from cv_term_mapping
    entity_type_ids = cv_term_mapping['entity_id'].to_list()

    # Get NAME identifiers for entity types
    entity_type_names = (
        identifiers
        .filter(
            (pl.col('entity_id').is_in(entity_type_ids)) &
            (pl.col('type_id') == name_type_entity_id)
        )
        .select(['entity_id', 'identifier'])
    )

    # Join with cv_term_mapping to get accession -> name mapping
    accession_to_name = (
        cv_term_mapping
        .join(entity_type_names, on='entity_id', how='left')
        .select(['accession', 'identifier'])
    )

    # Build dictionary, using accession as fallback if no name found
    # Also format names: capitalize first letter, remove spaces and hyphens
    mapping = {}
    for row in accession_to_name.iter_rows(named=True):
        accession = row['accession']
        name = row['identifier']
        if name:
            # Format name: capitalize each word and remove spaces/hyphens
            # (e.g., "protein complex" -> "ProteinComplex", "cross-reference type" -> "CrossReferenceType")
            formatted_name = ''.join(word.capitalize() for word in name.replace('-', ' ').split())
            mapping[accession] = formatted_name
        else:
            mapping[accession] = accession

    return mapping
