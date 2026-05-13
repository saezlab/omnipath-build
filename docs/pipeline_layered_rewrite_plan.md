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

Archive creation now writes these public archive Parquet members directly from
the source DuckDB state. It no longer exports DuckDB tables to an intermediate
source-gold Parquet directory and then reads those Parquets back through the
generic archive builder.

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

For a non-empty source scope, the direct builder loads only scoped silver rows
and builds scoped changed source-gold frames. Current source-gold state remains
in DuckDB for the incremental apply path: changed frames are staged as temporary
DuckDB tables, existing fingerprints are stabilized from scoped DuckDB probes,
registry rows are extended in DuckDB, affected current rows are selected by SQL,
and the final scoped delete/insert mutation is applied in place to `gold_*`
tables. The apply path no longer reads all current source-gold tables into
Polars and then replaces the full DuckDB state. Raw-record deletions are handled
from `source_run_scope_raw_record`: even when silver has no current occurrences
left for a deleted raw record, gold removes the old entity evidence,
occurrence-map rows, and relation evidence for that raw record. After the merge,
staged frames are not compared as a whole source. Gold decides `gold_changed`
from the explicit scoped dependency closure: scoped entity evidence, occurrence
maps, fingerprint maps, affected entity aggregates, scoped relation evidence,
affected relation aggregates, and registry rows for affected keys. State and zip
archive are written only if that scoped delta is non-empty. If there is no
scoped delta, `source_run_scope_entity` and `source_run_scope_relation` are
cleared for the run and the latest archive pointer is reused. If there is a
scoped delta, gold writes scoped entity/relation keys to
`source_run_scope_entity` and `source_run_scope_relation`; deleted keys remain in
the scope tables with nullable final partition columns so downstream layers can
delete stale combined state.

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

### Combined Rewrite

Implemented files:

```text
omnipath_build/rewrite/combine.py
omnipath_build/rewrite/combine_duckdb.py
omnipath_build/rewrite/build_resources.py
omnipath_build/rewrite/__init__.py
omnipath_build/cli/commands.py
Makefile
```

The rewrite command now runs bronze, silver, source gold, and then combined:

```bash
make rewrite_pipeline SOURCES=signor,uniprot
```

Combined can also be run against existing rewrite source-gold state:

```bash
uv run python -m omnipath_build.cli.commands combined-rewrite signor,uniprot \
  --data-root data_rewrite
```

The combined rewrite reads source-local DuckDB files directly:

```text
data_rewrite/state/sources/<source>.duckdb
```

and writes combined state to:

```text
data_rewrite/state/combined.duckdb
```

Public combined exports are written to:

```text
data_rewrite/artifacts/combined/latest/
  entity.parquet
  entity_evidence.parquet
  entity_relation.parquet
  entity_relation_evidence.parquet
  relation_annotation_term.parquet
  resources.parquet
```

Combined reports are written separately:

```text
data_rewrite/reports/combined/
  combined_build_summary.json
  relation_annotation_summary.json
  build_manifest.jsonl
  latest.json
  runs/<combined_run_id>.json
```

There is no `data_rewrite/combined/` output directory in the rewrite path, and
combined no longer writes `runs/<run_id>/affected/` Parquet artifacts. Affected
keys are internal state/scope only.

Public combined Parquet does not expose internal source/merge keys or
partitioning columns such as `entity_key`, `relation_key`, `entity_bucket`,
`entity_part`, `relation_bucket`, or `relation_part`. The public join contract
is the stable numeric IDs: `entity_id`, `relation_id`,
`subject_entity_id`, `object_entity_id`, and `term_entity_id`.

The rewrite has its own combine engine copy under `omnipath_build/rewrite`.
The legacy/current `omnipath_build/gold/combine_duckdb.py` and
`omnipath_build/gold/build_resources.py` paths remain unchanged. The rewrite
engine accepts attached source DuckDB shards in addition to legacy source-gold
Parquet directories. Source tables are read from
`gold_entity`, `gold_entity_evidence`, `gold_entity_relation`, and
`gold_entity_relation_evidence`.

Combined rewrite has a local `CombinedRewriteConfig` instead of importing
source-gold `GoldPartitionConfig`. `bucket_count` remains the stable hashing
space, while `part_count` is only an internal recompute chunk count. The default
combined rewrite `part_count` is `16`; source-gold still defaults to `128`.

Combined state currently includes the existing combine state tables:

```text
entity_key_map
relation_key_map
entity
entity_source
entity_evidence
entity_relation
relation_source
entity_relation_evidence
```

and adds rewrite-oriented run metadata/scope tables:

```text
combined_run
combined_run_scope_entity
combined_run_scope_relation
```

These scope tables are for recompute and reporting inside
`combined.duckdb`; they are not downstream handoff artifacts.

When combined state is empty, the rewrite bootstraps from all selected source
state. When combined state exists, it builds affected global key tables from
`source_run_scope_entity` and `source_run_scope_relation` in the attached source
state files, expands relation scope for affected entities, and recomputes the
affected parts.

Manual `signor,uniprot` validation:

- bootstrap from `data_rewrite/state/sources/*.duckdb` completed and exported
  `data_rewrite/artifacts/combined/latest`;
- incremental combine from source scope tables completed and preserved row
  counts;
- state/export row counts matched internally:
  - `entity`: `70,227`
  - `entity_evidence`: `77,895`
  - `entity_relation`: `512,266`
  - `entity_relation_evidence`: `520,751`
  - `relation_annotation_term`: `649,816`
  - `resources`: `30`

Observed parity gap against current `data/combined/latest`:

- current reference has `69,756` entities and `1,225,579` relations;
- rewrite has `70,227` entities and `512,266` relations;
- this follows the already documented source-gold mismatch, especially
  `uniprot` source-gold relation aggregation (`523,918` rewrite relations vs
  `1,192,785` current source-gold relations while relation evidence row counts
  match).

Useful manual probes:

```sql
select count(*) from combined_run;
select count(*)
from combined_run_scope_entity
where combined_run_id = '<combined_run_id>';
select count(*)
from combined_run_scope_relation
where combined_run_id = '<combined_run_id>';
select count(*) from entity;
select count(*) from entity_evidence;
select count(*) from entity_relation;
select count(*) from entity_relation_evidence;
```

Postgres handoff contract:

- bootstrap/full refresh reads only the flat public snapshot under
  `data_rewrite/artifacts/combined/latest`;
- incremental loading should read concrete delete/upsert staging tables from
  `data_rewrite/state/combined.duckdb`, not affected-key files;
- if an external loader cannot read DuckDB state directly, a future optional
  export can materialize those delete/upsert staging tables under
  `data_rewrite/artifacts/postgres_delta/<combined_run_id>/`;
- affected entity/relation keys remain internal recompute scope and are not a
  Postgres handoff contract.

## Next Steps

### 1. Source Gold Incremental Merge

Extend the scoped merge tests until changed, deleted, and source-all scopes are
covered well enough to use source scopes as combined input.

### 2. Combined Rewrite Hardening

Continue parity work after source-gold relation aggregation is aligned with the
current checked-in reference. Add tests for bootstrap, empty source scopes, and
incremental changed/deleted source scopes.

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
