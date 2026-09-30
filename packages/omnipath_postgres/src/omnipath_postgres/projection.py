"""Stream resolved Parquet into lossless rows for the PostgreSQL loader.

Identifiers and relation keys are copied as published. Projection never resolves
entities or reinterprets Biolink terms, quantities or source evidence.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pyarrow.parquet as pq


TableName = Literal["entities", "identifiers", "annotations", "relations", "evidence"]

# COPY batches may be larger, but wide nested evidence/raw records must not be
# decoded or converted into a complete SQL result before the first row is read.
_MAX_PARQUET_BATCH_ROWS = 64
_PARQUET_READ_BUFFER_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProjectedRecord:
    """A target table plus Python values ready for PostgreSQL JSON adaptation.

    ``record_json`` and ``quantity`` remain nested Python dictionaries, and
    ``sources`` remains a list or null. The loader adapts them to its JSON/array
    columns. Raw source payloads are validated separately and are never projected.
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
    """Stream one Parquet file with buffered I/O and at most 64 decoded rows.

    A direct file reader preserves the published schema without hive partition
    inference. Disable column-chunk prefetch and use fixed-size input buffers;
    unbuffered Parquet reads can retain an entire compressed column chunk. Only
    one row is converted to Python at a time. Unlike ``execute().fetchmany()``,
    this does not materialize a SQL result containing the whole source file.

    Memory still depends on the largest individual record, Parquet page and
    dictionary. Close the generator if stopping early to release its file and
    decoder immediately.
    """
    _validate_batch_size(batch_size)
    parquet = pq.ParquetFile(
        table_path,
        memory_map=False,
        pre_buffer=False,
        buffer_size=_PARQUET_READ_BUFFER_BYTES,
    )
    try:
        batches = parquet.iter_batches(
            batch_size=min(batch_size, _MAX_PARQUET_BATCH_ROWS), use_threads=False
        )
        try:
            for batch in batches:
                for index in range(batch.num_rows):
                    yield batch.slice(index, 1).to_pylist()[0]
                # Release the previous batch before asking the decoder for another.
                del batch
        finally:
            # Closing the generator also frees its C++ reader when the consumer
            # stops while still inside a decoded batch.
            batches.close()
    finally:
        parquet.close()


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
    field. Raw payloads do not become PostgreSQL records.
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


@dataclass(frozen=True)
class PayloadReference:
    """Transient pointer metadata from one validated source payload, without its body."""

    ordinal: int
    owner_kind: Literal["entity", "relation"]
    owner_key: str
    source: str | None
    row_id: str | None
    source_record_sha256: str | None
    source_record_type: str | None


def iter_validated_payloads(
    directory: str | Path, *, batch_size: int = 1024
) -> Iterator[PayloadReference]:
    """Validate published raw JSON in bounded batches without returning or storing it.

    Original Parquets remain authoritative. The loader checks each returned
    pointer against copied owners, checks reaction provenance against the exact
    source text digest and parsed shape, and checks the consumed count against
    the manifest; no source payload table is created in PostgreSQL.
    """
    _validate_batch_size(batch_size)
    directory = Path(directory)
    for ordinal, row in enumerate(
        iter_rows(directory / "evidence_payloads.parquet", batch_size=batch_size)
    ):
        pointers = [field for field in ("relation_key", "entity_key") if row[field] is not None]
        if len(pointers) != 1:
            raise ValueError(f"Payload {ordinal} must refer to exactly one entity or relation")
        owner = pointers[0]
        _validate_key(row[owner], owner)
        digest = shape = None
        if row["payload_json"] is not None:
            try:
                parsed = json.loads(row["payload_json"], parse_constant=_reject_nonfinite)
                # An overflowing JSON exponent also becomes a nonfinite float.
                _validate_json(parsed, f"payload {ordinal}")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid payload_json at payload {ordinal}: {exc}") from exc
            digest = hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
            shape = (
                "object"
                if isinstance(parsed, dict)
                else "array"
                if isinstance(parsed, list)
                else "scalar"
            )
        yield PayloadReference(
            ordinal,
            "entity" if owner == "entity_key" else "relation",
            row[owner],
            row["source"],
            row["row_id"],
            digest,
            shape,
        )
