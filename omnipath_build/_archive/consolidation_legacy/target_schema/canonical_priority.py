from __future__ import annotations

from pypath.internals.cv_terms import IdentifierNamespaceCv

SAFE_MERGE_IDENTIFIER_TYPES: tuple[str, ...] = (
    str(IdentifierNamespaceCv.UNIPROT.value),
    str(IdentifierNamespaceCv.UNIPROT_TREMBL.value),
    str(IdentifierNamespaceCv.UNIPARC.value),
    str(IdentifierNamespaceCv.REFSEQ_PROTEIN.value),
    str(IdentifierNamespaceCv.ENTREZ.value),
    str(IdentifierNamespaceCv.HGNC.value),
    str(IdentifierNamespaceCv.ENSEMBL.value),
    str(IdentifierNamespaceCv.REFSEQ.value),
    str(IdentifierNamespaceCv.STANDARD_INCHI.value),
    str(IdentifierNamespaceCv.STANDARD_INCHI_KEY.value),
    str(IdentifierNamespaceCv.COMPLEXPORTAL.value),
    str(IdentifierNamespaceCv.REACTOME_STABLE_ID.value),
    str(IdentifierNamespaceCv.REACTOME_ID.value),
    str(IdentifierNamespaceCv.SIGNOR.value),
    str(IdentifierNamespaceCv.INTACT.value),
)

SAFE_MERGE_PRIORITY: dict[str, int] = {
    type_id: rank for rank, type_id in enumerate(SAFE_MERGE_IDENTIFIER_TYPES)
}

DEFAULT_PRIORITY_RANK = 1_000_000


def canonical_priority_rank(type_id: str | None) -> int:
    if type_id is None:
        return DEFAULT_PRIORITY_RANK
    return SAFE_MERGE_PRIORITY.get(type_id, DEFAULT_PRIORITY_RANK)


def choose_canonical_identifier(
    identifiers: list[tuple[str | None, str | None]],
) -> tuple[str, str] | None:
    candidates: list[tuple[int, str, str]] = []

    for type_id, identifier in identifiers:
        if type_id is None or identifier is None:
            continue
        candidates.append((canonical_priority_rank(type_id), type_id, identifier))

    if not candidates:
        return None

    _, type_id, identifier = min(candidates)
    return type_id, identifier
