# Search RAM optimization plan

Date: 2026-03-24

## Goal

Reduce RAM and index/storage pressure in the search stack by:

1. removing `evidence` from the Meilisearch indexes while keeping it in the parquet outputs
2. storing ontology/filter terms as canonical accessions only (for example `GO:0003677` instead of `DNA binding:GO:0003677`)
3. adding an API endpoint in `../omnipath-present` so the frontend can fetch evidence lazily for detail views
4. measuring before/after impact on parquet build memory, Meilisearch index size, and live container memory

---

## Current state

### Live containers

From `docker stats --no-stream`:

- `omnipath-present-api-service-1`: `720.9 MiB`
- `omnipath-present-entity-service-1`: `236.3 MiB`
- `omnipath-present-omnipath-meilisearch-1`: `171.9 MiB`

Observation:

- Meilisearch is not currently the highest live RAM consumer.
- The bigger issue appears to be the payload shape and indexing/build overhead of the search documents.

### Meilisearch database footprint

From Meilisearch `/stats` and local `.meili-temp/db`:

- `databaseSize`: `4,299,685,888` bytes
- `usedDatabaseSize`: `2,933,870,592` bytes
- `.meili-temp/db` on disk: `897M`

Per index raw document DB sizes:

- `search_associations`: `484,757,504` bytes
- `search_interactions`: `227,291,136` bytes
- `search_entities`: `214,163,456` bytes
- `search_sources`: negligible

### Current parquet outputs inspected

Run inspected: `data/output/run-20260324-074913`

Files:

- `search_interactions.parquet`: `17.4 MB`
- `search_entities.parquet`: `23.8 MB`
- `search_associations.parquet`: `13.3 MB`

Rows:

- `search_interactions`: `39,960`
- `search_entities`: `114,663`
- `search_associations`: `774,984`

### Estimated in-memory dataframe sizes

Using Polars `estimated_size`:

- `search_interactions`: `~1986.8 MB`
- `search_entities`: `~1079.4 MB`
- `search_associations`: `~723.0 MB`

Combined: `~3789.2 MB`

### Heavy columns in current payloads

#### `search_interactions`

Largest estimated columns:

- `participant_annotation_terms_go`: `~1105.0 MB`
- `participant_annotation_terms_hp`: `~666.4 MB`
- `evidence`: `~156.3 MB`
- `interaction_annotation_terms`: `~36.3 MB`

Average list sizes:

- `participant_annotation_terms_go`: avg `66.0`, max `414`
- `participant_annotation_terms_hp`: avg `56.3`, max `612`
- `evidence`: avg `1.59`, max `27`

#### `search_entities`

Largest estimated columns:

- `descriptions`: `~447.8 MB`
- `cv_terms_go`: `~275.8 MB`
- `identifiers`: `~167.8 MB`
- `cv_terms_hp`: `~79.2 MB`

#### `search_associations`

Largest estimated columns:

- `evidence`: `~495.0 MB`
- `sources`: `~78.7 MB`
- `association_key`: `~54.7 MB`

---

## Optimization direction

### Decision 1: keep `evidence` in parquet, remove it from Meilisearch

Rationale:

- `evidence` is bulky nested detail payload
- it is useful for detail pages and exports
- it is not needed as an indexed/faceted field in Meilisearch
- it can be fetched lazily on demand by `interaction_id` or `association_id`

Expected savings from removing `evidence` from indexed search documents:

#### Dataframe-side savings

Approximate savings in current parquet-derived frames:

- `search_interactions.evidence`: `~156 MB`
- `search_associations.evidence`: `~495 MB`

Total estimated build/dataframe savings: `~651 MB`

#### Meilisearch-side savings

We do not have per-field byte accounting from Meilisearch, but expected reduction is material because `evidence` is the dominant nested payload for interactions and associations.

Working estimate:

- interactions index: roughly `50-120 MB` less
- associations index: roughly `250-400 MB` less
- total Meilisearch reduction: roughly `300-500 MB`

These are estimates and must be measured after implementation.

### Decision 2: store ontology/filter values as canonical accessions only

Examples:

- current: `DNA binding:GO:0003677`
- target: `GO:0003677`

This applies to fields like:

- entity search:
  - `cv_terms_go`
  - `cv_terms_mi`
  - `cv_terms_om`
  - `cv_terms_hp`
  - `cv_terms_kw`
- interaction search:
  - `interaction_annotation_terms`
  - `participant_annotation_terms_go`
  - `participant_annotation_terms_mi`
  - `participant_annotation_terms_om`
  - `participant_annotation_terms_hp`
  - `participant_annotation_terms_kw`
- association search:
  - `association_annotation_terms`

Rationale:

- filter semantics remain unchanged because filtering is done on exact term values
- canonical IDs are the stable representation
- labels should be resolved separately by the ontology service instead of being duplicated in every indexed document

### Estimated savings from accession-only terms

#### String payload reduction

##### `search_entities`

- `cv_terms_go`: `28.8M chars -> 7.2M chars` (`75.0%` saved)
- `cv_terms_hp`: `8.2M chars -> 2.6M chars` (`67.9%` saved)
- total term chars: `37.0M -> 9.8M` (`73.4%` saved)

##### `search_interactions`

- `interaction_annotation_terms`: `3.77M -> 0.89M` (`76.3%` saved)
- `participant_annotation_terms_go`: `115.8M -> 26.4M` (`77.2%` saved)
- `participant_annotation_terms_hp`: `69.8M -> 22.5M` (`67.8%` saved)
- total term chars: `189.5M -> 49.8M` (`73.7%` saved)

##### `search_associations`

- `association_annotation_terms`: `1.33M -> 0.51M` (`61.2%` saved)

#### Rough dataframe simulation

A rough replacement simulation gave:

- `search_entities`: `1079.4 MB -> 735.6 MB` (`~343.8 MB` saved)
- `search_interactions`: `1986.8 MB -> 227.6 MB` (`~1759.3 MB` saved)
- `search_associations`: `723.0 MB -> 719.7 MB` (`~3.3 MB` saved)

Important note:

- these are rough eager-frame estimates, not exact production runtime numbers
- the direction is still clear: canonical ID-only storage should significantly reduce payload size, especially for interaction participant ontology arrays

---

## Target architecture

### Search index responsibilities

Meilisearch should store:

- IDs and keys needed for result retrieval
- fields needed for text search
- fields needed for faceting/filtering
- compact summary fields needed for ranking or list rendering

Meilisearch should not store:

- bulky nested evidence payloads used only in detail views

### Evidence responsibilities

Parquet outputs continue to store full evidence.

FastAPI service in `../omnipath-present` adds endpoints to resolve evidence lazily from parquet-backed or equivalent data access.

The frontend will fetch evidence only when the user opens an interaction or association detail panel.

### Ontology label responsibilities

Meilisearch stores canonical ontology IDs only.

Frontend resolves labels/definitions through existing ontology APIs, for example:

- `/api/ontology/terms`

If needed, add an ontology label search endpoint later so the filter sidebar can search by label even though Meilisearch stores IDs only.

---

## Implementation plan

## Phase 1: update search builders and indexing payloads

### 1. Remove `evidence` from indexed search documents

#### Interactions

Update `omnipath_build/search_builder/build_search_interactions.py` so that:

- `evidence` remains in parquet output only if parquet is intended to remain the full-fidelity artifact
- a Meilisearch import payload excludes `evidence`

Possible implementation strategies:

1. keep a single parquet with full payload and strip fields in the importer before sending to Meili
2. emit two artifacts:
   - full parquet for archival/detail retrieval
   - lean search parquet for Meili import

Preferred initial strategy:

- keep the existing parquet as full-fidelity output
- strip `evidence` in the importer path before upload to Meili

Reason:

- minimal disruption to existing build artifacts
- easy to compare before/after
- preserves full detail in parquet without duplicating storage logic immediately

#### Associations

Apply the same pattern in `omnipath_build/search_builder/build_search_associations.py` and/or importer logic.

### 2. Convert ontology/filter arrays to canonical IDs only

Update builders so these arrays contain canonical IDs only:

- entities: `cv_terms_*`
- interactions: `interaction_annotation_terms`, `participant_annotation_terms_*`
- associations: `association_annotation_terms`

Likely files:

- `omnipath_build/search_builder/build_search_entities.py`
- `omnipath_build/search_builder/build_search_interactions.py`
- `omnipath_build/search_builder/build_search_associations.py`

Implementation detail:

- term normalization should extract canonical accession from the current `label:ACCESSION` value generation path
- output arrays should contain only values like `GO:0003677`, `HP:0002027`, `MI:0915`, `OM:0310`

### 3. Keep current Meilisearch settings unless field names change

Current filterable field names can remain unchanged.

That means the frontend can keep using the same filter keys:

- `cv_terms_go`
- `participant_annotation_terms_go`
- `interaction_annotation_terms`
- etc.

Only the value format changes.

---

## Phase 2: add evidence lookup API in `../omnipath-present`

### 4. Add lazy evidence endpoints in FastAPI service

Add endpoints that return full evidence by record ID.

Initial candidates:

- `GET /interactions/{interaction_id}/evidence`
- `GET /associations/{association_id}/evidence`

or POST batch variants if useful later:

- `POST /interactions/evidence`
- `POST /associations/evidence`

with body like:

```json
{ "ids": [123, 456, 789] }
```

### 5. Data source for evidence endpoints

Initial implementation options:

1. read from the full parquet artifacts directly
2. load into an auxiliary local database/table optimized for keyed lookup
3. materialize a lightweight sidecar dataset keyed by `interaction_id` / `association_id`

Preferred first implementation:

- use the existing parquet outputs if access patterns and latency are acceptable
- if direct parquet lookup is too slow, follow up with a keyed sidecar store

Recommended shape:

- retain only the columns needed for evidence resolution in the lookup path:
  - interactions: `interaction_id`, `interaction_key`, `evidence`
  - associations: `association_id`, `association_key`, `evidence`

### 6. Frontend integration for lazy evidence fetch

In `../omnipath-present`:

- update interaction detail view to fetch evidence on open rather than relying on evidence being embedded in Meili result payloads
- update association detail view similarly if applicable

Likely affected areas:

- interaction detail components in `next-omnipath/src/features/interactions-search/components/`
- any association details/explore views that currently assume evidence is already present in the search hit

Behavior:

- list views and result cards remain lean
- detail panel triggers evidence fetch by ID
- loading and error states should be shown in the detail panel

---

## Phase 3: frontend ontology label handling with accession-only values

### 7. Render labels using ontology service instead of facet value text

Current frontend behavior often parses labels from values like `DNA binding:GO:0003677`.

After switching to accession-only values, the frontend should:

1. treat the filter value as canonical ID
2. resolve labels via ontology API
3. cache term metadata client-side where possible

The existing `CvTermHoverCard` already resolves term IDs via `/api/ontology/terms`, which is a good starting point.

### 8. Update selected filter chips and sidebar options

Places that currently derive labels from the raw facet value should be updated to:

- display the resolved ontology label if available
- fall back to the raw ID if metadata is not yet loaded

Likely frontend files:

- `next-omnipath/src/features/interactions-search/components/filter-sidebar.tsx`
- `next-omnipath/src/features/workspace/refine/entities-refine-panel.tsx`
- `next-omnipath/src/features/workspace/refine/interactions-refine-panel.tsx`
- possibly `next-omnipath/src/features/search/components/entity-filter-sidebar.tsx`

### 9. Rework ontology term search in the filter sidebar

Current ontology sidebar search uses Meili facet search over the facet string values.

Problem:

- once Meili stores only IDs, searching for a label like `dna binding` will no longer match the facet value directly

Recommended solution:

- add or use an ontology search endpoint that searches labels by text and returns canonical IDs
- intersect those IDs with the current facet counts from Meili

Possible endpoint shape:

- `POST /api/ontology/search`

Example response:

```json
{
  "terms": [
    {"id": "GO:0003677", "label": "DNA binding"},
    {"id": "GO:0005634", "label": "nucleus"}
  ]
}
```

Fallback if we want a simpler first pass:

- resolve labels for currently visible facet options and do client-side filtering on those labels

This simpler fallback may be sufficient for an initial implementation.

---

## Phase 4: measurement and validation

After implementation, rerun the same inspection steps and record new numbers.

### 10. Measure parquet and build-side impact

For the latest run, record:

- file sizes of the three search parquets
- row counts and schema
- Polars `estimated_size` for each dataset
- biggest columns by estimated size

### 11. Measure Meilisearch impact

Record:

- container memory from `docker stats`
- Meili `/stats`
- per-index `rawDocumentDbSize`
- `.meili-temp/db` disk usage
- indexing/import time if practical

### 12. Record frontend/API behavior

Validate:

- entity filters still work by ontology terms
- interaction filters still work by ontology terms
- selected filter chips render labels correctly
- ontology hover cards still work
- interaction details lazily load evidence
- association details lazily load evidence

---

## Risks and open questions

### Risk 1: direct parquet lookup latency for evidence

Direct parquet reads may be too slow for per-click lookup if not cached.

Mitigation:

- start simple
- if needed, add a keyed sidecar store or preload a minimal lookup table

### Risk 2: frontend ontology search UX

Removing labels from facet values breaks label-based Meili facet search.

Mitigation:

- implement ontology search separately or do client-side filtering over resolved labels

### Risk 3: interaction payload still large after removing evidence

Even without evidence, participant ontology arrays are large.

Mitigation:

- canonical-ID-only storage should address a large part of this
- after migration, re-measure before considering further normalization or capping of outlier lists

### Risk 4: mixed old/new filter value formats during migration

Temporary deployments may have a mix of:

- `label:ID`
- `ID`

Mitigation:

- keep frontend parsing tolerant during rollout
- normalize selected filter values to canonical IDs where possible

---

## Deliverables checklist

### Build/index changes

- [ ] remove `evidence` from Meili import payload for interactions
- [ ] remove `evidence` from Meili import payload for associations
- [ ] keep full `evidence` in parquet outputs
- [ ] emit ontology/filter arrays as canonical IDs only

### API changes in `../omnipath-present`

- [ ] add interaction evidence endpoint
- [ ] add association evidence endpoint
- [ ] ensure endpoint can resolve evidence from current artifacts/data store

### Frontend changes in `../omnipath-present`

- [ ] lazy-load evidence in detail views
- [ ] render ontology labels from canonical IDs
- [ ] maintain hover-card term resolution
- [ ] update ontology filter search behavior

### Validation

- [ ] record updated live RAM numbers
- [ ] record updated Meili DB sizes
- [ ] record updated dataframe estimates
- [ ] compare before/after in this document or a follow-up report

---

## Before/after measurement template

### Before

Measured before the optimization changes / reindex:

- API container RAM: `720.9 MiB`
- Entity service RAM: `236.3 MiB`
- Meili container RAM: `232.2 MiB`
- Meili databaseSize: `4,299,685,888` bytes
- Meili usedDatabaseSize: `2,933,870,592` bytes
- Meili search_associations rawDocumentDbSize: `484,757,504` bytes
- Meili search_interactions rawDocumentDbSize: `227,291,136` bytes
- Meili search_entities rawDocumentDbSize: `214,163,456` bytes
- search_interactions estimated_size: `~1986.8 MB`
- search_entities estimated_size: `~1079.4 MB`
- search_associations estimated_size: `~723.0 MB`

### After

Measured after `make pipeline FULL_REINDEX=1 TEST_MODE=1` with run `run-20260324-083833`.

- API container RAM: `720.8 MiB`
- Entity service RAM: `236.8 MiB`
- Meili container RAM: `2.803 GiB` (post-import steady-ish snapshot; higher than pre-run because indexing/cache activity had just completed)
- Meili databaseSize: `2,896,007,168` bytes
- Meili usedDatabaseSize: `1,952,509,952` bytes
- Meili search_associations rawDocumentDbSize: `401,211,392` bytes
- Meili search_interactions rawDocumentDbSize: `94,265,344` bytes
- Meili search_entities rawDocumentDbSize: `185,155,584` bytes
- search_interactions estimated_size: `~493.3 MB`
- search_entities estimated_size: `~820.0 MB`
- search_associations estimated_size: `~720.7 MB`

### Notes

- implemented build/index changes:
  - interactions and associations keep `evidence` in parquet but exclude it from Meili import payloads
  - ontology/filter arrays now store canonical IDs only
- measured parquet/build-side improvements versus the earlier baseline:
  - `search_interactions` estimated size dropped from `~1986.8 MB` to `~493.3 MB`
  - `search_entities` estimated size dropped from `~1079.4 MB` to `~820.0 MB`
  - `search_associations` estimated size stayed roughly flat because evidence is still present in parquet by design
- measured Meili raw doc size improvements versus the earlier baseline:
  - `search_interactions`: `227,291,136 -> 94,265,344` bytes
  - `search_entities`: `214,163,456 -> 185,155,584` bytes
  - `search_associations`: `484,757,504 -> 401,211,392` bytes
- important caveat:
  - the new FastAPI and frontend code for lazy evidence loading was implemented in `../omnipath-present`, but those services were not rebuilt/restarted as part of `make pipeline`, so runtime verification of those new endpoints/UI flows still needs a service redeploy/restart
- evidence endpoint latency:
  - not yet measured against the running deployed service
- any regressions:
  - none observed in the search build/import path
- any further optimization candidates:
  - `descriptions` remains the heaviest entity field
  - `evidence` still dominates `search_associations.parquet`, which is expected until/if parquet-side sidecar evidence storage is introduced
