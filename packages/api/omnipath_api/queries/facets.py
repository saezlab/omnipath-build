"""Facets query service, including domain SQL and result shaping."""

from __future__ import annotations

import logging
import json
import time
from typing import Any


from omnipath_api.store.query_cache import normalized
from omnipath_api.serving_index import projected_paths


from omnipath_api.queries.constants import RELATION_QUALIFIER_FILTERS
from omnipath_api.models import normalize_filters

logger = logging.getLogger(__name__)


class FacetsQueries:
    """Facets queries over the engine storage and shaping contract."""

    def get_facets(
        self,
        filters: dict[str, Any] | None = None,
        resources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compute aggregated facet counts in a single vectorized pass."""
        t0 = time.perf_counter()
        paths = self._resolve_relation_paths(resources)
        if not paths:
            return {"categories": {}, "predicates": {}, "signs": {}, "elapsed_ms": 0.0}

        read_expr = self._read_expr(paths)

        where_sql, params = self._resolve_relation_where(filters, resources)

        # Single-pass multi-grouping aggregation (scans Parquet only once)
        facet_sql = f"""
            SELECT category, predicate, sign, count(*) AS n
            FROM {read_expr}
            WHERE {where_sql}
            GROUP BY category, predicate, sign
        """
        rows = self._db.execute(facet_sql, params).fetchall()

        categories: dict[str, int] = {}
        predicates: dict[str, int] = {}
        signs: dict[str, int] = {}

        for cat, pred, sign, count in rows:
            if cat:
                categories[cat] = categories.get(cat, 0) + count
            if pred:
                predicates[pred] = predicates.get(pred, 0) + count
            if sign is not None:
                s_key = str(sign)
                signs[s_key] = signs.get(s_key, 0) + count

        # Sort predicates by count descending
        sorted_predicates = dict(sorted(predicates.items(), key=lambda item: -item[1])[:25])
        sorted_categories = dict(sorted(categories.items(), key=lambda item: -item[1]))

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

        return {
            "categories": sorted_categories,
            "predicates": sorted_predicates,
            "signs": signs,
            "elapsed_ms": elapsed_ms,
        }

    def _cached_facets(self, kind, payload, resources, compute):
        # Paths identify pinned releases; fingerprint also invalidates moving latest.
        infos = self._selected_resource_infos(None)
        key = (
            kind,
            self._inventory_fingerprint,
            self._taxonomy_cache_version(),
            tuple(sorted(str(i["entities_path"]) for i in infos)),
            json.dumps(
                normalized({"payload": payload or {}, "resources": resources}), sort_keys=True
            ),
            json.dumps(self._release_scope.get(), sort_keys=True, default=str),
            json.dumps(
                [m.get("references") for m in self.releases.list()], sort_keys=True, default=str
            ),
        )
        return self._facet_cache.get(key, lambda: compute(payload, resources))

    def get_scoped_entity_facets(self, payload=None, resources=None):
        return self._cached_facets("entities", payload, resources, self._compute_entity_facets)

    def get_scoped_relation_facets(self, payload=None, resources=None):
        payload = dict(payload or {})
        limit = payload.pop("taxonomyLimit", None)
        query = str(payload.pop("taxonomyQuery", "") or "").strip().casefold()
        items = self._cached_facets("relations", payload, resources, self._compute_relation_facets)
        if limit is None:
            return items  # Existing API callers can still request the complete list.
        limit = max(1, min(int(limit), 10000))
        selected = set(map(str, payload.get("taxonomyIds") or payload.get("ncbi_tax_id") or []))
        taxa = [i for i in items if i["facetName"] == "taxonomy_id"]
        present = {i["facetValue"] for i in taxa}
        missing = self._label_taxonomy_facets(
            [
                dict(facetName="taxonomy_id", facetValue=value, scopedCount=0, facetCategory=None)
                for value in sorted(selected - present)
            ]
        )
        items.extend(missing)
        taxa.extend(missing)
        matching = [
            i
            for i in taxa
            if not query
            or query in i["facetValue"].casefold()
            or query in str(i.get("facetLabel") or "").casefold()
        ]
        visible = {i["facetValue"] for i in matching[:limit]} | selected
        return [i for i in items if i["facetName"] != "taxonomy_id" or i["facetValue"] in visible]

    def _compute_relation_facets(
        self,
        payload: dict[str, Any] | None = None,
        resources: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = payload or {}
        filter_payload = dict(payload)
        nested = filter_payload.pop("filters", None) or {}
        for key, value in nested.items():
            outer = filter_payload.get(key)
            if isinstance(value, list) and isinstance(outer, list):
                filter_payload[key] = outer + value
            elif outer is None:
                filter_payload[key] = value
        filters = normalize_filters(filter_payload)
        paths = self._resolve_relation_paths(resources or filters.get("sources"))
        if not paths:
            return []
        read_expr = self._read_expr(projected_paths(self.data_root, "relations", paths))
        common_filters = {**filters, **{term: [] for term in RELATION_QUALIFIER_FILTERS}}
        common_where, common_params = self._resolve_relation_where(common_filters, resources)
        qualifier_filters = {term: filters[term] for term in RELATION_QUALIFIER_FILTERS}
        where_sql, params = self._build_relation_where(qualifier_filters)
        queries = [
            f"""SELECT 'base' kind, to_json(struct_pack(
            category:=category, predicate:=predicate, subject_type:=subject_type, object_type:=object_type,
            taxon:=taxon, sign:=sign, sources:=sources, n:=count(*))) payload
            FROM matched WHERE {where_sql}
            GROUP BY category, predicate, subject_type, object_type, taxon, sign, sources"""
        ]
        query_params = list(params)
        qualifier_groups = {}
        for term in RELATION_QUALIFIER_FILTERS:
            qualifier_where, qualifier_params = self._build_relation_where(
                {**qualifier_filters, term: []}
            )
            qualifier_groups.setdefault((qualifier_where, tuple(qualifier_params)), []).append(term)
        for (qualifier_where, qualifier_params), terms in qualifier_groups.items():
            placeholders = ", ".join("?" for _ in terms)
            queries.append(f"""SELECT 'qualifier' kind,
                to_json(struct_pack(term:=annotation.term, value:=annotation.value, n:=count(DISTINCT relation_key))) payload
                FROM (SELECT relation_key,
                    UNNEST(list_filter(annotations, a -> a.scope='relation' AND a.term IN ({placeholders}))) annotation
                    FROM matched WHERE {qualifier_where}) q
                GROUP BY annotation.term, annotation.value""")
            query_params.extend([*terms, *qualifier_params])
        materialization = "MATERIALIZED" if filters["entity_ids"] else "NOT MATERIALIZED"
        combined = self._db.execute(
            f"""WITH matched AS {materialization} (
            SELECT category, predicate, subject_type, object_type, taxon, sign, sources, relation_key,
                list_filter(annotations, a -> a.scope='relation' AND a.term IN
                    ('causal_mechanism_qualifier','object_aspect_qualifier','object_direction_qualifier')) annotations
            FROM {read_expr} WHERE {common_where}
        ) {" UNION ALL ".join(queries)}""",
            [*common_params, *query_params],
        ).fetchall()
        grouped = [
            tuple(
                json.loads(raw)[key]
                for key in (
                    "category",
                    "predicate",
                    "subject_type",
                    "object_type",
                    "taxon",
                    "sign",
                    "sources",
                    "n",
                )
            )
            for kind, raw in combined
            if kind == "base"
        ]

        counts: dict[tuple[str, str, str | None], int] = {}

        def add(name: str, value: Any, count: int, category: str | None = None) -> None:
            if value is None or str(value).strip() == "":
                return
            key = (name, str(value), category)
            counts[key] = counts.get(key, 0) + int(count)

        for row in grouped:
            category, predicate, subject_type, object_type, taxon, sign, sources, n = row
            add("predicate", predicate, n, category)
            add("participant_type", subject_type, n)
            add("participant_type", object_type, n)
            add("taxonomy_id", taxon, n)
            add("sign", sign, n)
            add("category", category, n)
            if isinstance(sources, list):
                for source in dict.fromkeys(sources):
                    add("source", source, n)
            elif sources:
                add("source", sources, n)

        for kind, raw in combined:
            if kind == "qualifier":
                item = json.loads(raw)
                add(item["term"], item["value"], item["n"])

        out = [
            {
                "facetName": name,
                "facetValue": value,
                "facetCategory": category,
                "scopedCount": count,
            }
            for (name, value, category), count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1])
            )
        ]
        return self._label_taxonomy_facets(out)

    def _entity_scoped_facet_where(
        self,
        payload: dict[str, Any],
        *,
        apply_sources: bool = True,
    ) -> tuple[str, list[Any], list[str]]:
        payload = payload or {}
        query = str(payload.get("query") or "").strip()
        filters = normalize_filters(payload)
        scope = (
            filters["sources"] if apply_sources and filters["sources"] else payload.get("resources")
        )
        paths = self._resolve_entity_paths(scope)
        if not apply_sources:
            filters["sources"] = []
        where_clauses, params = self._entity_filter_clauses(filters, resources=scope)
        where_clauses.insert(0, "1=1")
        if query:
            where_clauses.append("(label ILIKE ? OR identifier ILIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        return " AND ".join(where_clauses), params, paths

    def _compute_entity_facets(
        self,
        payload: dict[str, Any] | None = None,
        resources: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = dict(payload or {})
        if resources:
            payload["resources"] = resources
        where_sql, params, paths = self._entity_scoped_facet_where(payload, apply_sources=True)
        if not paths:
            return []
        read_expr = self._read_expr(projected_paths(self.data_root, "entities", paths))
        groups = self._db.execute(
            f"""SELECT CASE WHEN GROUPING(entity_type)=0 THEN 'entity_type' ELSE 'taxonomy_id' END,
                       coalesce(entity_type, taxon), count(*)
                FROM {read_expr} WHERE {where_sql}
                GROUP BY GROUPING SETS ((entity_type), (taxon))""",
            params,
        ).fetchall()
        source_where, source_params, _ = self._entity_scoped_facet_where(
            payload, apply_sources=False
        )
        infos = self._selected_resource_infos(None)
        source_paths = [str(info["entities_path"]) for info in infos]
        source_projection = projected_paths(self.data_root, "entities", source_paths)
        source_counts = (
            dict(
                self._db.execute(
                    f"SELECT filename, count(*) FROM read_parquet(?, filename=true, union_by_name=true) WHERE {source_where} GROUP BY filename",
                    [source_projection, *source_params],
                ).fetchall()
            )
            if source_paths
            else {}
        )
        source_items = [
            dict(
                facetName="source",
                facetValue=info["resource"],
                scopedCount=source_counts.get(path, 0),
            )
            for info, path in zip(infos, source_projection)
        ]
        limit = int(payload.get("facetLimit") or 50)
        items = [
            dict(facetName=name, facetValue=str(value), scopedCount=count)
            for name, value, count in groups
            if value
        ]
        items.extend(source_items)
        items.sort(key=lambda item: (-item["scopedCount"], item["facetName"], item["facetValue"]))
        type_tax_limit = max(limit * 2, 20)
        typed = [item for item in items if item["facetName"] != "source"][:type_tax_limit]
        sources = sorted(source_items, key=lambda item: (-item["scopedCount"], item["facetValue"]))
        return self._label_taxonomy_facets(typed + sources)

    def get_entity_filter_options(self, resources: list[str] | None = None) -> dict[str, Any]:
        paths = self._resolve_entity_paths(resources)
        if not paths:
            return {"entity_types": [], "sources": [], "taxonomy_ids": []}
        read_expr = self._read_expr(paths)
        types = [
            r[0]
            for r in self._db.execute(
                f"SELECT DISTINCT entity_type FROM {read_expr} WHERE entity_type IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        taxons = [
            str(r[0])
            for r in self._db.execute(
                f"SELECT DISTINCT taxon FROM {read_expr} WHERE taxon IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        sources = sorted({info["resource"] for info in self._selected_resource_infos(resources)})
        return {"entity_types": types, "sources": sources, "taxonomy_ids": taxons}

    def get_relation_filter_options(self, resources: list[str] | None = None) -> dict[str, Any]:
        facets = self.get_scoped_relation_facets({}, resources=resources)
        predicates_by_category: dict[str, list[str]] = {}
        sources: list[str] = []
        interaction_types: list[str] = []
        for item in facets:
            if item["facetName"] == "predicate":
                category = item.get("facetCategory") or "uncategorized"
                predicates_by_category.setdefault(category, []).append(item["facetValue"])
            elif item["facetName"] == "source":
                sources.append(item["facetValue"])
            elif item["facetName"] == "participant_type":
                interaction_types.append(item["facetValue"])
        return {
            "predicatesByCategory": {k: sorted(set(v)) for k, v in predicates_by_category.items()},
            "sources": sorted(set(sources)),
            "interactionTypes": sorted(set(interaction_types)),
        }
