# Gold pipeline consolidation plan

## Naming
- Use **gold** for per-source outputs.
- Keep `silver -> gold -> canonicalize` as the core source pipeline.
- Treat `id_resolver` as a **shared dependency**, not a global pipeline stage.

## Target shape
For each selected source:
1. build silver
2. build gold
3. canonicalize gold with `id_resolver`

Shared task:
- materialize / reuse `id_resolver` mapping tables

Archived / out of scope for now:
- global identifier aggregation

## DAG model
Tasks:
- `resolver_mappings`
- `silver:{source}`
- `gold:{source}`
- `canonicalize:{source}`

Dependencies:
- `gold:{source} <- silver:{source}`
- `canonicalize:{source} <- gold:{source}`
- `canonicalize:{source} <- resolver_mappings`

## CLI direction
Keep the current CLI shape, but align semantics with the DAG:
- `source`: run selected sources through canonicalization
- `mappings`: build resolver tables only
- `all`: mappings + selected sources

## What to borrow from the old DAG pipeline
- task graph / dependency planning
- per-task logs and status
- run/build reports
- incremental reuse via fingerprints
- parallel execution across sources

## Storage and reporting
Keep the current `data_v2` structure, but make outputs versioned per source:
- `data_v2/silver/<source>/<version>/...`
- `data_v2/gold/<source>/<version>/...`
- optional stable pointers:
  - `data_v2/silver/<source>/latest`
  - `data_v2/gold/<source>/latest`

Reports stay separate from data outputs, e.g.:
- `data_v2/reports/runs/<run_id>.json`
- `data_v2/reports/latest.json`
- `data_v2/reports/changelog.ndjson`

Each report should record:
- selected sources
- task statuses
- which versions were produced or reused
- resolver mapping version used

## Refactor order
1. **Rename for clarity**
   - `target_schema` concepts -> `gold`
2. **Modularize the script**
   - split CLI, task functions, and path/config helpers
3. **Add DAG planning**
   - explicit task definitions for mappings / silver / gold / canonicalize
4. **Add versioned outputs + reports**
   - keep `data_v2` layout, add per-source versions and run reports
5. **Add reuse/state**
   - fingerprint outputs and skip unchanged tasks
6. **Add parallelism + progress**
   - run per-source chains concurrently

## End state
One gold pipeline with:
- shared resolver mappings
- independent per-source execution
- DAG-based orchestration
- versioned `data_v2/silver` and `data_v2/gold` outputs per source
- run/build reports under `data_v2/reports`
- better naming, logs, reuse, and CLI ergonomics
- no global aggregation stage in the active pipeline
