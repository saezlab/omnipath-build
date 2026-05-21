# DuckDB Direct Pipeline

The `omnipath_build` pipeline builds PostgreSQL tables from `pypath.inputs_v2`
datasets through DuckDB. Source records are projected into DuckDB evidence
tables, resolved into canonical entities and relations there, then copied into
PostgreSQL for serving and derived query artifacts.

## Data Model

The pipeline has three layers:

- Source evidence tables keep the raw source, dataset, row ID, entity
  occurrence, identifiers, relation evidence, and annotations.
- Canonical graph tables collapse equivalent evidence into deduplicated
  `entity` and `relation` rows.
- Derived query tables and bitmaps summarize canonical graph content for
  search, filtering, counts, and resource metadata.

Evidence IDs are deterministic UUIDs built from source, dataset, row ID, and
occurrence paths. Re-ingesting the same source snapshot therefore produces
stable evidence keys, while PostgreSQL uniqueness constraints deduplicate
shared identifiers, annotations, entities, and relations.

## Phases

### 1. Resolver Materialization

Resolver mappings translate evidence identifiers into canonical identifiers.
Protein mappings resolve to primary UniProt accessions and keep taxonomy for
species-scoped resolution. Chemical mappings resolve ChEBI, ChEMBL, HMDB,
LipidMaps, SwissLipids, and PubChem identifiers to standard InChI keys.

Build parquet resolver files:

```bash
make resolver
```

Create the PostgreSQL schema:

```bash
make db-setup
```

With `DROP_EXISTING=1`, secondary evidence indexes are deferred so full scratch
loads do not maintain them row by row. To drop and recreate the target schema,
run:

```bash
make db-reset
```

### 2. DuckDB Direct Load

Load discovers compatible `inputs_v2` entity and ontology datasets, skips
sources already present by default, and streams selected records through DuckDB.
DuckDB projection produces source-scoped evidence tables, canonicalizes entity
and relation rows, then copies the projected rows into PostgreSQL.

```bash
make load SOURCE=bindingdb
make load SOURCES=uniprot,bindingdb,intact
make load
```

Entity datasets are flattened by the shared evidence projector and written to
DuckDB by `DuckDBEvidenceProjector`. Interaction-like records with exactly two
members become relation evidence. Other member trees produce parent/member
entity evidence and membership relation evidence. Ontology-valued entity
annotations are also projected as relation evidence to CV-term entities.

Ontology datasets bypass ordinary evidence ingest because each term already has
a stable accession. They are projected as resolved CV-term entities with
annotation metadata and ontology relationship relations.

### 3. Canonicalization

Canonicalization is part of `make load`; it no longer runs through a separate
PostgreSQL implementation.

For each evidence entity, canonicalization groups equivalent occurrences by
entity type, taxonomy, and identifier set. Resolver candidates are ranked by
identifier strength. Direct identifiers such as UniProt and standard InChI key
win over stable cross-references, which win over weak names. Groups with one
best target become resolved; groups with conflicting best targets become
ambiguous; groups without accepted candidates receive unresolved fallback
entities.

After entity resolution, relation canonicalization replaces evidence endpoints
with canonical entity IDs and upserts deduplicated graph relations.

### 4. Derivation

Derived tables are rebuilt after selected sources have been ingested and
canonicalized:

```bash
make derive
```

This phase creates deferred indexes when needed, refreshes relation-count and
ontology-term summary tables, rebuilds roaring bitmap tables, and syncs the
resource summary table from discovered pypath resource metadata plus current
database content.

## Common Workflows

Build resolver files, recreate the schema, load all sources through the DuckDB
direct pipeline, and derive query tables:

```bash
make all DROP_EXISTING=1
```

Use existing resolver files and load all missing content:

```bash
make db-setup DROP_EXISTING=1
make load
make derive
```

Refresh one source and leave derived tables for a later batch refresh:

```bash
make reload SOURCE=bindingdb
```

Run a bounded smoke test in a separate schema:

```bash
make load SOURCE=bindingdb MAX_RECORDS=200000 SCHEMA=omnipath_test
```

## Refresh Semantics

`make load` is additive by default. When a selected source already has
source-scoped content in the target schema, it is skipped and left untouched.
Use `make reload` when current parser output should replace existing content.
Reload deletes existing evidence for that source first, then streams the source
again. Canonical relations and entities that become unreachable after deleting
that source are garbage-collected; graph rows still supported by other sources
are preserved.

`make load` and `make reload` run projection and canonicalization only. Run
`make derive` once after the selected source batch is complete when the database
should be query-ready.

## Main Options

- `DATABASE_URL`: PostgreSQL connection URL.
- `SCHEMA`: target schema, default `public`.
- `SOURCE`: one source module name.
- `SOURCES`: comma-separated source module names.
- `MAX_RECORDS`: per-dataset cap for smoke tests.
- `BATCH_SIZE`: source rows per DuckDB direct load batch.
- `DROP_EXISTING`: recreate schema during `db-setup`.
- `DERIVE`: run `derive` after `make pipeline`.
- `PUBCHEM_URL`: use one PubChem SDF `.gz` shard during resolver development.

## Maintenance

Drop and recreate the target schema:

```bash
make db-reset
```
