# Annotation resource interface brainstorm

Date: 2026-04-05  
Context: split the Resources page into top-level `interaction` vs `annotation` resource types, then design an annotation-specific workspace for selected annotation resources.

## Problem statement

We now have two distinct workflows:

1. **Gene set enrichment / annotation discovery**  
   Given a set of genes/entities, show statistically enriched annotations.

2. **Concept-to-entities expansion**  
   Given one or more annotations / ontology concepts, show all matching entities.  
   From there, users should be able to save the returned entities to a selection and then explore interactions of that set.

The current DuckDB resource workspace is strongly **interaction-centric**:
- it expects `interactions.parquet`
- the refine pane is about direction/sign/interaction type
- the results pane is an interaction table

That is a poor fit for annotation resources like:
- `chebi`
- `uniprot`
- `hpo`
- `omnipath_ontology`
- potentially pathway/complex resources when used primarily as annotation sources

## What the annotation workspace should optimize for

### Primary jobs to be done

- Select annotation resources
- Choose **query mode**:
  - **Entities → Annotations**
  - **Annotations → Entities**
- Search / browse annotation terms
- Inspect term counts and supporting resources
- Save resulting entities to a selection
- Hand off that selection into interaction exploration

### Nice-to-haves

- Ontology tree navigation
- Term grouping by ontology prefix (`GO`, `HP`, `CHEBI`, `OM`, `MI`, `KW`, etc.)
- Background/universe control for enrichment
- Multiple-testing correction controls
- Resource-aware breakdowns
- Export term tables or entity sets

## Current data shape we can build around

From `omnipath_build/gold/convert.py` the relevant gold files are:

- `annotations.parquet`
  - `subject_type`
  - `subject_id`
  - `cv_term`
  - `source`
- `entities.parquet`
  - `entity_id`
  - `entity_type`
  - `entity_attributes`
  - `taxonomy_id`
  - `source`
- `entity_identifiers_resolved.parquet`
- `entity_identifiers_source.parquet`

This suggests a pretty direct local DuckDB workflow:

- **Annotations → Entities**
  - filter `annotations.parquet` by term(s)
  - join to `entities.parquet`
  - join to identifier tables for display/export

- **Entities → Annotations**
  - map input IDs to local entities
  - join entities to annotations
  - aggregate counts
  - compare against a universe for enrichment

## Design options

---

## Option A — Single annotation workspace with two modes

A dedicated annotation workspace parallel to the current resource workspace.

### Modes

1. **Find annotations for entities**
2. **Find entities for annotations**

### Why this is attractive

- Very aligned with the two core use cases
- Reuses a single page / URL / mental model
- Easy to grow into a full annotation analysis workspace
- Natural place to add save-to-selection handoff

### Risks

- The two modes have different result shapes
- UI can get crowded if both modes are shown at once
- Enrichment adds statistical controls that the reverse lookup mode does not need

### MVP fit

Very good, if we make the mode switch explicit and let each mode own its own pane contents.

---

## Option B — Annotation term explorer first, enrichment second

Start with a **term-centric explorer** only:
- search terms
- inspect term metadata and counts
- select terms
- return entities
- save entities to selection

Enrichment is added later as a second feature.

### Why this is attractive

- Simplest path to a usable annotation resource workflow
- Strong support for the "concepts → entities → interactions" story
- Lower implementation risk

### Risks

- Does not directly solve the gene set enrichment ask yet
- Might need redesign later when enrichment is added

### MVP fit

Excellent for a fast first implementation.

---

## Option C — Wizard / stepper workflow

A guided, sequential flow:

1. Select resources
2. Choose analysis type
3. Enter entities or annotation terms
4. Review results
5. Save selection / open interactions

### Why this is attractive

- Very understandable for new users
- Good when workflows are multi-step and branch
- Helps us enforce required inputs cleanly

### Risks

- Slower for expert users
- Harder to support iterative refinement
- Feels less like an exploratory workspace and more like a form

### MVP fit

Okay, but less aligned with the existing OmniPath exploratory UI style.

---

## Option D — Split into two separate products/pages

Two separate pages:

1. `/duckdb/annotations/enrich`
2. `/duckdb/annotations/entities`

### Why this is attractive

- Each page can be highly optimized
- Very clean implementation boundaries
- Lower UI complexity per page

### Risks

- Fragmented discovery
- More routing/state duplication
- Harder to present as one coherent annotation workflow

### MVP fit

Reasonable technically, but probably too fragmented for now.

## Recommendation

### Recommended direction: **Option A with an Option B-shaped MVP**

Concretely:

- Build **one annotation workspace**
- Add a clear top-level mode switch:
  - **Annotations → Entities**
  - **Entities → Annotations**
- For the **first implementation**, prioritize **Annotations → Entities**
- Add **Entities → Annotations / enrichment** immediately after, using the same shell

This gives us:
- a stable home for annotation workflows
- a fast path to the concept-to-entity use case
- room to layer in enrichment without rethinking navigation

## Proposed IA

### Resources page

When selected resources are all / mostly `annotation` resources:

- primary CTA should be:
  - **Open annotation workspace**
- secondary CTAs:
  - download selection
  - copy selected IDs

If users select mixed resource types later, we can decide whether to:
- allow mixed mode, or
- force one workspace type at a time

For now I recommend:
- **annotation workspace requires annotation resources selected**
- interaction workspace remains for interaction resources

## Proposed annotation workspace structure

### Left pane: query + refine

Contains:
- selected resources
- mode switch
- term/entity input controls
- ontology scope / prefix filters
- entity type filters
- taxonomy filters
- source filters
- result actions

### Center pane: results

Mode-dependent:

#### In `Annotations → Entities`
- selected term chips
- summary counts
- matching entities table
- resource/source breakdown
- save entities to selection button

#### In `Entities → Annotations`
- input set summary
- enriched terms table
- counts, ratios, p-value, adjusted p-value
- ontology grouping / tree view
- add significant entities or terms to downstream workflows

### Right pane: details / assistant

Could mirror the existing chat pane structure, but initially:
- selected term details
- ontology definition / parent chain
- selected entity details
- provenance/resource support

A chat pane can remain optional.

## Wireframes

## Wireframe 1 — Annotation resources page CTA

```text
+-----------------------------------------------------------------------------------+
| Resources                                                                         |
| [All resource types] [Interaction] [Annotation*]                                  |
|                                                                                   |
| ... resource cards ...                                                            |
|                                                                                   |
| 4 resources selected                                                              |
| Estimated total size: 320 MB                                                      |
|                                                                                   |
| [Clear] [Copy selected IDs] [Download selection] [Open annotation workspace]      |
+-----------------------------------------------------------------------------------+
```

## Wireframe 2 — Annotation workspace shell

```text
+------------------------------------------------------------------------------------------------------+
| Annotation workspace                                                                                 |
| Resources: [uniprot] [hpo] [chebi] [omnipath_ontology]                                               |
| Mode: (•) Annotations → Entities   ( ) Entities → Annotations                                        |
+------------------------------+---------------------------------------------------+-------------------+
| Query & filters              | Results                                           | Details           |
|------------------------------|---------------------------------------------------|-------------------|
| Search annotation terms      | Selected terms: [GO:0006915] [HP:0001250]         | Term details      |
| [ apoptotic ...         ]    | 1,284 matching entities                           | ---------------- |
|                              | 4 resources contributed                           | GO:0006915        |
| Selected terms               |                                                   | apoptotic process |
| [GO:0006915    x]            | [Save entities to selection] [Export table]       | parents           |
| [HP:0001250    x]            |                                                   | children          |
|                              | Entity table                                      | definition        |
| Ontology prefixes            | ------------------------------------------------  | source support    |
| [x] GO [x] HP [ ] CHEBI      | Entity        Canonical ID   Type   Taxon Source  |                   |
| [ ] KW [ ] OM [ ] MI         | TP53          P04637         protein 9606  UniProt|                   |
|                              | CASP3         P42574         protein 9606  UniProt|                   |
| Entity type                  | ...                                               |                   |
| [x] protein [ ] complex      |                                                   |                   |
| [ ] small molecule           |                                                   |                   |
|                              |                                                   |                   |
| Taxonomy                     |                                                   |                   |
| [x] human [ ] mouse          |                                                   |                   |
|                              |                                                   |                   |
| Source filter                |                                                   |                   |
| [x] uniprot [x] hpo          |                                                   |                   |
+------------------------------+---------------------------------------------------+-------------------+
```

## Wireframe 3 — Enrichment mode

```text
+------------------------------------------------------------------------------------------------------+
| Annotation workspace                                                                                 |
| Resources: [uniprot] [hpo] [reactome? later]                                                         |
| Mode: ( ) Annotations → Entities   (•) Entities → Annotations                                        |
+------------------------------+---------------------------------------------------+-------------------+
| Query & filters              | Results                                           | Details           |
|------------------------------|---------------------------------------------------|-------------------|
| Input entities               | Input set: 137 entities                           | Selected term     |
| [ TP53, EGFR, AKT1, ... ]    | Matched: 126                                      | ---------------- |
|                              | Universe: all loaded entities                     | HP:0001250        |
| Identifier type              |                                                   | seizures          |
| (•) symbol ( ) uniprot       | Significant annotations                           | term definition   |
|                              | ------------------------------------------------  | parent hierarchy  |
| Universe                     | Term         k/K    n/N    p_adj   sources        | matching entities |
| (•) loaded resources         | GO:0006915  ...    ...    1e-8    uniprot        | source breakdown  |
| ( ) uploaded background      | HP:0001250  ...    ...    2e-5    hpo            |                   |
|                              | GO:...                                            |                   |
| Correction                   |                                                   |                   |
| (•) BH-FDR ( ) Bonferroni    | [Export terms] [Save matched entities]            |                   |
|                              |                                                   |                   |
| Min term size                | Volcano / bar chart later                         |                   |
| [ 5 ]                        |                                                   |                   |
+------------------------------+---------------------------------------------------+-------------------+
```

## UX notes

### Term entry

We should support three ways to add annotation terms:

1. **Search by term ID or label**
2. **Browse ontology tree / grouped prefixes**
3. **Paste a list of term IDs**

### Entity entry

We should support:

1. Paste gene/entity identifiers
2. Load current selection as input
3. Later: load entities returned from another workspace step

### Result actions

The most important action is:

- **Save resulting entities to selection**

Then let users navigate to:
- interaction explore page
- interaction DuckDB workspace
- selection page

## Query semantics to define early

These choices affect the UI, so we should decide them explicitly.

### For `Annotations → Entities`

When multiple terms are selected, should matching entities satisfy:
- **ANY** selected term, or
- **ALL** selected terms?

Recommendation:
- default to **ANY**
- provide a toggle for **ALL**

### For `Entities → Annotations`

Need to define:
- what counts as the universe
- whether to collapse duplicate annotations across resources
- how to handle multiple ontology namespaces together

Recommendation:
- default universe = all loaded entities from selected resources
- deduplicate by `(entity_id, cv_term)` before enrichment
- show ontology prefix column and allow filtering by prefix

## Proposed implementation phases

## Phase 1 — Annotation lookup MVP

Goal: support **Annotations → Entities** only.

### Deliverables

- New annotation workspace route, parallel to current resource workspace
- Load selected annotation resources into DuckDB
- Search/browse terms from `annotations.parquet`
- Return matching entities
- Save entity result set to selection
- Link into interaction exploration

### Why this first

It directly supports the user's stated flow:
- choose annotation concepts
- get matching entities
- save them to selection
- explore interactions of that set

## Phase 2 — Enrichment

Goal: add **Entities → Annotations**.

### Deliverables

- Paste or load entity set
- Resolve identifiers to local entities
- compute enrichment over local annotation universe
- ranked results table
- ontology detail panel

## Phase 3 — Ontology-native navigation

### Deliverables

- ontology tree browser
- branch summaries
- term set builder
- smarter namespace-specific UX

## Suggested component architecture

A likely parallel to the current interaction workspace:

- `src/app/duckdb/annotations/workspace/page.tsx`
- `src/features/duckdb/annotations/workspace-shell.tsx`
- `src/features/duckdb/annotations/context.tsx`
- `src/features/duckdb/annotations/refine-pane.tsx`
- `src/features/duckdb/annotations/results-pane.tsx`
- `src/features/duckdb/annotations/details-pane.tsx` or chat pane

Shared utilities:
- SQL builders for term search and entity lookup
- entity selection export helpers
- ontology term label / hover card reuse

## Open questions

1. Do we allow mixed interaction + annotation resource selections in one workspace?
2. Should pathway resources like Reactome/WikiPathways open in annotation workspace, interaction workspace, or both depending on intent?
3. Do we treat `annotations.parquet` as flat term membership only, or immediately integrate ontology parent expansion?
4. For enrichment, do we compute statistics fully in DuckDB, or partially in JS/Python after fetching aggregates?
5. Should annotation resources support saving **terms** as well as **entities**?

## Recommended next implementation step

Build **Phase 1 / Option A-mode-1**:

- annotation workspace route
- resource loading for annotation artifacts
- term search
- term → entities results
- save result entities to selection

That gives the first complete annotation-native workflow with the best user value / implementation effort ratio.
