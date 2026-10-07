"""Test fixtures: write a resource version's tables from readable nested rows.

Fixtures describe entities with their identifiers, annotations and evidence, and
relations with their annotations and evidence, as the API returns them; this writes
them as the normalized tables the build publishes (``omnipath_core.schema``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from omnipath_core.measurements import QUANTITY_STRUCT
from omnipath_core.schema import PUBLISHED_TABLES, RELATION_QUALIFIERS, SERVING_TABLES

_QUANTITY = [field.name for field in QUANTITY_STRUCT]


def _flat_quantity(annotation):
    quantity = annotation.get("quantity") or {}
    return {f"quantity_{name}": quantity.get(name) for name in _QUANTITY}


def _connectivity(entity):
    reference = entity.get("reference_entity_key") or ""
    return reference[9:23] if reference.startswith("inchikey:") else None


def write_resource(folder, entities=(), relations=(), payloads=None):
    """Write every table of one resource version into ``folder``; returns the folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    entities = sorted(entities, key=lambda e: e["entity_key"])
    relations = sorted(relations, key=lambda r: r["relation_key"])
    relation_count = {}
    for relation in relations:
        for side in ("subject", "object"):
            key = relation.get(f"{side}_entity_key")
            relation_count.setdefault(key, set()).add(relation["relation_key"])
    tables = {name: [] for name in (*PUBLISHED_TABLES, *SERVING_TABLES)}
    for entity_id, entity in enumerate(entities):
        identifiers = entity.get("identifiers") or []
        annotations = entity.get("annotations") or []
        evidence = entity.get("evidence") or []
        tables["entity"].append(
            dict(
                entity_id=entity_id,
                entity_key=entity["entity_key"],
                entity_type=entity.get("entity_type"),
                namespace=entity.get("namespace"),
                identifier=entity.get("identifier"),
                taxon=entity.get("taxon") or "",
                label=entity.get("label"),
                has_hierarchy=bool(entity.get("has_hierarchy")),
                parent_count=entity.get("parent_count") or 0,
                child_count=entity.get("child_count") or 0,
                reference_entity_key=entity.get("reference_entity_key"),
                gene_reference_keys=entity.get("gene_reference_keys"),
                group_connectivity=_connectivity(entity),
                identifier_count=len(identifiers),
                annotation_count=len(annotations),
                evidence_count=len(evidence),
                relation_count=len(relation_count.get(entity["entity_key"], ())),
            )
        )
        for ordinal, item in enumerate(identifiers):
            tables["entity_identifier"].append(
                dict(
                    entity_id=entity_id,
                    ordinal=ordinal,
                    ns=item.get("ns"),
                    id=item.get("id"),
                    is_canonical=bool(item.get("is_canonical")),
                    source=item.get("source") or "",
                )
            )
        for ordinal, item in enumerate(annotations):
            tables["entity_annotation"].append(
                dict(
                    entity_id=entity_id,
                    ordinal=ordinal,
                    term=item.get("term"),
                    value=item.get("value"),
                    **_flat_quantity(item),
                    source=item.get("source"),
                    dataset=item.get("dataset"),
                )
            )
        for ordinal, item in enumerate(evidence):
            tables["entity_evidence"].append(
                dict(
                    item,
                    entity_id=entity_id,
                    ordinal=ordinal,
                    annotations=item.get("annotations") or [],
                )
            )
        reference = entity.get("reference_entity_key")
        groups = [("reference", reference)] if reference else []
        groups += [("gene", key) for key in entity.get("gene_reference_keys") or []]
        if _connectivity(entity):
            groups.append(("connectivity", _connectivity(entity)))
        tables["entity_group"] += [
            dict(group_key=key, kind=kind, entity_id=entity_id) for kind, key in groups
        ]
        terms = {("label", entity.get("label")), ("identifier", entity.get("identifier"))}
        terms |= {("identifier", item.get("id")) for item in identifiers}
        tables["entity_term"] += [
            dict(term=value.lower(), kind=kind, entity_id=entity_id)
            for kind, value in terms
            if value
        ]
    for relation_id, relation in enumerate(relations):
        annotations = relation.get("annotations") or []
        evidence = relation.get("evidence") or []
        qualifiers = {
            name: sorted(
                {
                    a.get("value")
                    for a in annotations
                    if a.get("term") == name and a.get("scope", "relation") == "relation"
                }
            )
            for name in RELATION_QUALIFIERS
        }
        tables["relation"].append(
            dict(
                {name: relation.get(name) for name in PUBLISHED_TABLES["relation"].names},
                relation_id=relation_id,
                statement_kind=relation.get("statement_kind") or "relation",
                taxon=relation.get("taxon") or "",
                is_directed=bool(relation.get("is_directed")),
                sign=relation.get("sign") or 0,
                sources=relation.get("sources") or [],
                evidence_count=relation.get("evidence_count", len(evidence)),
                annotation_count=len(annotations),
                **qualifiers,
            )
        )
        for ordinal, item in enumerate(annotations):
            tables["relation_annotation"].append(
                dict(
                    relation_id=relation_id,
                    ordinal=ordinal,
                    term=item.get("term"),
                    value=item.get("value"),
                    **_flat_quantity(item),
                    source=item.get("source"),
                    dataset=item.get("dataset"),
                    scope=item.get("scope", "relation"),
                )
            )
        for ordinal, item in enumerate(evidence):
            tables["relation_evidence"].append(
                dict(
                    item,
                    relation_id=relation_id,
                    ordinal=ordinal,
                    annotations=item.get("annotations") or [],
                )
            )
        for side in ("subject", "object"):
            for kind, key in (
                ("entity", relation.get(f"{side}_entity_key")),
                ("reference", relation.get(f"{side}_reference_entity_key")),
            ):
                if key:
                    tables["relation_endpoint"].append(
                        dict(
                            key=key,
                            key_kind=kind,
                            side=side,
                            relation_id=relation_id,
                            predicate=relation.get("predicate"),
                            category=relation.get("category"),
                        )
                    )
    tables["evidence_payloads"] = list(payloads or [])
    order = {
        "relation_endpoint": ("key", "relation_id"),
        "entity_group": ("group_key", "entity_id"),
        "entity_term": ("term", "entity_id"),
    }
    for name, schema in {**PUBLISHED_TABLES, **SERVING_TABLES}.items():
        rows = tables[name]
        if name in order:
            rows = sorted(rows, key=lambda r: tuple(r[c] for c in order[name]))
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), folder / f"{name}.parquet")
    return folder


def write_manifest(folder, resource, version, **extra):
    """A valid build manifest for the tables written in ``folder``; returns it."""
    import hashlib

    from omnipath_core.versioning import BUILD_SCHEMA_VERSION, SERVING_SCHEMA_VERSION

    folder = Path(folder)
    files = {
        path.name: dict(
            rows=pq.read_metadata(path).num_rows,
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(folder.glob("*.parquet"))
    }
    manifest = dict(
        schema_version=BUILD_SCHEMA_VERSION,
        serving_schema_version=SERVING_SCHEMA_VERSION,
        resource=resource,
        version=version,
        files=files,
        **extra,
    )
    (folder / "build_manifest.json").write_text(json.dumps(manifest))
    return manifest


def _folder(path):
    path = Path(path)
    return path.parent if path.suffix == ".parquet" else path


def read_resource(folder):
    """The nested (entities, relations) a resource version was written from; ``folder``
    may also be one of its table files."""
    folder = _folder(folder)

    def rows(name):
        return pq.read_table(folder / f"{name}.parquet").to_pylist()

    def children(name, parent):
        found = {}
        for row in sorted(rows(name), key=lambda r: (r[parent], r["ordinal"])):
            owner = row.pop(parent)
            row.pop("ordinal")
            if "quantity_" + _QUANTITY[0] in row:
                quantity = {q: row.pop("quantity_" + q) for q in _QUANTITY}
                row["quantity"] = (
                    quantity if any(v is not None for v in quantity.values()) else None
                )
            found.setdefault(owner, []).append(row)
        return found

    nested = {
        name: children(name, "entity_id")
        for name in ("entity_identifier", "entity_annotation", "entity_evidence")
    }
    entities = []
    for row in rows("entity"):
        entity_id = row.pop("entity_id")
        for derived in (
            "group_connectivity",
            "identifier_count",
            "annotation_count",
            "evidence_count",
            "relation_count",
        ):
            row.pop(derived)
        row["identifiers"] = nested["entity_identifier"].get(entity_id, [])
        row["annotations"] = nested["entity_annotation"].get(entity_id, [])
        row["evidence"] = nested["entity_evidence"].get(entity_id, [])
        entities.append(row)
    annotations = children("relation_annotation", "relation_id")
    evidence = children("relation_evidence", "relation_id")
    relations = []
    for row in rows("relation"):
        relation_id = row.pop("relation_id")
        for derived in ("annotation_count", *RELATION_QUALIFIERS):
            row.pop(derived)
        row["annotations"] = annotations.get(relation_id, [])
        row["evidence"] = evidence.get(relation_id, [])
        relations.append(row)
    return entities, relations


def rewrite_resource(folder, entities=None, relations=None):
    """Write a resource version again with new entities and/or relations (a new one
    when nothing is written yet)."""
    folder = _folder(folder)
    old_entities, old_relations, payloads = [], [], []
    if (folder / "entity.parquet").exists():
        old_entities, old_relations = read_resource(folder)
        payloads = pq.read_table(folder / "evidence_payloads.parquet").to_pylist()
    write_resource(
        folder,
        old_entities if entities is None else entities,
        old_relations if relations is None else relations,
        payloads,
    )


def nested_rows(path):
    """The nested entities (``entity.parquet``) or relations (``relation.parquet``)."""
    entities, relations = read_resource(path)
    return relations if Path(path).name.startswith("relation") else entities
