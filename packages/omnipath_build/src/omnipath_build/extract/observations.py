"""Raw entity and relation observation dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RawEntityObservation:
    """Extracted raw entity observation before canonicalization."""

    entity_key: str
    entity_type: str
    namespace: str
    identifier: str
    taxon: str | None = None
    label: str | None = None
    identifiers: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    identity_scope: str | None = None
    structure_derivations: list[dict[str, Any]] = field(default_factory=list)


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
