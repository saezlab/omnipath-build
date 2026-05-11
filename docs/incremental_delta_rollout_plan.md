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

## Source Processing Pseudocode

Current end-to-end source processing is a sequence of artifact handoffs. A
pipeline run should inspect manifests and deltas first, print the execution
plan, wait for confirmation unless `--yes` is set, then execute only the needed
stages.

```text
process_source(source, from_stage):
  if from_stage <= download:
    ensure downloads exist in the configured download cache

  if from_stage <= bronze:
    for each raw dataset in source:
      snapshot = preparse raw records
      previous_state = data/bronze/<source>/<dataset>/state/records.parquet

      delta = diff previous_state vs snapshot by _raw_record_key
        added   = snapshot rows not in previous_state
        removed = previous_state rows not in snapshot

      write snapshot records, delta, and manifest
      if snapshot is accepted:
        replace bronze state/records.parquet
        update latest.json

  if from_stage <= silver:
    read bronze delta(s)

    if raw-keyed silver state exists:
      staged_silver = previous silver state
      delete rows whose _raw_record_key was removed upstream
      map only added/changed bronze raw rows
      insert mapped silver rows into staged_silver
    else:
      staged_silver = full source silver build

    silver_delta = diff previous silver state vs staged_silver
    write versioned silver tables, delta tables, and manifest
    promote staged_silver to data/silver/<source>/state/

  if from_stage <= gold:
    read silver manifest and delta

    if no previous gold output:
      build full source gold:
        build granular entities/entity_evidence.parquet from all silver rows
        reduce entities/entity.parquet from entity_evidence
        build relations/entity_relation_evidence.parquet from all silver rows
        reduce relations/entity_relation.parquet from relation_evidence
        write entity_map and entity_occurrence_map indexes
    else if silver delta is empty:
      copy previous gold entities/ and relations/ forward
      write empty gold affected-key and delta artifacts
    else:
      affected_raw_record_ids = raw IDs from silver delta
      changed_silver = filter silver tables to affected_raw_record_ids
      changed_gold = build source gold evidence from changed_silver only

      remap changed entity evidence by fingerprint to preserve existing
      source-local entity keys where the entity already existed

      staged entity_evidence =
        previous entity_evidence
        minus rows whose raw_record_id is affected
        plus changed entity_evidence

      staged entity.parquet =
        reduce staged entity_evidence, preserving existing entity_pk by entity_key

      update entity_map and entity_occurrence_map for changed fingerprints and
      occurrences

      remap changed relation evidence endpoint keys after entity key remapping

      staged relation_evidence =
        previous relation_evidence
        minus rows whose raw_record_id is affected
        plus changed relation_evidence

      staged entity_relation.parquet =
        reduce staged relation_evidence, preserving existing relation_pk by relation_key

      rewrite relation_evidence relation_pk/relation_evidence_pk from staged
      relation table

      affected_entity_keys =
        previous entity_evidence keys touched by affected raw IDs
        union changed entity_evidence keys

      affected_relation_keys =
        previous relation_evidence keys touched by affected raw IDs
        union changed relation_evidence keys

      write gold affected-key and delta artifacts scoped to affected keys

    promote staged gold to data/gold/<source>/
    write _SUCCESS.json and _delta/<build_id>/

  if combine is needed:
    read gold affected-key artifacts

    if no combined state exists:
      bootstrap data/combined/state.duckdb source-by-source and key-batched
    else:
      delete affected entity/relation keys from combined DuckDB state
      read only affected source gold rows by key
      reduce granular source entity_evidence into combined aggregate
        entity_evidence.raw_record_ids
      upsert affected entities, relations, relation evidence, entity evidence,
        and relation annotation terms

    export data/combined/latest/
    write data/combined/runs/<run_id>/affected/*.parquet
    write data/combined/runs/<run_id>/delta/*.parquet

  if postgres is requested:
    if full rebuild requested or no database state exists:
      load data/combined/latest/ from scratch
      rebuild indexes/materialized views/bitmaps
    else:
      read data/combined/runs/<run_id>/delta/*.parquet
      delete affected rows
      upsert replacement rows
      refresh relation annotations and bitmaps for affected IDs
```

## Stage 1: Silver State And Delta

Implemented: silver keeps current versioned outputs and also writes a
source-level `state/` directory plus per-version `delta/` tables and
`manifest.json`. When a raw-keyed state exists, silver uses the bronze delta as
the handoff contract: unchanged rows are streamed from `state/`, removed raw
records are filtered out, and only added/changed bronze rows are mapped again.

Current silver behavior:

- Silver writes versioned source outputs under `data/silver/<source>/<version>/`.
- It writes these current tables:
  - `entity_occurrence.parquet`
  - `entity_identifier.parquet`
  - `entity_annotation.parquet`
  - `membership.parquet`
  - `membership_annotation.parquet`
- Reuse still respects `inputs_module_hash.json` at the pipeline planning
  boundary.
- If bronze delta is empty and raw-keyed silver state exists, silver copies the
  state forward and writes zero-row delta tables.
- If bronze delta has changes, raw entity datasets map only changed bronze rows
  and seed unchanged rows from the previous silver state.
- Sources that are not raw entity datasets fall back to a full source silver
  rebuild.

Relevant files:

- `omnipath_build/silver/build.py`
- `omnipath_build/silver/tables.py`
- `omnipath_build/silver/paths.py`
- `omnipath_build/pipeline/tasks.py`
- `omnipath_build/pipeline/dag.py`

Current layout:

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

Each silver table keeps existing columns and adds provenance / row identity
columns:

```text
_raw_record_key      string
_snapshot_id         string
_silver_row_key      string
_silver_row_hash     uint64
```

Delta tables add:

```text
_change_type         string  # added | removed
```

The row hash intentionally excludes `_snapshot_id`, so an unchanged raw record
does not cause downstream churn only because it appeared in a newer bronze
snapshot.

Pipeline reuse now checks the silver delta: if a silver task executed but every
delta table has zero added and removed rows, an existing gold output for that
source can be reused.

Earlier target notes:

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

Implemented: gold keeps existing current-state paths and adds per-build delta
artifacts. Each gold build stages source output, reads the silver delta when it
is available, scopes affected key derivation to raw records from that silver
delta, writes `_delta/<build_id>/`, then promotes the staged snapshot.

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
  canonical_identifier: string
  canonical_identifier_type: string
  change_type: string
  raw_record_id: string
  occurrence_id: string
  fingerprint: string
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
  subject_entity_key: string
  predicate: string
  object_entity_key: string
  relation_category: string
  raw_record_id: string
  change_type: string
  row_hash: uint64
  reason: string
```

Current implementation:

```text
read silver delta
if silver delta is empty, write empty affected-key and delta artifacts
otherwise derive affected raw_record_ids / occurrence_ids from silver delta
delete affected source gold evidence rows
build changed source gold evidence rows from changed silver rows only
insert changed evidence rows
reduce source gold final tables from evidence
diff previous source gold vs staged source gold only for affected keys when safe
write affected keys and deltas
atomically promote staged gold to data/gold/<source>
```

This establishes the artifact contract and avoids rescanning unrelated gold rows
for delta generation. Source gold updates are represented as evidence
delete+insert operations, followed by reductions to final source tables.

Current source gold evidence rows are being moved toward that contribution
layer:

- `entities/entity_evidence.parquet` is now the granular source entity evidence
  table, with one row per source/raw-record/occurrence/fingerprint
  contribution. It carries `canonical_identifier`,
  `canonical_identifier_type`, `raw_record_id`, `occurrence_id`, and
  `fingerprint`.
- `relations/entity_relation_evidence.parquet` now carries the structural
  relation identity (`subject_entity_key`, `predicate`, `object_entity_key`,
  `relation_category`) in addition to `relation_key`.
- Combined/Postgres-facing exports still keep the aggregate combined
  `entity_evidence.raw_record_ids` shape by reducing granular source evidence
  during combine.
- Source `entity.parquet` and `entity_relation.parquet` are now written through
  reducers over the corresponding source evidence tables while preserving
  existing source-local surrogate IDs. This makes full builds and incremental
  updates share the same projection semantics.

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

Implemented first pass: the DuckDB combine now writes run-scoped affected-key
and delta artifacts under `data/combined/runs/<run_id>/` for both bootstrap and
incremental runs. `data/combined/latest/` remains the current snapshot.

Current combine behavior:

- DuckDB state is canonical local state.
- `latest/` parquet files are exported current snapshots.
- Pipeline consumes gold delta affected-key artifacts.

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

Current run artifacts:

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

For incremental runs, delete files are keyed from stable DuckDB key maps, so a
row deleted from current state still has the old integer ID available for
Postgres deletes. Upsert files are filtered directly from current DuckDB state
after recompute. For bootstrap runs, delete files are empty and upsert files
contain the full current state.

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

Implemented for the DuckDB combine path: incremental entity updates now expand
to existing combined relations whose subject or object entity key is affected,
then replay those relation keys in the same batched relation update path.

## Stage 4: Postgres Consumes Combine Deltas

Implemented first pass: Postgres incremental table loads can consume a combine
run directory directly. The pipeline passes `combine_result.metadata["run_dir"]`
to the loader, so Postgres no longer needs to derive its own delta by filtering
`combined/latest` from affected key arrays. The old affected-key loader remains
as a fallback for manual calls without a run manifest.

Current Postgres incremental load still filters full combined parquet files by
in-memory affected key lists.

Relevant files:

- `omnipath_build/postgres/postgres.py`
- `omnipath_build/postgres/bitmaps.py`
- `omnipath_build/pipeline/dag.py`

Current behavior:

- Postgres incremental mode takes a combine run manifest or delta directory.
- It reads delta parquet files directly.
- It deletes/upserts affected rows from delta files only.
- It refreshes bitmaps for affected IDs from delta delete/upsert files.

Implemented flow:

1. Read `*_delete.parquet` and `*_upsert.parquet`.
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
