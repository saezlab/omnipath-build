# Resources → DuckDB adaptation report

## Scope
Initial investigation for adapting the `/resources` selection flow to the DuckDB workspace using `omnipath_build` gold artifacts instead of the existing search/export Parquet flow.

Sources inspected:
- `docs/resources_duckdb_adaptation_task.md`
- `data_v2/gold/*/*/*.parquet`
- `../omnipath-present/api-service/api_service/exports.py`
- `../omnipath-present/api-service/api_service/resource_downloads.py`
- `../omnipath-present/next-omnipath/src/lib/duckdb/sql.ts`
- `../omnipath-present/next-omnipath/src/features/duckdb/workspace/context.tsx`
- `../omnipath-present/next-omnipath/src/lib/subsets/client.ts`
- `../omnipath-present/next-omnipath/src/types/subsets.ts`

---

## 1. Observed gold resource package schemas

Across the current `data_v2/gold/<resource>/<version>/...` outputs, the resource package schemas are consistent per artifact type.

### `entities.parquet`
Columns:
- `entity_id: Int64`
- `entity_type: String`
- `display_name: String`
- `canonical_identifier: String`
- `canonical_identifier_type: String`
- `entity_attributes: List<Struct{term, value, unit}>`
- `taxonomy_id: String`
- `source: String`

Notes:
- one row per resource-local entity record
- single `source` scalar, not `sources: string[]`
- no `names`, `synonyms`, `gene_symbols`, `descriptions`, `references`, `ontology_terms`, `cv_terms`, or `identifiers[]` fields in the current search-export shape

### `interactions.parquet`
Columns:
- `interaction_id: Int64`
- `entity_a_id: Int64`
- `entity_b_id: Int64`
- `direction: Int64`
- `sign: Int64`
- `mechanism_term: String`
- `statement_term: String`
- `record_attributes: List<Struct{term, value, unit}>`
- `entity_a_attributes: List<Struct{term, value, unit}>`
- `entity_b_attributes: List<Struct{term, value, unit}>`
- `evidence: List<Struct{term, value, unit}>`
- `source: String`

Notes:
- one row per resource interaction record, not the older search-oriented pair document
- no `interaction_key`
- no `interaction_type`
- no `is_directed`
- no top-level `sources: string[]`
- no flattened `interaction_annotation_terms` or `participant_annotation_terms`
- no `evidence_count`

### `associations.parquet`
Columns:
- `association_id: Int64`
- `parent_entity_id: Int64`
- `member_entity_id: Int64`
- `role_term_id: String`
- `stoichiometry: String`
- `record_attributes: List<Struct{term, value, unit}>`
- `parent_attributes: List<Struct{term, value, unit}>`
- `member_attributes: List<Struct{term, value, unit}>`
- `evidence: List<Struct{term, value, unit}>`
- `source: String`

### `annotations.parquet`
Columns:
- `subject_type: String`
- `subject_id: Int64`
- `cv_term: String`
- `source: String`

Notes:
- this is an external side-table relative to entities/interactions/associations
- likely required to rebuild flattened ontology/filter fields expected by the current workspace

### `entity_identifiers.parquet`
Columns:
- `entity_id: Int64`
- `identifier: String`
- `identifier_type: String`
- `is_canonical: Boolean`
- `source: String`

Notes:
- this is also a side-table
- likely required to reconstruct entity identifier arrays and display heuristics used in the frontend

---

## 2. Current DuckDB workspace input contract

The current DuckDB workspace in `omnipath-present` does **not** consume raw gold resource files.
It consumes server-materialized subsets from:
- `/api/exports/interactions/parquet`
- `/api/exports/entities/parquet`
- `/api/exports/associations/parquet`

Those endpoints in `api_service/exports.py` filter the existing search Parquet files directly and preserve their search-oriented schemas.

### Current interaction subset assumptions
From `src/lib/duckdb/sql.ts` and `api_service/exports.py`, the workspace expects interaction rows with at least:
- `interaction_key`
- `member_a_id`
- `member_b_id`
- `interaction_type`
- `is_directed`
- `sign`
- `evidence_count`
- `sources: string[]`
- `interaction_annotation_terms: string[]`
- `participant_annotation_terms: string[]`

It also relies on:
- filtering via `entity_ids` against `member_a_id` / `member_b_id`
- local DuckDB filters using `list_contains(...)` on `sources`, `interaction_annotation_terms`, `participant_annotation_terms`
- ordering by `evidence_count DESC, interaction_key`
- extracting entity scope by unioning `member_a_id` and `member_b_id`

### Current entity subset assumptions
The workspace expects entity rows with at least:
- `entity_id`
- `entity_type`
- `names: string[]`
- `gene_symbols: string[]`
- `identifiers: [{ key, value }]`

It also benefits from:
- `descriptions: string[]`
- `references: string[]`
- `sources: string[]`
- `synonyms: string[]`
- `ontology_terms: string[]`
- `cv_terms: string[]`

The current frontend entity adapter in `src/lib/duckdb/sql.ts` explicitly normalizes these fields and uses them for:
- entity badges
- hover cards
- display name selection
- canonical identifier selection

### Current association subset assumptions
Less of the current DuckDB UI uses associations today, but the export route assumes search-style columns such as:
- `association_id`
- `association_key`
- `parent_entity_id`
- `member_entity_id`
- `parent_entity_type`
- `member_entity_type`
- `sources: string[]`
- `association_annotation_terms: string[]`

---

## 3. Schema mismatches that matter

### Interactions: major mismatch
Gold `interactions.parquet` uses:
- `entity_a_id` / `entity_b_id`
- `direction` instead of `is_directed`
- scalar `source` instead of `sources[]`
- structured attributes/evidence instead of flattened annotation term arrays
- no `interaction_key`, `interaction_type`, `evidence_count`

Current workspace expects:
- search-pair identity and display fields
- top-level list fields for fast local filter SQL
- pre-flattened annotation term arrays

This means the raw gold interaction files are **not directly mountable** in the current DuckDB workspace.

### Entities: major mismatch
Gold `entities.parquet` is minimal and normalized around:
- display name
- canonical identifier
- attributes side-channel
- scalar source

Current workspace expects a richer search-style denormalized entity document. In particular, the existing entity rendering path would lose:
- names/synonyms/gene symbols
- identifier arrays
- ontology term arrays
- sources arrays
- description/reference fields

Without normalization/materialization, the current entity badge and hover-card path will degrade sharply.

### Associations: moderate-to-major mismatch
Gold `associations.parquet` has enough core IDs for graph structure, but lacks:
- `association_key`
- denormalized source arrays
- flattened annotation term arrays
- explicit parent/member type fields

The current association export/search path assumes those are already present.

### Side-table mismatch
Gold packages split important information across:
- `entities.parquet`
- `entity_identifiers.parquet`
- `annotations.parquet`

The current DuckDB workspace assumes most of that has already been denormalized into the subset artifact it loads.

### Global mixed dataset vs per-resource package mismatch
The current export flow is built on one global search dataset and subsets it by filters.
The new resource flow starts from one or more per-resource packages.

This changes assumptions around:
- deduplication across resources
- ID overlap across selected resources
- whether one “interaction” table is a pure union or a canonical merged view
- how source provenance is preserved when multiple resource packages are opened together

---

## 4. What can be reused unchanged

### Reusable frontend pieces
These can largely stay as-is if the server returns a normalized contract close to the current subset schema:
- DuckDB WASM setup and browser file registration
- IndexedDB saved-session cache
- workspace loading/progress flow
- local paging/count/facet query framework
- entity summary extraction and local entity lookup pattern
- `/resources` page selection UX as the starting point

### Reusable backend pattern
These patterns can also be reused:
- FastAPI as the materialization boundary
- Next.js proxy route pattern used for `/api/exports/*/parquet`
- materialize-download-open flow already used by the DuckDB workspace

---

## 5. What needs an adaptation layer

### Required server-side normalization
A server materialization layer should convert one or more selected gold resource packages into a predictable DuckDB bundle.

At minimum it should:
- union selected resource files by artifact type
- add provenance for selected resources
- derive/normalize column names expected by the frontend
- flatten filterable annotation terms from structured attributes/evidence
- enrich entity rows using `entity_identifiers.parquet` and `annotations.parquet`

### Interaction normalization likely needed
Proposed normalized columns for a resource-DuckDB interactions artifact:
- `interaction_id`
- `interaction_key` = stable key over participants + source provenance strategy
- `member_a_id` from `entity_a_id`
- `member_b_id` from `entity_b_id`
- `interaction_type` from `statement_term`, `mechanism_term`, or a derived fallback
- `is_directed` derived from `direction`
- `sign`
- `evidence_count` derived from `len(evidence)` or `1` fallback
- `sources: string[]` from scalar `source` and/or evidence provenance
- `interaction_annotation_terms: string[]` flattened from `record_attributes`, `evidence`, and chosen interaction-level terms
- `participant_annotation_terms: string[]` flattened from `entity_a_attributes` and `entity_b_attributes`
- `resource_ids: string[]` or `source_resources: string[]` to preserve multi-resource provenance
- optional raw passthrough fields for future details views

### Entity normalization likely needed
Proposed normalized columns for a resource-DuckDB entities artifact:
- `entity_id`
- `entity_type`
- `names: string[]` seeded from `display_name`, canonical identifier, and known aliases if available
- `gene_symbols: string[]` derived when identifier types or attributes indicate gene symbols
- `descriptions: string[]` if derivable, otherwise empty
- `references: string[]` if derivable from attributes/evidence, otherwise empty
- `sources: string[]` from scalar `source`
- `synonyms: string[]` from non-canonical identifiers and relevant attributes where available
- `ontology_terms: string[]` from `annotations.parquet`
- `cv_terms: string[]` from `annotations.parquet`
- `identifiers: [{ key, value }]` from `entity_identifiers.parquet`
- retain `display_name`, `canonical_identifier`, `canonical_identifier_type`, `taxonomy_id`
- add `resource_ids: string[]`

### Association normalization likely needed
For associations, similar normalization should derive:
- `association_key`
- `sources: string[]`
- `association_annotation_terms: string[]`
- optional parent/member type fields via entity join

---

## 6. Recommended endpoint/data contract

## FastAPI endpoint shape
Recommended first endpoint:

`POST /resources/duckdb/materialize`

Request body:
```json
{
  "resource_ids": ["reactome", "signor"],
  "include": ["interactions", "entities"],
  "format": "bundle",
  "normalization": "workspace_v1"
}
```

Optional later fields:
```json
{
  "resource_ids": ["reactome", "signor"],
  "include": ["interactions", "entities", "associations"],
  "format": "bundle",
  "normalization": "workspace_v1",
  "deduplicate": false,
  "include_raw_columns": true,
  "filename": "resources_reactome_signor"
}
```

### Response shape
Prefer a **bundle** response, not raw package passthrough.

Reason:
- the current DuckDB client wants a stable artifact contract
- raw packages would force the browser to replicate normalization logic already better suited to FastAPI/polars
- multi-resource unioning and enrichment are much easier server-side

Recommended bundle contents:
- `manifest.json`
- `interactions.parquet` (normalized)
- `entities.parquet` (normalized)
- optional `associations.parquet` (normalized)

`manifest.json` should include:
- selected `resource_ids`
- resolved versions per resource
- row counts per artifact
- normalization version
- column list per artifact
- whether rows were unioned or deduplicated

### Frontend contract
Frontend should receive a **normalized/materialized bundle** tailored for the workspace, not raw gold files.

---

## 7. Multi-resource representation in DuckDB

For the initial implementation, treat multi-resource selection as a **unioned dataset** with provenance columns.

Recommended representation:
- one normalized `interactions` table/view
- one normalized `entities` table/view
- optional one normalized `associations` table/view
- each row carries provenance, e.g.:
  - `resource_ids: string[]` for merged rows
  - or `resource_id: string` if rows are kept unmerged

### Initial recommendation
Start with:
- **no cross-resource semantic deduplication**
- **simple union** of selected packages
- preserve row-level `resource_id`
- optionally add a stable `row_origin_key`

Why:
- safer and easier to validate
- avoids inventing merge semantics before the resource workspace is usable
- still supports source filtering and clear provenance

Later, if needed, add a deduplicated/canonicalized mode.

---

## 8. Extend current workspace or build a parallel resource path?

### Recommendation
Use a **parallel resource-materialization path** feeding the same DuckDB UI where practical.

Concretely:
- keep the existing `/api/exports/*/parquet` flow unchanged
- add a new FastAPI resource-materialization endpoint
- add a new Next proxy route, e.g. `/api/resources/duckdb/materialize`
- add a resource-open code path from `/resources`
- reuse the existing DuckDB browser/session/UI infrastructure after artifact download

### Why not force it through current export endpoints?
Because the current export endpoints are tightly coupled to the old search schema and filter model. Reusing them for resource packages would hide important differences and likely create a brittle hybrid.

### Why not build a totally separate workspace UI?
Because most of the expensive frontend work is already solved:
- browser DuckDB
- caching
- loading state
- local query patterns
- entity lookup pattern

The cleaner split is at the **server artifact contract**, not the full UI layer.

---

## 9. Proposed implementation plan

### Phase 1: server-side investigation helpers
1. Add a small FastAPI/internal helper module that:
   - resolves selected resource latest versions
   - enumerates available artifact files
   - loads/concatenates matching artifact types across resources
2. Add debug endpoints or tests that emit:
   - resolved versions
   - schemas
   - row counts

### Phase 2: normalized workspace bundle v1
3. Implement normalization functions for:
   - `interactions.parquet`
   - `entities.parquet`
   - optionally `associations.parquet`
4. Produce a `workspace_v1` bundle with:
   - `interactions.parquet`
   - `entities.parquet`
   - `manifest.json`
5. Keep normalization intentionally minimal but aligned with the current DuckDB frontend expectations.

### Phase 3: frontend resource-open path
6. On `/resources`, replace the disabled “Open for local querying” button with a call to the new proxy route.
7. Download the normalized bundle and open it in the DuckDB workspace.
8. Pass selected resource IDs in URL/session metadata for labeling and cache keys.

### Phase 4: workspace integration
9. Add a new client helper alongside `materializeInteractionsSubset` / `materializeEntitiesSubset`, e.g.:
   - `materializeResourceWorkspaceBundle(resourceIds)`
10. Teach the DuckDB workspace loader to accept:
   - current subset mode, or
   - new resource bundle mode
11. Reuse local querying and entity summary logic against the normalized artifacts.

### Phase 5: associations and details parity
12. Add normalized associations if/when the resource workspace needs them.
13. Revisit richer detail views using preserved raw columns from the gold artifacts.

---

## 10. Recommended first cut

The cleanest first cut is:
- support opening selected resources into DuckDB for **interactions + entities only**
- use **server-side normalization/materialization**
- return a **workspace bundle** rather than raw package files
- represent multi-resource selection as a **union with provenance**
- keep the existing search/export DuckDB path untouched

That gets `/resources -> open in DuckDB` working quickly without dragging the old Meilisearch/search-export assumptions into the new resource flow.

---

## 11. Key conclusion

The current gold resource packages are structurally closer to normalized build outputs, while the existing DuckDB workspace expects denormalized search/export artifacts.

So the main missing piece is not browser DuckDB capability; it is a **FastAPI normalization/materialization layer** that turns selected `data_v2/gold/<resource>/<version>/...` packages into a stable workspace-oriented bundle contract.
