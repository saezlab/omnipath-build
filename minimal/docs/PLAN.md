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

3. Read existing preparse snapshots through `inputs_v2` with provenance:
   - use `use_preparse=True`
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
  evidence. Resolved rows use resolver identities such as `uniprot_primary` or
  `standard_inchi`; unresolved rows use `evidence_identifier_set` hashes.
- `relation`: one general relation table pointing at `entity`, without its own
  resolved/unresolved status.
- `relation_evidence_relation`: mapping table from source relation evidence to
  deduplicated general relations.

Candidate multiplicity still decides resolution. Multiple identifiers that
agree on the same UniProt or standard InChI resolve cleanly; identifiers that
point at different values remain explicit ambiguous evidence while mapping to
an unresolved fallback entity. Evidence with zero candidates but a supported
entity type is grouped by `entity_type`, `taxonomy_id`, and sorted identifier
set, so repeated unresolved mentions collapse to one `entity` row.

The executable pass is:

```bash
uv run python -m minimal.cli canonicalize --source intact --dataset interactions
```

Resolver row acceptance is controlled by `resolver_mapping_policy`. The default
policy accepts UniProt accession identity/secondary mappings without taxonomy,
requires taxonomy for protein reference mappings, and accepts the currently
materialized chemical resolver sources.

Row-hash-based gating can be added later on top of the existing preparse
metadata once the minimal ingest path is working.

## Make Orchestration

One-time database setup is explicit:

```bash
make minimal_pipeline_setup
```

This creates the minimal schema, loads resolver mappings, and creates
secondary indexes.

The normal source pipeline is:

```bash
make minimal_pipeline SOURCES=uniprot
```

This ingests each source, canonicalizes each source, and refreshes derived
tables and bitmaps. The default ingest path remains incremental: source-row
synchronization removes stale rows and entity datasets ingest only changed rows
from the snapshot delta.

When bootstrapping minimal tables from an existing raw snapshot, opt into full
current-row ingest explicitly:

```bash
make minimal_pipeline SOURCES=uniprot BOOTSTRAP=1
```

## Minimal Deliverables

1. `minimal/schema.py` for DDL.
2. `minimal/ingest.py` for preparse-backed streaming ingest.
3. `minimal/cli.py` with `init-db`, `load-resolver`, `ingest`, and
   `canonicalize` commands.
4. Focused tests for idempotent DDL, preparse-backed ingest, and insert
   deduplication.
