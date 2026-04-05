# Annotation resource workspace: refined implementation plan

Date: 2026-04-05

This refines the earlier brainstorm into a concrete implementation plan based on the decisions we just made.

## Decided direction

- We will build a dedicated **annotation workspace**.
- It will eventually support two modes:
  - `Annotations → Entities`
  - `Entities → Annotations`
- For now, **only `Annotations → Entities` will be implemented**.
- `Entities → Annotations` will exist as a visible placeholder / blank state for now.
- The annotation workspace should make **heavy use of the ontology API** in `../omnipath-present`.
- We should treat **Reactome** and **WikiPathways** as both:
  - interaction resources
  - annotation resources

That last point is important, because it means the current single-valued `top_level_category` is useful for a first split, but is **not sufficient as the long-term capability model**.

---

## 1. Product / UX decisions

## 1.1 Core MVP story

The first end-to-end workflow is:

1. User selects annotation-capable resources on the Resources page
2. User opens the annotation workspace
3. User searches ontology terms by label / synonym / accession
4. User selects one or more terms
5. User sees matching entities
6. User saves some or all entities to selection
7. User navigates to interaction exploration with that saved selection

This is the workflow we should optimize first.

## 1.2 Resource capability model

We now need to distinguish **capabilities** from **presentation categories**.

### Current state

We currently added:
- `top_level_category = interaction | annotation | null`

That works for a first visual split, but fails for dual-use resources.

### Refined model

We should add capability flags to `resources.parquet`:

- `supports_interactions: bool`
- `supports_annotations: bool`
- `supports_ontology: bool`

Suggested derivation:

- `supports_interactions`
  - `interaction_count > 0`
- `supports_annotations`
  - `annotation_count > 0`
  - OR `ontology_term_count > 0`
- `supports_ontology`
  - `ontology_term_count > 0`
  - and later optionally also if the API service has a matching ontology loaded

### Why this matters

This lets us correctly represent:
- `reactome` → interactions + annotations + ontology
- `wikipathways` → interactions + annotations + ontology
- `signor` → interactions + annotations
- `uniprot` → annotations
- `hpo` → annotations + ontology

### Recommendation

Keep `top_level_category` for now as a coarse display affordance, but move workflow routing and CTA logic to capability flags.

---

## 1.3 Workspace routing rules

### New route

Add a dedicated route:

- `/duckdb/annotations/workspace?resources=...`

### Rules for CTA visibility on Resources page

If selected resources include at least one `supports_annotations = true` resource:
- show **Open annotation workspace**

If selected resources include at least one `supports_interactions = true` resource:
- show **Open in DuckDB** or rename to **Open interaction workspace**

This allows dual-use selections naturally.

### For mixed selections

For now, mixed selections are okay if the chosen workspace can actually use them.

In practice:
- annotation workspace loads only the annotation-relevant artifacts from selected resources
- interaction workspace loads only the interaction-relevant artifacts

That keeps the UX simple and supports Reactome/WikiPathways well.

---

## 1.4 Annotation workspace UX shape

We should mirror the current 3-pane workspace structure, but with annotation-native behavior.

### Left pane: query + refine

Purpose:
- choose terms
- browse ontology structure
- set matching semantics
- refine entity result set

Sections:
- selected resources
- mode switch
- term search input
- selected term chips
- ontology tree / grouped branches
- term matching mode: `ANY` / `ALL`
- optional filters:
  - ontology prefix
  - entity type
  - taxonomy
  - source/resource

### Center pane: results

For MVP (`Annotations → Entities`):
- result summary
- matching entities table
- result counts by resource
- actions:
  - save all to selection
  - save selected rows to selection
  - open selection page / explore interactions

### Right pane: details

This should be ontology-heavy.

When a term is selected:
- label / accession
- definition
- namespace
- resource support
- local count in current dataset
- merged ontology tree / parent context

When an entity row is selected:
- entity details
- identifiers
- matched terms
- source support

The existing chat pane can be deferred or replaced with a details pane for now.

---

## 2. Ontology API usage plan

The ontology API should be a first-class part of this workspace, not a decoration.

## 2.1 Existing frontend API routes we should use immediately

Already available in `next-omnipath`:

- `POST /api/ontology/search`
- `POST /api/ontology/terms`
- `POST /api/ontology/tree`

These are enough for the first MVP.

### MVP usage

- **search**
  - term autocomplete / search suggestions
- **terms**
  - labels, definitions, namespace rendering for selected terms and result details
- **tree**
  - merged hierarchy view for currently selected terms

## 2.2 Additional ontology API routes likely worth exposing soon

The backend API service already supports richer endpoints, including:
- term details
- parents
- children
- ancestors
- descendants
- trajectories

Recommendation:
- MVP can ship with `search`, `terms`, `tree`
- immediately after MVP, add frontend proxy routes for:
  - `GET /api/ontology/[ontologyId]/term/[termId]`
  - `GET /api/ontology/[ontologyId]/term/[termId]/children`
  - `GET /api/ontology/[ontologyId]/term/[termId]/descendants`
  - maybe `.../parents` and `.../ancestors`

This would let the details pane become much richer and more interactive.

## 2.3 OBO loading requirement

You explicitly want each annotation ontology loaded in the ontology service from OBO when available.

This aligns well with the API service behavior:
- local `.obo` files are auto-discovered from `ONTOLOGY_DATA_DIR`
- the service maps prefixes like `GO`, `HP`, `OM`, `MI`
- it also maps:
  - `WP...` → `wikipathways`
  - `R-...` → `reactome_pathways`

### Implication for the build/deploy flow

We should treat presence of per-resource `.obo` files in gold artifacts as an expected input to the ontology service.

### Immediate implementation assumption

For MVP we assume:
- selected annotation resources with ontology terms have corresponding OBO content already available to the ontology service
- if the API cannot resolve some terms, the UI should degrade gracefully and still display raw accessions

---

## 3. Data loading plan for the annotation workspace

The interaction workspace currently loads:
- `interactions.parquet`
- `entities.parquet`
- identifier tables

The annotation workspace should instead load:

- `annotations.parquet`
- `entities.parquet`
- `entity_identifiers_source.parquet`
- `entity_identifiers_resolved.parquet`
- optionally note which resources contribute `.obo` files, but OBO content stays in the ontology API service, not in DuckDB

## 3.1 Local DuckDB tables / views

Proposed mounted views:

- `resource_annotations`
- `resource_entities`
- `resource_entity_identifiers_source`
- `resource_entity_identifiers_resolved`

## 3.2 Minimum query patterns

### A. Available terms with counts

From selected resources:
- `SELECT cv_term, count(distinct subject_id), count(*) ...`
- filtered to `subject_type = 'entity'` initially
- joined with current source/resource filters

This powers:
- selected-term counts
- resource breakdowns
- local counts in the details pane

### B. Entities matching selected terms

Core logic:
- start from `resource_annotations`
- filter to selected `cv_term`s
- `subject_type = 'entity'`
- join to `resource_entities`
- join to resolved identifiers for display

Support both semantics:
- `ANY` selected term
- `ALL` selected terms

### C. Selected term support by resource

For the details pane:
- count matching entities per `source`
- maybe count raw annotation rows per `source`

### D. Entity-to-term expansion for selected entity row

When a row is selected:
- fetch all `cv_term`s attached to that entity in loaded resources
- render with ontology labels via API

---

## 4. Refined implementation phases

## Phase 0 — capability model correction

Before or alongside the workspace build, add the resource capability fields.

### Changes

#### `omnipath_build/pipeline/resources_index.py`
Add:
- `supports_interactions`
- `supports_annotations`
- `supports_ontology`

#### `../omnipath-present/next-omnipath/src/lib/resources.ts`
Extend `ResourceRecord` and summaries.

#### `../omnipath-present/next-omnipath/src/features/resources/page.tsx`
Update action logic:
- show annotation workspace CTA if any selected resources support annotations
- show interaction workspace CTA if any selected resources support interactions
- dual-use selections can show both

### Why phase 0 matters

This is the main correction required by the new Reactome/WikiPathways decision.

---

## Phase 1 — annotation workspace shell + loading

Goal: get a real annotation workspace route working end-to-end.

### New files

- `../omnipath-present/next-omnipath/src/app/duckdb/annotations/workspace/page.tsx`
- `../omnipath-present/next-omnipath/src/features/duckdb/annotations/workspace-shell.tsx`
- `../omnipath-present/next-omnipath/src/features/duckdb/annotations/context.tsx`
- `../omnipath-present/next-omnipath/src/features/duckdb/annotations/refine-pane.tsx`
- `../omnipath-present/next-omnipath/src/features/duckdb/annotations/results-pane.tsx`
- `../omnipath-present/next-omnipath/src/features/duckdb/annotations/details-pane.tsx`
- maybe `../omnipath-present/next-omnipath/src/features/duckdb/annotations/use-duckdb-annotation-workspace-ui-state.ts`

### Context responsibilities

The context should:
- parse selected `resources` from URL
- fetch workspace manifest
- download annotation-relevant artifacts
- register parquet files with DuckDB WASM
- mount annotation/entity/identifier views
- maintain selected terms
- maintain query mode
- maintain term matching mode (`ANY`/`ALL`)
- maintain entity result pagination
- expose term count summaries and entity results
- expose selected row / selected term details

### UX behavior

On initial load:
- materialize local annotation tables
- show empty state until terms are selected

---

## Phase 2 — ontology-heavy `Annotations → Entities` MVP

Goal: first useful analysis flow.

### Left pane features

#### Term search
Use `POST /api/ontology/search`.

Input behavior:
- search by label, synonym, accession
- selecting a suggestion adds the term chip
- allow pasting a term accession directly

#### Selected term chips
Each chip should use:
- `OntologyTermLabel`
- `CvTermHoverCard`

#### Merged tree preview
Use `POST /api/ontology/tree` for selected terms.

Display:
- branch grouping
- selected term location/context
- collapse/expand sections

#### Matching semantics
Toggle:
- `ANY selected term`
- `ALL selected terms`

### Center pane features

#### Result summary
Show:
- number of selected terms
- number of matching entities
- number of contributing resources
- maybe per-prefix distribution later

#### Entity table
Columns:
- display name
- canonical identifier
- entity type
- taxonomy
- matched term count
- supporting resources

#### Actions
- save all visible entities to selection
- save checked rows to selection
- go to selection
- explore interactions

### Right pane features

When a term is selected:
- ontology label
- accession
- definition
- namespace
- merged hierarchy context
- local entity count
- per-resource support

When an entity is selected:
- entity summary card
- identifiers
- matched terms
- source/resource support

---

## Phase 3 — placeholder mode for `Entities → Annotations`

This mode should exist in the UI but not be implemented yet.

### Behavior

- mode toggle is visible
- switching to it shows a clear placeholder:
  - “Entity-set enrichment is coming next.”
  - maybe brief explanation of planned inputs and outputs

This is useful because it locks in the IA now without requiring the stats work yet.

---

## 5. Query/state model

## 5.1 Annotation workspace state

Proposed state shape:

- `resourceIds: string[]`
- `mode: 'annotations_to_entities' | 'entities_to_annotations'`
- `selectedTerms: string[]`
- `selectedPrefixFilters: string[]`
- `selectedSources: string[]`
- `selectedEntityTypes: string[]`
- `selectedTaxonomyIds: string[]`
- `termMatchMode: 'any' | 'all'`
- `pageIndex`
- `pageSize`
- `selectedEntityIds: string[]` for checkbox selection in results
- `focusedTermId?: string`
- `focusedEntityId?: string`

## 5.2 URL encoding

For MVP, we can keep only `resources` in the URL and store the rest in component/context state.

If the workspace becomes a major navigable surface, later we can encode:
- selected terms
- mode
- filters

But that is not required for the first implementation.

---

## 6. Save-to-selection integration plan

This is critical for the downstream workflow.

Use existing selection infrastructure:
- `useEntitySelection()`
- local storage + URL-backed selection ids

### Requirement

The annotation workspace needs enough entity info to add proper `SelectedEntity` objects, not just raw IDs.

At minimum when saving an entity we should provide:
- `id`
- `entityId`
- `name`
- `type`
- maybe `cv_terms`
- maybe `fullResult` if easy, but not required for MVP

### MVP save behavior

- `Save all visible entities to selection`
- `Save selected rows to selection`

After save, offer links/buttons to:
- `/selection`
- existing interaction explore page

---

## 7. File-by-file implementation plan

## 7.1 `omnipath_build`

### `omnipath_build/pipeline/resources_index.py`
Add capability booleans.

Potential fields:
- `supports_interactions`
- `supports_annotations`
- `supports_ontology`

Then regenerate `data_v2/gold/resources.parquet`.

---

## 7.2 Resources page

### `../omnipath-present/next-omnipath/src/lib/resources.ts`
- extend `ResourceRecord`
- extend summaries if needed

### `../omnipath-present/next-omnipath/src/features/resources/page.tsx`
- compute selected resources’ capabilities
- show both workspace CTAs when applicable
- add `Open annotation workspace` link:
  - `/duckdb/annotations/workspace?resources=...`
- keep the current interaction CTA for interaction-capable resources

---

## 7.3 Annotation workspace frontend

### Routing / shell
- `src/app/duckdb/annotations/workspace/page.tsx`
- `src/features/duckdb/annotations/workspace-shell.tsx`

### State / loading
- `src/features/duckdb/annotations/context.tsx`

### Panes
- `src/features/duckdb/annotations/refine-pane.tsx`
- `src/features/duckdb/annotations/results-pane.tsx`
- `src/features/duckdb/annotations/details-pane.tsx`

### Shared SQL helpers
Likely add a new helper file rather than overloading interaction SQL:
- `src/lib/duckdb/annotation-resource-sql.ts`

Expected helpers:
- mount annotations
- mount entities
- mount identifiers
- query term counts
- query entity page for selected terms
- query per-resource support
- query entity details / term details

---

## 7.4 Ontology API frontend usage

### Reuse immediately
- `OntologyTermLabel`
- `CvTermHoverCard`
- `/api/ontology/search`
- `/api/ontology/terms`
- `/api/ontology/tree`

### Likely near-term additions
Add richer term details proxies if needed for the details pane.

---

## 8. Risks / gotchas

## 8.1 Capability mismatch with current `top_level_category`

This is now known and should be corrected early.

## 8.2 Term resolution gaps

Not every `cv_term` present in local data may resolve in the ontology API immediately.

Mitigation:
- always render raw term id as fallback
- never block query execution on ontology metadata fetch

## 8.3 Annotation row explosion

Some resources may have very large annotation tables.

Mitigation:
- aggregate term counts lazily
- paginate entity results
- avoid loading too many detail rows eagerly

## 8.4 Entity display quality

Some entities may not have great display labels from identifiers alone.

Mitigation:
- use canonical resolved identifiers when possible
- fall back to source identifiers or entity id

---

## 9. Recommended next coding step

The next best implementation step is:

### Step A
Add capability flags to `resources.parquet` and update the Resources page CTA logic.

### Step B
Scaffold the annotation workspace route/shell/context with artifact loading for:
- annotations
- entities
- identifiers

### Step C
Implement the first complete `Annotations → Entities` loop:
- ontology search
- selected term chips
- local entity query
- save-to-selection

That gives us the first usable annotation-native workflow while preserving room for enrichment next.
