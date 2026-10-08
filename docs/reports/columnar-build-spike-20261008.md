# Columnar build spike (October 2026)

A two-day experiment to find out where the resource build spends its time and
whether a set-at-a-time ("columnar") executor could make it much faster. It was
run on BindingDB and stopped before any of it was merged. This note records what
was learned, for a later optimization effort.

Code: branch `columnar-build` in omnipath-build (6 commits after `cebec41`) and in
pypath (`376929ed2`, `e0b33e6d5`). Nothing on these branches is merged.

## Bottom line

- The build is slow because almost all of it is per-row Python, not because of
  DuckDB or the identity lookups. The cost is spread over thousands of small
  calls; there is no single hotspot to fix.
- Evaluating the existing mapping code once per *distinct* input, resolving each
  distinct entity once, and writing in bulk reproduces the current output
  exactly and is 3.9x faster in wall time and 2.2x cheaper in CPU on BindingDB.
- Large CPU gains (10x and more) need the remaining per-row and per-entity
  Python moved into SQL: plain field mappings, unambiguous resolution and the
  relation rows of the writer. The prototypes suggest this is feasible.
- No new language is needed. DuckDB and Arrow already supply native speed; the
  mapping definitions can stay in Python.

## Where the time goes today

Per-row cost of each stage, measured on 20,000 rows per resource (nicesrv, one
process):

| resource | parse | map | flatten | resolve + write | total |
|---|---|---|---|---|---|
| ChEMBL activities | 100–508 µs | 296 µs | 103 µs | 575 µs | 1.5 ms |
| BindingDB | 976 µs | 680 µs | 203 µs | 1050 µs | 3.0 ms |
| IntAct | 74 µs | 560 µs | 134 µs | 1198 µs | 2.0 ms |
| UniProt | 42 µs | 699 µs | 806 µs | 5532 µs | 7.1 ms |

That is 150–650 rows per second per core. In the last full build ChEMBL spent
13,032 s preparing and 736 s finalizing.

- **Parse**: Python CSV/dict building per row. BindingDB's DuckDB reader exists
  but is off by default (`OMNIPATH_BINDINGDB_USE_DUCKDB`).
- **Map**: the `tabular_builder` DSL interprets about 25 field definitions per
  record, with dedupe hashing, `isinstance` checks and even LinkML enum
  pretty-printing on hot paths.
- **Resolve + write**: the LMDB lookups and the Rust decision kernel are a small
  share. Most is Python around them: `deepcopy` of the matcher's memo (half of
  UniProt's resolve time), JSON decoding of key-value records, per-entity object
  building and the writer's per-entity and per-relation loops.
- **Finalize** (DuckDB) is minor for most resources; see below for what remains.

The mapping definitions are mostly declarative: 491 plain field references and
770 CV definitions against 267 lambdas.

## What the spike built

1. **Parse into a DuckDB table.** pypath `Dataset` gained an optional
   `raw_table` hook. BindingDB's version reproduces the CSV parser exactly
   (columns, sequence-column renames, ChEMBL-run fix, pChEMBL filter, row order)
   in SQL. Payload JSON is written by DuckDB and only rows with non-ASCII text
   are serialized in Python.
2. **Distinct evaluation of the existing mapping code.** Each relation endpoint
   and each relation-level field runs once per distinct tuple of the columns it
   reads, in forked processes; SQL joins results back to rows and applies the
   builders' dedupe. Dependencies are traced on sample rows; evaluation sees
   only those columns, and any read outside them is detected and retried with
   more columns, so results stay exact.
3. **Resolve each distinct entity once** with the unchanged resolver, in
   parallel chunks, then make the writer's entity rows there.
4. **Bulk write.** The writer was split into `entity_rows`, `relation_rows` and
   `ingest_entities` / `ingest_relations` / `ingest_payloads`; the row path
   composes the same parts. The columnar path calls them once in bulk, then runs
   the unchanged finalize.

Parity was checked at two levels: observation tables against a dump of the
row path's extractor output, and all eleven published tables (row for row,
every column) against the native nicesrv build of the same input.

## Results

BindingDB 202610, 2,500,553 rows, on beauty with the same identity library:

| stage | row path wall | new path wall | new path CPU |
|---|---|---|---|
| parse | | 89 s | 368 s |
| map and flatten | | 154 s | 617 s |
| resolve | | 23 s | 535 s |
| write and finalize | 278 s (finalize) | 208 s | 900 s |
| **total** | **1,841 s** | **474 s** | **2,420 s** |

The row path (8 workers) used 5,440 CPU seconds; its prepare phase took 1,561 s.
The native nicesrv build of the same input took 3,919 s on 5 workers. All
eleven tables of the new path are identical to the native build.

Stage by stage on BindingDB 202605 (706,922 rows):

- Parse: 27 s in SQL against 601 s loading the Python parser's rows.
- Map and flatten: about 20 s against 365 s for the row path on the same 24
  workers.
- Target endpoints: 5,143 distinct inputs instead of 706,922 rows, after the
  `_target` mapper stopped copying the whole row.

Pure-SQL prototypes give the ceiling: BindingDB parse and map in DuckDB took
31 µs of CPU per row against about 2 ms today. A set-based join resolved 1.41 M
of 1.45 M distinct chemical observations in 16 s on 2 threads, against
roughly 3,300 s of resolve-and-write today. That join skips the resolver's
exception, structure and gene-product rules.

## Remaining cost and next steps

In order of expected gain:

1. **Write and finalize, 900 CPU s.** The finalize bucket loop and reference
   identifiers dominate; bucket passes are independent and could run
   concurrently. The per-relation Python in `relation_rows` can become SQL.
2. **Map, 617 CPU s.** Compile plain field definitions (column, split, regex,
   value map) to SQL; keep the per-distinct Python only for custom functions.
3. **Resolve, 535 CPU s.** Settle unambiguous observations with joins over the
   hub Parquet indexes; send only conflicts and rule-heavy cases to the
   resolver. Verify per observation against the resolver.
4. **Parse, 368 CPU s.** Mostly unzipping the 9 GB TSV and payload JSON.

Doing 1–3 is estimated to bring BindingDB to roughly 500–800 CPU seconds,
7–10x below today. That is an estimate, not a measurement. The approach then
has to be extended resource by resource: entity datasets, memberships,
ontology relations and other parsers. Each resource keeps the row path until
its columnar version matches it exactly.

## Changes worth landing on their own

These help the current pipeline without the columnar executor:

- Already landed: the writer frees its working tables and merges buckets on one
  thread (`cebec41`). This fixed ChEMBL's out-of-memory failure in finalize.
- **Finalize buckets** (`def04d1`): 16 key-range buckets for tables up to 64 M
  rows instead of 256, and complex payload rewrite in 65,536-row batches.
  Same output; finalize 202 s to 137 s on BindingDB.
- **Writer split** (`2a1f52b`): no behaviour change; needed for any bulk path.
- **Resolver `memo_size`** (`d479273`): the memo's `deepcopy` is a large share of
  resolve time; worth measuring smaller memos or immutable results in the row
  path too.
- **pypath** (`376929ed2`, `e0b33e6d5`): `_target` copies only the fields it
  reads; `Dataset.raw_table` plus BindingDB's SQL parser (20x faster parse).
- Turning on BindingDB's DuckDB reader would already cut its parse time.

## Pitfalls met

- `IS NOT DISTINCT FROM` joins on many columns made DuckDB fall back to a
  nested-loop join. Join on a hash of the tuple and check that hashes are
  unique.
- A mapper that copies the row (`dict(row)`) depends on every column, which
  defeats distinct evaluation. Copy only the fields it reads.
- Keys absent from every input row must read as absent, not as violations.
  Otherwise dependency tracing loops.
- DuckDB's `to_json` matches Python's `json.dumps` only for ASCII text. Python
  escapes code points from 0x7F upward.
- DuckDB's regex engine (RE2) has no lookahead, so such cases need a Python UDF.
- Per-task re-reading of a shared input file and deep-copying memos were each
  bigger costs than the work itself.
- Logging was the cheapest profiler on beauty, which lacks ptrace permission
  for `py-spy`.

## Environment notes

- The resolver needs the identity library and the hub LMDB stores. The hub
  Parquet indexes (`by_id`, `by_record`, `records`) are enough to rebuild the
  stores with `omnipath-build build-hub-kv`: minutes on beauty, but about
  140 GB of disk (UniProt 62 GB, PubChem 56 GB, Entrez 17 GB).
- The identity manifest holds absolute hub paths; a copy elsewhere needs them
  rewritten (nothing hashes them).
- Beauty's shared pypath cache (`/scratch/instances/_shared/pypath-data`) has
  most inputs, including PubChem SDFs, but not UniProt `idmapping` or the NCBI
  gene files that the hub export streams.

## Reproducing

Scripts on the branch, under `scripts/columnar/`:

- `reference_observations.py`: dump the row path's observations.
- `run_relations.py`: columnar observations, with a diff against that dump.
- `build_relations.py`: full columnar build with wall and CPU per stage.
- `compare_tables.py`: row-for-row diff of two builds' published tables.
- `compare_raw.py`: diff a SQL table parser against the row parser.
- `time_row_build.py`: CPU and wall time of a row-path build.
- `profile_finalize.py`: time finalize phases and slow statements.
