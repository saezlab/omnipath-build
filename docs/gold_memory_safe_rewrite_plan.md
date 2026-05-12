# Gold Pipeline Memory-Safe Rewrite Plan

## Context

The current gold build works for moderate sources, but high-volume sources such as BindingDB expose several memory risks:

- `extract_from_silver_tables` eagerly collects identifier, annotation, occurrence, and membership tables into Polars DataFrames and builds nested list columns.
- `build_entities` keeps multiple full entity representations alive: temporary entities, canonicalized entities, final entities, occurrence maps, and entity evidence.
- `build_relations` reads all silver tables eagerly, then duplicates them into Python dictionaries keyed by occurrence, membership, and relation.
- Incremental gold merging reads full previous evidence tables, filters them in memory, concatenates changed evidence, and reduces again.

The desired direction is to make gold a lineage/keyed transformation over canonical silver parquet, with DuckDB or streaming Arrow/Polars doing large joins/group-bys, and Python reserved for small semantic rules that cannot yet be expressed declaratively.

## Goals

- Bound peak memory by table size chunks or DuckDB spillable operators, not by full source size.
- Replace the current gold builder with the DuckDB-backed builder; do not maintain a selectable legacy engine or long-lived fallback path.
- Preserve gold artifact contracts consumed by downstream pipeline stages:
  - `entities/entity.parquet`
  - `entities/entity_map.parquet`
  - `entities/entity_occurrence_map.parquet`
  - `entities/entity_evidence.parquet`
  - `relations/entity_relation.parquet`
  - `relations/entity_relation_evidence.parquet`
  - canonicalization reports and `_SUCCESS.json`
- Preserve raw-record lineage through `raw_record_id`, `occurrence_id`, `entity_key`, and `relation_key`.
- Let first builds bootstrap without producing expensive per-row deltas.
- Let incremental builds update by changed raw-record scope without reading the whole previous source output into memory.

## Non-Goals

- Do not redesign silver schemas in this pass.
- Do not change global combined schema semantics.
- Do not hand-optimize only BindingDB in gold; source-specific optimizations belong in bronze/silver parsers.
- Do not require all semantic annotation classification to be SQL on day one.
- Do not add `GOLD_ENGINE=legacy|duckdb`, compatibility switches, or source-level fallbacks to the old Python gold implementation.
- Do not keep the old implementation alive after the replacement path is validated by tests and benchmark gates.

## Current Hotspots

### 1. Entity Extraction

`omnipath_build/gold/utils/silver_entity_extraction.py` does partial lazy scans, then collects:

- `ids_fmt`
- `anns`
- `occ_df`
- `membership_df`
- `has_ontology_backing`

It then groups identifiers and attributes into nested list columns per occurrence, computes fingerprints through Python UDFs, deduplicates in memory, and returns full DataFrames to `build_entities`.

This is especially expensive for interaction sources because every raw interaction expands to parent and member occurrences, plus many identifiers and annotations.

### 2. Entity Canonicalization

`build_entities.py` canonicalizes over full in-memory frames. Resolver joins are mostly Polars lazy inside `id_resolver.resolve.parquet`, but the input and output are materialized DataFrames. The resolver input includes identifiers that cannot resolve unless filtered earlier.

The current flow builds:

1. occurrence-level candidates
2. deduplicated fingerprint-level temp entities
3. canonicalized entities
4. final entities
5. fingerprint map
6. occurrence map
7. entity evidence
8. reduced final entities from evidence

Several of these are full-source tables.

### 3. Relation Build

`omnipath_build/gold/build_relations.py` is the largest immediate risk:

- reads all silver tables with `pl.read_parquet`
- builds `annotations_by_occ`
- builds `identifiers_by_occ`
- builds `membership_annotations_by_id`
- builds `membership_rows_by_parent`
- builds `occurrence_rows`
- stores `relation_index` for every unique relation in Python
- reads relation evidence back into memory and reduces it

For BindingDB-sized sources, this duplicates already-large silver data in Python object form.

### 4. Incremental Gold Merge

`_build_gold_source_incremental` in `pipeline/tasks.py` builds changed gold for scoped silver rows, then reads full previous entity and relation evidence into memory, filters by `raw_record_id`, concatenates changed evidence, and reduces.

This is correct structurally, but not memory safe for large previous outputs.

## Proposed Architecture

Replace the current in-memory gold source builder with a DuckDB-backed builder and a per-source temporary/state database:

```text
silver parquet
  -> DuckDB staging views
  -> entity candidate tables
  -> resolver candidate tables
  -> canonical entity tables
  -> entity evidence parquet
  -> relation evidence parquet
  -> reduced entity/relation parquet
```

The builder should write parquet artifacts directly from DuckDB queries where possible. Python should be used only for:

- CV label formatting until labels are materialized upstream or mapped through a table
- entity fingerprint and entity/relation key functions until equivalent SQL UDFs are registered
- annotation classification rules if they remain too semantic for SQL
- canonicalization report formatting

Use DuckDB temp tables for reusable intermediate results. This gives explicit lifetimes and lets DuckDB spill to disk when configured.

## Target Dataflow

### Step A: Build Occurrence Classification Table

Create a DuckDB table/view `occurrence_class` from silver:

Columns:

- `occurrence_id`
- `record_id`
- `_raw_record_id`
- `_raw_record_key`
- `parent_occurrence_id`
- `entity_role`
- `entity_type`
- `entity_type_label`
- `has_membership`
- `has_ontology_backing`
- `record_class`
- `occurrence_order`

Implementation:

- Read `entity_occurrence.parquet`.
- Read distinct parent occurrence IDs from `membership.parquet`.
- Read distinct ontology-backed occurrence IDs from `entity_identifier.parquet`.
- Compute `record_class` in SQL using the same rule ordering as today.

Memory benefit:

- Avoids collecting occurrence and membership tables into Python.
- Gives relation build and entity build a shared classification source.

### Step B: Build Identifier and Annotation Normalization Tables

Create normalized staging tables:

- `identifier_norm(occurrence_id, identifier_type, identifier_type_label, identifier)`
- `annotation_norm(occurrence_id, term, term_label, value, unit, unit_label, annotation_pos)`

Implementation options:

- Prefer a small CV term mapping table for accession-to-label formatting instead of Python UDFs.
- If no mapping table exists yet, register scalar UDFs for `format_cv_term`, `_normalize_attr_term`, and accession checks.

Memory benefit:

- Avoids full `ids_fmt` and `anns` materialization in Polars.
- Keeps repeated term formatting in a reusable table.

### Step C: Build Entity Candidates as Flat Tables

Replace nested list candidate rows with flat candidate components:

- `entity_candidate(candidate_id, fingerprint, occurrence_id, entity_type, taxonomy_id, candidate_order)`
- `entity_candidate_identifier(fingerprint, identifier_type, identifier)`
- `entity_candidate_attribute(fingerprint, term, value, unit)`
- `occurrence_fingerprint_map(occurrence_id, fingerprint)`

Important change:

- Do not store `identifiers` and `entity_attributes` as large list columns until the final parquet projection.
- Compute fingerprint from a canonicalized identifier representation. Options:
  - initially register a Python UDF over an aggregated sorted JSON/list string
  - later replace with a stable SQL hash over sorted normalized identifiers

Memory benefit:

- Flat tables are easier for DuckDB to group, spill, and join.
- Large per-occurrence nested structs are avoided.

### Step D: Canonicalization Through DuckDB Tables

Split canonicalization into relational tables:

- `resolver_input(entity_pk, entity_type, taxonomy_id, id, id_type)`
- `resolver_result(...)`
- `preferred_uniprot(entity_pk, primary_uniprot, taxonomy_id)`
- `preferred_inchi(entity_pk, standard_inchi)`
- `canonical_rows(entity_pk, canonical_identifier, canonical_identifier_type)`
- `entity_export_keys(local_entity_pk, export_entity_id, export_entity_id_type)`
- `canonical_identifier_rows(entity_id, entity_id_type, identifier, identifier_type, is_canonical, source_marker)`

Resolver table joins can stay in Polars only as a bounded internal implementation detail at first, but the interface must be file/table based:

1. write `resolver_input.parquet`
2. call resolver to produce `resolver_result.parquet`
3. continue canonicalization in DuckDB

Then migrate resolver joins to DuckDB separately if needed.

Memory benefit:

- Avoids keeping resolver input, resolved rows, authoritative identifiers, canonicalized entities, and final entities simultaneously in Python.

### Step E: Write Entity Artifacts Directly

Write these artifacts from DuckDB:

- `entity_map.parquet` from `fingerprint -> final entity_pk`
- `entity_occurrence_map.parquet` from `occurrence_fingerprint_map join entity_map`
- `entity_evidence.parquet` from occurrence/raw lineage joined to final entity rows
- `entity.parquet` from reduced evidence

For nested output columns (`identifiers`, `entity_attributes`, `sources`):

- Use DuckDB list/struct aggregation if compatible with downstream readers.
- If Arrow struct-list compatibility is problematic, write flat side tables first, then assemble final parquet in bounded batches by entity key.

## Relation Rewrite

### Step F: Materialize Relation Input Tables

Create DuckDB views/tables:

- `occurrence_class`
- `entity_occurrence_map`
- `entity_by_pk(entity_pk, entity_key)`
- `membership_with_entity_keys`
- `annotation_norm`
- `membership_annotation_norm`

For BindingDB-style interaction rows, the core relation can be built in SQL:

```text
parent interaction occurrence
  -> two member occurrences
  -> member entity pks/keys
  -> ordered subject/object participants
  -> relation_key
  -> relation evidence row
```

### Step G: Separate Core Relations From Semantic Annotation Relations

Implement relation build in two lanes:

1. SQL lane:
   - interaction participant relations
   - membership relations
   - relation key generation
   - evidence count aggregation

2. Python bounded lane:
   - annotation classification and ontology-object materialization
   - only process annotations that could become `annotation_relation` or evidence/attributes
   - stream by `raw_record_id` or occurrence batches

The Python lane should never build all annotations by occurrence. It should read batches from DuckDB, classify, and append evidence rows to parquet.
It is not a fallback to the old relation builder.

### Step H: Remove Python `relation_index`

Instead of maintaining `relation_index` in Python:

- write relation evidence rows first
- reduce to `entity_relation.parquet` with DuckDB:
  - group by `relation_key`, `subject_entity_key`, `predicate`, `object_entity_key`, `relation_category`
  - count evidence
  - assign stable `relation_pk`

For incremental builds:

- preserve previous `relation_pk` by joining previous `entity_relation.parquet` on `relation_key`
- assign new PKs after `max(previous_pk)`

Memory benefit:

- relation cardinality no longer controls Python heap size.

## Incremental Gold Rewrite

### Current Problem

`_build_gold_source_incremental` reads full previous evidence tables into Polars, filters out changed raw IDs, concatenates changed evidence, and reduces. This will become the next bottleneck after relation build is fixed.

### Target Approach

Maintain source-local DuckDB state for gold evidence:

- `gold/<source>/state.duckdb`
- tables:
  - `entity_evidence`
  - `entity`
  - `entity_map`
  - `entity_occurrence_map`
  - `entity_relation_evidence`
  - `entity_relation`

Incremental update:

1. Load changed silver rows only.
2. Build changed entity/relation evidence into temp tables.
3. Delete previous evidence rows where `raw_record_id in changed_raw_record_ids`.
4. Insert changed evidence.
5. Recompute affected entity keys and relation keys only.
6. Export full source parquet artifacts from DuckDB state, or export changed partitions if/when layout supports partitioning.

This mirrors the combined DuckDB state model and avoids full previous evidence materialization.

## Phased Migration

### Phase 0: Instrumentation and Guardrails

Add per-stage logging:

- parquet row counts and sizes
- DuckDB memory limit/temp directory
- peak RSS if available on Linux
- counts for occurrence classes, entity candidates, resolver rows, relation evidence rows

Add config knobs:

- `DUCKDB_MEMORY_LIMIT`
- `DUCKDB_TEMP_DIRECTORY`
- `GOLD_BATCH_SIZE`

Acceptance:

- Existing tests pass against the replacement builder.
- Instrumentation prints enough detail to diagnose source-specific blowups.
- No new code path can opt into the old gold builder.

### Phase 1: DuckDB Relation Reduction

Replace final `pl.read_parquet(...relation_evidence...) -> reduce_relations_from_evidence` with DuckDB group-by/export.

This is low risk and removes one full in-memory read after relation evidence has already been written.

Acceptance:

- `entity_relation.parquet` satisfies the existing artifact schema and relation-key uniqueness invariants for test-mode sources.
- Relation PK preservation works for incremental rebuilds.

### Phase 2: SQL Core Interaction Relations

For interaction-like sources with exactly two participants, implement SQL relation evidence generation.

Complex membership and annotation relations are handled by the bounded Python lane described above, not by the old relation builder.

Acceptance:

- BindingDB relation evidence has stable keys, endpoints, predicates, and evidence counts for core interaction rows.
- Python relation builder is bypassed for BindingDB core interactions.
- Peak RSS no longer scales with full BindingDB annotation/occurrence tables.

### Phase 3: DuckDB Entity Candidate Extraction

Create DuckDB-backed entity candidate tables and write:

- `entity_candidate`
- `entity_candidate_identifier`
- `entity_candidate_attribute`
- `occurrence_fingerprint_map`

Initially, use Python UDFs only for fingerprint computation and CV formatting.

Acceptance:

- `entity_map.parquet` and `entity_occurrence_map.parquet` satisfy schema, uniqueness, and lineage invariants for representative sources.
- Entity candidate intermediate tables can be inspected independently.

### Phase 4: File/Table-Based Canonicalization

Refactor canonicalization so it consumes and produces parquet/table artifacts rather than large DataFrames.

Acceptance:

- `entity.parquet` and `entity_evidence.parquet` satisfy schema, key, lineage, and evidence-count invariants.
- Resolver input is filtered to supported identifier types before resolver joins.
- Canonicalization report exposes the same categories of diagnostics.

### Phase 5: DuckDB Source Gold State

Introduce `gold/<source>/state.duckdb` and route incremental updates through delete/insert by raw-record scope.

Acceptance:

- Incremental update does not read full previous evidence into Python.
- Empty silver delta skips gold.
- Changed raw IDs update only affected entity/relation scopes.

### Phase 6: Remove Old Gold Builder

After replacement validation passes:

- route BindingDB, ChEMBL, IntAct, STITCH, and other high-volume sources through the DuckDB builder
- delete the old relation Python dict builder
- delete obsolete in-memory entity extraction entry points once entity extraction is replaced
- remove any migration-only comparison code that is not part of normal tests

## Validation Strategy

Build comparison utilities:

- compare row counts for every gold artifact
- compare key sets:
  - `entity_key`
  - `relation_key`
  - `occurrence_id`
  - `_fingerprint`
- compare evidence counts by key
- compare canonical identifiers by entity key
- compare relation endpoints/predicates by relation key

For nested columns, compare normalized exploded forms rather than raw serialized Arrow layout.

These utilities are migration and regression tools, not a long-term compatibility layer. Once the replacement builder is the only path, tests should assert schemas, keys, lineage, deterministic output, and aggregate counts from the produced artifacts directly.

## Performance and Memory Safety Test

Add an automated benchmark/regression test using a deterministic one-tenth IntAct slice. IntAct is a better representative test than small fixtures because it exercises interaction participants, membership edges, annotations, identifiers, and canonicalization at enough volume to expose memory behavior.

Slice definition:

- build or select IntAct silver rows where `hash(raw_record_id) % 10 = 0`
- include all dependent occurrence, identifier, annotation, membership, and relation rows reachable from those raw records
- write the sliced silver dataset to an isolated temp source directory so the gold builder sees a normal source layout
- keep the slice deterministic across machines and runs

Test command shape:

```text
build IntAct 1/10 silver slice
run gold build for the sliced source with DuckDB memory limit and temp directory set
record wall time, peak RSS, DuckDB temp spill size, input parquet size, and output parquet size
assert artifact schemas, key uniqueness, raw-record lineage, and evidence-count consistency
assert peak RSS stays below a configured multiple of the sliced silver parquet size
assert no gold stage calls full-source `pl.read_parquet`, `to_dicts`, or full-table `iter_rows`
```

Initial gate:

- peak RSS should be less than `2.5x` the total sliced silver parquet size, excluding resolver lookup tables
- no single Python batch should exceed `GOLD_BATCH_SIZE`
- DuckDB temp spill is allowed and should be reported, not treated as failure
- wall time is recorded as a trend metric first; add a hard regression threshold after two stable CI baselines

Impact measurement:

- run the current implementation once before deletion on the same one-tenth IntAct slice and store the metrics in a checked-in benchmark note or CI artifact
- compare the replacement builder against that baseline for peak RSS, wall time, and successful completion under a constrained DuckDB memory limit
- this baseline is only for measuring impact; it is not a supported legacy mode

Existing baseline setup:

- A one-off current-pipeline baseline helper already exists at `scripts/intact_10th_baseline.py`.
- It runs the existing pipeline in an isolated output root after monkeypatching only IntAct's raw parser to keep rows where `zero_based_raw_row_ordinal % 10 == 0`.
- The baseline was generated at `baseline_outputs/intact_10th_current`.
- The main comparison file is `baseline_outputs/intact_10th_current/metrics.json`; it includes wall time, peak RSS, command, output root, parquet row counts, schemas, file sizes, and SHA-256 hashes.
- Supporting files are `baseline_outputs/intact_10th_current/run.log` and `baseline_outputs/intact_10th_current/worker_summary.json`.
- The captured baseline run completed successfully with:
  - wall time: `198.46s`
  - peak RSS: `3551.9 MiB`
  - IntAct rows kept: `118,142` of `1,181,411`
  - gold `entity.parquet`: `27,605` rows
  - gold `entity_evidence.parquet`: `236,252` rows
  - gold `entity_relation.parquet`: `110,538` rows
  - gold `entity_relation_evidence.parquet`: `118,110` rows
- Rerun command:

```bash
uv run python scripts/intact_10th_baseline.py \
  --output-root baseline_outputs/intact_10th_current \
  --force
```

Use this folder as the current-builder comparison target while implementing the DuckDB replacement. Do not turn it into a supported legacy execution path.

## Memory Safety Rules

- Do not call `pl.read_parquet` on full high-volume source tables in gold build paths.
- Do not use `to_dicts`, `iter_rows`, or Python dict indexes over full silver/gold tables.
- Do not aggregate large list/struct columns until final artifact projection.
- Prefer DuckDB temp tables/views and `COPY (...) TO parquet`.
- Keep Python semantic processing bounded by explicit batches.
- First builds write key scopes and full artifacts, not row-level deltas.
- Incremental builds flow from raw-record lineage, not row comparisons.

## Open Questions

- Should source-local `state.duckdb` be the canonical gold state, with parquet as exported artifacts, or should parquet remain canonical?
- Do we want stable numeric `entity_pk` and `relation_pk` to persist across full rebuilds, or are stable string keys sufficient for downstream consumers?
- Should CV label formatting be materialized as a small lookup table during silver or gold?
- Can `compute_entity_fingerprint` and `compute_entity_key` be expressed as deterministic SQL UDFs without Python object conversion overhead?
- How much annotation classification can move to declarative SQL rules?

## Recommended First Implementation Slice

Start with relation memory safety because it is the largest BindingDB risk.

1. Add DuckDB reduction for `entity_relation.parquet` from `entity_relation_evidence.parquet`.
2. Use the existing deterministic one-tenth IntAct baseline setup for performance and output comparison.
3. Add a BindingDB/core-interaction SQL relation evidence path.
4. Replace current entity candidate extraction with DuckDB tables.
5. Remove old relation/entity code as each replacement path passes validation.

This sequence gives an early memory win without forcing the entire canonicalization stack to change at once.
