from __future__ import annotations

from collections.abc import Iterable

from pypath.internals.cv_terms import CvEnum


def _iter_cv_subclasses(base: type) -> Iterable[type]:
    for subcls in base.__subclasses__():
        yield subcls
        yield from _iter_cv_subclasses(subcls)


def _humanize_enum_name(name: str) -> str:
    return name.replace('_', ' ').title()


def build_cv_label_map() -> dict[str, str]:
    labels: dict[str, str] = {}
    for enum_cls in _iter_cv_subclasses(CvEnum):
        for member in enum_cls:
            labels.setdefault(str(member), _humanize_enum_name(member.name))
    return labels


CV_LABELS = build_cv_label_map()


def format_cv_term(accession: str | None) -> str | None:
    if accession is None:
        return None
    label = CV_LABELS.get(accession, accession)
    return f'{accession}:{label}'
