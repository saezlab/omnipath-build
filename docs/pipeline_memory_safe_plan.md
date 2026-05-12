Below is a concise developer implementation brief.

# Implementation instructions: bounded-memory bronze, silver, gold + combined

## 0. Apply the memory boundary from bronze onwards

The same invariant must hold for **bronze and silver**, not just gold/combined:

> every expensive join, anti-join, distinct, group, sort, id-assignment, and state rewrite must happen inside an explicit bucket or part boundary.

Bronze and silver may stream parser output row-by-row, but their snapshot comparison, raw-record ID assignment, state carry-forward, and table rewrites can still become global-memory operations if they read all old/new records at once.

## 1. Use two levels of partitioning

Do **not** make one physical Parquet partition per tiny logical bucket. Use:

```text
logical bucket = memory boundary
physical part  = file-count boundary
```

Example:

```text
logical_bucket_count = 4096
physical_part_count  = 128
buckets_per_part     = 32

bucket = stable_u64(key) % logical_bucket_count
part   = floor(bucket / buckets_per_part)
```

Use logical buckets when processing:

```text
WHERE bucket = ?
```

Use physical parts when storing:

```text
root/part=00000/data.parquet
root/part=00001/data.parquet
...
```

This keeps memory bounded by `bucket`, while keeping file count bounded by `part`.

Important: DuckDB Hive-style partitioned writes create a directory dataset, not one physical file, and can write one file per thread per partition directory. DuckDB also warns that many small partitions are expensive and recommends at least about 100 MB per partition. So final outputs should be compacted to **one file per physical part** where practical. ([DuckDB][1])

---

## 2. Add stable bucket columns everywhere from bronze onwards

Every intermediate and final bronze/silver/gold/combined table should carry the relevant bucket columns.

Use stable hash functions based on existing business keys, not version-dependent runtime hashes.

```text
raw_record_bucket  = stable_u64(_raw_record_key) % raw_record_bucket_count
raw_record_part    = floor(raw_record_bucket / buckets_per_raw_record_part)

occ_bucket         = stable_u64(occurrence_id) % occ_bucket_count
occ_part           = floor(occ_bucket / buckets_per_occ_part)

parent_bucket      = stable_u64(parent_occurrence_id) % parent_bucket_count
parent_part        = floor(parent_bucket / buckets_per_parent_part)

member_bucket      = stable_u64(member_occurrence_id) % member_bucket_count
member_part        = floor(member_bucket / buckets_per_member_part)

entity_bucket      = stable_u64(entity_key) % entity_bucket_count
entity_part        = floor(entity_bucket / buckets_per_entity_part)

relation_bucket    = stable_u64(relation_key) % relation_bucket_count
relation_part      = floor(relation_bucket / buckets_per_relation_part)

identifier_bucket  = stable_u64(identifier_type | identifier | taxonomy_id) % identifier_bucket_count
identifier_part    = floor(identifier_bucket / buckets_per_identifier_part)
```

Store the bucket algorithm and bucket counts in every manifest.

---

## 3. Use DuckDB for execution, Parquet for storage

Set DuckDB resource settings at the start of every bronze/silver/gold/combined task:

```sql
SET memory_limit = '16GB';
SET temp_directory = '/fast-ssd/omnipath-duckdb-tmp';
SET max_temp_directory_size = '500GB';
SET threads = 4;
SET preserve_insertion_order = false;
SET partitioned_write_max_open_files = 64;
```

Tune these from config.

DuckDB supports setting `memory_limit`, `threads`, `temp_directory`, and `max_temp_directory_size`; it also spills larger-than-memory intermediates to the configured temp directory when possible. However, DuckDB warns that some memory is outside the buffer manager, including vectors and complex aggregate states, so the pipeline should still avoid giant global joins, sorts, and list aggregations. ([DuckDB][2])

---

## 4. Implement one shared Parquet writer helper

Create a helper like:

```python
write_compact_part_files(
    con,
    query_sql,
    output_root,
    part_col,
    bucket_col,
    key_col,
    affected_parts=None,
)
```

For each physical part:

```sql
COPY (
    SELECT *
    FROM ({query_sql})
    WHERE part = {part}
    ORDER BY bucket, key
)
TO 'output_root/part=00042/data.parquet'
(
    FORMAT parquet,
    COMPRESSION zstd,
    ROW_GROUP_SIZE 100000
);
```

Write to a temporary path first:

```text
_output_tmp/<run_id>/part=00042/data.parquet
```

Then atomically replace:

```text
output_root/part=00042/
```

Do not rely on appending small files forever. Parquet is immutable; updates should rewrite affected physical parts.

DuckDB can read and write Parquet efficiently, including projection and filter pushdown. It also supports `COPY ... TO ... (FORMAT parquet, ROW_GROUP_SIZE ...)`. ([DuckDB][3])

---

# Bronze layer

## 5. Write raw records as bounded part files

Parser output should still be streamed in batches, but the persisted bronze state should not be one monolithic `records.parquet` for large datasets.

Output layout:

```text
data/bronze/<source>/<dataset>/state/records/part=00000/data.parquet
...
data/bronze/<source>/<dataset>/<snapshot_id>/delta/part=00000/data.parquet
```

Each raw row must include:

```text
_source
_dataset
_snapshot_id
_raw_record_key
_raw_record_id
raw_record_bucket
raw_record_part
```

Write temporary unassigned records by `raw_record_part`, then process one `raw_record_bucket` at a time for ID assignment and delta computation.

---

## 6. Assign `_raw_record_id` from a raw-key registry

Do not assign raw IDs with a global query like:

```sql
row_number() over (order by _raw_record_key)
```

Use a persistent registry:

```text
raw_record_key_registry
  _raw_record_key
  _raw_record_id
  first_seen_snapshot_id
  active
  raw_record_bucket
  raw_record_part
```

For each affected `raw_record_bucket`:

```text
read old registry rows for this bucket
read new unassigned raw rows for this bucket
reuse existing IDs
assign new IDs only inside the bucket
rewrite affected registry part
rewrite affected records part
```

If numeric IDs must be globally unique, allocate bucket-local ID ranges or maintain a small metadata counter per bucket. The stable identity is `_raw_record_key`; `_raw_record_id` is a surrogate.

---

## 7. Compute bronze deltas by raw-record bucket

Do not compute added/removed keys with global anti-joins over all old/new records.

For each affected or all `raw_record_bucket`s:

```sql
SELECT _raw_record_key, _raw_record_id, 'added' AS _change_type
FROM new_bucket
WHERE _raw_record_key NOT IN (SELECT _raw_record_key FROM old_bucket)
UNION ALL
SELECT _raw_record_key, _raw_record_id, 'removed' AS _change_type
FROM old_bucket
WHERE _raw_record_key NOT IN (SELECT _raw_record_key FROM new_bucket)
```

Write delta by `raw_record_part`. The bronze manifest must include affected raw buckets and parts so silver does not scan unrelated raw state.

For unchanged downloads/parser contracts, keep the current empty-delta optimization, but the reused `records_path` should point at the partitioned state dataset.

---

# Silver layer

## 8. Build silver tables as compact partitioned state

Silver should remain a streaming mapper from bronze records to canonical rows, but its physical state should be partitioned and carry the bucket columns needed by gold.

Preferred layout:

```text
data/silver/<source>/latest/entity_occurrence/part=00000/data.parquet
data/silver/<source>/latest/entity_identifier/part=00000/data.parquet
data/silver/<source>/latest/entity_annotation/part=00000/data.parquet
data/silver/<source>/latest/membership/part=00000/data.parquet
data/silver/<source>/latest/membership_annotation/part=00000/data.parquet
```

Required bucket columns:

```text
entity_occurrence:       occ_bucket, occ_part, parent_bucket, parent_part
entity_identifier:       occ_bucket, occ_part, identifier_bucket, identifier_part
entity_annotation:       occ_bucket, occ_part, parent_bucket, parent_part
membership:              parent_bucket, parent_part, member_bucket, member_part
membership_annotation:   parent_bucket, parent_part, member_bucket, member_part
all tables:              raw_record_bucket, raw_record_part
```

`parent_bucket` should be null for rows without `parent_occurrence_id`.

---

## 9. Silver incremental carry-forward must be part-local

The current pattern of streaming all previous silver rows and filtering an in-memory set of changed raw keys is only bounded when the changed-key set is small and still rewrites full tables.

Instead:

```text
read bronze delta affected_raw_record_parts
for each affected silver table and affected physical part:
  copy forward unchanged rows from only that old part
  append newly mapped rows for changed raw records in that part
  rewrite only the affected part
```

Avoid a Python `set` containing every removed/changed raw key for a large delta. If a large delta must be filtered, materialize changed keys as a bucketed Parquet table and anti-join inside `raw_record_bucket`.

Silver manifests must include:

```json
{
  "bucket_algorithm": "stable_u64_sha256_mod_v1",
  "raw_record_bucket_count": 4096,
  "raw_record_part_count": 128,
  "occ_bucket_count": 4096,
  "occ_part_count": 128,
  "identifier_bucket_count": 4096,
  "identifier_part_count": 128,
  "affected_raw_record_buckets": "...",
  "affected_raw_record_parts": "...",
  "affected_occ_buckets": "...",
  "affected_occ_parts": "...",
  "affected_identifier_buckets": "...",
  "affected_identifier_parts": "..."
}
```

---

## 10. Keep silver compatibility exports optional

If existing consumers require files such as:

```text
entity_occurrence.parquet
entity_identifier.parquet
...
```

treat them as optional compatibility exports. They should not be the core scalable state, and gold should read the partitioned silver tables.

---

# Gold entities

## 11. Extract entity candidates from silver

Read only needed silver columns.

Input:

```text
silver/entity_occurrence
silver/entity_identifier
```

Output:

```text
gold/work/entity_candidate/
```

Columns:

```text
source
dataset
snapshot_id
occurrence_id
entity_type
identifier_type
identifier
taxonomy_id
_raw_record_id
_raw_record_key
occ_bucket
occ_part
identifier_bucket
identifier_part
```

Do not read membership or annotation tables in this step.

---

## 12. Fingerprint occurrences by `occurrence_id`

Rows that must meet:

```text
all identifiers for the same occurrence_id
```

Process by `occ_bucket`.

Pseudo-loop:

```python
for occ_bucket in affected_or_all_occ_buckets:
    run fingerprint query where occ_bucket = ?
```

Output:

```text
occurrence_fingerprint
  occurrence_id
  entity_type
  fingerprint
  _raw_record_id
  _raw_record_key
  identifier_bucket
  identifier_part
```

Do not group by `occurrence_id` globally.

---

## 13. Join resolver mappings by identifier bucket

Resolver mappings must be partitionable by:

```text
identifier_type
identifier
taxonomy_id
```

Output resolver layout:

```text
resolver/current/part=00000/data.parquet
...
```

Resolver columns:

```text
identifier_type
identifier
taxonomy_id
canonical_identifier_type
canonical_identifier
identifier_bucket
identifier_part
```

Join one identifier bucket at a time:

```sql
SELECT ...
FROM entity_candidate c
JOIN resolver r
  ON c.identifier_type = r.identifier_type
 AND c.identifier = r.identifier
 AND coalesce(c.taxonomy_id, '') = coalesce(r.taxonomy_id, '')
WHERE c.identifier_bucket = ?
  AND r.identifier_bucket = ?;
```

Output:

```text
canonical_entity_evidence
  occurrence_id
  fingerprint
  entity_type
  canonical_identifier_type
  canonical_identifier
  taxonomy_id
  entity_key
  entity_bucket
  entity_part
  _raw_record_id
  _raw_record_key
```

---

## 14. Compute `entity_key` row-locally

Use:

```text
entity_key = sha256(canonical_identifier_type | canonical_identifier | taxonomy_id)
```

Then immediately assign:

```text
entity_bucket
entity_part
```

and write by `entity_part`.

No global read is needed for this operation.

---

## 15. Reduce entities by `entity_key`

Rows that must meet:

```text
all canonical entity evidence for the same entity_key
```

Process by `entity_bucket`.

For each bucket:

```sql
SELECT
    entity_key,
    any_value(entity_type) AS entity_type,
    any_value(canonical_identifier_type) AS canonical_identifier_type,
    any_value(canonical_identifier) AS canonical_identifier,
    any_value(taxonomy_id) AS taxonomy_id,
    count(*) AS evidence_count
FROM canonical_entity_evidence
WHERE entity_bucket = ?
GROUP BY entity_key;
```

Do not use global `GROUP BY entity_key`.

Do not build large arrays like:

```text
list(all sources)
list(all evidence)
list(all PMIDs)
```

Keep evidence normalized.

---

## 16. Assign `entity_pk` from a registry

Do not use:

```sql
row_number() over (order by entity_key)
```

Use a persistent registry:

```text
entity_key_registry
  entity_key
  entity_pk
  first_seen_build_id
  active
  entity_bucket
  entity_part
```

For each affected `entity_bucket`:

```text
read registry rows for this bucket
reuse existing PKs
assign new PKs to unseen entity_keys
write updated registry part
```

The stable public identity is `entity_key`. `entity_pk` is only an internal surrogate.

---

## 17. Write gold entity outputs

Write these as compact physical parts:

```text
data/gold/<source>/entities/entity/part=00000/data.parquet
data/gold/<source>/entities/entity_evidence/part=00000/data.parquet
data/gold/<source>/entities/entity_occurrence_map/part=00000/data.parquet
```

Important partitioning:

```text
entity.parquet              -> entity_part
entity_evidence.parquet     -> entity_part
entity_occurrence_map       -> occ_part
```

The occurrence map must be partitioned by `occurrence_id`, because relations need to join on:

```text
member_occurrence_id -> entity_key/entity_pk
```

---

# Gold relations

## 18. Join membership to entity occurrence map

Rows that must meet:

```text
membership.member_occurrence_id
entity_occurrence_map.occurrence_id
```

So both sides must be processed by:

```text
occ_bucket
```

Input:

```text
silver/membership
gold/entities/entity_occurrence_map
```

Output:

```text
participant_entity
  parent_occurrence_id
  member_occurrence_id
  membership_role
  entity_key
  entity_pk
  source
  dataset
  _raw_record_id
  parent_bucket
  parent_part
```

Write `participant_entity` by `parent_part`, because the next step groups by parent interaction.

---

## 19. Attach parent annotations by `parent_occurrence_id`

Rows that must meet:

```text
participants for the same parent_occurrence_id
annotations for the same parent_occurrence_id
```

Prepare parent annotations from:

```text
silver/entity_annotation
```

Partition by:

```text
parent_bucket
parent_part
```

Then process one `parent_bucket` at a time:

```sql
SELECT ...
FROM participant_entity p
LEFT JOIN parent_annotation a
  ON p.parent_occurrence_id = a.occurrence_id
WHERE p.parent_bucket = ?
  AND a.parent_bucket = ?;
```

---

## 20. Build relation candidates by parent bucket

For each `parent_occurrence_id`, identify:

```text
source participant
target participant
effect
PMID
source_database
```

Then compute:

```text
predicate
subject_entity_key
object_entity_key
relation_category
relation_key
relation_bucket
relation_part
```

Example predicate mapping:

```text
effect = inhibits -> negatively_regulates
effect = binds    -> interacts_with
```

Output:

```text
relation_candidate
  source
  dataset
  raw_record_id
  subject_entity_key
  predicate
  object_entity_key
  relation_category
  relation_key
  evidence
  relation_bucket
  relation_part
```

Write by `relation_part`.

---

## 21. Reduce relations by `relation_key`

Rows that must meet:

```text
all evidence for the same relation_key
```

Process by `relation_bucket`.

For each bucket:

```sql
SELECT
    relation_key,
    any_value(subject_entity_key) AS subject_entity_key,
    any_value(predicate) AS predicate,
    any_value(object_entity_key) AS object_entity_key,
    any_value(relation_category) AS relation_category,
    count(*) AS evidence_count
FROM relation_candidate
WHERE relation_bucket = ?
GROUP BY relation_key;
```

Do not globally group all relations.

---

## 22. Assign `relation_pk` from a registry

Use:

```text
relation_key_registry
  relation_key
  relation_pk
  first_seen_build_id
  active
  relation_bucket
  relation_part
```

Process only affected `relation_bucket`s.

---

## 23. Write gold relation outputs

Write:

```text
data/gold/<source>/relations/entity_relation/part=00000/data.parquet
data/gold/<source>/relations/entity_relation_evidence/part=00000/data.parquet
```

Partition both by:

```text
relation_part
```

---

# Gold deltas

## 24. Publish bucket-aware deltas

Gold must write:

```text
data/gold/<source>/_delta/<build_id>/
  affected_entity_keys.parquet
  affected_entity_buckets.parquet
  affected_entity_parts.parquet
  affected_relation_keys.parquet
  affected_relation_buckets.parquet
  affected_relation_parts.parquet
  manifest.json
```

The manifest must include:

```json
{
  "source": "...",
  "build_id": "...",
  "silver_snapshot_id": "...",
  "resolver_snapshot_id": "...",
  "entity_key_algorithm": "sha256_v1",
  "relation_key_algorithm": "sha256_v1",
  "bucket_algorithm": "stable_u64_sha256_mod_v1",
  "entity_bucket_count": 4096,
  "entity_part_count": 128,
  "relation_bucket_count": 4096,
  "relation_part_count": 128
}
```

Resolver changes must invalidate gold even if silver did not change.

---

# Combined layer

## 25. Keep combined state normalized

Do not merge sources by building large arrays.

Use source-fact tables:

```text
combined_entity_source
  entity_key
  source
  source_build_id
  evidence_count
  payload_hash
  active
  entity_bucket
  entity_part

combined_relation_source
  relation_key
  source
  source_build_id
  evidence_count
  payload_hash
  active
  relation_bucket
  relation_part
```

Use registries:

```text
combined_entity_registry
  entity_key
  entity_pk
  active
  entity_bucket
  entity_part

combined_relation_registry
  relation_key
  relation_pk
  active
  relation_bucket
  relation_part
```

These may be DuckDB tables or compact Parquet part files. If avoiding many files is the priority, a persistent DuckDB state database for registries is acceptable; public outputs should remain Parquet.

---

## 26. Combined bootstrap

For initial build:

```python
for entity_part in all_entity_parts:
    read gold/*/entities/entity/part=<entity_part>
    merge by entity_key
    update combined_entity_source
    update combined_entity_registry
    write combined/latest/entities/part=<entity_part>/data.parquet

for relation_part in all_relation_parts:
    read gold/*/relations/entity_relation/part=<relation_part>
    merge by relation_key
    update combined_relation_source
    update combined_relation_registry
    write combined/latest/relations/part=<relation_part>/data.parquet
```

This may scan all source gold outputs once, but never all in memory at once.

---

## 27. Combined incremental update

For each changed source build:

```text
read source gold delta
get affected_entity_parts
get affected_relation_parts
```

For each affected entity part:

```text
remove/deactivate previous combined_entity_source rows for source + affected keys
insert new source facts from source gold entity part
recompute combined entity rows for affected keys or affected part
rewrite combined/latest/entities/part=XXXXX/data.parquet
```

For each affected relation part:

```text
remove/deactivate previous combined_relation_source rows for source + affected keys
insert new source facts from source gold relation part
recompute combined relation rows for affected keys or affected part
rewrite combined/latest/relations/part=XXXXX/data.parquet
```

Do not reread unrelated parts.

---

## 28. Combined relation entity PK lookup

Combined relations should store:

```text
subject_entity_key
object_entity_key
```

When materializing relation output, look up final combined entity PKs from:

```text
combined_entity_registry
```

Only read registry parts containing the subject/object keys needed by the affected relation part.

Do not join all combined relations to all combined entities.

---

## 29. Public combined output

Preferred layout:

```text
data/combined/latest/entities/part=00000/data.parquet
data/combined/latest/entities/part=00001/data.parquet

data/combined/latest/relations/part=00000/data.parquet
data/combined/latest/relations/part=00001/data.parquet
```

Optional compatibility exports:

```text
data/combined/latest/entities.parquet
data/combined/latest/relations.parquet
```

Treat single-file exports as optional full-export jobs, not as the core scalable pipeline.

---

# Prohibited patterns

The developer should avoid these from bronze onwards:

```text
pandas DataFrame containing all bronze records
pandas DataFrame containing all silver rows
pandas DataFrame containing all gold entities
pandas DataFrame containing all gold relations
Python dict containing all occurrence_id -> entity_pk
Python dict containing all entity_key -> entity_pk
Python dict containing all relation_key -> relation_pk
global GROUP BY entity_key
global GROUP BY relation_key
global ORDER BY entity_key
global ORDER BY relation_key
row_number() over all keys
global DISTINCT _raw_record_key
global GROUP BY _raw_record_key
global anti-join between old and new raw records
full silver state rewrite for a small raw delta
list() aggregation over unbounded evidence
rewriting all combined outputs on every incremental run
```

---

# Acceptance tests

Add these tests before considering the refactor done:

1. **Equivalence test**
   Compare bucketed gold/combined output against the old global implementation on a small fixture.

2. **Memory-cap test**
   Run bronze, silver, gold, and combined with a deliberately low DuckDB memory limit and confirm completion.

3. **Incremental locality test**
   Change one raw record and verify only expected entity/relation parts are rewritten.

4. **File-count test**
   Verify each public table has approximately:

   ```text
   physical_part_count files
   ```

   not thousands of tiny files.

5. **No-global-query test**
   For heavy bronze/silver/gold/combined queries, require a `WHERE bucket = ?` or `WHERE part = ?` boundary.

6. **Resolver invalidation test**
   Change one resolver mapping and confirm affected gold entity and relation keys are recomputed.

The core implementation rule is: **every expensive join or group must happen inside an explicit bucket or part boundary, and every final Parquet dataset must be compacted to a small, predictable number of physical files.**

[1]: https://duckdb.org/docs/lts/data/partitioning/partitioned_writes.html "Partitioned Writes – DuckDB"
[2]: https://duckdb.org/docs/current/configuration/overview.html "Configuration – DuckDB"
[3]: https://duckdb.org/docs/current/data/parquet/overview.html "Reading and Writing Parquet Files – DuckDB"