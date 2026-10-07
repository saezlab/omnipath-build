"""Grouped entity views: chemicals by structure, gene products by their gene.

A query-time view; canonical identities and relation endpoints stay intact. Chemicals
group by connectivity (the first InChIKey block of their reference), other entities by
a shared Entrez gene reference. An entity whose resource copies disagree, or that has
no group, is a singleton. ``auto`` applies both, the other strategies one of them.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import json
import time

from omnipath_api.models import normalize_filters
from omnipath_api.shape.display_name import display_name, preferred_name

_FIELDS = (
    "entity_key, entity_type, namespace, identifier, taxon, label, reference_entity_key, "
    "gene_reference_keys, has_hierarchy, parent_count, child_count, identifier_count, "
    "annotation_count, relation_count, resource, entity_id"
)


@lru_cache(maxsize=1)
def chemical_types():
    from omnipath_core.biolink import entity_type, schema

    return tuple(
        entity_type(name) for name in schema().class_descendants("chemical entity", reflexive=True)
    )


def search_groups(engine, *, strategy="chemical_connectivity", **kwargs):
    """Cached per release and inventory: the explorer's first page is the same for everyone."""
    from omnipath_api.entity_details import cache_key, page_details

    start = time.perf_counter()
    detail_limit = kwargs.pop("detail_limit", 20)
    detail_offset = kwargs.pop("detail_offset", 0)
    resources = kwargs.get("resources") or (kwargs.get("filters") or {}).get("sources")
    key = cache_key(
        engine, "groups", json.dumps([strategy, kwargs], sort_keys=True, default=str), resources
    )
    result = engine._detail_cache.get(key, lambda: _search_groups(engine, strategy, **kwargs))
    groups = [dict(group) for group in result["groups"]]
    if kwargs.get("include_details"):
        for group in groups:
            group["entity"] = page_details(group["entity"], detail_limit, detail_offset)
    return dict(
        result, groups=groups, elapsed_ms=round((time.perf_counter() - start) * 1000, 2)
    )


def _search_groups(
    engine,
    strategy,
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
    **kwargs,
):
    filters = normalize_filters(filters)
    scope = resources or filters["sources"] or None
    if not engine._selected_resource_infos(scope):
        return {"groups": [], "nextCursor": None}
    clauses, params = engine._entity_filter_clauses(filters, resources=resources)
    where, params = engine._entity_match_where(query, clauses, params, scope)
    types = chemical_types()
    marks = ",".join("?" for _ in types)
    chemical = strategy in {"auto", "chemical_connectivity"}
    gene = strategy in {"auto", "gene_reference"}
    # One row per entity key, then one per group: the release-wide default page groups
    # millions of keys, so each step aggregates once and avoids DISTINCT aggregates
    # (min = max is "exactly one distinct non-null value").
    cte = f"""WITH selected AS (
        SELECT {_FIELDS}, group_connectivity FROM {engine._table("entity", scope)} WHERE {where}
    ), keys AS (
        SELECT entity_key, bool_or(coalesce(entity_type IN ({marks}), FALSE)) AS chemical,
            CASE WHEN min(group_connectivity) = max(group_connectivity)
                THEN min(group_connectivity) END AS connectivity,
            CASE WHEN min(reference_entity_key) = max(reference_entity_key)
                AND bool_and(coalesce(starts_with(reference_entity_key, 'entrez:'), FALSE))
                THEN min(reference_entity_key) END AS gene_reference
        FROM selected GROUP BY entity_key
    ), assigned AS (
        SELECT entity_key,
            CASE WHEN chemical AND {chemical} THEN connectivity END AS connectivity,
            CASE WHEN NOT chemical AND {gene} THEN gene_reference END AS reference_entity_key
        FROM keys
    ), grouped AS (
        SELECT *, coalesce('connectivity:' || connectivity, 'gene:' || reference_entity_key,
            'entity:' || entity_key) AS group_key
        FROM assigned
    )"""
    params = [*params, *types]
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
    member_fields = ", ".join(f"{name} := s.{name}" for name in _FIELDS.split(", "))
    # Members are collected only for the page's groups.
    page = engine._fetch_dicts(
        cte
        + f""", counts AS (
            SELECT group_key, min(connectivity) AS connectivity,
                min(reference_entity_key) AS reference_entity_key, count(*) AS member_count
            FROM grouped GROUP BY group_key
        ), page AS (
            SELECT * FROM counts WHERE {extra} ORDER BY member_count DESC, group_key LIMIT ?
        ) SELECT page.*, list(struct_pack({member_fields}) ORDER BY s.entity_key, s.resource)
            AS member_rows
        FROM page JOIN grouped g USING (group_key) JOIN selected s USING (entity_key)
        GROUP BY ALL ORDER BY member_count DESC, group_key""",
        [*params, *values, 1 if group_key else int(limit) + 1],
    )
    next_cursor = (
        json.dumps([page[int(limit) - 1]["member_count"], page[int(limit) - 1]["group_key"]])
        if not group_key and len(page) > int(limit)
        else None
    )
    page = page[: int(limit)]
    if include_details:
        engine._with_entity_children([row for group in page for row in group["member_rows"]])
    groups = []
    for group in page:
        by_key = defaultdict(list)
        for row in group.pop("member_rows"):
            by_key[row["entity_key"]].append(row)
        members = [engine._merge_entity_row_group(copies) for copies in by_key.values()]
        summaries = [engine._to_entity_summary(row) for row in members]
        visible = [s for s in summaries if s["entityPk"] > (member_cursor or "")]
        if group["connectivity"]:
            entity = _chemical_card(engine, group, members, summaries, include_details)
        elif group["reference_entity_key"]:
            entity = _gene_card(group, summaries)
        else:
            entity = summaries[0]
        is_group = bool(group["connectivity"] or group["reference_entity_key"])
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
                groupResources=scope or [],
            )
        entity["groupMemberCursor"] = next_member_cursor
        group.update(
            entity=entity,
            members=visible[:member_limit],
            is_group=is_group,
            group_label=group["connectivity"] or group["reference_entity_key"],
            nextMemberCursor=next_member_cursor,
        )
        groups.append(group)
    return {"groups": groups, "nextCursor": next_cursor}


def _union(summaries, field, identity):
    return list(
        {identity(item): item for summary in summaries for item in summary.get(field) or []}.values()
    )


def _chemical_card(engine, group, members, summaries, include_details):
    """A structure group: every member's identifiers, labels and sources; its name the
    preferred one of the member labels."""
    entity = engine._to_entity_summary(
        engine._merge_entity_row_group(members if include_details else members[:1])
    )
    identifiers = _union(summaries, "identifiers", lambda i: (i["identifierType"], i["identifier"]))
    entity.update(
        identifiers=identifiers,
        identifiersTotal=len(identifiers),
        sources=sorted({s for summary in summaries for s in summary["sources"]}),
        displayName=preferred_name(s["label"] for s in summaries) or display_name(entity),
        canonicalIdentifier=group["connectivity"],
        canonicalIdentifierType="connectivity",
        primaryNamespace="connectivity",
        primaryIdentifier=group["connectivity"],
        groupStrategy="chemical_connectivity",
        # A stereoisomer's depiction cannot represent the whole group.
        ontologyHierarchy=None,
    )
    if include_details:
        entity["entityAttributes"] = _union(
            summaries, "entityAttributes", lambda a: json.dumps(a, sort_keys=True, default=str)
        ) or None
    if len({m.get("taxon") for m in members}) > 1:
        entity.update(taxonomyId=None, taxonomyName=None, taxon=None)
    return entity


def _gene_card(group, summaries):
    """A gene group: annotations, identifiers and sources of every member; a gene's
    function text sits on the protein record resolved to it, not on the first member."""
    entity = dict(summaries[0])
    identifiers = _union(summaries, "identifiers", lambda i: (i["identifierType"], i["identifier"]))
    attributes = _union(
        summaries, "entityAttributes", lambda a: json.dumps(a, sort_keys=True, default=str)
    )
    entity.update(
        entityAttributes=attributes or None,
        identifiers=identifiers,
        identifiersTotal=len(identifiers),
        sources=sorted({s for summary in summaries for s in summary["sources"]}),
        referenceEntityKey=group["reference_entity_key"],
        entityType="gene",
        memberEntityTypes=sorted({s["entityType"] for s in summaries}),
        canonicalIdentifier=group["reference_entity_key"].split(":", 1)[-1],
        canonicalIdentifierType="entrez",
        groupStrategy="gene_reference",
    )
    return entity
