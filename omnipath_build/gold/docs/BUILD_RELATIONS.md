# `build_relations.py`

## Overview

`build_relations.py` is the **second script** in the Option B3 two-pass gold pipeline. It reads per-source silver parquet files and the `entity_map.parquet` produced by `build_entities.py`, then constructs all relations (interactions, memberships, annotations) using the **final canonical entity PKs**.

Because entities are already canonicalized and deduplicated, this script **never rewrites relation tables**. Each relation is written once with the correct PKs. This eliminates the schema thrashing of the old three-step pipeline where relations were first written with temporary PKs, then rewritten with string canonical IDs, then remapped back to final int PKs.

## CLI

```bash
uv run python omnipath_build/gold/build_relations.py \
  --silver-dir    data_v2/silver/corum/1 \
  --entity-map    /tmp/corum/entities/entity_map.parquet \
  --output-dir    /tmp/corum/relations \
  --source-name   corum
```

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--silver-dir` | yes | — | Directory containing `.parquet` silver files (excludes `resource.parquet`) |
| `--entity-map` | yes | — | Path to `entity_map.parquet` from `build_entities.py` |
| `--output-dir` | yes | — | Directory to write `entity_relation.parquet` and `entity_relation_evidence.parquet` |
| `--source-name` | yes | — | Source name used for metadata and the `sources` column |

## High-level pipeline

```
silver parquet files          entity_map.parquet
       |                             |
       v                             v
+------------------+          +------------------+
| 1. Load entity   |          | 2. Stream silver |
|    map into dict |          |    row-by-row    |
|    (fingerprint  |          |    (PyArrow      |
|     -> final PK) |          |     batches)     |
+------------------+          +------------------+
       |                             |
       +-------------+---------------+
                     |
                     v
          +---------------------+
          | 3. Classify row +   |
          |    lookup entity PKs|
          +---------------------+
                     |
         +-----------+-----------+
         |           |           |
         v           v           v
   interaction   membership   annotation
   relation      relation     relation
         |           |           |
         +-----------+-----------+
                     |
                     v
          +---------------------+
          | 4. Deduplicate      |
          |    relations in-mem |
          |    (structural key) |
          +---------------------+
                     |
                     v
          +---------------------+
          | 5. Write evidence   |
          |    rows immediately |
          |    (buffered parquet|
          |     writer)         |
          +---------------------+
                     |
                     v
       entity_relation.parquet
       entity_relation_evidence.parquet
```

---

## Phase 1: Load entity map

The script reads `entity_map.parquet` into a Python dict:

```python
entity_map = {
    "7f9a79664868034e9d8dc9d62ae7c7be": 295,   # fingerprint -> final PK
    "50f5266d5bbacd61f3a1c146ca1c6eac": 289,
    ...
}
```

This is a simple in-memory hash map. For typical sources (up to ~100k entities), it consumes a few MB of RAM.

---

## Phase 2: Stream silver rows

The script iterates over all `.parquet` files in the silver directory (excluding `resource.parquet`) using `pyarrow.parquet.ParquetFile.iter_batches(batch_size=10_000)`. Each batch is converted to Python dicts (`to_pylist()`) and processed row-by-row.

This is the same streaming pattern used by the old `projector.py` and `convert.py`, ensuring bounded memory usage regardless of source size.

---

## Phase 3: Classify and route rows

For each silver row, the script calls `classify_silver_record(row)` and routes to the appropriate handler:

| Record class | Handler | Notes |
|--------------|---------|-------|
| `ignored` | Skip | No-op |
| `ontology_term_only` | Skip | Ontology terms were already extracted in `build_entities.py` |
| `interaction_relation` | `_project_interaction()` | Does **not** materialize a parent entity; only members become relation endpoints |
| `membership_relation` | `_project_memberships()` | Parent entity + each member |
| `entity_only` | `_emit_annotation_relations()` | Standalone entity; only annotations may produce relations |
| `entity_with_ontology_backing` | `_project_memberships()` + `_emit_annotation_relations()` | Both memberships and annotations |

### Entity lookup

Every entity reference (parent or member) is resolved through `entity_occurrence_map.parquet`:

1. Read the occurrence ID from `entity_occurrence.parquet` or `membership.parquet`.
2. Look up `occurrence_id -> entity_pk` from `entity_occurrence_map.parquet`.
3. If missing, skip that relation endpoint.

This is O(1) per lookup and avoids reconstructing nested silver rows during relation building.

---

## Phase 4: Project interactions

Entry point: `_project_interaction()`

### What triggers it

Rows where `type` is one of:
- `OM:0013` (Interaction)
- `MI:0217` (Reaction)
- `MI:0220` (Catalysis)
- `MI:0221` (Control)
- `MI:0222` (Degradation)

And the row has at least one `membership`.

### Member processing

For each `membership` in the row:
1. Extract the `member` sub-row
2. Classify it (fallback to `entity_only` if `ignored`)
3. Look up its final PK via fingerprint
4. Emit annotation relations for the member's own annotations
5. Collect the member PK + membership annotations

### Participant ordering

Members are passed to `order_interaction_participants()` which inspects role annotations (`SOURCE`, `TARGET`) within each member's membership annotations to determine which member is "subject" and which is "object".

If exactly 2 ordered participants exist, the interaction proceeds. Otherwise it is skipped.

### Predicate selection

`predicate_for_interaction(row, ordered_participants)` determines the predicate based on:
- Row type (interaction → `interacts_with`, catalysis → `positively_regulates`, etc.)
- Sign annotations (positive/negative)
- Role ordering (transforms_to vs affects for reactions)

### Attribute classification

Annotations are classified into four buckets using `collect_attributes()` with appropriate `AnnotationContext`:

| Bucket | Context | Source annotations |
|--------|---------|-------------------|
| `record_attributes` | `participant_side='record'` | Row-level annotations |
| `subject_attributes` | `participant_side='subject'` | Membership annotations of subject member |
| `object_attributes` | `participant_side='object'` | Membership annotations of object member |
| `evidence` | `participant_side='record'` | Row-level + both members' membership annotations |

### Evidence merging

Evidence from multiple sources is merged into a single list:

```
evidence = row_evidence + subject_membership_evidence + object_membership_evidence
```

---

## Phase 5: Project memberships

Entry point: `_project_memberships(parent_pk, row)`

### What triggers it

Any row with `membership` arrays regardless of type.

Membership edges are projected as broad associations:
- Complex / protein family / default membership → predicate `has_member`
- Pathway / reaction membership → predicate `has_participant`
- relation category `association`

### Parent evidence

Parent-level annotations are collected as `evidence` (not attributes) and will be attached to every membership relation produced from this row.

### Per-membership processing

For each `membership`:
1. Extract the `member` sub-row
2. Classify and look up its final PK
3. Emit annotation relations for the member
4. Determine subject/object orientation:
   - If `membership.is_parent == True`: member is subject, parent is object
   - Otherwise: parent is subject, member is object
5. Determine predicate via `predicate_for_membership(parent_type, membership)`
6. Classify membership annotations into `subject_attributes`, `object_attributes`, and `evidence`
7. Merge parent evidence with membership evidence

---

## Phase 6: Emit annotation relations

Entry point: `_emit_annotation_relations(entity_pk, annotations, record_class)`

### What triggers it

After processing any non-ignored, non-ontology-only entity (parent or member). It inspects the entity's own annotations for ontology term references that should become relations.

### Annotation classification

For each annotation:
1. `classify_annotation()` returns a bucket
2. If bucket is `annotation_relation`, check `materialize_ontology_object()`
3. If the ontology object should be materialized, extract its fingerprint
4. Look up the ontology entity's final PK in `entity_map`
5. Build a relation with predicate from `annotation_predicate()`:
   - REACTOME / WP → `involved_in`
   - Default, including GO / HP / MONDO → `associated_with`

### Relation category

All annotation-style relations have `relation_category = 'association'`.

---

## Phase 7: Write relation evidence

Entry point: `_write_relation_evidence()`

This is called by all three relation builders (interaction, membership, annotation) and handles both the evidence row and the relation aggregation.

### Structural key deduplication

Relations are deduplicated by a tuple key:

```python
key = (subject_entity_pk, predicate, object_entity_pk, relation_category)
```

The first time a key is seen, a new relation row is created with:
- `relation_pk` — auto-incremented 1-indexed
- `evidence_count = 0`
- `sources = set()`

Each subsequent evidence row for the same key increments `evidence_count` and adds the source to `sources`.

### Evidence row writing

Every call to `_write_relation_evidence()` writes one evidence row immediately to the `BufferedParquetWriter`:

```python
{
    'source': source_name,
    'relation_evidence_pk': auto_increment,
    'relation_pk': relation_row['relation_pk'],
    'subject_entity_pk': subject_entity_pk,
    'predicate': predicate,
    'object_entity_pk': object_entity_pk,
    'relation_category': relation_category,
    'record_attributes': [...],
    'subject_attributes': [...],
    'object_attributes': [...],
    'evidence': [...],
}
```

This ensures evidence rows are written in the order they are encountered, without buffering all of them in memory.

### Relation row deferral

Relation rows (the aggregated, deduplicated view) are **not** written immediately. They are accumulated in `self.relation_index` (a dict keyed by the structural tuple). At `close()` time, all relation rows are sorted by `relation_pk` and flushed to the parquet writer.

This is necessary because `evidence_count` and `sources` are not known until all evidence rows have been processed.

---

## Phase 8: Close and flush

Entry point: `RelationBuilder.close()`

1. Sort all accumulated relation rows by `relation_pk`
2. Write each to `entity_relations` (the `BufferedParquetWriter`)
3. Flush both writers (`entity_relation_evidence` and `entity_relations`)
4. If a writer never received rows, delete the empty file

The `try/finally` in `build_relations()` ensures writers are always closed, even if an exception occurs mid-stream.

---

## Output files

### `entity_relation.parquet`

Aggregated, deduplicated relation table:

```
relation_pk         int64
subject_entity_pk   int64
predicate           string
object_entity_pk    int64
relation_category   string
evidence_count      int64
sources             list<string>
```

- `relation_pk` is contiguous 1-indexed
- `evidence_count` is the number of evidence rows for this structural key
- `sources` is a deduplicated, sorted list of contributing source names

### `entity_relation_evidence.parquet`

One row per evidence (i.e., per original silver row / membership that produced a relation):

```
source                  string
relation_evidence_pk    int64
relation_pk             int64
subject_entity_pk       int64
predicate               string
object_entity_pk        int64
relation_category       string
record_attributes       list<struct{term, value, unit}>
subject_attributes      list<struct{term, value, unit}>
object_attributes       list<struct{term, value, unit}>
evidence                list<struct{term, value, unit}>
```

- `relation_evidence_pk` is contiguous 1-indexed across all evidence rows
- `relation_pk` is a FK to `entity_relation.parquet`
- Attribute columns are `null` when no annotations matched that bucket

---

## Key design decisions

### Why row-wise Python for classification?

Annotation classification (whether an annotation is `evidence`, `record_attribute`, `subject_attribute`, `object_attribute`, or `annotation_relation`) is inherently semantic. It depends on:
- The record class (interaction vs membership vs entity-only)
- The participant side (subject vs object vs record)
- The annotation term (role, stoichiometry, taxonomy, pubmed, etc.)

This is a complex decision tree that maps poorly to vectorized Polars expressions. Row-wise Python makes the logic clear, testable, and maintainable.

### Why streaming instead of loading all silver into memory?

For large sources like UniProt (700k+ relations), loading all silver data into a Polars DataFrame would consume significant RAM. Streaming via PyArrow batches keeps memory bounded at ~batch_size rows.

### Why deduplicate relations in-memory?

Relation deduplication requires knowing whether `(subject_pk, predicate, object_pk, category)` has been seen before. A Python dict provides O(1) lookup and is the simplest correct implementation. The number of unique relations is typically much smaller than the number of evidence rows (e.g., UniProt: 713k relations, 713k evidence rows — no dedup needed; wikipathways: 305k evidence → 139k relations — moderate dedup).

### Why not use Polars for the relation aggregation?

We could buffer all evidence rows into a Polars DataFrame and then `group_by().agg()`. However:
- Evidence rows are large (11 columns including nested structs)
- Buffering 700k+ rows in Python dicts is already memory-heavy
- The in-memory dict approach lets us write evidence rows immediately, keeping peak memory lower

For sources with millions of evidence rows, a future optimization could switch to a two-phase approach (buffer to temp parquet, then aggregate with Polars).

---

## Why this design

| Concern | How it's handled |
|---------|-----------------|
| **Correctness** | Relations built once with final PKs; no remapping |
| **Memory** | Streaming silver; only relation index in memory |
| **Speed** | O(1) entity lookup; O(1) relation dedup per evidence row |
| **Maintainability** | Same semantic classification logic as old `projector.py` |
| **Testability** | Each relation type (interaction, membership, annotation) is a separate method |
| **No schema thrashing** | Relations never change schema; PKs are final from the start |

## Relationship to old pipeline

| Old step | New equivalent |
|----------|---------------|
| `projector.py` relation building | **This entire script** |
| `canonicalize_projector.py` relation rewriting | **Not needed** — relations use final PKs from the start |
| `dedup_projector.py` relation dedup | `_write_relation_evidence()` in-memory dedup |

The key improvement: **zero relation rewrites**. In the old pipeline:
1. `projector.py` wrote relations with temporary int PKs
2. `canonicalize_projector.py` rewrote them with string canonical IDs
3. `dedup_projector.py` rewrote them back to final int PKs

In the new pipeline, relations are written **once** with the correct final int PKs.

## Error handling

### Missing entity in map

If `_lookup_entity_pk()` cannot find a fingerprint in `entity_map`, it prints:

```
[source] WARNING: entity not found in map: 7f9a7966... (MI:0326:Protein)
```

This should be rare. It typically means:
- The fingerprint computation diverged between `build_entities.py` and `build_relations.py` (bug)
- An entity was filtered out during canonicalization (e.g., ambiguous entity with no fallback)

### Empty output

If no relations are produced, both parquet files are deleted by `BufferedParquetWriter.close()` rather than leaving empty files.
