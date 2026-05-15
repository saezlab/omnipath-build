# Minimal Direct-to-Postgres Plan

## Scope

Build a separate prototype under `minimal/` which streams `inputs_v2` rows
directly into source-scoped PostgreSQL evidence tables.

This prototype keeps source evidence separate from graph materialization.
General entity population comes from scoped evidence identifiers. Resolver
candidates create resolved entities; unresolved-but-supported evidence creates
stable identifier-set entities.

## Phase 1: Source-Scoped Evidence Ingest

1. Define minimal PostgreSQL DDL for refresh-loaded source evidence:
   - `identifier(type, value)`
   - `entity_evidence(source, dataset, row_id, entity_type, taxonomy_id)`
   - `entity_evidence_identifier(entity_evidence_id, identifier_id)`
   - `relation_evidence(source, dataset, row_id, subject_entity_evidence_id, predicate, object_entity_evidence_id, relation_category)`
   - `annotation(term, value, unit, scope, entity_evidence_id, relation_evidence_id)`

2. Add natural unique constraints so every ingest operation can use
   `INSERT ... ON CONFLICT DO NOTHING`.

3. Refresh selected sources as the only write mode:
   - delete existing source-scoped evidence and orphaned graph rows once per
     selected source
   - stream parser and mapper output directly into the ingest backend
   - use the run-local row index as `row_id`

4. Flatten mapped `Entity` objects directly into evidence tables:
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
- `entity`: one general entity table for resolved and unresolved typed
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
non-empty entity type is grouped by `entity_type`, `taxonomy_id`, and sorted
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

Incremental raw-row deltas are intentionally out of scope. Re-running a source
is a refresh: existing evidence for that source is removed, then current parser
output is streamed again.

## Make Orchestration

One-time database setup is explicit:

```bash
make db-setup
```

This creates the public schema tables, loads resolver mappings, and creates
secondary indexes for non-scratch setup.

For scratch loads, pass `MINIMAL_DROP_EXISTING=1`; schema setup defers
secondary evidence indexes until after ingest so bulk loading does not maintain
them row by row.

The source pipeline has three explicit phases:

```bash
make ingest sources=uniprot
make canonicalize sources=uniprot
make derive
```

Omit `SOURCE`/`SOURCES` to discover and run every raw-backed minimal source:

```bash
make minimal_pipeline
```

`ingest` refreshes source evidence by streaming current parser output into
minimal evidence tables. `canonicalize` resolves scoped evidence into entity
and relation graph tables, creating deferred evidence indexes first if they are
missing. `derive` refreshes derived tables, bitmaps, and query-oriented indexes.

For normal source loading, use the convenience target:

```bash
make load sources=uniprot
```

This runs `ingest` and `canonicalize`, but does not run `derive`. That keeps
multi-source loads cheap. Canonicalization creates any deferred evidence indexes
before resolving, so scratch loads get fast ingest and indexed canonicalization.
Run `make derive` once after all selected sources are loaded when the schema
should be query-ready.

## Minimal Deliverables

1. `minimal/schema.py` for DDL.
2. `minimal/ingest.py` for direct streaming ingest.
3. `minimal/cli.py` with `init-db`, `load-resolver`, `ingest`,
   `canonicalize`, and `derive` commands.
4. Focused tests for idempotent DDL, refresh ingest, and insert deduplication.
