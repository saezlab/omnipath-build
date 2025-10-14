"""
Generic cleaning and normalization functions.
These are reusable across all sources.
"""
from typing import Optional, List, Dict, Any

__all__ = [
    'build_annotations',
    'build_cross_references',
    'clean_synonyms',
    'normalize_id',
    'normalize_inchi',
    'normalize_inchikey',
    'normalize_smiles',
]


def normalize_inchikey(value: Optional[str]) -> Optional[str]:
    """Normalize InChIKey format."""
    if not value:
        return None
    # Remove whitespace, uppercase
    cleaned = value.strip().upper()
    # Validate format (14-10-1 character blocks)
    if len(cleaned) == 27 and cleaned[14] == '-' and cleaned[25] == '-':
        return cleaned
    return None


def normalize_inchi(value: Optional[str]) -> Optional[str]:
    """Normalize InChI format."""
    if not value:
        return None
    cleaned = value.strip()
    # Ensure starts with InChI=
    if not cleaned.startswith('InChI='):
        return None
    return cleaned


def normalize_smiles(value: Optional[str]) -> Optional[str]:
    """Normalize SMILES string."""
    if not value:
        return None
    return value.strip()


def normalize_id(value: Optional[str], prefix: Optional[str] = None) -> Optional[str]:
    """Normalize identifier with optional prefix."""
    if not value:
        return None
    cleaned = value.strip()
    if prefix and not cleaned.startswith(prefix):
        cleaned = f"{prefix}{cleaned}"
    return cleaned


def clean_synonyms(synonyms: Optional[List[str]]) -> Optional[List[str]]:
    """Clean and deduplicate synonym list."""
    if synonyms is None or (hasattr(synonyms, '__len__') and len(synonyms) == 0):
        return None
    # Remove empty, strip whitespace, deduplicate
    cleaned = list(set(s.strip() for s in synonyms if s and str(s).strip()))
    return cleaned if cleaned else None


def build_cross_references(
    chebi: Optional[str] = None,
    pubchem_compound: Optional[str] = None,
    kegg: Optional[str] = None,
    drugbank: Optional[str] = None,
    cas: Optional[str] = None,
    **kwargs
) -> Optional[List[Dict[str, str]]]:
    """Build cross-references list from identifier fields."""
    refs = []

    if chebi:
        refs.append({"type": "chebi", "value": normalize_id(chebi, "CHEBI:")})
    if pubchem_compound:
        refs.append({"type": "pubchem_compound", "value": str(pubchem_compound)})
    if kegg:
        refs.append({"type": "kegg_compound", "value": normalize_id(kegg, "C")})
    if drugbank:
        refs.append({"type": "drugbank", "value": drugbank})
    if cas:
        refs.append({"type": "cas", "value": cas})

    return refs if refs else None


def build_annotations(
    molecular_weight: Optional[str] = None,
    average_molecular_weight: Optional[str] = None,
    chemical_formula: Optional[str] = None,
    iupac_name: Optional[str] = None,
    **kwargs
) -> Optional[List[Dict[str, Any]]]:
    """Build annotations list from property fields."""
    annots = []

    if molecular_weight:
        annots.append({
            "term": "monoisotopic_molecular_weight",
            "value": molecular_weight,
            "units": "Da"
        })
    if average_molecular_weight:
        annots.append({
            "term": "average_molecular_weight",
            "value": average_molecular_weight,
            "units": "Da"
        })
    if chemical_formula:
        annots.append({
            "term": "chemical_formula",
            "value": chemical_formula
        })
    if iupac_name:
        annots.append({
            "term": "iupac_name",
            "value": iupac_name
        })

    return annots if annots else None
