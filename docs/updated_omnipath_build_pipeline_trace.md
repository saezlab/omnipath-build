# Target OmniPath build pipeline trace: DuckDB-state refactor

This document describes the proposed refactored OmniPath build pipeline. It is
intended to guide implementation work, not merely document the current code.

The main points to keep straight:

- DuckDB state is the authoritative internal state.
- Parquet is no longer internal state by default.
- Durable Parquet is reserved for public exports, release artifacts, optional
  Postgres table deltas, and optional debug exports.
- The separate full-rebuild code path is removed. A full rebuild becomes an
  incremental merge with `affected_scope = all`.
- Source-level state and combined state remain conceptually separate, because
  they answer different questions.
- The preferred implementation uses one logical state system with source-level
  DuckDB shards plus a combined DuckDB, instead of one monolithic database file.

## 0. Design goals

The refactor should preserve the current functional guarantees:

| Requirement | Preserve? | Notes |
|---|---:|---|
| Stable raw record IDs | Yes | Raw IDs remain stable across accepted snapshots. |
| Stable source entity and relation keys | Yes | Entity and relation key registries remain stateful. |
| Stable combined numeric IDs | Yes | Global ID registries remain stateful. |
| Incremental source updates | Yes | Raw/silver/gold updates use affected scopes. |
| Incremental combined updates | Yes | Combined recomputes affected global entities and relations. |
| Gold source release archive | Yes | Gold zip remains the source-level release artifact. |
| Combined public Parquet | Yes | Combined latest remains the public merged product. |
| Postgres incremental load | Yes | Requires combined table delete/upsert deltas. |
| Reports and reproducibility metadata | Yes | Stored in pipeline metadata and exported as JSON. |

The refactor should remove or demote these current internal artifacts:

```text
data/bronze/.../state/records/
data/bronze/.../<snapshot_id>/delta/
data/silver/<source>/<version>/
data/silver/<source>/state/
data/gold/<source>/entities/
data/gold/<source>/relations/
data/gold/<source>/_delta/
data/combined/runs/<run_id>/affected/
```

## 1. Target data root layout

Preferred layout:

```text
data/
  state/
    pipeline.duckdb
    combined.duckdb
    sources/
      <source>.duckdb

  artifacts/
    gold/
      <source>/
        latest.json
        <gold_version>/
          <source>.zip
          manifest.json

  combined/
    latest/
      entity/
      entity_evidence/
      entity_relation/
      entity_relation_evidence/
      relation_annotation_term/
      resources.parquet
      combined_build_summary.json
      relation_annotation_summary.json
      build_manifest.jsonl

    runs/
      latest.json
      <run_id>/
        manifest.json
        delta/                  # optional, only if Postgres incremental is enabled
          entity_delete.parquet
          entity_upsert.parquet
          entity_evidence_delete.parquet
          entity_evidence_upsert.parquet
          entity_relation_delete.parquet
          entity_relation_upsert.parquet
          entity_relation_evidence_delete.parquet
          entity_relation_evidence_upsert.parquet
          relation_annotation_term_delete.parquet
          relation_annotation_term_upsert.parquet

  reports/
    latest.json
    changelog.ndjson
    runs/<run_id>.json
    memory/<run_id>.ndjson

  debug/                       # optional, not a normal pipeline contract
    silver/<source>/<run_id>/
    gold/<source>/<run_id>/
    scopes/<run_id>/
```

### Why source-sharded state instead of one physical database?

The pipeline should have one logical state model, but the default physical
layout should be sharded:

```text
data/state/pipeline.duckdb
data/state/sources/<source>.duckdb
data/state/combined.duckdb
```

This keeps source builds parallelizable and limits failure blast radius.

## 2. State ownership

Each layer has exactly one authoritative state location.

| Layer | Authoritative state | Durable external artifacts |
|---|---|---|
| Pipeline orchestration | `data/state/pipeline.duckdb` | reports JSON, changelog |
| Bronze/raw records | `data/state/sources/<source>.duckdb` | none by default |
| Silver canonical tables | `data/state/sources/<source>.duckdb` | optional debug export only |
| Source gold | `data/state/sources/<source>.duckdb` | source gold zip |
| Combined | `data/state/combined.duckdb` | combined latest Parquet |
| Postgres handoff | `data/state/combined.duckdb` staging tables | optional table delta Parquet |

A handoff artifact must be one of these:

| Kind | Meaning | Example |
|---|---|---|
| State | Durable internal truth used by future runs | DuckDB tables |
| Scope | Keys or IDs that need recomputation | stored in DuckDB run-scope tables |
| Row delta | Concrete delete/upsert rows for an external table | combined Postgres delta Parquet |
| Export | Public materialized output | gold zip, combined latest Parquet |

The words `state`, `scope`, `delta`, and `export` should not be used
interchangeably.

## 3. Pipeline metadata database

`data/state/pipeline.duckdb` owns orchestration metadata.

Suggested tables:

```text
pipeline_run
  run_id
  started_at
  finished_at
  status
  selected_sources
  jobs
  start_mode
  config_json

pipeline_task_run
  run_id
  task_name
  task_kind
  source
  status
  started_at
  finished_at
  depends_on_json
  output_artifacts_json
  error_json

source_run_index
  run_id
  source
  source_run_id
  source_state_path
  status
  scope_strategy
  affected_raw_record_count
  affected_occurrence_count
  affected_entity_key_count
  affected_relation_key_count
  gold_artifact_path

combined_run_index
  run_id
  combined_run_id
  combined_state_path
  status
  mode
  affected_entity_key_count
  affected_relation_key_count
  latest_dir
  postgres_delta_dir

artifact_registry
  artifact_id
  artifact_kind
  source
  run_id
  version
  path
  content_hash
  manifest_json
  created_at

input_signature
  owner_kind              # source, resolver, combined
  owner_name
  signature_kind          # parser, inputs_module, resolver, builder, schema, key_algorithm
  signature_hash
  signature_json
  recorded_at

latest_pointer
  pointer_kind
  owner_name
  artifact_kind
  run_id
  version
  path
  updated_at
```

Reports can still be exported as JSON files under `data/reports/`, but the
pipeline database should be the canonical metadata source.

## 4. Source state database

Each source has one state database:

```text
data/state/sources/<source>.duckdb
```

It owns bronze, silver, source-gold, and source-scope state for that source.

### 4.1 Source metadata tables

```text
source_run
  source_run_id
  pipeline_run_id
  source
  started_at
  finished_at
  status
  scope_strategy
  input_signature_hash
  previous_source_run_id
  manifest_json

source_input_signature
  source
  signature_kind
  signature_hash
  signature_json
  recorded_at

source_artifact
  source_run_id
  artifact_kind
  path
  content_hash
  manifest_json
```

### 4.2 Bronze/raw-record tables

Bronze no longer writes durable Parquet state by default.

Suggested tables:

```text
bronze_dataset_snapshot
  snapshot_id
  source
  dataset
  source_run_id
  parser_contract_hash
  file_fingerprint
  records_hash
  status
  created_at
  manifest_json

bronze_raw_record_registry
  source
  dataset
  raw_record_key
  raw_record_id
  raw_record_bucket
  first_seen_snapshot_id
  last_seen_snapshot_id
  is_current

bronze_raw_record_current
  source
  dataset
  raw_record_key
  raw_record_id
  raw_record_bucket
  raw_record_part
  payload_json
  snapshot_id

bronze_raw_record_change
  source_run_id
  source
  dataset
  raw_record_key
  raw_record_id
  raw_record_bucket
  raw_record_part
  change_type
```

The raw payload can be stored as canonical JSON by default:

```text
payload_json
```

Dataset-specific typed raw tables may be added later for performance, but they
should not be required for the state model.

Raw record ID assignment remains stable:

```text
raw_record_id = raw_record_bucket * 1_000_000_000_000 + local_id
```

### 4.3 Silver canonical tables

Silver no longer writes durable Parquet state by default.

Suggested tables:

```text
silver_entity_occurrence
silver_entity_identifier
silver_entity_annotation
silver_membership
silver_membership_annotation
```

Each table keeps lineage columns:

```text
source
dataset
raw_record_key
raw_record_id
raw_record_bucket
raw_record_part
snapshot_id
source_run_id
```

Occurrence IDs remain deterministic when raw IDs are available:

```text
<dataset>:<raw_record_id>:parent
<dataset>:<raw_record_id>:parent:member:<n>
```

Silver run scopes are stored as tables, not Parquet directories:

```text
source_run_scope_raw_record
  source_run_id
  source
  dataset
  raw_record_id
  raw_record_key
  reason

source_run_scope_occurrence
  source_run_id
  source
  occurrence_id
  raw_record_id
  reason
```

### 4.4 Source-gold tables

Source gold remains source-local. It answers:

> What is the current gold output for this one source?

Suggested tables:

```text
gold_entity
gold_entity_evidence
gold_entity_map
gold_entity_occurrence_map
gold_entity_relation
gold_entity_relation_evidence

gold_entity_key_registry
gold_relation_key_registry
```

The registries preserve stable source-local primary-key assignment by
`entity_key` and `relation_key`.

Source-gold affected scopes are stored as tables:

```text
source_run_scope_entity
  source_run_id
  source
  entity_key
  entity_bucket
  entity_part
  reason

source_run_scope_relation
  source_run_id
  source
  relation_key
  relation_bucket
  relation_part
  reason
```

Current source-gold Parquet directories are not durable state anymore:

```text
data/gold/<source>/entities/
data/gold/<source>/relations/
```

They may be generated temporarily when building the source gold zip, then
deleted.

## 5. Combined state database

The combined database is:

```text
data/state/combined.duckdb
```

It answers:

> What is the current cross-source merged database?

Suggested tables:

```text
combined_run
  combined_run_id
  pipeline_run_id
  started_at
  finished_at
  status
  mode                    # incremental, bootstrap, migration
  manifest_json

combined_entity_key_map
  entity_key
  entity_id
  first_seen_run_id
  last_seen_run_id
  is_current

combined_relation_key_map
  relation_key
  relation_id
  first_seen_run_id
  last_seen_run_id
  is_current

combined_entity
combined_entity_source
combined_entity_evidence
combined_entity_relation
combined_relation_source
combined_entity_relation_evidence
combined_relation_annotation_term
```

Combined run scopes are stored as tables:

```text
combined_run_scope_entity
  combined_run_id
  source
  entity_key
  entity_id
  entity_part
  reason

combined_run_scope_relation
  combined_run_id
  source
  relation_key
  relation_id
  relation_part
  reason
```

If Postgres incremental loading is enabled, combined table deltas are first
materialized in DuckDB staging tables:

```text
combined_delta_entity_delete
combined_delta_entity_upsert
combined_delta_entity_evidence_delete
combined_delta_entity_evidence_upsert
combined_delta_entity_relation_delete
combined_delta_entity_relation_upsert
combined_delta_entity_relation_evidence_delete
combined_delta_entity_relation_evidence_upsert
combined_delta_relation_annotation_term_delete
combined_delta_relation_annotation_term_upsert
```

Those staging tables are then exported to:

```text
data/combined/runs/<run_id>/delta/*.parquet
```

## 6. Pipeline entry and task graph

The main orchestration entry point remains conceptually:

```python
omnipath_build.pipeline.dag.run_pipeline
```

The refactored task graph should be simpler:

| Task | Created when | Depends on | Responsibility |
|---|---|---|---|
| `resolver` | resolver enabled | none | Build/update resolver tables in state. |
| `source:<source>` | source builds enabled | `resolver` when needed | Update bronze, silver, and source-gold state for one source; export gold zip if changed. |
| `combine` | combine enabled | all scheduled source tasks, unless partial combine is explicitly enabled | Update combined state and export combined latest. |
| `postgres` | Postgres URI provided | `combine` | Load full combined export or apply combined table deltas. |
| `report` | always | all tasks | Export reports and update metadata. |

The old stage boundaries become internal source-update phases:

```text
bronze -> silver -> source gold
```

They are no longer separate durable filesystem contracts.

### Start modes

Current `start_stage` values can be preserved for compatibility, but internally
they should translate into update scopes or disabled phases.

| User value | Internal behavior |
|---|---|
| `download`, `from-download` | Re-evaluate/download raw inputs, then update source state. |
| `bronze`, `from-bronze` | Reuse available downloads where possible, update raw/silver/gold state. |
| `silver`, `from-silver` | Recompute silver/gold from existing raw state for selected source scope. |
| `gold`, `from-gold` | Recompute source gold from existing silver state, or run combine from existing source-gold state. |
| `combined`, `from-combined` | Run combine from existing source-gold state. |

A future cleanup can replace `start_stage` with explicit commands:

```text
update-sources
update-combined
export-gold
export-combined
load-postgres
```

## 7. Resource discovery

Resource discovery can remain mostly unchanged.

`discover_resources()` still imports public modules under the inputs package and
discovers:

- `Resource` objects;
- `Dataset` objects;
- `OntologyDataset` objects;
- `ArtifactDataset` objects;
- datasets nested inside `Resource.datasets()`.

The important change is what discovery feeds:

Current model:

```text
discovered dataset -> bronze Parquet -> silver Parquet -> gold Parquet
```

Target model:

```text
discovered dataset -> source state transaction -> optional exports
```

Gold-buildable sources are still sources whose discovered functions include a
non-`resource` dataset with `output_kind` of `entity` or `ontology`.

## 8. Source update trace

A source update is the main unit of incremental work.

```text
source:<source>
  1. Open source state database.
  2. Begin source run.
  3. Compute input signatures.
  4. Determine source scope.
  5. Update bronze/raw-record state.
  6. Update silver canonical state.
  7. Update source-gold state.
  8. Record affected source entity/relation keys.
  9. Export gold zip if required.
  10. Commit source run metadata.
```

### 8.1 Begin source run

Create a `source_run_id`, for example:

```text
source-run-YYYYMMDD-HHMMSS-<source>
```

Record:

```text
source
pipeline_run_id
previous_source_run_id
started_at
input_signature_hash
scope_strategy
```

### 8.2 Compute input signatures

The source input signature should include:

```text
parser contract hash
inputs module hash
silver schema version
gold builder version
entity key algorithm version
relation key algorithm version
resolver mapping hash
partition settings
```

Example manifest field:

```json
{
  "input_signature": {
    "parser_contract_hash": "...",
    "inputs_module_hash": "...",
    "silver_schema_version": "...",
    "gold_builder_version": "...",
    "entity_key_algorithm": "sha256_v1",
    "relation_key_algorithm": "sha256_v1",
    "resolver_mapping_hash": "...",
    "raw_bucket_count": 4096,
    "entity_part_count": 128,
    "relation_part_count": 128
  }
}
```

### 8.3 Determine source scope

The source update should always produce a scope. Common strategies:

| Strategy | Meaning |
|---|---|
| `empty` | No input or signature change. Nothing to recompute. |
| `raw_delta` | Recompute records affected by raw added/removed/changed keys. |
| `dataset_all` | Recompute all current records for one dataset. |
| `source_all` | Recompute all current records for the source. |
| `resolver_affected` | Recompute entities affected by resolver mapping changes, when supported. |
| `schema_migration` | State schema changed; run migration before update. |
| `key_algorithm_migration` | Entity/relation key semantics changed; requires explicit migration or rebootstrap. |

There is no separate normal full-rebuild branch. The old full rebuild is:

```text
scope_strategy = source_all
```

### 8.4 Update bronze/raw-record state

For each dataset:

1. Build parser contract from the raw parser callable and parser kwargs.
2. Locate or download the raw input file.
3. Fingerprint the input file.
4. If file fingerprint and parser contract match accepted state, mark dataset
   scope as `empty`.
5. Otherwise parse the raw file.
6. Canonicalize each parser dictionary.
7. Compute `_raw_record_key`.
8. Reuse or assign `_raw_record_id` from `bronze_raw_record_registry`.
9. Compute added, removed, and changed raw records.
10. Update `bronze_raw_record_current`.
11. Insert rows into `bronze_raw_record_change`.
12. Insert affected rows into `source_run_scope_raw_record`.

No durable bronze Parquet is written by default.

### 8.5 Update silver canonical state

If raw scope is empty and silver input signatures are compatible, silver is
reused.

Otherwise:

1. Read affected current raw records from `bronze_raw_record_current`.
2. Delete existing silver rows for affected raw IDs, datasets, or source scope.
3. Map affected current raw records to canonical silver entities.
4. Insert new silver rows into:

   ```text
   silver_entity_occurrence
   silver_entity_identifier
   silver_entity_annotation
   silver_membership
   silver_membership_annotation
   ```

5. Populate `source_run_scope_occurrence` from affected raw IDs and occurrence
   IDs.

For ontology datasets, the default scope can remain dataset-wide unless a finer
ontology delta is implemented.

### 8.6 Update source-gold state

Gold uses the same merge implementation for small, dataset-wide, and source-wide
scopes.

1. Read affected occurrence IDs from `source_run_scope_occurrence`.
2. Build temporary changed-only gold entity evidence and maps from current
   silver state.
3. Resolve identifiers using resolver tables.
4. Compute entity fingerprints.
5. Compute stable `entity_key` values.
6. Preserve existing entity registry assignments where possible.
7. Delete old gold entity evidence/maps for affected raw IDs, occurrence IDs,
   or source-wide scope.
8. Insert changed gold entity evidence/maps.
9. Recompute affected `gold_entity` rows.
10. Build changed-only relation evidence from current silver state and updated
    entity maps.
11. Recompute relation keys where subject/object keys changed.
12. Preserve existing relation registry assignments where possible.
13. Delete old affected relation evidence.
14. Insert changed relation evidence.
15. Recompute affected `gold_entity_relation` rows.
16. Populate `source_run_scope_entity` and `source_run_scope_relation`.

The key invariant:

```text
Gold state is updated by merge, even when the affected scope is the whole source.
```

### 8.7 Export source gold zip

If source-gold state changed, export a release archive:

```text
data/artifacts/gold/<source>/<gold_version>/<source>.zip
```

The zip is generated from source state tables. Temporary unzipped Parquet may be
created under a staging directory and then deleted.

Suggested zip contents:

```text
entity/
entity_evidence/
entity_map/
entity_occurrence_map/
entity_relation/
entity_relation_evidence/
manifest.json
canonicalization_report.md
canonicalization_summary.json
```

The unzipped source-gold Parquet directories are not retained by default.

### 8.8 Commit source run

After the source state transaction and artifact export succeed:

1. Mark `source_run.status = succeeded`.
2. Update latest source run pointer.
3. Register gold zip artifact in `pipeline.artifact_registry`.
4. Record affected counts.
5. Export optional debug snapshots if configured.

If the source run fails, the previous source state remains authoritative.

## 9. Combined update trace

The combined task updates global state from completed source runs.

```text
combine
  1. Open combined state database.
  2. Read successful source run scopes from this pipeline run.
  3. If no source scopes changed, reuse combined latest.
  4. Attach or read relevant source state databases.
  5. Expand affected entity/relation keys.
  6. Recompute affected global entities and relations.
  7. Merge into combined state.
  8. Export combined latest Parquet.
  9. Optionally export Postgres table deltas.
  10. Commit combined run metadata.
```

### 9.1 Collect source scopes

For each changed source, read from source state:

```text
source_run_scope_entity
source_run_scope_relation
```

No gold `_delta` Parquet is required.

If a source run reports `scope_strategy = source_all`, combine receives all
current entity and relation keys for that source as affected keys.

### 9.2 Expand combined scope

Combined should expand the initial affected key set:

1. Add affected source entity keys.
2. Add affected source relation keys.
3. Add relations that reference affected entity keys.
4. Add relation annotation terms affected by changed relation parts.
5. Map affected keys to stable global IDs via:

   ```text
   combined_entity_key_map
   combined_relation_key_map
   ```

Write the result to:

```text
combined_run_scope_entity
combined_run_scope_relation
```

### 9.3 Merge combined entities

For affected entity keys:

1. Read current source-gold entity rows from relevant source state databases.
2. Recompute global entity rows.
3. Update `combined_entity_key_map` for new keys.
4. Delete old affected rows from:

   ```text
   combined_entity
   combined_entity_source
   combined_entity_evidence
   ```

5. Insert recomputed rows.
6. Preserve stable `entity_id` values for existing keys.

### 9.4 Merge combined relations

For affected relation keys:

1. Read current source-gold relation rows from relevant source state databases.
2. Recompute global relation rows.
3. Update `combined_relation_key_map` for new keys.
4. Delete old affected rows from:

   ```text
   combined_entity_relation
   combined_relation_source
   combined_entity_relation_evidence
   combined_relation_annotation_term
   ```

5. Insert recomputed rows.
6. Preserve stable `relation_id` values for existing keys.

### 9.5 Export combined latest Parquet

Combined latest remains the main public export:

```text
data/combined/latest/
  entity/
  entity_evidence/
  entity_relation/
  entity_relation_evidence/
  relation_annotation_term/
  resources.parquet
  combined_build_summary.json
  relation_annotation_summary.json
  build_manifest.jsonl
```

Internal partition columns may still be used in state, but public exports should
only include public columns.

### 9.6 Export Postgres deltas, if enabled

If Postgres incremental loading is enabled, produce actual table deltas:

```text
data/combined/runs/<run_id>/delta/
  entity_delete.parquet
  entity_upsert.parquet
  entity_evidence_delete.parquet
  entity_evidence_upsert.parquet
  entity_relation_delete.parquet
  entity_relation_upsert.parquet
  entity_relation_evidence_delete.parquet
  entity_relation_evidence_upsert.parquet
  relation_annotation_term_delete.parquet
  relation_annotation_term_upsert.parquet
```

These are concrete delete/upsert payloads, not affected scopes.

If Postgres incremental loading is disabled, these files do not need to be
written.

## 10. Postgres load trace

Postgres reads only combined artifacts.

| Condition | Action |
|---|---|
| target empty or `drop_existing=True` | Bootstrap from `data/combined/latest/`. |
| incremental run and `delta/*.parquet` exists | Apply delete/upsert table deltas. |
| incremental run but no table deltas | Leave base tables unchanged and refresh only requested indexes/views, or fail if strict mode is enabled. |

The loader should not consume source-gold outputs, source scopes, silver state,
or bronze state.

Recommended strict behavior:

```text
postgres_incremental_required = true
```

When enabled, an incremental Postgres load fails if combined table deltas are
missing. This avoids silently leaving base tables stale.

## 11. Removing separate full rebuild mode

The refactored pipeline should not have two normal implementations:

```text
incremental path
full rebuild path
```

Instead, it should have one implementation:

```text
compute affected scope
recompute current rows for that scope
merge into state
export artifacts
```

Old full rebuild scenarios become scoped updates:

| Current scenario | Target behavior |
|---|---|
| First source build | `scope_strategy = source_all`; initialize registries and merge. |
| Missing previous silver state | Source state has no silver rows; `source_all` update. |
| Parser code changed | `dataset_all` or `source_all` update. |
| Inputs module hash changed | `dataset_all` or `source_all` update. |
| Previous gold state missing | Source-gold tables empty; `source_all` update. |
| Combined state empty | Combined scope is all source keys; initialize global registries and merge. |

Exceptional cases still need explicit handling:

| Exceptional case | Handling |
|---|---|
| Entity key algorithm changed | Migration or rebootstrap. Not a normal incremental run. |
| Relation key algorithm changed | Migration or rebootstrap. Not a normal incremental run. |
| State schema changed | Schema migration. |
| State corruption | Recovery or rebootstrap. |
| Resolver semantics changed globally | Resolver-affected scope if possible; otherwise source-wide scope for affected sources. |

So the user-facing language can still say “bootstrap” for first build, but the
implementation should use the same merge machinery.

## 12. Resolver mappings

Resolver mappings should move into state by default.

Suggested location:

```text
data/state/pipeline.duckdb
```

or a dedicated resolver database:

```text
data/state/resolver.duckdb
```

Suggested tables:

```text
resolver_protein_identifier_lookup
resolver_chemical_identifier_lookup
resolver_mapping_run
resolver_mapping_signature
```

## 13. Export policy

Parquet export policy should be explicit.

| Export | Default | Reason |
|---|---:|---|
| Bronze records Parquet | No | Internal state only. |
| Bronze delta Parquet | No | Scope is stored in DuckDB. |
| Silver canonical Parquet | No | Internal state only. |
| Silver delta Parquet | No | Gold consumes scope from state. |
| Unzipped source-gold Parquet | No | Generate temporarily for zip only. |
| Source gold zip | Yes | Source release artifact. |
| Gold affected-scope Parquet | No | Combined reads scope from state. |
| Combined latest Parquet | Yes | Main public merged export. |
| Combined affected-scope Parquet | No | Internal state only. |
| Combined table delta Parquet | Optional | Required for incremental Postgres load. |
| Resolver Parquet | Optional | Compatibility export. |
| Debug silver/gold/scope Parquet | Optional | Useful during refactor and debugging. |

Suggested flags:

```text
--export-debug-silver
--export-debug-gold
--export-debug-scopes
--export-resolver-parquet
--export-postgres-delta
```

## 14. Updated end-to-end lineage

For one raw parser row, the target lineage path is:

1. Raw parser emits a dictionary.
2. Source state computes `raw_record_key`.
3. Source state assigns or reuses `raw_record_id`.
4. Source state records raw changes in `bronze_raw_record_change`.
5. Silver canonical tables are updated in the same source state database.
6. Occurrence IDs are derived from dataset and raw record ID.
7. Source-gold tables derive entity fingerprints and stable `entity_key` values.
8. Source-gold relation tables derive stable `relation_key` values.
9. Source-gold registries preserve source-local PK assignment.
10. Source run scope records affected entity and relation keys.
11. Combined state reads source run scopes and source-gold current rows.
12. Combined state assigns or reuses global `entity_id` and `relation_id`.
13. Combined state recomputes affected global rows.
14. Combined latest Parquet is exported.
15. Optional Postgres table deltas are exported from combined state.

Key summary:

| Layer | Key or ID | Stable across | Purpose |
|---|---|---|---|
| Download | file SHA-256 | identical local file content | Detect unchanged downloaded inputs. |
| Bronze | `raw_record_key` | identical parser row content | Content-address raw rows. |
| Bronze | `raw_record_id` | accepted source state lifetime | Compact lineage ID. |
| Bronze | `raw_record_bucket`, `raw_record_part` | deterministic from raw key | Bounded raw partitioning. |
| Silver | `occurrence_id` | same raw ID and dataset shape | Attach identifiers, annotations, and memberships. |
| Gold entities | entity fingerprint | same source entity description | Pre-resolution entity grouping and maps. |
| Gold entities | `entity_key` | same canonical entity identity | Source and combined entity business key. |
| Gold relations | `relation_key` | same subject/predicate/object/category | Source and combined relation business key. |
| Combined | `entity_id`, `relation_id` | combined state lifetime | Stable exported numeric IDs. |

## 15. Debugging checklist in the refactored pipeline

When a run does more work than expected, check state first, not Parquet
internals.

### 15.1 Pipeline run

```sql
select *
from pipeline_run
order by started_at desc
limit 5;
```

```sql
select task_name, source, status, error_json
from pipeline_task_run
where run_id = '<run_id>'
order by started_at;
```

### 15.2 Source input signatures

In `data/state/sources/<source>.duckdb`:

```sql
select signature_kind, signature_hash, recorded_at
from source_input_signature
order by recorded_at desc;
```

### 15.3 Raw changes

```sql
select dataset, change_type, count(*) as n
from bronze_raw_record_change
where source_run_id = '<source_run_id>'
group by dataset, change_type
order by dataset, change_type;
```

### 15.4 Silver scope

```sql
select reason, count(*) as raw_records
from source_run_scope_raw_record
where source_run_id = '<source_run_id>'
group by reason;
```

```sql
select reason, count(*) as occurrences
from source_run_scope_occurrence
where source_run_id = '<source_run_id>'
group by reason;
```

### 15.5 Gold affected keys

```sql
select reason, count(*) as entities
from source_run_scope_entity
where source_run_id = '<source_run_id>'
group by reason;
```

```sql
select reason, count(*) as relations
from source_run_scope_relation
where source_run_id = '<source_run_id>'
group by reason;
```

### 15.6 Combined scope and export

In `data/state/combined.duckdb`:

```sql
select *
from combined_run
order by started_at desc
limit 5;
```

```sql
select reason, count(*) as entities
from combined_run_scope_entity
where combined_run_id = '<combined_run_id>'
group by reason;
```

```sql
select reason, count(*) as relations
from combined_run_scope_relation
where combined_run_id = '<combined_run_id>'
group by reason;
```

### 15.7 Artifact registry

In `data/state/pipeline.duckdb`:

```sql
select artifact_kind, source, version, path, created_at
from artifact_registry
where run_id = '<run_id>'
order by artifact_kind, source;
```

## 16. Migration plan from the current pipeline

### Phase 1: Add state databases and dual-write

- Introduce `data/state/pipeline.duckdb`.
- Introduce `data/state/sources/<source>.duckdb`.
- Continue writing current Parquet state and delta artifacts.
- Also write equivalent source-state tables and run-scope tables.
- Add validation comparing current Parquet outputs to DB-derived exports.

### Phase 2: Make gold and combine read scopes from DuckDB

- Gold writes source scopes into source state.
- Combine reads source scopes from source state instead of
  `data/gold/<source>/_delta/`.
- Keep `_delta/` as compatibility/debug export only.

### Phase 3: Move silver state into source DuckDB

- Write silver canonical tables into source state.
- Gold reads silver from source state.
- Keep `data/silver/` only as compatibility/debug export.

### Phase 4: Move bronze state into source DuckDB

- Write raw record registry/current/change tables into source state.
- Silver reads raw records from source state.
- Keep `data/bronze/` only as compatibility/debug export.

### Phase 5: Make source-gold DuckDB authoritative

- Source-gold tables and registries live in source state.
- Gold zip is exported from source state.
- Stop retaining unzipped `data/gold/<source>/entities/` and `relations/` by default.

### Phase 6: Make combined DuckDB authoritative

- Combined tables and global registries live in `data/state/combined.duckdb`.
- Combined latest Parquet is exported from combined state.
- Remove dependency on source-gold Parquet directories.

### Phase 7: Generate real Postgres table deltas

- Combined incremental runs materialize delete/upsert rows in DuckDB.
- Export table deltas to `data/combined/runs/<run_id>/delta/` when enabled.
- Make Postgres incremental load consume only these table deltas.

### Phase 8: Remove old internal artifact contracts

Deprecate and eventually remove required reads from:

```text
data/bronze/
data/silver/
data/gold/<source>/entities/
data/gold/<source>/relations/
data/gold/<source>/_delta/
data/combined/runs/<run_id>/affected/
```

Keep optional debug exports behind flags.

## 17. Compatibility aliases

During migration, old paths can be kept as aliases or exports:

| Old path | Target status |
|---|---|
| `data/bronze/.../state/records/` | optional debug export |
| `data/bronze/.../<snapshot_id>/delta/` | optional debug export |
| `data/silver/<source>/state/` | optional debug export |
| `data/silver/<source>/<version>/delta/` | optional debug export |
| `data/gold/<source>/state.duckdb` | replaced by `data/state/sources/<source>.duckdb` |
| `data/gold/<source>/entities/` | temporary export for zip only |
| `data/gold/<source>/relations/` | temporary export for zip only |
| `data/gold/<source>/_delta/` | optional debug export from source scope tables |
| `data/combined/state.duckdb` | moved to `data/state/combined.duckdb`, or kept as alias |
| `data/combined/runs/<run_id>/affected/` | optional debug export from combined scope tables |

## 18. Summary

The target pipeline is still a layered incremental pipeline, but the layers are
state transitions rather than filesystem contracts.

Current mental model:

```text
bronze Parquet -> silver Parquet -> gold Parquet/state -> gold _delta -> combined state/Parquet -> Postgres
```

Target mental model:

```text
source DuckDB state -> source scope -> combined DuckDB state -> public exports
```

Durable normal artifacts become:

```text
gold zip archive per source/version
combined latest Parquet
optional combined Postgres table deltas
reports/manifests
```

Everything else is internal state, temporary staging, or optional debug export.
