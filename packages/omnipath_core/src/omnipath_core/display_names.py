"""Preferred display names, computed before paging identifiers.

Preserves the explorer's chemical-name scoring. Lexical ties are deterministic
across resource order and pages. SQL and Python share the same scoring rules.
"""

import re

NAME_NAMESPACES = ("name", "om:0202", "entry_name", "entry name")
NAME_RULES = (
    (r"(?i)^https?://", 100),
    (r"(?i)^(MLS|SMR|cid_|ZINC|SID_|CID_|InChI=)", 100),
    (r"^[A-Z0-9._-]{1,3}$", 10),
    (r"^[A-Z][0-9A-Z]{3,}$", 8),
    (r"^[A-Z][a-z]", -3),
)
CHEMICAL_TYPES = {
    "chemicalentity",
    "chemical",
    "smallmolecule",
    "compound",
    "metabolite",
    "drug",
    "lipid",
}


def name_rank(name):
    name = name.strip()
    score = sum(penalty for pattern, penalty in NAME_RULES if re.search(pattern, name))
    score += 25 * (len(name) > 80) + 8 * (len(name) > 40) + 4 * (len(name) < 4)
    return score, len(name), name


def preferred_name(names):
    names = {name.strip() for name in names if name and name.strip()}
    return min(names, key=name_rank) if names else None


def name_rank_sql(value):
    value = f"trim({value})"
    parts = [
        f"CASE WHEN regexp_matches({value}, '{pattern}') THEN {penalty} ELSE 0 END"
        for pattern, penalty in NAME_RULES
    ]
    parts += [
        f"CASE WHEN len({value}) {op} {length} THEN {penalty} ELSE 0 END"
        for op, length, penalty in [(">", 80, 25), (">", 40, 8), ("<", 4, 4)]
    ]
    return f"struct_pack(score := ({' + '.join(parts)}), length := len({value}), name := {value})"


def preferred_name_sql():
    """One scalar preferred name per input row; no grouping across sources."""
    namespaces = ",".join("'" + ns + "'" for ns in NAME_NAMESPACES)
    names = f"list_filter(list_prepend(label, list_transform(list_filter(identifiers, x -> lower(x.ns) IN ({namespaces}) OR ends_with(lower(x.ns), ':name') OR contains(lower(x.ns), ' entry name')), x -> x.id)), n -> n IS NOT NULL AND trim(n) <> '')"
    best = f"(list_sort(list_transform({names}, n -> {name_rank_sql('n')}))[1]).name"
    kinds = ",".join("'" + kind + "'" for kind in sorted(CHEMICAL_TYPES))
    return f"CASE WHEN lower(replace(entity_type, '_', '')) IN ({kinds}) THEN coalesce({best}, label, identifier) ELSE coalesce(nullif(label, ''), identifier) END"
