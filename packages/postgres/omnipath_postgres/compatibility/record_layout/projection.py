"""Stream resolved Parquet into lossless rows for the PostgreSQL loader.

Identifiers and relation keys are copied as published. Projection never resolves
entities or reinterprets Biolink terms, quantities or source evidence.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq


TableName = Literal["entities", "identifiers", "annotations", "relations", "evidence"]

# COPY batches may be larger, but wide nested evidence/raw records must not be
# decoded or converted into a complete SQL result before the first row is read.
_MAX_PARQUET_BATCH_ROWS = 64
_PARQUET_READ_BUFFER_BYTES = 64 * 1024
# Raw payload dictionaries retain one native value per distinct text instead of
# expanding that text for every owner; their scalar metadata batches stay small.
_MAX_PAYLOAD_DICTIONARY_BATCH_ROWS = 1024

# A raw source record may occur once for each of many owners. Retain only its
# exact text and validation result, with conservative room for the digest,
# tuple, integer, OrderedDict node and hash-table slots in each entry's charge.
_MAX_PAYLOAD_CACHE_BYTES = 64 * 1024 * 1024
_MAX_PAYLOAD_CACHE_ENTRIES = 256
_PAYLOAD_CACHE_ENTRY_OVERHEAD = 512


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
    Entity evidence, gene reference arrays and molecular forms stay nested in
    ``record_json`` rather than becoming unsupported scalar COPY columns.
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
            name: value
            for name, value in row.items()
            if name not in {"identifiers", "annotations", "evidence", "gene_reference_keys"}
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
                    **{
                        name: evidence[name]
                        for name in ("source", "dataset", "row_id", "upstream_id")
                    },
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


def _validate_payload_text(text: str, ordinal: int) -> tuple[str, str]:
    """Validate a body and return metadata without retaining the parsed object."""
    try:
        parsed = json.loads(text, parse_constant=_reject_nonfinite)
        # An overflowing JSON exponent also becomes a nonfinite float.
        _validate_json(parsed, f"payload {ordinal}")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid payload_json at payload {ordinal}: {exc}") from exc
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    shape = (
        "object" if isinstance(parsed, dict) else "array" if isinstance(parsed, list) else "scalar"
    )
    return digest, shape


class _PayloadValidationCache:
    """Per-reader LRU of valid exact source text, never parsed payload bodies."""

    def __init__(self, *, max_bytes: int | None = None, max_entries: int | None = None) -> None:
        self.max_bytes = _MAX_PAYLOAD_CACHE_BYTES if max_bytes is None else max_bytes
        self.max_entries = _MAX_PAYLOAD_CACHE_ENTRIES if max_entries is None else max_entries
        self.size_bytes = 0
        self.entries: OrderedDict[str, tuple[str, str, int]] = OrderedDict()

    def validate(self, text: str, ordinal: int) -> tuple[str, str]:
        # The published schema is string-valued. Let JSON validation report any
        # malformed non-string inputs instead of attempting to cache them.
        cacheable = isinstance(text, str) and self.max_entries > 0
        charge = sys.getsizeof(text) + _PAYLOAD_CACHE_ENTRY_OVERHEAD if cacheable else 0
        cacheable = cacheable and charge <= self.max_bytes
        if cacheable:
            cached = self.entries.get(text)
            if cached is not None:
                self.entries.move_to_end(text)
                digest, shape, _ = cached
                return digest, shape

        # Failed validation never changes the cache. In particular, invalid
        # nonfinite constants and overflowing exponents cannot become hits.
        digest, shape = _validate_payload_text(text, ordinal)
        if cacheable:
            while self.entries and (
                len(self.entries) >= self.max_entries or self.size_bytes + charge > self.max_bytes
            ):
                self.size_bytes -= self.entries.popitem(last=False)[1][2]
            self.entries[text] = (digest, shape, charge)
            self.size_bytes += charge
        return digest, shape


class _DictionaryPayloadValidationCache:
    """One current Arrow dictionary plus bounded per-index validation metadata."""

    def __init__(self, dictionary: pa.Array) -> None:
        self.dictionary = dictionary
        self.entries: OrderedDict[int, tuple[str | None, str | None]] = OrderedDict()
        self.max_entries = min(
            _MAX_PAYLOAD_CACHE_ENTRIES, _MAX_PAYLOAD_CACHE_BYTES // _PAYLOAD_CACHE_ENTRY_OVERHEAD
        )

    def validate(self, index: int, ordinal: int) -> tuple[str | None, str | None]:
        cached = self.entries.get(index)
        if cached is not None:
            self.entries.move_to_end(index)
            return cached
        # Convert and parse only referenced values, never the entire dictionary.
        # The Python text/parsed body is released when this method returns.
        text = self.dictionary[index].as_py()
        result = (None, None) if text is None else _validate_payload_text(text, ordinal)
        if self.max_entries > 0:
            if len(self.entries) >= self.max_entries:
                self.entries.popitem(last=False)
            self.entries[index] = result
        return result


@dataclass(frozen=True)
class _DictionaryPayloadValue:
    cache: _DictionaryPayloadValidationCache
    index: int


def _iter_payload_rows(table_path: str | Path, *, batch_size: int) -> Iterator[dict[str, Any]]:
    """Read raw owners with native payload dictionaries, without expanding bodies.

    Parquet dictionaries can contain a multi-megabyte value repeated thousands
    of times. Keep the dictionary and convert only each owner's scalar metadata.
    PyArrow may copy equal dictionary buffers between batches, so compare their
    exact native values once per batch. Reset at every row group, and retain only
    the current dictionary plus at most 256 digest/shape results.
    """
    _validate_batch_size(batch_size)
    parquet = pq.ParquetFile(
        table_path,
        memory_map=False,
        pre_buffer=False,
        buffer_size=_PARQUET_READ_BUFFER_BYTES,
        read_dictionary=["payload_json"],
    )
    try:
        raw_column = parquet.schema_arrow.get_field_index("payload_json")
        if raw_column < 0:
            raise ValueError("Raw payload Parquet is missing the payload_json column")
        raw_type = parquet.schema_arrow.field(raw_column).type
        batch_cap = (
            _MAX_PAYLOAD_DICTIONARY_BATCH_ROWS
            if pa.types.is_dictionary(raw_type)
            else _MAX_PARQUET_BATCH_ROWS
        )
        for group in range(parquet.metadata.num_row_groups):
            cache = None
            batches = parquet.iter_batches(
                row_groups=[group],
                batch_size=min(batch_size, batch_cap),
                use_threads=False,
            )
            try:
                for batch in batches:
                    column = batch.schema.get_field_index("payload_json")
                    payloads = batch.column(column)
                    metadata = batch.select(
                        [index for index in range(batch.num_columns) if index != column]
                    )
                    if isinstance(payloads, pa.DictionaryArray):
                        dictionary = payloads.dictionary
                        if cache is None or not cache.dictionary.equals(dictionary):
                            cache = _DictionaryPayloadValidationCache(dictionary)
                        else:
                            # Release the previous native dictionary when the
                            # decoder supplies a new, exactly equal copy.
                            cache.dictionary = dictionary
                        for index in range(batch.num_rows):
                            row = metadata.slice(index, 1).to_pylist()[0]
                            raw_index = payloads.indices[index].as_py()
                            row["payload_json"] = (
                                None
                                if raw_index is None
                                else _DictionaryPayloadValue(cache, raw_index)
                            )
                            yield row
                            del row
                        del dictionary
                    else:
                        # Nullable or non-dictionary columns retain the same
                        # exact-text validation path, one Python body at a time.
                        cache = None
                        for index in range(batch.num_rows):
                            row = metadata.slice(index, 1).to_pylist()[0]
                            row["payload_json"] = payloads[index].as_py()
                            yield row
                            del row
                    del metadata, payloads, batch
            finally:
                batches.close()
                cache = None
    finally:
        parquet.close()


def iter_validated_payloads(
    directory: str | Path, *, batch_size: int = 1024
) -> Iterator[PayloadReference]:
    """Validate every raw occurrence without expanding repeated dictionary bodies.

    Original Parquets remain authoritative. The loader checks each returned
    pointer against copied owners, checks reaction provenance against the exact
    source text digest and parsed shape, and checks the consumed count against
    the manifest; no source payload table is created in PostgreSQL.

    Raw dictionary batches contain at most 1024 owners (generic nested-record
    batches remain capped at 64). One current dictionary has at most 256 cached
    index results and is discarded at row-group boundaries. Non-dictionary text
    uses the per-call exact-text LRU, capped at 256 entries and 64 MiB of charged
    key/entry storage. Parsed Python bodies are never retained in either cache.
    """
    _validate_batch_size(batch_size)
    directory = Path(directory)
    cache = _PayloadValidationCache()
    rows = _iter_payload_rows(directory / "evidence_payloads.parquet", batch_size=batch_size)
    try:
        for ordinal, row in enumerate(rows):
            pointers = [field for field in ("relation_key", "entity_key") if row[field] is not None]
            if len(pointers) != 1:
                raise ValueError(f"Payload {ordinal} must refer to exactly one entity or relation")
            owner = pointers[0]
            _validate_key(row[owner], owner)
            digest = shape = None
            if isinstance(row["payload_json"], _DictionaryPayloadValue):
                value = row["payload_json"]
                digest, shape = value.cache.validate(value.index, ordinal)
                del value
            elif row["payload_json"] is not None:
                digest, shape = cache.validate(row["payload_json"], ordinal)
            yield PayloadReference(
                ordinal,
                "entity" if owner == "entity_key" else "relation",
                row[owner],
                row["source"],
                row["row_id"],
                digest,
                shape,
            )
            del row
    finally:
        close = getattr(rows, "close", None)
        if close is not None:
            close()
