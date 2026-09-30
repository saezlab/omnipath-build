"""Canonical PyArrow schemas defining the contract for OmniPath Parquet serving files.

The serving contract consists of three nested Parquet tables per resource version:
- entities.parquet: ENTITY_SCHEMA
- relations.parquet: RELATION_SCHEMA
- evidence_payloads.parquet: PAYLOAD_SCHEMA
"""

from __future__ import annotations

from omnipath_core.measurements import QUANTITY_STRUCT
import pyarrow as pa

IDENTIFIER_STRUCT = pa.struct(
    [
        ("ns", pa.string()),
        ("id", pa.string()),
        ("is_canonical", pa.bool_()),
        ("source", pa.string()),
    ]
)

ANNOTATION_STRUCT = pa.struct(
    [
        ("term", pa.string()),
        ("value", pa.string()),
        ("quantity", QUANTITY_STRUCT),
        ("source", pa.string()),
        ("dataset", pa.string()),
        ("scope", pa.string()),
    ]
)

ENTITY_ANNOTATION_STRUCT = pa.struct(
    [
        ("term", pa.string()),
        ("value", pa.string()),
        ("quantity", QUANTITY_STRUCT),
        ("source", pa.string()),
        ("dataset", pa.string()),
    ]
)

EVIDENCE_STRUCT = pa.struct(
    [
        ("source", pa.string()),
        ("dataset", pa.string()),
        ("row_id", pa.string()),
        ("upstream_id", pa.string()),
        ("annotations", pa.list_(ANNOTATION_STRUCT)),
    ]
)

ENTITY_SCHEMA = pa.schema(
    [
        ("entity_key", pa.string()),
        ("entity_type", pa.string()),
        ("namespace", pa.string()),
        ("identifier", pa.string()),
        ("taxon", pa.string()),
        ("label", pa.string()),
        ("has_hierarchy", pa.bool_()),
        ("parent_count", pa.int64()),
        ("child_count", pa.int64()),
        ("identifiers", pa.list_(IDENTIFIER_STRUCT)),
        ("annotations", pa.list_(ENTITY_ANNOTATION_STRUCT)),
    ]
)

RELATION_SCHEMA = pa.schema(
    [
        ("relation_key", pa.string()),
        ("statement_kind", pa.string()),
        ("subject_entity_key", pa.string()),
        ("subject_label", pa.string()),
        ("subject_type", pa.string()),
        ("predicate", pa.string()),
        ("object_entity_key", pa.string()),
        ("object_label", pa.string()),
        ("object_type", pa.string()),
        ("taxon", pa.string()),
        ("is_directed", pa.bool_()),
        ("sign", pa.int32()),
        ("category", pa.string()),
        ("interaction_class", pa.string()),
        ("sources", pa.list_(pa.string())),
        ("evidence_count", pa.int64()),
        ("evidence", pa.list_(EVIDENCE_STRUCT)),
        ("annotations", pa.list_(ANNOTATION_STRUCT)),
    ]
)

PAYLOAD_SCHEMA = pa.schema(
    [
        ("relation_key", pa.string()),
        ("entity_key", pa.string()),
        ("source", pa.string()),
        ("row_id", pa.string()),
        ("payload_json", pa.string()),
    ]
)
