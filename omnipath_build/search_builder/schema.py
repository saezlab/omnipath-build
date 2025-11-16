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
from pypath.internals.cv_terms.annotations import MoleculeAnnotationsCv


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

    # Find the entity_id for CV_TERM_ACCESSION (OM:0204)
    cv_term_accession_entity = identifiers.filter(
        pl.col('identifier') == CV_TERM_ACCESSION_TYPE
    )

    if len(cv_term_accession_entity) == 0:
        raise ValueError(f"CV_TERM_ACCESSION ({CV_TERM_ACCESSION_TYPE}) not found in entity_identifier table")

    cv_term_accession_entity_id = cv_term_accession_entity['entity_id'][0]

    # Get all CV term accessions (identifiers with type_id == cv_term_accession_entity_id)
    cv_terms = (
        identifiers
        .filter(pl.col('type_id') == cv_term_accession_entity_id)
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
        'interaction_type': accession_to_id.get(INTERACTION_TYPE_ACCESSION),
        'cv_term_type': accession_to_id.get(CV_TERM_TYPE_ACCESSION),
        'complex_type': accession_to_id.get(COMPLEX_TYPE_ACCESSION),
    }


# =============================================================================
# Entity Type Formatting
# =============================================================================

ENTITY_TYPE_LABELS = {
    EntityTypeCv.PROTEIN.value: "Protein",
    EntityTypeCv.GENE.value: "Gene",
    EntityTypeCv.RNA.value: "RNA",
    EntityTypeCv.PROTEIN_COMPLEX.value: "Complex",
    EntityTypeCv.SMALL_MOLECULE.value: "SmallMolecule",
    EntityTypeCv.PHENOTYPE.value: "Phenotype",
    EntityTypeCv.STIMULUS.value: "Stimulus",
    EntityTypeCv.PROTEIN_FAMILY.value: "ProteinFamily",
    EntityTypeCv.CV_TERM.value: "CVTerm",
}


def format_entity_type_label(accession: str) -> str:
    """Convert entity type accession to Meilisearch-friendly label.

    Args:
        accession: Entity type accession (e.g., "MI:0326")

    Returns:
        Formatted label (e.g., "Protein")
    """
    return ENTITY_TYPE_LABELS.get(accession, accession)
