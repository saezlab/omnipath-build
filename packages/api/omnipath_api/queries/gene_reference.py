"""Gene-centered and automatic presentation, retaining source-typed members."""

from collections import defaultdict
import json

from omnipath_api.models import normalize_filters
from omnipath_api.serving_index import entity_group_rows, projected_paths
from omnipath_api.shape.display_name import display_name, preferred_name


def search_groups(engine, **kwargs):
    """Cached per release and inventory: the explorer's first page is the same for everyone."""
    from omnipath_api.entity_details import cache_key

    filters = kwargs.get("filters") or {}
    resources = kwargs.get("resources") or filters.get("sources")
    key = cache_key(
        engine, "entity-groups", json.dumps(kwargs, sort_keys=True, default=str), resources
    )
    return engine._detail_cache.get(key, lambda: _search_groups(engine, **kwargs))


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
    mixed=False,
    detail_limit=20,
    detail_offset=0,
    **kwargs,
):
    filters = normalize_filters(filters)
    scope = resources or filters["sources"]
    paths = engine._resolve_entity_paths(scope)
    if not paths:
        return {"groups": [], "nextCursor": None}
    read = engine._read_expr(projected_paths(engine.data_root, "entities", paths))
    raw = engine._read_expr(paths)
    clauses, params = engine._entity_filter_clauses(filters, resources=resources)
    q = query.strip()
    if q:
        modes = ("prefix",) if engine._is_entity_key(q) else ("prefix", "contains", "nested")
        for mode in modes:
            text, values = engine._entity_text_where(q, mode=mode)
            hits = engine._fetch_dicts(
                f"SELECT DISTINCT entity_key FROM {raw if mode == 'nested' else read} "
                f"WHERE ({' AND '.join(clauses) or 'TRUE'}) AND {text}",
                [*params, *values],
            )
            if hits:
                clauses.append("entity_key IN (SELECT unnest(?::VARCHAR[]))")
                params.append([r["entity_key"] for r in hits])
                break
        else:
            clauses.append("FALSE")
    fields = (
        "entity_key, entity_type, namespace, identifier, taxon, label, "
        "reference_entity_key, gene_reference_keys, has_hierarchy, parent_count, child_count"
    )
    from omnipath_api.queries.connectivity import chemical_types

    types = chemical_types()
    type_sql = ",".join("?" for _ in types)
    # One row per entity key, then one per group: the release-wide default page groups
    # millions of keys, so each step aggregates once and avoids DISTINCT aggregates
    # (min = max is "exactly one distinct non-null value" where all rows are non-null).
    chemical = f"bool_or(coalesce(entity_type IN ({type_sql}), FALSE))" if mixed else "FALSE"
    cte = f"""WITH selected AS (
        SELECT DISTINCT {fields} FROM {read}
        WHERE {" AND ".join(clauses) or "TRUE"}
    ), keys AS (
        SELECT entity_key, CASE WHEN min(reference_entity_key) = max(reference_entity_key)
            AND bool_and(coalesce(starts_with(reference_entity_key, 'entrez:'), FALSE))
            AND bool_and(coalesce(entity_type NOT IN ({type_sql}), FALSE))
            THEN min(reference_entity_key) END AS gene_reference,
            {chemical} AS chemical
        FROM selected GROUP BY entity_key
    )"""
    params = [*params, *types, *(types if mixed else [])]
    if mixed:
        # Connectivity is precomputed per row in the serving projection. Chemical keys
        # in this search scope include their resource copies, to detect conflicts.
        cte += f""", chemical_assignments AS (
            SELECT entity_key, CASE WHEN NOT bool_or(group_ambiguous)
                AND min(group_connectivity) = max(group_connectivity)
                THEN min(group_connectivity) END AS connectivity
            FROM {entity_group_rows(engine, paths)}
            WHERE entity_key IN (SELECT entity_key FROM keys WHERE chemical)
            GROUP BY entity_key
        ), assigned AS (
            SELECT k.entity_key,
                CASE WHEN NOT k.chemical THEN k.gene_reference END AS grouping_reference,
                c.connectivity,
                CASE WHEN k.chemical
                    THEN coalesce('connectivity:' || c.connectivity, 'entity:' || k.entity_key)
                    ELSE coalesce('gene:' || k.gene_reference, 'entity:' || k.entity_key)
                END AS group_key
            FROM keys k LEFT JOIN chemical_assignments c USING(entity_key)
        )"""
    else:
        cte += """, assigned AS (
            SELECT entity_key, gene_reference AS grouping_reference, NULL::VARCHAR AS connectivity,
                coalesce('gene:' || gene_reference, 'entity:' || entity_key) AS group_key
            FROM keys
        )"""
    extra, values = "TRUE", []
    if group_key:
        extra, values = "group_key = ?", [group_key]
    elif cursor:
        try:
            count, key = json.loads(cursor)
            if not isinstance(count, int) or count < 1 or not isinstance(key, str):
                raise ValueError("Invalid group cursor")
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid group cursor") from exc
        extra, values = (
            "(member_count < ? OR (member_count = ? AND group_key > ?))",
            [count, count, key],
        )
    member_fields = ", ".join(f"{name} := g.{name}" for name in fields.split(", "))
    page = engine._fetch_dicts(
        cte
        + f""", counts AS (
            SELECT group_key, min(grouping_reference) AS reference_entity_key,
                min(connectivity) AS connectivity, count(*) AS member_count
            FROM assigned GROUP BY group_key
        ), page AS (
            SELECT * FROM counts WHERE {extra}
            ORDER BY member_count DESC, group_key LIMIT ?
        ) SELECT page.*, list(struct_pack({member_fields}) ORDER BY g.entity_key, g.label)
            AS member_rows FROM page JOIN assigned a USING(group_key)
            JOIN selected g USING(entity_key)
            GROUP BY ALL ORDER BY member_count DESC, group_key""",
        [*params, *values, 1 if group_key else limit + 1],
    )
    next_cursor = (
        json.dumps([page[limit - 1]["member_count"], page[limit - 1]["group_key"]])
        if not group_key and len(page) > limit
        else None
    )
    page = page[:limit]
    preferred_by_key = {}
    if mixed and not include_details:
        chemical_keys = [
            row["entity_key"]
            for group in page
            if group["connectivity"]
            for row in group["member_rows"]
        ]
        if chemical_keys:
            from omnipath_api.name_index import lookup_names

            preferred_by_key = lookup_names(engine, paths, list(dict.fromkeys(chemical_keys)))
    groups = []
    for group in page:
        by_key = defaultdict(list)
        for row in group.pop("member_rows"):
            by_key[row["entity_key"]].append(row)
        members = [engine._merge_entity_row_group(copies) for copies in by_key.values()]
        if include_details:
            members = engine._fetch_entities_by_keys(list(by_key), scope)
        members.sort(key=lambda row: row["entity_key"])
        chemical = bool(group["connectivity"])
        if chemical and include_details:
            members = [
                dict(
                    row,
                    identifiers=[
                        *(row.get("identifiers") or []),
                        {"ns": row["namespace"], "id": row["identifier"]},
                        {"ns": "name", "id": row.get("label") or row["identifier"]},
                    ],
                )
                for row in members
            ]
        summaries = [engine._to_entity_summary(r) for r in members]
        visible = [s for s in summaries if s["entityPk"] > (member_cursor or "")]
        entity = dict(summaries[0])
        if chemical:
            entity = engine._to_entity_summary(
                engine._merge_entity_row_group(members if include_details else members[:1])
            )
            identifiers = {
                (item["identifierType"], item["identifier"]): item
                for summary in summaries
                for item in summary["identifiers"]
            }
            entity["identifiers"] = list(identifiers.values())
            entity["identifiersTotal"] = len(identifiers)
            entity["sources"] = sorted({s for summary in summaries for s in summary["sources"]})
            entity["displayName"] = display_name(entity)
            if not include_details:
                entity["displayName"] = (
                    preferred_name(preferred_by_key.get(m["entity_key"]) for m in members)
                    or entity["displayName"]
                )
            entity.update(
                canonicalIdentifier=group["connectivity"],
                canonicalIdentifierType="connectivity",
                primaryNamespace="connectivity",
                primaryIdentifier=group["connectivity"],
                groupStrategy="chemical_connectivity",
                ontologyHierarchy=None,
            )
            if len({m.get("taxon") for m in members}) > 1:
                entity.update(taxonomyId=None, taxonomyName=None, taxon=None)
        elif group["reference_entity_key"]:
            entity.update(
                referenceEntityKey=group["reference_entity_key"],
                entityType="gene",
                memberEntityTypes=sorted({s["entityType"] for s in summaries}),
                canonicalIdentifier=group["reference_entity_key"].split(":", 1)[-1],
                canonicalIdentifierType="entrez",
                groupStrategy="gene_reference",
            )
        is_group = chemical or bool(group["reference_entity_key"])
        next_member_cursor = (
            visible[member_limit - 1]["entityPk"] if len(visible) > member_limit else None
        )
        if is_group:
            entity.update(
                entityPk=group["group_key"],
                groupMemberKeys=[
                    s["entityPk"]
                    for s in (summaries if include_member_keys else visible[:member_limit])
                ],
                groupMemberCount=len(summaries),
                groupDetailsLoaded=include_details,
                groupQuery=query,
                groupFilters=filters,
                groupResources=scope,
            )
        group.update(
            entity=entity,
            members=visible[:member_limit],
            is_group=is_group,
            group_label=group["connectivity"] or group["reference_entity_key"],
            nextMemberCursor=next_member_cursor,
        )
        entity["groupMemberCursor"] = next_member_cursor
        if include_details and mixed:
            from omnipath_api.entity_details import page_details

            group["entity"] = page_details(entity, detail_limit, detail_offset)
        groups.append(group)
    return {"groups": groups, "nextCursor": next_cursor}
