"""Utilities for building identifier lists from source records."""

from typing import Any, Callable
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv
from omnipath_build.utils.silver_schema import Identifier


def build_identifiers(
    record: Any,
    mapping: dict[str, IdentifierNamespaceCv],
    transformers: dict[str, Callable] | None = None,
    filters: dict[str, Callable] | None = None,
    accession_attr: str | None = None,
) -> list[Identifier] | None:
    """
    Build list of identifiers from a record object.

    Args:
        record: Object with identifier attributes
        mapping: Dict mapping attr_name -> IdentifierNamespaceCv (e.g. {'inchi': IdentifierNamespaceCv.INCHI})
        transformers: Optional dict of attr_name -> transformer functions for values
        filters: Optional dict of attr_name -> filter functions (return False to skip)
        accession_attr: If provided, adds an ACCESSION identifier using this attribute

    Returns:
        List of Identifier objects, or None if empty

    Example:
        # Define mapping in resource file
        LIPIDMAPS_IDENTIFIERS = {
            'id': IdentifierNamespaceCv.LIPIDMAPS,
            'inchikey': IdentifierNamespaceCv.INCHIKEY,
            'inchi': IdentifierNamespaceCv.INCHI,
            'smiles': IdentifierNamespaceCv.SMILES,
            'chebi': IdentifierNamespaceCv.CHEBI,
            'pubchem': IdentifierNamespaceCv.PUBCHEM,
        }

        identifiers = build_identifiers(
            rec,
            mapping=LIPIDMAPS_IDENTIFIERS,
            accession_attr='id'
        )
    """
    transformers = transformers or {}
    filters = filters or {}
    identifiers = []

    # Add ACCESSION first if specified
    if accession_attr:
        accession_value = getattr(record, accession_attr, None)
        if accession_value:
            # Apply transformer if exists
            if accession_attr in transformers:
                accession_value = transformers[accession_attr](accession_value)
            # Convert to string for numeric values
            if isinstance(accession_value, (int, float)):
                accession_value = str(accession_value)
            identifiers.append(Identifier(type=IdentifierNamespaceCv.ACCESSION, value=accession_value))

    # Add other identifiers
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

        # Convert to string for numeric values
        if isinstance(value, (int, float)):
            value = str(value)

        identifiers.append(Identifier(type=id_type, value=value))

    return identifiers if identifiers else None
