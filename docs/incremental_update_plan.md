# Incremental build/update plan

## Goal

Move from snapshot-style rebuilds to a provenance-aware incremental system where raw input changes are classified as added, removed, changed, or unchanged records, and only affected downstream rows are recomputed and updated.

## Target architecture

```text
raw snapshots
  → fast raw-record index
  → raw-record diff: added / removed / changed / unchanged
  → incremental silver rows
  → source contribution rows
  → combined aggregate rows
  → PostgreSQL transactional diff/apply
  → selective derived artifact refresh
```

The database should behave like a deterministic materialized view over immutable raw snapshots.

## Core principles

1. Every raw record has a stable key and content hash.
2. Every silver/gold/combined row has provenance back to raw records.
3. Entity, relation, evidence, annotation, and resource rows use stable semantic keys, not sequential PKs, as identity.
4. Outputs include row hashes so unchanged rows can be skipped cheaply.
5. Combined tables are aggregates over source contribution rows.
6. PostgreSQL updates happen transactionally by delete/insert/update diffs.

## 1. Raw snapshots

Each downloaded file/API response is stored or referenced as an immutable snapshot.

Manifest fields should include:

```text
source
dataset
snapshot_id
url / request metadata
retrieved_at
etag
last_modified
content_sha256
parser_version
source_declared_version, if available
```

If a raw snapshot is byte-identical to the previous snapshot, no downstream work is needed.

## 2. Fast raw-record pre-parse

Before full normalization, run a lightweight parser that emits only record identity and hashes:

```text
source
dataset
snapshot_id
raw_record_key
raw_record_hash
optional source_native_id
optional byte offset / partition / line number
```

Diff the new raw-record index against the previous one:

```text
same key + same hash      → unchanged
same key + different hash → changed
key only in new snapshot  → added
key only in old snapshot  → removed
```

Preferred key:

```text
raw_record_key = source native row/record ID
```

Fallback key:

```text
raw_record_key = hash(source, dataset, normalized identifying fields)
```

If no stable native ID exists, a modified record may appear as one removed record plus one added record. That is acceptable.

For many file sources, this step may still scan the whole file, but it avoids expensive downstream work for unchanged records.

## 3. Full pre-parse as the standard input mode

Every source update starts by running the full raw-record pre-parse for that source/dataset. This is the single standard mode for change detection.

The pre-parse may read the whole file/API response, but it should do only cheap work:

```text
record boundary detection
stable raw_record_key extraction
normalized raw_record_hash computation
minimal metadata capture
```

It should not perform expensive normalization, identifier resolution, object construction, canonicalization, or relation building.

This keeps the incremental model uniform across sources:

```text
always pre-parse all raw records
always diff raw_record_key + raw_record_hash
only process added/removed/changed records downstream
```

Source-specific optimizations such as API delta fetching or partition-level skipping can be added later only as implementation details, but they should still produce the same complete raw-record index for comparison.

## 4. Incremental silver

Silver rows should carry raw-record provenance:

```text
silver_row_key
silver_row_hash
source
dataset
raw_record_key
raw_record_hash
snapshot_id
silver_table_name
transform_version
```

Update rule:

```text
removed raw records → delete old silver rows for those raw_record_keys
changed raw records → delete old silver rows, insert newly generated rows
added raw records   → insert newly generated rows
unchanged records   → do nothing
```

Current silver tables can remain conceptually similar:

```text
entity_occurrence
entity_identifier
entity_annotation
membership
membership_annotation
```

but each row should include provenance and stable row keys.

## 5. Source contribution layer

Gold should be split into source-level contribution rows and aggregate rows.

Contribution examples:

```text
source_entity_contribution
source_relation_contribution
source_relation_evidence_contribution
source_annotation_contribution
```

Each contribution row should include:

```text
contribution_key
row_hash
source
dataset
raw_record_key
silver_row_key, where applicable
resolver_version
transform_version
```

Stable semantic keys:

```text
entity_key   = hash(canonical_identifier_type, canonical_identifier)
relation_key = hash(subject_entity_key, predicate, object_entity_key, relation_category)
evidence_key = hash(source, dataset, raw_record_key, relation_key, evidence_payload)
resource_key = hash(source/resource identifier)
```

Integer `entity_pk` and `relation_pk` may remain as compatibility/surrogate fields, but should not be used as primary identity for incremental updates.

## 6. Handling removed and changed records

Removed records require historical output state.

For removed or changed raw records:

1. Look up existing contribution rows by `source`, `dataset`, `raw_record_key`.
2. Capture affected `entity_key`, `relation_key`, `evidence_key`, and annotation keys.
3. Delete old contribution rows.
4. For changed records, generate and insert new contribution rows.
5. Recompute only affected aggregate keys.

This is why contribution rows must be persisted, not just final aggregates.

## 7. Combined aggregates

Combined tables should be materialized aggregates over source contributions.

Examples:

```text
combined_entity(entity_key)
combined_relation(relation_key)
combined_relation_evidence(evidence_key)
relation_annotation_term(annotation_key)
resources(resource_key)
```

Update rule:

```text
affected_entity_keys   = entity keys touched by deleted/inserted contributions
affected_relation_keys = relation keys touched by deleted/inserted contributions

recompute combined_entity only for affected_entity_keys
recompute combined_relation only for affected_relation_keys
delete aggregate rows with no remaining contributions
upsert changed aggregate rows using row_hash
```

A full combined rebuild can remain as a validation/debug fallback.

## 8. Resolver changes

Resolver mappings are global and can change canonical IDs, entity keys, relation keys, and evidence keys.

Every contribution should record resolver mapping version/hash.

Initial policy:

```text
resolver mapping changed → rebuild all gold contributions and combined aggregates
```

Future policy:

```text
resolver mapping changed → identify affected namespaces/identifiers → rebuild only affected records/sources
```

## 9. PostgreSQL update model

PostgreSQL should be updated transactionally from changed combined rows.

Base table strategy:

1. Load changed rows into staging tables.
2. Delete live rows whose stable keys disappeared.
3. Insert new stable keys.
4. Update existing keys where `row_hash` changed.
5. Commit atomically.

Diff rule:

```text
same key + same row_hash      → unchanged
same key + different row_hash → update
key only in staging/new state → insert
key only in live/old state    → delete
```

Tables should include stable keys and row hashes:

```text
entity.entity_key, entity.row_hash
entity_relation.relation_key, entity_relation.row_hash
entity_relation_evidence.evidence_key, entity_relation_evidence.row_hash
relation_annotation_term.annotation_key, relation_annotation_term.row_hash
resources.resource_key, resources.row_hash
```

## 10. Derived artifacts

Initial policy:

```text
incrementally update base tables
rebuild/refresh materialized views
rebuild bitmap tables
keep indexes in place
```

Future policy:

```text
refresh only derived partitions affected by changed entity/relation/source/predicate keys
```

Useful partition dimensions:

```text
source
predicate
relation_category
entity_type
taxon
```

## 11. Declarative transformations

Where possible, transformations should be expressed as declarative models, ideally SQL/DuckDB-backed:

```text
raw_record_index
silver_* views/tables
source_contribution views/tables
combined aggregate views/tables
```

DuckDB views alone do not provide automatic incremental view maintenance, but DuckDB can efficiently execute targeted recomputation when given affected keys.

The orchestrator should manage:

```text
raw-record diffs
affected key sets
targeted recomputation
MERGE/delete/upsert into persisted tables
PostgreSQL diff/apply
```

## 12. Update algorithm

For a source update:

```text
1. Fetch/store new raw snapshot.
2. Run full fast pre-parse to build the complete raw-record index.
3. Diff against previous raw-record index.
4. Determine added, removed, changed, unchanged raw records.
5. For removed/changed records, load old silver/contribution rows.
6. Parse/map only added/changed records where possible.
7. Delete old silver/contribution rows for removed/changed records.
8. Insert new silver/contribution rows for added/changed records.
9. Compute affected entity/relation/evidence/annotation/resource keys.
10. Recompute only affected combined aggregate rows.
11. Apply PostgreSQL deletes/inserts/updates in one transaction.
12. Refresh affected derived artifacts, or rebuild them initially.
13. Write build/update manifest.
```

## Current implementation status

Implemented first raw/silver provenance pieces:

- Bronze preparse materializes parser records with `_raw_record_key` and compact stable `_raw_record_id`.
- `_raw_record_id` is reused for unchanged keys; added keys get `max(previous_id) + n`.
- `delta.parquet` contains `_raw_record_key`, `_raw_record_id`, and `_change_type` (`added`/`removed`).
- Full `records.parquet` is temporary during processing, then accepted into one mutable state file:

```text
bronze/<source>/<dataset>/state/records.parquet
```

- Snapshot directories retain only compact incremental artifacts:

```text
bronze/<source>/<dataset>/<snapshot_id>/
  delta.parquet
  manifest.json
```

- Silver tables carry `_raw_record_id` instead of repeated raw key/snapshot provenance.
- Deterministic silver IDs use `_raw_record_id`, e.g. `interactions:32162:parent`.
- Removed obsolete `row_number` and `record_class_hint` from `entity_occurrence`.
- If a new preparse delta is empty and silver tables already exist, silver rewrite is skipped; the new bronze snapshot is still accepted.
- Verified on `connectomedb.interactions` under `new_incremental_test/`.

## 13. Implementation phases

### Phase 1: stable keys, row hashes, manifests

Add stable keys and row hashes to combined artifacts and PostgreSQL base tables. Add source manifests with raw input, code, resolver, schema, and output hashes.

### Phase 2: raw-record index

Add full fast pre-parse for every source/dataset and persist complete raw-record indexes. Use them to classify records as added, removed, changed, or unchanged.

### Phase 3: contribution layer

Introduce persisted source contribution tables with raw-record provenance. Combined outputs become aggregates over contributions.

### Phase 4: incremental combined recomputation

Use affected key sets to recompute only impacted combined entities, relations, evidence, annotations, and resources.

### Phase 5: incremental PostgreSQL loader

Add PostgreSQL modes:

```bash
--postgres-mode full-reload
--postgres-mode incremental
```

Incremental mode applies stable-key/row-hash diffs transactionally.

### Phase 6: selective derived refresh

Make materialized views and bitmap tables incrementally or partition-refreshable.

## Summary

The full incremental design is:

```text
fast raw-record diff
  → process only added/removed/changed records
  → persist source contribution rows with provenance
  → recompute only affected combined aggregates
  → update PostgreSQL by stable-key row diffs
```

This is the long-term design. The standard entry point is always a complete raw-record pre-parse followed by downstream processing only for added, removed, or changed records.
