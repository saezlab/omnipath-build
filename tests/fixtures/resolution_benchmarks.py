"""Resolution benchmark fixtures (spec-002 T006).

Pure, importable data — **no database dependency at import time**. These
fixtures pin the expected outcome of gene-anchored entity resolution and the
chemical-naming cascade, and are consumed by the US7/US1/US2/US8 tests
(``tests/test_resolution_benchmarks.py`` and friends).

Two flavours of benchmark are distinguished so consuming tests can degrade
gracefully on a capped ``MAX_RECORDS`` build:

* **Core invariants** — must hold on *any* build (the EGFR human benchmark, the
  Entrez anchoring / one-gene-per-organism rule, alanine not orphaned). A test
  failure here is a real regression.
* **Present-if-in-build** — orthologs / extra cases that a capped build may omit.
  Consuming tests must ``skip`` (not fail) when the entity is absent.

All values below were validated read-only against the **dev4** build DB
(``omnipath-build-postgres-1`` at localhost:55432) on 2026-06-08:

* human **EGFR** = Entrez ``1956`` / taxon ``9606`` / label ``EGFR`` /
  representative UniProt ``P00533`` (reviewed). Reachable from genesymbol EGFR,
  Ensembl ENSG00000146648, UniProt P00533 and Entrez 1956 — all four collapse
  to the *same single* gene entity.
* mouse **Egfr** = Entrez ``13649`` / taxon ``10090`` / label ``Egfr`` /
  representative UniProt ``Q01279`` (reviewed) — PRESENT in the capped build.
* rat **Egfr** = Entrez ``24329`` / taxon ``10116`` — ABSENT in the capped
  build (present-if-in-build).
* **alanine** chemicals (``Alanine``, ``L-Alanine``, ``D-Alanine``,
  ``beta-Alanine``) present with ``label_rule = 'chemical_name'`` — human
  readable names, never an InChIKey or a 32-hex hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# CV / id-type names (as stored in vocab_entity_type / vocab_identifier_type)
# ---------------------------------------------------------------------------

GENE_ENTITY_TYPE = 'Gene:MI:0250'
CHEMICAL_ENTITY_TYPE = 'Chemical:OM:0037'

#: identifier-type names keyed by the short alias used in benchmark inputs.
ID_TYPE_NAMES = {
    'genesymbol': 'Gene Name Primary:OM:0200',
    'entrez': 'Entrez:MI:0477',
    'ensembl': 'Ensembl:MI:0476',
    'uniprot': 'Uniprot:MI:1097',
}

#: label_rule values that count as a real, human-readable chemical name.
CHEMICAL_NAME_RULES = ('chemical_name', 'chemical_iupac_name', 'goslin_lipid')

#: the universal opaque last-resort label rule no chemical may be left on.
CHEMICAL_FALLBACK_RULE = 'identifier_fallback'

# Regexes (as plain strings, for both Python and Postgres ``~``) that an
# acceptable chemical label must NOT match: an InChIKey or a 32-hex hash.
INCHIKEY_REGEX = r'^[A-Z]{14}-[A-Z]{10}-[A-Z]$'
HEX32_REGEX = r'^[0-9a-f]{32}$'


# ---------------------------------------------------------------------------
# Gene benchmarks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneBenchmark:
    """One gene-anchored resolution benchmark case.

    ``inputs`` maps an id-type alias (see :data:`ID_TYPE_NAMES`) to the value
    that must resolve to *this* gene entity. When ``core`` is False the case is
    present-if-in-build: consuming tests skip it when the entity is absent.
    """

    description: str
    inputs: dict[str, str]
    expected_entrez: str
    expected_taxonomy: int
    expected_label: str
    expected_uniprot: str
    expected_uniprot_reviewed: bool = True
    core: bool = True


#: Human EGFR — the canonical core benchmark.
EGFR_HUMAN = GeneBenchmark(
    description='human EGFR — every input id type collapses to one gene entity',
    inputs={
        'genesymbol': 'EGFR',
        'entrez': '1956',
        'ensembl': 'ENSG00000146648',
        'uniprot': 'P00533',
    },
    expected_entrez='1956',
    expected_taxonomy=9606,
    expected_label='EGFR',
    expected_uniprot='P00533',
    expected_uniprot_reviewed=True,
    core=True,
)

#: Mouse Egfr ortholog — present in the capped dev4 build (validated).
EGFR_MOUSE = GeneBenchmark(
    description='mouse Egfr ortholog (gene-anchored, not dropped)',
    inputs={'entrez': '13649'},
    expected_entrez='13649',
    expected_taxonomy=10090,
    expected_label='Egfr',
    expected_uniprot='Q01279',
    expected_uniprot_reviewed=True,
    core=False,
)

#: Rat Egfr ortholog — ABSENT in the capped dev4 build (validated absent);
#: kept present-if-in-build so a fuller build is also covered.
EGFR_RAT = GeneBenchmark(
    description='rat Egfr ortholog (present-if-in-build; absent on dev4 cap)',
    inputs={'entrez': '24329'},
    expected_entrez='24329',
    expected_taxonomy=10116,
    expected_label='Egfr',
    expected_uniprot='Q9QX70',
    expected_uniprot_reviewed=True,
    core=False,
)

#: All gene benchmarks (core + present-if-in-build).
GENE_BENCHMARKS: tuple[GeneBenchmark, ...] = (
    EGFR_HUMAN,
    EGFR_MOUSE,
    EGFR_RAT,
)

#: The subset that must always hold.
CORE_GENE_BENCHMARKS: tuple[GeneBenchmark, ...] = tuple(
    b for b in GENE_BENCHMARKS if b.core
)


# ---------------------------------------------------------------------------
# Chemical benchmarks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChemicalBenchmark:
    """A chemical-naming benchmark (US1 'no orphaned chemicals').

    At least one chemical entity must carry one of ``expected_labels`` produced
    by one of ``expected_label_rules`` — and that label must not look like an
    InChIKey or an opaque hash.
    """

    description: str
    expected_labels: tuple[str, ...]
    expected_entity_type: str = CHEMICAL_ENTITY_TYPE
    expected_label_rules: tuple[str, ...] = CHEMICAL_NAME_RULES
    core: bool = True


#: Alanine — the US1 'no orphaned chemicals' benchmark. Validated on dev4 as
#: ``Alanine`` / ``L-Alanine`` / ``D-Alanine`` / ``beta-Alanine``, all
#: ``label_rule = 'chemical_name'``.
ALANINE = ChemicalBenchmark(
    description='alanine is a named chemical, not an InChIKey/hash (US1)',
    expected_labels=('Alanine', 'L-Alanine', 'D-Alanine', 'beta-Alanine'),
    expected_entity_type=CHEMICAL_ENTITY_TYPE,
    expected_label_rules=('chemical_name', 'chemical_iupac_name'),
    core=True,
)

CHEMICAL_BENCHMARKS: tuple[ChemicalBenchmark, ...] = (ALANINE,)

CORE_CHEMICAL_BENCHMARKS: tuple[ChemicalBenchmark, ...] = tuple(
    b for b in CHEMICAL_BENCHMARKS if b.core
)


# ---------------------------------------------------------------------------
# Helpers (pure, no DB)
# ---------------------------------------------------------------------------


def looks_like_inchikey_or_hash(label: str) -> bool:
    """True if ``label`` is an InChIKey- or 32-hex-hash-shaped raw identifier."""
    import re

    return bool(
        re.match(INCHIKEY_REGEX, label) or re.match(HEX32_REGEX, label)
    )
