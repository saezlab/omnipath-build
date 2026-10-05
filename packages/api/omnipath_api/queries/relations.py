"""Relations query service, including domain SQL and result shaping."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any


from omnipath_core.biolink import (
    predicate as biolink_predicate,
    entity_type as biolink_entity_type,
    annotation_value,
)

from omnipath_api.serving_index import projected_paths
from omnipath_api.molecular import (
    columns,
    read,
    occurrences_expression,
    form_match_sql,
    has_form_filters,
    matching_evidence,
)
from omnipath_api.models import normalize_filters


from omnipath_api.queries.constants import RELATION_QUALIFIER_FILTERS

logger = logging.getLogger(__name__)


class RelationsQueries:
    """Relations queries over the engine storage and shaping contract."""

    def _build_relation_where(
        self,
        filters: dict[str, Any] | None,
        resolved_keys: list[str] | None = None,
        resolved_scope_keys: list[str] | None = None,
    ) -> tuple[str, list[Any]]:
        filters = normalize_filters(filters)
        where_clauses = ["1=1"]
        params: list[Any] = []
        entity_tokens = filters["entity_ids"] + filters["entity_pks"]
        scope_tokens = filters["scope_entity_ids"]

        def endpoint_clause(keys: list[str], mode: str) -> None:
            if not keys:
                where_clauses.append("FALSE")
                return
            subject = "subject_entity_key IN (SELECT unnest(?::VARCHAR[]))"
            obj = "object_entity_key IN (SELECT unnest(?::VARCHAR[]))"
            if mode in {"source", "source_only"}:
                where_clauses.append(subject)
                params.append(keys)
            elif mode in {"target", "target_only"}:
                where_clauses.append(obj)
                params.append(keys)
            else:
                where_clauses.append(f"({subject} {'AND' if mode == 'both' else 'OR'} {obj})")
                params.extend([keys, keys])

        if entity_tokens:
            if resolved_keys is None:
                raise ValueError("Entity filters must be resolved before SQL construction")
            endpoint_clause(resolved_keys, filters["gene_mode"])
        if scope_tokens:
            if resolved_scope_keys is None:
                raise ValueError("Scope filters must be resolved before SQL construction")
            endpoint_clause(resolved_scope_keys, filters["scope_endpoint_mode"])

        categories = self._as_list(filters.get("categories") or filters.get("relation_categories"))
        if categories:
            placeholders = ", ".join("?" for _ in categories)
            where_clauses.append(f"category IN ({placeholders})")
            params.extend(categories)

        predicates = self._as_list(filters.get("predicates"))
        if predicates:
            norm_predicates = [biolink_predicate(p) for p in predicates]
            placeholders = ", ".join("?" for _ in norm_predicates)
            where_clauses.append(f"predicate IN ({placeholders})")
            params.extend(norm_predicates)

        for term in RELATION_QUALIFIER_FILTERS:
            values = self._as_list(filters.get(term))
            if values:
                values = [annotation_value(term, value) for value in values]
                placeholders = ", ".join("?" for _ in values)
                where_clauses.append(
                    "len(list_filter(annotations, a -> "
                    f"a.term = ? AND a.scope = 'relation' "
                    f"AND a.value IN ({placeholders}))) > 0"
                )
                params.extend([term, *values])

        if "is_directed" in filters and filters["is_directed"] is not None:
            where_clauses.append("is_directed = ?")
            params.append(bool(filters["is_directed"]))

        signs = self._as_list(filters.get("signs"))
        if "sign" in filters and filters["sign"] is not None and not signs:
            signs = [filters["sign"]]
        if signs:
            placeholders = ", ".join("?" for _ in signs)
            where_clauses.append(f"sign IN ({placeholders})")
            params.extend(int(s) for s in signs)

        min_ev = filters.get("min_evidence_count")
        if min_ev is not None and int(min_ev) > 1:
            where_clauses.append("evidence_count >= ?")
            params.append(int(min_ev))

        interaction_types = self._as_list(
            filters.get("interaction_types")
            or filters.get("interactionTypes")
            or filters.get("entity_types")
        )
        if interaction_types:
            norm_types = [biolink_entity_type(t) for t in interaction_types]
            placeholders = ", ".join("?" for _ in norm_types)
            where_clauses.append(
                f"(subject_type IN ({placeholders}) OR object_type IN ({placeholders}))"
            )
            params.extend(norm_types)
            params.extend(norm_types)

        taxons = self._as_list(
            filters.get("ncbi_tax_id") or filters.get("taxonomy_ids") or filters.get("taxonomyIds")
        )
        if taxons:
            placeholders = ", ".join("?" for _ in taxons)
            where_clauses.append(f"taxon IN ({placeholders})")
            params.extend(str(t) for t in taxons)

        sources = self._as_list(filters.get("sources"))
        if sources:
            source_clauses = ["list_contains(COALESCE(sources, []::VARCHAR[]), ?)" for _ in sources]
            where_clauses.append("(" + " OR ".join(source_clauses) + ")")
            params.extend(str(s) for s in sources)

        return " AND ".join(where_clauses), params

    def _resolve_relation_where(self, filters, resources=None):
        filters = normalize_filters(filters)
        tokens = filters["entity_ids"] + filters["entity_pks"]
        scope = filters["scope_entity_ids"]
        where, params = self._build_relation_where(
            filters,
            self.resolve_entity_keys(tokens, resources) if tokens else None,
            self.resolve_entity_keys(scope, resources) if scope else None,
        )
        if filters["reference_entity_keys"]:
            where += " AND (subject_reference_entity_key IN (SELECT unnest(?::VARCHAR[])) OR object_reference_entity_key IN (SELECT unnest(?::VARCHAR[])))"
            params.extend([filters["reference_entity_keys"], filters["reference_entity_keys"]])
        if has_form_filters(filters):
            expr = occurrences_expression(columns(self._resolve_relation_paths(resources)))
            match, values = form_match_sql(filters)
            where += f" AND len(list_filter({expr}, ev -> {match})) > 0"
            params.extend(values)
        return where, params

    def search_relations(
        self,
        filters: dict[str, Any] | None = None,
        resources: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Search and extract filtered interaction networks without unneeded evidence payload overhead."""
        t0 = time.perf_counter()
        filters = filters or {}
        paths = self._resolve_relation_paths(resources)
        if not paths:
            return {"rows": [], "total": 0, "elapsed_ms": 0.0, "files_scanned": 0}

        scalar_paths = projected_paths(self.data_root, "relations", paths)
        original_by_projection = dict(zip(scalar_paths, paths))
        read_expr = self._read_expr(scalar_paths)

        filters = normalize_filters(filters)
        entity_tokens = filters["entity_ids"] + filters["entity_pks"]
        scope_tokens = filters["scope_entity_ids"]
        where_sql, params = self._resolve_relation_where(filters, resources)

        # Sort/count only lightweight rows, then read payload columns for the page's files.
        page_read = read(
            scalar_paths,
            filename=True,
            file_row_number=True,
        )
        if has_form_filters(filters) and "molecular_occurrences" in columns(
            projected_paths(self.data_root, "relations", paths)
        ):
            where_sql = where_sql.replace(
                occurrences_expression(columns(paths)),
                occurrences_expression(
                    columns(projected_paths(self.data_root, "relations", paths))
                ),
            )
        page_sql = f"""
            WITH matched AS MATERIALIZED (
                SELECT filename, file_row_number, category, predicate, subject_label, object_label
                FROM {page_read} WHERE {where_sql}
            ), page AS (
                SELECT filename, file_row_number, category, predicate, subject_label, object_label FROM matched
                ORDER BY category, predicate, subject_label, object_label
                LIMIT {int(limit)} OFFSET {int(offset)}
            ) SELECT page.*, totals.matching_total FROM page
            CROSS JOIN (SELECT count(*) AS matching_total FROM matched) totals
            ORDER BY category, predicate, subject_label, object_label
        """
        if not (scope_tokens or entity_tokens):
            # Avoid materializing the entire collection for an unscoped browse.
            page_sql = f"""SELECT filename, file_row_number FROM {page_read}
                WHERE {where_sql} ORDER BY category, predicate, subject_label, object_label
                LIMIT {int(limit)} OFFSET {int(offset)}"""
        page = self._fetch_dicts(page_sql, params)
        rows = []
        if page:
            if "matching_total" in page[0]:
                total = int(page[0]["matching_total"])
            elif offset == 0 and len(page) < limit:
                total = len(page)
            else:
                total = self._db.execute(
                    f"SELECT count(*) FROM {read_expr} WHERE {where_sql}", params
                ).fetchone()[0]
            by_file = defaultdict(list)
            for item in page:
                by_file[item["filename"]].append(item["file_row_number"])
            found = {}
            extra_cols = (
                ", evidence, annotations"
                if include_details
                else ", list_filter(annotations, a -> a.term IN ('causal_mechanism_qualifier', 'object_aspect_qualifier', 'object_direction_qualifier')) AS annotations"
            )
            for filename, row_numbers in by_file.items():
                detail_path = original_by_projection[filename] if include_details else filename
                detail_read = read([detail_path], file_row_number=True)
                items = self._fetch_dicts(
                    f"""SELECT file_row_number, relation_key,
                    subject_entity_key, subject_label, subject_type, predicate,
                    object_entity_key, object_label, object_type, subject_reference_entity_key, object_reference_entity_key, taxon, is_directed, sign,
                    category, interaction_class, sources, evidence_count {extra_cols}
                    FROM {detail_read} WHERE file_row_number IN (SELECT unnest(?::BIGINT[]))""",
                    [row_numbers],
                )
                for item in items:
                    found[(filename, item.pop("file_row_number"))] = item
            rows = [found[(item["filename"], item["file_row_number"])] for item in page]
        elif offset == 0:
            total = 0
        else:
            total = self._db.execute(
                f"SELECT count(*) FROM {read_expr} WHERE {where_sql}", params
            ).fetchone()[0]

        if include_details and has_form_filters(filters):
            for row in rows:
                row["evidence"] = matching_evidence(row.get("evidence"), filters)
                row["evidence_count"] = len(row["evidence"])
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

        return {
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "elapsed_ms": elapsed_ms,
            "files_scanned": len(paths),
        }

    def search_relations_api(self, filters=None, resources=None, limit=20, offset=0):
        started = time.perf_counter()
        payload = dict(filters=filters or {}, limit=limit, offset=offset)
        result = self._cached_facets(
            "relation-page",
            payload,
            resources,
            lambda p, r: self._search_relations_api_uncached(resources=r, **p),
        )
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result

    def _search_relations_api_uncached(
        self,
        filters: dict[str, Any] | None = None,
        resources: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = self.search_relations(
            filters=filters,
            resources=resources,
            limit=limit,
            offset=offset,
            include_details=has_form_filters(normalize_filters(filters)),
        )
        relations = [self._to_entity_relation(row) for row in result["rows"]]
        by_pk = self._endpoint_entities_by_pk(result["rows"], resources)
        rows = [
            {
                "relation": relation,
                "subjectEntity": self._hydrated_endpoint(row, "subject", by_pk),
                "objectEntity": self._hydrated_endpoint(row, "object", by_pk),
            }
            for row, relation in zip(result["rows"], relations)
        ]
        next_offset = offset + len(relations)
        next_cursor = str(next_offset) if next_offset < int(result["total"]) else None
        return {
            "relations": relations,
            "rows": rows,
            "total": result["total"],
            "nextCursor": next_cursor,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def get_relation_by_pk(
        self, relation_pk: str, resources: list[str] | None = None
    ) -> dict[str, Any] | None:
        paths = self._resolve_relation_paths(resources)
        if not paths:
            return None
        read_expr = self._read_expr(paths)
        rows = self._fetch_dicts(
            f"""
            SELECT *
            FROM {read_expr}
            WHERE relation_key = ?
            LIMIT 1
            """,
            [relation_pk],
        )
        if not rows:
            return None
        row = rows[0]
        by_pk = self._endpoint_entities_by_pk([row], resources)
        relation = self._to_entity_relation(row)
        return {
            "relation": relation,
            "subjectEntity": self._hydrated_endpoint(row, "subject", by_pk),
            "objectEntity": self._hydrated_endpoint(row, "object", by_pk),
        }
