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
  holding under real, uncapped data. Still working through `chembl.molecules`
  as of 12:31 UTC; `reactome`/`hmdb`/`kegg`/`rhea`/`pfocr`/`tcdb`/`intact`
  not yet reached in this phase (staging also runs alphabetically). This
  section will be updated with copy/derive timings and the acceptance-critical
  sources' canonicalize times once reached.

**Outcome**: not yet known — run in progress. Update this entry (or add a
follow-up dated entry) with the final phase timings and the
`coverage-acceptance.sql` first-four-blocks result once it finishes.
