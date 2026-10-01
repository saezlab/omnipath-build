# Build run log

A running record of individual `make load` / `make reload` invocations against
a real database — not build *tuning* (see [`build-tuning.md`](build-tuning.md))
or pipeline *architecture* (see [`pipeline.md`](pipeline.md)), but the history
of actual runs: why each one happened, what parameters it used, and how long
its phases took. The goal is that nobody has to re-derive "why did we run
this" or "how long does this normally take" from scratch by scrolling terminal
history — it's written down once, here, per run.

## Format

One entry per run, newest first. Each entry:

- **Date / who / cycle**: when, and what spec/task motivated it (link the
  task id if there is one).
- **Reason**: why this run happened — what question it answers or what change
  it verifies. Not "ran a build" — the actual motivating question.
- **Parameters**: the exact invocation (`SOURCES`, `MAX_RECORDS`, `LOAD_JOBS`/
  `--stage-jobs`, `THREADS`, `BATCH_SIZE`, target database). Enough to
  reproduce the run verbatim.
- **Phase durations**: wall-clock time per major phase (per-source delete,
  preparse, stage/canonicalize, copy, derive), and any single-dataset outlier
  worth calling out by name. Exact numbers where the log gives them; honest
  ranges/estimates where it doesn't.
- **Outcome**: what changed, what it confirmed or refuted, links to the
  acceptance query results if the run was measured against
  `contracts/coverage-acceptance.sql`.

Keep entries factual and specific — numbers and log excerpts, not vibes.

---

## 2026-09-30 — full resolved-Parquet release on nicesrv, attempt 5 (pending)

**Reason**: retry the fixed release after HMDB entity staging exceeded attempt
4's 512MB DuckDB allowance. All 46 resources / 230 exact production rotated-CSV
projections have now passed with 1024MB and one thread. The preflight staged all
selected records, checked each table's expected count, and removed its temporary
CSV/spill directories. It took 1144.172568 s, process peak RSS 803,992 KiB.
It made no PostgreSQL writes. The separate HMDB entity test staged all 220,400
rows in 8.489402 s and used process peak RSS 768,216 KiB.

**Parameters**: loader `420c407` (DuckDB projection `91317a3`, narrow statistics
`15500f7`, bounded alias index `3c9bab3`); native replay remains `300d06b`,
pypath `e27d72e095f53b1a0fd76d63bbe059c10f8a48d8`.
Unchanged release `2026.9.30.4`, canonical manifest SHA-256
`06f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e`;
schema `full_20260930`, same 46 exact pins. Invocation:
`uv run --frozen --no-sync python
/root/projects/omnipath-migration/full-release-20260930/postgres-sql-build.py`.
`load_release(..., batch_size=1024, duckdb_threads=1, memory_limit='1024MB',
validate_source_records=False, temp_directory=state)` then all three products.
Database/role `omnipath_migration`, localhost5440, separate PG16 container.
Process cap 3 GiB RAM / 256 MiB swap / two CPUs; PG4 GiB/two CPUs; disk floor
64 GiB. CSV16MB rotation threshold / COPY1 MiB byte blocks. Session work_mem16MB,
maintenance128MB, gather1, jit off. Unit:
`omnipath-migration-full-postgres-sql-attempt5-20260930.service`.

Raw source auditing is now disabled by the approved default; `--validate-source-records`
retains the old deeper audit when requested. File schemas/counts/byte sizes/SHA
before and after loading, normalized integrity, owner constraints, reaction
annotation checks and atomic rollback remain mandatory. All 419 PostgreSQL/subset
tests passed in 23.56 s, including identical stored output in both modes.

**Phase durations**: pending in the new `postgres-build.json`/`.log`.

**Outcome**: pending. Expected base rows: 6,383,473 entities; 80,136,865 identifiers;
18,339,902 relations; 22,887,251 evidence; 150,690,040 annotations. No resource
rebuilds or source-version changes for this retry. The full read-only and
PostgreSQL-only product rebuild checks remain queued behind base/product commit.

## 2026-09-30 — full resolved-Parquet release on nicesrv, attempt 4 (failed)

**Reason**: retry the unchanged full release after supplying narrow planner
statistics before every source-owner check. The third attempt copied BindingDB
correctly, then was cancelled during inefficient owner lookups and rolled back.
The new regression shows that unanalysed resource/version index probes filter
98,976 of 100,000 synthetic rows per 1,024-owner batch. After narrow ANALYZE,
both custom and generic plans put all three scoped keys in the index condition.
Cache warming affects timings; predicate placement is the primary evidence.

**Parameters**: loader `3c9bab3` (owner statistics `15500f7`); native replay remains `300d06b`, pypath
`e27d72e095f53b1a0fd76d63bbe059c10f8a48d8`. Unchanged private release
`2026.9.30.4`, manifest SHA-256
`06f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e`;
schema `full_20260930`; all 46 exact pins in `release.json`.
Invocation: `uv run --frozen --no-sync python
/root/projects/omnipath-migration/full-release-20260930/postgres-sql-build.py`.
Same database/role `omnipath_migration`, localhost 5440, isolated PG16 container.
Same one-thread / 512 MB DuckDB SQL → 16 MB CSV rotation threshold → 1 MiB
PostgreSQL COPY byte blocks; raw batches 1024 dictionary indices / 64 plain rows.
Process limits: 3 GiB RAM / 256 MiB swap / two CPUs; PG: 4 GiB/two CPUs;
64 GiB disk reserve. Session work_mem16MB, maintenance128MB, parallel gather1,
jit off. Unit: `omnipath-migration-full-postgres-sql-attempt4-20260930.service`.

Each resource now gets scalar resource/version/key statistics for entities and
relations before owner queries. Reaction resources sample their additional
scalar join/filter columns in the same call. No preliminary wide-JSON sampling,
query rewrite or change to integrity rules. Base, indexes, derivations and exact
release metadata still share one transaction; all three products follow after
base commit. A queued verifier then checks the complete release read-only and
rebuilds products with private Parquets unavailable, rolling that rebuild back.

**Phase durations**: 7864.979847 s total; release read 5.886399 s; typed SQL
validation 76.155310 s; successful CSV staging 702.586871 s; PostgreSQL COPY
4773.493333 s; raw audits 2286.706690 s for 12,431,857 occurrences; narrow owner
statistics 13.223463 s. Fourteen resources completed all five tables and audits.
The copy total was 161,266,864 rows in 4,726 CSV files / 84,911,231,452 bytes.
ChEMBL's raw audit took 845.336509 s and FooDB's took 925.231693 s. All completed
tables matched their expected counts. No final indexes, derived tables, products
or full verification ran. The changed loader passed all 396 PostgreSQL/subset
tests in 21.86 s; Ruff, diff checks and independent review passed. The separate
39-resource raw validation remains successful.

A read-only screen covered all 46 selected resources: canonical identifiers and
labels had maximum 1240 UTF-8 bytes; 2442 alias index candidates exceeded a
2000-byte composite-field screen. The unbounded alias index failed in a local
disposable PG16 cluster on these actual values (3704-byte entry versus 2704-byte
limit). The index now uses the first 256 lowered characters with text_pattern_ops;
all stored TEXT and nested/view values remain complete. The same 2442 values
passed actual index creation in 0.013856 s and exact full-value comparison.
Candidate-file SHA-256: d510c11aee16ac89e3170cfccac08e941d910e58f2b843ad7ad63e702d84827b.
Shared-prefix and four-byte Unicode regressions preserve complete lookup results.
This preflight prevented a late full-transaction index failure; no source rebuild
or query API change was needed.

**Outcome**: failed while staging HMDB entities, before any HMDB table copied.
HMDB SQL validation passed in 8.789128 s; its CSV projection then needed a
128 MiB allocation with 394.0 MiB already used against the 488.2 MiB effective
DuckDB allowance. `OutOfMemoryException` unwound the outer transaction.
At 2026-09-30T18:45:03.288337Z, a fresh private connection confirmed
`full_20260930` absent, no import/verifier backends, and both `pilot_20260930`
and `annotation_sample_20260930` preserved. Thus no base load committed.
Archived private evidence: `postgres-build-attempt4.json`/`.log`,
`attempt4-rollback.json`, and `verification-wait-attempt4.log`.

The seven uncapped native replays still cover all 300,008 originally published
source records; 39 other pins are reused. No Parquets or production services
were changed by this failed import. Before retrying, test the exact projections
at a larger bounded DuckDB limit. Future loads use `420c407`'s approved default
`validate_source_records=False`; optional raw-audit counts will be empty while
artifact checks, normalized integrity and reaction-annotation checks stay
mandatory. This failed attempt itself retained the original deep audits.

## 2026-09-30 — full resolved-Parquet release on nicesrv, attempt 3 (cancelled)

**Reason**: run the complete fixed release through the restored DuckDB SQL
projection and bulk COPY path after exact equivalence tests and a real-data
comparison. The two Python-loading attempts rolled back without base commits.

**Parameters**: loader `91317a3`; unchanged native replay `300d06b`, pypath
`e27d72e095f53b1a0fd76d63bbe059c10f8a48d8`. Private release `2026.9.30.4`,
manifest SHA-256 `06f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e`,
schema `full_20260930`; exact 46 pins in the private `release.json`.
Invocation: `uv run --frozen --no-sync python
/root/projects/omnipath-migration/full-release-20260930/postgres-sql-build.py`.
The script calls `load_release(..., batch_size=1024, duckdb_threads=1,
memory_limit='512MB', temp_directory=state)` then all three subset adapters.
Database/role `omnipath_migration`, localhost 5440, isolated PG16 container
(two CPUs / 4 GiB RAM). Process: two CPUs / 3 GiB RAM / 256 MiB swap;
64 GiB free-disk reserve. Session: work_mem16MB, maintenance128MB,
max_parallel_workers_per_gather1, jit off. Unit:
`omnipath-migration-full-postgres-sql-20260930.service`.

DuckDB validates typed source shapes/counts in two aggregate scans per resource,
then flattens all five tables and serializes JSON in SQL. One table at a time
is written to CSV chunks (16 MB rotation threshold), forwarded as 1 MiB byte
blocks to PostgreSQL COPY, and deleted. Each chunk is a separate immediate-FK
statement inside the atomic base transaction; complete parent tables precede
children. No Python projected-row expansion. Raw validation remains the separate
dictionary-preserving reader with every pointer/owner/SHA/type/count check.
No source parsing, entity resolution, downloads or resource rebuilds.

**Phase durations**: deliberately cancelled after 2002.583925 s. Release
reading took 5.378590 s; BindingDB SQL shape/count validation took 16.292700 s.
All five BindingDB tables copied: 52,774,666 rows. Their SQL staging total was
238.644504 s and PostgreSQL COPY total 1447.194627 s. Source-owner validation
started at elapsed 1708.965 s but did not finish; indexes, derived tables and
products did not run. A live owner-key query waited on DataFileRead after
36.416660 s. The separate 39-resource raw validation remains successful.

**Outcome**: cancelled to correct missing planner statistics before non-reaction
source-owner checks. The preliminary scalar ANALYZE had covered only reaction
resources; the batch owner lookups also need current entity/relation statistics.
No data or schema change is required. Psycopg cancelled the active query and
rolled back. A fresh private connection confirmed `full_20260930` absent, no
import/verifier backend remaining and both previous private schemas present.
`postgres-build-attempt3.json`/`.log` and `attempt3-rollback.json` retain the
results. The queued verifier stopped before SQL checks. The seven full native
rebuilds, 39 reused versions and fixed release pins remain unchanged. Production
was not published or modified.

## 2026-09-30 — DuckDB bulk projection/COPY comparison on nicesrv (completed)

**Reason**: confirm the restored SQL projection/COPY path is faithful and measure
its cost against the Python implementation before restarting the full release.

**Parameters**: code `91317a3`, private `bulk-benchmark.py` in
`/root/projects/omnipath-migration/full-release-20260930`, invoked with
`uv run --frozen --no-sync python`. The first 50,000 published BindingDB entities
(version `2026.9.8.1`) are written once to a temporary Parquet subset; relations
are empty in both paths. SQL and Python consume identical files. Both load all
five target tables with complete entity JSON, identifier and annotation
occurrences and the same PostgreSQL schema/primary keys/immediate foreign keys.
SQL uses one DuckDB thread, 512 MB working memory, 16 MB CSV rotation threshold
and 1 MiB byte forwarding. Python uses the previous reference projector,
1024-row COPY batches and parent flushing. All writes use one private transaction
on database/role `omnipath_migration`, localhost 5440, with separate new schemas
`bulk_sql_benchmark_20260930` and `bulk_python_benchmark_20260930`; the transaction
is rolled back after exact bidirectional EXCEPT ALL checks on every projected
column. No source parser, resolution, download or product/index derivation.
Process cap: 3 GiB RAM / 256 MiB swap / two CPUs; isolated PG cap: 4 GiB/two CPUs.

**Phase durations**: subset preparation 0.869144 s; SQL projection/COPY
17.944366 s, Python projection/COPY 28.587794 s. SQL staging took 0.997005 s
across five tables; PostgreSQL COPY took 16.913948 s. Total benchmark/check/
rollback script 55.885584 s. SQL ran first once; Python benefited from any warmed
cache. Process peak RSS 532,532 KiB is cumulative and cannot compare the methods'
memory independently. The cgroup peak was 467.8 MiB. This is a projection/
transport comparison, not a full-release performance estimate.

**Outcome**: success. Both paths loaded the same 50,000 entities and 880,917
identifiers. The relation, evidence and annotation outputs were empty; those
populated cases are covered by the integration fixtures. All projected columns,
including full entity JSON, passed exact bidirectional EXCEPT ALL. Both schemas
were rolled back and confirmed absent. SQL reduced measured elapsed time by
about 37%; no full-release speed claim follows from this subset. The corrected
loader passed 389 PostgreSQL/subset tests
(19.57 s) plus a separate actual multi-file CSV rotation regression (1.63 s).
Independent code/benchmark review, Ruff and diff checks passed.

## 2026-09-30 — full resolved-Parquet release on nicesrv, attempt 2 (cancelled)

**Reason**: retry the complete fixed release after correcting repeated raw payload
expansion. The first attempt rolled back before committing; no artifacts changed.

**Parameters**: loader `a71fcf7`, same release `2026.9.30.4`, canonical manifest
SHA-256 `06f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e`,
schema `full_20260930`, `batch_size=1024`, database/role `omnipath_migration` on
localhost 5440. Same `uv run --frozen --no-sync python .../postgres-build.py`,
46 resource pins, 3 GiB process/4 GiB container memory limits, two CPUs,
256 MiB process swap, 64 GiB disk reserve and session settings as attempt 1.
The unit is `omnipath-migration-full-postgres-attempt2-20260930.service`.
Raw reading retains one native dictionary and at most 256 index validation
results; dictionary batches have at most 1024 scalar owners. Plain raw and nested
record batches remain capped at 64. Every occurrence still receives pointer/key,
owner and count checks; SHA/type validation reuses only exact dictionary values.
No raw body is stored in PostgreSQL. All three products are built after base commit.

**Phase durations**: cancelled at the user's efficiency concern after
1040.683043 s; release reading 5.792572 s, completed COPY calls 699.742567 s.
28,084,290 copied rows are enumerated in `postgres-build-attempt2.json`; indexes,
derived tables and subsets did not run. Before restart, the actual 42,536-row
FooDB block passed in 1.010 s with peak RSS 472,936 KiB and one JSON parse/SHA
call for its 42,831,695-byte text. All 297 disposable PostgreSQL/subset tests
passed (14.22 s); independent review, Ruff and diff checks passed.

The separate artifact-only check completed all 39 reused resources, covering
23,561,803 raw rows, in 284.930287 s (completed first-attempt checks reused;
inputs and hashes unchanged). FooDB's 4,211,723 occurrences took 48.381147 s.
The corrected check peaked at 594 MiB inside its 3 GiB/one CPU cap.

**Outcome**: deliberately cancelled to restore the intended DuckDB SQL projection
and bulk COPY pipeline. The Python row-expansion implementation was inefficient
at full scale. Psycopg cancelled active COPY; a fresh private connection confirmed
`full_20260930` absent, no import backend remaining and both earlier private
schemas retained. The queued verifier was stopped before it began SQL checks.
`attempt2-rollback.json` records the rollback. The full artifacts and successful
raw validation remain available; native replay scope is all 300,008 originally
published records across seven resources, with 39 unchanged pins. Nothing was
published to production.

## 2026-09-30 — full resolved-Parquet release on nicesrv, attempt 1 (cancelled)

**Reason**: validate the complete release after moving all former PostgreSQL
payload consumers to published annotations. The seven affected resources were
rebuilt without a record cap; 39 unchanged resources retain their original pins.

**Parameters**: loader code `c2dfc11`, native replay code `300d06b`, pypath
`e27d72e095f53b1a0fd76d63bbe059c10f8a48d8`. Private release `2026.9.30.4`,
canonical manifest SHA-256
`06f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e`.
Exact resource pins, file checksums and preflight counts are retained in
`/root/projects/omnipath-migration/full-release-20260930/release.json` and
`final-preflight.json`. Data root: that directory's `data/`.

The private `launch-replay.py` ran `scripts/replay_resource.py` against the seven
original published resource directories, with version `2026.9.30.4`, immutable
reference `/root/projects/full_parquet/data/reference/.library-compact-20260912`,
private cached Human-GEM TSV, `--resource-ram-gb 3 --batch-workers 1
--duckdb-threads 1 --memory-limit 512MB --min-free-disk-gb 64`. No `--max-records`
argument. The process group had a 6 GiB memory / 512 MiB swap cap and two CPUs.
All 300,008 published source records, original row IDs and exact raw text passed
replay verification. Unpublished rows discarded by historical builds are outside
this replay's coverage. The 39 reused versions are independent file copies.

Database invocation, from the isolated migration checkout:

```sh
uv run --frozen --no-sync python \
  /root/projects/omnipath-migration/full-release-20260930/postgres-build.py
```

The script calls `load_release(..., schema='full_20260930', batch_size=1024)`
then `build_subsets(..., products=('metsigdb','network_views','cosmos'))` in a
separate transaction after the base release commits. Database/role
`omnipath_migration`, localhost port 5440, isolated container
`omnipath-migration-postgres` (2 CPUs / 4 GiB RAM). Import process: 3 GiB memory,
256 MiB swap, two CPUs, minimum disk reserve 64 GiB on the same filesystem as its
Docker volume. Session: `work_mem=16MB`, `maintenance_work_mem=128MB`,
`max_parallel_workers_per_gather=1`, `jit=off`. Buffered Arrow decoding uses at
most 64 rows; exact-text validation cache at most 256 entries / 64 MiB charged
storage. COPY foreign keys are immediate within the one atomic base transaction.
No source deletion, download, reference preparation, reparse or entity resolution
runs in PostgreSQL. Existing schemas and production services are preserved.

**Phase durations**: full native replay Rhea 329.122207 s, Recon3D 62.496945 s,
MetAtlas 76.459144 s, KEGG 388.689825 s, Reactome 101.476603 s,
MACdb 34.849949 s, ConnectomeDB2025 61.983096 s. Per-dataset coverage, native
prepare/finalize timings and source hashes are in `rebuilt-data/replay_report.json`.
KEGG is the replay outlier. Final fixed-release preflight 10.144114 s.
The first database attempt ran 2527.816089 s before deliberate cancellation.
Release reading took 5.889342 s; completed COPY calls took 1611.889483 s.
BindingDB projection was incomplete; indexes, derived tables and products did
not run. `postgres-build-attempt1.json` and `postgres-build-attempt1.log`
retain its phase/source progress. Source projection/COPY and raw-validation intervals
are approximate (final partial batches and preliminary ANALYZE sit outside those
intervals); loader totals and separate COPY call totals are authoritative.

**Outcome**: cancelled before any base commit after 51,925,536 rows were copied
inside the transaction. A concurrent artifact check identified a 42,831,695-byte
FooDB dictionary value repeated 42,536 times; expanding 64 rows materialized
2,741,228,480 bytes before Python validation. This would make later loading
unsafe/slow. Only the private import and checker units were stopped to replace
the raw reader with dictionary-preserving validation. Psycopg cancelled COPY and
rolled back; a separate connection confirmed `full_20260930` absent, no import
backend remaining, and both existing private schemas retained. Production
container IDs/start times were unchanged. Disk returned from about 491 GiB free
to 530 GiB. No source artifacts or pins changed.

An earlier artifact-only check was OOM-killed while DuckDB materialized FooDB;
it made no database writes. Two bounded Arrow attempts were deliberately stopped
(the second to enable exact-text caching, the third for dictionary preservation).
Completed checks from the first attempt are reusable because input hashes are
unchanged. Retry and full validation remain pending. Preflight expects 6,383,473
entities, 80,136,865 identifiers, 18,339,902 relations, 22,887,251 evidence
occurrences and 150,690,040 annotations; 26,711,866 raw payload rows are validated
and discarded. A failed/interrupted commit boundary must be inspected through
release metadata rather than inferred from a missing completion flag.

## 2026-09-30 — capped annotation migration on nicesrv (completed)

**Reason**: verify that all former PostgreSQL product payload readers can use
published annotations, so raw source bodies can stay exclusively in Parquet.

**Parameters**: build code `7ed2e63`, pypath `e27d72e09`; seven new private
resource versions and private release `2026.9.30.3`. Exactly 20 original records
per resource, 140 total. Sources: Rhea, Recon3D, MetAtlas/Human-GEM, KEGG,
Reactome, MACdb and ConnectomeDB2025. Selected originals and input hashes live in
`/root/projects/omnipath-migration/annotation-migration-20260930/inputs`.

Native rebuild invocation, from the isolated migration checkout:

```sh
uv run --frozen --no-sync python scripts/annotation_migration_smoke.py \
  --inputs /root/projects/omnipath-migration/annotation-migration-20260930/inputs \
  --output-dir /root/projects/omnipath-migration/annotation-migration-20260930/data \
  --library-dir /root/projects/full_parquet/data/reference/.library-compact-20260912 \
  --version 2026.9.30.3
```

One preparation worker and DuckDB thread, 1 GiB resource RAM, batches at most
20 records, 20 relations and 64 MiB. The selected iterator enforces a total
20-record cap across datasets, beyond the pipeline's per-dataset cap. No source
downloads or reference preparation. Native resolution used the existing immutable
prototype reference. An initial launch with an incorrect `data/ref` path failed
before creating output or consuming records; the verified `data/reference` path
above succeeded.

Database invocation is the private `postgres-check.py` in that state directory,
using `uv run --frozen --no-sync python` from the isolated checkout. Loader
`batch_size=64`, DuckDB one thread / 256 MiB, database/role `omnipath_migration`
on localhost port 5440 in `omnipath-migration-postgres`. Fresh schema
`annotation_sample_20260930`; existing pilot schema retained. No source deletion,
preparse or canonicalization runs in the loader. Source bodies are streamed and
validated, including exact SHA/type checks, then discarded.

**Phase durations**: native resource builds: Rhea 5.66 s, Recon3D 3.20 s,
MetAtlas 4.16 s, KEGG 4.63 s, Reactome 4.19 s, MACdb 3.01 s,
ConnectomeDB2025 3.21 s. Database code `4c896eb`; validate_files 0.011331 s,
copy_and_validate 2.239933 s,
indexes_and_derived 0.548047 s,
recheck_files 0.007211 s; load/commit
2.823386 s. Per-resource projection/COPY: connectomedb2025 0.043 s; kegg 0.537 s; macdb 0.045 s; metatlas 0.097 s; reactome 0.148 s; recon3d 0.045 s; rhea 0.260 s.
Index creation and initial derivation were measured together by the loader;
no source delete, preparse or canonicalization phase ran in PostgreSQL.
PG-only product rebuild 0.944530 s; full load/check script
5.039105 s. KEGG was the sample's copy outlier (largest fan-out).

**Outcome**: all seven capped native builds passed strict manifest/schema/hash
inspection. The new schema committed successfully; every projected row matched Parquet
(1,482 entities, 19,781 identifiers, 1,974 relations, 2,226 evidence occurrences,
5,772 annotations). All 2,232 raw rows were verified then discarded. Reaction,
COSMOS, MetSigDB and network checks passed with the Parquet directory hidden;
source artifacts were subsequently restored and revalidated. MetaLinksDB had no
eligible server sample rows; populated cases passed separate integration fixtures.
Original pilot metadata, all 21 original resource artifacts and production
container identities/start times were preserved. These private capped versions
must not replace full production artifacts. See the
[validation report](source-attributes-validation.md) for counts and scope limits.


## 2026-09-30 — nicesrv resolved-Parquet pilot, attempt 2

**Reason**: repeat the identical pinned pilot after fixing statistics timing
identified by the cancelled first attempt.

**Parameters**: code `fa439b380c692de7531aa9dc067c9a3843bacb3b`; private release
`2026.9.30.1`, SIGNOR/ChemOnt/Rhea each `2026.9.5.17`;
`batch_size=1024`, DuckDB one thread / 256 MB. Same isolated new container,
volume and localhost port `5440`, database `omnipath_migration`,
fresh schema `pilot_20260930`; 2 CPUs / 4 GiB RAM. Exact configuration,
artifact hashes and scripts are retained in the private server pilot directory.

**Phase durations**: validate_files 0.13 s, copy_and_validate 313.03 s, indexes_and_derived 384.53 s, recheck_files 0.13 s;
total successful load including commit 697.88 s.
Per-resource projection/copy: chemont 1.33 s, rhea 174.18 s, signor 31.64 s.
No delete, extraction/preparse or canonicalization ran. Independent verification
took 176.28 s. Rhea is the source copy outlier.

**Outcome**: committed pilot; every base record/raw payload matched its original
Parquet, all flat-array, ontology, reaction, index and constraint checks passed.
Database size 7.70 GiB from 134.44 MiB compressed input.
Production PostgreSQL and prototype services were unchanged. Complete evidence
and scope limits are in [the pilot report](nicesrv-parquet-pilot.md).

## 2026-09-30 — nicesrv resolved-Parquet pilot, attempt 1 (rolled back)

**Reason**: test the migration loader against existing prototype artifacts before
expanding to a complete release. No resource extraction or resolution ran.

**Parameters**: migration commit `0417420`, private release `2026.9.30.1`
pinning SIGNOR, ChemOnt and Rhea at `2026.9.5.17`; source data
`/root/projects/full_parquet/data`. Loader `batch_size=1024`, DuckDB one thread
and 256 MB limit. A new PostgreSQL 16 container `omnipath-migration-postgres`
uses only its new volume `omnipath-migration-postgres-data` and localhost port
`5440`, database/role `omnipath_migration`, schema `pilot_20260930`; 2 CPUs,
4 GiB memory, 256 MB shared buffers, 16 MB work_mem. Private scripts, manifest,
credentials and logs are in `/root/projects/omnipath-migration/pilot-20260930`.

**Phase durations**: preflight schema/footer/hash validation 0.14 s;
ChemOnt copy/projection 1.31 s, Rhea 174.87 s, SIGNOR 30.28 s; integrity
validation 115.67 s; ordinary indexes 15.85 s; ontology derivation 12.96 s.
Reaction derivation's first FETCH remained blocked for about 262 s and the
attempt was cancelled at 624.21 s total. Rhea is the copy outlier (842,046
payload rows). At 140 s into the reaction FETCH, sampled container block I/O
was 70.4 GB read / 21.7 GB written; those are cumulative container counters,
not reaction-only I/O. Nine input files total 140,970,841 bytes.

**Outcome**: base integrity checks passed, but fresh-table statistics were only
collected after derivation. The reaction join therefore ran without column
statistics and did excessive disk work. Cancelled only the pilot backend;
the transaction rolled back and the pilot schema is absent. The production
PostgreSQL and prototype containers were not accessed for SQL or changed.
Attempt logs are retained as `attempt1.log` and `attempt1-result.json`.
Moving ANALYZE before integrity joins/derivation and including derived tables
in final statistics collection is the first correction; a retry will determine
whether additional query changes are needed.

## 2026-09-10 — cycle 011's final full uncapped rebuild

**Reason**: land the two fixes the cycle had deliberately deferred to its
final rebuild (`annotation_object_entity`'s unpromoted duplicate chemical
entities, `chemical_fallback.py`'s unnormalized name-tier canonical key),
then verify the whole cycle (WP1-WP8) against `contracts/coverage-
acceptance.sql` on one clean, complete build — the gating step before cycle
011 can be considered closed.

**Parameters**: `make reset-content` then `make reload` (`RELOAD_EXISTING=1`,
all sources, uncapped, default `LOAD_JOBS=1`/`THREADS=4`/`BATCH_SIZE=50000`)
against `DATABASE_URL=postgresql://omnipath:omnipath@chemres1-omnipath-build-postgres:5432/omnipath`
and `OMNIPATH_BUILD_UTILS_PG_URL=postgresql://omnipath:omnipath_utils_chemres1-utils_dev@omnipath-utils-chemres1-utils-db:5432/omnipath_utils`,
then `make derive`, then (`omnipath-metabo`) a one-time
`TRUNCATE metabo_lipid_name_resolution` (the ~1.9M-row incremental cache still
carrying pre-T121 chain counts) followed by `post_build_metabo(force=True,
conflicts=False)`.

**Four real bugs found and fixed along the way** (all committed, all
verified live before proceeding to the next step):

1. `84a3ff1` — `annotation_object_entity` self-typed a chemical annotation
   object's raw `(object_id_type, object_id)` instead of promoting it through
   the resolver, minting permanent unpromoted duplicates (the deferred fix
   this rebuild existed to land). Fixed via a new `annotation_object_resolution`
   table (mirrors `ontology_relation_endpoint_key`'s existing pattern for "a
   bare external identifier that is not a full mention"); three other read
   sites needed the identical fix to stay consistent with the entity this now
   mints (`sources` attribution, `annotation_projected`,
   `pq_annotation_relation_evidence_resolved`).
2. `d5c2cda` — WP5's T092 (widening the resolver's chemical namespace list to
   drugbank/reactome) registered both in `RESOLVER_CHEMICAL_SLUG_TO_IDENTIFIER_TYPE`
   but never in `resolver/identifier_types.py`'s separate, must-stay-in-sync
   stable-id registry every resolver setup call validates against. Crashed
   **every single dataset** identically on the first reload attempt
   (`source_rows=0` across all 45 sources, `ValueError: Unknown resolver
   identifier type: 'Drugbank:MI:2002'`) — caught immediately since a full
   reload is exactly the kind of run T092's own narrower verification never
   exercised.
3. `ef6a75b` — `structure_consistency_finding`/`_summary` (WP6, T098) were
   never added to `CONTENT_TABLES`; `reset-content` truncates the whole list
   in one statement, so the unlisted table (with a `data_source` FK) made
   Postgres refuse the entire truncate.
4. `a141963` + `1667fc3` — two separate `vocab_identifier_type` seeding bugs,
   both only reachable on a database with real build history (this sandbox's
   table accumulates across every build ever run here — it is not a content
   table, `reset-content` never touches it): `_ensure_static_identifier_types`
   unconditionally upserted by id, which either crashed or would have silently
   renamed an unrelated row wherever a newly-statically-listed name (T092's
   drugbank/reactome, T116's lipid name) already held a different id from an
   earlier partial run; separately, WP7's own lipid-name seed's
   `... FROM vocab_identifier_type WHERE NOT EXISTS (...)` guard didn't
   actually skip the insert when false (an aggregate without GROUP BY always
   returns one row), so it still attempted an insert with a bogus id and
   crashed on the same already-populated row. Both made purely additive/
   idempotent; verified live as true no-ops against the populated table
   before rerunning.

One further **non-code** issue: the first reload attempt ran clean for
1h48m (through chembl/foodb/go/intact/kegg/pfocr/stitch) then hard-crashed on
a `UnicodeDecodeError` copying a SwissLipids shard to Postgres. Root cause: a
stale preparse cache (`pypath-data/swisslipids/preparse/lipids/batch_50000_all`,
dated 2026-09-08, predating this cycle's SwissLipids `InChIKey=` prefix fix)
had corrupted bytes baked in from before the fix landed — not a code bug.
Deleting the cache directory (forcing a fresh preparse, correctly applying
the `encoding='latin-1'` the loader already declares) resolved it; verified
with a small scoped reload before restarting the full one. This also
suggests the earlier-documented "SwissLipids reload blocked by a `cachedir`
bug" note may no longer reproduce — a fresh reload succeeded cleanly this
time with no sign of that error.

**Phase durations** (second, successful full attempt): reload
**6536.2s (~1h49m)** — `sources=45 skipped_sources=0 datasets=100
failed_sources=3 failed_datasets=5 source_rows=9,920,814
identifiers=63,614,139 annotations=92,029,431` (matches an earlier cycle
baseline almost exactly); the 5 failed datasets are the already-documented
pre-existing bugs (`bindingdb.interactions`, `metatlas.metabolites`,
`mirbase.matures`, `mirbase.precursors`, `ptfi.foods`) — none new. `derive`
**~1h15m** (chem-resolution-level 232s: `chemical_entities=2,401,031
members=7,203,093 groups=6,877,860 relations=15,421,707`; ambiguous-name
candidates 71s; bitmaps 589.6s). `post_build_metabo` **4816.0s (~80m)** —
structure substrate 2,196,732 molecules; Goslin lipid labels on 1,027,283
entities (1,124,040 names resolved) with **`partially_specified=6,258`
appearing as its own real level for the first time** (direct, real-data
confirmation of WP7's T109 fix — the removed species-downgrade used to
collapse every one of these); QC structure-consistency: internal=503,958
cross_reference=85,974 cross_reference_pair=21,125.

**Outcome — the full `coverage-acceptance.sql` gate**:

| SC | Check | Baseline → target | Result | Verdict |
|---|---|---|---|---|
| SC-001 | ChEBI-canonical entities | 54,323 → ≤23,000 | 50,301 | fail (moved from 51,832; not expected to close alone, per `deferred-items.md`) |
| SC-002/003 | Reactome structure reach | ~0.1% → ≥80% | 63.6% | fail (known, legitimate ChEBI class-node structurelessness, not a bug) |
| SC-002/003 | zero-reach group ≥60% | — | rhea 81.9%, pfocr 81.9%, intact 80.6% | pass (tcdb has <100 chemical entities, below the query's own threshold) |
| SC-004 | Reactome↔HMDB shared | 4 → ≥1,300 | 484 | fail (expected; not this fix's target) |
| SC-005 | unresolved chemical entities | 13,631 → ≤5,500, accounted | 365, 365 accounted | **pass** |
| SC-006 | KEGG structure reach ≥70% | — | 81.9% | **pass** |
| SC-006 | KEGG pair-check agreement ≥90% | — | no rows (kegg is itself a structure authority, so it never appears as a *structureless citing* party under WP6's redesigned pair check — a scoping mismatch with this criterion's original, pre-redesign assumption, not a new failure) | n/a |
| SC-007 | every identity syntactically valid | — | 0 invalid | **pass** |
| SC-007 | no identity exceeds the hub threshold | — | largest = 2,650 | **pass** |
| SC-011 | no lipid name collision across chain assignment | — | 0 | **pass** |
| SC-012 | name-canonical entities | 171,852 → ≤40,000 | 47,282 | fail, but close (a ~3.6x reduction from baseline; the normalize_name fix worked, remaining gap is other near-duplicate forms a simple case/whitespace fold doesn't catch) |
| SC-013 | manifest carries coverage | — | true | **pass** |
| SC-014 | db size ≤82GB × 1.10 | — | 91 GB (budget 90.2 GB) | fail, narrowly (~1% over; WP6/WP7 added more than R11's ~3% estimate) |

SC-008/009/010/015/016 are verified elsewhere per the acceptance file's own
header (contract tests, not this SQL gate).

**Net**: 6 of 13 directly-gated criteria pass outright (SC-005, SC-006a,
SC-007 ×2, SC-011, SC-013); SC-001/004/002-003/012/014 remain open, all
either already-documented-as-out-of-scope for these two fixes or improved
substantially without fully closing. No new, unexplained acceptance
regressions.

---

## 2026-09-09 — omnipath-utils WP5: RefMet/Reactome loads, HMDB extension, SwissLipids fix, exemptions (T084-T094)

**Reason**: verify spec 011 WP5 (namespace coverage) against the live
`chemres1` utils DB — a different database
(`omnipath-utils-chemres1-utils-db:5432/omnipath_utils`) than
`omnipath-build`'s own, logged here per the standing rule of recording
every real run against a project database.

**Parameters**: not a `make` invocation — direct Python calls (via
`DatabaseBuilder`) against
`postgresql://omnipath:omnipath_utils_chemres1-utils_dev@omnipath-utils-chemres1-utils-db:5432/omnipath_utils`:
`_long_refmet()`, `_populate_reactome()`, `_long_hmdb()` (re-run with the
CAS/IUPAC/traditional-IUPAC extension), `_clear_wrong_content_rows()`,
`populate_namespace_exemptions()`, plus a one-time SQL cleanup of
already-stored SwissLipids rows and two runs of
`rebuild_resolver_chemical_duckdb` (the first with a since-fixed Reactome
bug, so its output was discarded and rebuilt).

**Phase durations**: RefMet load (208,170 raw rows fetched from a 22.9 MB
CSV, cached fast) a few seconds. Reactome load (`reactome_chebis()`, a
streamed TSV) well under a minute. HMDB re-run (2.2M+ metabolite records,
the largest single source) a few minutes. `resolver_chemical` rebuild via
DuckDB: ~65s to compute 132.8M rows, ~90-100s to write the Postgres staging
table, then a native `CREATE MATERIALIZED VIEW` + index over that — the
first rebuild attempt's own `DROP TABLE IF EXISTS` (lacking `CASCADE`)
failed because the old materialized view still depended on the staging
table; fixed by dropping the old view `CASCADE` first (a pre-existing
script limitation, not something this cycle introduced).

**Outcome**: RefMet — 208,170 `refmet_id<->name` rows plus 98,502
cross-reference rows to chebi/hmdb/kegg/lipidmaps/pubchem (a raw-CSV
quirk cost one round trip: RefMet's first CSV header column carries a
leading space, `" refmet_id"` not `"refmet_id"`, silently producing zero
rows for the primary id until caught and fixed). Reactome — a new
`reactome` id_type, 38,329 `chebi<->reactome` `id_mapping` rows (a second
bug caught and fixed: Reactome's raw `chebi_id` is a bare number, but
`id_mapping`'s own chebi convention is `CHEBI:NNNN` — verified against
existing `bigg->chebi` and `chebi->inchikey` rows before fixing), 31,406 of
which now bridge to a real InChIKey through the rebuilt
`resolver_chemical`. DrugBank — no new load needed (already had 15,293
`id_mapping` rows from an earlier run), now reads 14,138 rows through
`resolver_chemical` after `duckdb_load.py`'s namespace-slug list was
widened (T092). SwissLipids — 777,811 stored `swisslipids -> inchikey`
rows cleaned to 593,217 well-formed keys (184,509 `InChIKey=none`
placeholders deleted, 592,254 prefixed values stripped in place, 85 other
garbage values deleted); the loader fix itself is verified by a unit test
since the live reload is still blocked by the pre-existing `cachedir` bug.
HMDB — extended to also reach CAS (15,672-216,844 rows depending on the
pairing), IUPAC name and traditional IUPAC name, fields its raw records
already carried but the loader never read. Rhea/PDBe — 3,219 and 242,934
wrong-content `id_mapping` rows respectively cleared (UniChem's Rhea
"compound id" is really the participant's ChEBI id; its PDBe "id" is a
display label plus conformer type, not the ligand code), and both excluded
from future auto-discovery. 21 namespaces recorded as reviewed exemptions
(`namespace_exemption`, new table) — id_types.yaml declares each reachable
via hmdb/ramp/chalmers_gem, but a live check of each source's actual raw
data confirms none of the fields exist. Full namespace-by-namespace
reasoning in `saezverse` `deferred-items.md`.

**Verification**: every declared small-molecule namespace (53 total) now
has either real `id_mapping`/`id_mapping_long` rows or a recorded, reviewed
exemption, and no exemption is stale (both checked directly via SQL against
the live database — the equivalent `pytest` suite,
`tests/test_namespace_coverage.py`, is correct but impractically slow over
this DB's network round-trip time, ~5-14s per parametrized case across 60
cases; the two fast, non-DB unit tests in `test_swisslipids_backend.py`
pass in under half a second).

---

## 2026-09-09 — omnipath-metabo WP6: structure consistency diagnostics (T098-T105)

**Reason**: verify WP6's three structure-consistency checks
(`omnipath-metabo/omnipath_metabo/postbuild/_qc_layer.py`) against the live
build — a different repo/service (`omnipath-metabo`, freshly cloned this
session) than `omnipath-build`'s own `make load`/`make reload`, but the same
target database, so logged here per the standing rule of recording every real
run against it.

**Parameters**: not a `make` invocation — direct Python calls against
`postgresql://omnipath:omnipath@chemres1-omnipath-build-postgres:5432/omnipath`,
schema `public`: `build_structure_consistency_findings(conn, schema='public')`
(three times, iterating on a performance fix) and `post_build_metabo(conn,
schema='public', force=True, conflicts=False)` (the full post-build pipeline,
including the new QC step).

**Phase durations**: first version of the cross-reference/cross-reference-pair
queries — driven from `entity_evidence` (all chemical mentions, ~tens of
millions across 43 partitions) before filtering down to structure-bearing
ones — did not finish in 15 minutes (`timeout 900` killed it). Rewritten to
start from the ~2.9M structure-authority-typed `identifier_evidence` rows
(and a precomputed authority-identifier-to-entity map, built once, reused by
both checks): full three-check pass **671s (~11m12s)** —
`internal=1,199 cross_reference=44,303 cross_reference_pair=16,979
summary_rows=194`. The full `post_build_metabo(force=True)` pipeline
(substrate rebuild + specificity + facet + lipid labels + QC, no RaMP
conflicts): structure substrate **2,223,612 molecules**; specificity
`cis_trans_only=102,451 constitution_only=752,411 no_structure=288,224
stereospecific=1,173,513 unknown_constitution=387,377
variable_constitution=195,237`; Goslin lipid labels on 1,072,117 entities
(1,125,099 names resolved, 770,019 unresolved).

**Outcome**: the checks are correctly derived from `identifier_authority`
(no hand-listed sources), classify sensibly (`different_structure` well
under half of every summary row, e.g. a real `refmet`/`kegg` namespace
disagreement at 3.3% agreement against markedly-higher agreement for other
pairs — the same class of defect research.md R8's original KEGG
investigation found), and are served with no request-time computation
(`test_qc_layer.py`, 13/13 passing). The internal check is narrower than
`contracts/quality-control-api.md` describes — this build's rdkit cartridge
has no InChI support (`mol_from_inchi` does not exist; `mol_inchikey`
returns the literal string `InChI not available`), and `omnipath-metabo`
deliberately runs chemistry only through the cartridge, never Python rdkit —
so it compares a record's own duplicate SMILES assertions against each
other, not SMILES vs. InChI vs. InChIKey. Full writeup in `saezverse`
`specs/011-chemical-resolution/deferred-items.md`.

**Self-inflicted incident, noted for the record**: an intermediate
`test_postbuild.py::test_post_build_refuses_on_build_id_mismatch` run was
killed by its own 30-minute `timeout` wrapper mid-way through a `force=True`
rebuild (the QC step roughly tripled that test's total cost, since it calls
`post_build_metabo` three times). This left `metabo_build_state` stuck at
the test's injected sentinel `deadbeefdead` and an orphaned
`CREATE INDEX ... metabo_entity_structure_mol_idx` still finishing
server-side (harmless — any subsequent `build_structure_substrate` call
drops and rebuilds it anyway). Fixed with one more `force=True` run once the
orphaned index finished; not a code defect.

---

## 2026-09-09 — derive run for entity_name/T072-T074, and the shared-database rename

**Reason**: verify T072-T074 (entity_name table, its population, the new
label-cascade pass) against real data by running `derive` on the live build
DB, per T065's test. Mid-run, a colleague reported that `netds1` and this
sandbox (`chemres1`) appeared to be sharing the same build database
container. Confirmed on `beauty`: both sandboxes' Docker Compose projects
defaulted to the same project name (`omnipath-build`, from the directory
basename), so both had been writing to the exact same
`omnipath-build-postgres-1` container and volume the whole cycle, unknown
to either side. Full story in the `shared-db-incident` memory.

**Parameters**: `make derive` (no `MAX_RECORDS`, full uncapped) against
`DATABASE_URL=postgresql://omnipath:omnipath@omnipath-build-postgres-1:5432/omnipath`
(the hostname *at the time this ran* — see below, it no longer resolves).
Launched ~17:07 UTC.

**Phase durations**: total `event=done seconds=3073.780` (~51m14s),
`failed_steps=` empty. Major phases: derive-tables (schema/PK/index setup
plus `entity_identifier_lookup`/`entity_relation_counts`/
`entity_ontology_term`/`entity_source_count`) 351s; chemical resolution
levels 185s; chemical ambiguous-name candidates 62s; `classify_chemical_class`
308s; `classify_metabolic_domain` 439s; interactions (the largest single
phase) 679s; `entity_labels` 145s; **`entity_name` 77s, 4,157,246 rows
written**; **`chemical_labels` 158s**, `chemical_name=1,194,082
chemical_iupac_name=4,605 chemical_preferred_name=0 chemical_identifier=1,699,773
without_real_label=753`; bitmaps (the second-largest phase) 452s; metsigdb
158s.

**Outcome**: `entity_name`/T072-T073 confirmed working against real,
full-scale data. `chemical_preferred_name=0` is expected, not a bug — the
new T074 pass is a safety net for names `entity_name` reaches but the
pre-existing tiered cascade doesn't; today both read identical underlying
data, so pass 1 already covers everything pass 1b would have caught.
`tests/test_preferred_label.py` (T065) 4/4 GREEN against this run's
resulting state.

**Database hostname changed as a result of the shared-DB fix**: the
container was renamed `omnipath-build-postgres-1` →
`chemres1-omnipath-build-postgres` and its old network aliases dropped
(disconnect+reconnect on the running container — Postgres itself was never
restarted, no data touched). Every command above used the *old* name,
still correct history for what actually ran; **any new command from this
point on must use the new name** — `db-access.env` and the `cycle011-status`
memory are both already updated. `netds1`'s own isolation (a distinct
`COMPOSE_PROJECT_NAME`) is still pending on their side as of this writing.

---

## 2026-09-09 — WP2 full uncapped rebuild (spec 011, cycle 011)

**Reason**: WP2 (T048-T060, candidate arbitration — skeleton collapse,
neutral-parent/least-specified-stereo tie-break, authority arbitration,
`resolution_conflict` recording) is implemented and unit-tested
(`main@8d9b0f1`), but not yet checked against real, full-scale data. This
run exists to answer: does WP2 actually move SC-001/002/003/004/005 past
their targets now, and does T061/T062's threshold hold (unresolved <5,500,
KEGG structure reach >70%)?

First had to fix a database schema issue found while preparing this run:
`entity`, `entity_evidence`, `identifier_evidence`, `relation`,
`relation_evidence`, and `annotation` all predated their declared
`PRIMARY KEY` on this database (`CREATE TABLE IF NOT EXISTS` is a no-op on
an existing table, so a table created before the PK was added to its DDL
never retrofits one) — blocked every `make db-setup`. Separately,
`gene_protein_representative`/`state`/`state_component`/`evidence_state`
were never in `CONTENT_TABLES`, so `reset-content` never truncated them;
stale rows survived every prior wipe this cycle and eventually collided
with `evidence_state`'s own PK add. Both fixed in `main@b7e16b0`, verified
with a clean `make reset-content` (`entity` count confirmed 0) followed by
two consecutive clean `make db-setup` runs.

**Parameters**: identical to the two T047 runs below —

```
make reload \
  DATABASE_URL="postgresql://omnipath:omnipath@omnipath-build-postgres-1:5432/omnipath" \
  OMNIPATH_BUILD_UTILS_PG_URL="postgresql://omnipath:omnipath_utils_chemres1-utils_dev@omnipath-utils-chemres1-utils-db:5432/omnipath_utils"
```

All ~47 sources, uncapped (`--max-records 0`), `--stage-jobs 1`,
`BATCH_SIZE=50000`, `THREADS=4`. Only Postgres content was wiped
(`reset-content`), not the on-disk preparse shard cache, so preparse reused
the cache from the 2026-09-08 runs and was fast, same as the second T047
run below.

**Finished** ~13:58 UTC. Pipeline's own reported total: `sources=45
skipped_sources=0 datasets=100 failed_sources=3 failed_datasets=4
source_rows=9920814 identifiers=63614139 annotations=92029431
total=6455.728s` (~1h47m36s) — same 4 pre-existing unrelated failures as
both 2026-09-08 runs (bindingdb.interactions, metatlas.metabolites,
mirbase.precursors, ptfi.foods), plus the already-documented
`hormone2cell` discovery failure. `source_rows`/`identifiers`/`annotations`
are byte-for-byte identical to the second 2026-09-08 run, as expected —
same source data, cache reused.

**Outcome — first four acceptance blocks, before vs. after WP2**:

| Criterion | Target | Before WP2 (both 2026-09-08 runs) | After WP2 (this run) |
|---|---|---|---|
| SC-001 ChEBI-canonical | ≤23,000 | 51,832 (fail) | 51,832 (fail, unchanged — tied to the deferred `annotation_object_entity` bug, not WP2's scope) |
| SC-002/003 Reactome structure reach | ≥80% | 63.1% (fail) | 63.7% (fail, ~unchanged — legitimate ChEBI generic/class-node structurelessness, confirmed non-bug) |
| SC-002/003 intact/rhea/pfocr | ≥60% | pass | pass (80.6% / 81.9% / 81.9%) |
| SC-004 Reactome↔HMDB shared | ≥1,300 | 493 (fail) | 494 (fail, unchanged — same root cause as SC-001) |
| **SC-005 unresolved chemical entities** | **≤5,500** | **11,424 (fail)** | **365 (PASS)** |
| **SC-006 KEGG structure reach** | **≥70%** | not yet passing | **81.9% (PASS)** |

T061/T062's two thresholds (unresolved <5,500, KEGG structure reach >70%)
both cleared. SC-001/002-003(reactome)/004 remain exactly where they were —
expected, since none of them are in WP2's scope (they trace to the deferred
`annotation_object_entity` bug and legitimate ChEBI class-node
structurelessness, both already root-caused in the 2026-09-08 entry below).

**Bug found via this run's own diagnostic column**: SC-005's
`with_conflict_record` came back 0 despite 11,424→365 unresolved entities
and WP2's own unit tests proving the `resolution_conflict` CTE logic is
correct. Root cause: `resolution_conflict` was created correctly inside
each shard's DuckDB session, but was never added to
`STAGED_LOAD_TABLES` (`duckdb_direct_pipeline.py`), the `pq_*` view mapping,
or `_bulk_copy_canonical`'s COPY step (`duckdb_load.py`) — so it was
silently discarded before ever reaching Postgres. `resolution_conflict` was
0 rows database-wide after this run despite genuine candidate disagreements
existing. Fixed in `main@4e6f5c2`; verified against real data with a
standalone `kegg`-only reload (35,307 correctly-translated conflict rows
written for kegg alone). Full rebuild re-run below to backfill this for
every source.

---

## 2026-09-09 — WP2 rebuild #2, after fixing the resolution_conflict copy bug

**Reason**: the run above proved WP2's resolution logic works
(SC-005/SC-006 both clear target), but `resolution_conflict` was silently
empty database-wide due to the COPY bug fixed in `main@4e6f5c2` (see above).
A `kegg`-only standalone reload confirmed the fix works (35,307 rows for
kegg alone), but left the database in a mixed state — only `kegg` carries
`resolution_conflict` rows, the other 44 sources still reflect the buggy
pre-fix run. Re-running the full rebuild for a coherent, fully-correct
state and an accurate `with_conflict_record` figure.

**Parameters**: identical to the run above, preceded by a verified-clean
`make reset-content` (`entity` count confirmed 0).

**Finished**: pipeline's own reported total: `sources=45 skipped_sources=0
datasets=100 failed_sources=3 failed_datasets=4 source_rows=9920814
identifiers=63614139 annotations=92029431 total=7213.428s` (~2h0m13s — a
little slower than run #1's 1h47m36s, ordinary run-to-run variance on the
shared host, not a regression). `source_rows`/`identifiers`/`annotations`
identical to run #1 and to both 2026-09-08 runs, as expected — same source
data, same resolution logic, only the `resolution_conflict` COPY path
changed. Same 4 pre-existing unrelated failures again
(bindingdb.interactions, metatlas.metabolites, mirbase.precursors,
ptfi.foods).

**`resolution_conflict` now populated for every source with real
disagreements**, not just `kegg`:

| source | conflict rows |
|---|---|
| kegg | 35,307 |
| recon3d | 23,514 |
| metatlas | 3,472 |
| wikipathways | 2,117 |
| pmidb | 416 |
| phenol_explorer | 268 |
| intact | 168 |
| mrclinksdb | 68 |
| chebi | 12 |
| lipidmaps | 6 |
| mebocost | 4 |

Total 65,352 rows across 11 sources.

**Outcome — full re-run of the acceptance gate**: identical to run #1 on
every criterion (SC-001 51,832 fail, SC-002/003 Reactome 63.7% fail /
intact+rhea+pfocr pass their own bar, SC-004 494 fail, SC-005 365 unresolved
pass, SC-006 KEGG 81.9% pass) — confirming the copy-bug fix only affected
the `resolution_conflict` diagnostic table, not any resolution outcome.
**SC-005's `with_conflict_record` is now 365, exactly matching `unresolved`
(365)** — every unresolved chemical entity is accounted for by a recorded
conflict, closing the gap run #1 exposed. This is the fully-correct,
coherent state of the database for cycle 011's WP2 work: T061/T062 both
clear target, and the "recorded, never silently dropped" guarantee the
spec's User Story 2 asks for is now actually true in Postgres, not just in
the DuckDB session that computes it.

---

## 2026-09-08 — T047 full uncapped rebuild (spec 011, cycle 011)

**Reason**: T046's capped reload (`reactome,hmdb,chebi,kegg`, `MAX_RECORDS=5000`)
surfaced and fixed two real bugs beyond WP1's own T027-T045 —
`omnipath-build main@d879ba9` (resolver_candidate joined evidence's raw
identifier instead of the T040/T041-normalized one) and `main@656ad24`
(`multigene_split._explode_one` silently dropped `identifier_normalized` when
rebuilding `entity_identifier_raw` for shards with a multi-gene UniProt
mention). Both verified fixed on the capped run (Reactome structure reach
~0.1% → ~86.6%, Reactome↔HMDB shared metabolites 0 → 263 in the 5,000-row-
capped sample). T047 is the uncapped, all-sources run needed to actually check
`contracts/coverage-acceptance.sql`'s first four blocks (SC-001, SC-002/003,
SC-004, SC-005) — the capped numbers are indicative, not the acceptance gate
itself.

**Parameters**:

```
make reload \
  DATABASE_URL="postgresql://omnipath:omnipath@omnipath-build-postgres-1:5432/omnipath" \
  OMNIPATH_BUILD_UTILS_PG_URL="postgresql://omnipath:omnipath_utils_chemres1-utils_dev@omnipath-utils-chemres1-utils-db:5432/omnipath_utils"
```

i.e. Makefile defaults: `SOURCES=` (all ~47 discovered source modules, not a
named subset), `MAX_RECORDS=` (uncapped, `--max-records 0`), `BATCH_SIZE=50000`,
`THREADS=4`, `LOAD_JOBS=1` (`--stage-jobs 1`, sequential staging — not tuned up
for this run; `build-tuning.md` documents the utils PG is provisioned for
`LOAD_JOBS` up to 32, worth trying on a future uncapped run if wall-clock time
matters), `--reload-existing --append`. Started ~10:27 UTC.

**Phase durations** (this run is a long alphabetical sweep through sources;
timings below are what was actually observed, not estimates, unless marked):

- Per-source delete (existing content wipe before reload): fast, not a
  bottleneck — ran through all ~47 sources in the first few minutes.
- Preparse pass (raw pypath records → cached shard files on disk, one source
  at a time, `--stage-jobs 1`): the dominant cost so far.
  - `chembl.molecules`: **3211s (~53.5 min)** end to end — this includes
    downloading `chembl_36_sqlite.tar.gz` (5.23 GB) from the upstream EBI
    mirror at a bursty, throttled ~700 kB/s–6.5 MB/s (not a steady rate),
    producing 1,422,531 rows across 29 shards.
  - `chembl.activities` (same download, separate dataset): 3,459,458 rows
    across 70 shards, exported as 50k-row Parquet chunks — the single largest
    dataset preparsed so far.
  - `chembl.mechanisms`: 7,568 rows, fast.
  - `intact.interactions`: `human.zip` (1.09 GB) download alone took ~9 min at
    a similarly throttled rate; 1,240,727 rows.
  - `stitch.mouse_interactions`: still building as of the last check (350,000+
    rows across 6+ shards, 330s+ elapsed for this one dataset) — noticeably
    slower than `stitch.human_interactions` (338,521 rows, fast).
  - By contrast, the acceptance-relevant chemical sources preparsed quickly
    once reached: `chebi.molecules` 216,787 rows, `kegg.reactions` 87,381 rows,
    `reactome` (reactions 14,953 / pathways 2,883 / controls 10,080 /
    control_groups 1,130), `rhea` and `pfocr` — all sub-minute.
  - Preparse pass finished entirely at 12:26 UTC (~1h59m elapsed): 103
    (source, dataset) pairs preparsed across all ~47 sources, alphabetically
    `bindingdb` → `wikipathways`.
- Stage/canonicalize/copy pass (the real chemical-resolution pipeline —
  project → canonicalize → COPY to Postgres, one dataset/shard at a time,
  50k-row shards): began 12:26 UTC, much faster throughput than preparse.
  `task_done` reached 40 within 5 minutes of starting (vs. 95 datasets over
  ~2h for preparse) — most individual shard canonicalize times are single-digit
  seconds. `chebi.molecules` (216,787 rows, the acceptance-critical source for
  T040/T041/d879ba9) staged and canonicalized cleanly across 5 shards, 2–16s
  canonicalize each, **no BinderException** — the resolver-candidate fix is
  holding under real, uncapped data. All 7 acceptance-critical sources
  (chebi/hmdb/reactome/kegg/rhea/pfocr/tcdb/intact) finished staging clean,
  no failures, no `BinderException` anywhere in the run.

**Finished** 13:55 UTC, **total 3h27m39s** (12,464.7s). Final summary:
`sources=45 datasets=99 failed_sources=4 failed_datasets=5 source_rows=9,901,436
identifiers=63,468,308 annotations=91,865,478`. 5 failed datasets:
`drugcentral.interactions` (disk — see the follow-up entry below),
`bindingdb.interactions` (`BadZipFile`), `metatlas.metabolites`
(`ArrowTypeError`), `mirbase.precursors` (`IndexError`), `ptfi.foods` (no JSON
files found) — the last four are pre-existing, unrelated to this cycle.

**Outcome**: ran `contracts/coverage-acceptance.sql`'s first four blocks —
**none pass yet**: SC-001 51,832 (target ≤23,000), SC-002/003 Reactome 63.1%
(target ≥80%; `intact`/`pfocr`/`rhea` did clear their own ≥60% bar), SC-004
shared 493 (target ≥1,300), SC-005 unresolved 11,424 (target ≤5,500). All four
show large real improvement over baseline (SC-004 alone is 123x baseline), just
short of target. See the follow-up entry below for the investigation into why,
and two more real bugs found along the way.

---

## 2026-09-08 — T047 clean rebuild, after a full `reset-content` wipe

**Reason**: suspected the 51,832/63.1%/493/11,424 numbers above were inflated
by orphaned entities left over from this sandbox's many prior test reloads
across this whole cycle (24,187 of the 51,832 "ChEBI-canonical" entities have
zero `entity_evidence_resolution` rows referencing them). Wiped all content
tables (`make reset-content`, not just per-source deletes) and reran the exact
same full uncapped reload to test whether a genuinely clean database gives
different numbers.

**What actually happened**: `reset-content` itself was broken —
`CONTENT_TABLES` (the list it truncates) was missing 6 tables carrying foreign
keys into it, so Postgres refused the TRUNCATE. Fixed all 6 in two commits
(`main@bf34258` `interaction_fact_resource`; `main@36544a9` five more found by
querying `information_schema` systematically rather than fixing one crash at a
time: `data_source_license`, `entity_annotation_relation_default`,
`identifier_authority`, `identifier_role`, `resource_overlap_summary`). Also
had to `pg_terminate_backend` a zombie diagnostic query that had outlived my
own `pkill` by over an hour and was holding a lock blocking the truncate.

**Parameters**: identical to the run above —
`make reload DATABASE_URL=... OMNIPATH_BUILD_UTILS_PG_URL=...`, all sources,
uncapped, `--stage-jobs 1` — preceded by a verified-clean `reset-content`
(`entity` count confirmed 0 before launch). Started ~15:45 UTC.

**Phase durations**: dramatically faster — preparse was **instant** (reused
the shard cache from the run above, since only Postgres content was wiped,
not the on-disk preparse cache). `task_done` reached 97 within 23 minutes
(vs. ~2h for the first run to even start real staging). `foodb.foods` was
again the standout slow dataset (16.8min projection this time, vs. 6min in
the first run — 992 rows exploding into 4.66M identifiers, a huge fan-out,
not a bug). All 7 acceptance-critical sources confirmed clean again,
including `tcdb`.

**Finished** 17:38 UTC, **total 1h54m41s** (6,881.5s) — 45% faster than the
first run thanks to cache reuse. `sources=45 datasets=100 failed_sources=3
failed_datasets=4 source_rows=9,920,814 identifiers=63,614,139
annotations=92,029,431`. Only 4 failed datasets this time — `drugcentral`
succeeded (the first run's failure was transient shared-host disk pressure,
not a real bug); the same 4 pre-existing unrelated bugs (bindingdb, metatlas,
mirbase, ptfi) reproduced identically.

**Outcome — the wipe theory was wrong, but led to the real bug.** Re-ran the
acceptance gate: **identical numbers** to the contaminated run (51,832 /
63.1% / 493 / 11,424), down to the exact same orphaned entity UUID
(`1f9f0a48-1292-d05d-6c0b-894283589c84`, CHEBI:107644) reappearing byte for
byte after a complete wipe. Entity ids are content-addressed hashes, so this
proved the orphans are a **reproducible pipeline bug, not stale data**.

Root-caused it: `duckdb_load.py`'s `annotation_object_entity` CTE (inside
`batch_entity_candidate`) self-types an annotation-relation's object by its
raw `(object_id_type, object_id)` verbatim — no resolver join, no InChIKey
promotion, and only CV-term objects are excluded. A chemical annotation
object (e.g. a reaction annotated with a bare ChEBI id, not a full
`entity_evidence` mention) mints its own dead-end entity, permanently
separate from whatever properly-resolved InChIKey entity the same real
molecule gets via the normal mention pipeline. Documented in code
(`main@95ee298`, comment only — **not yet pushed**, GitHub token expired
mid-session, needs a sandbox credential refresh) and in memory as a follow-up,
not fixed this cycle per an explicit decision to defer it.

Separately, investigated the Reactome 63.1% shortfall directly: legitimate,
not a bug — 269 of a 270-id sample of stuck-at-ChEBI entities are either
R-group-wildcard generic templates or have no structural data in ChEBI at
all. Matches the spec's own control that ChEBI class-nodes must never gain a
fabricated structure.

**Infra note**: this session's `/workspace/instances/chemres1` (a CephFS
mount, separate from the container's local `/scratch`-backed overlay) hit
genuine zero-bytes-available for about an hour (14:13-15:25 UTC) mid-wipe —
corrupted `uv.lock`, blocked all writes including `git checkout`. Self-cleared;
nothing fixable from inside the sandbox if it recurs, just poll and wait.

T048+ (WP2, candidate arbitration) now looks clearly necessary to actually
clear SC-001/002/003/004/005's targets, not just a nice-to-have — the
unresolved counts are in the right range to match WP2's already-documented
candidate-ambiguity problem.

## 2026-10-01: main alignment setup and authorized old-schema cleanup

The user authorized using nicesrv and removing only the previous approximately 190 GiB full migration build to make room for alignment with main. No Parquet outputs, resolution reference or cache were changed. A guarded drop removed `full_20260930` from private PostgreSQL16 at loopback5440; both prior pilot schemas and all four production/prototype containers remained intact. The schema occupied206,661,582,848bytes (192.47GiB). Free filesystem space rose to562,186,121,216bytes. A schema-only DDL backup and copied release/build/sample reports are retained in `/root/projects/omnipath-migration/main-alignment-20261001`; cleanup receipt records four matching protected container IDs/start times.

A separate PostgreSQL18 image was built from main9f9bb709c764's Dockerfile, imageSHA256e261f1bed70e7d1001653fa69875e8ce2ea79ed2ef2b99834fffd77d26c32201. Isolated container`omnipath-migration-main-postgres` binds only127.0.0.1:5441 with separate volume`omnipath-migration-main-postgres-data`, memory8GiB, CPU3, shared_buffers1GB, work_mem32MB, maintenance_work_mem512MB and two parallel maintenance workers. Existing privatePG16 and protected services were retained.

Read-only scalar audit of the unchanged pinned46-resource release2026.9.30.4 scanned6,383,473published entity rows. It found no missing canonical type/namespace/identifier fields;2,188keys differed only taxonomy, of which2,181contain one known taxon plus the prototype's empty-string unknown marker and only7contain multiple actual known taxa.39natural-key conflict groups are distinct source-scoped name fallback identities. The adapter retains published identity and original occurrences, selects sole known taxonomy where unambiguous and NULL on actual conflict, and uses explicit published fallback identity only for colliding source-scoped names. No upstream recanonicalization occurred.

Development checks are ongoing. The first private main-oracle test run passed20non-DB checks but two setup tests failed because the migration pypath dependency lacks main's LIPID_NAME CV enum. The oracle now uses the exact CV package from main's pinned pypath33f37fbaab59993d24f5c32bb4b3e7587085bcf4; the migration dependency is untouched. This is a bounded development fixture run, not a full resource build or mandatory post-build gate. Full load has not started.


## 2026-10-01: bounded main-compatible PostgreSQL pilots

All loads below use the isolated PostgreSQL18 instance at loopback5441, unchanged synthetic serving Parquets, DuckDB1thread/512MB, source fixture with3entities/1statement/2evidence entries, and a new destination schema. No resource parser, resolver or real resource rebuild ran. Each base used all20 canonical/evidence/provenance COPY tables with late main keys, FKs and indexes.

First base pilot `aligned_pilot_20261001` succeeded: artifact checks0.003178s, DuckDB projection0.380812s, CSV staging0.019629s, COPY0.011752s, constraints0.125269s and base post-copy0.236685s. Its first shared finish failed at build-manifest cost serialization after successful earlier shared steps; main's exact cost-shaping helpers corrected the type mismatch. PostgreSQL-only finish succeeded without repeated COPY: shared derivations0.241692s, MetSigDB0.012112s, network presets0.005879s, COSMOS0.019649s. The tiny fixture has no eligible MetSigDB/reaction product rows; it tests orchestration rather than product science.

Second pilot `aligned_pilot2_20261001` succeeded through base: artifact0.002926s, projection0.363939s, CSV0.015982s, COPY0.009198s, constraints0.099499s, base post-copy0.194888s. Its shared derive failed when the bitmap extension was found installed under the first pilot schema. The isolated extension was explicitly relocated to public with dependent objects preserved; code now installs extensions in public and reports mismatched existing namespaces without implicit relocation. PostgreSQL-only finish retained the copied base: shared derivations0.244538s, MetSigDB0.011740s, network presets0.005744s, COSMOS0.023541s. All three complete products, subset_build_metadata and parquet_phase committed with the pinned pilot release. A separately selected network product rebuild through the public subsets entrypoint succeeded in0.005278s, with callback after durable commit and other products retained.

Focused local regression suite:393passed/326skipped in5.06s; server-only tests are skipped locally by explicit opt-in. Guarded isolated PostgreSQL fixtures:110passed in7.78s, including exact main catalogue definitions and late indexes/statistics, source-specific nullable/sign facts, merged reactions/transport/stoichiometry, quantities/publications, ontology shortest closure/labels, source-local miRBase maturation, full COSMOS row/statistics parity, protein labels, ChEBI display identity, two-schema bitmap types and actual product checkpoint durability. The server fixture override targets only loopback5441/databaseomnipath_migration and creates disposable random schemas. This is bounded development verification; no mandatory full-release verification/rebuild/rollback/safety phase was reinstated.

Reports are retained under `/root/projects/omnipath-migration/main-alignment-20261001`:pilot-main-base.json,pilot-main-finished.json,pilot2-main-finished.json,pilot2-selected-product.json. Exact main reference9f9bb709c764 and pypath CV dependency33f37fbaab59 are pinned independently from the migration pypath. Genuine unavailable original inputs are declared in build_manifest/build_capability and the column map.

Final frozen main-oracle rerun: 15 passed in 3.27s, including BIGINT source IDs and executed view/function definitions. Ruff and patch whitespace checks passed.

## 2026-10-01: full main-compatible release load started

Runtime commit `d0a1c80c33699353a589e06b957ad49960f01ef2` is pushed on `parquet-migration`. A615-file source snapshot was verified byte-for-byte on nicesrv; its manifest SHA256 is66c69d10b3d4113fd9e00ba1e102443788af970b08d0886eba362dce0fbdd4b2. Copied main SQL retains two inherited whitespace warnings; the implementation's Ruff checks passed.

The full build started at2026-10-01T20:25:13.932388+00:00, using unchanged release2026.9.30.4 (46resources, canonical SHA25606f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e) in new schema`aligned_full_20261001`, isolated mainPG18 at loopback5441. Parameters: DuckDB2threads/2GB, on-disk projection and CSV staging, late main keys/FKs/indexes, durable base and complete-product commits. Systemd unit`omnipath-migration-main-aligned-20261001.service` caps the builder at5GiB/2CPU and runs atNice10. No mandatory verification, rollback-rebuild or safety job is queued.

Preflight confirmed all615runtime files, mainPG18 image/loopback binding, public extension namespaces, no competing active private backend, both prior pilot schemas in privatePG16 and all four running protected services. Free disk560,904,417,280bytes. Initial monitor at20:27UTC showed healthy DuckDBvalidate_and_stage, matching release pins and no committed base yet. Memory was approximately2.34GBanonymous plus reclaimable file cache; zero OOM events. The existing15-minute check is active for this new run and reports meaningful changes only. Full timings, product results and final storage are pending.

## 2026-10-01: full attempt1 diagnosis and taxonomy validation correction

The first aligned full run stopped beforeCOPY at20:34:50UTC after576.513767s. No base, product or COPY checkpoint was committed; the fresh schema contains only setup/seed metadata (approximately1.5MiB). Its reports/events/log/error and source manifest are archived privately under main-alignment-20261001/attempt1-d0a1c80.

Read-only scalar audit of18,339,902published statement rows confirmed exactly35shared statement keys differ only in scalar taxonomy, all betweenChEMBL andDrugCentral. Endpoints, predicate, statement kind, direction, sign, category and interaction class agree. The publisher computes taxonomy as a resource-specific consensus and excludes it from the statement key. The projection check now allows only that legitimate taxonomy variation while preserving both source-owned statements/evidence/taxon annotations; true identity and all other existing consistency checks remain. No Parquet was changed and no arbitrary biological value was selected. The bounded aggregate diagnostic completed in31.611060s after restricting its nine-field detail comparison to the35already identified keys; earlier512MiB/1GiB diagnostic attempts exceeded working memory.

Five new projection regressions verify source-owned taxonomy/evidence and reject changed subject/object/predicate/kind. Focused projection/loader/MetSigDB tests:69passed,6opt-in server tests skipped in3.08s. The new exact-main MetSigDB oracle passed all3tests on private5441 in2.59s:20synthetic source records cover allfive complete memberships/provenance, productDDL/indexes, idempotence and source-local stale-row removal. The failed empty schema will be preserved under an attempt-specific name; restart uses a fresh target and cannot reuseCOPY because none ran.


## 2026-10-01: corrected full attempt2 running overnight

The failed attempt1 schema was confirmed empty of copied scientific tables and renamed to `aligned_full_20261001_attempt1_empty`; its setup metadata and private archived diagnostics are retained. No COPY or committed base existed to resume.

Runtime `bbe8ea40f90f60d5a5bfaad0c99de2baac856fc5` was pushed and deployed as a verified 616-file snapshot, manifest SHA256 `9ccb45f39eb83dd577ef55b508a2ff300a398d685733a2001611a2edec841060`. The corrected full load started at 2026-10-01T20:53:47.904529+00:00 in `omnipath-migration-main-aligned-attempt2-20261001.service`, using a fresh `aligned_full_20261001` schema. The unchanged 46-resource release, private PostgreSQL18 at loopback5441, DuckDB2threads/2GB, builder5GiB/2CPU/Nice10, late main keys/FKs/indexes and durable base/product checkpoints remain as recorded above. Preflight free disk was557,452,517,376bytes.

At21:03:07UTC the run had passed shared-statement validation and reached DuckDB dimensions; no base had committed yet. Free disk538,289,545,216bytes. The user requested overnight progress; the existing15-minute monitor was updated to this single attempt, with authorization to diagnose clear failures, test narrow corrections and preserve completed checkpoints. No mandatory exhaustive verification, rebuild/rollback or safety job is queued. Completion, final phase durations, representative samples and storage remain pending.


## 2026-10-01: attempt2 memory diagnosis and bounded dimension preparation

Attempt2 was deliberately stopped at2026-10-01T21:13:08.295055+00:00 after1160.390526s, still in DuckDB dimensions and beforeCOPY. It had passed the corrected statement identity validation. No base/product checkpoint or scientific COPY rows existed; the setup-only schema is retained as `aligned_full_20261001_attempt2_empty`. Reports/log/source receipt are archived privately under `main-alignment-20261001/attempt2-bbe8ea4-dimension-memory`. Fresh checks confirmed zero remaining builder backends, both original pilots and all four protected services unchanged.

The dimension collector fetched every entity type, predicate, category and annotation scope occurrence before deduplicating in Python. At diagnosis the process used approximately4.9GBanonymous memory under its5GiB service limit, with no OOM kills but heavy memory and disk pressure. The SQL now selects distinct non-null names before fetching. The existing sorted vocabulary allocation, seeded IDs, dataset pairs, scientific projection and published Parquets are unchanged.

A bounded10,000-row-per-input fixture compares exact prior/new dimension and dataset outputs and reduces transferred names from35,006to14. Subagent focused tests used a disposable local PostgreSQL16 cluster: `OMNIPATH_TEST_POSTGRES=1 uv run pytest -q packages/omnipath_postgres/tests/test_aligned_projection.py packages/omnipath_postgres/tests/test_aligned_loader.py packages/omnipath_postgres/tests/test_duckdb_projection.py packages/omnipath_subsets/tests/test_main_adapter.py`;129passed in4.70s, Ruff clean. Root reviewed the patch and its seeded-ID/full-value parity comparator. The next full retry keeps the same pinned release and checkpoint parameters; no mandatory verification or rebuild/rollback job is introduced.


## 2026-10-01: full attempt3 started with bounded dimensions

Corrected runtime `2942d38385c1319f41a4ee1d5559c65604fb0893` is pushed on `parquet-migration`. Its616committed source files were verified on nicesrv; runtime manifest SHA256 `be1269009cf961a20a1bd9b11dfe628e6420ef85edc024d53cba5e94d408fedc`. The single `omnipath-migration-main-aligned-attempt3-20261001.service` started at2026-10-01T21:19:23.787442+00:00 in fresh `aligned_full_20261001`, same unchanged release2026.9.30.4/46resources/canonical SHA25606f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e, privatePG18loopback5441, DuckDB2threads/2GB, builder5GiB/2CPU/Nice10 and durable base/complete-product commits. Preflight free disk538,289,389,568bytes; both prior pilots, public extension placement, private image/loopback and protected services were checked.

Initial progress at21:19:46UTC: validate_and_stage, healthy active unit, matching release pins and no base yet. The existing15-minute heartbeat now follows attempt3 and retains prior failure/cancellation history. A separate bounded read-only manual sample helper is prepared for completion; no mandatory verifier/rebuild/rollback/safety phase was added. Full phase times, actual product results, final sample and storage remain pending.


## 2026-10-01: attempt3 memory limit and equivalent entity aggregate

Attempt3 failed beforeCOPY at2026-10-01T21:29:32.914704+00:00 after609.127313s with DuckDBOutOfMemoryException in the entities phase (1.8GiB/1.8GiB pool used). Dimension preparation completed in3.421316s, confirming the SQL deduplication fix. The exact failing SELECT was not captured, so no more specific attribution is claimed. No base/product checkpoint or scientific COPY rows existed. The setup-only schema is retained as `aligned_full_20261001_attempt3_empty`; diagnostics/reports are archived under `main-alignment-20261001/attempt3-2942d38-duckdb-memory`. All four protected service IDs/start times matched their baseline.

The sole-known taxonomy aggregate now usesMIN=MAX rather thanCOUNT(DISTINCT)=1; both ignore NULL and yield NULL for zero or multiple distinct knownBIGINT values. A20-occurrence comparator executes the entire old/new entity projection and compares complete rows in eight entity/identifier tables/views, including empty, invalid and overlapping source cases; upstream invalid-taxonomy validation is unchanged.25focused tests passed in3.01s. This eliminates unnecessary distinct state in that aggregate without changing any Parquet, identity, main schema or scientific result.

Independent coverage was completed for structured measurement identities, actual Parquet-to-COPY ontology terms/axioms and frozen-main term/shortest-closure outputs, plus exact main preset registry descriptors/DDL/defaults/upserts. Main presets register metadata only; resource/organism/license filtering and folds remain the separate API consumer's responsibility, outside this build oracle. The pinned release already usesconnectomedb2025 andmetatlas. Root ran all seven new checks with `OMNIPATH_TEST_POSTGRES=1 uv run --frozen --no-sync pytest -q packages/omnipath_postgres/tests/test_main_network_presets_parity.py packages/omnipath_postgres/tests/test_main_parquet_semantics.py packages/omnipath_postgres/tests/test_aligned_entity_memory.py`:7passed in2.38s on disposable local PostgreSQL16; Ruff clean.

Next retry is configured for DuckDB1thread/4GB and a7GiB builder cap, retaining the same2CPU/Nice10, exact pinned release and durable base/product checkpoints. The standalone helper now records its own hash and private traceback filenames/functions/line numbers for precise future failure diagnosis. No mandatory verification/rebuild/rollback phase was added; full completion and actual full-scale memory/storage/timings remain pending.


## 2026-10-01: full attempt4 started with revised memory settings

Full attempt4 started at2026-10-01T21:43:49.757124+00:00 as `omnipath-migration-main-aligned-attempt4-20261001.service`. Runtime commit `5703acf50630e8c1716b0b183c0510087200b66f` is pushed;742committed source files (including the completed independent fixtures and workspace package files) were verified, manifest SHA256 `74abecfd60313583ff2414cac4ddfc201cee26e22feb70ed769c3807de15bd4c`. Standalone run helper SHA256 `44e773253b3bc7db11e2a2e56408edc2398396c95dd594bc301ac583862a8146`.

Parameters: unchanged release2026.9.30.4/46resources/canonical SHA25606f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e; fresh `aligned_full_20261001` in isolated mainPG18loopback5441; DuckDB1thread/4GB, on-disk staging, builderMemoryMax7GiB/CPU2/Nice10; exact-main late keys/FKs/indexes and complete base/product checkpoints. Admission confirmed previous unit/backend terminal, fresh target, source hashes, private image/loopback, public extensions, both original pilots and protected services. Free disk538,278,174,720bytes; hostMemAvailable12,998,672KiB. Initial21:45UTCprogress wasvalidate_and_stage, active unit, matching pins and no base yet.

The existing15-minute overnight heartbeat now follows only attempt4. No mandatory verification/rebuild/rollback/safety phase is queued. Prior failed/cancelled empty schemas and diagnostics are preserved. Full outcome, timings, representative samples and final storage remain pending.


## 2026-10-01 — Main alignment full attempt4: annotation CTAS memory failure

- Runtime: `5703acf50630e8c1716b0b183c0510087200b66f`; unit `omnipath-migration-main-aligned-attempt4-20261001.service`; private PostgreSQL18 at loopback5441; release `2026.9.30.4`, manifest `06f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e`. DuckDB1thread/4GB, builder7GiB, base/product checkpoints enabled, no mandatory verifier.
- Started `2026-10-01T21:43:49.757124+00:00`; failed `2026-10-01T22:39:50.912637+00:00`; wall **3361.156325s**. Canonical/entity/statement/dictionary preparation passed. Prepared staged links included42,991,511 entity identifiers,42,607,573 source evidence identifiers,8,562,132 entity annotation links and22,887,251 relation evidence rows. These were not PostgreSQL COPY results.
- Exact failed CTAS: `relation_evidence_annotation`, `prepare_aligned_release` line705. DuckDB OOM requested1GiB at3.4/3.7GiB used. No COPY began and no checkpoint was committed, so a new load is required; there is no copied base to repeat or lose.
- Archived reports/logs/runtime manifest under `/root/projects/omnipath-migration/main-alignment-20261001/attempt4-5703acf-annotation-memory`; run report SHA256 `bb02c438281d7bcff0b20ef366132ff0f6559ab8ad6a1f637dc756fd3312c27a`. Proven-empty schema retained as `aligned_full_20261001_attempt4_empty`. Both original pilots, both completed aligned pilots and all four protected service IDs/start times were confirmed intact. Temporary projection/spill cleanup restored free disk to538,317,664,256bytes.
- Next implementation: narrow primitive ownership inputs clustered by complete-owner shard; preserve existing source/dataset/scope and evidence-versus-statement rules. Independent full-row old-query regression and review must finish before launching the next attempt. No Parquet, reference, cache, resolver or main scientific algorithm changes.


## 2026-10-01 — Bounded owner-aware annotation preparation fix

- Two narrow DuckDB link inputs carry only primitive resource/version/owner, evidence ordinal/UUID, annotation UUID, raw source/dataset, normalized scope and typed/synthetic flags. They are physically ordered by a stored 128-group owner hash; each group stays complete and filter predicates target the stored group column. Values, quantities, arrays and upstream record fields stay out of the ownership joins.
- Separate evidence-owned and surviving statement-owned paths preserve the frozen query's exact null-safe suppression, raw wildcard matching, scope normalization, synthetic evidence and final four-column DISTINCT. The staged result is reused for counts/COPY. No main schema/index/scientific algorithm or source artifact changed.
- Independent full-row/decoded-expectation fixture: 13 passed in3.77s; combined projection/quantity suite:38 passed in8.58s over nine synthetic records. Loader/checkpoint/product adapter suite:60 passed in1.48s, including real DuckDB CSV and disposable PostgreSQL checkpoint visibility. Ruff passed; independent review found no remaining row-semantic issue. Progress field `event` initially collided with real loader `_emit`; renamed to `state` and tested through that exact callback.
- Exact full-release memory behavior is pending the next load. An exceptionally large complete owner cannot be split without a separate reviewed semantic approach; the fix does not claim a fixed bound independent of owner skew.


## 2026-10-01 — Main alignment full attempt5 launched

- Runtime `afd4ccb9b8f722d5dee885d567a7f378a9a1e88e`, reviewed 128-group narrow annotation-link fix; unit `omnipath-migration-main-aligned-attempt5-20261001.service`, invocation `279b6708d46c450d98ef3d105f4c5ac9`. Started **2026-10-01T22:58:29.704298+00:00**.
- Fresh schema `aligned_full_20261001` on private PostgreSQL18 loopback5441; unchanged46-resource release2026.9.30.4, manifest `06f55112b371055b9b539c27eb7886d403b0131098393d1bafe4723ce006c03e`. No artifact, cache, reference or resolver changes.
- Verified743committed runtime files, source manifest SHA256 `796cfb701cc383eecb42fc0cd5ab688a01849d510b0f3e23067c923890311c19`; archive SHA256 `31f88ba48b6adb2b21b73a2a3a72d0332fb4c21bd5e4fb6b7f3520815be61354`. Helper SHA256 `44e773253b3bc7db11e2a2e56408edc2398396c95dd594bc301ac583862a8146`.
- DuckDB1thread/4GB; builderMemoryMax7GiB/CPUQuota200%/Nice10. Base and complete-product checkpoints enabled; no mandatory verifier/rebuild/rollback job. Preflight free538,317,246,464bytes, hostMemAvailable12,989,360KiB; exact protected service IDs/start times and both original/completed aligned pilot pairs preserved.
- At22:58:44UTC authoritative service state was active/running in validate_and_stage. Actual completion/phase times/storage and representative manual samples remain pending. Monitor target and15-minute heartbeat updated to this unit/revision.
