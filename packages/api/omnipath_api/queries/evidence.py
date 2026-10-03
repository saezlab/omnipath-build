"""Evidence query service, including domain SQL and result shaping."""

from __future__ import annotations

import logging
import json
from typing import Any


from omnipath_api.annotations import (
    split_compiled_annotations,
)


logger = logging.getLogger(__name__)


def payload_query(payload_read_expr: str) -> str:
    return f"SELECT source, row_id, payload_json FROM {payload_read_expr} WHERE relation_key = ?"


class EvidenceQueries:
    """Evidence queries over the engine storage and shaping contract."""

    def get_relation_evidence(
        self, relation_pk: str, resources: list[str] | None = None
    ) -> dict[str, Any]:
        paths = self._resolve_relation_paths(resources)
        if not paths:
            return {"evidence": [], "annotations": []}
        read_expr = self._read_expr(paths)
        rows = self._fetch_dicts(
            f"SELECT relation_key, evidence, annotations FROM {read_expr} WHERE relation_key = ? LIMIT 1",
            [relation_pk],
        )
        if not rows:
            return {"evidence": [], "annotations": []}
        row = rows[0]
        nested_evidence = row.get("evidence") or []
        annotations = row.get("annotations") or []
        if not isinstance(nested_evidence, list):
            nested_evidence = []
        if not isinstance(annotations, list):
            annotations = []

        compiled_rel = split_compiled_annotations(annotations)

        evidence_items = list(nested_evidence)
        if not evidence_items:
            evidence_items = [{"source": "unknown", "row_id": "0"}]

        evidence_rows: list[dict[str, Any]] = []
        for index, item in enumerate(evidence_items):
            record = item if isinstance(item, dict) else {"value": item}
            row_id = str(record.get("row_id") or index)
            source_name = str(record.get("source") or "unknown")

            item_anns = record.get("annotations")
            if item_anns and isinstance(item_anns, list) and len(item_anns) > 0:
                ev_compiled = split_compiled_annotations(item_anns)
            else:
                ev_compiled = compiled_rel

            evidence_rows.append(
                {
                    "relationPk": relation_pk,
                    "relationEvidencePk": f"{relation_pk}:{row_id}",
                    "source": source_name,
                    "dataset": record.get("dataset"),
                    "upstreamId": record.get("upstream_id"),
                    "subjectAttributes": ev_compiled["subject"],
                    "objectAttributes": ev_compiled["object"],
                    "recordAttributes": ev_compiled["relation"],
                    "evidence": ev_compiled["relation"],
                }
            )

        return {
            "evidence": evidence_rows,
            "annotations": compiled_rel["relation"],
        }

    def get_relation_payloads(
        self, relation_pk: str, resources: list[str] | None = None
    ) -> dict[str, Any]:
        """Fetch raw input JSON/string records from evidence_payloads.parquet for inspection."""
        payload_paths = self._resolve_payload_paths(resources)
        if not payload_paths:
            return {"relationPk": relation_pk, "payloads": []}
        payload_expr = self._read_expr(payload_paths)
        rows = self._fetch_dicts(
            payload_query(payload_expr),
            [relation_pk],
        )
        out = []
        for r in rows:
            raw = r.get("payload_json")
            parsed = raw
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "Non-JSON evidence payload for relation %s, row %s",
                        relation_pk,
                        r.get("row_id"),
                    )
                    parsed = {"raw": raw}
            out.append(
                {
                    "source": str(r.get("source") or "unknown"),
                    "rowId": str(r.get("row_id") or ""),
                    "payload": parsed,
                }
            )
        return {"relationPk": relation_pk, "payloads": out}
