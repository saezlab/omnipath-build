# Search Schema v2 Cutover Plan (Direct Switch)

This plan is for a **breaking, non-additive cutover** across:

- `omnipath_build` (index/document generation + Meilisearch settings)
- `../omnipath-present/next-omnipath` (UI + API + types)

No backward-compat layer. Build + present switch together.

---

## 0) Final decisions (agreed)

1. **Annotation shape = 3 keys only**
   - `{ term, value, unit }`
   - `term` and `unit` are both `label:accession`

2. **Source provenance format**
   - Use compact strings: `label:id` (e.g. `FooDB:1766`)
   - No split `source_ids` / `source_names`

3. **Evidence split retained**
   - Keep `evidence` array with deterministic `evidence_serial: 1..n`
   - Evidence-level grouping in both interactions and associations

4. **Keep build-time direction/sign fields (current behavior)**
   - Retain `directions`, `has_direction`, `has_positive_sign`, `has_negative_sign`
   - Continue computing these in `omnipath_build`; frontend keeps using these booleans

5. **Keep flattened Meilisearch term arrays**
   - `interaction_annotation_terms`
   - `association_annotation_terms`
   - Terms included only when annotation has no value and no unit

6. **Entities CV terms**
   - Remove generic `cv_terms`
   - Keep ontology-specific: `cv_terms_go`, `cv_terms_mi`, `cv_terms_om`, `cv_terms_hp`, `cv_terms_kw`

7. **Entity relation fields**
   - Keep `complexes/pathways/reactions/reactants/products/stoichiometry/pathway_steps`
   - Note:
     - `complexes/pathways/reactions` are derivable from associations (redundant but useful for entity cards)
     - `reactants/products/stoichiometry/pathway_steps` are reaction-role projections and not present in associations

---

## 1) Target schema deltas (breaking)

## 1.1 search_interactions

### Remove
- old map-list evidence fields (`*_annotation_terms/values/units` as keyed maps)

### Add/keep
- `directions`
- `has_direction`
- `has_positive_sign`
- `has_negative_sign`
- `interaction_id`
- `interaction_key`
- `member_a_id`, `member_b_id`
- `member_types` (formatted `label:accession`)
- `sources` (doc-level union, `label:id`)
- `evidence: [{ evidence_serial, source, interaction_annotations, member_a_annotations, member_b_annotations }]`
  - one evidence row per evidence unit per source
  - each `*_annotations` is `[{term, value, unit}]`
- `interaction_annotation_terms` (doc-level flattened union; root-level filterable terms)

## 1.2 search_associations

### Remove
- old single merged `annotations` semantics (no evidence split)

### Add/keep
- existing identity fields (`association_id`, `association_key`, parent/member ids + types)
- `sources` (doc-level union, `label:id`)
- `evidence: [{ evidence_serial, source, annotations }]`
- `association_annotation_terms` (doc-level flattened union; root-level filterable terms)

## 1.3 search_entities

### Remove
- `cv_terms`

### Keep
- existing fields incl. `complexes/pathways/reactions/reactants/products/stoichiometry/pathway_steps`
- `cv_terms_go`, `cv_terms_mi`, `cv_terms_om`, `cv_terms_hp`, `cv_terms_kw`

---

## 2) omnipath_build changes

## 2.1 `omnipath_build/search_builder/build_search_interactions.py`

1. Keep existing direction/sign computation block:
   - retain causal traits/sign tables
   - retain `_build_sign_df`, `_build_causal_traits`, `_get_param_directions`
   - retain creation of `directions_df`

2. Rebuild evidence aggregation:
   - group by evidence unit (interaction entity + source + normalized annotation bundles)
   - produce `evidence_serial` deterministically (sorted grouping key)
   - store annotations as list of `{term, value, unit}`
   - term/unit formatted as `label:accession`

3. Add evidence/doc-level source formatting:
   - use `SourceName:source_id` format in evidence `source` and top-level `sources`

4. Compute term flattening from new annotation structs:
   - include only no-value/no-unit terms
   - write top-level `interaction_annotation_terms`

5. Keep `interaction_id` deterministic assignment.

## 2.2 `omnipath_build/search_builder/build_search_associations.py`

1. Replace merged `annotations` construction with evidence-first model.
2. Build `evidence` entries with deterministic `evidence_serial`.
3. Store evidence `source` as `label:id` string.
4. Use 3-key annotations `{term, value, unit}`.
5. Compute top-level `association_annotation_terms` union.

## 2.3 `omnipath_build/search_builder/build_search_entities.py`

1. Remove output `cv_terms` column.
2. Keep generating ontology-specific arrays `cv_terms_go|mi|om|hp|kw`.
3. Ensure no downstream code assumes `cv_terms` exists.

## 2.4 `omnipath_build/search/meilisearch.py`

1. Update interactions filterable attrs:
   - keep `has_direction`, `has_positive_sign`, `has_negative_sign`
   - keep `interaction_annotation_terms`, `member_*`, `member_types`, `sources`, `interaction_id`

2. Update entity filterable attrs if needed:
   - do not include `cv_terms` (already not present)

3. Associations settings unchanged except new evidence payload is displayed.

## 2.5 Optional schema constants cleanup

- `omnipath_build/search_builder/schema.py`
  - keep ontology term sets used for flattening/filtering
  - keep constants used by the direction/sign pipeline

---

## 3) ../omnipath-present changes

## 3.1 Types

### File: `src/types/meilisearch.ts`

1. Replace interaction evidence type with new 3-key annotation lists:
   - `InteractionAnnotation = { term: string; value?: string | null; unit?: string | null }`
   - `InteractionEvidence = { evidence_serial: number; source: string; interaction_annotations: InteractionAnnotation[]; member_a_annotations: InteractionAnnotation[]; member_b_annotations: InteractionAnnotation[] }`

2. Update `MeilisearchInteraction`:
   - keep `directions`, `has_direction`, `has_positive_sign`, `has_negative_sign`
   - keep `interaction_annotation_terms`, `sources`, etc.

3. Update `MeilisearchAssociation`:
   - replace old `annotations` with `evidence[]`

4. Update `MeilisearchFilters`:
   - keep boolean sign/direction filters (`has_direction`, `has_positive_sign`, `has_negative_sign`)
   - keep `interaction_annotation_terms` and others

## 3.2 Meilisearch query/filter building

### Files:
- `src/lib/meilisearch/filters.ts`
- `src/lib/meilisearch/search.ts`
- `src/app/api/meilisearch/facet-search/route.ts`

Changes:
1. Keep boolean interaction filter clauses (`has_direction`, `has_positive_sign`, `has_negative_sign`).
2. Keep these fields in requested facets.
3. Keep facets for `member_types`, `interaction_annotation_terms`, `sources`.

## 3.3 Interaction UI

### Files:
- `src/features/interactions-search/components/filter-sidebar.tsx`
- `src/features/interactions-search/components/interaction-details.tsx`
- `src/features/explore/components/interactions-explore-tab.tsx`

Changes:
1. Keep Directionality/Effect button group backed by precomputed booleans.
2. Keep existing direction-derived source/target swapping logic where currently used.
3. Update details rendering from old map-based evidence to new arrays of `{term,value,unit}`.
4. Keep graph arrows/sign coloring based on precomputed direction/sign fields.

## 3.4 Entity UI (`cv_terms` removal)

### Files:
- `src/features/search/components/result-card.tsx`
- `src/features/search/components/entity-details-dialog.tsx`
- `src/contexts/entity-selection-context.tsx`
- `src/features/explore/page.tsx`
- `src/features/explore/components/related-entities-tab.tsx`
- `src/features/explore/components/annotations-explore-tab.tsx`

Changes:
1. Replace all `cv_terms` usage with helper union of ontology arrays:
   - `cv_terms_go|mi|om|hp|kw`
2. Update counts/badges and selected entity payload to use unioned terms.

## 3.5 Associations UI

### Files:
- `src/features/explore/components/associations-explore-tab.tsx`
- any association details components using old `annotations`

Changes:
1. Read evidence-based association payload.
2. Display per-evidence annotation bundles and `evidence_serial`.

---

## 4) Cutover order (single breaking release)

1. Implement + test build changes locally.
2. Rebuild datasets:
   - `search_entities.parquet`
   - `search_interactions.parquet`
   - `search_associations.parquet`
3. Update Meilisearch settings (new filterable facets).
4. Import new indexes.
5. Deploy `omnipath-present` with updated types/UI/API filters.
6. Smoke-test end-to-end.

No mixed-version window.

---

## 5) Validation checklist

## Build-side validation

- Interaction docs:
  - `directions`/`has_*sign` present and populated
  - `evidence` exists and has `evidence_serial`
  - annotations are `{term,value,unit}`
  - `interaction_annotation_terms` populated from null-value/null-unit terms only
- Association docs:
  - evidence split exists
  - top-level `association_annotation_terms` correct union
- Entity docs:
  - `cv_terms` absent
  - ontology-specific arrays present

## Present-side validation

- No runtime TypeScript errors after schema switch.
- Interaction filters work with term arrays and sources.
- Interaction details render evidence from new schema.
- Entity cards/details show annotation counts via ontology array union.
- Explore tabs still compute:
  - complexes/pathways/reactions from entity fields
  - interactions/associations counts from APIs.

---

## 6) Notes from data inspection (important)

1. `search_entities.complexes/pathways/reactions` match associations-derived values exactly in current data (redundant but valid cache fields).
2. `reactants/products` are non-empty and come from reaction-role annotations (not from associations index).
3. Many parent-member pairs have multiple member instances with distinct annotation signatures; therefore evidence grouping must remain annotation-driven, but raw instance IDs do not need to be exposed in final search payload.
