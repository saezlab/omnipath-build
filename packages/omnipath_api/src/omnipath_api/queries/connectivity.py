"""A query-time chemical view; canonical identities and relation endpoints stay intact."""

from __future__ import annotations

from functools import lru_cache
import time
import json
from omnipath_api.shape.display_name import display_name, preferred_name


@lru_cache(maxsize=1)
def chemical_types():
    from omnipath_core.biolink import entity_type, schema

    return tuple(
        entity_type(name) for name in schema().class_descendants("chemical entity", reflexive=True)
    )


def search_groups(engine, **kwargs):
    from omnipath_api.entity_details import cache_key

    start = time.perf_counter()
    detail_limit = kwargs.pop("detail_limit", 20)
    detail_offset = kwargs.pop("detail_offset", 0)
    resources = kwargs.get("resources") or (kwargs.get("filters") or {}).get("sources")
    key = cache_key(engine, "groups", json.dumps(kwargs, sort_keys=True), resources)
    result = engine._detail_cache.get(key, lambda: _search_groups(engine, **kwargs))
    if kwargs.get("include_details"):
        from omnipath_api.entity_details import page_details

        for group in result["groups"]:
            group["entity"] = page_details(group["entity"], detail_limit, detail_offset)
    result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def _search_groups(
    engine,
    *,
    query="",
    filters=None,
    resources=None,
    limit=20,
    cursor=None,
    group_key=None,
    member_cursor=None,
    member_limit=5,
    include_details=False,
    include_member_keys=False,
):
    start = time.perf_counter()
    filters = filters or {}
    paths = engine._resolve_entity_paths(resources or filters.get("sources"))
    if not paths:
        return {"groups": [], "nextCursor": None, "elapsed_ms": 0}
    read = engine._read_expr(paths)
    clauses, params = engine._entity_filter_clauses(filters)
    types = chemical_types()
    q = query.strip()
    if q:
        # Use the same prefix/substring/alias fallback as the ungrouped search;
        # toggling the view must not broaden the matching entity set.
        modes = ("prefix",) if engine._is_entity_key(q) else ("prefix", "contains", "nested")
        for mode in modes:
            text_sql, text_params = engine._entity_text_where(q, mode=mode)
            if engine._fetch_dicts(
                f"SELECT 1 FROM {read} WHERE ({' AND '.join(clauses) or 'TRUE'}) AND {text_sql} LIMIT 1",
                [*params, *text_params],
            ):
                clauses.append(text_sql)
                params.extend(text_params)
                break
        else:
            clauses.append("FALSE")
    valid = "regexp_full_match(upper(identifier), '[A-Z]{14}-[A-Z]{10}-[A-Z]')"
    aliases = "list_distinct(list_transform(list_filter(identifiers, x -> lower(x.ns) = 'inchikey' AND regexp_full_match(upper(x.id), '[A-Z]{14}-[A-Z]{10}-[A-Z]')), x -> left(upper(x.id), 14)))"
    fields = [
        "entity_type",
        "namespace",
        "identifier",
        "taxon",
        "label",
        "has_hierarchy",
        "parent_count",
        "child_count",
    ]
    scalars = ", ".join(f"min({name}) AS {name}" for name in fields)
    # Missing or conflicting connectivity is a singleton, never one shared null group.
    cte = f"""WITH filtered AS (
        SELECT *, CASE WHEN namespace = 'inchikey' AND {valid} THEN FALSE ELSE coalesce(len({aliases}) > 1, FALSE) END AS ambiguous_key,
          CASE WHEN entity_type IN ({",".join("?" for _ in types)}) THEN
          CASE WHEN namespace = 'inchikey' AND {valid} THEN left(upper(identifier), 14)
          WHEN len({aliases}) = 1 THEN ({aliases})[1] ELSE NULL END END AS connectivity
        FROM {read} WHERE {" AND ".join(clauses) or "TRUE"}
    ), entities AS (
        SELECT entity_key, {scalars},
          CASE WHEN NOT bool_or(ambiguous_key) AND count(DISTINCT connectivity) = 1 THEN min(connectivity) END AS connectivity
        FROM filtered GROUP BY entity_key
    ), grouped AS (
        SELECT *, coalesce('connectivity:' || connectivity, 'entity:' || entity_key) AS group_key FROM entities
    )"""
    params = [*types, *params]
    # Keyset pagination follows the same count-descending order as the cards.
    page_params = []
    group_where = "TRUE"
    if group_key:
        group_where = "group_key = ?"
        page_params = [group_key]
    elif cursor:
        try:
            count, key = json.loads(cursor)
            if not isinstance(count, int) or count < 1 or not isinstance(key, str):
                raise ValueError("Invalid group cursor")
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid group cursor") from exc
        group_where = "(member_count < ? OR (member_count = ? AND group_key > ?))"
        page_params = [count, count, key]
    page_limit = 1 if group_key else int(limit) + 1
    member_fields = ", ".join(f"{name} := g.{name}" for name in ["entity_key", *fields])
    # Materialize the scalar grouping once; only collect members for the page.
    groups = engine._fetch_dicts(
        cte
        + f""", counts AS (
        SELECT group_key, min(connectivity) AS connectivity, count(*) AS member_count
        FROM grouped GROUP BY group_key
    ), page AS (
        SELECT * FROM counts WHERE {group_where}
        ORDER BY member_count DESC, group_key LIMIT {page_limit}
    ) SELECT page.*, list(struct_pack({member_fields}) ORDER BY g.entity_key) AS member_rows
      FROM page JOIN grouped g USING (group_key)
      GROUP BY ALL ORDER BY member_count DESC, group_key
    """,
        [*params, *page_params],
    )
    next_cursor = (
        json.dumps([groups[int(limit) - 1]["member_count"], groups[int(limit) - 1]["group_key"]])
        if not group_key and len(groups) > int(limit)
        else None
    )
    groups = groups[: int(limit)]
    if groups:
        by_key = {}
        if include_details:
            # Nested identifiers and annotations are needed only when a card opens.
            member_keys = [row["entity_key"] for group in groups for row in group["member_rows"]]
            records = engine._fetch_dicts(
                f"SELECT * FROM {read} WHERE entity_key IN ({','.join('?' for _ in member_keys)})",
                member_keys,
            )
            for row in records:
                row["identifiers"] = [
                    *(row.get("identifiers") or []),
                    {"ns": row["namespace"], "id": row["identifier"]},
                    {"ns": "name", "id": row.get("label") or row["identifier"]},
                ]
                by_key.setdefault(row["entity_key"], []).append(row)
        preferred_by_key = {}
        if not include_details:
            # Only inspect name identifiers for this page, never annotations.
            # Reduce all aliases to one ranked value per member inside DuckDB.
            member_keys = [
                row["entity_key"]
                for group in groups
                if group["connectivity"]
                for row in group["member_rows"]
            ]
            if member_keys:
                from omnipath_api.name_index import lookup_names

                preferred_by_key = lookup_names(engine, paths, member_keys)
        for group in groups:
            group["group_label"] = group["connectivity"]
            group["is_group"] = group["connectivity"] is not None
            all_members = group.pop("member_rows")
            if include_details:
                all_members = [
                    engine._merge_entity_row_group(by_key[row["entity_key"]]) for row in all_members
                ]
            members = [m for m in all_members if m["entity_key"] > (member_cursor or "")]
            group["members"] = [
                engine._to_entity_summary({k: row.get(k) for k in ["entity_key", *fields]})
                for row in members[:member_limit]
            ]
            group["nextMemberCursor"] = (
                members[member_limit - 1]["entity_key"] if len(members) > member_limit else None
            )
            display_members = all_members if include_details else all_members[:1]
            summaries = [engine._to_entity_summary(row) for row in display_members]
            entity = engine._to_entity_summary(engine._merge_entity_row_group(display_members))
            # Include every canonical identifier and label, not just nested aliases.
            identifiers = {}
            for summary in summaries:
                for identifier in summary["identifiers"]:
                    identifiers.setdefault(
                        (identifier["identifierType"], identifier["identifier"]), identifier
                    )
            entity["identifiers"] = list(identifiers.values())
            entity["identifiersTotal"] = len(identifiers)
            entity["sources"] = sorted(
                {source for summary in summaries for source in summary["sources"]}
            )
            entity["displayName"] = display_name(entity)
            if group["is_group"]:
                if not include_details:
                    entity["displayName"] = (
                        preferred_name(preferred_by_key.get(m["entity_key"]) for m in all_members)
                        or entity["displayName"]
                    )
                entity["entityPk"] = group["group_key"]
                entity["canonicalIdentifier"] = group["connectivity"]
                entity["canonicalIdentifierType"] = "connectivity"
                entity["primaryNamespace"] = "connectivity"
                entity["primaryIdentifier"] = group["connectivity"]
                taxa = {m.get("taxon") for m in all_members}
                if len(taxa) > 1:
                    entity["taxonomyId"] = None
                    entity["taxonomyName"] = None
                    entity["taxon"] = None
                entity["groupMemberKeys"] = [
                    m["entity_key"]
                    for m in (all_members if include_member_keys else members[:member_limit])
                ]
                entity["groupMemberCursor"] = group["nextMemberCursor"]
                entity["groupMemberCount"] = len(all_members)
                entity["groupDetailsLoaded"] = include_details
                entity["groupQuery"] = query
                entity["groupFilters"] = filters
                entity["groupResources"] = resources or filters.get("sources") or []
                # A stereoisomer's depiction cannot represent the whole group.
                entity["ontologyHierarchy"] = None
            group["entity"] = entity
    return {
        "groups": groups,
        "nextCursor": next_cursor,
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
    }
