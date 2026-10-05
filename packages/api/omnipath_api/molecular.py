"""Stored gene references and occurrence-local molecular identity; no resolution at read time."""

from functools import lru_cache
from pathlib import Path
import pyarrow.parquet as pq
from omnipath_api.store.connection import format_read_parquet


@lru_cache(maxsize=2048)
def _columns(path, size, mtime):
    return frozenset(pq.read_schema(path).names)


def columns(paths):
    return (
        set().union(
            *(_columns(str(p), Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in paths)
        )
        if paths
        else set()
    )


def read(paths, **options):
    expr = format_read_parquet(paths, **options)
    names = columns(paths)
    extras = []
    if "entity_key" in names:
        defaults = {"reference_entity_key": "NULL::VARCHAR", "gene_reference_keys": "[]::VARCHAR[]"}
    elif "relation_key" in names:
        defaults = {
            "subject_reference_entity_key": "NULL::VARCHAR",
            "object_reference_entity_key": "NULL::VARCHAR",
        }
    else:
        defaults = {}
    for name, default in defaults.items():
        if name not in names:
            extras.append(f"{default} AS {name}")
    return f"(SELECT *, {', '.join(extras)} FROM {expr})" if extras else expr


def occurrences_expression(names):
    if "molecular_occurrences" in names:
        return (
            "coalesce(molecular_occurrences, list_transform(evidence, e -> to_json(e)))"
            if "evidence" in names
            else "molecular_occurrences"
        )
    if "evidence" in names:
        return "list_transform(evidence, e -> to_json(e))"
    return "[]::JSON[]"


def has_form_filters(filters):
    return any(
        filters.get(k)
        for k in ("protein_entity_keys", "transcript_entity_keys", "isoform_identifiers")
    )


def form_match_sql(filters, *, occurrence="ev"):
    """All specified identity constraints match one endpoint of one occurrence."""
    params = []
    sides = []
    for side in ("subject", "object"):
        clauses = []
        for key, field in [
            ("protein_entity_keys", "protein_entity_key"),
            ("transcript_entity_keys", "transcript_entity_key"),
            ("isoform_identifiers", "isoform_identifier"),
        ]:
            values = filters.get(key) or []
            if not values:
                continue
            if field == "isoform_identifier":
                path = f"$.{side}_molecular_form.{field}"
                value = f"json_extract_string({occurrence}, '{path}.ns') || ':' || json_extract_string({occurrence}, '{path}.id')"
            else:
                value = f"json_extract_string({occurrence}, '$.{side}_molecular_form.{field}')"
            clauses.append(f"list_contains(?::VARCHAR[], {value})")
            params.append(values)
        sides.append("(" + (" AND ".join(clauses) or "TRUE") + ")")
    mode = filters.get("molecular_endpoint_mode", "any")
    if mode == "source":
        return sides[0], params[: len(params) // 2]
    if mode == "target":
        return sides[1], params[len(params) // 2 :]
    return f"({sides[0]} {'AND' if mode == 'both' else 'OR'} {sides[1]})", params


def matching_evidence(items, filters):
    def endpoint(form):
        form = form or {}
        for key, field in [
            ("protein_entity_keys", "protein_entity_key"),
            ("transcript_entity_keys", "transcript_entity_key"),
        ]:
            if filters.get(key) and form.get(field) not in filters[key]:
                return False
        iso = form.get("isoform_identifier") or {}
        return (
            not filters.get("isoform_identifiers")
            or f"{iso.get('ns')}:{iso.get('id')}" in filters["isoform_identifiers"]
        )

    result = []
    for item in items or []:
        subject, obj = (
            endpoint(item.get("subject_molecular_form")),
            endpoint(item.get("object_molecular_form")),
        )
        mode = filters.get("molecular_endpoint_mode", "any")
        match = (
            subject
            if mode == "source"
            else obj
            if mode == "target"
            else subject and obj
            if mode == "both"
            else subject or obj
        )
        if match:
            result.append(item)
    return result
