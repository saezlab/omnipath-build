# Normalized silver pipeline optimization plan

## Goal

Improve the pipeline from source-specific silver outputs to per-source gold and combined outputs without overengineering the raw input layer.

The main principle is:

> Keep resource/input definitions flexible and Python-friendly for now. Optimize the stable handoff between silver and gold by making silver physically normalized and columnar-consumable.

This plan intentionally does **not** start by compiling `inputs_v2` declarations to Polars/DuckDB expressions. Raw parsing and `EntityBuilder` object construction can remain as-is until measurements show they are the dominant cost.

## Current bottleneck hypothesis

The most likely avoidable bottleneck is not `raw -> silver`, but repeated reconstruction of nested silver parquet into Python objects/dictionaries during gold builds.

Current path:

```text
raw source
  -> Python parser / EntityBuilder
  -> nested silver Entity parquet
  -> pyarrow batch.to_pylist()
  -> recursive Python extraction
  -> Python entity-description dictionaries
  -> Polars frames
  -> canonical gold
```

Target path:

```text
raw source
  -> existing Python parser / EntityBuilder
  -> normalized silver parquet tables
  -> Polars/DuckDB scans
  -> canonical gold
```

The earlier flat silver experiment suggests the largest win comes from avoiding recursive Python parsing and avoiding millions of intermediate Python dictionaries during `silver -> gold`.

---

## Step 1: Establish baseline timings and artifact sizes

Before changing code, measure the current pipeline per representative source.

Recommended sources:

- `uniprot`: large, simple entity records, no meaningful membership nesting.
- `chebi`: large entity source with ontology-related data.
- `signor`: interaction source with memberships and relation projection.

Measure separately:

```text
silver -> gold entities
silver -> gold relations
gold -> combined
```

For `silver -> gold entities`, break down:

```text
extract_all_from_silver()
pre-dedup/fingerprint work
canonicalization/resolver work
final reduction/write
```

For `silver -> gold relations`, break down:

```text
silver read/to_pylist
row classification/entity lookup
relation evidence writing
relation aggregation/write
```

Output:

- a short benchmark report in `docs/`
- timings for current nested silver
- row counts and file sizes for silver/gold artifacts

Success criteria:

- We have baseline numbers to compare normalized silver against.

---

## Step 2: Define normalized silver physical tables

Design a small set of normalized silver parquet tables that can represent the current `Entity` structure without requiring nested row reconstruction.

Initial proposed tables:

### `entity_occurrence.parquet`

One row per emitted entity occurrence, including parent entities and member entities.

Suggested columns:

```text
occurrence_id          int64/string, source-local stable occurrence key
record_id              string/int64, source raw record key if available
parent_occurrence_id   nullable, for nested member context if useful
entity_role            string, e.g. parent/member/interaction_subject/interaction_object
entity_type            string
source                 string
dataset                string
```

Optional columns:

```text
record_class_hint       string, optional preclassification hint
row_number              int64, diagnostic/debug aid
```

### `entity_identifier.parquet`

One row per identifier attached to an occurrence.

```text
occurrence_id
identifier_type
identifier
source
dataset
```

### `entity_annotation.parquet`

One row per annotation attached to an entity occurrence.

```text
occurrence_id
term
value
unit
source
dataset
```

This table can later feed:

- entity attributes
- evidence attributes
- annotation relations
- taxonomy extraction
- ontology term entity extraction

### `membership.parquet`

One row per parent/member relationship from silver membership.

```text
parent_occurrence_id
member_occurrence_id
is_parent
source
dataset
```

Optional:

```text
membership_id
membership_role
```

### `membership_annotation.parquet`

One row per annotation attached to a membership edge.

```text
membership_id or parent_occurrence_id + member_occurrence_id
term
value
unit
source
dataset
```

### `ontology_term.parquet`

For explicit ontology term exports or inferred ontology term rows.

```text
term_id
ontology_prefix
label
definition
synonyms
source
```

### `resource.parquet`

Resource metadata can stay as-is initially, or be normalized later.

Output:

- documented schema in `docs/` or `omnipath_build/silver/`
- schema helpers for empty frames and writers

Success criteria:

- The normalized tables can represent UniProt, ChEBI, and at least one interaction/membership source.
- Gold entity and relation builders can be implemented from these tables without reading nested silver.

---

## Step 3: Add a normalized silver writer while keeping existing inputs unchanged

Do **not** rewrite `pypath.inputs_v2`.

Keep this path:

```text
Dataset.raw()
  -> mapper
  -> pypath.internals.silver_schema.Entity
```

Change/add the silver writer so that each `Entity` can be flattened while it is still in memory:

```text
Entity
  -> entity_occurrence rows
  -> entity_identifier rows
  -> entity_annotation rows
  -> membership rows
  -> membership_annotation rows
```

This avoids this later expensive path:

```text
nested parquet -> Python row dict -> recursive extraction
```

Implementation approach:

- Add a new output mode to the silver builder, e.g. `normalized=True`.
- For each input dataset, maintain per-dataset writers for normalized tables.
- Generate deterministic `occurrence_id`s within source/dataset, e.g. sequential integers or stable dataset-prefixed IDs.
- Recursively flatten memberships at write time.
- Keep existing nested silver writer available during transition.

Important design choice:

- The silver writer may still use Python recursion over `Entity` objects.
- That is acceptable because it happens once during `raw -> silver`.
- The optimization target is avoiding repeated recursion during `silver -> gold`.

Output:

```text
data_v2/silver/<source>/<version>/normalized/entity_occurrence.parquet
data_v2/silver/<source>/<version>/normalized/entity_identifier.parquet
data_v2/silver/<source>/<version>/normalized/entity_annotation.parquet
data_v2/silver/<source>/<version>/normalized/membership.parquet
data_v2/silver/<source>/<version>/normalized/membership_annotation.parquet
```

or equivalent names directly in the source silver directory.

Success criteria:

- Existing input modules do not require major edits.
- Callables remain supported because they are evaluated by the current mapper.
- UniProt normalized silver can be generated from existing `proteins_schema`.
- Existing nested silver can still be generated for validation/fallback.

---

## Step 4: Implement columnar gold entity build from normalized silver

Add a new entity extraction path that detects normalized silver and bypasses `extract_all_from_silver()`.

Current:

```python
entity_descriptions, ontology_term_rows = extract_all_from_silver(silver_dir, source_name)
```

Target:

```python
occurrences = pl.scan_parquet(.../entity_occurrence.parquet)
identifiers = pl.scan_parquet(.../entity_identifier.parquet)
annotations = pl.scan_parquet(.../entity_annotation.parquet)
```

Columnar work:

1. Filter invalid/empty identifiers.
2. Normalize CV terms and identifier values.
3. Extract taxonomy from annotation rows where term is NCBI taxonomy.
4. Build entity attributes from annotation rows using existing classification semantics.
5. Construct stable fingerprint material per occurrence:

```text
entity_type + sorted(identifier_type, identifier) pairs
```

6. Deduplicate occurrences by fingerprint before resolver input.
7. Build `temp_entities` and `temp_identifiers` directly as Polars frames.
8. Reuse existing `_canonicalize_entities()` and `_reduce_entities()` initially.
9. Write the same gold outputs:

```text
entities/entity.parquet
entities/entity_map.parquet
entities/ontology_term.parquet
canonicalization_report.md
canonicalization_summary.json
```

Need to preserve `entity_map.parquet` semantics.

Current `entity_map.parquet` maps:

```text
_fingerprint -> final entity_pk
```

For relation building from normalized silver, it may be useful to additionally write:

```text
occurrence_id -> fingerprint -> final entity_pk
```

Either extend `entity_map.parquet` or add:

```text
entity_occurrence_map.parquet
```

Output:

- `build_entities()` chooses normalized path when available.
- Legacy nested extraction remains as fallback.

Success criteria:

- UniProt gold entity outputs match the legacy path semantically.
- Extraction/pre-dedup time is close to the flat-silver experiment scale.
- No Python reconstruction of nested silver rows occurs in the normalized path.

---

## Step 5: Implement columnar ontology annotation entity extraction

Some silver annotations materialize ontology term entities, e.g. GO terms or keyword IDs attached to proteins.

Current path extracts these by scanning each row's annotations in Python.

Normalized path should derive them from `entity_annotation.parquet`:

```text
filter annotations where term == CV_TERM_ACCESSION
filter valid accession-like values
construct CV_TERM entity occurrences/identifiers
```

Columnar output should be merged with regular entity candidates before canonicalization/dedup.

Also handle explicit ontology term rows from:

```text
ontology_term.parquet
```

Output:

- ontology term entity candidates generated columnarly
- ontology term metadata deduplicated columnarly where possible

Success criteria:

- Sources like UniProt still produce ontology-backed entities for GO/keyword annotations.
- ChEBI/Reactome ontology metadata behavior remains correct.

---

## Step 6: Implement relation build from normalized silver

After entity builds use normalized silver, relation building should also avoid rereading nested silver.

Current relation builder:

```text
silver parquet
  -> pyarrow batches
  -> to_pylist
  -> recursive row traversal
  -> compute entity descriptions/fingerprints
  -> lookup entity PK
  -> write relation evidence
```

Target relation builder:

```text
normalized silver tables
  -> join occurrence/entity maps
  -> derive relations and evidence columnarly where possible
```

Initial scope:

### Annotation relations

From `entity_annotation.parquet`:

1. Identify annotations classified as `annotation_relation`.
2. Identify object ontology entity from annotation value.
3. Join subject occurrence to `entity_occurrence_map`.
4. Join ontology object fingerprint/entity to entity map.
5. Emit relation evidence rows.

### Membership relations

From `membership.parquet`:

1. Join parent/member occurrence IDs to entity PKs.
2. Determine subject/object direction from `is_parent` and current predicate logic.
3. Attach membership annotations/evidence.
4. Emit relation evidence rows.

### Interaction relations

For binary interactions:

1. Use membership rows for interaction participants.
2. Order participants using existing semantics.
3. Determine predicate from interaction annotations and participant roles.
4. Emit evidence rows.

Pragmatic transition:

- Start with simple annotation/membership relations.
- Keep legacy relation builder fallback for complex sources until parity is proven.

Output:

- normalized relation build path
- same output files:

```text
relations/entity_relation.parquet
relations/entity_relation_evidence.parquet
```

Success criteria:

- UniProt and simple annotation-relation sources do not need nested silver relation reads.
- One interaction source can be validated against legacy output.
- Relation outputs match legacy semantics or intentional differences are documented.

---

## Step 7: Validate semantic parity against legacy nested silver

For each migrated source, build both paths:

```text
legacy nested silver -> gold
normalized silver -> gold
```

Compare:

### Entity outputs

- entity count
- identifier row count
- canonical identifier distribution
- entity type counts
- taxonomy counts
- ontology term count
- ambiguous canonicalization report counts

### Entity map outputs

- fingerprint count
- unmapped fingerprint count
- duplicate/final PK reduction behavior

### Relation outputs

- relation count
- relation evidence count
- predicate counts
- relation category counts
- participant entity type pairs

Use exact equality where possible, otherwise use normalized/sorted comparisons.

Output:

- validation script(s)
- per-source parity report

Success criteria:

- UniProt parity established first.
- ChEBI parity established for entity/ontology behavior.
- At least one interaction source parity established before retiring nested fallback for relations.

---

## Step 8: Optimize resolver integration only after normalized extraction is in place

The resolver/canonicalization path is already mostly Polars-based. Do not rewrite it first.

After normalized silver is consumed directly, profile canonicalization again.

Possible later optimizations:

- avoid repeated `.collect()` of resolver mapping tables
- cache mapping frames across sources in a long-running process
- keep resolver input lazy longer
- reduce eager intermediate frames in `_canonicalize_entities()`
- make protein reference expansion lazy/join-based

Output:

- targeted resolver optimization only if profiling shows it dominates after extraction is fixed

Success criteria:

- We avoid premature rewrites of resolver logic.
- Any resolver optimization is justified by post-normalized-silver profiles.

---

## Step 9: Keep combined gold mostly unchanged initially

The combined layer is already columnar compared to the silver/gold extraction layer.

Current combined operations:

```text
scan per-source gold entity/relation files
concat
group/dedup
remap local PKs to global PKs
write combined outputs
```

Initial plan:

- Keep combined logic unchanged.
- Re-run combined after normalized source gold outputs are produced.
- Validate combined row counts and schemas.

Possible later optimizations:

- reduce eager `.collect()` calls
- partition combined outputs
- incremental source-level recomputation
- use DuckDB for larger-than-memory global joins if needed

Output:

- no major combined rewrite in the first implementation phase

Success criteria:

- Normalized silver changes do not require combined schema changes.
- Combined remains a consumer of stable per-source gold packages.