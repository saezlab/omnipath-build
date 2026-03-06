# DAG + Freshness Incremental Build Implementation Spec (Local-First)

## Status

- **Target**: New implementation for local testing.
- **Compatibility policy**: **No backward compatibility**, **no legacy fallback paths**, **no dual-mode behavior**.
- **Resilience policy**: source-scoped errors are recorded and the previous version artifact is reused for that source when available.
- **Scope**: Replace current incremental behavior with deterministic DAG + artifact fingerprinting + freshness-driven invalidation.

---

## 1. Goals

1. Stop overwriting the latest version by default.
2. Recompute only what is required.
3. Drive invalidation by:
   - source freshness checks (remote file changed),
   - code/config changes,
   - upstream artifact hash changes.
4. Keep implementation simple:
   - fine-grained per-source tasks,
   - coarse combined tasks.
5. Reuse existing Meilisearch incremental import logic for changed search datasets.

---

## 2. Explicit Non-Goals

1. No support for legacy workflow behavior.
2. No migration adapters for old manifests.
3. No “best effort fallback” to legacy/full-rebuild logic when planner data is missing.
4. No optional scoped manual execution mode in this phase.
5. No partial `combined_gold` recomputation in this phase (it remains a single coarse task).

---

## 3. Hard Constraints (must follow)

1. **No backward compatibility code paths.**
2. **No fallback branches** to old build behavior.
3. **Fail fast** on missing/invalid manifest or artifact metadata.
4. Source-scoped runtime/check errors must use explicit `reused_on_error` behavior (reuse prior source artifact) when prior artifact exists.
5. All task execution decisions must be made from declared fingerprints + dependency graph only.
6. New run directory is always a new `data/v-YYYYMMDD-HHMMSS` directory.

---

## 4. High-Level Architecture

### 4.1 Task graph

Two levels only.

#### Per-source tasks (for each source `S`)

1. `freshness_scan:S`
2. `silver:S`
3. `local_gold:S`

#### Combined tasks

4. `combined_gold` (entity_identifiers + global_tables together)
5. `search_entities`
6. `search_interactions`
7. `search_associations`
8. `search_sources`
9. `index_import:entities`
10. `index_import:interactions`
11. `index_import:associations`
12. `index_import:sources`

### 4.2 Dependency edges

- `silver:S` depends on `freshness_scan:S`.
- `local_gold:S` depends on `silver:S`.
- `combined_gold` depends on all `local_gold:S`.
- each `search_*` depends on `combined_gold` (and `search_sources` also reads per-source reports as today).
- each `index_import:*` depends on corresponding `search_*` parquet artifact.

---

## 5. Data Layout

All paths are mandatory.

```text
data/
  artifacts/
    <artifact_hash>/
      metadata.json
      files/...                  # task outputs stored content-addressed

  v-YYYYMMDD-HHMMSS/
    manifest.json               # full run manifest
    build/
      materialized/             # symlink/hardlink view for current pipeline consumers
      freshness/
        report.json             # full freshness artifact
    output/
      ...                       # exported release files

  latest -> v-YYYYMMDD-HHMMSS
```

### 5.1 No legacy directories

Do not keep or read old build layout conventions in planner logic. Consumers should use materialized outputs generated from current manifest.

---

## 6. Manifest Specification

File: `data/<version>/manifest.json`

```json
{
  "version": "v-20260305-143000",
  "created_at": "2026-03-05T14:30:00Z",
  "base_version": "v-20260303-113436",
  "runtime": {
    "uv_lock_sha256": "...",
    "git_commit": "..."
  },
  "tasks": {
    "freshness_scan:bindingdb": {
      "task_type": "freshness_scan",
      "source": "bindingdb",
      "fingerprint": "...",
      "artifact_hash": "...",
      "status": "executed",
      "deps": []
    },
    "silver:bindingdb": {
      "task_type": "silver",
      "source": "bindingdb",
      "fingerprint": "...",
      "artifact_hash": "...",
      "status": "executed_or_reused",
      "deps": ["freshness_scan:bindingdb"]
    }
  }
}
```

### 6.1 Required fields

- `fingerprint`: deterministic hash over task inputs.
- `artifact_hash`: content hash of output artifact directory.
- `status`: one of `executed`, `reused`, `reused_on_error`.
- `deps`: exact dependency task keys.

No optional compatibility fields.

---

## 7. Freshness Artifact Specification

File: `data/<version>/build/freshness/report.json`

```json
{
  "checked_at": "2026-03-05T14:20:00Z",
  "sources": {
    "bindingdb": {
      "status": "changed",
      "method": "redownload_hash_compare",
      "resources": [
        {
          "resource_id": "bindingdb_main",
          "url": "...",
          "status": "changed",
          "method": "etag|last_modified|size|remote_hash|redownload_hash_compare",
          "local": {
            "etag": "...",
            "last_modified": "...",
            "size": 123,
            "sha256": "..."
          },
          "remote": {
            "etag": "...",
            "last_modified": "...",
            "size": 456,
            "sha256": "..."
          }
        }
      ]
    }
  }
}
```

### 7.1 Allowed source statuses

- `changed`
- `unchanged`
- `error_reused`
- `error_blocking`

No `unknown` status in this phase.

### 7.2 Freshness decision policy

- Any resource `status=changed` => source `status=changed`.
- If all resources `unchanged` => source `status=unchanged`.
- Any check failure with prior source artifact available => source `status=error_reused` (record error, reuse previous source artifact, continue run).
- Any check failure without prior source artifact => source `status=error_blocking` and abort run.

---

## 8. Fingerprint Rules

Each task fingerprint is SHA-256 over canonical JSON:

```json
{
  "task_key": "silver:bindingdb",
  "task_type": "silver",
  "params": {...},
  "code_hashes": {...},
  "config_hashes": {...},
  "dep_artifact_hashes": [...],
  "runtime_hashes": {...},
  "freshness_inputs": {...}
}
```

### 8.1 Inputs by task type

#### `freshness_scan:S`
- source identity
- freshness policy config
- code hash of freshness implementation
- runtime hashes

#### `silver:S`
- source identity
- code hash: `pypath.inputs_v2.<source>` module tree
- code hash: silver loader implementation
- relevant config hash
- dependency artifact hash: `freshness_scan:S`
- runtime hashes

#### `local_gold:S`
- code hash: local table builder
- dependency artifact hash: `silver:S`
- runtime hashes

#### `combined_gold`
- code hash: global gold builder(s)
- dependency artifact hashes: all `local_gold:S`
- runtime hashes

#### `search_*`
- code hash: corresponding search builder module
- dependency artifact hash: `combined_gold`
- runtime hashes

#### `index_import:*`
- code hash: importer implementation
- dependency artifact hash: corresponding `search_*`
- runtime hashes

---

## 9. Planner + Executor Algorithm

## 9.1 Build plan

1. Load previous manifest from `data/latest/manifest.json`.
2. Construct full DAG for discovered sources.
3. Topologically evaluate tasks.
4. For each task:
   - compute fingerprint,
   - compare with previous task fingerprint,
   - if equal: mark `reused` and reuse prior `artifact_hash`,
   - else: execute task, write artifact, compute `artifact_hash`, mark `executed`.
5. If a **per-source** task execution/check fails:
   - if previous artifact for the same task/source exists: mark `reused_on_error`, attach structured error details, reuse prior `artifact_hash`, continue,
   - otherwise: fail run immediately.

## 9.2 Execution invariants

- Task executes at most once per run.
- Reused (`reused` or `reused_on_error`) task must not execute any side effects.
- Executed task must write outputs only into a temporary work dir, then publish as content-addressed artifact atomically.

## 9.3 Failure behavior

- Combined/global task failure aborts run.
- Per-source task/check failure triggers `reused_on_error` only when prior artifact exists for that source task.
- Per-source task/check failure without prior artifact aborts run.
- Every `reused_on_error` decision must be recorded in manifest + freshness/error report.
- No automatic fallback full rebuild.

---

## 10. Artifact Contract

Each task defines explicit output files. Artifact hash is computed over:

1. relative file paths,
2. file bytes,
3. metadata normalization (mtime ignored).

Store outputs under `data/artifacts/<artifact_hash>/files/...`.

`metadata.json` must include:
- `task_key`
- `fingerprint`
- `artifact_hash`
- `created_at`
- `files` list with per-file sha256 + size

---

## 11. Materialization

After planning/execution, create a materialized view for downstream tools:

`data/<version>/build/materialized/...`

Populate using symlinks (or hardlinks) to artifact files for:
- per-source silver outputs,
- per-source local gold outputs,
- combined gold outputs,
- search outputs.

All existing pipeline steps should read from this materialized tree for local testing.

---

## 12. Index Import Behavior

For each dataset (`entities`, `interactions`, `associations`, `sources`):

- If corresponding `search_*` artifact hash unchanged vs previous run: skip import task.
- If changed: run existing incremental importer against previous parquet and current parquet.

No full reindex fallback in this phase.

---

## 13. CLI / Entry Point Changes

Introduce a single new orchestrator command (example):

```bash
uv run python -m omnipath_build.pipeline.run_dag
```

Required behavior:
1. Always create new `DATA_VERSION`.
2. Use `data/latest` as previous baseline.
3. Execute DAG planner + runner.
4. Write manifest.
5. Update `data/latest` symlink only on successful completion.

No legacy command interleaving in this first implementation.

---

## 14. Implementation Phases

### Phase 1 (must-have)

1. Manifest model + persistence.
2. Fingerprint utilities.
3. Freshness scan task + report artifact.
4. Planner (reuse vs execute).
5. Per-source `silver:S` and `local_gold:S` task wrappers.
6. Coarse `combined_gold` task wrapper.
7. `search_*` + `index_import:*` wrappers.
8. Materialization.

### Phase 2 (optional after local validation)

1. Parallel task execution where dependencies allow.
2. Additional observability/reporting.
3. Finer-grained combined DAG decomposition.

---

## 15. Local Test Plan (mandatory)

### Test A: clean run
- No previous manifest.
- Expect all tasks `executed`.

### Test B: no changes
- Run again without code/data changes.
- Expect all tasks `reused` except freshness scan (if freshness task semantics require execution; if fingerprint stable and report reused, then reused is valid).
- No silver/gold/search recomputation.

### Test C: source data changed
- Simulate changed remote file for one source.
- Expect only that source’s `silver/local_gold` rerun.
- Expect `combined_gold`, `search_*`, and changed `index_import:*` rerun.

### Test D: input module code changed for one source
- Modify `pypath.inputs_v2.<source>` code.
- Expect same invalidation pattern as Test C.

### Test E: search builder code changed
- Modify one search builder module.
- Expect corresponding `search_*` and its `index_import:*` rerun; upstream silver/gold reused.

### Test F: freshness check error with prior source artifact
- Simulate network/check failure for one source with prior artifact available.
- Expect source marked `error_reused`, prior source artifacts reused, run continues.

### Test G: freshness check error without prior source artifact
- Simulate network/check failure for one source with no prior artifact.
- Expect fail-fast (`error_blocking`).

---

## 16. Acceptance Criteria

1. New run version directory is created every run.
2. No overwrite of previous version directory.
3. Manifest fully describes task decisions.
4. Unchanged tasks are reused deterministically.
5. Changed source invalidates only required downstream tasks.
6. Index updates are incremental for changed search datasets.
7. No backward compatibility or legacy fallback code present.
8. Source-scoped errors are recorded and reused from previous artifacts when available (`reused_on_error`).

---

## 17. Developer Rules

1. Do not add compatibility flags.
2. Do not add optional/implicit fallback planner branches; only the explicit `reused_on_error` path is allowed.
3. Do not silently continue on missing manifests/artifacts.
4. Keep task contracts explicit and typed.
5. Prefer small pure functions for fingerprint computation and dependency expansion.