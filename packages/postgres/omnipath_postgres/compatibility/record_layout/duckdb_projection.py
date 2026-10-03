"""DuckDB SQL projections of resolved Parquets for bulk PostgreSQL COPY.

Queries return scalar columns and JSON text in the loader's exact column order.
Production callers COPY these SELECTs directly; Python never receives expanded
entity, relation, evidence, identifier or annotation rows.
"""

from __future__ import annotations

from pathlib import Path
from omnipath_postgres.parquet_queries import (
    _literal,
    _scan,
    validate_resource as validate_resource,
)


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
