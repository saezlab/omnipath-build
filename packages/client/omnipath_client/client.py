"""The installed resources as a DuckDB database, with the hosted API's queries."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import os
from pathlib import Path
import typing
from typing import Any

import duckdb
from omnipath_engine import ParquetServingEngine
from omnipath_engine.filters import SearchFilters
from omnipath_engine.queries.export import RELATION_EXPORT_LIMIT
import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import SERVING, ResourceRecord
from .database import DATABASE, in_sync, synchronize
from .errors import ClientError
from .installation import data_directory, read_installation

# search(): the engine's merged entities under the entity table's column names.
_ENTITIES = pa.schema(
    [
        ("entity_key", pa.string()),
        ("label", pa.string()),
        ("entity_type", pa.string()),
        ("namespace", pa.string()),
        ("identifier", pa.string()),
        ("taxon", pa.string()),
        ("relation_count", pa.int64()),
        ("reference_entity_key", pa.string()),
        ("gene_reference_keys", pa.list_(pa.string())),
    ]
)


def _filters(filters: dict[str, Any]) -> dict[str, Any]:
    """The explorer's filters by name; one value stands for a list of one."""
    fields = SearchFilters.model_fields
    unknown = set(filters) - fields.keys()
    if unknown:
        raise TypeError(f"Unknown filters: {sorted(unknown)}; available: {sorted(fields)}")
    return {
        name: [value]
        if typing.get_origin(fields[name].annotation) is list and isinstance(value, (str, int))
        else value
        for name, value in filters.items()
    }


class Client:
    """The installed resources of one data directory; use as a context manager.

    Everything is read from local files: run ``init()`` first. ``sql`` queries the
    directory's DuckDB database; ``search``, ``relations`` and ``entity`` answer like
    the hosted API and explorer. Returned relations remain usable until this client is
    closed. Instances are intended for one thread. Connect again after ``init()``
    changed the directory.
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        memory_limit: str = "2GB",
        threads: int | None = None,
    ) -> None:
        self.data_dir = data_directory(data_dir)
        installation = read_installation(self.data_dir)
        if installation is None or not installation["resources"]:
            raise ClientError(
                f"No OmniPath data in {self.data_dir}; run omnipath_client.init() first"
            )
        self._release = installation["release"]
        self._catalog: dict[str, ResourceRecord] = {
            record["resource_id"]: record for record in installation["resources"]
        }
        for record in self._catalog.values():
            for name in (*(f["name"] for f in record["files"]), *(f"{t}.parquet" for t in SERVING)):
                self._check(record, name)
        if not in_sync(self.data_dir, installation):
            # A moved directory or an interrupted init(): views are rewritten at once.
            try:
                synchronize(self.data_dir, installation, memory_limit)
            except (OSError, duckdb.Error) as exc:
                raise ClientError(
                    f"The database in {self.data_dir} is out of date; run init() again"
                ) from exc
        self.path = self.data_dir / DATABASE
        self._memory_limit = memory_limit
        self._threads = threads
        self._engine: ParquetServingEngine | None = None
        self._closed = False
        connection = duckdb.connect(str(self.path), read_only=True)
        try:
            connection.execute("SET memory_limit = ?", [memory_limit])
            if threads is not None:
                connection.execute("SET threads = ?", [threads])
        except duckdb.Error:
            connection.close()
            raise
        self._connection = connection

    @property
    def release(self) -> str:
        """The release of the last ``init()``."""
        return self._release

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClientError("Client is closed")

    def _check(self, record: dict[str, Any], name: str) -> None:
        """One file of an installed resource; missing or truncated files are errors."""
        path = self.data_dir / "resources" / record["resource_id"] / record["version"] / name
        if not path.resolve().is_relative_to(self.data_dir.resolve()):
            raise ClientError("Installed file escapes the data directory")
        size = next((f["size_bytes"] for f in record["files"] if f["name"] == name), None)
        if not path.is_file() or (size is not None and path.stat().st_size != size):
            raise ClientError(f"Missing or truncated file: {path}; run init() again")

    def resources(self) -> list[dict[str, Any]]:
        """The installed resources with their catalog metadata; an independent copy."""
        self._ensure_open()
        return deepcopy(list(self._catalog.values()))

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """The read-only DuckDB connection to the database, for DuckDB's own interface."""
        self._ensure_open()
        return self._connection

    def sql(self, query: str, parameters: Sequence[Any] | None = None) -> duckdb.DuckDBPyRelation:
        """Run SQL on the database: ``entity``, ``relation`` and the other tables over
        every installed resource (with ``_resource`` and ``_resource_version``), or one
        resource's tables as ``signor.relation``. Caller-supplied local SQL; not a
        sandbox."""
        return self.connection.sql(
            query, params=list(parameters) if parameters is not None else None
        )

    @property
    def engine(self) -> ParquetServingEngine:
        """The hosted API's query engine over the installed resources, for everything
        the API offers beyond ``search``, ``relations`` and ``entity``."""
        self._ensure_open()
        if self._engine is None:
            self._engine = ParquetServingEngine(
                self.data_dir,
                memory_limit=self._memory_limit,
                threads=self._threads or os.cpu_count() or 2,
            )
        return self._engine

    def search(self, query: str, *, limit: int = 20, **filters: Any) -> duckdb.DuckDBPyRelation:
        """Entities matching a name or identifier, merged across resources and ranked
        as in the explorer's search. ``filters`` are the explorer's, such as
        ``entity_types``, ``taxonomy_ids`` and ``sources``."""
        found = self.engine.search_entities_api(query, filters=_filters(filters), limit=limit)
        rows = [
            dict(
                entity_key=entity["entityPk"],
                label=entity.get("label"),
                entity_type=entity.get("entityType"),
                namespace=entity.get("canonicalIdentifierType"),
                identifier=entity.get("canonicalIdentifier"),
                taxon=entity.get("taxonomyId"),
                relation_count=entity.get("relationCount"),
                reference_entity_key=entity.get("referenceEntityKey"),
                gene_reference_keys=entity.get("geneReferenceKeys") or [],
            )
            for entity in found["entities"]
        ]
        return self.connection.from_arrow(pa.Table.from_pylist(rows, schema=_ENTITIES))

    def relations(
        self,
        entities: str | Sequence[str] | None = None,
        *,
        limit: int = RELATION_EXPORT_LIMIT,
        **filters: Any,
    ) -> duckdb.DuckDBPyRelation:
        """Relations as the explorer exports them: one row per relation with its
        annotations, evidence and the product records its evidence names.

        ``entities`` are exact names, identifiers, entity keys or group keys such as
        ``gene:entrez:7157``, at either endpoint. ``filters`` are the explorer's, such
        as ``sources``, ``predicates``, ``taxonomy_ids``, ``signs``,
        ``reference_entity_keys``, ``protein_entity_keys`` and ``isoform_identifiers``.
        At most 100,000 relations; larger selections are SQL on ``relation``."""
        if not 1 <= limit <= RELATION_EXPORT_LIMIT:
            raise ValueError(f"limit must be between 1 and {RELATION_EXPORT_LIMIT}")
        if entities is not None:
            if "entity_ids" in filters:
                raise TypeError("Give entities once: positionally or as entity_ids")
            filters["entity_ids"] = [entities] if isinstance(entities, str) else list(entities)
        data, _ = self.engine.export_slice(_filters(filters), None, limit)
        return self.connection.from_arrow(pq.read_table(pa.BufferReader(data)))

    def entity(self, entity_key: str) -> dict[str, Any] | None:
        """One entity by its exact key (as ``search`` returns it), with its identifiers,
        annotations and relationship summary as the explorer's detail page shows them;
        None when no installed resource has it."""
        return self.engine.get_entity_details(entity_key)

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            if self._engine is not None:
                self._engine.close()
            self._closed = True

    def __enter__(self) -> Client:
        self._ensure_open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def connect(
    data_dir: str | Path | None = None,
    *,
    memory_limit: str = "2GB",
    threads: int | None = None,
) -> Client:
    """Open the data directory's database: the directory given, else OMNIPATH_DATA_DIR,
    else the per-user default."""
    return Client(data_dir, memory_limit=memory_limit, threads=threads)
