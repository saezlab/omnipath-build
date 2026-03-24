# ChEBI integration plan

## Summary

This document proposes how to integrate **ChEBI** into the build in a way that matches the newer pathway/ontology design already used for Reactome and WikiPathways:

- **small molecules / structures** are emitted as normal silver-layer entities
- **ChEBI ontology terms and hierarchy** are exported separately as an OBO artifact
- **small molecule entities** carry attached ChEBI parent term accessions as annotations
- the ontology service resolves ChEBI labels, definitions, synonyms, and ancestry

Primary ontology source:

- `https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.obo.gz`

---

## Design goal

We want to separate two different things that ChEBI contains:

1. **chemical entities we want to search/use as molecules**
   - names
   - ChEBI accession
   - formula / mass / optional structural descriptors
   - synonyms
   - database cross-references

2. **ontology content we want to use for semantic expansion/filtering**
   - ontology definitions
   - synonyms
   - parent-child hierarchy
   - relationship graph
   - ontology metadata

The key principle should be:

- **ChEBI small molecules are not emitted as ontology-only records**
- **ChEBI ontology is not modeled as silver entity parquet**
- instead:
  - silver entities represent the actual molecules
  - OBO represents the ontology
  - entities reference ontology parents using `CV_TERM_ACCESSION`

This is the same architectural direction now used for Reactome pathways and WikiPathways pathways.

---

## Desired final outputs

### Silver entity outputs

A ChEBI input module should emit at least one entity dataset, likely:

- `molecules`

Potential optional future datasets:

- `structures`
- `cross_references`
- `roles` / `applications` only if they are modeled as entity-level annotations and prove useful

### Silver ontology outputs

A ChEBI ontology dataset should emit:

- `chebi.obo`

### Entity annotations

Each emitted ChEBI molecule entity should carry:

- its own direct ChEBI accession as identifier
- one or more ancestor / parent ChEBI term accessions as annotations

Recommended annotation form:

- `IdentifierNamespaceCv.CV_TERM_ACCESSION`

That way downstream systems can:

- filter a molecule by direct ChEBI ID
- expand to broader classes using ontology ancestry
- resolve labels/definitions from the ontology service

---

## Proposed module structure

Create a new `inputs_v2` module, e.g.:

- `pypath/pypath/inputs_v2/chebi.py`

This module should own both:

- silver entity extraction for molecules
- ontology export for ChEBI terms

### Proposed datasets

- `molecules=Dataset(...)`
- `ontology=OntologyDataset(...)`

This mirrors the current source-owned pattern:

- `reactome`: mechanistic entities + pathway ontology
- `wikipathways`: interaction entities + pathway ontology
- `chebi`: molecule entities + chemical ontology

---

## Data model proposal

## 1. Molecule entities

Each ChEBI molecule entity should be a normal silver `Entity`.

### Recommended entity type

Likely one of:

- `EntityTypeCv.SMALL_MOLECULE`
- or `EntityTypeCv.LIPID` / subtype only when clearly warranted later

For the initial implementation, defaulting to:

- `EntityTypeCv.SMALL_MOLECULE`

is the safest choice.

### Recommended identifiers

At minimum:

- `IdentifierNamespaceCv.CHEBI`
- `IdentifierNamespaceCv.NAME`
- `IdentifierNamespaceCv.SYNONYM`

Possible additional identifiers if present in xrefs:

- `IdentifierNamespaceCv.KEGG_COMPOUND`
- `IdentifierNamespaceCv.PUBCHEM_COMPOUND`
- `IdentifierNamespaceCv.HMDB`
- `IdentifierNamespaceCv.LIPIDMAPS`

### Recommended annotations

At minimum where available:

- `MoleculeAnnotationsCv.DESCRIPTION` from term definition
- `IdentifierNamespaceCv.CV_TERM_ACCESSION` for parent / ancestor ChEBI terms
- optional formula / mass if we have suitable CV terms already

### Important modeling choice

The entity should represent the **chemical record as a searchable molecule**, not the ontology term document.

So for a ChEBI accession like `CHEBI:15377`:

- as an entity, it is a small molecule record
- as ontology, it is a term in `chebi.obo`

The same accession exists in both layers, but for different purposes.

---

## 2. Parent-term annotations on molecules

This is the main semantic bridge.

For each molecule entity, compute a set of broader ChEBI classes and attach them as:

- `IdentifierNamespaceCv.CV_TERM_ACCESSION`

Examples:

- molecule: `CHEBI:15377` water
- attached ontology annotations might include broad classes such as:
  - `CHEBI:24431` chemical entity
  - plus other relevant ancestor classes depending on the graph

### Recommendation: include ancestors, not just direct parents

For downstream filtering and search, ancestor propagation is more useful than only direct parents.

That means if a term has the path:

- child -> parent -> grandparent

then the molecule entity should carry:

- direct parent accession
- broader ancestor accession(s)

This matches the Reactome annotation strategy already adopted.

### Recommendation: configurable relation filter

ChEBI has multiple relationship types. We should decide which ones contribute to semantic parent annotations.

Suggested initial rule:

- propagate along `is_a`
- optionally include selected structural relations later if needed

Avoid mixing in every relation initially.

---

## 3. Ontology export

The ontology dataset should emit a standalone OBO artifact:

- `chebi.obo`

This should come directly from the ChEBI OBO source with minimal transformation.

### What the ontology output should include

For each ontology term:

- `id`
- `name`
- `def`
- `synonym`
- `xref`
- `is_a`
- selected `relationship:` lines
- `is_obsolete` when present

### Important scoping choice

We should export the ontology terms, but **not treat the ontology export itself as the silver entity stream**.

That means:

- yes: export `chebi.obo`
- no: emit ontology term parquet as the main content for ChEBI

This is what is meant here by:

- extract the ChEBI ontology separately
- do not model the ontology itself as the small molecule entity dataset

---

## Source considerations

## Source 1: ChEBI OBO

URL:

- `https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.obo.gz`

This is likely sufficient to bootstrap both:

- ontology export
- term metadata for molecules
- ancestry propagation for parent-term annotations

### Advantage

Single-source, ontology-native, no need to reconcile a second export first.

### Tradeoff

Some structure-rich fields may be limited compared with full database dumps or SDF exports.

---

## Open question: where do structures come from?

The user goal mentioned:

- extracting the small molecules / structures
n
We need to decide whether the OBO alone is enough for the first pass.

### Option A: derive first-pass molecule entities from OBO only

Emit one entity per ChEBI term that appears to be a usable molecule term.

#### Pros

- simplest implementation
- one source only
- ontology and entity metadata stay aligned

#### Cons
n- OBO is ontology-centric, not structure-centric
- structural fields may be sparse or inconsistent
- may include many non-molecule conceptual classes unless filtered carefully

### Option B: OBO for ontology + a second ChEBI chemical export for entity details

Use:

- OBO for hierarchy / ontology
- another ChEBI export for structures and molecule-level details

#### Pros

- cleaner semantic split
- richer entity records
- easier to separate “molecule records” from “classes/roles/applications”

#### Cons

- more implementation work
- requires accession reconciliation

### Recommendation

Start with a staged approach:

#### Phase 1

Use **OBO-only** to prove the architecture and parent-term annotation flow.

#### Phase 2

If needed, upgrade the molecule dataset to a richer ChEBI source for structures and descriptors.

This keeps the architecture stable while deferring source-complexity decisions.

---

## Filtering strategy for emitted molecule entities

If we use OBO for initial molecule extraction, we should **not** emit every ChEBI term as a small molecule entity.

ChEBI contains:

- molecular entities
- broad classes
- roles
- applications
- ontology-organizing concepts

### Initial filtering options

#### Option 1: emit all non-obsolete terms as molecules

Not recommended.

This would overproduce conceptual terms that are not useful as molecule entities.

#### Option 2: emit only terms descending from selected chemical roots

Recommended.

For example, keep terms descending from broad chemical-entity roots while excluding terms that are primarily:

- biological roles
- applications
- non-entity conceptual branches

#### Option 3: emit only leaf-like or structure-backed terms

Potentially attractive, but harder to define robustly from OBO alone.

### Recommendation

For first implementation:

- emit terms from the **chemical entity branch**
- exclude obvious role/application branches
- exclude obsolete terms

This filtering logic should be explicit and documented in the parser.

---

## Proposed parser behavior

Create a new parser module, likely:

- `pypath/pypath/inputs_v2/parsers/chebi.py`

### Raw outputs

Suggested parser outputs:

- `data_type='molecules'`
- `data_type='ontology_terms'`

### `ontology_terms`

Should produce normalized records like:

```python
{
    'id': 'CHEBI:15377',
    'name': 'water',
    'definition': '...',
    'synonyms': '...',
    'comments': '...',
    'xrefs': 'PubChem:962;... ',
    'is_a': 'CHEBI:24431;...',
    'is_obsolete': 'false',
}
```

### `molecules`

Should produce records like:

```python
{
    'chebi_id': 'CHEBI:15377',
    'name': 'water',
    'synonyms': '...',
    'definition': '...',
    'xrefs': '...',
    'ancestor_terms': 'CHEBI:24431;...',
}
```

The parser should build:

- term metadata index
- `is_a` parent map
- ancestor closure for propagated annotations

---

## Proposed `inputs_v2` module shape

In `pypath/pypath/inputs_v2/chebi.py`:

### Resource config

Use a new resource CV term if needed, e.g.:

- `ResourceCv.CHEBI`

If it does not exist yet, add it.

### Datasets

```python
resource = Resource(
    config,
    molecules=Dataset(...),
    ontology=OntologyDataset(...),
)
```

### `molecules` dataset

Maps raw ChEBI molecule rows into small-molecule silver entities.

### `ontology` dataset

Maps normalized ontology rows into `OntologyTerm` records and serializes to:

- `chebi.obo`

---

## Ontology relationships to preserve

### Initial recommendation

Preserve in OBO export:

- `is_a`
- `is_obsolete`
- xrefs
- definitions
- synonyms

### Optional later

Evaluate selected non-`is_a` ChEBI relations for retention, such as:

- `has_role`
- `is_conjugate_acid_of`
- `is_conjugate_base_of`
- `is_tautomer_of`

But these should probably **not** drive parent annotation propagation initially.

The first implementation should stay conservative:

- use `is_a` for ancestor annotation propagation
- preserve richer relations in ontology export only if needed

---

## Downstream usage model

### Silver entities

ChEBI entities can be used in:

- search
- molecule-centric browsing
- cross-resource joining
- annotation expansion via attached parent accessions

### Ontology service

The ChEBI OBO can be loaded into the ontology service for:

- label lookup
- definitions
- synonym resolution
- ancestry / descendant traversal
- semantic expansion of filters

This avoids duplicating full ontology detail into every entity record.

---

## Implementation phases

## Phase 1: architecture fit

1. add `ResourceCv.CHEBI` if missing
2. add `inputs_v2/parsers/chebi.py`
3. add `inputs_v2/chebi.py`
4. emit:
   - `molecules.parquet`
   - `chebi.obo`
5. attach propagated ChEBI ancestor accessions to molecule entities

### Acceptance criteria

- ChEBI source appears in silver discovery
- `chebi.obo` appears in combined/output artifacts
- molecule entities have direct ChEBI IDs and parent-term annotations

## Phase 2: improve molecule quality

1. refine filtering of ontology terms emitted as molecule entities
2. enrich entity identifiers and cross-references
3. add better structural metadata if another ChEBI source is needed

## Phase 3: ontology-service integration

1. register `chebi.obo` in ontology service config
2. verify label/definition/ancestry resolution
3. verify molecule search/filter behavior using propagated ChEBI parent terms

---

## Key open questions

1. **Is OBO-only enough for the first-pass molecule dataset?**
   - likely yes for architecture
   - maybe no for rich structural metadata

2. **Which ChEBI branches should be emitted as molecule entities?**
   - chemical entity branches yes
   - roles/applications probably no

3. **How many ancestor levels should be attached to molecule entities?**
   - recommendation: full transitive closure on `is_a`

4. **Should selected non-`is_a` relationships also be propagated as annotations?**
   - recommendation: no, not initially

5. **Do we need a separate structure-rich ChEBI source later?**
   - likely yes if we want InChI/SMILES/formula/mass quality

---

## Recommended first implementation

The most pragmatic first version is:

- parse `chebi.obo.gz`
- export it as `chebi.obo`
- emit a filtered molecule entity dataset from the same source
- attach transitive `is_a` ancestor ChEBI accessions as `CV_TERM_ACCESSION`
- keep ontology and entities in the same `inputs_v2` module

This gives us a design consistent with the current ontology refactor work while leaving room to add richer structure extraction later.
