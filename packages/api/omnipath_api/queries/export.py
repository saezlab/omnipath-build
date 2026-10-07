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

        from omnipath_api.molecular import form_match_sql, has_form_filters
        from omnipath_api.models import normalize_filters
        from omnipath_core.measurements import QUANTITY_STRUCT

        filters = normalize_filters(filters)
        selection, params = self._relation_selection(filters, resources)
        names = [f"quantity_{field.name}" for field in QUANTITY_STRUCT]
        quantity = (
            f"CASE WHEN {' AND '.join(f'{n} IS NULL' for n in names)} THEN NULL ELSE struct_pack("
            + ", ".join(f"{field.name} := quantity_{field.name}" for field in QUANTITY_STRUCT)
            + ") END"
        )
        form, form_params = "TRUE", []
        if has_form_filters(filters):
            form, form_params = form_match_sql(filters)
        # Rows as the published tables nest them: each relation with its annotations and
        # evidence (only the matching occurrences under a molecular form filter), and
        # the product entities its evidence names.
        sql = f"""WITH selected AS MATERIALIZED ({selection}),
        annotations AS (
            SELECT resource, relation_id, list(struct_pack(term := term, value := value,
                quantity := {quantity}, source := source, dataset := dataset, scope := scope)
                ORDER BY ordinal) AS annotations
            FROM {self._table("relation_annotation", resources)}
            WHERE (resource, relation_id) IN (SELECT resource, relation_id FROM selected)
            GROUP BY ALL
        ), evidence AS (
            SELECT resource, relation_id, list(struct_pack(source := source, dataset := dataset,
                row_id := row_id, upstream_id := upstream_id, annotations := annotations,
                subject_molecular_form := subject_molecular_form,
                object_molecular_form := object_molecular_form) ORDER BY ordinal) AS evidence
            FROM {self._table("relation_evidence", resources)}
            WHERE (resource, relation_id) IN (SELECT resource, relation_id FROM selected) AND {form}
            GROUP BY ALL
        ), rows AS (
            SELECT s.* EXCLUDE (resource, relation_id, evidence_count),
                coalesce(a.annotations, []) AS annotations, coalesce(e.evidence, []) AS evidence,
                {"len(coalesce(e.evidence, []))" if form_params else "s.evidence_count"} AS evidence_count
            FROM selected s LEFT JOIN annotations a USING (resource, relation_id)
            LEFT JOIN evidence e USING (resource, relation_id)
        ), product_keys AS (
            SELECT DISTINCT unnest([f.subject_molecular_form.protein_entity_key,
                f.subject_molecular_form.transcript_entity_key,
                f.object_molecular_form.protein_entity_key,
                f.object_molecular_form.transcript_entity_key]) AS entity_key
            FROM rows, unnest(rows.evidence) AS occurrences(f)
        ) SELECT rows.*, (SELECT list(e ORDER BY e.entity_key, e.resource) FROM {self._table("entity", resources)} e
            WHERE e.entity_key IN (SELECT entity_key FROM product_keys)) AS referenced_product_records
        FROM rows"""
        params = [*params, *form_params]

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
