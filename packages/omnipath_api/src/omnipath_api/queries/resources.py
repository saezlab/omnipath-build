"""Resources query service, including domain SQL and result shaping."""

from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote


import pyarrow as pa
import pyarrow.parquet as pq


logger = logging.getLogger(__name__)


class ResourcesQueries:
    """Resources queries over the engine storage and shaping contract."""

    def list_resources(self) -> list[dict[str, Any]]:
        """Return list of indexed resources with table statistics."""
        self._refresh_inventory_if_stale()
        out = []
        for key, paths in sorted(self._visible_resources().items()):
            try:
                rel_meta = pq.read_metadata(paths["relations_path"])
                ent_meta = pq.read_metadata(paths["entities_path"])
                out.append(
                    {
                        "key": key,
                        "resource": paths["resource"],
                        "queryable": paths["resource"] not in self._disabled_query_resources(),
                        "version": paths["version"],
                        "entities_count": ent_meta.num_rows,
                        "relations_count": rel_meta.num_rows,
                        "entities_size_kb": round(paths["entities_path"].stat().st_size / 1024, 1),
                        "relations_size_kb": round(
                            paths["relations_path"].stat().st_size / 1024, 1
                        ),
                    }
                )
            except (OSError, pa.ArrowException):
                logger.exception("Cannot inspect indexed resource %s", key)
                continue
        return out

    def resolve_resource_info(self, resource_id: str) -> dict[str, Any] | None:
        """Resolve a resource slug or `resource/version` key to its latest inventory row."""
        token = str(resource_id or "").strip().strip("/")
        if not token:
            return None
        self._refresh_inventory_if_stale()
        visible = self._visible_resources()
        if token in visible:
            return visible[token]
        latest = self._visible_latest().get(token)
        if latest:
            return latest
        for info in self._visible_latest().values():
            if info["resource"] == token:
                return info
        return None

    def _parquet_file_record(
        self, name: str, path: Path | None, *, include_schema: bool = False
    ) -> dict[str, Any] | None:
        if path is None:
            return None
        file_path = Path(path)
        if not file_path.is_file():
            return None
        try:
            meta = pq.read_metadata(file_path)
            rows = meta.num_rows
        except (OSError, pa.ArrowException):
            logger.exception("Cannot read artifact metadata %s", file_path)
            raise
        record: dict[str, Any] = {
            "name": name,
            "size_bytes": file_path.stat().st_size,
            "rows": rows,
        }
        if self.public_data_url:
            relative_path = file_path.relative_to(self.data_root).as_posix()
            record["url"] = f"{self.public_data_url}/{quote(relative_path, safe='/')}"
        if include_schema:
            try:
                schema = pq.read_schema(file_path)
                record["columns"] = [
                    {"name": field.name, "type": str(field.type)} for field in schema
                ]
            except (OSError, pa.ArrowException):
                logger.exception("Cannot read artifact schema %s", file_path)
                raise
        return record

    def _resource_file_map(self, info: dict[str, Any]) -> dict[str, Path | None]:
        return {
            "entities.parquet": info.get("entities_path"),
            "relations.parquet": info.get("relations_path"),
            "evidence_payloads.parquet": info.get("payloads_path"),
        }

    def _download_file_map(
        self, info: dict[str, Any], *, include_evidence: bool = False
    ) -> dict[str, Path | None]:
        files = self._resource_file_map(info)
        names = self.RESOURCE_PARQUET_FILES if include_evidence else self.PUBLIC_DOWNLOAD_FILES
        return {name: files[name] for name in names if name in files}

    def list_resource_files(
        self, resource_id: str, *, include_evidence: bool = False
    ) -> dict[str, Any] | None:
        info = self.resolve_resource_info(resource_id)
        if not info:
            return None
        files = [
            record
            for name, path in self._download_file_map(
                info, include_evidence=include_evidence
            ).items()
            if (record := self._parquet_file_record(name, path, include_schema=True))
        ]
        return {
            "resource_id": info["resource"],
            "version": info["version"],
            "files": files,
        }

    def get_resource_file_path(self, resource_id: str, filename: str) -> Path | None:
        if filename not in self.RESOURCE_PARQUET_FILES:
            return None
        info = self.resolve_resource_info(resource_id)
        if not info:
            return None
        path = self._resource_file_map(info).get(filename)
        if path and Path(path).is_file():
            return Path(path)
        return None

    def iter_resource_file_paths(
        self, resource_id: str, *, include_evidence: bool = False
    ) -> list[tuple[str, Path]]:
        info = self.resolve_resource_info(resource_id)
        if not info:
            return []
        out: list[tuple[str, Path]] = []
        for name, path in self._download_file_map(info, include_evidence=include_evidence).items():
            if path and Path(path).is_file():
                out.append((name, Path(path)))
        return out

    def list_resource_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        disabled = self._disabled_query_resources()
        for info in self._selected_resource_infos(include_disabled=True):
            files = [
                record
                for name, path in self._download_file_map(info).items()
                if (record := self._parquet_file_record(name, path))
            ]
            if not files:
                continue
            entity_rows = next(
                (item["rows"] for item in files if item["name"] == "entities.parquet"), 0
            )
            relation_rows = next(
                (item["rows"] for item in files if item["name"] == "relations.parquet"), 0
            )
            manifest_path = Path(info["entities_path"]).parent / "build_manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text())
            except FileNotFoundError:
                manifest = {}
            except (OSError, ValueError):
                logger.exception("Cannot read build manifest %s", manifest_path)
                raise
            from omnipath_core.resource_metadata import resource_metadata

            try:
                resolution = json.loads(
                    (manifest_path.parent / "resolution_stats.json").read_text()
                )
                if not isinstance(resolution, dict) or "input_entities" not in resolution:
                    resolution = None
            except FileNotFoundError:
                resolution = None
            except (OSError, ValueError):
                logger.exception("Cannot read resolution statistics for %s", manifest_path.parent)
                raise
            max_records = manifest.get("max_records")
            sample_build = max_records is not None if "max_records" in manifest else None
            catalog.append(
                {
                    "max_records": max_records,
                    "sample_build": sample_build,
                    "dataset_subset": manifest.get("datasets"),
                    "resolution_stats": resolution,
                    **resource_metadata(info["resource"], manifest),
                    "resource_id": info["resource"],
                    "queryable": info["resource"] not in disabled,
                    "resource_name": info["resource"],
                    "version": info["version"],
                    "entity_count": entity_rows,
                    "interaction_count": relation_rows,
                    "total_size_bytes": sum(item["size_bytes"] for item in files),
                    "files": files,
                }
            )
        return catalog
