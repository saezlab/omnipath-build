"""Export query service, including domain SQL and result shaping."""

from __future__ import annotations

import io
import logging
import json
from pathlib import Path
from typing import Any


import pyarrow as pa
from omnipath_api.store.connection import sql_literal


logger = logging.getLogger(__name__)


def copy_parquet_command(select_sql: str, target_path: Path) -> str:
    return (
        f"COPY ({select_sql}) TO {sql_literal(str(target_path))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def copy_csv_command(select_sql: str, target_path: Path, delimiter: str = ",") -> str:
    return f"COPY ({select_sql}) TO {sql_literal(str(target_path))} (HEADER, DELIMITER {sql_literal(delimiter)})"


class ExportQueries:
    """Export queries over the engine storage and shaping contract."""

    def export_slice(
        self,
        filters: dict[str, Any] | None = None,
        resources: list[str] | None = None,
        format: str = "parquet",
    ) -> tuple[bytes, str]:
        """Export custom filtered network as Parquet, Arrow IPC, CSV, or JSON."""
        import tempfile

        paths = self._resolve_relation_paths(resources)
        read_expr = self._read_expr(paths)

        where_sql, params = self._resolve_relation_where(filters, resources)
        from omnipath_api.molecular import columns, form_match_sql, has_form_filters
        from omnipath_api.models import normalize_filters

        filters = normalize_filters(filters)
        select = "*"
        projection_params = []
        if has_form_filters(filters):
            match, projection_params = form_match_sql(filters, occurrence="to_json(ev)")
            select = f"* EXCLUDE(evidence, evidence_count), list_filter(evidence, ev -> {match}) AS evidence"
        selected = f"SELECT {select} FROM {read_expr} WHERE {where_sql}"
        params = [*projection_params, *params]
        entity_paths = self._resolve_entity_paths(resources)
        if entity_paths and "evidence" in columns(paths):
            entity_read = self._read_expr(entity_paths)
            keys = " UNION ALL ".join(
                f"SELECT json_extract_string(to_json(ev), '$.{side}_molecular_form.{field}') AS entity_key FROM selected, UNNEST(evidence) AS occurrences(ev)"
                for side in ("subject", "object")
                for field in ("protein_entity_key", "transcript_entity_key")
            )
            count = ", len(evidence) AS evidence_count" if has_form_filters(filters) else ""
            sql = f"WITH selected AS ({selected}), product_keys AS ({keys}) SELECT selected.*{count}, (SELECT list(e) FROM {entity_read} e WHERE e.entity_key IN (SELECT entity_key FROM product_keys)) AS referenced_product_records FROM selected"
        else:
            sql = selected

        if format == "parquet":
            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                self._db.execute(copy_parquet_command(sql, tmp_path), params)
                data = tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)
            return data, "application/vnd.apache.parquet"
        elif format == "csv":
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                self._db.execute(copy_csv_command(sql, tmp_path), params)
                data = tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)
            return data, "text/csv"
        else:
            arrow_table = self._db.execute(sql, params).fetch_arrow_table()
            if format == "arrow":
                buf = io.BytesIO()
                with pa.ipc.new_stream(buf, arrow_table.schema) as writer:
                    writer.write_table(arrow_table)
                return buf.getvalue(), "application/vnd.apache.arrow.stream"
            else:
                data = arrow_table.to_pylist()
                return json.dumps(data, default=str).encode("utf-8"), "application/json"
