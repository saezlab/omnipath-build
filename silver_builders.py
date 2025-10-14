"""
Builder functions for constructing silver tables.

These functions provide a clean, declarative interface for mapping
source data to silver schema.
"""
import polars as pl
from typing import Dict, Optional

__all__ = [
    'select_entities',
    'select_interactions',
]


def select_entities(
    df: pl.DataFrame,
    source: str,
    entity_type: str,
    accession: str,
    inchikey: Optional[str] = None,
    inchi: Optional[str] = None,
    smiles: Optional[str] = None,
    name: Optional[str] = None,
    synonyms: Optional[str] = None,
    cross_references: Optional[Dict[str, str]] = None,
    annotations: Optional[Dict[str, str]] = None,
    references: Optional[str] = None,
) -> pl.DataFrame:
    """
    Select and map columns to silver_entities schema.

    Args:
        df: Input dataframe with cleaned data
        source: Source name (constant, e.g., 'hmdb')
        entity_type: Entity type (constant, e.g., 'compound')
        accession: Column name for accession
        inchikey: Column name for InChIKey
        inchi: Column name for InChI
        smiles: Column name for SMILES
        name: Column name for primary name
        synonyms: Column name for synonyms list
        cross_references: Dict mapping reference types to column names
                         e.g., {'chebi': 'chebi_id', 'pubchem_compound': 'pubchem_compound_id'}
        annotations: Dict mapping annotation terms to column names
                    e.g., {'chemical_formula': 'chemical_formula', ...}
        references: Column name for references list (PMIDs)

    Returns:
        DataFrame with silver_entities schema

    Example:
        >>> silver_df = select_entities(
        ...     cleaned_df,
        ...     source='hmdb',
        ...     entity_type='compound',
        ...     accession='accession',
        ...     inchikey='inchikey',
        ...     cross_references={'chebi': 'chebi_id', 'kegg_compound': 'kegg_id'},
        ... )
    """
    selections = []

    # Required fields
    selections.append(pl.lit(source).alias('source'))
    selections.append(pl.col(accession).alias('accession'))
    selections.append(pl.lit(entity_type).alias('entity_type'))

    # Optional structural identifiers
    if inchikey:
        selections.append(pl.col(inchikey).alias('inchikey'))
    if inchi:
        selections.append(pl.col(inchi).alias('inchi'))
    if smiles:
        selections.append(pl.col(smiles).alias('smiles'))

    # Cross-references (build JSON from dict)
    if cross_references:
        xref_cols = list(cross_references.values())

        def build_xref_json(row_dict):
            refs = []
            for ref_type, col_name in cross_references.items():
                value = row_dict.get(col_name)
                if value is not None:
                    # Handle special cases
                    if ref_type == 'chebi' and not str(value).startswith('CHEBI:'):
                        value = f"CHEBI:{value}"
                    elif ref_type == 'kegg_compound' and not str(value).startswith('C'):
                        value = f"C{value}"

                    refs.append(f'{{"type":"{ref_type}","value":"{value}"}}')

            return f'[{",".join(refs)}]' if refs else None

        selections.append(
            pl.struct(xref_cols).map_elements(build_xref_json, return_dtype=pl.Utf8).alias('cross_references')
        )

    # Names
    if name:
        selections.append(pl.col(name).alias('name'))
    if synonyms:
        selections.append(pl.col(synonyms).alias('synonyms'))

    # Annotations (build JSON from dict)
    if annotations:
        annot_cols = list(annotations.values())

        def build_annot_json(row_dict):
            annots = []
            for term, col_name in annotations.items():
                value = row_dict.get(col_name)
                if value is not None:
                    # Escape quotes
                    value_str = str(value).replace('"', '\\"')
                    # Add units for weight fields
                    if 'weight' in term:
                        annots.append(f'{{"term":"{term}","value":"{value_str}","units":"Da"}}')
                    else:
                        annots.append(f'{{"term":"{term}","value":"{value_str}"}}')

            return f'[{",".join(annots)}]' if annots else None

        selections.append(
            pl.struct(annot_cols).map_elements(build_annot_json, return_dtype=pl.Utf8).alias('annotations')
        )

    # References
    if references:
        selections.append(pl.col(references).alias('references'))

    return df.select(selections)


def select_interactions(
    df: pl.DataFrame,
    source: str,
    entity_a_identifier: str,
    entity_a_identifier_type: str,
    entity_b_identifier: str,
    entity_b_identifier_type: str,
    entity_a_name: Optional[str] = None,
    entity_b_name: Optional[str] = None,
    interaction_type: Optional[str] = None,
    detection_method: Optional[str] = None,
    is_directed: Optional[bool] = None,
    direction: Optional[str] = None,
    sign: Optional[str] = None,
    causal_mechanism: Optional[str] = None,
    causal_statement: Optional[str] = None,
    sentence: Optional[str] = None,
    interaction_annotations: Optional[Dict[str, str]] = None,
    reference_type: Optional[str] = None,
    reference_value: Optional[str] = None,
) -> pl.DataFrame:
    """
    Select and map columns to silver_interactions schema.

    Similar to select_entities but for interactions.

    Args:
        df: Input dataframe with cleaned data
        source: Source name
        entity_a_identifier: Column name for entity A identifier
        entity_a_identifier_type: Type of entity A identifier (constant or column)
        entity_b_identifier: Column name for entity B identifier
        entity_b_identifier_type: Type of entity B identifier (constant or column)
        ... (other fields)

    Returns:
        DataFrame with silver_interactions schema
    """
    # TODO: Implement when needed for interaction sources
    raise NotImplementedError("select_interactions not yet implemented")
