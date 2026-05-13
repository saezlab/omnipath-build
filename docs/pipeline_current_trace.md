# Current OmniPath build pipeline trace

This document describes the current pipeline implementation as it exists in
the codebase. It is intentionally more implementation-focused than
`pipeline_source_trace.md`, which used a small fictional source and now misses
several important current behaviors.

The main point to keep straight:

- `state.duckdb` is persisted state used to make future updates cheap.
- `delta/` and `_delta/` directories are handoff artifacts describing what
  changed or what should be recomputed.
- There are multiple delta concepts at different layers. They are related, but
  they are not interchangeable.

## Code map

The main orchestration entry point is `omnipath_build.pipeline.dag.run_pipeline`.

Important implementation files:

| Area | File | Role |
|---|---|---|
| DAG and reports | `omnipath_build/pipeline/dag.py` | Plans tasks, executes them with dependencies, collects affected scopes, writes reports. |
| Path helpers | `omnipath_build/pipeline/paths.py` | Defines `data/silver`, `data/gold`, `data/reports`, source path mapping, and latest pointers. |
| Pipeline stage tasks | `omnipath_build/pipeline/tasks.py` | Builds silver, resolver mappings, gold source outputs, silver deltas, and gold affected-scope artifacts. |
| Raw record materialization | `pypath/pypath/inputs_v2/raw_records.py` | Builds bronze raw snapshots, assigns raw record IDs, computes bronze deltas. |
| Resource discovery and silver writer | `omnipath_build/silver/build.py`, `omnipath_build/silver/tables.py` | Discovers input datasets, maps raw records to canonical silver tables, writes silver partitioned datasets. |
| Gold entities | `omnipath_build/gold/build_entities.py` | Canonicalizes source entities and writes partitioned source-level gold entity outputs. |
| Gold relations | `omnipath_build/gold/build_relations.py` | Builds source-level relation evidence and relation outputs from silver and entity maps. |
| Gold source state | `omnipath_build/gold/source_state.py` | Maintains source-local `state.duckdb` and exports updated source-level gold outputs. |
| Combined layer | `omnipath_build/gold/combine_duckdb.py` | Maintains global combined `state.duckdb`, exports combined latest outputs, and writes combine run manifests. |
| Postgres load | `omnipath_build/postgres/postgres.py` | Loads combined parquet into Postgres; can apply combine run table deltas if they exist. |

## Data root layout

With default settings, the pipeline writes under `data/`.

```text
data/
  bronze/
    <source>/<dataset>/
      latest.json
      state/
        records/
      <snapshot_id>/
        delta/
        manifest.json

  silver/
    <source>/
      latest
      latest.json
      state/
        entity_occurrence/
        entity_identifier/
        entity_annotation/
        membership/
        membership_annotation/
        manifest.json
      <version>/
        entity_occurrence/
        entity_identifier/
        entity_annotation/
        membership/
        membership_annotation/
        delta/
          entity_occurrence/
          entity_identifier/
          entity_annotation/
          membership/
          membership_annotation/
        manifest.json
        inputs_module_hash.json

  gold/
    <source>/
      entities/
        entity/
        entity_evidence/
        entity_map/
        entity_occurrence_map/
        manifest.json
        canonicalization_report.md
        canonicalization_summary.json
      relations/
        entity_relation/
        entity_relation_evidence/
        manifest.json
      state.duckdb
      state_manifest.json
      _delta/
        latest.json
        <build_id>/
          affected_raw_record_ids.parquet
          affected_occurrence_ids.parquet
          affected_entity_keys.parquet
          affected_entity_buckets.parquet
          affected_entity_parts.parquet
          affected_relation_keys.parquet
          affected_relation_buckets.parquet
          affected_relation_parts.parquet
          manifest.json
      _SUCCESS.json
      <source>.zip

  combined/
    state.duckdb
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
        affected/
          entity_keys.parquet
          relation_keys.parquet

  reports/
    latest.json
    changelog.ndjson
    runs/<run_id>.json
    memory/<run_id>.ndjson
```

Some readers accept both historical single-file tables like
`entity.parquet` and current partitioned datasets like `entity/part=00000`.
The current writers favor partitioned directory datasets.

## 1. Pipeline entry and task graph

`run_pipeline()` normalizes the requested `start_stage` and builds
`PipelinePaths` from `data_root`.

Supported start stages:

| User value | Normalized stage |
|---|---|
| `download`, `from-download` | `download` |
| `bronze`, `from-bronze` | `bronze` |
| `silver`, `from-silver` | `silver` |
| `gold`, `from-gold` | `gold` |

The pipeline then discovers source capabilities under `inputs_package`, which
defaults to `pypath.inputs_v2`.

Task graph rules:

| Task | Created when | Depends on |
|---|---|---|
| `resolver_mappings` | mappings enabled and source builds start before gold | none |
| `silver:<source>` | source builds enabled and start is `download` or `bronze` | none |
| `gold:<source>` | source has gold-buildable entity or ontology datasets and start is before gold | `silver:<source>` if silver is scheduled, plus `resolver_mappings` when enabled |
| `combine` | combine enabled | all scheduled gold tasks |
| `postgres` | Postgres URI provided | `combine` |

Gold-buildable sources are sources whose discovered functions include a
non-`resource` dataset with `output_kind` of `entity` or `ontology`.

`start_stage=gold` does not schedule per-source silver or gold tasks. It runs
combine from existing `data/gold/<source>/` outputs when combine is enabled.

Before execution, the DAG prints a plan. It can reuse resolver mappings when
required resolver parquet files already exist. It can also reuse combine when no
gold task executed and a combined latest output already exists.

Tasks run in a `ThreadPoolExecutor` with `jobs` workers. A failed dependency
normally skips dependents, except `combine` may still run when the failed
dependencies are gold tasks. That allows a partial combined build from the gold
sources that completed.

## 2. Resource discovery

`discover_resources()` imports every public module under the inputs package.

For each source module it discovers:

- one `Resource` object, emitted as the `resource` function when present;
- `Dataset` objects, emitted as `entity` outputs unless marked as
  `id_translation`;
- `OntologyDataset` objects, emitted as `ontology` outputs;
- `ArtifactDataset` objects, emitted as `artifact` outputs;
- datasets nested inside `Resource.datasets()`.

The `resource` function represents source metadata. It is not used to decide
whether a source is gold-buildable. Entity and ontology datasets are.

For regular entity and ontology datasets, discovery wraps the dataset call so
the silver builder invokes it with:

```python
{
    "source": source_name,
    "dataset": dataset_name,
    "use_preparse": True,
    "changed_only": changed_only,
}
```

That wrapper is what connects the silver stage to bronze raw snapshots.

## 3. Download and bronze raw records

Bronze is implemented inside `pypath.inputs_v2.raw_records` and is triggered
from `run_silver_loader()` before a dataset is mapped to silver.

For a `Dataset`, the sequence is:

1. Build a parser contract from the raw parser callable and parser kwargs.
2. Look for an existing downloaded file in `PYPATH_DOWNLOAD_DATADIR` or
   `pypath-data`.
3. Fingerprint the local file when it exists.
4. If fingerprint and parser contract match the latest accepted raw snapshot,
   create a new empty-delta snapshot without reparsing the file.
5. Otherwise open or download the raw file and run the raw parser.
6. Materialize parser dictionaries as bronze raw records.

Bronze default root:

```text
OMNIPATH_BRONZE_ROOT
OMNIPATH_RAW_RECORDS_ROOT
data/bronze
```

The first environment variable set wins; if neither is set, `data/bronze` is
used.

### Bronze records

`materialize_raw_records()` writes one snapshot under:

```text
data/bronze/<source>/<dataset>/<snapshot_id>/
```

Each parser-emitted dictionary is cleaned and augmented:

| Column | Meaning |
|---|---|
| `_source` | Source name. |
| `_dataset` | Dataset/function name. |
| `_raw_record_key` | BLAKE2b-256 hash of the canonicalized parser row. |
| `_raw_record_id` | Stable integer ID assigned after hash computation. |
| `raw_record_bucket` | Stable logical bucket, modulo 4096. |
| `raw_record_part` | Physical part derived from bucket and effective part count. |

Reserved parser column names are renamed by prefixing `raw`, so a parser field
named `_raw_record_id` becomes `raw_raw_record_id`.

Raw records are written as a partitioned dataset:

```text
records/
  part=00000/data.parquet
  part=00001/data.parquet
  ...
```

The raw record key is based on sorted field names and canonical JSON-like bytes
for values. Lists, tuples, dicts, bytes, scalars, and fallback string
representations are normalized before hashing.

### Bronze raw record IDs

Raw record IDs are assigned by bucket. Existing keys reuse their previous
`_raw_record_id`. New keys are assigned after the maximum local ID already seen
in the bucket.

The numeric layout is:

```text
raw_record_id = raw_record_bucket * 1_000_000_000_000 + local_id
```

That makes IDs stable across snapshots while keeping per-bucket assignment
bounded.

### Bronze delta

Bronze delta compares current raw keys with previous accepted raw keys.

```text
delta/
  part=00000/data.parquet
  part=00001/data.parquet
  ...
```

Delta columns:

| Column | Meaning |
|---|---|
| `_raw_record_key` | Changed raw record key. |
| `_raw_record_id` | Stable raw record ID. |
| `raw_record_bucket` | Logical raw bucket. |
| `raw_record_part` | Physical raw part. |
| `_change_type` | `added` or `removed`. |

When the downloaded file and parser contract are unchanged, bronze writes an
empty delta under a new snapshot directory and points `records_path` at the
existing accepted state records.

### Bronze accept

After silver successfully processes the dataset, `accept_last_preparse()` calls
`accept_raw_snapshot()`.

Accept does two important things:

1. Moves full records to mutable state:

   ```text
   data/bronze/<source>/<dataset>/state/records/
   ```

2. Writes:

   ```text
   data/bronze/<source>/<dataset>/latest.json
   ```

Snapshot directories retain their compact `delta/` and `manifest.json`. The
latest manifest is rewritten so `records_path` points to `state/records/`.

## 4. Silver source build

`build_silver_source()` creates a new numeric version under:

```text
data/silver/<source>/<version>/
```

The DAG chooses the version with `next_numeric_version()`. It also writes a
stable pointer file:

```text
data/silver/<source>/latest
```

`_write_silver_state_and_delta()` additionally writes:

```text
data/silver/<source>/latest.json
```

The `latest` file is used by `resolve_silver_version()`. `latest.json` is
metadata for humans and tools.

### Inputs module hash

Before running silver, `hash_inputs_module()` hashes all Python files backing
the source module. The hash is stored in:

```text
data/silver/<source>/<version>/inputs_module_hash.json
```

Silver incremental mode requires:

- prior silver state exists;
- every silver state table has `_raw_record_key`;
- the source supports incremental silver;
- the previous inputs module hash equals the current hash.

If the source code changed or previous state is missing, silver still writes a
new snapshot, but its delta strategy is `no_per_row_delta`.

### Silver mapping

`run_silver_loader()` selects discovered resource functions for the source and
processes them in order.

For each entity or ontology dataset:

1. Bronze preparse is requested.
2. Bronze reports changed raw keys.
3. The dataset call maps parser rows to `pypath.internals.silver_schema.Entity`
   objects.
4. `SilverTableWriter` expands each entity into canonical silver tables.

The canonical silver tables are:

| Table | Contents |
|---|---|
| `entity_occurrence` | One row for each emitted entity occurrence, including nested member entities. |
| `entity_identifier` | Identifier rows attached to occurrences. |
| `entity_annotation` | Annotation rows attached to occurrences. |
| `membership` | Parent/member links for nested entities. |
| `membership_annotation` | Annotations on membership edges. |

Each silver table carries lineage columns:

| Column | Meaning |
|---|---|
| `_raw_record_id` | Stable bronze raw record ID. |
| `_raw_record_key` | Bronze content key. |
| `raw_record_bucket` | Bronze bucket. |
| `raw_record_part` | Bronze part. |
| `_snapshot_id` | Bronze snapshot ID. |

Occurrence IDs are deterministic when raw record IDs are available. Parent
occurrences use:

```text
<dataset>:<raw_record_id>:parent
```

Nested members append member suffixes, for example:

```text
<dataset>:<raw_record_id>:parent:member:0
```

### Silver incremental update

When silver has compatible prior state, the writer is seeded from:

```text
data/silver/<source>/state/
```

For entity datasets, it excludes rows whose `_raw_record_key` appears in the
bronze delta as `removed`, then maps only changed current raw rows. For ontology
datasets, it excludes the dataset and rewrites that dataset.

If bronze delta is empty and prior silver state exists, silver mapping is
skipped. The silver task returns the previous snapshot directory and the DAG
marks the silver result as reused with `skipped: empty_bronze_delta`.

### Silver state and delta

After mapping, `_write_silver_state_and_delta()` does two things for every
silver table:

1. Copies the current table to:

   ```text
   data/silver/<source>/state/<table>/
   ```

2. Writes a table-specific delta under the new version:

   ```text
   data/silver/<source>/<version>/delta/<table>/part=00000.parquet
   ```

When incremental is available, silver delta rows are selected by raw-record
lineage:

- `added`: current rows whose `_raw_record_key` was not in previous state.
- `removed`: previous rows whose `_raw_record_key` is not in current output.

When incremental is not available, delta files contain zero rows and the
manifest says:

```json
{
  "delta_strategy": "no_per_row_delta",
  "no_per_row_delta_reason": "missing_previous_state"
}
```

Possible no-delta reasons include:

- `unsupported_source`
- `missing_previous_state`
- `inputs_changed_or_missing_previous_hash`

## 5. Resolver mappings

Resolver mappings are built by `build_resolver_mappings()` into
`id_resolver/data` unless an external mapping directory is supplied.

The pipeline considers mappings ready when both files exist:

```text
id_resolver/data/proteins/protein_identifier_lookup.parquet
id_resolver/data/chemicals/chemical_identifier_lookup.parquet
```

In full mode, resolver tables use `uniprot` plus configured chemical sources.
In test mode, they use:

```text
uniprot
chebi
```

Gold entity canonicalization uses these tables to resolve local identifiers to
canonical identifiers.

## 6. Gold source build

Gold source outputs live under:

```text
data/gold/<source>/
```

`build_gold_source()` first checks:

- whether silver has any data;
- whether previous gold outputs are ready;
- whether source-local gold `state.duckdb` exists;
- whether silver delta scope is available and readable.

If compatible previous gold and source state exist, and the silver delta is
empty, gold is skipped with:

```text
skipped: empty_silver_delta
```

If compatible previous gold and source state exist, and silver has changed raw
records, gold uses source-state incremental merge.

Otherwise, gold performs a full source rebuild.

### Silver delta scope for gold

Before building or merging, gold summarizes silver deltas by writing temporary
scope files under the staged gold directory:

```text
_silver_delta_scope/
  affected_raw_record_ids.parquet
  affected_occurrence_ids.parquet
```

`_silver_delta_scope_from_delta()` unions all silver table deltas and extracts:

- distinct raw record IDs from raw lineage columns;
- distinct occurrence IDs from occurrence columns;
- raw record IDs reached by joining affected occurrence IDs back to
  `entity_occurrence`.

That scope decides whether source-state incremental merge is possible and what
records must be rebuilt.

### Full gold source rebuild

A full rebuild writes into a temporary staged directory first:

```text
<tmp>/op-pipeline-gold-*/<source>/
  entities/
  relations/
```

Then it publishes staged outputs into `data/gold/<source>/`.

Full rebuild sequence:

1. `build_entities()` reads silver tables and resolver mappings.
2. `build_relations()` reads silver tables and the staged entity maps.
3. `initialize_gold_source_state()` builds source-local `state.duckdb` from the
   staged entity and relation outputs.
4. `_write_gold_delta_artifacts()` writes a gold affected-key scope.
5. Staged `entities/`, `relations/`, `state.duckdb`, and
   `state_manifest.json` are copied into the gold source directory.
6. A source archive is written.
7. `_SUCCESS.json` records the result metadata.

### Gold entities

`build_entities()` processes the silver source in bounded occurrence parts.

For each occurrence part it:

1. Filters silver tables to the occurrence part.
2. Extracts entity descriptions.
3. Computes entity fingerprints.
4. Resolves identifiers with the resolver mapping tables.
5. Repairs applicable protein resolutions.
6. Reduces equivalent entities.
7. Computes stable `entity_key` values.
8. Writes partitioned intermediate evidence and maps.

`entity_key` uses `sha256_v1` via `compute_entity_key()`. Conceptually it is a
hash of the canonical entity identity, including canonical identifier,
identifier namespace, entity type, and taxonomy where applicable.

Final entity outputs:

```text
entities/
  entity/
  entity_evidence/
  entity_map/
  entity_occurrence_map/
  manifest.json
  canonicalization_report.md
  canonicalization_summary.json
```

Important outputs:

| Table | Purpose |
|---|---|
| `entity` | One row per source-level canonical entity key. |
| `entity_evidence` | Source evidence grouped under entity keys, with raw lineage. |
| `entity_occurrence_map` | Maps silver occurrence IDs to source entity PKs and keys. |
| `entity_map` | Maps entity fingerprints to source entity PKs and keys. |

Gold entity manifests record partitioning settings, row counts, output names,
and canonicalization summary.

### Gold relations

`build_relations()` reads:

- silver tables;
- `entities/entity`;
- `entities/entity_map`;
- `entities/entity_occurrence_map`.

It processes bounded parent parts. For each part it:

1. Filters silver parent occurrences and linked member rows.
2. Loads only needed entity maps.
3. Converts interactions, memberships, and selected annotations to relation
   evidence.
4. Computes `relation_key` with `compute_relation_key()`.
5. Adds relation bucket and part columns.
6. Writes partitioned relation evidence intermediates.

The finalize step reduces relation evidence into:

```text
relations/
  entity_relation/
  entity_relation_evidence/
  manifest.json
```

Important outputs:

| Table | Purpose |
|---|---|
| `entity_relation` | One row per source-level canonical relation key. |
| `entity_relation_evidence` | Evidence rows, raw record IDs, subject/object attributes, and evidence attributes. |

Gold relation manifests record partitioning settings and relation row counts.

### Source-local gold state

The source-level gold state file is:

```text
data/gold/<source>/state.duckdb
```

It is managed by `omnipath_build.gold.source_state`.

It contains current source-local gold state, not a delta:

| Table | Purpose |
|---|---|
| `entity` | Current source entity rows. |
| `entity_evidence` | Current source entity evidence. |
| `entity_occurrence_map` | Current source occurrence-to-entity map. |
| `entity_map` | Current source fingerprint-to-entity map. |
| `entity_relation` | Current source relation rows. |
| `entity_relation_evidence` | Current source relation evidence. |
| `entity_key_registry` | Stable source entity PK assignment by `entity_key`. |
| `relation_key_registry` | Stable source relation PK assignment by `relation_key`. |

`state_manifest.json` records:

- `kind: source_state`;
- source name;
- state path;
- mode, `bootstrap` or `incremental`;
- bucket and part counts;
- row counts.

### Incremental gold source merge

When gold can merge incrementally, `_build_gold_source_incremental()` builds a
temporary changed-only silver directory:

```text
<staged>/_changed_silver/
```

It contains only silver rows affected by the silver delta scope. Gold entities
and relations are built for that changed subset, then
`merge_gold_source_state()` applies the changed evidence to a staged copy of
the previous source `state.duckdb`.

Entity merge:

- finds existing entity keys touched by affected raw records;
- finds affected occurrence IDs and fingerprints;
- keeps entity keys stable when fingerprints already existed under a previous
  canonical identity;
- deletes old affected evidence;
- inserts changed evidence;
- updates entity registry for new keys;
- recomputes affected `entity`, `entity_occurrence_map`, and `entity_map` rows.

Relation merge:

- finds existing relation keys touched by affected raw records;
- remaps relation subject/object keys when entity keys changed;
- recomputes relation keys for changed evidence;
- updates relation registry for new keys;
- deletes old affected relation evidence;
- inserts changed relation evidence;
- recomputes affected `entity_relation` rows.

After the staged source state is merged, source outputs are re-exported from the
staged state into staged `entities/` and `relations/`.

### Gold `_delta/` affected scope

After a successful full or incremental gold source build, the pipeline writes:

```text
data/gold/<source>/_delta/<build_id>/
```

This directory is not the source state. It is a durable affected-key handoff
for the combined layer.

Current root artifacts:

| Artifact | Meaning |
|---|---|
| `affected_raw_record_ids.parquet` | Raw records from the silver delta scope. |
| `affected_occurrence_ids.parquet` | Silver occurrences from the silver delta scope. |
| `affected_entity_keys.parquet` | Source entity keys combine should revisit. |
| `affected_entity_buckets.parquet` | Bucket summary for affected entity keys. |
| `affected_entity_parts.parquet` | Part summary for affected entity keys. |
| `affected_relation_keys.parquet` | Source relation keys combine should revisit. |
| `affected_relation_buckets.parquet` | Bucket summary for affected relation keys. |
| `affected_relation_parts.parquet` | Part summary for affected relation keys. |
| `manifest.json` | Counts, strategy, and artifact metadata. |

The writer also creates `entities/` and `relations/` directories under the
build ID, but current manifests set `per_row_delta: false` and no per-row gold
delta payload is written there.

`_delta/latest.json` points to the latest build ID.

Strategies in the manifest include:

| Strategy | Meaning |
|---|---|
| `first_build_key_scope` | First build marks all current source entity and relation keys as affected. |
| `silver_delta_target` | Silver lineage delta was used to derive affected source keys. |
| `empty_silver_delta` | No source keys affected. |
| `full_gold_diff` | Default metadata value before a more specific strategy is assigned. |

If an affected-key scope cannot be produced, the manifest records
`affected_key_scope_available: false`. Combine then falls back to a bootstrap
style update instead of targeted affected-key mode.

## 7. Combined layer

Combined outputs live under:

```text
data/combined/
```

The combined state file is:

```text
data/combined/state.duckdb
```

This is separate from each source-level `data/gold/<source>/state.duckdb`.

Source state answers: "what is the current gold output for this one source?"

Combined state answers: "what is the current cross-source merged database?"

### Collecting affected scopes

Before combine runs, the DAG calls
`_collect_affected_scope_from_gold_artifacts()` for gold sources that executed
in the current run.

For each changed source, it locates:

```text
data/gold/<source>/_delta/<build_id>/
```

It requires:

```text
affected_entity_keys.parquet
affected_relation_keys.parquet
```

It also reads manifest counts for affected entities, relations, buckets, and
parts. If any changed source lacks usable affected artifacts, combine is run
without affected-key paths.

### Combined bootstrap

`build_combined()` bootstraps when:

- combined `state.duckdb` is empty; or
- no affected-key paths were supplied.

Bootstrap behavior:

1. Reset combined state tables.
2. Recompute every entity part from every source gold output.
3. Recompute every relation part from every source gold output.
4. Export all parts to `data/combined/latest/`.
5. Rebuild relation annotation terms.
6. Write run artifacts and build summary.
7. Build `resources.parquet`.

### Combined incremental update

When affected-key paths are supplied and combined state is not empty, combine
uses incremental mode.

Incremental behavior:

1. Read affected source entity keys and relation keys into temporary tables.
2. Add deterministic `entity_part` and `relation_part` columns.
3. Expand affected relation keys to include relations that reference affected
   entity keys.
4. Recompute only affected entity parts when affected entities exist.
5. Recompute only affected relation parts when affected relations exist.
6. Export only changed parts into the existing `latest/` directory.
7. Rebuild relation annotation output for affected relation parts.
8. Write run artifacts.

The combined state schema includes:

| Table | Purpose |
|---|---|
| `entity_key_map` | Stable global `entity_id` assignment by `entity_key`. |
| `relation_key_map` | Stable global `relation_id` assignment by `relation_key`. |
| `entity` | Current global entity rows. |
| `entity_source` | Per-source entity presence and payload hash. |
| `entity_evidence` | Current global entity evidence. |
| `entity_relation` | Current global relation rows. |
| `relation_source` | Per-source relation presence and payload hash. |
| `entity_relation_evidence` | Current global relation evidence. |

### Combined latest outputs

Current combined outputs are partitioned datasets under:

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

The export intentionally drops internal partition columns from the public
entity and relation tables. Evidence tables are joined to global IDs where
needed.

`build_manifest.jsonl` appends one line per combined build, recording:

- mode;
- changed source;
- affected entity and relation counts;
- row counts;
- bucket and part settings.

### Combined run artifacts

Each combine execution writes:

```text
data/combined/runs/<run_id>/
  manifest.json
```

For incremental runs it also writes:

```text
affected/
  entity_keys.parquet
  relation_keys.parquet
```

The run manifest records:

- `mode`, `bootstrap` or `incremental`;
- `changed_source`;
- `latest_dir`;
- `run_dir`;
- affected entity and relation counts;
- paths to affected key artifacts when present.

Current important distinction: combined run `affected/` key artifacts are not
the same as table upsert/delete deltas. They tell what keys were recomputed.
They do not currently contain table rows to delete or upsert.

## 8. Postgres load

The Postgres loader reads the combined latest parquet directory. It can operate
in three table-loading modes:

| Condition | Action |
|---|---|
| target tables empty, or `drop_existing=True` | Full bootstrap from combined latest parquet. |
| combine run is incremental and `runs/<run_id>/delta/*.parquet` has work | Apply table delta. |
| target has data and no table delta is supplied | Leave base tables unchanged, then refresh requested indexes/views. |

The incremental Postgres loader expects table delta files under:

```text
data/combined/runs/<run_id>/delta/
  entity_delete.parquet
  entity_upsert.parquet
  entity_relation_delete.parquet
  entity_relation_upsert.parquet
  entity_relation_evidence_upsert.parquet
  entity_evidence_delete.parquet
  entity_evidence_upsert.parquet
  relation_annotation_term_delete.parquet
  relation_annotation_term_upsert.parquet
```

As of the current combine writer, combine run artifacts include `affected/`
key files but do not write these `delta/` upsert/delete files. That means the
Postgres loader will not apply a table delta unless another producer writes
`runs/<run_id>/delta/`.

After base table handling, Postgres can create:

- secondary indexes;
- bitmap tables;
- materialized views.

Bitmap tables are fully populated on bootstrap. On table-delta loads, affected
IDs are removed before delta application and added back after upserts.

## 9. Reports and memory monitor

Every pipeline run creates a run ID:

```text
run-YYYYMMDD-HHMMSS
```

Reports:

```text
data/reports/runs/<run_id>.json
data/reports/latest.json
data/reports/changelog.ndjson
```

The report contains:

- selected sources;
- discovered gold sources;
- task graph results;
- task output directories;
- status, such as `executed`, `reused`, `skipped`, or `failed`;
- task metadata;
- memory summary when available.

The memory monitor samples to:

```text
data/reports/memory/<run_id>.ndjson
```

## 10. End-to-end row lineage

For one raw parser row, the lineage path is:

1. Raw parser emits a dictionary.
2. Bronze computes `_raw_record_key`.
3. Bronze assigns or reuses `_raw_record_id`.
4. Silver maps the row to one or more occurrences and carries `_raw_record_*`
   lineage columns into every canonical silver table.
5. Gold entities derive fingerprints and stable `entity_key` values.
6. Gold relations derive stable `relation_key` values from subject key,
   predicate, object key, and relation category.
7. Source gold `state.duckdb` persists current source rows and stable source
   PK registries.
8. Gold `_delta/<build_id>` records which entity and relation keys changed.
9. Combined `state.duckdb` merges affected source keys into global IDs and
   cross-source rows.
10. Combined latest parquet exposes public global tables.

Key summary:

| Layer | Key or ID | Stable across | Purpose |
|---|---|---|---|
| Download | file SHA-256 | identical local file content | Detect unchanged downloaded inputs. |
| Bronze | `_raw_record_key` | identical parser row content | Content-address raw rows. |
| Bronze | `_raw_record_id` | accepted snapshots for same raw key | Compact lineage ID. |
| Bronze | `raw_record_bucket`, `raw_record_part` | deterministic from raw key | Bounded raw partitioning. |
| Silver | `occurrence_id` | same raw ID and dataset shape | Attach identifiers, annotations, and memberships. |
| Gold entities | entity fingerprint | same source entity description | Pre-resolution entity grouping and maps. |
| Gold entities | `entity_key` | same canonical entity identity | Source and combined entity business key. |
| Gold relations | `relation_key` | same subject/predicate/object/category | Source and combined relation business key. |
| Combined | `entity_id`, `relation_id` | combined state lifetime | Stable exported numeric IDs. |

## 11. State and delta terminology

This table is the short version for debugging state-vs-delta confusion.

| Path | Layer | Kind | Consumed by | Contains |
|---|---|---|---|---|
| `data/bronze/<source>/<dataset>/state/records/` | Bronze | State | Future bronze, silver raw iteration | Full accepted raw records. |
| `data/bronze/<source>/<dataset>/<snapshot_id>/delta/` | Bronze | Delta | Silver preparse logic | Added/removed raw record keys. |
| `data/silver/<source>/state/` | Silver | State | Future silver incremental updates | Full current canonical silver tables. |
| `data/silver/<source>/<version>/delta/` | Silver | Delta | Gold source build | Added/removed silver rows by raw lineage. |
| `data/gold/<source>/state.duckdb` | Gold source | State | Future gold source incremental updates | Current source-level gold rows and key registries. |
| `data/gold/<source>/_delta/<build_id>/` | Gold source | Affected scope | Combined layer | Affected raw IDs, occurrence IDs, entity keys, relation keys, bucket and part summaries. |
| `data/combined/state.duckdb` | Combined | State | Future combine incremental updates | Current cross-source merged rows and global key registries. |
| `data/combined/runs/<run_id>/affected/` | Combined | Affected scope | Reports, possible downstream planning | Affected global entity and relation keys. |
| `data/combined/runs/<run_id>/delta/` | Combined/Postgres | Table delta, if present | Postgres incremental loader | Expected delete/upsert parquet files. Not currently written by combine. |

## 12. Common incremental scenarios

### No downloaded data or parser change

1. Bronze creates an empty-delta snapshot.
2. Silver mapping skips and reuses the previous silver snapshot.
3. Gold sees silver reuse and, if source state exists, reuses gold.
4. Combine reuses existing combined output if no gold sources executed.

### Raw rows changed, silver/gold state compatible

1. Bronze writes added/removed raw keys.
2. Silver seeds from `silver/<source>/state/`, removes rows affected by removed
   raw keys, maps changed current raw rows, and writes silver deltas.
3. Gold extracts affected raw IDs and occurrence IDs from silver deltas.
4. Gold builds changed-only entities/relations and merges them into
   `gold/<source>/state.duckdb`.
5. Gold writes `_delta/<build_id>` affected-key scope.
6. Combine reads that affected-key scope and recomputes only affected global
   parts.

### Source parser code changed

1. Silver inputs module hash changes.
2. Silver writes a new full snapshot with `delta_strategy: no_per_row_delta`.
3. Gold cannot use silver per-row scope, so it falls back to full source gold
   rebuild.
4. Gold still writes a `_delta/<build_id>` affected-key scope for combine.
5. Combine can use that scope if it is available; otherwise it bootstraps.

### Previous gold state missing

1. Gold performs a full source rebuild.
2. `initialize_gold_source_state()` creates `gold/<source>/state.duckdb`.
3. Gold `_delta/<build_id>` marks first-build source keys as affected.
4. Combine can target those keys if combined state already exists, or bootstrap
   if combined state is empty.

## 13. Practical debugging checklist

When a pipeline run does more work than expected, check these files in order:

1. Bronze latest:

   ```text
   data/bronze/<source>/<dataset>/latest.json
   ```

   Look at `snapshot_id`, `records_path`, `delta_path`, and
   `delta_keys_by_type`.

2. Silver version manifest:

   ```text
   data/silver/<source>/<version>/manifest.json
   ```

   Look at `delta_strategy`, `no_per_row_delta_reason`, `delta_counts`, and
   `inputs_module_hash`.

3. Silver state readiness:

   ```text
   data/silver/<source>/state/
   ```

   All five silver tables should exist and include `_raw_record_key` for
   incremental mode.

4. Gold source success:

   ```text
   data/gold/<source>/_SUCCESS.json
   data/gold/<source>/state_manifest.json
   ```

   Confirm source state exists and whether the state manifest mode is
   `bootstrap` or `incremental`.

5. Gold affected scope:

   ```text
   data/gold/<source>/_delta/latest.json
   data/gold/<source>/_delta/<build_id>/manifest.json
   ```

   Confirm `affected_key_scope_available`, affected counts, and strategy.

6. Combined summary:

   ```text
   data/combined/latest/combined_build_summary.json
   data/combined/runs/latest.json
   data/combined/runs/<run_id>/manifest.json
   ```

   Confirm `mode`, changed source, updated parts, and affected counts.

7. Pipeline report:

   ```text
   data/reports/latest.json
   ```

   Confirm which tasks were `executed`, `reused`, `skipped`, or `failed`.
