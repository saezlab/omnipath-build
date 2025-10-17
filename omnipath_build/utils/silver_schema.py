"""
Canonical definitions for silver-layer schemas.

Keeping the PyArrow schema objects and the namedtuple helpers in one place
avoids accidental divergence across the pipeline.
"""
from typing import Optional, List, Dict, Any, NamedTuple
from enum import Enum
import pyarrow as pa

__all__ = [
    'IdentifierType',
    'Identifier',
    'SilverEntity',
    'SilverInteraction',
    'SilverCvTerm',
    'SILVER_ENTITY_SCHEMA',
    'SILVER_INTERACTION_SCHEMA',
    'SILVER_CV_TERM_SCHEMA',
]


class IdentifierType(str, Enum):
    """Enumeration of known identifier types for entities."""
    # Source-specific accessions
    ACCESSION = "accession"

    # Structural identifiers
    INCHIKEY = "inchikey"
    INCHI = "inchi"
    SMILES = "smiles"

    # Protein/gene databases
    UNIPROT = "uniprot"
    ENTREZ = "entrez"
    ENSEMBL = "ensembl"
    HGNC = "hgnc"
    REFSEQ = "refseq"
    REFSEQP = "refseqp"

    # Chemical databases
    CHEBI = "chebi"
    PUBCHEM = "pubchem"
    PUBCHEM_COMPOUND = "pubchem_compound"
    CHEMBL = "chembl"
    DRUGBANK = "drugbank"
    LIPIDMAPS = "lipidmaps"
    HMDB = "hmdb"
    KEGG = "kegg"
    CAS = "cas"
    METANETX = "metanetx"

    # Other
    OTHER = "other"


class Identifier(NamedTuple):
    """An identifier with its type."""
    type: IdentifierType
    value: str


class SilverEntity(NamedTuple):
    """Silver entity record matching silver_entities schema."""
    # Required fields
    source: str
    entity_type: str  # 'protein', 'gene', 'compound', etc.

    # All identifiers consolidated here using the IdentifierType enum
    identifiers: Optional[List[Identifier]] = None

    # Names
    name: Optional[str] = None
    synonyms: Optional[List[str]] = None  # ["synonym1", "synonym2"]

    # Optional membership info
    members: Optional[List[Dict[str, Any]]] = None  # [{"member_id": "...", "member_id_type": "...", "stoichiometry": 2, "role": "..."}]
    parent_accession: Optional[str] = None  # For entities that are part of another entity (e.g., protein complex)

    # Optional annotations
    annotations: Optional[List[Dict[str, Any]]] = None  # [{"term": "...", "value": "...", "units": "..."}]
    references: Optional[List[str]] = None  # [12345678, 23456789] (PMIDs e.g.)

    # Optional metadata (if provided by meta database)
    secondary_source: Optional[str] = None


class SilverInteraction(NamedTuple):
    """
    Cleaned interaction records (one row per source evidence record, before deduplication).
    Silver interaction record matching silver_interactions schema.
    """
    # Required fields - metadata
    source: str

    # Required fields - interaction participants
    entity_a: SilverEntity
    entity_b: SilverEntity

    # Optional evidence details
    interaction_type: Optional[str] = None  # 'physical association', 'phosphorylation', etc. accessions
    detection_method: Optional[str] = None
    is_directed: Optional[bool] = None
    direction: Optional[str] = None  # 'a_to_b', 'b_to_a', 'bidirectional'
    sign: Optional[str] = None  # 'positive', 'negative', 'neutral', 'unknown' accessions
    causal_mechanism: Optional[str] = None
    causal_statement: Optional[str] = None
    sentence: Optional[str] = None  # Extracted sentence from paper

    # Optional annotations
    interaction_annotations: Optional[List[Dict[str, Any]]] = None  # General interaction annotations

    # Optional reference
    references: Optional[List[str]] = None  # [12345678, 23456789] (PMIDs e.g.)


class SilverCvTerm(NamedTuple):
    """
    Controlled vocabulary terms from sources (one row per source term).
    Silver CV term record matching silver_cv_terms schema.
    """
    # Required fields - metadata
    source: str

    # Required fields - term identification
    term_accession: str  # Formal accession if available (e.g., 'GO:0008150')
    namespace: str

    # Optional term information (if provided by source)
    term_name: Optional[str] = None  # The actual term/value
    term_definition: Optional[str] = None
    term_definition_refs: Optional[List[str]] = None
    term_synonyms: Optional[List[str]] = None  # ["synonym1", "synonym2"]
    term_parent_accessions: Optional[List[str]] = None  # ["GO:0008150", "GO:0009987"]
    term_parent_names: Optional[List[str]] = None
    term_alt_ids: Optional[List[str]] = None


SILVER_ENTITY_FIELDS = [
    pa.field('source', pa.string(), nullable=False),
    pa.field('entity_type', pa.string(), nullable=False),
    pa.field(
        'identifiers',
        pa.list_(pa.struct([
            pa.field('type', pa.string()),  # Use IdentifierType enum values
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
