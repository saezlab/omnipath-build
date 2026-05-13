# Layered Pipeline Rewrite Plan

Rewrite the pipeline layer by layer. The current pipeline remains the reference
output under `./data`, while rewrite outputs are written under `./data_rewrite`.

Current reference command:

```bash
make pipeline SOURCES=uniprot,signor
```

Current rewrite command:

```bash
make rewrite_pipeline SOURCES=signor,uniprot
```

Each rewrite layer should produce equivalent outputs before the next layer
starts. Manual comparisons against `./data` are enough for now; no separate
validation harness is required.

## Implemented Foundation

### Bronze Rewrite

Implemented files:

```text
omnipath_build/rewrite/bronze.py
omnipath_build/rewrite/__init__.py
omnipath_build/cli/commands.py
Makefile
```

The rewrite entry point is:

```bash
make rewrite_pipeline SOURCES=signor,uniprot
```

This currently runs the bronze rewrite only. It discovers raw datasets through
the existing `pypath.inputs_v2` resource discovery path and writes source-local
DuckDB state:

```text
data_rewrite/
  state/
    sources/
      signor.duckdb
      uniprot.duckdb
```

Each source DuckDB contains:

```text
bronze_dataset_snapshot
bronze_raw_record_registry
bronze_raw_record_current
bronze_raw_record_change
bronze_raw_record_current__<dataset_slug>__<hash>
```

The generic bronze tables own snapshot metadata, stable raw IDs, current key
state, and affected scopes. Raw payload data lives in one dataset-local typed
current table per raw dataset:

```text
bronze_raw_record_current__<dataset_slug>__<hash>
```

These typed tables preserve the original cleaned raw parser column names and add
lineage columns:

```text
_source
_dataset
_raw_record_key
_raw_record_id
raw_record_bucket
raw_record_part
snapshot_id
```

Stable raw record IDs use the same algorithm as the current pipeline:

```text
raw_record_id = raw_record_bucket * 1_000_000_000_000 + local_id
```

The bronze rewrite does not export Parquet. DuckDB state is the only rewrite
bronze output. Compatibility should be checked by querying DuckDB directly and
comparing summaries/probes against current `data/bronze`.

Verified manually for `signor,uniprot`:

- row counts match current bronze manifests;
- duplicate raw-key counts match;
- distinct raw record ID counts match;
- first-run change scopes match;
- distinct `(raw_record_key, raw_record_id)` sets match.

Useful manual probes:

```sql
select dataset, count(*)
from bronze_raw_record_current
group by dataset
order by dataset;

select dataset, count(distinct raw_record_key), count(distinct raw_record_id)
from bronze_raw_record_current
group by dataset
order by dataset;

select dataset, change_type, count(distinct raw_record_key)
from bronze_raw_record_change
group by dataset, change_type
order by dataset, change_type;

select dataset, count(*) as duplicate_row_count
from (
  select dataset, raw_record_key, count(*) as n
  from bronze_raw_record_current
  group by dataset, raw_record_key
)
where n > 1
group by dataset
order by dataset;
```

The `bronze_dataset_snapshot.manifest_json` records the typed table name for
each dataset as `typed_current_table`.

## Next Steps

### 1. Silver Rewrite

Initial implementation files:

```text
omnipath_build/rewrite/silver.py
omnipath_build/rewrite/__init__.py
omnipath_build/cli/commands.py
Makefile
```

The rewrite command now runs bronze and then silver:

```bash
make rewrite_pipeline SOURCES=signor,uniprot
```

Silver can also be run against existing rewrite bronze state:

```bash
uv run python -m omnipath_build.cli.commands silver-rewrite signor,uniprot \
  --data-root data_rewrite
```

Implement silver tables in the same source DuckDB files:

```text
silver_entity_occurrence
silver_entity_identifier
silver_entity_annotation
silver_membership
silver_membership_annotation
```

Read affected raw records from DuckDB and update silver tables by scope.

Validate silver outputs against current `./data/silver`.

Use `bronze_raw_record_change` as the affected raw-record scope. For first runs,
all added raw keys should be mapped. For subsequent runs, only added and removed
raw keys should drive silver updates where the dataset supports raw-keyed
incremental mapping.

The current parser mapper path expects Python dictionaries. The silver rewrite
should read from the dataset-local typed raw tables and avoid `json.loads` in
the hot path.

```text
dataset-local typed raw tables with original raw parser column names
```

Then emit silver rows into DuckDB with lineage columns:

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

Current silver rewrite behavior:

- reads dataset-local typed bronze tables directly from source DuckDB state;
- uses `bronze_raw_record_change` for affected raw-record IDs;
- bootstraps a dataset from all current bronze rows if no silver rows exist yet;
- deletes and rewrites affected silver rows by `(source, dataset, raw_record_id)`;
- maps raw dictionaries with the existing dataset mapper and expands entities
  into DuckDB silver tables.

Verified manually:

- `signor` rewrite silver row counts match current `data/silver/signor/state`
  for all five canonical silver tables.
- `uniprot` protein rows match current state, while rewrite also maps the
  available `ontology` bronze dataset into silver. The current checked-in
  `data/silver/uniprot/state` only contains `proteins`, so the total source
  counts differ by the ontology rows.

For manual comparison while silver is being implemented, inspect summary counts
and selected row probes against current:

```text
data/silver/<source>/state/<table>/part=00000/data.parquet
data/silver/<source>/<version>/<table>/part=00000/data.parquet
data/silver/<source>/<version>/delta/<table>/...
data/silver/<source>/<version>/manifest.json
data/silver/<source>/latest.json
```

### 2. Source Gold Rewrite

Implement source-gold tables and key registries in source DuckDB files.

Use one merge path for both incremental updates and full source scopes.

Export source gold compatibility artifacts and validate against current
`./data/gold`.

### 3. Combined Rewrite

Implement combined state in `combined.duckdb`.

Attach source DuckDB files, read source-gold state directly, maintain global ID
registries, and export combined Parquet.

Validate against current `./data/combined/latest`.

### 4. Pipeline Metadata

Add orchestration metadata once source and combined layers are stable enough to
need run tracking:

```text
data_rewrite/state/pipeline.duckdb
```

Suggested tables remain:

```text
pipeline_run
pipeline_task_run
source_run_index
combined_run_index
artifact_registry
input_signature
latest_pointer
```

### 5. Postgres Handoff

Generate concrete combined table delete/upsert deltas from combined DuckDB state.

Validate bootstrap and incremental Postgres loads end with equivalent tables.

### 6. Cutover

After all layers pass comparison:

- switch the default pipeline command to the rewrite;
- keep the old pipeline behind an explicit compatibility command;
- stop writing internal Parquet state by default;
- keep public exports, release artifacts, reports, and optional debug exports.
