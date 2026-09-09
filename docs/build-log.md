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
