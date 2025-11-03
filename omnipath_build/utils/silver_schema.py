"""
Canonical definitions for silver-layer schemas.

Keeping the PyArrow schema objects and the namedtuple helpers in one place
avoids accidental divergence across the pipeline.
"""
from typing import List, Dict, Any, NamedTuple
from enum import Enum

import pyarrow as pa

from omnipath_build.utils.cv_term_enums import (
    EntityTypeCv,
    IdentifierNamespaceCv,
    MembershipRoleCv,
    BiologicalRoleCv,
    ExperimentalRoleCv,
    IdentificationMethodCv,
    InteractionTypeCv,
    DetectionMethodCv,
    CausalMechanismCv,
    CausalStatementCv,
    ComplexExpansionCv,
    ReferenceTypeCv,
)

__all__ = [
    'Identifier',
    'SilverEntity',
    'SilverInteraction',
    'SilverCvTerm',
    'SILVER_ENTITY_SCHEMA',
    'SILVER_INTERACTION_SCHEMA',
    'SILVER_CV_TERM_SCHEMA',
]

class Member(NamedTuple):
    """Member of a complex or group entity."""

    identifier: str
    identifier_type: IdentifierNamespaceCv
    role: BiologicalRoleCv | None = None
    stoichiometry: float | None = None


class MemberOf(NamedTuple):
    """Parent entity that this entity is a member of (inverse of Member)."""

    identifier: str
    identifier_type: IdentifierNamespaceCv
    role: MembershipRoleCv | None = None


class Identifier(NamedTuple):
    """An identifier with its type."""

    type: IdentifierNamespaceCv
    value: str


class Reference(NamedTuple):
    """Reference for an interaction or entity."""

    type: ReferenceTypeCv
    value: str  # e.g., '12345678' for pmid


class SilverEntity(NamedTuple):
    """Silver entity record matching silver_entities schema."""

    # Required fields
    source: str
    entity_type: EntityTypeCv

    # All identifiers consolidated here using PSI-MI identifier namespaces
    # (including names and synonyms via IdentifierNamespaceCv.NAME and .SYNONYM)
    identifiers: List[Identifier] | None = None

    # Optional organism (NCBI Taxonomy ID)
    organism: int | None = None

    # Optional membership info
    members: List[Member] | None = None
    parent_identifier: str | None = None
    parent_identifier_type: IdentifierNamespaceCv | None = None
    is_member_of: List[MemberOf] | None = None

    # Optional annotations
    annotations: List[Dict[str, Any]] | None = None
    references: List[Reference] | None = None

    # Optional metadata (if provided by meta database)
    secondary_source: str | None = None

    # Optional interaction participant role information
    # These fields are only populated when the entity participates in an interaction
    biological_role: BiologicalRoleCv | None = None
    experimental_role: ExperimentalRoleCv | None = None
    stoichiometry: float | None = None
    identification_method: IdentificationMethodCv | None = None


class SilverInteraction(NamedTuple):
    """Cleaned interaction records (one row per source evidence record)."""

    # Required fields - metadata
    source: str

    # Required fields - interaction participants
    entity_a: SilverEntity
    entity_b: SilverEntity

    # Optional evidence details
    interaction_type: InteractionTypeCv | None = None
    detection_method: DetectionMethodCv | None = None
    causal_mechanism: CausalMechanismCv | None = None
    causal_statement: CausalStatementCv | None = None
    sentence: str | None = None

    complex_expansion: ComplexExpansionCv | None = None

    # Optional annotations
    interaction_annotations: List[Dict[str, Any]] | None = None

    # Optional reference
    references: List[Reference] | None = None


SILVER_ENTITY_FIELDS = [
    pa.field('source', pa.string(), nullable=False),
    pa.field('entity_type', pa.string(), nullable=False),
    pa.field(
        'identifiers',
        pa.list_(pa.struct([
            pa.field('type', pa.string()),
            pa.field('value', pa.string()),
        ])),
    ),
    pa.field('organism', pa.int64()),
    pa.field(
        'members',
        pa.list_(pa.struct([
            pa.field('identifier', pa.string()),
            pa.field('identifier_type', pa.string()),
            pa.field('role', pa.string()),
            pa.field('stoichiometry', pa.float64()),
        ])),
    ),
    pa.field('parent_accession', pa.string()),
    pa.field(
        'is_member_of',
        pa.list_(pa.struct([
            pa.field('identifier', pa.string()),
            pa.field('identifier_type', pa.string()),
            pa.field('role', pa.string()),
        ])),
    ),
    pa.field(
        'annotations',
        pa.list_(pa.struct([
            pa.field('term', pa.string()),
            pa.field('value', pa.string()),
            pa.field('units', pa.string()),
        ])),
    ),
    pa.field(
        'references',
        pa.list_(pa.struct([
            pa.field('type', pa.string()),
            pa.field('value', pa.string()),
        ])),
    ),
    pa.field('secondary_source', pa.string()),
]

SILVER_ENTITY_SCHEMA = pa.schema(SILVER_ENTITY_FIELDS)

SILVER_INTERACTION_SCHEMA = pa.schema([
    pa.field('source', pa.string(), nullable=False),
    pa.field('entity_a', pa.struct(SILVER_ENTITY_FIELDS), nullable=False),
    pa.field('entity_b', pa.struct(SILVER_ENTITY_FIELDS), nullable=False),
    pa.field('interaction_type', pa.string()),
    pa.field('detection_method', pa.string()),
    pa.field('causal_mechanism', pa.string()),
    pa.field('causal_statement', pa.string()),
    pa.field('sentence', pa.string()),
    pa.field(
        'interaction_annotations',
        pa.list_(pa.struct([
            pa.field('key', pa.string()),
            pa.field('value', pa.string()),
        ])),
    ),
    pa.field(
        'references',
        pa.list_(pa.struct([
            pa.field('type', pa.string()),
            pa.field('value', pa.string()),
        ])),
    ),
])

