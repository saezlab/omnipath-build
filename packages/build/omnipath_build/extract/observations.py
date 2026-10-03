"""Raw entity and relation observation dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from omnipath_resolver import RawEntityObservation as RawEntityObservation


@dataclass(slots=True)
class RawRelationObservation:
    """Extracted raw relation observation before canonicalization."""

    relation_key: str
    subject_entity_key: str
    predicate: str
    object_entity_key: str
    source: str
    dataset: str
    row_id: str
    upstream_id: str
    statement_kind: str = "relation"
    annotations: list[dict[str, Any]] = field(default_factory=list)
    payload_json: str | None = None
