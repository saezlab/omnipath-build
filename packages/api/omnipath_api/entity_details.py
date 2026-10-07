"""Release-aware entity hydration and paged, resource-local relationship reads."""

import json
from omnipath_api.serving_index import adjacency_rows, relation_rows_path, signature


def cache_key(engine, kind, public_id, resources, *page):
    paths = engine._resolve_entity_paths(resources)
    if kind == "relationships":
        paths += engine._resolve_relation_paths(resources)
    return json.dumps(
        [
            kind,
            public_id,
            page,
            signature(paths),
            engine._release_scope.get(),
            engine._taxonomy_cache_version(),
            [r.get("references") for r in engine.releases.list()],
        ],
        sort_keys=True,
        default=str,
    )


def core(engine, public_id, resources=None):
    def load():
        rows = engine._fetch_entities_by_keys([public_id], resources)
        return {"entity": engine._to_entity_summary(rows[0])} if rows else None

    return engine._detail_cache.get(cache_key(engine, "entity", public_id, resources), load)


def relationships(engine, public_id, resources=None, limit=50, offset=0):
    def load():
        paths = engine._resolve_relation_paths(resources)
        empty = dict(relationships=[], relationshipsTotal=0, nextCursor=None)
        if not paths:
            return empty
        # Relation endpoints come from the per-resource adjacency projection, sorted
        # by entity key; filename keeps source copies distinct and confines hydration.
        keys = list(dict.fromkeys(public_id if isinstance(public_id, list) else [public_id]))
        if not keys:
            return empty
        adjacency, params = adjacency_rows(
            engine,
            paths,
            keys,
            "predicate IN ('has_input','has_output','enabled_by','has_member','has_part')",
        )
        rows = engine._fetch_dicts(
            f"""SELECT filename, relation_row, relation_key, predicate, count(*) OVER () AS total
            FROM (SELECT DISTINCT filename, relation_row, relation_key, predicate FROM {adjacency})
            ORDER BY predicate, relation_key, filename LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        )
        if not rows:
            return empty
        # Relations are read by key (files are sorted by it) from the small-row-group
        # copy, without their evidence: a page shows its count, not its items.
        from omnipath_api.molecular import columns, read

        hydrated = {}
        for path in sorted({r["filename"] for r in rows}):
            source = relation_rows_path(engine.data_root, path)
            keys = [r["relation_key"] for r in rows if r["filename"] == path]
            fields = "* EXCLUDE (evidence)" if "evidence" in columns([source]) else "*"
            for record in engine._fetch_dicts(
                f"SELECT {fields} FROM {read([source], union_by_name=True)} "
                f"WHERE relation_key IN ({','.join('?' for _ in keys)})",
                keys,
            ):
                hydrated[(path, record["relation_key"])] = record
        records = [hydrated[(r["filename"], r["relation_key"])] for r in rows]
        keys = sorted(
            {r[side] for r in records for side in ("subject_entity_key", "object_entity_key")}
        )
        endpoints = {
            r["entity_key"]: engine._to_entity_summary(r)
            for r in engine._fetch_entities_by_keys(keys, resources, slim=True)
        }
        result = [
            dict(
                relation=engine._to_entity_relation(r),
                subjectEntity=endpoints.get(r["subject_entity_key"]),
                objectEntity=endpoints.get(r["object_entity_key"]),
                annotations=r.get("annotations") or [],
            )
            for r in records
        ]
        total = int(rows[0]["total"])
        return dict(
            relationships=result,
            relationshipsTotal=total,
            nextCursor=str(offset + len(rows)) if offset + len(rows) < total else None,
        )

    return engine._relationship_cache.get(
        cache_key(engine, "relationships", public_id, resources, limit, offset), load
    )


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
        rows = entity.get(field) or []
        longest = max(longest, len(rows))
        result[field] = rows[offset : offset + limit]
        result[field + "Total"] = len(rows)
        result[field + "NextCursor"] = str(offset + limit) if offset + limit < len(rows) else None
    result["detailNextCursor"] = str(offset + limit) if offset + limit < longest else None
    return result
