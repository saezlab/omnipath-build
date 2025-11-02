"""OmniPath ontology resource - emits OmniPath-specific CV terms as entities."""

import inspect
from enum import Enum

from omnipath_build.utils.silver_schema import SilverEntity, Identifier
from omnipath_build.utils import cv_term_enums
from omnipath_build.utils.cv_term_enums import EntityTypeCv, IdentifierNamespaceCv

__all__ = [
    'omnipath_ontology',
]


def _format_enum_name(name: str) -> str:
    """Convert ENUM_NAME to a human readable title."""
    return name.replace("_", " ").title()


def omnipath_ontology():
    """
    Generate SilverEntity records for all OmniPath-specific CV terms.

    Automatically discovers all enum classes in cv_term_enums module and
    extracts OmniPath terms (those starting with "OM:").
    """
    # Discover all enum classes from the cv_term_enums module
    for name, obj in inspect.getmembers(cv_term_enums):
        # Skip non-enum classes
        if not (inspect.isclass(obj) and issubclass(obj, Enum) and obj is not Enum):
            continue

        # Iterate through enum members
        for member in obj:
            accession = member.value

            # Only process OmniPath terms (starting with "OM:")
            if not isinstance(accession, str) or not accession.startswith("OM:"):
                continue

            # Generate human-readable name from enum member name
            term_name = _format_enum_name(member.name)

            # Build identifiers list
            identifiers = [
                Identifier(type=IdentifierNamespaceCv.CV_TERM_ACCESSION, value=accession),
                Identifier(type=IdentifierNamespaceCv.NAME, value=term_name),
            ]

            yield SilverEntity(
                source='omnipath_ontology',
                entity_type=EntityTypeCv.CV_TERM,
                identifiers=identifiers,
            )
