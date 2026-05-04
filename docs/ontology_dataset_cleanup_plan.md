# Ontology Dataset Cleanup Plan

## Problem statement

Ontology terms are now supposed to be normal entities (`entity_type = OM:0012:Cv Term`, canonical identifier type `OM:0204:Cv Term Accession`). However the current pipeline still has a split/legacy path:

1. Some ontology downloads are represented as `ArtifactDataset` and only write an `.obo` artifact in silver.
2. `build_ontology_terms.py` scans silver `.obo` files and writes a temporary `data/combined/ontology_term.parquet`.
3. `combine.py` reads that temporary parquet, converts terms into normal entities, then deletes `ontology_term.parquet`.
4. Per-source gold outputs therefore often contain placeholder ontology entities without labels/definitions/synonyms; the enrichment only happens late in combine.

This is why `data/gold/hpo/entities/entity.parquet` has bare `HP:*` placeholders, while `data/combined/entity.parquet` is enriched from the temporary ontology build.

The target architecture should make ontology term entities first-class outputs of ontology datasets, not a hidden combined-stage side effect.

## Current inventory

### Inputs using `ArtifactDataset` for OBO-like ontology artifacts

| Module | Current dataset | Artifact | Notes |
|---|---|---|---|
| `pypath/inputs_v2/go.py` | `ontology=ArtifactDataset(...)` | `go.obo` | Standard OBO download. Should become generic OBO-backed ontology dataset. |
| `pypath/inputs_v2/hpo.py` | `ontology=ArtifactDataset(...)` | `hp.obo` | Standard OBO download plus separate gene-phenotype annotation dataset. Should produce HP term entities directly. |
| `pypath/inputs_v2/psi_mi.py` | `ontology=ArtifactDataset(...)` | `psi_mi.obo` | Standard-ish OBO with small date-line cleanup. Should preserve cleanup hook or raw artifact decision. |
| `pypath/inputs_v2/omnipath_ontology.py` | `ontology=ArtifactDataset(...)` | generated `omnipath_mi.obo` | Generated from internal CV enums. Should produce `OntologyTerm`s or entities directly from the same internal terms. |

### Inputs already using `OntologyDataset`

| Module | Current dataset | Notes |
|---|---|---|
| `pypath/inputs_v2/chebi.py` | `ontology=OntologyDataset(...)` | Produces normalized ChEBI OBO artifact from parsed `OntologyTerm`s, but not ontology term entities. Also has separate small-molecule entities. Need decide whether ChEBI CV term entities are in addition to molecule entities. |
| `pypath/inputs_v2/reactome.py` | `pathway_ontology=OntologyDataset(...)` | Produces pathway ontology artifact from BioPAX-derived terms, but not entities. |
| `pypath/inputs_v2/wikipathways.py` | `pathway_ontology=OntologyDataset(...)` | Produces pathway ontology artifact from RDF-derived terms, but not entities. |
| `pypath/inputs_v2/macdb.py` | `trait=OntologyDataset(...)` | Produces MACdb trait ontology artifact, but not entities. |

### Pipeline pieces involved

| File | Current responsibility | Cleanup needed |
|---|---|---|
| `pypath/inputs_v2/base.py` | Defines `Dataset`, `OntologyDataset`, `ArtifactDataset` | Extend/replace ontology abstraction so it can emit both artifact and term entities. |
| `omnipath_build/silver/build.py` | Discovers datasets; `OntologyDataset` currently has `output_kind='ontology'` and only writes an artifact via `_process_ontology_output`. `ArtifactDataset` writes only artifacts. | Make ontology datasets produce dual outputs: artifact + silver entity tables. |
| `omnipath_build/silver/tables.py` | Writes normal `Entity` records into canonical silver tables. | Reuse for ontology term entities; likely no schema change needed. |
| `omnipath_build/gold/build_entities.py` | Extracts/canonicalizes normal entities from silver tables and writes per-source gold `entity.parquet`. Also writes legacy `ontology_term.parquet` from ontology annotations. | After ontology datasets emit normal entities, gold source entities will be enriched directly. Legacy `ontology_term.parquet` should be removed or limited to migration diagnostics. |
| `omnipath_build/gold/build_ontology_terms.py` | Scans silver `.obo` artifacts and creates combined `ontology_term.parquet`. | Deprecate/remove once ontology entities are produced in silver. Parser logic can be moved/reused in pypath as generic OBO parser. |
| `omnipath_build/gold/combine.py` | Reads per-source entities plus temporary ontology terms, converts ontology terms to entities, deletes `ontology_term.parquet`. | Remove `_build_ontology_terms()` and `_ontology_entity_rows()` path. Combine should only merge normal entity parquet outputs. |
| `omnipath_build/pipeline/dag.py` | Has explicit `ontology_terms` task between silver and combine. | Remove this task once ontology entities are generated during source builds. |
| `omnipath_build/gold/build_resources.py` | Counts `entities/ontology_term.parquet`. | Change ontology term counts to count CV-term entities in `entities/entity.parquet`, possibly by source/dataset. |
| `omnipath_build/postgres/*` | Loads combined `entity` and attributes; no ontology term table. | Mostly OK. Consider adding a view for ontology labels/definitions/synonyms. |

## Proposed target design

### 1. Make ontology datasets dual-output

`OntologyDataset` should represent a structured ontology source that can produce:

1. An ontology artifact, usually `.obo`, needed by the API service and external consumers.
2. Normal silver `Entity` records for each ontology term.

Conceptually:

```text
OntologyDataset
  raw/download
    -> records / OntologyTerm objects
       -> artifact writer: .obo
       -> entity writer: CV_TERM entities in silver tables
```

For standard OBO downloads, the artifact should ideally preserve the source content byte/text as closely as possible. For generated ontologies (Reactome/WikiPathways/MACdb/OmniPath), the artifact can continue to be rendered from `OntologyTerm` records.

### 2. Add a generic OBO parser in `pypath.inputs_v2`

Move/adapt the existing parser logic from `omnipath_build/gold/build_ontology_terms.py` into a reusable parser, e.g.:

```text
pypath/pypath/inputs_v2/parsers/obo.py
```

Minimum parsed fields:

- `id`
- `name`
- `def`
- `synonym`
- `alt_id`
- `is_obsolete`
- `namespace`
- `xref`
- `is_a`
- `relationship`
- `comment`

The parser should yield records compatible with an `OntologyTerm` mapper. For truly standard OBO sources like GO/HPO/PSI-MI, there should be no source-specific term mapping beyond dataset config.

### 3. Convert `OntologyTerm` to normal `Entity`

Add a generic converter, either in `pypath.inputs_v2.base` or a helper module:

```python
def ontology_term_to_entity(term: OntologyTerm) -> Entity:
    ...
```

Mapping:

| OBO / `OntologyTerm` field | Entity representation |
|---|---|
| `id` | Identifier `IdentifierNamespaceCv.CV_TERM_ACCESSION` |
| `name` | Identifier `IdentifierNamespaceCv.NAME`; annotation/attribute name if desired |
| `definition` | Annotation `OntologyAnnotationCv.DEFINITION` |
| `synonyms` | Identifier `IdentifierNamespaceCv.SYNONYM` |
| `alt_id` | Additional `IdentifierNamespaceCv.CV_TERM_ACCESSION` identifiers, if `OntologyTerm` schema is extended |

Important: `Entity.type = EntityTypeCv.CV_TERM`.

### 4. Extend `OntologyTerm` schema if needed

Current `pypath.internals.ontology_schema.OntologyTerm` lacks `alt_ids` and `namespace`. To avoid losing standard OBO data, add fields such as:

```python
alt_ids: list[str] | None = None
namespace: str | None = None
```

Potentially also store relationship modifiers later, but not needed for first cleanup.

### 5. Update silver processing

Current behavior in `omnipath_build/silver/build.py`:

- `OntologyDataset` -> `output_kind='ontology'`
- `_process_ontology_output()` materializes terms and writes only `.obo`
- no `SilverTableWriter` is created for ontology datasets

Change to one of these designs:

#### Option A: `OntologyDataset` remains one dataset with dual processing

- Discovery marks it as `output_kind='ontology'`.
- `process_resource_function()` for ontology datasets:
  1. materializes/streams `OntologyTerm`s
  2. writes `.obo`
  3. converts terms to `Entity`s
  4. writes those entities to silver tables using `SilverTableWriter`, dataset name = ontology dataset name

This is less invasive for inputs modules.

#### Option B: `OntologyDataset.datasets()` exposes logical suboutputs

- `ontology.artifact`
- `ontology.entities`

This is cleaner conceptually but requires more discovery/path/reporting changes.

Recommended first implementation: **Option A**, because it minimizes churn.

### 6. Convert current ontology inputs

#### GO

Replace `ArtifactDataset` with generic OBO `OntologyDataset`:

```python
ontology=OboOntologyDataset(
    download=Download(... go.obo ...),
    document=OntologyDocument(ontology='go', default_namespace='gene_ontology'),
    file_stem='go',
)
```

#### HPO

Replace `ArtifactDataset` with generic OBO `OntologyDataset` for `hp.obo`. Keep `annotations=Dataset(...)` for genes-to-phenotype.

Expected result: `data/gold/hpo/entities/entity.parquet` contains enriched HP CV-term entities with names/definitions/synonyms where available in source OBO.

#### PSI-MI

Replace `ArtifactDataset` with generic OBO `OntologyDataset`, preserving the current date-line cleanup either as:

- a `preprocess_text` hook before OBO parsing/rendering, or
- an artifact renderer hook while parser consumes the cleaned text.

#### OmniPath ontology

Instead of only rendering text from CV enums, produce `OntologyTerm` objects from `_extract_om_terms()` and use an `OntologyDataset` with no download. The same terms can render the artifact and emit entities.

#### ChEBI

Already has `OntologyDataset`. It should automatically gain entity output when silver processing changes.

Decision needed: keep both ChEBI molecule entities and ChEBI CV-term entities. They have different canonical identifier types (`MI:0474:Chebi` vs `OM:0204:Cv Term Accession`), so combine will keep them separate. This is useful for annotation ontology joins, but API UX may want cross-links between the molecule and ontology-term entity.

#### Reactome / WikiPathways / MACdb

Already have `OntologyDataset`. They should automatically gain CV-term entity output.

### 7. Simplify gold/combine pipeline

After silver emits ontology term entities:

1. Remove/deprecate `build_ontology_terms.py` from the main DAG.
2. Remove `ontology_terms` task from `pipeline/dag.py`.
3. Remove temporary combined `ontology_term.parquet` dependency from `combine.py`.
4. Remove `_ontology_entity_rows()` from `combine.py`.
5. Stop writing per-source `entities/ontology_term.parquet` in `build_entities.py` once no longer needed.
6. Ensure `build_relations.py` still resolves annotation objects to ontology term entities through normal `entity_map` lookup.

## Migration strategy

1. Add generic OBO parser and tests.
2. Add `OntologyTerm -> Entity` conversion and tests.
3. Update silver `OntologyDataset` processing to write both artifact and entities.
4. Convert `go.py`, `hpo.py`, `psi_mi.py`, `omnipath_ontology.py` from `ArtifactDataset` to ontology datasets.
5. Rebuild a small subset: `go`, `hpo`, `psi_mi`, `omnipath_ontology`.
6. Verify per-source gold outputs now contain enriched CV-term entities:
   - `HP:0001250` has name and definition in `data/gold/hpo/entities/entity.parquet`.
   - GO terms have names/definitions in `data/gold/go/entities/entity.parquet`.
7. Remove combined-stage ontology term build/read/delete path.
8. Rebuild full combined output.

