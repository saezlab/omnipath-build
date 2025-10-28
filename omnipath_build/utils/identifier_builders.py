"""Utilities for building identifier lists from source records."""

from typing import Any, Callable
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv
from omnipath_build.utils.silver_schema import Identifier


def build_identifiers(
    record: Any,
    mapping: dict[str, IdentifierNamespaceCv],
    transformers: dict[str, Callable] | None = None,
    filters: dict[str, Callable] | None = None,
) -> list[Identifier] | None:
    """
    Build list of identifiers from a record object.

    Args:
        record: Object with identifier attributes
        mapping: Dict mapping attr_name -> IdentifierNamespaceCv (e.g. {'inchi': IdentifierNamespaceCv.STANDARD_INCHI})
        transformers: Optional dict of attr_name -> transformer functions for values
        filters: Optional dict of attr_name -> filter functions (return False to skip)

    Returns:
        List of Identifier objects, or None if empty

    Example:
        # Define mapping in resource file
        LIPIDMAPS_IDENTIFIERS = {
            'id': IdentifierNamespaceCv.LIPIDMAPS,
            'inchikey': IdentifierNamespaceCv.STANDARD_INCHI_KEY,
            'inchi': IdentifierNamespaceCv.STANDARD_INCHI,
            'smiles': IdentifierNamespaceCv.SMILES,
            'chebi': IdentifierNamespaceCv.CHEBI,
            'pubchem': IdentifierNamespaceCv.PUBCHEM_COMPOUND,
        }

        identifiers = build_identifiers(
            rec,
            mapping=LIPIDMAPS_IDENTIFIERS,
        )
    """
    transformers = transformers or {}
    filters = filters or {}
    identifiers = []

    # Add identifiers from mapping
    for attr_name, id_type in mapping.items():
        value = getattr(record, attr_name, None)

        if not value:
            continue

        # Apply filter if exists
        if attr_name in filters and not filters[attr_name](value):
            continue

        # Apply transformer if exists
        if attr_name in transformers:
            value = transformers[attr_name](value)

        # Handle list values (e.g., synonyms) - create one identifier per item
        if isinstance(value, list):
            for item in value:
                if item:  # Skip empty items
                    identifiers.append(Identifier(type=id_type, value=str(item)))
        else:
            # Convert to string for numeric values
            if isinstance(value, (int, float)):
                value = str(value)
            identifiers.append(Identifier(type=id_type, value=value))

    return identifiers if identifiers else None
