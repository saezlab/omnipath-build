"""Build-time alias removal and matching display-label updates."""

import re


def clean_record(record, excluded=()):
    """Remove excluded aliases and recompute a removed display label."""
    identifiers = record["identifiers"]
    if isinstance(identifiers, dict):
        pairs = [(ns, value) for ns, values in identifiers.items() for value in values]
    else:
        pairs = identifiers
    excluded = set(excluded)
    kept = [(ns, value) for ns, value in pairs if (ns, value) not in excluded]
    if len(kept) == len(pairs):
        return record
    if isinstance(identifiers, dict):
        updated = {}
        for ns, value in kept:
            updated.setdefault(ns, []).append(value)
    else:
        updated = [list(pair) for pair in kept]
    result = dict(record, identifiers=updated)
    if excluded:
        chemical = record["kind"] == 1
        priority = (
            {"name": 0, "chebi": 1, "pubchem": 2} if chemical else {"genesymbol": 0, "name": 1}
        )
        choices = []
        for ns, value in kept:
            value = value.strip()
            if ns not in priority or not 1 <= len(value) <= 120:
                continue
            if chemical and re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z0-9]", value):
                continue
            length = len(value) if ns in {"genesymbol", "name"} else 0
            long = length > (15 if ns == "genesymbol" else 80 if chemical else 120)
            choices.append(((priority[ns], long, length, value), ns, value))
        if choices:
            _, ns, label = min(choices)
            if ns == "chebi" and not label.upper().startswith("CHEBI"):
                label = "CHEBI:" + label
            elif ns == "pubchem":
                label = "CID:" + label
        else:
            label = record["entity_id"].split(":", 1)[-1]
        result["label"] = label
    return result
