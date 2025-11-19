import json
from pathlib import Path

import polars as pl


def load_tables(db_dir: Path):
    return {
        "entities": pl.read_parquet(db_dir / "entity.parquet"),
        "memberships": pl.read_parquet(db_dir / "membership.parquet"),
        "membership_annotations": pl.read_parquet(db_dir / "membership_annotation.parquet"),
        "entity_identifiers": pl.read_parquet(db_dir / "entity_identifier.parquet"),
    }


def get_identifier_type_id(entity_identifiers: pl.DataFrame, identifier_code: str) -> int | None:
    """Return the entity_id for an identifier type entity (e.g., 'OM:0201')."""
    row = entity_identifiers.filter(pl.col("identifier") == identifier_code)
    return None if row.is_empty() else row["entity_id"][0]


def get_entity_names(entity_identifiers: pl.DataFrame, entity_ids, type_id: int):
    return (
        entity_identifiers
        .filter(pl.col("entity_id").is_in(entity_ids) & (pl.col("type_id") == type_id))
        .select("entity_id", "identifier")
    )


def get_annotation_info(entity_identifiers: pl.DataFrame, annotation_ids, cv_type_id, name_type_id):
    """Return CV term accessions + names for annotation entity IDs."""
    if not annotation_ids:
        return {}

    cv = (
        entity_identifiers
        .filter(pl.col("entity_id").is_in(annotation_ids) & (pl.col("type_id") == cv_type_id))
        .select("entity_id", "identifier")
    )

    names = (
        entity_identifiers
        .filter(pl.col("entity_id").is_in(annotation_ids) & (pl.col("type_id") == name_type_id))
        .select("entity_id", "identifier")
    )

    cv_map = {row["entity_id"]: row["identifier"] for row in cv.iter_rows(named=True)}
    name_map = {row["entity_id"]: row["identifier"] for row in names.iter_rows(named=True)}

    return {
        annot_id: {
            "cv_term": cv_map.get(annot_id),
            "name": name_map.get(annot_id),
        }
        for annot_id in annotation_ids
    }


def _format_annotation_term(annotation_id: int | None,
                            annotation_info: dict[int, dict[str, str | None]]) -> str:
    """Return consistent term string (Name:ID) for filtering."""
    if annotation_id is None:
        return "UNKNOWN:unknown"
    info = annotation_info.get(annotation_id, {})
    base_label = info.get("name") or info.get("cv_term") or "UNKNOWN"
    return f"{base_label}:{annotation_id}"


def _build_annotation_maps(records: list[tuple[str, str | None, str | None]]):
    """Convert ordered term/value/unit tuples into keyed dict maps."""
    if not records:
        return {}
    terms = {}
    values = {}
    units = {}
    for idx, (term, value, unit) in enumerate(records, start=1):
        key = str(idx)
        terms[key] = term
        if value:
            values[key] = value
        if unit:
            units[key] = unit
    payload = {"terms": terms}
    if values:
        payload["values"] = values
    if units:
        payload["units"] = units
    return payload


def summarize_interaction(interaction_id: int, tables: dict[str, pl.DataFrame]):
    entities = tables["entities"]
    memberships = tables["memberships"]
    membership_annotations = tables["membership_annotations"]
    entity_identifiers = tables["entity_identifiers"]

    memberships_for = memberships.filter(pl.col("parent_id") == interaction_id)
    if memberships_for.is_empty():
        raise ValueError(f"No memberships found for interaction {interaction_id}")
    if len(memberships_for) < 2:
        raise ValueError(f"Need at least two members for interaction {interaction_id}")

    interaction_annotation_rows = memberships.filter(pl.col("member_id") == interaction_id)

    member_ids = memberships_for["member_id"].to_list()
    member_entities = (
        entities
        .filter(pl.col("entity_id").is_in(member_ids))
        .select(["entity_id", "entity_type_id"])
    )
    member_type_map = {row["entity_id"]: row["entity_type_id"] for row in member_entities.iter_rows(named=True)}

    name_type_id = get_identifier_type_id(entity_identifiers, "OM:0202")  # NAME
    type_names_df = get_entity_names(entity_identifiers, member_type_map.values(), name_type_id)
    type_name_map = {row["entity_id"]: row["identifier"] for row in type_names_df.iter_rows(named=True)}

    membership_ids = memberships_for["id"].to_list()
    mem_annots = membership_annotations.filter(pl.col("membership_id").is_in(membership_ids))

    member_annotation_ids: set[int] = set()
    member_annotation_unit_ids: set[int] = set()
    membership_member_map = {row["id"]: row["member_id"] for row in memberships_for.iter_rows(named=True)}

    member_annotation_records: list[dict[str, int | str | None]] = []
    if len(mem_annots):
        for row in mem_annots.iter_rows(named=True):
            member_id = membership_member_map.get(row["membership_id"])
            if member_id is None:
                continue
            annot_id = row["annotation_id"]
            unit_id = row["annotation_unit"]
            value = row["annotation_value"]
            if isinstance(value, str):
                value = value.strip()
            member_annotation_records.append({
                "member_id": member_id,
                "annotation_id": annot_id,
                "annotation_value": value,
                "annotation_unit_id": unit_id,
                "source_id": row["source_id"],
            })
            if annot_id is not None:
                member_annotation_ids.add(annot_id)
            if unit_id is not None:
                member_annotation_unit_ids.add(unit_id)

    interaction_annotations = [
        {
            "annotation_id": row["parent_id"],
            "annotation_value": row["annotation_value"].strip() if isinstance(row["annotation_value"], str) else row["annotation_value"],
            "annotation_unit_id": row["annotation_unit"],
            "source_id": row["source_id"],
        }
        for row in interaction_annotation_rows.iter_rows(named=True)
    ]
    interaction_annotation_ids = {
        row["annotation_id"] for row in interaction_annotations
        if row["annotation_id"] is not None
    }
    interaction_annotation_unit_ids = {
        row["annotation_unit_id"] for row in interaction_annotations
        if row["annotation_unit_id"] is not None
    }

    all_annotation_ids = sorted(
        member_annotation_ids |
        member_annotation_unit_ids |
        interaction_annotation_ids |
        interaction_annotation_unit_ids
    )
    cv_type_id = get_identifier_type_id(entity_identifiers, "OM:0201")   # CV_TERM_ACCESSION
    annotation_info = get_annotation_info(entity_identifiers, all_annotation_ids, cv_type_id, name_type_id)
    # Build member payload (a/b)
    sorted_members = sorted(memberships_for.iter_rows(named=True), key=lambda r: (r["member_id"], r["id"]))
    member_labels = ["a", "b"]
    member_id_to_label = {}
    member_types_list = []
    member_a_id = None
    member_b_id = None

    for label, row in zip(member_labels, sorted_members[:2]):
        member_id = row["member_id"]
        type_id = member_type_map.get(member_id)
        type_name = type_name_map.get(type_id) or "UNKNOWN"
        type_suffix = f"{type_name}:{type_id}" if type_id is not None else type_name
        if label == "a":
            member_a_id = member_id
        else:
            member_b_id = member_id
        member_types_list.append(type_suffix)
        member_id_to_label[member_id] = label

    if member_a_id is None or member_b_id is None:
        raise ValueError(f"Failed to determine both members for interaction {interaction_id}")

    evidence_by_source: dict[int, dict[str, list[tuple[str, str | None, str | None]]]] = {}

    def ensure_source(source_id: int | None):
        if source_id is None:
            return None
        return evidence_by_source.setdefault(source_id, {
            "interaction": [],
            "member_a": [],
            "member_b": [],
        })

    for ann in interaction_annotations:
        target = ensure_source(ann["source_id"])
        if target is None:
            continue
        term = _format_annotation_term(ann["annotation_id"], annotation_info)
        unit_term = _format_annotation_term(ann["annotation_unit_id"], annotation_info) if ann["annotation_unit_id"] is not None else None
        target["interaction"].append((term, ann["annotation_value"], unit_term))

    for rec in member_annotation_records:
        label = member_id_to_label.get(rec["member_id"])
        if label is None:
            continue
        target = ensure_source(rec["source_id"])
        if target is None:
            continue
        term = _format_annotation_term(rec["annotation_id"], annotation_info)
        unit_term = _format_annotation_term(rec["annotation_unit_id"], annotation_info) if rec["annotation_unit_id"] is not None else None
        key = "member_a" if label == "a" else "member_b"
        target[key].append((term, rec["annotation_value"], unit_term))

    evidence = []
    for source_id, data in sorted(evidence_by_source.items()):
        entry = {}
        interaction_payload = _build_annotation_maps(data["interaction"])
        if interaction_payload:
            entry["interaction_annotation_terms"] = interaction_payload["terms"]
            if "values" in interaction_payload:
                entry["interaction_annotation_values"] = interaction_payload["values"]
            if "units" in interaction_payload:
                entry["interaction_annotation_units"] = interaction_payload["units"]

        member_a_payload = _build_annotation_maps(data["member_a"])
        if member_a_payload:
            entry["member_a_annotation_terms"] = member_a_payload["terms"]
            if "values" in member_a_payload:
                entry["member_a_annotation_values"] = member_a_payload["values"]
            if "units" in member_a_payload:
                entry["member_a_annotation_units"] = member_a_payload["units"]

        member_b_payload = _build_annotation_maps(data["member_b"])
        if member_b_payload:
            entry["member_b_annotation_terms"] = member_b_payload["terms"]
            if "values" in member_b_payload:
                entry["member_b_annotation_values"] = member_b_payload["values"]
            if "units" in member_b_payload:
                entry["member_b_annotation_units"] = member_b_payload["units"]

        if entry:
            evidence.append(entry)

    return {
        "member_a_id": member_a_id,
        "member_b_id": member_b_id,
        "member_types": member_types_list,
        "evidence": evidence,
    }


def find_interaction_by_intact_id(entity_identifiers: pl.DataFrame, intact_id: str) -> int:
    intact_type_id = get_identifier_type_id(entity_identifiers, "OM:0103")
    match = entity_identifiers.filter(
        (pl.col("type_id") == intact_type_id) &
        (pl.col("identifier") == intact_id)
    )
    if match.is_empty():
        raise ValueError(f"No interaction found for IntAct ID {intact_id}")
    return match["entity_id"][0]


def get_raw_intact_row(intact_file: Path, intact_id: str):
    df = pl.read_parquet(intact_file)
    indexed = df.with_row_index("row_idx")
    matches = (
        indexed
        .select(["row_idx", "identifiers"])
        .explode("identifiers")
        .filter(
            (pl.col("identifiers").struct.field("type") == "OM:0103") &
            (pl.col("identifiers").struct.field("value") == intact_id)
        )
    )
    if matches.is_empty():
        raise ValueError(f"No source row found for IntAct ID {intact_id}")
    row_idx = matches["row_idx"][0]
    return indexed.filter(pl.col("row_idx") == row_idx).to_dicts()[0]


def main():
    db_dir = Path("databases/omnipath/output")
    intact_file = Path("databases/omnipath/data/intact/intact_interactions.parquet")
    intact_id = "EBI-22274805"

    tables = load_tables(db_dir)
    interaction_id = find_interaction_by_intact_id(tables["entity_identifiers"], intact_id)

    raw_row = get_raw_intact_row(intact_file, intact_id)
    processed_summary = summarize_interaction(interaction_id, tables)

    print(f"=== Source IntAct row for {intact_id} ===")
    print(json.dumps(raw_row, indent=2))
    print(f"\n=== Processed summary for entity {interaction_id} ===")
    print(json.dumps(processed_summary, indent=2))


if __name__ == "__main__":
    main()
