# Incremental Delta Rollout Plan

This document is a handoff for continuing the incremental pipeline work in a new
session. It summarizes the current implementation state, the design decisions
made so far, and a staged plan for extending the bronze `records.parquet` plus
`delta.parquet` pattern into silver, gold, combine, and Postgres.

## Current State

The pipeline now has a DuckDB-backed combine path:

- `omnipath_build/gold/combine_duckdb.py`
- `build_combined(...)` always uses DuckDB; the legacy Polars combine path and
  engine switch have been removed.
- Bootstrap is source-sequential and key-batched.
- Incremental combine is also key-batched.
- The combine step keeps a local state store at:

```text
data/combined/state.duckdb
```

and exports current parquet snapshots to:

```text
data/combined/latest/
```

The pipeline also prints a preflight execution plan before running tasks. By
default, execution requires pressing Enter after the plan. Automation can pass:

```bash
--yes
```

or with Make:

```bash
make pipeline YES=1
```

The current planner can show affected entity/relation counts for combine. It
currently derives those by comparing previous combined evidence with current
source gold evidence. That is a temporary bridge, not the desired long-term
contract.

## Important Design Decision

Keep the bronze content-hash identity model.

The raw record key remains content-derived:

```text
_raw_record_key = hash(canonicalized raw record content)
```

This means a raw content edit is represented as:

```text
old raw record removed
new raw record added
```

That is intentional. We do **not** need a separate `changed` event type.

Rationale:

- The delete+insert model is simpler and less error-prone.
- It avoids source-specific natural-key logic.
- It avoids trusting upstream IDs that may not actually be stable.
- It gives one uniform delta contract across all sources.
- Downstream stages can recompute affected rows from removed and added records.

The delta vocabulary should therefore stay:

```text
added
removed
```

Updates are modeled as one removed row plus one added row.

## Current Bronze Contract

Bronze already follows the desired state/delta pattern.

Relevant files:

- `pypath/pypath/inputs_v2/raw_records.py`
  - `materialize_raw_records`
  - `accept_raw_snapshot`
  - `_write_records_with_ids`
  - `_write_delta`
- `pypath/pypath/inputs_v2/base.py`
  - provenance wrappers and raw-record iteration

Current artifacts:

```text
data/bronze/<source>/<dataset>/<snapshot_id>/records.parquet
data/bronze/<source>/<dataset>/<snapshot_id>/delta.parquet
data/bronze/<source>/<dataset>/<snapshot_id>/manifest.json

data/bronze/<source>/<dataset>/state/records.parquet
data/bronze/<source>/<dataset>/latest.json
```

Important invariants to copy:

- Write attempted snapshot/delta first.
- Advance the mutable pointer only after the snapshot is accepted.
- Keep current state separate from immutable deltas.
- Preserve provenance downstream.
- Do not treat `_raw_record_id` as globally unique. It is scoped to source and
  dataset.

## Target Delta Chain

The desired pipeline contract is:

```text
bronze delta
  -> silver delta
  -> gold affected keys + gold deltas
  -> combine run deltas
  -> Postgres delta load
```

Each stage should emit:

```text
manifest.json
current state
delta artifacts
```

The pipeline should use manifests and delta artifacts for planning and execution,
not infer changes by scanning large current-state parquets.

## Stage 1: Silver State And Delta

Current silver behavior:

- Silver writes versioned source outputs under `data/silver/<source>/<version>/`.
- It writes these current tables:
  - `entity_occurrence.parquet`
  - `entity_identifier.parquet`
  - `entity_annotation.parquet`
  - `membership.parquet`
  - `membership_annotation.parquet`
- Reuse currently depends mostly on `inputs_module_hash.json`.
- If bronze delta is empty and silver output exists, silver can skip rewriting.
- Otherwise silver effectively rebuilds source silver output.

Relevant files:

- `omnipath_build/silver/build.py`
- `omnipath_build/silver/tables.py`
- `omnipath_build/silver/paths.py`
- `omnipath_build/pipeline/tasks.py`
- `omnipath_build/pipeline/dag.py`

Proposed layout:

```text
data/silver/<source>/
  state/
    entity_occurrence.parquet
    entity_identifier.parquet
    entity_annotation.parquet
    membership.parquet
    membership_annotation.parquet
    manifest.json

  <snapshot_id>/
    delta/
      entity_occurrence.parquet
      entity_identifier.parquet
      entity_annotation.parquet
      membership.parquet
      membership_annotation.parquet
    manifest.json

  latest.json
```

Each silver table should keep existing columns and add enough provenance to
support row-level deltas:

```text
silver_row_key       string
silver_row_hash      uint64 or string
_raw_record_key      string
_raw_record_id       int64
_snapshot_id         string
```

Delta tables add:

```text
_change_type         string  # added | removed
```

Keep `record_id` for compatibility because gold currently reads it.

Silver delta derivation:

- For bronze `added` records:
  - map the new raw record
  - write derived silver rows as `_change_type = "added"`
- For bronze `removed` records:
  - find previous silver rows by `_raw_record_key` or `_raw_record_id`
  - write those rows as `_change_type = "removed"`
  - remove them from silver current state

Because content edits are represented as removed+added in bronze, silver does
not need a separate changed path.

Silver manifest should include:

```json
{
  "layer": "silver",
  "source": "signor",
  "snapshot_id": "...",
  "previous_snapshot_id": "...",
  "created_at": "...",
  "completed_at": "...",
  "upstream_manifests": [".../bronze/.../manifest.json"],
  "inputs_module_hash": {"sha256": "..."},
  "schema_version": "silver_schema_v1",
  "row_counts": {
    "entity_occurrence.parquet": 0
  },
  "delta_counts": {
    "entity_occurrence.parquet": {
      "added": 0,
      "removed": 0
    }
  }
}
```

## Stage 2: Gold Affected-Key Artifacts

Current gold behavior:

```text
data/gold/<source>/entities/entity.parquet
data/gold/<source>/entities/entity_evidence.parquet
data/gold/<source>/entities/entity_map.parquet
data/gold/<source>/entities/entity_occurrence_map.parquet
data/gold/<source>/relations/entity_relation.parquet
data/gold/<source>/relations/entity_relation_evidence.parquet
data/gold/<source>/_SUCCESS.json
```

Gold evidence is already the right semantic bridge:

- `entity_evidence.parquet` is per `(source, entity_key)` and has
  `raw_record_ids`.
- `entity_relation_evidence.parquet` is per evidence row and has one
  `raw_record_id`.

Relevant files:

- `omnipath_build/gold/build_entities.py`
- `omnipath_build/gold/build_relations.py`
- `omnipath_build/gold/utils/table_schema.py`
- `omnipath_build/pipeline/tasks.py`
- `omnipath_build/pipeline/dag.py`

First implementation should keep existing gold current-state paths for
compatibility and add per-build delta artifacts.

Proposed per-source layout:

```text
data/gold/<source>/
  entities/
    entity.parquet
    entity_evidence.parquet
    entity_map.parquet
    entity_occurrence_map.parquet
  relations/
    entity_relation.parquet
    entity_relation_evidence.parquet
  _SUCCESS.json

  _delta/<build_id>/
    manifest.json
    affected_entity_keys.parquet
    affected_relation_keys.parquet
    entities/entity_delta.parquet
    relations/entity_relation_delta.parquet
    relations/entity_relation_evidence_delta.parquet
```

Affected key schemas:

```text
affected_entity_keys.parquet
  source: string
  entity_key: string
  change_type: string  # added | removed
  reason: string       # silver_delta | resolver_mapping | source_rebuild
```

```text
affected_relation_keys.parquet
  source: string
  relation_key: string
  change_type: string  # added | removed
  reason: string
```

Gold delta schemas:

```text
entities/entity_delta.parquet
  source: string
  entity_key: string
  change_type: string
  raw_record_ids: list[string]
  entity_type: string
  taxonomy_id: string
  row_hash: uint64
  reason: string
```

```text
relations/entity_relation_delta.parquet
  source: string
  relation_key: string
  change_type: string
  subject_entity_key: string
  predicate: string
  object_entity_key: string
  relation_category: string
  row_hash: uint64
  reason: string
```

```text
relations/entity_relation_evidence_delta.parquet
  source: string
  relation_key: string
  raw_record_id: string
  change_type: string
  row_hash: uint64
  reason: string
```

Pragmatic first implementation:

```text
build staged source gold
diff previous source gold vs staged source gold
write affected keys and deltas
atomically promote staged gold to data/gold/<source>
```

That establishes the artifact contract without immediately making gold itself
row-incremental.

Later optimization:

```text
consume silver deltas directly
rebuild only source-local gold rows touched by added/removed raw records
```

Resolver mapping changes:

- Add resolver mapping digest to gold manifest / `_SUCCESS.json`.
- If resolver digest changes, rebuild and diff affected gold sources.
- Entity key remapping should appear as removed old key plus added new key.
- Relation keys include endpoint entity keys, so relation affected keys must
  include old and new relation keys after remapping.

## Stage 3: Combine Run Deltas

Current combine behavior:

- DuckDB state is canonical local state.
- `latest/` parquet files are exported current snapshots.
- Pipeline currently derives affected keys by comparing previous combined
  evidence to current source gold evidence.

Relevant files:

- `omnipath_build/gold/combine_duckdb.py`
- `omnipath_build/gold/combine.py`
- `omnipath_build/pipeline/dag.py`

Target behavior:

- Combine consumes gold affected-key parquet artifacts directly.
- Pipeline stops passing large Python sets/lists of affected keys.
- Combine writes run-scoped affected keys and deltas.

Keep current snapshot:

```text
data/combined/latest/
  entity.parquet
  entity_relation.parquet
  entity_relation_evidence.parquet
  entity_evidence.parquet
  relation_annotation_term.parquet
  resources.parquet
  combined_build_summary.json
```

Add run artifacts:

```text
data/combined/runs/<run_id>/
  manifest.json
  affected/entity_keys.parquet
  affected/relation_keys.parquet
  delta/entity_upsert.parquet
  delta/entity_delete.parquet
  delta/entity_relation_upsert.parquet
  delta/entity_relation_delete.parquet
  delta/entity_relation_evidence_upsert.parquet
  delta/entity_evidence_upsert.parquet
  delta/entity_evidence_delete.parquet
  delta/relation_annotation_term_upsert.parquet
  delta/relation_annotation_term_delete.parquet
```

Core schemas:

```text
affected/entity_keys.parquet
  source: string?
  entity_key: string
  change_type: string?
  reason: string?
```

```text
affected/relation_keys.parquet
  source: string?
  relation_key: string
  change_type: string?
  reason: string?
```

Upsert files use the same schema as their current-state target tables.

Delete files:

```text
entity_delete.parquet
  entity_id: int64
  entity_key: string
```

```text
entity_relation_delete.parquet
  relation_id: int64
  relation_key: string
```

```text
entity_evidence_delete.parquet
  source: string
  entity_key: string
```

```text
relation_annotation_term_delete.parquet
  relation_id: int64
```

Important combine detail:

When entity keys are affected, combine should also expand affected relation
keys for relations whose participant entity keys changed, because
`participant_types` depends on entity state.

## Stage 4: Postgres Consumes Combine Deltas

Current Postgres incremental load still filters full combined parquet files by
in-memory affected key lists.

Relevant files:

- `omnipath_build/postgres/postgres.py`
- `omnipath_build/postgres/bitmaps.py`
- `omnipath_build/pipeline/dag.py`

Target behavior:

- Postgres incremental mode takes a combine run manifest or delta directory.
- It stages delta parquet files into temp tables.
- It deletes/upserts affected rows from delta files only.
- It refreshes bitmaps for affected IDs from staged deltas.

Suggested flow:

1. Stage `*_delete.parquet` and `*_upsert.parquet`.
2. Remove old bitmap memberships for affected IDs.
3. Delete affected relation annotations, evidence, relations, and entity evidence.
4. Upsert entities and rebuild `entity_identifier` from `entity_upsert`.
5. Upsert relations.
6. Insert replacement relation evidence and relation annotations.
7. Insert entity evidence upserts.
8. Add new bitmap memberships.

Full rebuild remains available:

```text
drop_existing=True -> full load from combined/latest
```

## Pipeline Planning Contract

The pipeline should analyze before execution and print:

```text
[plan] execution plan
[plan]   reuse silver:signor -> ...
[plan]   run   gold:signor -> ...
[plan]   run   combine -> incremental sources=signor entities=12 relations=34
[plan] Press Enter to execute this plan, or Ctrl+C to abort.
```

Long-term, the planner should read manifests and delta artifacts:

- Bronze manifest/delta decides whether silver runs.
- Silver manifest/delta decides whether gold runs.
- Gold affected-key artifacts decide whether combine runs.
- Combine run delta decides whether Postgres incremental load runs.

Do not use CLI intent alone to decide work. CLI `from=` only chooses where to
start checking.

## Cleanup Targets

After artifact-driven deltas are implemented:

- Remove or demote `pipeline.dag._read_key_hashes`.
- Remove or demote `pipeline.dag._changed_keys_by_row_hash`.
- Replace `_collect_affected_keys` with artifact loading.
- Stop putting full affected key arrays into pipeline reports.
- Stop passing affected keys as large Python sets/lists between stages.
- Stop Postgres incremental filtering of full `latest/*.parquet`.
- Keep JSON affected-key CLI inputs only as temporary compatibility, or convert
  them immediately to temp affected-key parquet files.

## Suggested Test Plan

1. Bronze:
   - Added record emits `added`.
   - Removed record emits `removed`.
   - Content edit emits `removed + added`.

2. Silver:
   - Initial bootstrap writes state and empty/complete delta as expected.
   - Added bronze record creates added silver rows.
   - Removed bronze record creates removed silver rows.
   - Silver state after incremental equals fresh bootstrap.

3. Gold:
   - Added/removed silver rows produce affected entity/relation key artifacts.
   - Resolver mapping change emits old and new entity/relation keys.
   - Source-local deletion emits removed affected keys.

4. Combine:
   - Consumes gold affected-key parquets.
   - Writes run delta artifacts.
   - Incremental latest output equals fresh bootstrap.
   - Entity changes expand relation affected keys when participant types change.

5. Postgres:
   - Bootstrap empty schema, then apply combine delta.
   - Compare base tables, relation annotations, and bitmaps against a fresh
     bootstrap.

## Recommended Next Step

Start with gold affected-key artifacts, even before silver is fully delta-native.

Reason:

- It immediately removes the weakest current bridge: combine affected-key
  inference by row-hashing combined evidence against source gold evidence.
- Gold can initially rebuild a source and diff previous vs staged output.
- This gives combine and Postgres a stable artifact contract while silver
  state/delta is implemented in parallel.

After that, implement silver state/delta and make gold consume silver deltas
directly.
