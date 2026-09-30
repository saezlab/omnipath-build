"""Separate canonical chemical identity from reference-library coverage in reports."""

from copy import deepcopy

ABSENT_ANCHOR_REASONS = {
    "supplied_inchikey_not_in_reference",
    "derived_inchikey_not_in_reference",
}


def canonical_identity_statistics(statistics):
    """Reclassify validated unique primary anchors, preserving lookup statistics.

    This aggregate adapter requires one unresolved bundle per entity. More
    complex inputs must be classified from their per-entity evidence instead.
    It changes reporting only; it never invents reference matches or targets.
    """
    result = deepcopy(statistics)
    if result.get("resolution_counting_policy") == "canonical_identity_v1":
        return result
    counts = result["entity_counts"]
    reasons = result["unresolved_reasons"]
    absent = [r for r in reasons if r["unresolved_reason"] in ABSENT_ANCHOR_REASONS]
    if absent:
        if (
            counts["partially_resolved"]
            or sum(r["bundles"] for r in reasons) != counts["unresolved"]
            or any(r["bundles"] != r["entity_keys"] for r in absent)
        ):
            raise ValueError(
                "Canonical reclassification requires per-entity accounting for this resource"
            )
    result["reference_match_counts"] = deepcopy(counts)
    result["reference_lookup_details"] = {
        k: result.pop(k) for k in ("bundles", "by_type", "structure_derivations") if k in result
    }
    result["identified_inchikey_absent_from_reference"] = {
        r["unresolved_reason"]: r["entity_keys"] for r in absent
    }
    identified = sum(result["identified_inchikey_absent_from_reference"].values())
    counts["fully_resolved"] += identified
    counts["unresolved"] -= identified
    counts["resolved_fraction"] = (
        (counts["fully_resolved"] + counts["partially_resolved"]) / counts["eligible_entities"]
        if counts["eligible_entities"]
        else None
    )
    result["unresolved_reasons"] = [
        r for r in reasons if r["unresolved_reason"] not in ABSENT_ANCHOR_REASONS
    ]
    result["resolution_counting_policy"] = "canonical_identity_v1"
    result["scope_note"] = (
        "Resolution includes valid supplied or SMILES-derived full InChIKeys even when absent from the reference. Reference matches remain separately counted. Only chemical and gene/protein entities are eligible; other types are outside scope."
    )
    return result
