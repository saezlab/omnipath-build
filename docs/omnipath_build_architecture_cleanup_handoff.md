# OmniPath Build architecture cleanup handoff

## Goal
Reduce the active surface area of `omnipath_build`, move clearly obsolete code to `_archive`, and reorganize the remaining code around the workflows we still use.

## Current read of the codebase
There are effectively **two generations** of pipeline code in the repo:

### 1. Active / still useful
These are the parts that appear to matter for the current direction:

- `omnipath_build/loaders/silver.py`
  - dynamic discovery + silver materialization
- `omnipath_build/gold_pipeline/`
  - new source DAG: `resolver_mappings -> silver -> gold`
  - versioned `data_v2/silver/<source>/<n>`
  - versioned `data_v2/gold/<source>/<n>`
  - reports under `data_v2/reports`
- `scripts/silver_to_target_schema.py`
  - still used by `gold_pipeline.tasks`
  - despite the name, this is really the per-source gold package builder
- `scripts/target_schema_entity_dedup.py`
  - still used by `gold_pipeline.tasks`
- `omnipath_build/package_emitter/`
  - still used by `scripts/silver_to_target_schema.py` for silver path resolution
- `omnipath_build/target_schema/`
  - helper logic still used by conversion / dedup
- `id_resolver/*`
  - shared dependency for canonicalization

### 2. Older pipeline generations / likely legacy
These look like older or parallel implementations and are good archive candidates unless someone still depends on them:

- `omnipath_build/pipeline/`
  - older large DAG pipeline for freshness, local gold, combined gold, search parquet, Meilisearch import
- `omnipath_build/pipeline/run_dag.py`
- `omnipath_build/pipeline/import_indexes.py`
- `omnipath_build/loaders/gold.py`
  - old multi-step gold loader based on `omnipath_build/gold/*`
- `omnipath_build/gold/`
  - old local/global table pipeline, not used by the new `gold_pipeline`
  - exception: maybe keep individual pieces temporarily if still needed by debug tools
- `omnipath_build/cli/commands.py`
  - still exposes old `gold` semantics
- `scripts/build_global_entity_identifiers.py`
  - old global aggregation flow
- `omnipath_build/scripts/build_merge_edge_debug_snapshot.py`
  - debug utility for old gold identity logic
- `omnipath_build/search/`
- `omnipath_build/search_builder/`
- `omnipath_build/meilisearch-importer/`
  - only relevant if search/index pipeline is still active

## Immediate cleanup recommendations

### A. Archive old DAG/search stack
If the current priority is the new source pipeline only, move these to `_archive`:

- `omnipath_build/pipeline/`
- `omnipath_build/search/`
- `omnipath_build/search_builder/`
- `omnipath_build/meilisearch-importer/`
- `omnipath_build/scripts/build_merge_edge_debug_snapshot.py`

Do this only if nobody is actively using the search/index build.

### B. Archive old gold loader stack
Likely archive:

- `omnipath_build/loaders/gold.py`
- `omnipath_build/gold/`

These represent the old “local/global gold tables” architecture and are not part of the new `gold_pipeline` path anymore.

### C. Simplify CLI ownership
Long-term, there should probably be **one active CLI entrypoint** for the current pipeline.

Good target:
- keep `python -m omnipath_build.gold_pipeline.cli`

Then either:
- remove `omnipath_build/cli/commands.py`, or
- reduce it to a thin compatibility wrapper that only forwards to the active pipeline.

## Recommended folder structure for the active code
The current active code is spread across `gold_pipeline/`, `package_emitter/`, `target_schema/`, and top-level `scripts/`.

A cleaner structure would be:

```text
omnipath_build/
  archive/
  silver/
    discover.py
    build.py
    paths.py
  gold/
    build.py              # convert + dedup + canonicalize per source
    canonicalize.py
    dedup.py
    schema_helpers.py
  pipeline/
    cli.py
    dag.py
    reports.py
    state.py
  shared/
    paths.py
    logging.py
```

### Practical version of that, with less churn
If we want minimal movement, a smaller cleanup would be:

- keep `omnipath_build/gold_pipeline/` as the orchestration layer
- create:
  - `omnipath_build/gold_package/`
    - move code out of `scripts/silver_to_target_schema.py`
    - move code out of `scripts/target_schema_entity_dedup.py`
- move reusable path helpers from `package_emitter/config.py` into a more neutral module, e.g.:
  - `omnipath_build/shared/paths.py`
- keep `omnipath_build/target_schema/` only for schema/canonical helper functions

That would let us retire the top-level scripts and make the active pipeline import only package modules.

## Concrete next steps

### Phase 1: classify and freeze
1. Confirm whether search/index build is still needed.
2. Confirm whether old `omnipath_build/gold/*` outputs are still needed.
3. If not needed, move those directories to `_archive` in one pass.

### Phase 2: remove script-as-library usage
Right now the active pipeline imports from top-level scripts:
- `scripts/silver_to_target_schema.py`
- `scripts/target_schema_entity_dedup.py`

Refactor these into package modules, e.g.:
- `omnipath_build/gold_package/converter.py`
- `omnipath_build/gold_package/dedup.py`

Then update `omnipath_build/gold_pipeline/tasks.py` to import from there.

### Phase 3: consolidate paths and naming
Unify naming around the active concepts:
- `silver`
- `gold`
- `resolver mappings`
- `reports`

Avoid remaining names like:
- `target_schema_pipeline`
- `package_emitter` for code that is no longer really package emission
- old `local/global tables` naming if that pipeline is archived

## Suggested keep / archive split

### Keep active
- `omnipath_build/gold_pipeline/`
- `omnipath_build/loaders/silver.py`
- `omnipath_build/package_emitter/` (temporarily, until paths are moved)
- `omnipath_build/target_schema/`
- `omnipath_build/utils/` (selectively)
- `omnipath_build/validators/`
- `scripts/silver_to_target_schema.py` (temporarily)
- `scripts/target_schema_entity_dedup.py` (temporarily)

### Good archive candidates
- `omnipath_build/pipeline/`
- `omnipath_build/loaders/gold.py`
- `omnipath_build/gold/`
- `omnipath_build/search/`
- `omnipath_build/search_builder/`
- `omnipath_build/meilisearch-importer/`
- `scripts/build_global_entity_identifiers.py`
- old debug scripts tied to the archived pipeline

## Main architectural opinion
Yes: **a lot of the repo can probably move to `_archive`**.

The active direction now looks much smaller and cleaner:
- one shared silver builder
- one per-source gold builder
- one orchestrator DAG
- one shared resolver dependency
- one report/state layer

The main remaining cleanup is to stop importing core functionality from top-level `scripts/` files and move that logic into a proper package module.
