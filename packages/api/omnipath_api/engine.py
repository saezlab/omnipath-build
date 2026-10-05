"""High-performance analytical serving engine using DuckDB over Parquet files.
Supports multi-resource union, predicate pushdown, faceted counts, and binary exports.
"""

from __future__ import annotations

import logging
import json
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_api.store.inventory import ReleaseStore, InventorySnapshot
from omnipath_api.store.query_cache import QueryCache
from omnipath_api.store.query_pool import QueryPool
from omnipath_api.store.connection import get_connection


from omnipath_api.queries.resources import ResourcesQueries
from omnipath_api.queries.relations import RelationsQueries
from omnipath_api.queries.entities import EntitiesQueries
from omnipath_api.queries.facets import FacetsQueries
from omnipath_api.queries.export import ExportQueries
from omnipath_api.queries.evidence import EvidenceQueries
from omnipath_api.queries.selection import SelectionQueries
from omnipath_api.queries.ontology import OntologyQueries
from omnipath_api.queries.stats import StatsQueries


logger = logging.getLogger(__name__)


class ParquetServingEngine(
    ResourcesQueries,
    RelationsQueries,
    EntitiesQueries,
    FacetsQueries,
    ExportQueries,
    EvidenceQueries,
    SelectionQueries,
    OntologyQueries,
    StatsQueries,
):
    """Out-of-core streaming query engine powered by DuckDB."""

    _CHUNKS_FILE = ".entities.chunks.parquet"
    _ENTITY_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
    _ENTITY_SCALAR_COLS = (
        "entity_key, entity_type, namespace, identifier, taxon, label, "
        "has_hierarchy, parent_count, child_count, reference_entity_key, gene_reference_keys"
    )

    def __init__(
        self, data_root: str | Path = "data", *, public_data_url: str | None = None
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.public_data_url = (
            public_data_url
            if public_data_url is not None
            else os.getenv("OMNIPATH_PUBLIC_DATA_URL", "")
        ).rstrip("/")
        if self.public_data_url:
            url = urlsplit(self.public_data_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.netloc
                or url.query
                or url.fragment
                or url.username
            ):
                raise ValueError(
                    "OMNIPATH_PUBLIC_DATA_URL must be an HTTP(S) base URL without credentials, query or fragment"
                )
        self.read_only = False
        self.releases = ReleaseStore(self.data_root)
        self._release_scope: ContextVar[dict[str, Any] | None] = ContextVar(
            "release_scope", default=None
        )
        self._taxonomy_scope: ContextVar[dict[str, str] | None] = ContextVar(
            "taxonomy_scope", default=None
        )
        self._db_local = threading.local()
        self._connections = []
        self._connections_lock = threading.Lock()
        self._db_generation = 0
        self._inventory_lock = threading.Lock()
        self._inventory_fingerprint: tuple[tuple[Any, ...], ...] | None = None
        self._facet_cache = QueryCache()
        self._detail_cache = QueryCache(max_entries=256, max_bytes=32 * 1024**2)
        self._relationship_cache = QueryCache(max_entries=128, max_bytes=32 * 1024**2)
        self._example_cache = QueryCache(max_entries=16, max_bytes=1024**2)
        self._inventory = InventorySnapshot({}, {}, ())
        self._inventory_scope: ContextVar[InventorySnapshot | None] = ContextVar(
            "inventory_scope", default=None
        )
        self._query_pool = QueryPool(
            self._open_query_connection,
            lambda: self._db_generation,
            size=int(os.getenv("OMNIPATH_QUERY_CONCURRENCY", "2")),
            timeout=float(os.getenv("OMNIPATH_QUERY_WAIT_SECONDS", "30")),
        )
        with self.query_scope():
            self._refresh_inventory_if_stale(force=True)

    @staticmethod
    def _is_entity_key(token: str) -> bool:
        return bool(ParquetServingEngine._ENTITY_KEY_RE.fullmatch(token))

    def _split_entity_tokens(self, tokens: list[str]) -> tuple[list[str], list[str]]:
        keys: list[str] = []
        other: list[str] = []
        for token in tokens:
            (keys if self._is_entity_key(token) else other).append(token)
        return keys, other

    @classmethod
    def _version_is_ready(cls, ver_dir: Path) -> bool:
        """Skip versions still being written (streaming ingest leaves chunk sidecars)."""
        ent_file = ver_dir / "entities.parquet"
        rel_file = ver_dir / "relations.parquet"
        if not ent_file.is_file() or not rel_file.is_file():
            return False
        if (ver_dir / cls._CHUNKS_FILE).exists():
            return False
        return True

    def _scan_inventory_fingerprint(self) -> tuple[tuple[Any, ...], ...]:
        """Cheap disk snapshot of complete resource versions (path, mtime, size)."""
        resources_dir = self.data_root / "resources"
        if not resources_dir.exists():
            return ()
        items: list[tuple[Any, ...]] = []
        for res_dir in resources_dir.iterdir():
            if not res_dir.is_dir():
                continue
            for ver_dir in res_dir.iterdir():
                if not ver_dir.is_dir() or not self._version_is_ready(ver_dir):
                    continue
                ent_file = ver_dir / "entities.parquet"
                rel_file = ver_dir / "relations.parquet"
                stats_file = ver_dir / "resolution_stats.json"
                try:
                    ent_stat = ent_file.stat()
                    rel_stat = rel_file.stat()
                    stats_mtime = stats_file.stat().st_mtime_ns if stats_file.is_file() else 0
                except OSError:
                    continue
                items.append(
                    (
                        f"{res_dir.name}/{ver_dir.name}",
                        ent_stat.st_mtime_ns,
                        ent_stat.st_size,
                        rel_stat.st_mtime_ns,
                        rel_stat.st_size,
                        stats_mtime,
                    )
                )
        return tuple(sorted(items))

    def _open_query_connection(self) -> duckdb.DuckDBPyConnection:
        return get_connection(memory_limit="1GB", threads=2)

    @contextmanager
    def query_scope(self):
        """Lease one bounded database for a complete query, including exports."""
        if getattr(self._db_local, "query_connection", None) is not None:
            yield
            return
        with self._query_pool.acquire() as connection:
            self._db_local.query_connection = connection
            try:
                yield
            finally:
                self._db_local.query_connection = None

    def _close_direct_connection(self, connection):
        try:
            connection.close()
        finally:
            with self._connections_lock:
                if connection in self._connections:
                    self._connections.remove(connection)

    @property
    def _db(self) -> duckdb.DuckDBPyConnection:
        """One DuckDB connection per thread so relation search and facets can overlap."""
        scoped = getattr(self._db_local, "query_connection", None)
        if scoped is not None:
            return scoped
        gen = self._db_generation
        conn: duckdb.DuckDBPyConnection | None = getattr(self._db_local, "conn", None)
        conn_gen = getattr(self._db_local, "gen", None)
        if conn is None or conn_gen != gen:
            if conn is not None:
                try:
                    self._close_direct_connection(conn)
                except duckdb.Error:
                    logger.exception("Failed to close stale DuckDB connection")
            conn = self._open_query_connection()
            with self._connections_lock:
                self._connections.append(conn)
            self._db_local.conn = conn
            self._db_local.gen = gen
        return conn

    def _reset_query_connection(self) -> None:
        self._db_generation += 1
        self._facet_cache.clear()
        conn = getattr(self._db_local, "conn", None)
        if conn is not None:
            try:
                self._close_direct_connection(conn)
            except duckdb.Error:
                logger.exception("Failed to reset DuckDB connection")
            self._db_local.conn = None
            self._db_local.gen = None

    def _refresh_inventory_if_stale(self, *, force: bool = False) -> bool:
        """Re-index parquet resources when the data directory changes. Returns True on reload."""
        if self._inventory_scope.get() is not None and not force:
            return False
        fingerprint = self._scan_inventory_fingerprint()
        if not force and fingerprint == self._inventory_fingerprint:
            return False
        with self._inventory_lock:
            fingerprint = self._scan_inventory_fingerprint()
            if not force and fingerprint == self._inventory_fingerprint:
                return False
            if self._inventory_fingerprint is not None:
                self._reset_query_connection()
            self._discover_inventory()
            self._inventory_fingerprint = fingerprint
            return True

    def _discover_inventory(self) -> None:
        """Find all available resource parquet files and release manifests."""
        resources = {}
        resources_dir = self.data_root / "resources"
        if resources_dir.exists():
            for res_dir in resources_dir.iterdir():
                if not res_dir.is_dir():
                    continue
                res_name = res_dir.name
                for ver_dir in res_dir.iterdir():
                    if not ver_dir.is_dir() or not self._version_is_ready(ver_dir):
                        continue
                    ent_file = ver_dir / "entities.parquet"
                    rel_file = ver_dir / "relations.parquet"
                    key = f"{res_name}/{ver_dir.name}"
                    payload_file = ver_dir / "evidence_payloads.parquet"
                    try:
                        rel_rows = pq.read_metadata(rel_file).num_rows
                    except (OSError, pa.ArrowException):
                        logger.exception("Skipping unreadable resource %s", ver_dir)
                        continue
                    try:
                        label_quality = float(
                            self._db.execute(
                                f"SELECT coalesce(avg((label IS DISTINCT FROM identifier)::int), 0) FROM {self._read_expr([str(ent_file)])}"
                            ).fetchone()[0]
                            or 0
                        )
                    except duckdb.Error:
                        logger.warning("Cannot score entity labels in %s", ent_file, exc_info=True)
                        label_quality = 0.0
                    mtime = max(ent_file.stat().st_mtime, rel_file.stat().st_mtime)
                    resources[key] = {
                        "resource": res_name,
                        "version": ver_dir.name,
                        "entities_path": ent_file,
                        "relations_path": rel_file,
                        "payloads_path": payload_file if payload_file.exists() else None,
                        "relations_count": rel_rows,
                        "label_quality": label_quality,
                        "mtime": mtime,
                    }
        self._inventory = InventorySnapshot(
            resources, self._pick_latest_resources(resources), self._scan_inventory_fingerprint()
        )

    def reload_resources(self) -> None:
        """Refresh inventory of parquet resources from disk."""
        with self.query_scope():
            self._refresh_inventory_if_stale(force=True)

    @property
    def resources(self):
        return (self._inventory_scope.get() or self._inventory).resources

    @property
    def _latest_by_resource(self):
        return (self._inventory_scope.get() or self._inventory).latest

    @contextmanager
    def release_scope(self, version: str | None = None, *, snapshot=None):
        """Pin all storage lookups to one atomically published inventory generation."""
        if snapshot is None:
            self._refresh_inventory_if_stale()
            snapshot = self._inventory_scope.get() or self._inventory
        inventory_token = self._inventory_scope.set(snapshot)
        token = taxonomy_token = None
        try:
            version = version or self.releases.default()
            if version == "working":
                version = "latest"
            manifest = None if version == "latest" else self.releases.get(version)
            if manifest is not None:
                for source, resource_version in manifest["resources"].items():
                    key = f"{source}/{resource_version}"
                    if key not in self.resources or not self.resources[key].get("payloads_path"):
                        raise ValueError(f"OmniPath {version} requires unavailable resource {key}")
            token = self._release_scope.set(manifest)
            taxonomy_token = self._taxonomy_scope.set(self._resolve_taxonomy_names())
            yield version
        finally:
            if taxonomy_token is not None:
                self._taxonomy_scope.reset(taxonomy_token)
            if token is not None:
                self._release_scope.reset(token)
            self._inventory_scope.reset(inventory_token)

    def inventory_snapshot(self):
        self._refresh_inventory_if_stale()
        return self._inventory

    def close(self):
        """Release all connections after the HTTP worker has drained requests."""
        self._query_pool.close()
        for connection in tuple(self._connections):
            self._close_direct_connection(connection)

    def _visible_resources(self) -> dict[str, dict[str, Any]]:
        manifest = self._release_scope.get()
        if manifest is None:
            return self.resources
        return {
            f"{source}/{version}": self.resources[f"{source}/{version}"]
            for source, version in manifest["resources"].items()
        }

    def _visible_latest(self) -> dict[str, dict[str, Any]]:
        if self._release_scope.get() is None:
            return self._latest_by_resource
        return {info["resource"]: info for info in self._visible_resources().values()}

    def _pick_latest_resources(self, resources=None) -> dict[str, dict[str, Any]]:
        """Prefer the highest numeric version; legacy-only resources use file time."""

        def rank(info: dict[str, Any]) -> tuple:
            version = info["version"]
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
                return (1, tuple(map(int, version.split("."))), version)
            return (0, info.get("mtime", 0), version)

        latest: dict[str, dict[str, Any]] = {}
        for info in (resources if resources is not None else self.resources).values():
            current = latest.get(info["resource"])
            if current is None or rank(info) > rank(current):
                latest[info["resource"]] = info
        return latest

    def _disabled_query_resources(self) -> set[str]:
        """Live serving policy, independent of immutable releases and downloads."""
        try:
            policy = json.loads((self.data_root / "query_policy.json").read_text())
        except FileNotFoundError:
            return set()
        disabled = policy.get("disabled_resources", [])
        if not isinstance(disabled, list) or any(not isinstance(item, str) for item in disabled):
            raise ValueError(
                "query_policy.json disabled_resources must be a list of resource slugs"
            )
        return set(disabled)

    def _selected_resource_infos(
        self, selected_resources: list[str] | None = None, *, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        self._refresh_inventory_if_stale()
        visible = self._visible_resources()
        latest = self._visible_latest()
        disabled = set() if include_disabled else self._disabled_query_resources()
        if not visible:
            return []
        if not selected_resources:
            return [info for info in latest.values() if info["resource"] not in disabled]
        infos: list[dict[str, Any]] = []
        seen: set[str] = set()
        for res in selected_resources:
            info = visible.get(res) or latest.get(res)
            if info is None:
                raise ValueError(
                    f"Resource {res!r} is unavailable in the selected OmniPath release"
                )
            if info["resource"] in disabled:
                raise ValueError(f"Resource {res!r} is disabled for queries")
            marker = str(info["relations_path"])
            if marker not in seen:
                infos.append(info)
                seen.add(marker)
        return infos

    def _resolve_relation_paths(self, selected_resources: list[str] | None = None) -> list[str]:
        """Resolve Parquet file paths for selected resources."""
        return [
            str(info["relations_path"])
            for info in self._selected_resource_infos(selected_resources)
        ]

    def _resolve_entity_paths(self, selected_resources: list[str] | None = None) -> list[str]:
        """Resolve entity Parquet file paths."""
        return [
            str(info["entities_path"]) for info in self._selected_resource_infos(selected_resources)
        ]

    def _resolve_payload_paths(self, selected_resources: list[str] | None = None) -> list[str]:
        """Resolve evidence payload Parquet file paths."""
        return [
            str(info["payloads_path"])
            for info in self._selected_resource_infos(selected_resources)
            if info.get("payloads_path")
        ]

    @staticmethod
    def _read_expr(paths: list[str]) -> str:
        from omnipath_api.molecular import read

        return read(paths, union_by_name=True)

    @staticmethod
    def _jsonify(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): ParquetServingEngine._jsonify(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [ParquetServingEngine._jsonify(v) for v in value]
        try:
            import numpy as np

            if isinstance(value, np.ndarray):
                return ParquetServingEngine._jsonify(value.tolist())
            if isinstance(value, np.integer):
                return int(value)
            if isinstance(value, np.floating):
                return float(value)
            if isinstance(value, np.bool_):
                return bool(value)
        except ModuleNotFoundError as exc:
            if exc.name != "numpy":
                raise
        return str(value)

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [item for item in value if item is not None and str(item).strip() != ""]
        return [value]

    def _fetch_dicts(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cursor = self._db.execute(sql, params or [])
        cols = [col[0] for col in cursor.description]
        return [self._jsonify(dict(zip(cols, row))) for row in cursor.fetchall()]

    RESOURCE_PARQUET_FILES = ("entities.parquet", "relations.parquet", "evidence_payloads.parquet")
    PUBLIC_DOWNLOAD_FILES = ("entities.parquet", "relations.parquet")
