"""Catalogue products and evidence-supported forms have distinct navigation."""

import json
from typing import Literal

from omnipath_api.molecular import columns, matching_evidence, read


def _product_kind(summary):
    entity_type = (summary.get("entityType") or "").lower()
    # Reusable writer records have these literal types. A namespace describes
    # an identifier, not whether this navigation selected a product record.
    return entity_type if entity_type in {"protein", "transcript"} else None


def _referenced_products(engine, relations, standalone, known_keys, resources):
    """Hydrate at most 100 additional exact product records in one scalar lookup."""
    keys = {}

    def remember(form):
        for field in ("protein_entity_key", "transcript_entity_key"):
            key = (form or {}).get(field)
            if key and key not in known_keys:
                keys.setdefault(key, None)
                if len(keys) == 100:
                    return True
        return False

    # Give standalone observations their labels even when a relation has many
    # different paired forms. Both endpoint products of each returned pair count.
    full = False
    for record in standalone:
        if remember(record["occurrence"].get("molecular_form")):
            full = True
            break
    if not full:
        for row in relations:
            for evidence in row["evidence"]:
                for side in ("subject", "object"):
                    if remember(evidence.get(side + "_molecular_form")):
                        full = True
                        break
                if full:
                    break
            if full:
                break
    return (
        engine.get_entities_by_pks(list(keys), resources, slim=True).get("entities") or []
        if keys
        else []
    )


def context(
    engine,
    entity_id,
    resources=None,
    isoform_identifier=None,
    limit=20,
    offset=0,
    view: Literal["reference", "product"] = "reference",
):
    if view not in {"reference", "product"}:
        raise ValueError("Context view must be reference or product.")
    if isoform_identifier and view != "product":
        raise ValueError("Isoform selection requires product view.")
    gene_group = entity_id.startswith("gene:")
    entity = None if gene_group else engine.get_entity_core(entity_id, resources)
    summary = entity["entity"] if entity else {}
    reference = entity_id[5:] if gene_group else summary.get("referenceEntityKey")
    if not gene_group and not entity:
        return None
    product_kind = _product_kind(summary)
    is_product = view == "product"
    if is_product and product_kind is None:
        raise ValueError("Product view requires a protein or transcript record.")
    gene_reference = bool(reference and reference.startswith("entrez:"))
    filters = (
        {"reference_entity_keys": [reference]} if gene_reference else {"entity_pks": [entity_id]}
    )
    if is_product:
        field = product_kind + "_entity_keys"
        filters = {field: [entity_id]}
    if isoform_identifier:
        filters["isoform_identifiers"] = [isoform_identifier]
    page = engine.search_relations(filters, resources, limit, offset, include_details=True)
    by_pk = engine._endpoint_entities_by_pk(page["rows"], resources)
    relations = [
        {
            "relation": engine._to_entity_relation(row),
            "subjectEntity": engine._hydrated_endpoint(row, "subject", by_pk),
            "objectEntity": engine._hydrated_endpoint(row, "object", by_pk),
            "evidence": row.get("evidence") or [],
        }
        for row in page["rows"]
    ]
    forms = {}
    for row in page["rows"]:
        for evidence in row.get("evidence") or []:
            for side in ("subject", "object"):
                if not is_product:
                    field, selected = (
                        ("_reference_entity_key", reference)
                        if gene_reference
                        else ("_entity_key", entity_id)
                    )
                    if row.get(side + field) != selected:
                        continue
                form = evidence.get(side + "_molecular_form")
                if (
                    form
                    and is_product
                    and not matching_evidence(
                        [{"subject_molecular_form": form}],
                        {**filters, "molecular_endpoint_mode": "source"},
                    )
                ):
                    continue
                if form:
                    forms.setdefault(json.dumps(form, sort_keys=True), form)
    products, standalone = [], []
    standalone_more = False
    paths = engine._resolve_entity_paths(resources)
    if paths:
        if reference:
            rows = engine._fetch_dicts(
                f"SELECT * FROM {engine._read_expr(paths)} WHERE list_contains(gene_reference_keys, ?) AND namespace <> 'entrez'",
                [reference],
            )
            products = [
                engine._to_entity_summary(r) for r in engine._merge_duplicate_entity_rows(rows)
            ]
        # Standalone evidence can predate stored references; native typed-key
        # selection still applies, and product view keeps its exact form filter.
        if "evidence" in columns(paths):
            predicates, values = (
                (["reference_entity_key = ?"], [reference])
                if gene_reference
                else (["entity_key = ?"], [entity_id])
            )
            if is_product:
                # An asserted native product can be named by observations
                # on a gene row without having any catalogue gene links.
                field = product_kind + "_entity_key"
                predicates = [f"json_extract_string(to_json(ev), '$.molecular_form.{field}') = ?"]
                values = [entity_id]
            if isoform_identifier:
                predicates.append(
                    "json_extract_string(to_json(ev), '$.molecular_form.isoform_identifier.ns') || ':' || json_extract_string(to_json(ev), '$.molecular_form.isoform_identifier.id') = ?"
                )
                values.append(isoform_identifier)
            standalone = engine._fetch_dicts(
                f"SELECT entity_key AS entityPk, ev AS occurrence FROM {read(paths, filename=True, file_row_number=True)}, UNNEST(evidence) WITH ORDINALITY AS occurrences(ev, occurrence_index) WHERE {' AND '.join(predicates)} ORDER BY filename, file_row_number, occurrence_index LIMIT ? OFFSET ?",
                [*values, limit + 1, offset],
            )
            standalone_more = len(standalone) > limit
            standalone = standalone[:limit]
            for record in standalone:
                form = record["occurrence"].get("molecular_form")
                if form:
                    forms.setdefault(json.dumps(form, sort_keys=True), form)
    referenced_products = _referenced_products(
        engine,
        relations,
        standalone,
        set(by_pk) | {product["entityPk"] for product in products},
        resources,
    )
    return {
        "referenceEntityKey": reference,
        "standaloneEvidence": standalone,
        "catalogueProducts": products,
        "referencedProducts": referenced_products,
        "observedForms": list(forms.values()),
        "relations": relations,
        "relationsTotal": page["total"],
        "nextCursor": str(offset + limit)
        if offset + len(relations) < page["total"] or standalone_more
        else None,
        "filters": filters,
    }
