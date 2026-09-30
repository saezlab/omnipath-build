"""DuckDB SQL projections of resolved Parquets for bulk PostgreSQL COPY.

Queries return scalar columns and JSON text in the loader's exact column order.
Production callers COPY these SELECTs directly; Python never receives expanded
entity, relation, evidence, identifier or annotation rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECTION_COLUMNS = {
    "entities": (
        "resource",
        "version",
        "entity_key",
        "entity_type",
        "namespace",
        "identifier",
        "taxon",
        "label",
        "has_hierarchy",
        "parent_count",
        "child_count",
        "record_json",
    ),
    "identifiers": (
        "resource",
        "version",
        "entity_key",
        "ordinal",
        "ns",
        "id",
        "is_canonical",
        "source",
    ),
    "relations": (
        "resource",
        "version",
        "relation_key",
        "statement_kind",
        "subject_entity_key",
        "subject_label",
        "subject_type",
        "predicate",
        "object_entity_key",
        "object_label",
        "object_type",
        "taxon",
        "is_directed",
        "sign",
        "category",
        "interaction_class",
        "sources",
        "evidence_count",
        "record_json",
    ),
    "evidence": (
        "resource",
        "version",
        "relation_key",
        "ordinal",
        "source",
        "dataset",
        "row_id",
        "upstream_id",
        "record_json",
    ),
    "annotations": (
        "resource",
        "version",
        "owner_kind",
        "owner_key",
        "evidence_ordinal",
        "ordinal",
        "term",
        "value",
        "quantity",
        "source",
        "dataset",
        "scope",
    ),
}
JSON_COLUMNS = frozenset({"record_json", "sources", "quantity"})

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
    path = str((Path(directory) / filename).absolute())
    return f"read_parquet({_literal(path)}, hive_partitioning=false)"


def _json(expression: str) -> str:
    # SQL NULL must remain a COPY null, rather than a JSON text 'null'. A present
    # struct whose fields are all null is still serialized as its full object.
    return f"CASE WHEN {expression} IS NULL THEN NULL ELSE to_json({expression})::VARCHAR END"


def _select(
    table: str, source: str, resource: str, version: str, expressions: dict[str, str]
) -> str:
    values = {
        "resource": f"{_literal(resource)}::VARCHAR",
        "version": f"{_literal(version)}::VARCHAR",
        **expressions,
    }
    columns = ",\n    ".join(
        f'{values[column]} AS "{column}"' for column in PROJECTION_COLUMNS[table]
    )
    return f"SELECT\n    {columns}\nFROM {source}"


def _array_rows(scan: str, key: str, array: str) -> str:
    # The two set-returning expressions advance together, preserving duplicates
    # and positional ordinals rather than joining or deduplicating array values.
    return (
        f"SELECT {key} AS owner_key, unnest({array}) AS item, "
        f"(generate_subscripts({array},1)-1)::BIGINT AS ordinal FROM {scan}"
    )


def _evidence_rows(relations: str) -> str:
    return (
        "SELECT r.relation_key AS owner_key, unnest(r.evidence) AS item, "
        "(generate_subscripts(r.evidence,1)-1)::BIGINT AS ordinal "
        f"FROM {relations} r"
    )


def _annotation_rows(rows: str, owner_kind: str, *, evidence: bool = False) -> str:
    scope = "a.item.scope" if owner_kind != "entity" else "NULL::VARCHAR"
    evidence_ordinal = "a.evidence_ordinal" if evidence else "NULL::BIGINT"
    return f"""SELECT {_literal(owner_kind)}::VARCHAR AS owner_kind,
        a.owner_key, {evidence_ordinal} AS evidence_ordinal, a.ordinal,
        a.item.term AS term, a.item.value AS value,
        {_json("a.item.quantity")} AS quantity,
        a.item.source AS source, a.item.dataset AS dataset, {scope} AS scope
        FROM ({rows}) a"""


def projection_query(directory: str | Path, resource: str, version: str, table: str) -> str:
    """Return an explicitly ordered SELECT suitable for DuckDB COPY TO a sink.

    File names are fixed, hive inference is disabled, and file/resource/version
    literals are quoted. There is deliberately no global ORDER BY or Python row
    conversion: logical occurrence order is carried by the ordinal columns.
    """
    if table not in PROJECTION_COLUMNS:
        raise ValueError(f"Unknown projection table: {table!r}")
    entities = _scan(directory, "entities.parquet")
    relations = _scan(directory, "relations.parquet")
    if table == "entities":
        expressions = {
            name: f"e.{name}"
            for name in PROJECTION_COLUMNS[table]
            if name not in {"resource", "version", "record_json"}
        }
        expressions["record_json"] = _json("e")
        return _select(table, f"{entities} e", resource, version, expressions)
    if table == "relations":
        expressions = {
            name: f"r.{name}"
            for name in PROJECTION_COLUMNS[table]
            if name not in {"resource", "version", "record_json", "sources"}
        }
        expressions.update(record_json=_json("r"), sources=_json("r.sources"))
        return _select(table, f"{relations} r", resource, version, expressions)
    if table == "identifiers":
        rows = _array_rows(f"{entities} e", "e.entity_key", "e.identifiers")
        expressions = {"entity_key": "a.owner_key", "ordinal": "a.ordinal"}
        expressions.update(
            {name: f"a.item.{name}" for name in ("ns", "id", "is_canonical", "source")}
        )
        return _select(table, f"({rows}) a", resource, version, expressions)
    evidence_rows = _evidence_rows(relations)
    if table == "evidence":
        expressions = {
            "relation_key": "a.owner_key",
            "ordinal": "a.ordinal",
            "record_json": _json("a.item"),
            **{name: f"a.item.{name}" for name in ("source", "dataset", "row_id", "upstream_id")},
        }
        return _select(table, f"({evidence_rows}) a", resource, version, expressions)
    entity_annotations = _array_rows(f"{entities} e", "e.entity_key", "e.annotations")
    relation_annotations = _array_rows(f"{relations} r", "r.relation_key", "r.annotations")
    evidence_annotations = (
        "SELECT observation.owner_key, observation.ordinal AS evidence_ordinal, "
        "unnest(observation.item.annotations) AS item, "
        "(generate_subscripts(observation.item.annotations,1)-1)::BIGINT AS ordinal "
        f"FROM ({evidence_rows}) observation"
    )
    rows = " UNION ALL ".join(
        (
            _annotation_rows(entity_annotations, "entity"),
            _annotation_rows(relation_annotations, "relation"),
            _annotation_rows(evidence_annotations, "evidence", evidence=True),
        )
    )
    expressions = {
        name: f"a.{name}"
        for name in PROJECTION_COLUMNS[table]
        if name not in {"resource", "version"}
    }
    return _select(table, f"({rows}) a", resource, version, expressions)


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


def validate_resource(con: Any, directory: str | Path) -> ResourceValidation:
    """Validate source shapes and return only scalar counts/activity flags.

    Two aggregate scans inspect the published typed structs without converting
    expanded rows to Python or serializing raw source bodies. Null lists and
    null quantity fields are supported; null *entries* in nested struct lists
    would crash the reference projector and are rejected. Each present numeric
    quantity must be finite before SQL JSON serialization.
    """
    entities = _scan(directory, "entities.parquet")
    relations = _scan(directory, "relations.parquet")
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
