"""Utilities for building annotation lists from source records."""

from typing import Any, Callable


def build_annotations(
    record: Any,
    *specs: str | tuple[str, str] | tuple[str, str, str] | tuple[str, str, str, Callable],
) -> list[dict] | None:
    """
    Build annotation list from attribute specifications.

    Each spec can be:
    - Just an attribute name: 'category' -> {"term": "category", "value": rec.category}
    - (attr, term): ('formula', 'chemical_formula') -> {"term": "chemical_formula", "value": rec.formula}
    - (attr, term, units): ('exact_mass', 'exact_mass', 'Da') -> {"term": "exact_mass", "value": rec.exact_mass, "units": "Da"}
    - (attr, term, units, transformer): ('charge', 'charge', None, str) -> applies str() to value

    None values are automatically filtered out.

    Args:
        record: Source object with attributes
        *specs: Variable number of annotation specifications

    Returns:
        List of annotation dicts, or None if empty

    Example:
        annotations = build_annotations(
            rec,
            'category',                                    # Simple: uses attr name as term
            ('formula', 'chemical_formula'),              # Custom term name
            ('exact_mass', 'exact_mass', 'Da'),          # With units
            ('charge', 'charge', None, str),             # With transformer
        )
    """
    annotations = []

    for spec in specs:
        annotation = None

        if isinstance(spec, str):
            # Simple case: just attribute name
            value = getattr(record, spec, None)
            if value is not None:
                annotation = {"term": spec, "value": value}

        elif isinstance(spec, tuple):
            if len(spec) == 2:
                # (attr, term)
                attr_name, term = spec
                value = getattr(record, attr_name, None)
                if value is not None:
                    annotation = {"term": term, "value": value}

            elif len(spec) == 3:
                # (attr, term, units)
                attr_name, term, units = spec
                value = getattr(record, attr_name, None)
                if value is not None:
                    annotation = {"term": term, "value": value}
                    if units:
                        annotation["units"] = units

            elif len(spec) == 4:
                # (attr, term, units, transformer)
                attr_name, term, units, transformer = spec
                value = getattr(record, attr_name, None)
                if value is not None:
                    value = transformer(value)
                    annotation = {"term": term, "value": value}
                    if units:
                        annotation["units"] = units

        if annotation:
            annotations.append(annotation)

    return annotations if annotations else None
