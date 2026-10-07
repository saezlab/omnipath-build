"""Remote discovery, lazy DuckDB queries and portable offline snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import quote

import duckdb
import httpx

from .contracts import (
    TABLES,
    ResourceRecord,
    validate_catalog,
    validate_resource,
    validate_snapshot,
)
from .contracts import validate_url as _url
from .errors import ClientError

# Development deployment; override with ``api_url`` or the OMNIPATH_API_URL variable.
DEFAULT_API_URL = "https://omnipath-metabo-dev.schaul.click/api"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sides(endpoint: str) -> tuple[str, ...]:
    if endpoint not in {"any", "source", "target", "both"}:
        raise ValueError("endpoint must be any, source, target or both")
    return {"source": ("subject",), "target": ("object",)}.get(endpoint, ("subject", "object"))


class Client:
    """One catalog snapshot and DuckDB connection; use as a context manager.

    Discovery is lazy. Even ``release='latest'`` is resolved once per client.
    Returned relations remain usable until this client is closed. Instances are
    intended for one thread. Construct another client to discover newer versions.
    """

    def __init__(
        self,
        api_url: str | None = None,
        *,
        release: str = "latest",
        timeout: float = 30,
        memory_limit: str = "2GB",
        http_client: httpx.Client | None = None,
    ) -> None:
        api_url = api_url or os.environ.get("OMNIPATH_API_URL") or DEFAULT_API_URL
        self.api_url = _url(api_url).rstrip("/")
        if not isinstance(release, str) or not release.strip():
            raise ValueError("release must not be empty")
        self._release = release
        self._http = http_client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._owns_http = http_client is None
        self._memory_limit = memory_limit
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._httpfs_loaded = False
        self._catalog: dict[str, ResourceRecord] | None = None
        self._snapshot: Path | None = None
        self._verified: set[Path] = set()
        self._closed = False

    @property
    def release(self) -> str:
        return self._release

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClientError("Client is closed")

    def _get(self, path: str, **params: Any) -> Any:
        self._ensure_open()
        if self._snapshot is not None:
            raise ClientError("This client is offline; no API requests are available")
        response = self._http.get(f"{self.api_url}/{path}", params=params)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise ClientError(f"Invalid JSON response from {path}") from exc

    def releases(self) -> list[dict[str, Any]]:
        """List published OmniPath release manifests from the API."""
        return self._get("releases")["releases"]

    def resources(self) -> list[dict[str, Any]]:
        """Discover resource metadata once; return an independent copy."""
        self._ensure_open()
        if self._catalog is None:
            response = self._get("resources", shape="svelte", release=self.release)
            if not isinstance(response, dict):
                raise ClientError("Expected a catalog object")
            self._catalog = validate_catalog(response.get("resources"))
        return deepcopy(list(self._catalog.values()))

    def _select(self, resources: str | Sequence[str] | None) -> list[dict[str, Any]]:
        self._ensure_open()
        if self._catalog is None:
            self.resources()
        assert self._catalog is not None
        names = (
            list(self._catalog)
            if resources is None
            else ([resources] if isinstance(resources, str) else list(resources))
        )
        if not names:
            raise ValueError("Select at least one resource")
        unknown = set(names) - self._catalog.keys()
        if unknown:
            raise ValueError(f"Unknown resources in selected release: {sorted(unknown)}")
        return [self._catalog[name] for name in dict.fromkeys(names)]

    def files(self, resource: str, *, include_evidence: bool = False) -> list[dict[str, Any]]:
        """Return pinned file records; raw evidence requires an explicit opt-in."""
        record = self._select(resource)[0]
        if include_evidence and not any(
            f["name"] == "evidence_payloads.parquet" for f in record["files"]
        ):
            if self._snapshot is not None:
                raise ClientError(
                    "Snapshot lacks evidence; download again with include_evidence=True"
                )
            result = validate_resource(
                self._get(
                    f"resources/{quote(resource, safe='')}/files",
                    release=self.release,
                    include_evidence="true",
                )
            )
            if result["resource_id"] != resource or result["version"] != record["version"]:
                raise ClientError(
                    "Resource changed since discovery; use a pinned release or create a new Client"
                )
            # Do not silently change URLs/metadata already resolved by this client.
            evidence = [f for f in result["files"] if f["name"] == "evidence_payloads.parquet"]
            if not evidence:
                raise ClientError(f"No evidence artifact available for {resource}")
            record["files"].extend(evidence)
        return deepcopy(
            [
                f
                for f in record["files"]
                if include_evidence or f["name"] != "evidence_payloads.parquet"
            ]
        )

    def _path(self, record: dict[str, Any], table: str) -> str:
        files = self.files(record["resource_id"], include_evidence=table == "evidence_payloads")
        artifact = next((f for f in files if f["name"] == f"{table}.parquet"), None)
        if artifact is None:
            raise ClientError(f"Missing {table}.parquet for {record['resource_id']}")
        if self._snapshot is not None:
            path = (
                self._snapshot
                / "resources"
                / record["resource_id"]
                / record["version"]
                / artifact["name"]
            )
            if not path.resolve().is_relative_to(self._snapshot):
                raise ClientError("Snapshot artifact escapes its directory")
            if path not in self._verified:
                if not path.is_file() or path.stat().st_size != artifact["size_bytes"]:
                    raise ClientError(f"Missing or truncated snapshot artifact: {path}")
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != artifact.get("sha256"):
                    raise ClientError(f"Snapshot checksum mismatch: {path}")
                self._verified.add(path)
            return str(path)
        if not artifact.get("url"):
            raise ClientError(
                "Server does not provide static artifact URLs; configure static hosting or use API downloads"
            )
        return _url(artifact["url"])

    def _db(self) -> duckdb.DuckDBPyConnection:
        self._ensure_open()
        if self._connection is None:
            connection = duckdb.connect()
            try:
                connection.execute("SET memory_limit = ?", [self._memory_limit])
            except duckdb.Error:
                connection.close()
                raise
            self._connection = connection
        if self._snapshot is None and not self._httpfs_loaded:
            try:
                self._connection.execute("LOAD httpfs")
            except duckdb.Error:
                try:
                    self._connection.execute("INSTALL httpfs; LOAD httpfs")
                except duckdb.Error as error:
                    raise ClientError(
                        "DuckDB httpfs could not load; install the extension with network access first"
                    ) from error
            self._httpfs_loaded = True
        return self._connection

    def _table_sql(self, table: str, resources: str | Sequence[str] | None) -> str:
        parts = []
        for record in self._select(resources):
            path = self._path(record, table)
            parts.append(
                f"SELECT *, {_literal(record['resource_id'])} AS _resource, "
                f"{_literal(record['version'])} AS _resource_version FROM read_parquet({_literal(path)})"
            )
        return " UNION ALL BY NAME ".join(parts)

    def table(
        self,
        table: str,
        resources: str | Sequence[str] | None = None,
        *,
        filters: Mapping[str, Any] | None = None,
        columns: Sequence[str] | None = None,
    ) -> duckdb.DuckDBPyRelation:
        """Return a lazy relation; filters are equality/IN predicates, ANDed together.

        None means SQL NULL. Values in a list are ORed, including NULL. An empty
        list matches nothing. Columns are quoted and values are parameterized.
        ``_resource`` and ``_resource_version`` preserve per-file provenance.
        """
        if table not in TABLES:
            raise ValueError(f"table must be one of {TABLES}")
        sql = self._table_sql(table, resources)
        predicates, parameters = [], []
        for column, value in (filters or {}).items():
            values = list(value) if isinstance(value, (list, tuple, set)) else [value]
            non_null = [v for v in values if v is not None]
            clauses = []
            if non_null:
                clauses.append(f"{_identifier(column)} IN ({','.join('?' for _ in non_null)})")
                parameters.extend(non_null)
            if None in values:
                clauses.append(f"{_identifier(column)} IS NULL")
            predicates.append("(" + " OR ".join(clauses) + ")" if clauses else "FALSE")
        if columns is not None and (isinstance(columns, str) or not columns):
            raise ValueError("columns must be a non-empty sequence of column names")
        projection = ", ".join(_identifier(c) for c in columns) if columns is not None else "*"
        query = f"SELECT {projection} FROM ({sql}) AS data"
        if predicates:
            query += " WHERE " + " AND ".join(predicates)
        return self._db().sql(query, params=parameters)

    def entities(
        self, resources: str | Sequence[str] | None = None, **kwargs: Any
    ) -> duckdb.DuckDBPyRelation:
        """Query entity records; accepts table() filters and columns."""
        return self.table("entity", resources, **kwargs)

    def relations(
        self, resources: str | Sequence[str] | None = None, **kwargs: Any
    ) -> duckdb.DuckDBPyRelation:
        """Query relation records without cross-resource deduplication."""
        return self.table("relation", resources, **kwargs)

    def evidence(self, resources: str | Sequence[str], **kwargs: Any) -> duckdb.DuckDBPyRelation:
        """Explicitly query raw evidence payloads for selected resources."""
        return self.table("evidence_payloads", resources, **kwargs)

    def _lookup_sql(
        self, queries: str | Sequence[str], resources: str | Sequence[str] | None
    ) -> str:
        queries = [queries] if isinstance(queries, str) else list(queries)
        if not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
            raise ValueError("Provide at least one non-empty name, identifier or entity key")
        terms = ", ".join(_literal(q.lower()) for q in queries)
        return (
            f"SELECT * FROM ({self._table_sql('entity', resources)}) AS e WHERE "
            f"lower(label) IN ({terms}) OR lower(identifier) IN ({terms}) OR "
            f"lower(entity_key) IN ({terms}) OR (_resource, entity_id) IN (SELECT _resource, "
            f"entity_id FROM ({self._table_sql('entity_identifier', resources)}) "
            f"WHERE lower(id) IN ({terms}))"
        )

    def lookup(
        self,
        queries: str | Sequence[str],
        *,
        resources: str | Sequence[str] | None = None,
    ) -> duckdb.DuckDBPyRelation:
        """Match exact names, canonical IDs, alias IDs or entity keys, ignoring case.

        All matches are returned, including ambiguity across taxa and resources.
        Use the returned relation's filter() to narrow them. No fuzzy matching,
        namespace translation or identifier pivoting is performed.
        """
        sql = self._lookup_sql(queries, resources)
        return self._db().sql(sql)

    def related(
        self,
        query: str | Sequence[str] | None = None,
        *,
        resources: str | Sequence[str],
        subject: str | Sequence[str] | None = None,
        object: str | Sequence[str] | None = None,
    ) -> duckdb.DuckDBPyRelation:
        """Find relations incident to exact lookup matches in selected resources.

        A positional query matches either endpoint. Subject and object constrain
        their respective endpoints; multiple constraints are ANDed. Returns the
        relation rows with provenance, without identifier pivots.
        """
        if query is None and subject is None and object is None:
            raise ValueError(
                "Supply query, subject or object; use relations() for an unfiltered table"
            )
        clauses = []
        for terms, endpoints in (
            (query, ("subject", "object")),
            (subject, ("subject",)),
            (object, ("object",)),
        ):
            if terms is None:
                continue
            matches = self._lookup_sql(terms, resources)
            endpoint_clause = " OR ".join(
                f"e.entity_key = r.{side}_entity_key" for side in endpoints
            )
            clauses.append(
                f"EXISTS (SELECT 1 FROM ({matches}) AS e WHERE e._resource = r._resource AND ({endpoint_clause}))"
            )
        sql = (
            f"SELECT r.* FROM ({self._table_sql('relation', resources)}) AS r WHERE "
            + " AND ".join(clauses)
        )
        return self._db().sql(sql)

    def related_reference(
        self, reference_entity_key: str, *, resources: str | Sequence[str], endpoint: str = "any"
    ) -> duckdb.DuckDBPyRelation:
        """Browse a stored gene reference across source types."""
        sides = _sides(endpoint)
        clauses = [
            f"{side}_reference_entity_key = {_literal(reference_entity_key)}" for side in sides
        ]
        return self.relations(resources).filter(
            (" AND " if endpoint == "both" else " OR ").join(clauses)
        )

    def related_product(
        self,
        product_entity_key: str,
        *,
        resources: str | Sequence[str],
        isoform_identifier: str | None = None,
        endpoint: str = "any",
        product_type: str = "protein",
    ) -> duckdb.DuckDBPyRelation:
        """Relations with the evidence that names this exact product/isoform on one endpoint.

        Each row carries the matching ``relation_evidence`` rows as ``evidence`` (in
        source order) and their number as ``evidence_count``. Missing form context
        does not match. All constraints apply to the same evidence occurrence.
        """
        if product_type not in {"protein", "transcript"}:
            raise ValueError("product_type must be protein or transcript")
        clauses = []
        for side in _sides(endpoint):
            form = f"{side}_molecular_form"
            clause = f"{form}.{product_type}_entity_key = {_literal(product_entity_key)}"
            if isoform_identifier:
                clause += (
                    f" AND {form}.isoform_identifier.ns || ':' || {form}.isoform_identifier.id"
                    f" = {_literal(isoform_identifier)}"
                )
            clauses.append(f"({clause})")
        match = (" AND " if endpoint == "both" else " OR ").join(clauses)
        evidence = self._table_sql("relation_evidence", resources)
        return self._db().sql(
            f"""SELECT r.* EXCLUDE (evidence_count), m.evidence, len(m.evidence) AS evidence_count
            FROM ({self._table_sql("relation", resources)}) AS r JOIN (
                SELECT _resource, relation_id, list(struct_pack(source, dataset, row_id,
                    upstream_id, annotations, subject_molecular_form, object_molecular_form)
                    ORDER BY ordinal) AS evidence
                FROM ({evidence}) WHERE {match}
                GROUP BY _resource, relation_id
            ) AS m USING (_resource, relation_id)"""
        )

    def referenced_products(
        self, relations: duckdb.DuckDBPyRelation, *, resources: str | Sequence[str]
    ) -> duckdb.DuckDBPyRelation:
        """Return product entity rows named by the molecular forms of relations' evidence:
        the selected ``evidence`` (from related_product) or all of their evidence."""
        if "evidence" in relations.columns:
            occurrences = f"SELECT _resource, unnest(evidence) AS ev FROM ({relations.sql_query()})"
        else:
            occurrences = (
                f"SELECT _resource, struct_pack(subject_molecular_form, object_molecular_form) AS ev "
                f"FROM ({self._table_sql('relation_evidence', resources)}) "
                f"SEMI JOIN ({relations.sql_query()}) USING (_resource, relation_id)"
            )
        fields = ", ".join(
            f"ev.{side}_molecular_form.{kind}_entity_key"
            for side in ("subject", "object")
            for kind in ("protein", "transcript")
        )
        return self._db().sql(
            f"WITH occurrences AS ({occurrences}), "
            f"product_keys AS (SELECT _resource, unnest([{fields}]) AS entity_key FROM occurrences) "
            f"SELECT e.* FROM ({self._table_sql('entity', resources)}) e SEMI JOIN product_keys p "
            "ON p.entity_key=e.entity_key AND p._resource=e._resource"
        )

    def sql(
        self,
        query: str,
        *,
        resources: str | Sequence[str],
        parameters: Sequence[Any] | None = None,
        include_evidence: bool = False,
    ) -> duckdb.DuckDBPyRelation:
        """Run a SELECT/CTE query over selected resources.

        Each query has its own CTE per published table (entity, entity_identifier, ...,
        relation_evidence), so subsequent selections cannot change an earlier lazy
        result. Raw evidence payloads require an explicit opt-in. This executes
        caller-supplied local SQL and is not a sandbox.
        """
        tables = [t for t in TABLES if include_evidence or t != "evidence_payloads"]
        ctes = ", ".join(f"{table} AS ({self._table_sql(table, resources)})" for table in tables)
        query = query.rstrip().removesuffix(";")
        return self._db().sql(
            f"WITH {ctes} SELECT * FROM ({query}) AS result",
            params=list(parameters) if parameters is not None else None,
        )

    def download(
        self,
        destination: str | Path,
        *,
        resources: str | Sequence[str] | None = None,
        include_evidence: bool = False,
    ) -> Path:
        """Download complete files and atomically publish an offline snapshot.

        Destination must not exist. Partial downloads never become snapshots.
        Checksums in the snapshot detect subsequent local corruption; they are
        computed during transfer, not independent upstream authenticity proofs.
        """
        if self._snapshot is not None:
            raise ClientError("Already offline; copy the snapshot directory to move it")
        records = deepcopy(self._select(resources))
        destination = Path(destination).expanduser().absolute()
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".omnipath-", dir=destination.parent) as temporary:
            stage = Path(temporary)
            for record in records:
                record["files"] = self.files(
                    record["resource_id"], include_evidence=include_evidence
                )
                for artifact in record["files"]:
                    if not artifact.get("url"):
                        raise ClientError("Server does not provide a static download URL")
                    path = (
                        stage
                        / "resources"
                        / record["resource_id"]
                        / record["version"]
                        / artifact["name"]
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    digest, size = hashlib.sha256(), 0
                    with self._http.stream(
                        "GET", _url(artifact["url"]), headers={"Accept-Encoding": "identity"}
                    ) as response:
                        response.raise_for_status()
                        with path.open("wb") as stream:
                            for chunk in response.iter_bytes():
                                stream.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                    if size != artifact["size_bytes"] or size < 8:
                        raise ClientError(f"Truncated download: {artifact['name']}")
                    with path.open("rb") as stream:
                        header = stream.read(4)
                        stream.seek(-4, 2)
                        if header != b"PAR1" or stream.read(4) != b"PAR1":
                            raise ClientError(f"Invalid Parquet download: {artifact['name']}")
                    artifact["sha256"] = digest.hexdigest()
            manifest = {
                "snapshot_version": 1,
                "api_url": self.api_url,
                "release": self.release,
                "resources": records,
            }
            (stage / "snapshot.json").write_text(json.dumps(manifest, indent=2) + "\n")
            os.rename(stage, destination)
        return destination

    @classmethod
    def from_snapshot(cls, directory: str | Path, *, memory_limit: str = "2GB") -> Client:
        """Open a downloaded snapshot without API or httpfs access."""
        root = Path(directory).expanduser().resolve()
        try:
            data = validate_snapshot(json.loads((root / "snapshot.json").read_text()))
        except (ValueError, UnicodeError) as exc:
            raise ClientError("Invalid snapshot JSON") from exc
        client = cls(data["api_url"], release=data["release"], memory_limit=memory_limit)
        client._catalog = {record["resource_id"]: record for record in data["resources"]}
        client._snapshot = root
        return client

    def close(self) -> None:
        if not self._closed:
            if self._connection is not None:
                self._connection.close()
            if self._owns_http:
                self._http.close()
            self._closed = True

    def __enter__(self) -> Client:
        self._ensure_open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
