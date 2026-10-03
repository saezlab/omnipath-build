"""Validate published typed Parquet inputs without expanding Python rows."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .locations import is_remote, join_location

# Match Python str.strip(), including Unicode spaces and the four control
# separators Python treats as whitespace. Identifier text itself is never trimmed.
_KEY_WHITESPACE = (
    " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


@dataclass(frozen=True)
class ResourceValidation:
    counts: dict[str, int]
    has_activity: bool
    has_participant_relation: bool


def _literal(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("SQL literal must be a string without NUL characters")
    return "'" + value.replace("'", "''") + "'"


def _scan(directory: str | Path, filename: str) -> str:
    location = join_location(directory, filename)
    path = location if is_remote(location) else str(location.absolute())
    return f"read_parquet({_literal(path)}, hive_partitioning=false)"


def _bad_key(expression: str) -> str:
    return f"({expression} IS NULL OR trim({expression},{_literal(_KEY_WHITESPACE)})='')"


def _null_items(array: str) -> str:
    return f"list_contains(list_transform({array},item -> item IS NULL),true)"


def _nonfinite_quantities(array: str) -> str:
    return (
        f"list_contains(list_transform({array},item -> "
        "item.quantity.has_numeric_value IS NOT NULL "
        "AND NOT isfinite(item.quantity.has_numeric_value)),true)"
    )


def validate_resource(
    con: Any,
    directory: str | Path,
    *,
    entities_scan: str | None = None,
    relations_scan: str | None = None,
) -> ResourceValidation:
    """Validate source shapes and return only scalar counts/activity flags.

    Two aggregate scans inspect the published typed structs without converting
    expanded rows to Python or serializing raw source bodies. Null lists and
    null quantity fields are supported; null *entries* in nested struct lists
    would crash the reference projector and are rejected. Each present numeric
    quantity must be finite before SQL JSON serialization.
    """
    entities = entities_scan or _scan(directory, "entities.parquet")
    relations = relations_scan or _scan(directory, "relations.parquet")
    entity = con.execute(f"""SELECT count(*)::BIGINT,
        COALESCE(sum(COALESCE(array_length(e.identifiers),0)),0)::BIGINT,
        COALESCE(sum(COALESCE(array_length(e.annotations),0)),0)::BIGINT,
        COALESCE(bool_or(e.entity_type='molecular_activity'),false),
        COALESCE(bool_or({_bad_key("e.entity_key")}),false),
        COALESCE(bool_or({_null_items("e.identifiers")}),false),
        COALESCE(bool_or({_null_items("e.annotations")}),false),
        COALESCE(bool_or({_nonfinite_quantities("e.annotations")}),false)
        FROM {entities} e""").fetchone()
    if entity[4]:
        raise ValueError("entity_key must be a nonempty string")
    if entity[5] or entity[6]:
        raise ValueError("Null nested struct in entity identifiers or annotations")
    if entity[7]:
        raise ValueError("Invalid JSON value in entity annotations: quantity must be finite")
    keys = " OR ".join(
        _bad_key(f"r.{name}")
        for name in ("relation_key", "subject_entity_key", "object_entity_key")
    )
    relation = con.execute(f"""SELECT count(*)::BIGINT,
        COALESCE(sum(COALESCE(array_length(r.evidence),0)),0)::BIGINT,
        COALESCE(sum(COALESCE(array_length(r.annotations),0)),0)::BIGINT,
        COALESCE(sum(COALESCE(list_sum(list_transform(r.evidence,
            observation -> COALESCE(array_length(observation.annotations),0))),0)),0)::BIGINT,
        COALESCE(bool_or(r.statement_kind='relation'
            AND r.predicate IN ('has_input','has_output','enabled_by')),false),
        COALESCE(bool_or({keys}),false),
        COALESCE(bool_or({_null_items("r.evidence")}),false),
        COALESCE(bool_or({_null_items("r.annotations")}),false),
        COALESCE(bool_or(list_contains(list_transform(r.evidence,
            observation -> {_null_items("observation.annotations")}),true)),false),
        COALESCE(bool_or({_nonfinite_quantities("r.annotations")}),false),
        COALESCE(bool_or(list_contains(list_transform(r.evidence,
            observation -> {_nonfinite_quantities("observation.annotations")}),true)),false)
        FROM {relations} r""").fetchone()
    if relation[5]:
        raise ValueError(
            "relation_key, subject_entity_key and object_entity_key must be nonempty strings"
        )
    if relation[6] or relation[7] or relation[8]:
        raise ValueError("Null nested struct in relation/evidence annotations or evidence")
    if relation[9] or relation[10]:
        raise ValueError(
            "Invalid JSON value in relation/evidence annotations: quantity must be finite"
        )
    return ResourceValidation(
        counts={
            "entities": entity[0],
            "identifiers": entity[1],
            "relations": relation[0],
            "evidence": relation[1],
            "annotations": entity[2] + relation[2] + relation[3],
        },
        has_activity=entity[3],
        has_participant_relation=relation[4],
    )
