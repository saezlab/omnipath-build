# Rewrite Pipeline Work-Tracking Report

This report explains the current layered rewrite pipeline, the intermediate
state each layer writes, and how the new fixture-based test tracks whether the
pipeline only performs necessary work.

The concrete test is:

```text
tests/test_rewrite_pipeline_work_tracking.py
```

It uses small real-shaped TSV fixtures from:

```text
tests/fixtures/rewrite_pipeline/
  uniprot_proteins_initial.tsv
  signor_interactions_initial.tsv
  signor_interactions_changed.tsv
```

The test runs the rewrite pipeline in a temporary `data_rewrite` root, so it
does not depend on or mutate the checked-in `data_rewrite` state.

## Pipeline Layout

The rewrite pipeline uses source-local DuckDB shards plus a combined DuckDB:

```text
<temp>/data_rewrite/
  state/
    sources/
      uniprot.duckdb
      signor.duckdb
    combined.duckdb
  artifacts/
    gold/
      <source>/
        latest.json
        <version>/<source>.zip
    combined/
      latest/
        entity.parquet
        entity_evidence.parquet
        entity_relation.parquet
        entity_relation_evidence.parquet
        relation_annotation_term.parquet
        resources.parquet
  reports/
    combined/
      combined_build_summary.json
      relation_annotation_summary.json
      build_manifest.jsonl
      latest.json
      runs/<combined_run_id>.json
```

The main internal contract is DuckDB state. Parquet is treated as a public
export or release artifact, not as the normal intermediate state between
layers.

## Step 1: Bronze

Bronze ingests raw parser rows and writes source-local raw-record state.

Test entry point:

```python
materialize_bronze_duckdb(
    records=<fixture TSV rows>,
    source='uniprot' or 'signor',
    dataset='proteins' or 'interactions',
    data_root=<temp>/data_rewrite,
)
```

Intermediate outputs in `state/sources/<source>.duckdb`:

```text
bronze_dataset_snapshot
bronze_raw_record_registry
bronze_raw_record_current
bronze_raw_record_change
bronze_raw_record_current__<dataset>__<hash>
```

What these mean:

- `bronze_dataset_snapshot` records the accepted snapshot and manifest.
- `bronze_raw_record_registry` owns stable raw record IDs across snapshots.
- `bronze_raw_record_current` stores the current key and raw ID set.
- `bronze_raw_record_change` stores the latest snapshot's affected raw keys.
- `bronze_raw_record_current__...` is the typed table preserving raw columns.

Current change semantics:

```text
same rows again       -> no bronze changes
changed row content   -> old raw key removed + new raw key added
new row               -> added
deleted row           -> removed
```

The current bronze layer does not emit a separate `changed` change type.

Work tracked by the test:

```text
_bronze_delta(data_root, source)
```

Expected fixture behavior:

```text
UniProt initial load       -> {'added': 2}
SIGNOR initial load        -> {'added': 2}
SIGNOR no-op reload        -> {}
SIGNOR changed-row reload  -> {'added': 2, 'removed': 2}
```

## Step 2: Silver

Silver maps affected bronze raw rows to canonical source-local silver tables.

Test entry point:

```python
materialize_silver_duckdb(
    source=<source>,
    resource_functions=<only the fixture dataset functions>,
    data_root=<temp>/data_rewrite,
)
```

Intermediate outputs in `state/sources/<source>.duckdb`:

```text
silver_entity_occurrence
silver_entity_identifier
silver_entity_annotation
silver_membership
silver_membership_annotation
source_run_scope_raw_record
source_run_scope_occurrence
```

What these mean:

- `silver_entity_occurrence` is the canonical occurrence table.
- `silver_entity_identifier` stores occurrence identifiers.
- `silver_entity_annotation` stores occurrence annotations.
- `silver_membership` and `silver_membership_annotation` store nested entity
  membership, such as SIGNOR interaction participants.
- `source_run_scope_raw_record` records affected raw IDs for this source run.
- `source_run_scope_occurrence` records affected occurrence IDs derived from
  affected raw IDs.

Silver deletes existing rows for affected raw IDs, then maps only current
affected rows. Removed raw IDs are deleted but not remapped.

Work tracked by the test:

```text
SilverRewriteResult.mapped_raw_record_count
SilverRewriteResult.deleted_raw_record_count
source_run_scope_raw_record row count
source_run_scope_occurrence row count
```

Expected fixture behavior:

```text
UniProt initial load       -> mapped_raw_record_count = 2
SIGNOR initial load        -> mapped_raw_record_count = 2
SIGNOR no-op reload        -> mapped_raw_record_count = 0
SIGNOR changed-row reload  -> mapped_raw_record_count = 2
                            -> deleted_raw_record_count = 4
```

The changed SIGNOR reload has four affected raw IDs because two old keys are
removed and two current keys are added. Only the two current added rows are
mapped into silver.

## Step 3: Source Gold

Source gold canonicalizes silver occurrences into source-level entities,
entity evidence, relations, and relation evidence.

Test entry point:

```python
materialize_gold_duckdb(
    source=<source>,
    data_root=<temp>/data_rewrite,
    partition_config=<small test partition config>,
)
```

Intermediate outputs in `state/sources/<source>.duckdb`:

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

Release artifact output:

```text
artifacts/gold/<source>/
  latest.json
  <version>/<source>.zip
```

What these mean:

- `gold_entity` and `gold_entity_relation` are source-level aggregate rows.
- `gold_entity_evidence` and `gold_entity_relation_evidence` retain evidence.
- `gold_entity_map` and `gold_entity_occurrence_map` connect fingerprints and
  occurrences to stable entity keys.
- `gold_*_registry` tables preserve stable source-local key assignments.
- `source_run_scope_entity` and `source_run_scope_relation` tell combined which
  source keys need recomputation.

Gold uses one merge path for bootstrap, no-op, and delta runs. It reads current
source scopes from silver, builds scoped gold frames, merges only affected rows,
and writes source entity/relation scopes only when gold actually changed.

Work tracked by the test:

```text
GoldRewriteResult.gold_changed
GoldRewriteResult.archive_written
source_run_scope_entity row count
source_run_scope_relation row count
```

Expected fixture behavior:

```text
initial source loads       -> gold_changed = True, archive_written = True
SIGNOR no-op reload        -> gold_changed = False, archive_written = False
SIGNOR changed-row reload  -> gold_changed = True
```

After source-gold runs and before combined consumes them, the test verifies that
entity and relation scope tables are populated for real work and empty for a
no-op.

## Step 4: Combined

Combined merges source-gold state from selected source DuckDB shards into a
global combined DuckDB and public combined Parquet export.

Test entry point:

```python
materialize_combined_duckdb(
    sources=['uniprot'] or ['uniprot', 'signor'],
    data_root=<temp>/data_rewrite,
    config=<small test combined config>,
)
```

Intermediate output in `state/combined.duckdb`:

```text
combined_run
combined_run_scope_entity
combined_run_scope_relation
entity_key_map
relation_key_map
entity
entity_source
entity_evidence
entity_relation
relation_source
entity_relation_evidence
```

Public export output:

```text
artifacts/combined/latest/
  entity.parquet
  entity_evidence.parquet
  entity_relation.parquet
  entity_relation_evidence.parquet
  relation_annotation_term.parquet
  resources.parquet
```

Report output:

```text
reports/combined/
  combined_build_summary.json
  relation_annotation_summary.json
  build_manifest.jsonl
  latest.json
  runs/<combined_run_id>.json
```

Combined modes:

```text
first run with empty state -> bootstrap
later runs with scopes     -> incremental
later runs with no scopes  -> skipped: empty_affected_scope
```

In incremental mode, combined now carries the source name through:

```text
source_run_scope_entity/source_run_scope_relation
  -> affected_entity_keys/affected_relation_keys
  -> combined_run_scope_entity/combined_run_scope_relation
```

This lets tests and reports see which source actually triggered combined work.

Work tracked by the test:

```text
CombinedRewriteResult.mode
combined_run_scope_entity row count
combined_run_scope_relation row count
distinct source values in combined run scopes
```

Expected fixture behavior:

```text
UniProt-only first combine          -> bootstrap
SIGNOR added after UniProt          -> incremental, source scope = {'signor'}
SIGNOR changed-row reload           -> incremental, source scope = {'signor'}
```

The test specifically asserts that stale UniProt source scope is not consumed
again during later SIGNOR-only incremental combines.

## Scope Consumption

After a successful combined update with source scopes enabled, the rewrite
clears consumed source-local scope tables:

```text
source_run_scope_raw_record
source_run_scope_occurrence
source_run_scope_entity
source_run_scope_relation
```

This is important because source scopes are work queues for combined. If they
remain populated after combined succeeds, later combined runs can accidentally
repeat stale source work.

The test checks this lifecycle:

```text
after source-gold, before combined -> source scopes populated
after successful combined          -> source scopes empty
```

## Current Test Coverage Summary

The work-tracking test currently covers:

- bootstrap of UniProt source state;
- incremental addition of SIGNOR after UniProt;
- SIGNOR no-op behavior through bronze, silver, and gold;
- SIGNOR changed-row behavior represented as removed and added raw keys;
- silver mapping only current affected rows;
- gold writing scopes only for real changes;
- combined recording the source responsible for incremental work;
- combined clearing consumed source scopes after success.

The test intentionally does not assert full parity with the current checked-in
pipeline outputs. Existing docs already record known source-gold and combined
parity gaps. This test is focused on incremental correctness and work
avoidance.

## Follow-Up Checks

Useful next tests:

- combined no-op after scope consumption should return `skipped` with
  `empty_affected_scope`;
- changed UniProt rows should not cause SIGNOR source scope to be consumed;
- deleted SIGNOR rows should remove stale combined evidence;
- source scope clearing should happen only after combined success, not after a
  failed combined run;
- combined relation-scope expansion should be measured separately from direct
  source relation scopes.
