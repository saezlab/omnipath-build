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
`BATCH_SIZE=50000`, `THREADS=4`. Started ~09:10 UTC. Only Postgres content
was wiped (`reset-content`), not the on-disk preparse shard cache, so
preparse should reuse the cache from the 2026-09-08 runs and be fast, same
as the second T047 run below.

**Phase durations / outcome**: TBD, filling in once the run finishes.

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
