# Direct-to-Postgres Pipeline

The `omnipath_build` pipeline builds PostgreSQL tables directly from
`pypath.inputs_v2` datasets. It keeps source evidence separate from the
canonical graph so sources can be refreshed independently, then resolved and
summarized in later phases.

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

Load resolver tables into PostgreSQL:

```bash
make db-setup
```

`db-setup` also creates the schema. With `DROP_EXISTING=1`, secondary evidence
indexes are deferred so full scratch ingest does not maintain them row by row.

### 2. Source Evidence Ingest

Ingest discovers compatible `inputs_v2` entity and ontology datasets, deletes
existing content for each selected source, creates source partitions, and
streams current raw records into PostgreSQL.

```bash
make ingest SOURCE=bindingdb
make ingest SOURCES=uniprot,bindingdb,intact
make ingest
```

Entity datasets are flattened through `BulkIngestor`. Interaction-like records
with exactly two members become relation evidence. Other member trees produce
parent/member entity evidence and membership relation evidence. Ontology-valued
entity annotations are also projected as relation evidence to CV-term entities.

Ontology datasets bypass ordinary evidence ingest because each term already has
a stable accession. They are loaded directly as resolved CV-term entities with
annotation metadata and ontology relationship relations.

### 3. Canonicalization

Canonicalization resolves scoped evidence into graph entities and relations:

```bash
make canonicalize SOURCE=bindingdb
make canonicalize
```

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

Build resolver files, recreate the schema, load all sources, canonicalize, and
derive query tables:

```bash
make all DROP_EXISTING=1
```

Use existing resolver files and reload all content:

```bash
make db-setup DROP_EXISTING=1
make load
make derive
```

Refresh one source and leave derived tables for a later batch refresh:

```bash
make load SOURCE=bindingdb
```

Run a bounded smoke test in a separate schema:

```bash
make load SOURCE=bindingdb MAX_RECORDS=200000 SCHEMA=omnipath_test
```

## Refresh Semantics

Source ingest is refresh-based. When a source is selected, existing evidence for
that source is deleted before current parser output is streamed again. Canonical
relations and entities that become unreachable after deleting that source are
garbage-collected; graph rows still supported by other sources are preserved.

`make load` runs ingest and canonicalization only. Run `make derive` once after
the selected source batch is complete when the database should be query-ready.

## Main Options

- `DATABASE_URL`: PostgreSQL connection URL.
- `SCHEMA`: target schema, default `public`.
- `SOURCE`: one source module name.
- `SOURCES`: comma-separated source module names.
- `MAX_RECORDS`: per-dataset cap for smoke tests.
- `BATCH_SIZE`: source rows per COPY staging flush.
- `PROGRESS_EVERY`: progress print interval during ingest.
- `DROP_EXISTING`: recreate schema during `db-setup`.
- `DERIVE`: run `derive` after `make pipeline`.
- `PUBCHEM_URL`: use one PubChem SDF `.gz` shard during resolver development.
