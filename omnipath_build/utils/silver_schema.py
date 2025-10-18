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
    BiologicalRoleCv,
    ExperimentalRoleCv,
    IdentificationMethodCv,
    BiologicalEffectCv,
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
    identifiers: List[Identifier] | None = None

    # Names
    name: str | None = None
    synonyms: List[str] | None = None

    # Optional membership info
    members: List[Member] | None = None
    parent_identifier: str | None = None
    parent_identifier_type: IdentifierNamespaceCv | None = None

    # Optional annotations
    annotations: List[Dict[str, Any]] | None = None
    references: List[str] | None = None

    # Optional metadata (if provided by meta database)
    secondary_source: str | None = None


class InteractionParticipant(SilverEntity):
    """Entity participating in an interaction with contextual role information."""

    biological_role: BiologicalRoleCv | None = None
    experimental_role: ExperimentalRoleCv | None = None
    stoichiometry: float | None = None
    identification_method: IdentificationMethodCv | None = None


class SilverInteraction(NamedTuple):
    """Cleaned interaction records (one row per source evidence record)."""

    # Required fields - metadata
    source: str

    # Required fields - interaction participants
    entity_a: InteractionParticipant
    entity_b: InteractionParticipant

    # Optional evidence details
    interaction_type: InteractionTypeCv | None = None
    detection_method: DetectionMethodCv | None = None
    direction: str | None = None  # 'a_to_b', 'b_to_a', 'bidirectional', 'undirected'
    causal_mechanism: CausalMechanismCv | None = None
    causal_statement: CausalStatementCv | None = None
    sentence: str | None = None

    complex_expansion: ComplexExpansionCv | None = None

    # Optional annotations
    interaction_annotations: List[Dict[str, Any]] | None = None

    # Optional reference
    references: Reference | None = None


class SilverCvTerm(NamedTuple):
    """Controlled vocabulary terms from sources (one row per source term)."""

    # Required fields - metadata
    source: str

    # Required fields - term identification
    term_accession: str  # Formal accession if available (e.g., 'GO:0008150')
    namespace: str

    # Optional term information (if provided by source)
    term_name: str | None = None
    term_definition: str | None = None
    term_definition_refs: List[str] | None = None
    term_synonyms: List[str] | None = None
    term_parent_accessions: List[str] | None = None
    term_parent_names: List[str] | None = None
    term_alt_ids: List[str] | None = None


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
    pa.field('name', pa.string()),
    pa.field('synonyms', pa.list_(pa.string())),
    pa.field(
        'members',
        pa.list_(pa.struct([
            pa.field('key', pa.string()),
            pa.field('value', pa.string()),
        ])),
    ),
    pa.field('parent_accession', pa.string()),
    pa.field(
        'annotations',
        pa.list_(pa.struct([
            pa.field('term', pa.string()),
            pa.field('value', pa.string()),
            pa.field('units', pa.string()),
        ])),
    ),
    pa.field('references', pa.list_(pa.string())),
    pa.field('secondary_source', pa.string()),
]

SILVER_ENTITY_SCHEMA = pa.schema(SILVER_ENTITY_FIELDS)

SILVER_INTERACTION_SCHEMA = pa.schema([
    pa.field('source', pa.string(), nullable=False),
    pa.field('entity_a', pa.struct(SILVER_ENTITY_FIELDS), nullable=False),
    pa.field('entity_b', pa.struct(SILVER_ENTITY_FIELDS), nullable=False),
    pa.field('interaction_type', pa.string()),
    pa.field('detection_method', pa.string()),
    pa.field('is_directed', pa.bool_()),
    pa.field('direction', pa.string()),
    pa.field('sign', pa.string()),
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
    pa.field('references', pa.list_(pa.string())),
])

SILVER_CV_TERM_SCHEMA = pa.schema([
    pa.field('source', pa.string(), nullable=False),
    pa.field('term_accession', pa.string(), nullable=False),
    pa.field('namespace', pa.string(), nullable=False),
    pa.field('term_name', pa.string()),
    pa.field('term_definition', pa.string()),
    pa.field('term_definition_refs', pa.list_(pa.string())),
    pa.field('term_synonyms', pa.list_(pa.string())),
    pa.field('term_parent_accessions', pa.list_(pa.string())),
    pa.field('term_parent_names', pa.list_(pa.string())),
    pa.field('term_alt_ids', pa.list_(pa.string())),
])
