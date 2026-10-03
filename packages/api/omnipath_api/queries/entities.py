"""Entities query service, including domain SQL and result shaping."""

from __future__ import annotations

import hashlib
import logging
import json
import re
import time
from collections import defaultdict
from typing import Any


from omnipath_core.biolink import entity_type as biolink_entity_type

from omnipath_api.serving_index import projected_paths
from omnipath_api.models import normalize_filters, EntitySearchCursor
from omnipath_api.shape import shape_entity_summary, shape_relation_summary
from omnipath_api.annotations import (
    normalize_identifier_type,
)


logger = logging.getLogger(__name__)


class EntitiesQueries:
    """Entities queries over the engine storage and shaping contract."""

    def resolve_entity_keys(
        self,
        tokens: list[str] | None,
        resources: list[str] | None = None,
    ) -> list[str]:
        """Map labels, identifiers, public ids, or keys to entity_key values."""
        values = [str(t).strip() for t in (tokens or []) if str(t).strip()]
        if not values:
            return []
        keys, other = self._split_entity_tokens(values)
        found = list(keys)
        if not other:
            return list(dict.fromkeys(found))
        paths = self._resolve_entity_paths(resources)
        if not paths:
            return list(dict.fromkeys(found))
        read_expr = self._read_expr(paths)
        placeholders = ", ".join("?" for _ in other)
        upper_values = [v.upper() for v in other]
        sql = f"""
            SELECT DISTINCT entity_key
            FROM {read_expr}
            WHERE entity_key IN ({placeholders})
               OR identifier IN ({placeholders})
               OR UPPER(identifier) IN ({placeholders})
               OR UPPER(label) IN ({placeholders})
               OR UPPER(namespace || '|' || identifier) IN ({placeholders})
               OR COALESCE(len(list_filter(identifiers, x -> upper(x.id) IN ({placeholders}))), 0) > 0
        """
        params = other + other + upper_values + upper_values + upper_values + upper_values
        rows = self._db.execute(sql, params).fetchall()
        found.extend(str(row[0]) for row in rows if row and row[0])
        return list(dict.fromkeys(found))

    def search_entities(
        self,
        query: str = "",
        resources: list[str] | None = None,
        entity_types: list[str] | None = None,
        taxon: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Fast autocomplete and entity search over entities.parquet."""
        t0 = time.perf_counter()
        paths = self._resolve_entity_paths(resources)
        if not paths:
            return {"rows": [], "elapsed_ms": 0.0, "total_scanned_files": 0}

        read_expr = self._read_expr(paths)

        where_clauses = ["1=1"]
        params: list[Any] = []

        if query:
            clean_q = f"{query.strip()}%"
            where_clauses.append("(label ILIKE ? OR identifier ILIKE ?)")
            params.extend([clean_q, clean_q])

        if entity_types:
            placeholders = ", ".join("?" for _ in entity_types)
            where_clauses.append(f"entity_type IN ({placeholders})")
            params.extend(entity_types)

        if taxon:
            where_clauses.append("taxon = ?")
            params.append(str(taxon))

        where_sql = " AND ".join(where_clauses)
        prefix = f"{query}%" if query else "%"
        merged_rows = self._fetch_merged_entity_rows(
            read_expr,
            where_sql,
            params,
            rank_sql="MIN(CASE WHEN upper(label) = upper(?) THEN 1 WHEN label ILIKE ? THEN 2 ELSE 3 END)",
            rank_params=[query, prefix],
            limit=int(limit),
            slim=True,
        )
        rows = [
            {
                "entity_key": row.get("entity_key"),
                "label": row.get("label"),
                "entity_type": row.get("entity_type"),
                "namespace": row.get("namespace"),
                "identifier": row.get("identifier"),
                "taxon": row.get("taxon"),
                "num_annotations": self._list_len(row.get("annotations")),
            }
            for row in merged_rows
        ]
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

        return {
            "rows": rows,
            "count": len(rows),
            "elapsed_ms": elapsed_ms,
            "files_scanned": len(paths),
        }

    @staticmethod
    def _list_len(value: Any) -> int:
        return len(value) if isinstance(value, list) else 0

    def _merge_duplicate_entity_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = (
                normalize_identifier_type(
                    str(row.get("namespace") or ""), str(row.get("identifier") or "")
                ),
                str(row.get("identifier") or ""),
                str(row.get("taxon") or ""),
            )
            groups[key].append(row)
        return [self._merge_entity_row_group(members) for members in groups.values()]

    def _merge_entity_row_group(self, members: list[dict[str, Any]]) -> dict[str, Any]:
        if len(members) == 1:
            return members[0]

        def rank(row: dict[str, Any]) -> tuple[Any, ...]:
            label = str(row.get("label") or "")
            ident = str(row.get("identifier") or "")
            gene_like = int(bool(re.fullmatch(r"[A-Z][A-Z0-9-]{1,14}", label)))
            return (
                self._list_len(row.get("annotations")),
                self._list_len(row.get("identifiers")),
                gene_like,
                int(bool(label) and label != ident and not label.isdigit()),
                # Equal-ranked resource copies must not depend on Parquet scan order.
                label.casefold(),
                label,
                str(row.get("entity_key") or ""),
                bool(row.get("has_hierarchy")),
                int(row.get("parent_count") or 0),
                int(row.get("child_count") or 0),
            )

        merged = dict(max(members, key=rank))
        seen_ids: set[tuple[Any, Any]] = set()
        identifiers: list[Any] = []
        seen_anns: set[tuple[Any, ...]] = set()
        annotations: list[Any] = []
        for row in members:
            for item in row.get("identifiers") or []:
                if not isinstance(item, dict):
                    continue
                pair = (
                    item.get("ns") or item.get("identifierType"),
                    item.get("id") or item.get("identifier"),
                )
                if pair in seen_ids:
                    continue
                seen_ids.add(pair)
                identifiers.append(item)
            for item in row.get("annotations") or []:
                if not isinstance(item, dict):
                    continue
                key = (
                    item.get("term"),
                    item.get("value"),
                    json.dumps(item.get("quantity"), sort_keys=True),
                    item.get("source"),
                    item.get("dataset"),
                    item.get("scope"),
                )
                if key in seen_anns:
                    continue
                seen_anns.add(key)
                annotations.append(item)
        merged["identifiers"] = identifiers
        merged["annotations"] = annotations
        merged["_source_entity_keys"] = [
            str(row.get("entity_key") or "") for row in members if row.get("entity_key")
        ]
        merged["_source_entity_keys"] = list(dict.fromkeys(merged["_source_entity_keys"]))
        return merged

    def _fetch_merged_entity_rows(
        self,
        read_expr: str,
        where_sql: str,
        params: list[Any],
        *,
        rank_sql: str = "MIN(0)",
        rank_params: list[Any] | None = None,
        limit: int | None = None,
        slim: bool = False,
        after: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Load every resource copy of each matching identity, then merge them.

        Search hits are grouped by (namespace, identifier, taxon). Copies that did
        not themselves match the WHERE clause are still included so UniProt
        annotations and SIGNOR identifiers land on the same entity.
        """
        limit_sql = f"LIMIT {int(limit)}" if limit is not None else ""
        nested_cols = "" if slim else ", e.identifiers, e.annotations"
        identities = f"""SELECT namespace, identifier, coalesce(taxon, '') AS taxon,
                                {rank_sql} AS match_rank, coalesce(MIN(label), '') AS sort_label
            FROM {read_expr} WHERE {where_sql}
            GROUP BY namespace, identifier, coalesce(taxon, '')
            """
        query_params = list(rank_params or []) + list(params)
        after_sql = ""
        if after:
            after_sql = (
                "WHERE (match_rank, sort_label, namespace, identifier, taxon) > (?, ?, ?, ?, ?)"
            )
            query_params.extend(
                after[key] for key in ("matchRank", "sortLabel", "namespace", "identifier", "taxon")
            )
        identities = f"SELECT * FROM ({identities}) identities {after_sql} ORDER BY match_rank, sort_label, namespace, identifier, taxon {limit_sql}"
        # SELECT placeholders precede WHERE placeholders in this query.
        sql = f"""WITH identities AS ({identities})
            SELECT e.entity_key, e.entity_type, e.namespace, e.identifier, e.taxon, e.label,
                   e.has_hierarchy, e.parent_count, e.child_count{nested_cols}, i.match_rank AS _match_rank, i.sort_label AS _sort_label
            FROM {read_expr} e
            INNER JOIN identities i
              ON e.namespace = i.namespace
             AND e.identifier = i.identifier
             AND COALESCE(e.taxon, '') = i.taxon
            ORDER BY i.match_rank, i.sort_label, i.namespace, i.identifier, i.taxon, e.entity_key
        """
        return self._merge_duplicate_entity_rows(self._fetch_dicts(sql, query_params))

    def _taxonomy_names(self) -> dict[str, str]:
        scoped = self._taxonomy_scope.get()
        return scoped if scoped is not None else self._resolve_taxonomy_names()

    def _taxonomy_cache_version(self):
        from omnipath_core.taxonomy_lookup import lookup_path

        return lookup_path(self.data_root) if self._release_scope.get() is None else None

    def _resolve_taxonomy_names(self) -> dict[str, str]:
        from omnipath_core.taxonomy import read_reference

        manifest = self._release_scope.get()
        if manifest is None:
            from omnipath_core.taxonomy_lookup import lookup_path, read_lookup

            if path := lookup_path(self.data_root):
                return read_lookup(path)
            # "latest" is a moving view; named releases never inherit newer labels.
            manifest = next(
                (m for m in self.releases.list() if m.get("references", {}).get("taxonomy")), {}
            )
        reference = manifest.get("references", {}).get("taxonomy")
        if not reference:
            return {}
        path = self.data_root / "references/taxonomy" / reference["version"] / "taxonomy.parquet"
        return read_reference(str(path))

    def _label_taxonomy_facets(self, items: list[dict]) -> list[dict]:
        names = self._taxonomy_names()
        return [
            {**item, "facetLabel": names.get(item["facetValue"])}
            if item["facetName"] == "taxonomy_id"
            else dict(item)
            for item in items
        ]

    def _to_entity_summary(
        self, row: dict[str, Any], resource_hint: str | None = None
    ) -> dict[str, Any]:
        result = shape_entity_summary(row, resource_hint=resource_hint)
        result["taxonomyName"] = self._taxonomy_names().get(result.get("taxonomyId"))
        return result

    def _to_entity_relation(self, row: dict[str, Any]) -> dict[str, Any]:
        return shape_relation_summary(row)

    def _endpoint_entities_by_pk(
        self,
        rows: list[dict[str, Any]],
        resources: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        keys: list[str] = []
        for row in rows:
            for side in ("subject_entity_key", "object_entity_key"):
                key = str(row.get(side) or "").strip()
                if key:
                    keys.append(key)
        if not keys:
            return {}
        found = self.get_entities_by_pks(keys, resources, slim=True).get("entities") or []
        by_pk: dict[str, dict[str, Any]] = {}
        for entity in found:
            for pk in entity.get("sourceEntityPks") or [entity.get("entityPk")]:
                key = str(pk or "").strip()
                if key:
                    by_pk[key] = entity
        return by_pk

    def _entity_from_relation_endpoint(self, row: dict[str, Any], side: str) -> dict[str, Any]:
        prefix = "subject" if side == "subject" else "object"
        return self._to_entity_summary(
            {
                "entity_key": row.get(f"{prefix}_entity_key"),
                "namespace": row.get(f"{prefix}_type") or "unknown",
                "identifier": row.get(f"{prefix}_label") or row.get(f"{prefix}_entity_key"),
                "label": row.get(f"{prefix}_label"),
                "entity_type": row.get(f"{prefix}_type"),
                "taxon": row.get("taxon"),
                "identifiers": [],
                "annotations": [],
            }
        )

    def _hydrated_endpoint(
        self, row: dict[str, Any], side: str, by_pk: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        prefix = "subject" if side == "subject" else "object"
        key = str(row.get(f"{prefix}_entity_key") or "")
        if key and key in by_pk:
            return by_pk[key]
        return self._entity_from_relation_endpoint(row, side)

    def _entity_filter_clauses(
        self,
        filters: dict[str, Any] | None = None,
        *,
        resources: list[str] | None = None,
    ) -> tuple[list[str], list[Any]]:
        filters = normalize_filters(filters)
        clauses: list[str] = []
        params: list[Any] = []
        entity_types = self._as_list(filters.get("entity_types"))
        if entity_types:
            norm_types = [biolink_entity_type(t) for t in entity_types]
            placeholders = ", ".join("?" for _ in norm_types)
            clauses.append(f"entity_type IN ({placeholders})")
            params.extend(norm_types)
        taxons = self._as_list(filters.get("ncbi_tax_id") or filters.get("taxonomy_ids"))
        if taxons:
            placeholders = ", ".join("?" for _ in taxons)
            clauses.append(f"taxon IN ({placeholders})")
            params.extend(str(t) for t in taxons)
        tokens = filters["entity_ids"] + filters["entity_pks"]
        if tokens:
            keys = self.resolve_entity_keys(tokens, resources or filters["sources"] or None)
            clauses.append("entity_key IN (SELECT unnest(?::VARCHAR[]))")
            params.append(keys)
        return clauses, params

    def _looks_like_accession(self, query: str) -> bool:
        q = (query or "").strip()
        if not q:
            return False
        if "|" in q or ":" in q:
            return True
        if len(q) >= 6 and any(ch.isdigit() for ch in q):
            return True
        return bool(re.fullmatch(r"[OPQ][0-9][A-Z0-9]{3}[0-9](?:-\d+)?", q, re.I))

    def _entity_text_where(self, query: str, *, mode: str = "prefix") -> tuple[str, list[Any]]:
        q = (query or "").strip()
        if not q:
            return "1=1", []
        if self._is_entity_key(q):
            return "entity_key = ?", [q]
        prefix = f"{q}%"
        if mode == "nested":
            return (
                "COALESCE(len(list_filter(identifiers, x -> x.id ILIKE ? OR upper(x.id) = ?)), 0) > 0",
                [prefix, q.upper()],
            )
        if mode == "contains":
            contains = f"%{q}%"
            return ("label ILIKE ?", [contains])
        clauses = ["label ILIKE ?"]
        params: list[Any] = [prefix]
        if self._looks_like_accession(q):
            clauses.extend(["identifier ILIKE ?", "(namespace || '|' || identifier) ILIKE ?"])
            params.extend([prefix, prefix])
        return f"({' OR '.join(clauses)})", params

    def search_entity_groups(self, *, strategy="chemical_connectivity", **kwargs):
        from omnipath_api.queries.connectivity import search_groups

        strategies = {"chemical_connectivity": search_groups}
        if strategy not in strategies:
            raise ValueError(f"Unknown grouping strategy: {strategy}")
        return strategies[strategy](self, **kwargs)

    def search_connectivity_groups(self, **kwargs):
        return self.search_entity_groups(strategy="chemical_connectivity", **kwargs)

    def get_entity_examples(self):
        from omnipath_api.examples import get_examples

        return get_examples(self)

    def search_entities_api(
        self,
        query: str = "",
        filters: dict[str, Any] | None = None,
        resources: list[str] | None = None,
        limit: int = 20,
        cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        filters = normalize_filters(filters)
        paths = self._resolve_entity_paths(resources or filters.get("sources"))
        if not paths:
            return {"entities": [], "nextCursor": None, "total": 0, "elapsed_ms": 0.0}

        read_expr = self._read_expr(paths)
        scalar_expr = self._read_expr(projected_paths(self.data_root, "entities", paths))
        q = (query or "").strip()
        query_key = hashlib.sha256(
            json.dumps([q, filters, paths], sort_keys=True).encode()
        ).hexdigest()
        if cursor:
            cursor = EntitySearchCursor.model_validate(cursor).model_dump()
            if cursor["queryKey"] != query_key:
                raise ValueError("Entity cursor belongs to another query")
        filter_clauses, filter_params = self._entity_filter_clauses(filters, resources=resources)
        fetch_limit = int(limit) + 1
        prefix = f"{q}%"
        rank_sql = """
                MIN(CASE WHEN ? <> '' AND upper(label) = upper(?) THEN 1
                         WHEN ? <> '' AND upper(identifier) = upper(?) THEN 2
                         WHEN ? <> '' AND label ILIKE ? THEN 3
                         WHEN ? <> '' AND identifier ILIKE ? THEN 4
                         ELSE 5 END)
            """
        rank_params = [q, q, q, q, q, prefix, q, prefix]
        if not q or self._is_entity_key(q):
            modes = ("prefix",)
        else:
            modes = ("prefix", "contains", "nested")
        if cursor:
            modes = (cursor["phase"],)
        rows: list[dict[str, Any]] = []
        for mode in modes:
            text_sql, text_params = self._entity_text_where(q, mode=mode)
            rows = self._fetch_merged_entity_rows(
                read_expr if mode == "nested" else scalar_expr,
                " AND ".join([text_sql, *filter_clauses]),
                [*text_params, *filter_params],
                rank_sql=rank_sql,
                rank_params=rank_params,
                limit=fetch_limit,
                slim=True,
                after=cursor,
            )
            if rows:
                break
        page = rows[: int(limit)]
        from omnipath_api.name_index import lookup_names
        from omnipath_core.display_names import CHEMICAL_TYPES, preferred_name

        chemical_rows = [
            row
            for row in page
            if str(row.get("entity_type") or "").replace("_", "").lower() in CHEMICAL_TYPES
        ]
        keys = list(
            {
                key
                for row in chemical_rows
                for key in row.get("_source_entity_keys", [row["entity_key"]])
            }
        )
        names = lookup_names(self, paths, keys)
        for row in chemical_rows:
            row["_display_name"] = preferred_name(
                names.get(key) for key in row.get("_source_entity_keys", [row["entity_key"]])
            )
        entities = [self._to_entity_summary(row) for row in page]
        next_cursor = None
        if len(rows) > int(limit) and page:
            last = page[-1]
            next_cursor = {
                "relationCount": 0,
                "entityPk": str(last["entity_key"]),
                "phase": mode,
                "matchRank": int(last["_match_rank"]),
                "sortLabel": last["_sort_label"],
                "namespace": last["namespace"],
                "identifier": last["identifier"],
                "taxon": last.get("taxon") or "",
                "queryKey": query_key,
            }

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "entities": entities,
            "nextCursor": next_cursor,
            "total": len(entities) if next_cursor is None else int(limit) + 1,
            "elapsed_ms": elapsed_ms,
        }

    def _fetch_entities_by_keys(
        self,
        keys: list[str],
        resources: list[str] | None = None,
        *,
        slim: bool = False,
    ) -> list[dict[str, Any]]:
        paths = self._resolve_entity_paths(resources)
        if not paths or not keys:
            return []
        read_expr = self._read_expr(
            projected_paths(self.data_root, "entities", paths) if slim else paths
        )
        placeholders = ", ".join("?" for _ in keys)
        cols = self._ENTITY_SCALAR_COLS
        if not slim:
            cols = f"{cols}, identifiers, annotations"
        rows = self._fetch_dicts(
            f"SELECT {cols} FROM {read_expr} WHERE entity_key IN ({placeholders})",
            keys,
        )
        # A key-addressed lookup must not collapse distinct keys by accession.
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row["entity_key"])].append(row)
        return [self._merge_entity_row_group(group) for group in groups.values()]

    def get_entities_by_pks(
        self,
        pks: list[str],
        resources: list[str] | None = None,
        *,
        slim: bool = False,
    ) -> dict[str, Any]:
        values = [str(v).strip() for v in pks if str(v).strip()]
        if not values:
            return {"entities": []}
        keys, other = self._split_entity_tokens(values)
        rows: list[dict[str, Any]] = []
        if keys:
            rows.extend(self._fetch_entities_by_keys(keys, resources, slim=slim))
        if other:
            paths = self._resolve_entity_paths(resources)
            if paths:
                read_expr = self._read_expr(paths)
                placeholders = ", ".join("?" for _ in other)
                where_sql = f"""
                    entity_key IN ({placeholders})
                       OR identifier IN ({placeholders})
                       OR UPPER(namespace || '|' || identifier) IN ({placeholders})
                       OR COALESCE(len(list_filter(identifiers, x -> upper(x.id) IN ({placeholders}))), 0) > 0
                """
                upper_values = [v.upper() for v in other]
                rows.extend(
                    self._fetch_merged_entity_rows(
                        read_expr,
                        where_sql,
                        other + other + upper_values + upper_values,
                    )
                )
                if keys:
                    rows = self._merge_duplicate_entity_rows(rows)
        return {"entities": [self._to_entity_summary(row) for row in rows]}

    def get_entities_by_public_ids(
        self, public_ids: list[str], resources: list[str] | None = None
    ) -> dict[str, Any]:
        return self.get_entities_by_pks(public_ids, resources)

    def get_entity_core(self, public_id, resources=None):
        from omnipath_api.entity_details import core

        return core(self, public_id, resources)

    def get_entity_relationships(self, public_id, resources=None, limit=50, offset=0):
        from omnipath_api.entity_details import relationships

        return relationships(self, public_id, resources, limit, offset)

    def get_entity_details(
        self, public_id: str, resources: list[str] | None = None
    ) -> dict[str, Any] | None:
        # Details are addressed solely by the exact entity key. Identifier and
        # alias discovery belongs to search, never to single-entity hydration.
        rows = self._fetch_entities_by_keys([public_id], resources)
        if not rows:
            return None
        entity = self._to_entity_summary(rows[0])
        rel_paths = self._resolve_relation_paths(resources)
        interaction_count = 0
        annotations: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        relationships_total = 0
        if rel_paths:
            read_expr = self._read_expr(rel_paths)
            keys = [str(k) for k in (entity.get("sourceEntityPks") or []) if str(k).strip()]
            if entity.get("entityPk") and str(entity["entityPk"]) not in keys:
                keys.append(str(entity["entityPk"]))
            if not keys:
                keys = [str(entity.get("entityPk") or "")]
            placeholders = ", ".join("?" for _ in keys)
            count_row = self._db.execute(
                f"""
                SELECT count(*) FROM {read_expr}
                WHERE subject_entity_key IN ({placeholders})
                   OR object_entity_key IN ({placeholders})
                """,
                keys + keys,
            ).fetchone()
            interaction_count = int(count_row[0] if count_row else 0)
            assoc_rows = self._fetch_dicts(
                f"""
                SELECT relation_key, predicate
                FROM {read_expr}
                WHERE (subject_entity_key IN ({placeholders}) OR object_entity_key IN ({placeholders}))
                  AND category = 'association'
                LIMIT 100
                """,
                keys + keys,
            )
            annotations = [
                {"relationPk": r["relation_key"], "predicate": r["predicate"]} for r in assoc_rows
            ]
            page = self.get_entity_relationships(public_id, resources)
            relationships = page["relationships"]
            relationships_total = page["relationshipsTotal"]
        return {
            "entity": entity,
            "relationships": relationships,
            "relationshipsTotal": relationships_total,
            "summary": {"interactionCount": interaction_count},
            "annotations": annotations,
        }

    def resolve_identifiers(
        self, identifiers: list[str], resources: list[str] | None = None
    ) -> dict[str, Any]:
        values = [str(v).strip() for v in identifiers if str(v).strip()]
        if not values:
            return {"matches": [], "entities": []}
        found = self.get_entities_by_pks(values, resources).get("entities") or []
        if not found:
            for value in values[:20]:
                extra = (
                    self.search_entities_api(query=value, limit=5, resources=resources).get(
                        "entities"
                    )
                    or []
                )
                found.extend(extra)
        unique: dict[str, dict[str, Any]] = {}
        for entity in found:
            unique[entity["entityPk"]] = entity
        entities = list(unique.values())
        matches = []
        for identifier in values:
            needle = identifier.lower()
            entity_ids = []
            for entity in entities:
                public_id = f"{entity['canonicalIdentifierType']}|{entity['canonicalIdentifier']}"
                haystacks = [
                    entity["entityPk"].lower(),
                    entity["canonicalIdentifier"].lower(),
                    public_id.lower(),
                    str(entity.get("label") or "").lower(),
                ]
                if any(needle == h or needle in h for h in haystacks if h):
                    entity_ids.append(public_id)
            matches.append({"identifier": identifier, "entityIds": entity_ids})
        return {"matches": matches, "entities": entities}
