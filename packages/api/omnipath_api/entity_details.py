"""Release-aware entity hydration and paged, resource-local relationship reads."""

import json

# Structural relationships shown in an entity's dialog; other relations are paged
# from relation search.
STRUCTURAL = ("has_input", "has_output", "enabled_by", "has_member", "has_part")


def cache_key(engine, kind, public_id, resources, *page):
    return json.dumps(
        [
            kind,
            public_id,
            page,
            [info["key"] for info in engine._selected_resource_infos(resources)],
            engine._inventory_fingerprint,
            engine._release_scope.get(),
            engine._taxonomy_cache_version(),
            [r.get("references") for r in engine.releases.list()],
        ],
        sort_keys=True,
        default=str,
    )


def _copies(engine, public_id, resources):
    """The entity's rows in each selected resource, by key (files are sorted by it)."""
    return engine._entity_rows("entity_key = ?", [public_id], resources, nested=False)


def core(engine, public_id, resources=None):
    def load():
        copies = engine._with_entity_children(_copies(engine, public_id, resources))
        if not copies:
            return None
        entity = engine._to_entity_summary(engine._merge_entity_row_group(copies))
        # Evidence is paged from its own endpoint; the entity carries only its count.
        entity["molecularEvidenceTotal"] = sum(int(c["evidence_count"] or 0) for c in copies)
        return {"entity": entity}

    return engine._detail_cache.get(cache_key(engine, "entity", public_id, resources), load)


def evidence(engine, public_id, resources=None, limit=20, offset=0):
    """A page of an entity's evidence items, by resource and position."""

    def load():
        copies = _copies(engine, public_id, resources)
        if not copies:
            return None
        total = sum(int(c["evidence_count"] or 0) for c in copies)
        items, start = [], 0
        for copy in copies:
            count = int(copy["evidence_count"] or 0)
            first, last = max(offset - start, 0), min(offset + limit - start, count)
            if first < last:
                rows = engine._lookup(
                    "entity_evidence",
                    "entity_id",
                    [(copy["resource"], copy["entity_id"])],
                    extra="ORDER BY ordinal LIMIT ? OFFSET ?",
                    params=[last - first, first],
                )
                for row in rows:
                    for name in ("entity_id", "ordinal", "resource"):
                        row.pop(name)
                items += rows
            start += count
        end = offset + len(items)
        return dict(
            evidence=items,
            evidenceTotal=total,
            nextCursor=str(end) if items and end < total else None,
        )

    return engine._detail_cache.get(
        cache_key(engine, "evidence", public_id, resources, limit, offset), load
    )


def _endpoint_page(engine, keys, resources, where="TRUE", params=(), limit=50, offset=0):
    """A page of distinct (resource, relation_id, predicate) of relations with an
    endpoint among ``keys``, ordered by predicate; each row carries the total."""
    return engine._fetch_dicts(
        f"""SELECT resource, relation_id, predicate, count(*) OVER () AS total FROM (
            SELECT DISTINCT resource, relation_id, predicate
            FROM {engine._table("relation_endpoint", resources)}
            WHERE key IN (SELECT unnest(?::VARCHAR[])) AND key_kind = 'entity' AND {where})
        ORDER BY predicate, resource, relation_id LIMIT ? OFFSET ?""",
        [keys, *params, limit, offset],
    )


def _relations(engine, pairs):
    """Relation rows for (resource, relation_id) pairs, in the pairs' order."""
    found = {
        (r["resource"], r["relation_id"]): r
        for r in engine._lookup("relation", "relation_id", pairs)
    }
    return [found[pair] for pair in pairs if pair in found]


def relationships(engine, public_id, resources=None, limit=50, offset=0):
    def load():
        keys = list(dict.fromkeys(public_id if isinstance(public_id, list) else [public_id]))
        empty = dict(relationships=[], relationshipsTotal=0, nextCursor=None)
        if not keys:
            return empty
        marks = ",".join("?" for _ in STRUCTURAL)
        rows = _endpoint_page(
            engine, keys, resources, f"predicate IN ({marks})", STRUCTURAL, limit, offset
        )
        if not rows:
            return empty
        records = _relations(engine, [(r["resource"], r["relation_id"]) for r in rows])
        engine._children("relation_annotation", records, "annotations")
        result = engine._relation_items(records, annotations=lambda r: r["annotations"])
        total = int(rows[0]["total"])
        return dict(
            relationships=result,
            relationshipsTotal=total,
            nextCursor=str(offset + len(rows)) if offset + len(rows) < total else None,
        )

    return engine._relationship_cache.get(
        cache_key(engine, "relationships", public_id, resources, limit, offset), load
    )


def details(engine, public_id, resources=None):
    """The entity, its structural relationships, relation count and associations."""
    found = core(engine, public_id, resources)
    if not found:
        return None
    entity = found["entity"]
    keys = list(dict.fromkeys([*(entity.get("sourceEntityPks") or []), entity["entityPk"]]))
    counts = engine._fetch_dicts(
        f"""SELECT count(*) AS n, count(*) FILTER (WHERE category = 'association') AS a FROM (
            SELECT DISTINCT resource, relation_id, category
            FROM {engine._table("relation_endpoint", resources)}
            WHERE key IN (SELECT unnest(?::VARCHAR[])) AND key_kind = 'entity')""",
        [keys],
    )[0]
    associations = []
    if counts["a"]:
        rows = _endpoint_page(engine, keys, resources, "category = 'association'", limit=100)
        associations = [
            {"relationPk": r["relation_key"], "predicate": r["predicate"]}
            for r in sorted(
                _relations(engine, [(r["resource"], r["relation_id"]) for r in rows]),
                key=lambda r: (r["relation_key"], r["resource"]),
            )
        ]
    page = relationships(engine, public_id, resources)
    return {
        "entity": entity,
        "relationships": page["relationships"],
        "relationshipsTotal": page["relationshipsTotal"],
        "summary": {"interactionCount": int(counts["n"] or 0)},
        "annotations": associations,
    }


def page_details(entity, limit=20, offset=0):
    """Bound each nested collection in an entity response."""
    result = dict(entity)
    longest = 0
    for field in (
        "identifiers",
        "entityAttributes",
        "sources",
        "resources",
        "sourceEntityPks",
        "molecularEvidence",
    ):
        if field == "molecularEvidence" and "molecularEvidenceTotal" in entity:
            continue  # counted only; the items are paged from /entities/{id}/evidence
        rows = entity.get(field) or []
        longest = max(longest, len(rows))
        result[field] = rows[offset : offset + limit]
        result[field + "Total"] = len(rows)
        result[field + "NextCursor"] = str(offset + limit) if offset + limit < len(rows) else None
    result["detailNextCursor"] = str(offset + limit) if offset + limit < longest else None
    return result
