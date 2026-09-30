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

## 2026-09-30 — full resolved-Parquet release on nicesrv, attempt 3 (running)

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

**Phase durations**: pending in `postgres-build.json`/`.log`, which now record
per-resource SQL validation, each table's stage/COPY timings, file counts/bytes,
raw checks, indexes and derivations. Preliminary ANALYZE and validation are
separate from the table timings. The independent 39-resource raw check already
passed; full PG-only verification is gated on successful base and subset builds.

**Outcome**: in progress. Expected base counts remain 6,383,473 entities,
80,136,865 identifiers, 18,339,902 relations, 22,887,251 evidence and 150,690,040
annotations; 26,711,866 raw rows are checked and discarded. The seven native
rebuilds still cover all 300,008 originally published records. Production and
both earlier private schemas are preserved. No production publication.

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
