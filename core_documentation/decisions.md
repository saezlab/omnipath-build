# Decisions

The choices that shape the system, with the reason for each. When a decision
changes, edit its entry here in the same pull request and note the date.
Older discussions are in the linked source documents.

| # | Decision | Area |
| --- | --- | --- |
| [D1](#d1) | Resolve once, at build time | Architecture |
| [D2](#d2) | The Parquet files are the contract between systems | Architecture |
| [D3](#d3) | Versions are immutable; releases pin them | Versioning |
| [D4](#d4) | NCBI Gene is the shared reference for genes and products | Identity |
| [D5](#d5) | Keep the source's entity type | Identity |
| [D6](#d6) | Product identity lives in the molecular form | Identity |
| [D7](#d7) | No entity per molecular state | Identity |
| [D8](#d8) | Missing means unspecified | Identity |
| [D9](#d9) | Abstain rather than guess | Resolution |
| [D10](#d10) | Grouping is a query-time view | Resolution |
| [D11](#d11) | Evidence is a multiset with paired forms | Schema |
| [D12](#d12) | PostgreSQL reproduces main's layout without re-resolving | PostgreSQL |
| [D13](#d13) | Durable checkpoints, no mandatory verification run | PostgreSQL |
| [D14](#d14) | Raw payloads stay in Parquet | PostgreSQL |
| [D15](#d15) | Conflicting taxa project to empty | Schema |
| [D16](#d16) | Sample builds stay out of releases | Versioning |
| [D17](#d17) | MetSigDB uses published chemical entities | Subsets |
| [D18](#d18) | COSMOS keeps gene catalysts and separate source events | Subsets |

---

<a id="d1"></a>
## D1. Resolve once, at build time

**Decision.** Entity resolution, including reference preparation, happens inside
omnipath-build before Parquet publication. The API, PostgreSQL and subsets
consume resolved identities and never resolve again.

**Why.** One place owns identity, so every consumer sees the same entities.
Browsing does not pay for resolution per request.

**Implies.** A change to resolution rules requires a new resource version.

<a id="d2"></a>
## D2. The Parquet files are the contract between systems

**Decision.** `entities`, `relations` and `evidence_payloads` Parquets are the only
interface between builder, serving and PostgreSQL. There is no shared database
or cache.

**Why.** Systems can live on different servers and update on their own schedules.

<a id="d3"></a>
## D3. Versions are immutable; releases pin them

**Decision.** Resource versions are explicit numbers and are never overwritten.
A release pins one version per resource, plus the SHA-256 of its build manifest.
The API defaults to Latest; PostgreSQL loads one explicit release monthly.

**Why.** Resources can update at any time while analyses stay reproducible.

**Source.** [`VERSIONING.md`](../VERSIONING.md)

<a id="d4"></a>
## D4. NCBI Gene is the shared reference for genes and products

**Decision.** Wherever a supported mapping exists, `reference_entity_key` is the
NCBI Gene ID (`entrez:<id>`), for gene, protein and RNA records alike.

**Why.** Users browse by gene. A single reference collects knowledge about a
gene's products without merging them.

**Date.** 4 October 2026 · **Source.** [Molecular forms decision record](../docs/entity-resolution-and-molecular-forms.md)

<a id="d5"></a>
## D5. Keep the source's entity type

**Decision.** `entity_type` stays what the source reported (protein, gene, RNA…).
Resolving a protein to a gene reference does not change its type to `gene`.
We rejected moving the source type into a separate `participant_type` field.

**Why.** Protein-level and gene-level knowledge are different claims, and users
need to see that distinction.

**Implies.** A protein row and a gene row for the same gene are separate entities
with the same `reference_entity_key`.

**Date.** 5 October 2026 (clarification) · **Source.** [Decision record §2](../docs/entity-resolution-and-molecular-forms.md)

<a id="d6"></a>
## D6. Product identity lives in the molecular form

**Decision.** Protein evidence resolves separately to primary UniProt; that
product goes into the occurrence's `molecular_form`. Gene identifiers never
select a protein, and a representative protein is never used as the participant.

**Why.** Projecting gene evidence onto every protein of a gene invents claims
that no source made.

**Date.** 4 October 2026 · **Source.** [Decision record §3](../docs/entity-resolution-and-molecular-forms.md)

<a id="d7"></a>
## D7. No entity per molecular state

**Decision.** We keep reusable protein and transcript entities, but we do not mint
an entity for each combination of isoform, modification and variant.
Modifications and variants are individually stored on their occurrence.

**Why.** The number of combinations is unbounded. Combining evidence for X+Y with
X+Z must not invent an X+Y+Z form. A state model can be reconsidered if pathway
state transitions need it.

**Date.** 4 October 2026

<a id="d8"></a>
## D8. Missing means unspecified

**Decision.** An absent isoform, modification or variant means the source did not
say. It never means canonical isoform, unmodified or wild type. Older evidence
without molecular context stays unknown.

**Why.** Treating silence as a claim would create false negatives in form queries.

<a id="d9"></a>
## D9. Abstain rather than guess

**Decision.** Conflicting or ambiguous evidence leaves an observation unresolved.
Lookup keys with more than 10 candidates are removed whole, never truncated, and
archived in `ambiguous.parquet`.

**Why.** A false merge corrupts every downstream result silently; an unresolved
entity is visible and fixable. Judge resolution by correctness first, coverage
second.

**Source.** [`docs/reference-resolver.md`](../docs/reference-resolver.md)

<a id="d10"></a>
## D10. Grouping is a query-time view

**Decision.** Gene-reference grouping (biological entities) and InChIKey
connectivity grouping (chemicals) happen at query time. They never change stored
keys or relation endpoints. The explorer has a single Group toggle.

**Why.** Different users need grouped and exact views of the same data.

<a id="d11"></a>
## D11. Evidence is a multiset with paired forms

**Decision.** Each source occurrence stays a separate evidence item, even when
identical. Subject and object molecular forms stay together on their occurrence
and flip together when a symmetric relation is reordered.

**Why.** Counting and filtering need the real occurrences. A union of forms
across a relation would lose which participants were observed together.

<a id="d12"></a>
## D12. PostgreSQL reproduces main's layout without re-resolving

**Decision.** The loader projects published Parquets into main's existing tables
and indexes, adding context tables for references and molecular forms. Resolution
status is `published`; old resolver diagnostics are not fabricated.

**Why.** Main's queries and products keep working, and nothing claims more than the
Parquets actually contain.

**Source.** [Column map](../docs/postgres-parquet-column-map.md)

<a id="d13"></a>
## D13. Durable checkpoints, no mandatory verification run

**Decision.** The validated base commits before derivations. Each derivation and
product commits on its own and is skipped on retry. Fixture and parity tests are
developer checks; production runs have no rebuild-and-compare phase.

**Why.** Full loads are long. Resuming must not redo finished work.

<a id="d14"></a>
## D14. Raw payloads stay in Parquet

**Decision.** Source payload JSON and nested record JSON are not loaded into
PostgreSQL. Optional `parquet_*` inspection tables exist but have no scientific
reader.

**Why.** It keeps the database at the size of its scientific content.

<a id="d15"></a>
## D15. Conflicting taxa project to empty

**Decision.** The scalar `taxon` of a relation or entity is filled only when the
assertions agree. Conflicts give an empty scalar; each occurrence keeps its own.

**Why.** Picking one species arbitrarily would hide a real conflict.

<a id="d16"></a>
## D16. Sample builds stay out of releases

**Decision.** Builds with a record cap or dataset subset are refused by the
publisher and the PostgreSQL loader, unless the release sets
`"partial_resources": true`.

**Why.** A capped test build must never end up in a monthly snapshot by accident.

**Date.** 5 October 2026 · **Source.** [Branch review B2](../docs/reviews/parquet-migration-review-20261005.md)

<a id="d17"></a>
## D17. MetSigDB uses published chemical entities

**Decision.** MetSigDB includes published chemical entities, including native
fallback identities.

**Why.** The Parquets do not carry the legacy matched flag, and we chose not to
re-resolve to recover it.

<a id="d18"></a>
## D18. COSMOS keeps gene catalysts and separate source events

**Decision.** GeneID catalysts keep their GeneID label and `entrez` namespace; no
protein is selected from UniProt aliases. Reactions from different sources stay
separate events. Unknown direction does not create reverse edges, and
gene–protein–reaction rules do not become catalysis.

**Why.** The same reasons as [D6](#d6) and [D9](#d9): no invented participants.

**Source.** [`packages/subsets/README.md`](../packages/subsets/README.md)
