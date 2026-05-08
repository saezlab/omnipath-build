# Plan: `new_combine.py` — Cross-source Combine for B3 Pipeline

## Background

The old `combined.py` merged per-source gold outputs into a single warehouse. It had to handle the old split schema:

- `entity.parquet` → combined `entity.parquet`
- `interaction.parquet` + `interaction_evidence.parquet` → combined interaction tables
- `association.parquet` + `association_evidence.parquet` → combined association tables
- `entity_annotation.parquet` + `interaction_annotation.parquet` → combined annotation tables

The B3 pipeline produces a **unified schema** with fewer, cleaner tables. The combine step becomes simpler as a result.

---

## Input changes

### Old input (per source)

```
data_v2/gold/<source>/<version>/
  entity.parquet
  interaction.parquet
  interaction_evidence.parquet
  association.parquet
  association_evidence.parquet
  entity_annotation.parquet
  interaction_annotation.parquet
```

### New input (per source)

```
data_v2/gold_new/<source>/
  entities/
    entity.parquet
    entity_map.parquet          # not needed for combine
    ontology_term.parquet       # optional
  relations/
    entity_relation.parquet
    entity_relation_evidence.parquet
```

### Per-source discovery

Old: scan for `entity.parquet` anywhere under `data_v2/gold/`<br>
New: scan for `entity.parquet` under `data_v2/gold_new/<source>/entities/`

Sources are discovered by the presence of `entities/entity.parquet`.

---

## Output changes

### Old output

```
data_v2/combined/
  entity.parquet
  interaction.parquet
  interaction_evidence.parquet
  association.parquet
  association_evidence.parquet
  entity_annotation.parquet
  interaction_annotation.parquet
  ontologies/...
  combined_build_summary.json
```

### New output

```
data_v2/combined_new/
  entity.parquet
  entity_relation.parquet
  entity_relation_evidence.parquet
  ontology_term.parquet           # optional
  combined_build_summary.json
```

**4 files instead of 7.** The unified relation schema collapses interactions, associations, and annotations into one table.

---

## Step-by-step algorithm

### Step 1: Discover sources

```python
for source_dir in gold_new_root.iterdir():
    entity_path = source_dir / 'entities' / 'entity.parquet'
    if entity_path.exists():
        sources.append(source_dir.name)
```

### Step 2: Combine entities

Identical to old `combined.py`:

```
For each source:
  Read entity.parquet
  Add _source and _local_entity_pk columns

Group by (canonical_identifier, canonical_identifier_type):
  entity_type: first non-null
  taxonomy_id: first non-null
  entity_attributes: first non-null
  sources: merge unique lists
  identifiers: explode + dedup + re-list

Assign global entity_pk (1-indexed)
Build entity_pk_map: (_source, _local_entity_pk) -> entity_pk
```

**No change in logic.** The entity schema is identical between old and new pipelines.

### Step 3: Combine relations

This replaces both `_build_interaction()` and `_build_association()` from the old combined.py.

```
For each source:
  Read relations/entity_relation.parquet
  Add _source and _local_relation_pk columns
  Join entity_pk_map on subject_entity_pk -> global_subject_pk
  Join entity_pk_map on object_entity_pk -> global_object_pk

Group by (global_subject_pk, predicate, global_object_pk, relation_category):
  evidence_count: sum
  sources: merge unique lists

Assign global relation_pk (1-indexed)
Build relation_pk_map: (_source, _local_relation_pk) -> relation_pk
```

**Key differences from old:**
- Single pass instead of two (interaction + association)
- Group key includes `predicate` and `relation_category` instead of `direction`/`sign` or `role_term_id`/`stoichiometry`
- `evidence_count` is summed across sources

### Step 4: Combine relation evidence

Replaces both `_build_interaction_evidence()` and `_build_association_evidence()`.

```
For each source:
  Read relations/entity_relation_evidence.parquet
  Add _source and _local_relation_pk columns
  Join entity_pk_map on subject_entity_pk -> global_subject_pk
  Join entity_pk_map on object_entity_pk -> global_object_pk
  Join relation_pk_map on (_source, _local_relation_pk) -> relation_pk

Select and write
```

**Key differences from old:**
- Single evidence table with `record_attributes`, `subject_attributes`, `object_attributes`
- No `direction`/`sign` fields
- No separate parent/member attribute columns (renamed to subject/object in unified schema)

### Step 5: Combine ontology terms (optional)

```
For each source:
  Read entities/ontology_term.parquet (if exists)

Group by term_id:
  ontology_prefix: first non-null
  label: first non-null
  definition: first non-null
  synonyms: merge unique lists
  sources: merge unique lists
```

---

## Schema definitions

### `entity.parquet` (unchanged)

```python
ENTITY_SCHEMA = {
    'entity_pk': pl.Int64,
    'canonical_identifier': pl.String,
    'canonical_identifier_type': pl.String,
    'identifiers': pl.List(pl.Struct({'identifier': pl.String, 'identifier_type': pl.String})),
    'entity_type': pl.String,
    'taxonomy_id': pl.String,
    'entity_attributes': pl.List(pl.Struct({'term': pl.String, 'value': pl.String, 'unit': pl.String})),
    'sources': pl.List(pl.String),
}
```

### `entity_relation.parquet` (new combined schema)

```python
ENTITY_RELATION_SCHEMA = {
    'relation_pk': pl.Int64,
    'subject_entity_pk': pl.Int64,
    'predicate': pl.String,
    'object_entity_pk': pl.Int64,
    'relation_category': pl.String,
    'evidence_count': pl.Int64,
    'sources': pl.List(pl.String),
}
```

### `entity_relation_evidence.parquet` (new combined schema)

```python
ENTITY_RELATION_EVIDENCE_SCHEMA = {
    'source': pl.String,
    'relation_evidence_pk': pl.Int64,
    'relation_pk': pl.Int64,
    'subject_entity_pk': pl.Int64,
    'predicate': pl.String,
    'object_entity_pk': pl.Int64,
    'relation_category': pl.String,
    'record_attributes': pl.List(pl.Struct({'term': pl.String, 'value': pl.String, 'unit': pl.String})),
    'subject_attributes': pl.List(pl.Struct({'term': pl.String, 'value': pl.String, 'unit': pl.String})),
    'object_attributes': pl.List(pl.Struct({'term': pl.String, 'value': pl.String, 'unit': pl.String})),
    'evidence': pl.List(pl.Struct({'term': pl.String, 'value': pl.String, 'unit': pl.String})),
}
```

### `ontology_term.parquet` (optional)

```python
ONTOLOGY_TERM_SCHEMA = {
    'term_id': pl.String,
    'ontology_prefix': pl.String,
    'label': pl.String,
    'definition': pl.String,
    'synonyms': pl.List(pl.String),
    'sources': pl.List(pl.String),
}
```

---

## What is dropped vs old combined.py

| Old table | New equivalent | Reason |
|-----------|---------------|--------|
| `interaction.parquet` | `entity_relation.parquet` (category='interaction') | Unified schema |
| `association.parquet` | `entity_relation.parquet` (category='association') | Unified schema |
| `interaction_evidence.parquet` | `entity_relation_evidence.parquet` | Unified schema |
| `association_evidence.parquet` | `entity_relation_evidence.parquet` | Unified schema |
| `entity_annotation.parquet` | `entity_relation.parquet` (category='association') | Annotations are now relations |
| `interaction_annotation.parquet` | `entity_relation.parquet` (category='association') | Annotations are now relations |

---

## Edge cases

### Missing relation files

Some sources (e.g. `swisslipids`) produce entities but zero relations. The combine step should gracefully handle missing `relations/entity_relation.parquet` and `relations/entity_relation_evidence.parquet` by skipping that source for those tables.

### Missing ontology terms

Most sources don't produce `ontology_term.parquet`. If no sources have it, the combined file should not be written (or an empty frame should be written).

### Entity PK map coverage

Every entity referenced in relations must exist in the entity table. If a relation references an entity that was filtered out during entity combine (shouldn't happen, but defensive), the join will drop that relation row. This is the same behavior as the old combined.py.

### Evidence rows exceeding relation count

In the per-source pipeline, evidence_count on relations is the count of evidence rows for that structural key. In the combine step, evidence_count should be summed. The actual evidence rows should be deduplicated by all columns (since the same evidence might appear in multiple sources, though that's unlikely for per-source data).

---

## Why this is simpler

| Aspect | Old combined.py | New new_combine.py |
|--------|-----------------|-------------------|
| Tables to merge | 7 | 4 (or 3 if no ontology) |
| Entity join passes | 2 (interaction + association) | 1 (relations) |
| Evidence tables | 2 | 1 |
| Annotation tables | 2 | 0 (merged into relations) |
| Lines of code | ~578 | estimated ~350 |

---

## Implementation strategy

### Option A: Refactor `combined.py` in place

Add conditional logic to detect whether inputs are old-style or new-style, and branch accordingly. This keeps one file but adds complexity.

### Option B: Write `new_combine.py` from scratch (recommended)

Write a clean, standalone script that only understands the B3 schema. This avoids branching logic and makes the code easier to understand and maintain.

The old `combined.py` can remain untouched for backward compatibility with the old pipeline.

### Reuse plan

Functions that can be copied almost verbatim from `combined.py`:
- `discover_gold_source_dirs()` — adapted path pattern
- `_scan_source_artifact()` — unchanged
- `_build_entity()` — **unchanged** (entity schema is identical)
- `_write_if_nonempty()` — unchanged

Functions that need new implementation:
- `_build_relation()` — replaces `_build_interaction()` + `_build_association()`
- `_build_relation_evidence()` — replaces `_build_interaction_evidence()` + `_build_association_evidence()`
- `_build_ontology_terms()` — new

Functions that are **deleted**:
- `_build_entity_annotation()` — no longer needed
- `_build_interaction_annotation()` — no longer needed
- `_build_interaction()` — replaced
- `_build_association()` — replaced
- `_build_interaction_evidence()` — replaced
- `_build_association_evidence()` — replaced

---

## Orchestrator integration (`b3_pipeline.py`)

The combine step should be an optional but default-on **third step** in the orchestrator, after all per-source pipelines have finished.

### Why in the orchestrator?

- **Convenience**: one command runs the full pipeline end-to-end
- **Correctness**: combine is always run on the freshest gold outputs
- **Fail-fast**: if a source fails, the orchestrator can skip combine or fail early

### Proposed CLI changes

Add two new flags to `b3_pipeline.py`:

| Flag | Default | Description |
|------|---------|-------------|
| `--combine` | enabled | Run the combine step after all sources |
| `--combined-output-dir` | `<output-root>/../combined_new` | Where to write combined artifacts |

### Pseudocode for orchestrator flow

```python
def run_all_sources(silver_root, mapping_dir, output_root, combined_output_dir=None, combine=True):
    # Step 1: process all per-source pipelines
    results = []
    for source in discover_sources(silver_root):
        result = run_b3_pipeline(source=source, ...)
        results.append(result)

    # Step 2: combine (optional, default on)
    if combine:
        print('\n========== COMBINE ==========')
        try:
            from omnipath_build.gold.new_combine import build_combined_parquets
            combine_summary = build_combined_parquets(
                gold_root=output_root,
                output_dir=combined_output_dir or (output_root.parent / 'combined_new'),
            )
            print(f'Combine complete: {combine_summary["row_counts"]}')
        except Exception as e:
            print(f'COMBINE ERROR: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()

    return results
```

### Why a separate `new_combine.py` import?

- Keeps the combine logic standalone and testable
- `b3_pipeline.py` doesn't bloat with combine internals
- Users can run `new_combine.py` independently if they want to re-combine without re-running per-source pipelines

### Error handling strategy

- If **any source fails**, the orchestrator should still attempt combine on the sources that succeeded (partial combine is better than no combine)
- If **combine itself fails**, it should not fail the entire orchestrator run — log the error and return the per-source results
- If **no sources succeeded**, skip combine entirely

### Example output structure after orchestrator run

```
data_v2/gold_new/
  chebi/
    entities/entity.parquet
    relations/entity_relation.parquet
    ...
  corum/
    ...
  ...

data_v2/combined_new/
  entity.parquet
  entity_relation.parquet
  entity_relation_evidence.parquet
  ontology_term.parquet
  combined_build_summary.json
```
