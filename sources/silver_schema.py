"""
Canonical definitions for silver-layer schemas.

Keeping the PyArrow schema objects and the namedtuple helpers in one place
avoids accidental divergence across the pipeline.
"""
from typing import Optional, List, Dict, Any, NamedTuple
import pyarrow as pa

__all__ = [
    'SilverEntity',
    'SilverInteraction',
    'SILVER_ENTITY_SCHEMA',
    'SILVER_INTERACTION_SCHEMA',
    'get_entity_schema',
    'get_interaction_schema',
]


class SilverEntity(NamedTuple):
    """Silver entity record matching silver_entities schema."""
    # Required fields
    source: str
    accession: str
    entity_type: str

    # Optional structural identifiers
    inchikey: Optional[str] = None
    smiles: Optional[str] = None
    inchi: Optional[str] = None

    # Optional identifiers and names
    cross_references: Optional[List[Dict[str, str]]] = None
    name: Optional[str] = None
    synonyms: Optional[List[str]] = None

    # Optional complex/membership
    members: Optional[List[Dict[str, Any]]] = None

    # Optional annotations
    annotations: Optional[List[Dict[str, Any]]] = None
    references: Optional[List[str]] = None

    # Optional metadata
    secondary_source: Optional[str] = None


class SilverInteraction(NamedTuple):
    """Silver interaction record matching silver_interactions schema."""
    # Required fields
    source: str
    entity_a_identifier: str
    entity_a_identifier_type: str
    entity_b_identifier: str
    entity_b_identifier_type: str

    # Optional participant names
    entity_a_name: Optional[str] = None
    entity_b_name: Optional[str] = None

    # Optional evidence details
    interaction_type: Optional[str] = None
    detection_method: Optional[str] = None
    is_directed: Optional[bool] = None
    direction: Optional[str] = None
    sign: Optional[str] = None
    causal_mechanism: Optional[str] = None
    causal_statement: Optional[str] = None
    sentence: Optional[str] = None

    # Optional annotations
    interaction_annotations: Optional[List[Dict[str, Any]]] = None
    entity_a_context: Optional[List[Dict[str, Any]]] = None
    entity_b_context: Optional[List[Dict[str, Any]]] = None

    # Optional reference
    reference_type: Optional[str] = None
    reference_value: Optional[str] = None


SILVER_ENTITY_SCHEMA = pa.schema([
    pa.field('source', pa.string(), nullable=False),
    pa.field('accession', pa.string(), nullable=False),
    pa.field('entity_type', pa.string(), nullable=False),
    pa.field('inchikey', pa.string()),
    pa.field('smiles', pa.string()),
    pa.field('inchi', pa.string()),
    pa.field(
        'cross_references',
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
])

SILVER_INTERACTION_SCHEMA = pa.schema([
    pa.field('source', pa.string(), nullable=False),
    pa.field('entity_a_identifier', pa.string(), nullable=False),
    pa.field('entity_a_identifier_type', pa.string(), nullable=False),
    pa.field('entity_b_identifier', pa.string(), nullable=False),
    pa.field('entity_b_identifier_type', pa.string(), nullable=False),
    pa.field('entity_a_name', pa.string()),
    pa.field('entity_b_name', pa.string()),
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
    pa.field(
        'entity_a_context',
        pa.list_(pa.struct([
            pa.field('key', pa.string()),
            pa.field('value', pa.string()),
        ])),
    ),
    pa.field(
        'entity_b_context',
        pa.list_(pa.struct([
            pa.field('key', pa.string()),
            pa.field('value', pa.string()),
        ])),
    ),
    pa.field('reference_type', pa.string()),
    pa.field('reference_value', pa.string()),
])


def get_entity_schema() -> pa.Schema:
    """Return PyArrow schema for the silver_entities table."""
    return SILVER_ENTITY_SCHEMA


def get_interaction_schema() -> pa.Schema:
    """Return PyArrow schema for the silver_interactions table."""
    return SILVER_INTERACTION_SCHEMA
