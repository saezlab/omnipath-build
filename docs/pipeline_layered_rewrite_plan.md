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

The bronze stage discovers raw datasets through the existing `pypath.inputs_v2`
resource discovery path and writes source-local DuckDB state:

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

### Silver Rewrite

Implemented files:

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

Silver tables are stored in the same source DuckDB files:

```text
silver_entity_occurrence
silver_entity_identifier
silver_entity_annotation
silver_membership
silver_membership_annotation
```

The silver rewrite reads affected raw records from DuckDB and updates silver
tables by scope. It uses `bronze_raw_record_change` as the affected raw-record
scope and falls back to a dataset bootstrap from all current bronze rows when
silver state for that dataset does not exist yet.

Affected silver rows are deleted by `(source, dataset, raw_record_id)`, because
raw record IDs are stable per dataset and can collide across datasets in one
source DuckDB.

The current parser mapper path expects Python dictionaries. The silver rewrite
should read from the dataset-local typed raw tables and avoid `json.loads` in
the hot path.

```text
dataset-local typed raw tables with original raw parser column names
```

Silver rows include the existing silver lineage columns plus `source_run_id`:

```text
source
dataset
_raw_record_key
_raw_record_id
raw_record_bucket
raw_record_part
_snapshot_id
source_run_id
```

Current silver rewrite behavior:

- reads dataset-local typed bronze tables directly from source DuckDB state;
- uses `bronze_raw_record_change` for affected raw-record IDs;
- bootstraps a dataset from all current bronze rows if no silver rows exist yet;
- deletes and rewrites affected silver rows by `(source, dataset, raw_record_id)`;
- maps raw dictionaries with the existing dataset mapper and expands entities
  into DuckDB silver tables.

Verified manually for current `data_rewrite` state:

- `signor` rewrite silver row counts match current `data/silver/signor/state`
  for all five canonical silver tables.
- `uniprot` protein rows match current state, while rewrite also maps the
  available `ontology` bronze dataset into silver. The current checked-in
  `data/silver/uniprot/state` only contains `proteins`, so the total source
  counts differ by the ontology rows.

Useful manual probes:

```sql
select dataset, count(*)
from silver_entity_occurrence
group by dataset
order by dataset;

select dataset, count(*)
from silver_entity_identifier
group by dataset
order by dataset;

select dataset, count(*)
from silver_entity_annotation
group by dataset
order by dataset;

select dataset, count(*)
from silver_membership
group by dataset
order by dataset;

select dataset, count(*)
from silver_membership_annotation
group by dataset
order by dataset;
```

For manual comparison, inspect summary counts and selected row probes against
current:

```text
data/silver/<source>/state/<table>/part=00000/data.parquet
data/silver/<source>/<version>/<table>/part=00000/data.parquet
data/silver/<source>/<version>/delta/<table>/...
data/silver/<source>/<version>/manifest.json
data/silver/<source>/latest.json
```

Open silver follow-ups before source gold:

- Decide whether ontology datasets should be included in rewrite silver by
  default, or match current source-state behavior and exclude them unless the
  current pipeline starts retaining ontology silver state.
- Add focused tests around bootstrap mapping, empty change scope reuse, and
  `(source, dataset, raw_record_id)` delete scoping.
- Add a lightweight row-count comparison command or script once a second source
  besides `signor` has exact parity.

### Source Gold Rewrite

Implemented files:

```text
omnipath_build/rewrite/gold.py
omnipath_build/rewrite/gold_direct.py
omnipath_build/rewrite/__init__.py
omnipath_build/cli/commands.py
Makefile
```

The rewrite command now runs bronze, silver, and then source gold:

```bash
make rewrite_pipeline SOURCES=signor,uniprot
```

Gold can also be run against existing rewrite silver state:

```bash
uv run python -m omnipath_build.cli.commands gold-rewrite signor \
  --data-root data_rewrite
```

The gold rewrite builds source-gold from rewrite silver DuckDB tables directly.
The rewrite-owned `gold_direct.py` contains the source-gold builder
orchestration, silver extraction, entity/relation assembly, and DuckDB writes.
It no longer exports rewrite silver to temporary Parquet or calls the legacy
Parquet-oriented source-gold builders. The durable rewrite state is source-local
DuckDB:

```text
data_rewrite/state/sources/<source>.duckdb
```

Gold tables are written into the source DuckDB with a `gold_` prefix:

```text
gold_entity
gold_entity_evidence
gold_entity_map
gold_entity_occurrence_map
gold_entity_relation
gold_entity_relation_evidence
gold_entity_key_registry
gold_relation_key_registry
source_run_scope_entity
source_run_scope_relation
```

The rewrite does not write compatibility gold artifacts under
`data_rewrite/gold`. Validate table parity by querying DuckDB and comparing
against current `./data/gold`.

The rewrite does write the public per-source gold zip archive:

```text
data_rewrite/artifacts/gold/<source>/
  latest.json
  <gold_version>/
    <source>.zip
    manifest.json
```

The current archive compatibility shape is preserved:

```text
entities/entity.parquet
relations/entity_relation.parquet
relations/entity_relation_evidence.parquet
```

The archive is written only when staged source-gold state differs from current
source-gold state. Gold has one update path: apply the current source scope to
source-gold state. `source_run_scope_raw_record` is the primary source update
scope and `source_run_scope_occurrence` is the occurrence detail scope. If there
is no previous `gold_*` state, gold bootstraps by treating all current silver
raw records as the source scope. If both persisted scopes are empty and current
`gold_*` state exists, gold skips staged frame construction entirely, clears
gold scopes, and reuses the latest archive pointer. If the latest archive is
missing, gold regenerates the archive from current DuckDB state without marking
gold changed.

For a non-empty source scope, the direct builder loads only scoped silver rows,
builds scoped changed source-gold frames, stabilizes existing fingerprints
against current `gold_entity_map`, and merges the scoped frames into current
source-gold DuckDB state. The merge replaces scoped entity evidence,
occurrence-map rows, relation evidence, fingerprint-map rows, and recomputed
affected entity/relation aggregates. Raw-record deletions are handled from
`source_run_scope_raw_record`: even when silver has no current occurrences left
for a deleted raw record, gold removes the old entity evidence, occurrence-map
rows, and relation evidence for that raw record. After the merge, staged frames
are not compared as a whole source. Gold decides `gold_changed` from the
explicit scoped dependency closure: scoped entity evidence, occurrence maps,
fingerprint maps, affected entity aggregates, scoped relation evidence, affected
relation aggregates, and registry rows for affected keys. State and zip archive
are written only if that scoped delta is non-empty. If there is no scoped delta,
`source_run_scope_entity` and `source_run_scope_relation` are cleared for the run
and the latest archive pointer is reused. If there is a scoped delta, gold writes
scoped entity/relation keys to
`source_run_scope_entity` and `source_run_scope_relation`; deleted keys remain
in the scope tables with nullable final partition columns so downstream layers
can delete stale combined state.

Scoped frame construction is internally chunked by raw-record ID. Gold first
loads bounded silver chunks to extract entity candidates, deduplicates
fingerprints globally across the scope, canonicalizes that scoped candidate set,
then reloads bounded silver chunks to project entity evidence and relation
evidence against the global scoped entity maps. This avoids materializing all
scoped silver tables in one Polars frame. The final changed source-gold frames
are still materialized for the current DuckDB-state comparison and merge.

Manual `signor` validation with the same effective gold partition config as the
current reference (`part_count=1`, `min_part_size_mb=200`):

- row counts match current `data/gold/signor` for all six source-gold tables;
- `gold_entity_evidence` and `gold_entity_relation_evidence` row counts match;
- full entity/relation key sets do not yet match the checked-in reference.
- latest direct-builder archive verified at
  `data_rewrite/artifacts/gold/signor/20260513T110328957155Z/signor.zip`.
- no-op rerun verified with `gold_changed=False`, `archive_written=False`, and
  empty `source_run_scope_entity` / `source_run_scope_relation`.
- silver no-op followed by gold no-op verified with empty raw, occurrence,
  entity, and relation scope tables; gold returns immediately without building
  staged frames.
- synthetic deletion-scope probe on a copied `signor` state verified that a
  raw-record scope with no current silver occurrences removes old entity
  evidence, occurrence-map rows, and relation evidence, and writes a new archive
  only for the copied changed state. The same probe verified scoped gold output
  scopes: 7 affected entity keys and 76 affected relation keys for the sampled
  raw record.
- chunked bootstrap probe on a copied `signor` state with the internal chunk
  size forced to 100 raw records verified the same row counts as the default
  source build.

Observed mismatch details:

- `entity_key` set symmetric difference: `2,082` each direction;
- `relation_key` set symmetric difference: `12,898` each direction;
- full `entity_evidence` tuple difference over canonical identifier, taxonomy,
  occurrence, fingerprint, type, and raw ID: `14,020` each direction.

The mismatch appears to be taxonomy/canonicalization content, not row-count,
archive-shape, or rewrite silver drift. Example mismatches include the same
UniProt canonical identifier with different taxonomy IDs between rewrite and
current gold.

Parity investigation so far:

- rewrite silver and current silver have identical row counts for all five
  silver tables;
- `entity_occurrence`, `entity_identifier`, `entity_annotation`, and
  `membership` tuple comparisons matched exactly for `signor`;
- rebuilding source-gold entities from the existing current silver did not
  reproduce the checked-in `data/gold/signor` entity keys either;
- therefore this gap is downstream of silver, likely in source-gold
  canonicalization/reduction where grouped `first()` choices depend on input
  row ordering for ambiguous resolver/taxonomy cases.

Useful manual probes:

```sql
select count(*) from gold_entity;
select count(*) from gold_entity_evidence;
select count(*) from gold_entity_map;
select count(*) from gold_entity_occurrence_map;
select count(*) from gold_entity_relation;
select count(*) from gold_entity_relation_evidence;

select count(*) from (
  select entity_key from gold_entity
  except
  select entity_key
  from read_parquet(
    'data/gold/signor/entities/entity/**/*.parquet',
    union_by_name=true,
    hive_partitioning=true
  )
);
```

Open gold follow-ups:

- Make source-gold canonicalization deterministic for ambiguous resolver/taxonomy
  cases, then establish that deterministic output as the comparison baseline.
- Add focused tests for direct DuckDB source-gold materialization and zip export.
- Add tests for no-op gold reruns reusing the latest archive and clearing source
  scopes.
- Add tests for the silver-scope no-op gate in gold.
- Add focused tests for scoped gold updates, especially raw-record deletes,
  relation aggregate recomputation, and existing-fingerprint stabilization.
- Move the final source-gold comparison and table replacement from materialized
  Polars frames toward DuckDB temp tables when larger sources make final changed
  frames too large to keep in memory.

## Next Steps

### 1. Source Gold Incremental Merge

Extend the scoped merge tests until changed, deleted, and source-all scopes are
covered well enough to use source scopes as combined input.

### 2. Combined Rewrite

Implement combined state in `combined.duckdb`.

Attach source DuckDB files, read source-gold state directly, maintain global ID
registries, and export combined Parquet.

Validate against current `./data/combined/latest`.

### 3. Pipeline Metadata

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

### 4. Postgres Handoff

Generate concrete combined table delete/upsert deltas from combined DuckDB state.

Validate bootstrap and incremental Postgres loads end with equivalent tables.

### 5. Cutover

After all layers pass comparison:

- switch the default pipeline command to the rewrite;
- keep the old pipeline behind an explicit compatibility command;
- stop writing internal Parquet state by default;
- keep public exports, release artifacts, reports, and optional debug exports.
