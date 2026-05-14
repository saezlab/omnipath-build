# Minimal Direct-to-Postgres Plan

## Scope

Build a separate prototype under `minimal/` which streams pre-parsed
`inputs_v2` rows directly into source-scoped PostgreSQL evidence tables.

This prototype keeps source evidence separate from graph materialization.
General entity population comes from scoped evidence identifiers. Resolver
candidates create resolved entities; unresolved-but-supported evidence creates
stable identifier-set entities.

## Phase 1: Source-Scoped Evidence Ingest

1. Define minimal PostgreSQL DDL for append/deduplicated source evidence:
   - `source_row(source, dataset, row_id, snapshot_id, processed_at)`
   - `identifier(type, value)`
   - `entity_evidence(source, dataset, row_id, entity_type, taxonomy_id)`
   - `entity_evidence_identifier(entity_evidence_id, identifier_id)`
   - `relation_evidence(source, dataset, row_id, subject_entity_evidence_id, predicate, object_entity_evidence_id, relation_category)`
   - `annotation(term, value, unit, scope, entity_evidence_id, relation_evidence_id)`

2. Add natural unique constraints so every ingest operation can use
   `INSERT ... ON CONFLICT DO NOTHING`.

3. Materialize minimal preparse snapshots with provenance:
   - keep local raw snapshot code under `minimal/ingest/preparse.py`
   - store snapshots by source and dataset, e.g.
     `data/uniprot/<dataset>/<snapshot_id>/`
   - use `_raw_record_id` as stable source row ID

4. Track source rows in `source_row` for lineage only:
   - insert source/dataset/row ID/snapshot metadata
   - mark rows processed only after all evidence rows are written
   - do not implement row-hash gating in this phase

5. Flatten mapped `Entity` objects directly into evidence tables:
   - identifiers deduplicate globally in `identifier`
   - entity annotations attach to `annotation(scope='entity')`
   - membership and interaction structures produce `relation_evidence`
   - relation, subject, object, and evidence attributes attach through
     `annotation(scope=...)`

## Phase 2: Scoped Entity Resolution

Entity resolution is implemented as a separate pass:

- `entity_resolution_candidate`: every accepted resolver candidate for an
  evidence entity, kept as audit/debug data.
- `entity_evidence_resolution`: one status row per evidence entity.
- `entity`: one general entity table for resolved and unresolved supported
  evidence. Resolved rows use CV-formatted canonical identifier types such as
  `MI:1097:Uniprot` and `MI:1101:Standard Inchi Key`; unresolved rows use
  `evidence_identifier_set` hashes.
- `relation`: one general relation table pointing at `entity`, without its own
  resolved/unresolved status.
- `relation_evidence_relation`: mapping table from source relation evidence to
  deduplicated general relations.

Candidate multiplicity still decides resolution. Multiple identifiers that
agree on the same UniProt or standard InChIKey resolve cleanly; identifiers
that point at different values remain explicit ambiguous evidence while mapping
to an unresolved fallback entity. Standard InChI remains linked through the
identifier table for chemical evidence. Evidence with zero candidates but a
supported entity type is grouped by `entity_type`, `taxonomy_id`, and sorted
identifier set, so repeated unresolved mentions collapse to one `entity` row.

The executable pass is:

```bash
uv run python -m minimal.cli canonicalize --source intact --dataset interactions
```

Minimal resolver mappings are materialized independently from the shared
`id_resolver` package:

```bash
uv run python -m minimal.cli build-resolver uniprot chebi hmdb lipidmaps swisslipids pubchem
uv run python -m minimal.cli load-resolver
```

By default, `pubchem` discovers all current full-SDF shards from the NCBI
directory listing and streams PubChem CID, Standard InChIKey, and Standard InChI
into the minimal chemical resolver parquet. For development, pass
`--pubchem-url` with a single `.sdf.gz` URL/path.

Resolver row acceptance is controlled by `resolver_mapping_policy`. The default
policy accepts UniProt accession identity/secondary mappings without taxonomy,
requires taxonomy for protein reference mappings, and accepts the currently
materialized chemical resolver sources.

The incremental ingest boundary has two layers:

- raw snapshot/preparse compares stable raw rows (`row_id`, row hash) and
  produces the raw delta used for normal ingest and stale-row deletion
- input mapper/parser changes can alter parsed entity evidence without changing
  raw row hashes, so `--refresh` discards existing evidence for the selected
  source dataset and reloads all current rows from the latest raw snapshot

## Make Orchestration

One-time database setup is explicit:

```bash
make db-setup
```

This creates the minimal schema, loads resolver mappings, and creates
secondary indexes.

The source pipeline has four explicit phases:

```bash
make preparse sources=uniprot
make ingest sources=uniprot
make canonicalize sources=uniprot
make derive
```

`preparse` materializes raw source snapshots and computes the raw delta.
`ingest` maps the latest accepted preparse snapshot into minimal evidence
tables. `canonicalize` resolves scoped evidence into entity and relation graph
tables. `derive` refreshes derived tables and bitmaps.

For normal source loading, use the convenience target:

```bash
make load sources=uniprot
```

This runs `preparse`, `ingest`, and `canonicalize`, but does not run `derive`.
That keeps multi-source loads cheap. Run `make derive` once after all selected
sources are loaded when the schema should be query-ready.

The default ingest path remains incremental: source-row synchronization removes
stale rows and entity datasets ingest only changed rows from the snapshot
delta.

When source input mappers or parsers changed and raw row hashes are not enough
to identify affected evidence, refresh the selected source explicitly:

```bash
make load sources=uniprot REFRESH=1
```

`REFRESH=1` is an ingest mode. It keeps the latest preparse snapshot as the raw
truth, deletes existing minimal evidence for each selected source dataset, and
maps all current rows from that snapshot again. Use this when parser or mapper
changes alter parsed evidence even though raw row hashes did not change.

When bootstrapping minimal tables from an existing raw snapshot, opt into full
current-row ingest explicitly:

```bash
make load sources=uniprot BOOTSTRAP=1
```

## Minimal Deliverables

1. `minimal/schema.py` for DDL.
2. `minimal/ingest.py` for preparse-backed streaming ingest.
3. `minimal/cli.py` with `init-db`, `load-resolver`, `preparse`, `ingest`,
   `canonicalize`, and `derive` commands.
4. Focused tests for idempotent DDL, preparse-backed ingest, and insert
   deduplication.
