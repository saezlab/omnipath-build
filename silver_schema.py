"""
Silver table schema definitions and constructor functions.
Auto-generated from silver_tables.yaml (conceptually).
"""
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

__all__ = [
    'SilverEntity',
    'SilverInteraction',
]


@dataclass
class SilverEntity:
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
    references: Optional[List[int]] = None

    # Optional metadata
    secondary_source: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary, filtering out None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class SilverInteraction:
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

    def to_dict(self) -> dict:
        """Convert to dictionary, filtering out None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}
