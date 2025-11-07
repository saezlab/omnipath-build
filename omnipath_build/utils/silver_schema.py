"""
Canonical definitions for silver-layer schemas.

Keeping the PyArrow schema objects and the namedtuple helpers in one place
avoids accidental divergence across the pipeline.
"""
from typing import List, NamedTuple, Self


import pyarrow as pa

from omnipath_build.utils.cv_terms import (
    EntityTypeCv,
    IdentifierNamespaceCv,
    AnnotationTypeCv,  # This is the Union of all annotation CV terms
    LicenseCV,
    UpdateCategoryCV,
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


class DownloadConfig(NamedTuple):
    """ Configuration for source database downloads."""

    url: str  # Download URL
    method: str  # e.g. 'http', 'ftp', 's3', 'api'
    additional_params: dict | None = None  # e.g. headers, auth, etc.


class Identifier(NamedTuple):
    """An identifier with its type."""

    type: IdentifierNamespaceCv
    value: str
    
class Annotation(NamedTuple):
    """Annotation for an interaction or entity."""

    term: AnnotationTypeCv
    value: str | float | None = None
    units: str | None = None

class Membership(NamedTuple):
    member: 'Entity'  # Forward reference since Entity is defined below
    annotations: list[Annotation] | None = None

class Entity(NamedTuple):
    """ Entity record matching entities schema."""

    # Required fields
    source: str
    type: EntityTypeCv # e.g. EntityTypeCv.INTERACTION, EntityTypeCv.CV_TERM
    identifiers: List[Identifier]  # e.g. IdentifierNamespaceCv.NAME and .SYNONYM)
    annotations: List[Annotation] | None = None

    members: List[Membership] | None = None # e.g. for complexes and families
    is_member_of: List[Membership] | None = None # e.g. for proteins that are part of complexes or families
  
class Source(NamedTuple):
    """ Source database record."""

    id: str  # e.g. 'uniprot'
    name: str  # e.g. 'UniProt'
    download_config: DownloadConfig
    license: LicenseCV
    update_category: UpdateCategoryCV
    
    publication: str | None = None
    url: str | None = None
    description: str | None = None



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

