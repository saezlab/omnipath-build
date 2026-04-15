# Entity schema change summary for `omnipath_build/pipeline` and `id_resolver`

## Goal

Simplify entity handling so exported artifacts use semantic entity identifiers directly:

- `entity_id`
- `entity_id_type`

and all tables referencing entities use that same identifier pair instead of the current local numeric entity IDs.

Also simplify identifier handling by replacing the two current identifier parquet files with a single unified identifier artifact.

---

## Changes for `omnipath_build/pipeline`

### 1. Change the exported entity key

Today:
- `entities.parquet.entity_id` is a local numeric ID
- `interactions.parquet`, `associations.parquet`, and `annotations.parquet` reference that numeric ID

New:
- `entities.parquet` is keyed by:
  - `entity_id`
  - `entity_id_type`
- these are the exported semantic IDs
- when canonicalization succeeds, they should be the canonical identifier pair
- unresolved entities should still get a stable exported `(entity_id, entity_id_type)` pair

### 2. Revise `entities.parquet`

Keep a scalar provenance column:

- `entity_id`
- `entity_id_type`
- `entity_type`
- `entity_attributes`
- `taxonomy_id`
- `source`

Notes:
- `entity_id` is no longer a local numeric surrogate key

### 3. Replace the two identifier parquet files with one unified identifier artifact

Remove:
- `entity_identifiers_source.parquet`
- `entity_identifiers_resolved.parquet`

Add one unified file:
- `entity_identifiers.parquet`

Proposed core schema:
- `entity_id`
- `entity_id_type`
- `identifier`
- `identifier_type`
- `is_canonical`
- `sources`

Notes:
- `sources` records where the identifier assertion came from
- this unifies source-provided and resolver-produced identifiers in one artifact
- resolver-produced rows should be distinguishable by their `sources` values

### 4. Revise `interactions.parquet`

Replace numeric entity references with semantic entity keys:

- `interaction_id`
- `entity_a_id`
- `entity_a_id_type`
- `entity_b_id`
- `entity_b_id_type`
- `direction`
- `sign`
- `record_attributes`
- `entity_a_attributes`
- `entity_b_attributes`
- `evidence`
- `source`

So interactions reference entities via:
- `(entity_a_id, entity_a_id_type)`
- `(entity_b_id, entity_b_id_type)`

### 5. Revise `associations.parquet`

Replace numeric entity references with semantic keys:

- `association_id`
- `parent_entity_id`
- `parent_entity_id_type`
- `member_entity_id`
- `member_entity_id_type`
- `role_term_id`
- `stoichiometry`
- `record_attributes`
- `parent_attributes`
- `member_attributes`
- `evidence`
- `source`

### 6. Revise `annotations.parquet`

For entity annotations, replace numeric subject IDs with semantic entity keys:

- `subject_type`
- `subject_id`
- `subject_id_type`
- `cv_term`
- `source`

When `subject_type = 'entity'`, the subject should be:
- `subject_id = entity_id`
- `subject_id_type = entity_id_type`

### 7. Remove reliance on local numeric entity IDs in exported artifacts

The exported schema should no longer depend on local numeric entity IDs for joins.

All entity joins should instead use:
- `(entity_id, entity_id_type)`

This applies across:
- `entities.parquet`
- `entity_identifiers.parquet`
- `interactions.parquet`
- `associations.parquet`
- entity-targeting rows in `annotations.parquet`

---

## Changes for `id_resolver`

### 1. Keep the current resolver role

`id_resolver` should still:
- materialize mapping tables
- resolve supported identifiers to canonical backbone IDs
- provide provenance about how resolution happened

It does not need to own the final exported entity schema.

### 2. Continue to provide canonical ID selection

The pipeline should keep using resolver outputs to determine the final canonical identifier pair:

- proteins -> primary UniProt
- chemicals -> Standard InChI

### 3. Preserve resolver provenance so it can populate unified identifier rows

`resolve_identifier_frame(...)` already returns:
- `resolved_id`
- `resolved_id_type`
- `resolution_status`
- `resolution_source`

This provenance should be preserved by the pipeline when writing unified `entity_identifiers.parquet` rows.

In particular, the pipeline should be able to distinguish:
- identifiers asserted by the source itself
- identifiers produced by resolver canonicalization
- which resolver path/source produced the canonical identifier

### 4. No need for separate exported source/resolved identifier parquet files

With the new schema, `id_resolver` no longer needs to feed two separate public identifier artifacts.

Instead, its outputs should contribute to one unified exported file:
- `entity_identifiers.parquet`

with source provenance represented in:
- `sources`

---

## Net effect

### Pipeline artifacts
- `entities.parquet` uses semantic `(entity_id, entity_id_type)` keys
- `entities.parquet` keeps scalar `source`
- `entity_identifiers.parquet` replaces the separate source/resolved identifier parquet files
- `entity_identifiers.parquet` uses `sources`
- `interactions.parquet`, `associations.parquet`, and entity annotations all reference entities by `(id, id_type)`

### Resolver
- keeps doing canonicalization and provenance reporting
- does not require a major conceptual redesign
- mainly needs its provenance to be carried through into the unified identifier artifact
