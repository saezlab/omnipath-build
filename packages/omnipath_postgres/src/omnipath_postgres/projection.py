"""Stream resolved Parquet into lossless rows for the PostgreSQL loader.

Identifiers and relation keys are copied as published. Projection never resolves
entities or reinterprets Biolink terms, quantities or source evidence.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal

import duckdb


TableName = Literal["entities", "identifiers", "annotations", "relations", "evidence", "payloads"]


@dataclass(frozen=True)
class ProjectedRecord:
    """A target table plus Python values ready for PostgreSQL JSON adaptation.

    ``record_json`` and ``quantity`` remain nested Python dictionaries, and
    ``sources`` remains a list or null. The loader adapts them to its JSON/array
    columns. ``payload_json`` is the original text, including whitespace.
    """

    table: TableName
    values: dict[str, Any]


def _validate_batch_size(batch_size: int) -> None:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")


def _validate_key(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string; got {value!r}")


def _validate_json(row: dict[str, Any], context: str) -> None:
    try:
        json.dumps(row, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON value in {context}: {exc}") from exc


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant {value}")


def iter_rows(table_path: str | Path, *, batch_size: int = 1024) -> Iterator[dict[str, Any]]:
    """Read one Parquet file through DuckDB, retaining at most one fetched batch.

    Paths are parameterized and hive partition inference is disabled, so source
    fields and string keys are returned exactly as stored in the file. Close the
    generator if stopping early to release its connection immediately.
    """
    _validate_batch_size(batch_size)
    with duckdb.connect(config={"threads": "1", "memory_limit": "256MB"}) as connection:
        connection.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning=false)", [str(table_path)]
        )
        columns = tuple(column[0] for column in connection.description)
        while batch := connection.fetchmany(batch_size):
            for row in batch:
                yield dict(zip(columns, row, strict=True))


def iter_resource_records(
    directory: str | Path,
    resource: str,
    version: str,
    *,
    batch_size: int = 1024,
) -> Iterator[ProjectedRecord]:
    """Project a published resource version without changing its identities.

    All records carry ``resource`` and ``version``. Entity and relation rows
    contain every scalar source column plus the complete nested ``record_json``.
    Identifiers, annotations and evidence additionally get zero-based ordinals;
    repeated source array entries are retained rather than deduplicated.

    Annotations identify their owner with ``owner_kind`` and the exact source
    ``owner_key``. Only evidence annotations set ``evidence_ordinal``; others use
    null. Entity annotations have null ``scope`` because their schema lacks that
    field. Standalone entity payloads retain their null ``relation_key``.
    """
    _validate_batch_size(batch_size)
    directory = Path(directory)

    def record(table: TableName, values: dict[str, Any]) -> ProjectedRecord:
        return ProjectedRecord(
            table, {**deepcopy(values), "resource": resource, "version": version}
        )

    def annotations(
        items: list[dict[str, Any]] | None,
        owner_kind: str,
        owner_key: str,
        evidence_ordinal: int | None = None,
    ) -> Iterator[ProjectedRecord]:
        for ordinal, annotation in enumerate(items or ()):
            yield record(
                "annotations",
                {
                    "owner_kind": owner_kind,
                    "owner_key": owner_key,
                    "evidence_ordinal": evidence_ordinal,
                    "ordinal": ordinal,
                    "term": annotation["term"],
                    "value": annotation["value"],
                    "quantity": annotation["quantity"],
                    "source": annotation["source"],
                    "dataset": annotation["dataset"],
                    "scope": annotation.get("scope"),
                },
            )

    for row in iter_rows(directory / "entities.parquet", batch_size=batch_size):
        _validate_key(row["entity_key"], "entity_key")
        _validate_json(row, f"entity {row['entity_key']!r}")
        scalar = {
            name: value for name, value in row.items() if name not in {"identifiers", "annotations"}
        }
        yield record("entities", {**scalar, "record_json": row})
        for ordinal, identifier in enumerate(row["identifiers"] or ()):
            yield record(
                "identifiers", {"entity_key": row["entity_key"], "ordinal": ordinal, **identifier}
            )
        yield from annotations(row["annotations"], "entity", row["entity_key"])

    for row in iter_rows(directory / "relations.parquet", batch_size=batch_size):
        for field in ("relation_key", "subject_entity_key", "object_entity_key"):
            _validate_key(row[field], field)
        _validate_json(row, f"relation {row['relation_key']!r}")
        scalar = {
            name: value for name, value in row.items() if name not in {"evidence", "annotations"}
        }
        yield record("relations", {**scalar, "record_json": row})
        yield from annotations(row["annotations"], "relation", row["relation_key"])
        for ordinal, evidence in enumerate(row["evidence"] or ()):
            yield record(
                "evidence",
                {
                    "relation_key": row["relation_key"],
                    "ordinal": ordinal,
                    **{name: value for name, value in evidence.items() if name != "annotations"},
                    "record_json": evidence,
                },
            )
            yield from annotations(
                evidence["annotations"], "evidence", row["relation_key"], ordinal
            )

    for ordinal, row in enumerate(
        iter_rows(directory / "evidence_payloads.parquet", batch_size=batch_size)
    ):
        pointers = [field for field in ("relation_key", "entity_key") if row[field] is not None]
        if len(pointers) != 1:
            raise ValueError(f"Payload {ordinal} must refer to exactly one entity or relation")
        _validate_key(row[pointers[0]], pointers[0])
        if row["payload_json"] is not None:
            try:
                json.loads(row["payload_json"], parse_constant=_reject_nonfinite)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid payload_json at payload {ordinal}: {exc}") from exc
        yield record("payloads", {"ordinal": ordinal, **row})
