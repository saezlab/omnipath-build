# OmniPath build pipeline: traced dummy source

This note traces one realistic-but-small source through the pipeline, showing both **what each stage does** and **how the rows change**. The source is fictional (`toy_interactions`) but shaped like a typical interaction dataset.

> Scope: start at **download**, then **bronze**, **silver**, **gold**, and **combined/publish**. Hashes are deterministic examples from the dummy payload.

## Dummy source

Source module: `pypath.inputs_v2.toy_interactions`

Dataset: `interactions`

Downloaded file: `toy_interactions.tsv`

```tsv
source	target	effect	pmid	source_database
TP53	MDM2	inhibits	12345678	ToyPath
EGFR	GRB2	binds	23456789	ToyPath
```

---

## 1. Download

### What happens

The input module resolves the remote URL or cache location, downloads the raw file if needed, and records a download fingerprint. In the real code this is usually mediated by pypath/download cache behavior; cache hits avoid network work.

The important output for later incremental checks is a fingerprint of the downloaded content and parser contract.

### Output metadata

```json
{
  "source": "toy_interactions",
  "dataset": "interactions",
  "download_path": "pypath-data/toy_interactions/toy_interactions.tsv",
  "download_fingerprint": {
    "algorithm": "sha256",
    "sha256": "23e0c7ea35798a9e518977ad31b8abd2a6fdc7f9462cbdc96306a17a8f720d3c",
    "bytes": 110
  },
  "parser_contract": {
    "parser": "toy_interactions.interactions",
    "version": "parser_dict_columns_v1",
    "columns": ["source_gene", "target_gene", "effect", "pmid", "source_database"]
  }
}
```

If the download fingerprint and parser contract are unchanged from the previous accepted bronze snapshot, bronze can create an empty-delta snapshot without reparsing all rows.

---

## 2. Bronze: preparse raw rows and content-address them

### What happens

`pypath.inputs_v2.raw_records.materialize_raw_records()` writes parser-emitted dictionaries to parquet under `data/bronze/<source>/<dataset>/<snapshot_id>/`.

For each parsed row it:

1. Cleans reserved metadata column names.
2. Computes `_raw_record_key` using BLAKE2b over canonical JSON-like bytes of the row content.
3. Writes raw parser output to `records.parquet`.
4. Reuses old `_raw_record_id` values when the same `_raw_record_key` appears in the previous snapshot.
5. Computes `delta.parquet` with added/removed raw-record keys.
6. Writes `manifest.json` and, when accepted, updates `latest.json`.

### Parser output before bronze metadata

```json
[
  {
    "source_gene": "TP53",
    "target_gene": "MDM2",
    "effect": "inhibits",
    "pmid": "12345678",
    "source_database": "ToyPath"
  },
  {
    "source_gene": "EGFR",
    "target_gene": "GRB2",
    "effect": "binds",
    "pmid": "23456789",
    "source_database": "ToyPath"
  }
]
```

### `records.parquet` after bronze

| _source | _dataset | _raw_record_id | _raw_record_key | source_gene | target_gene | effect | pmid | source_database |
|---|---|---:|---|---|---|---|---|---|
| toy_interactions | interactions | 1 | `af22ac999edd2e4dbe7c36189ae57043b6147f81925b2a7ec93987cb210b13a9` | TP53 | MDM2 | inhibits | 12345678 | ToyPath |
| toy_interactions | interactions | 2 | `ad3e2d8c22e19adb260fef021ea0e8d48f9b572fa4d59ddb2c57d559e6783676` | EGFR | GRB2 | binds | 23456789 | ToyPath |

### `delta.parquet` on first build

| _raw_record_key | _raw_record_id | _change_type |
|---|---:|---|
| `af22ac999edd2e4dbe7c36189ae57043b6147f81925b2a7ec93987cb210b13a9` | 1 | added |
| `ad3e2d8c22e19adb260fef021ea0e8d48f9b572fa4d59ddb2c57d559e6783676` | 2 | added |

### `manifest.json` excerpt

```json
{
  "source": "toy_interactions",
  "dataset": "interactions",
  "snapshot_id": "20260512T120000000000Z",
  "previous_snapshot_id": null,
  "preparse_version": "parser_dict_columns_v1",
  "records_path": "data/bronze/toy_interactions/interactions/20260512T120000000000Z/records.parquet",
  "delta_path": "data/bronze/toy_interactions/interactions/20260512T120000000000Z/delta.parquet",
  "rows": 2,
  "min_raw_record_id": 1,
  "max_raw_record_id": 2,
  "distinct_raw_record_ids": 2,
  "delta_keys_by_type": {"added": 2, "removed": 0}
}
```

---

## 3. Silver: convert raw records into canonical source tables

### What happens

`build_silver_source()` calls `run_silver_loader()`, which discovers resource/dataset functions and streams records into canonical silver parquet tables using `SilverTableWriter`.

For this interaction dataset, each raw row becomes:

- one parent interaction occurrence,
- two member protein occurrences,
- identifiers for each protein,
- annotations for publication/effect/source metadata,
- membership rows linking the interaction occurrence to its participant occurrences.

Silver preserves raw lineage on every emitted row:

- `_raw_record_id`
- `_raw_record_key`
- `_snapshot_id`

This lineage drives incremental silver and gold diffs.

### `entity_occurrence.parquet`

| occurrence_id | record_id | parent_occurrence_id | entity_role | entity_type | source | dataset | _raw_record_id | _raw_record_key |
|---|---:|---|---|---|---|---|---:|---|
| interactions:1:parent | 1 | null | parent | interaction | toy_interactions | interactions | 1 | `af22ac...b13a9` |
| interactions:1:member:1 | 1 | interactions:1:parent | member | protein | toy_interactions | interactions | 1 | `af22ac...b13a9` |
| interactions:1:member:2 | 1 | interactions:1:parent | member | protein | toy_interactions | interactions | 1 | `af22ac...b13a9` |
| interactions:2:parent | 2 | null | parent | interaction | toy_interactions | interactions | 2 | `ad3e2d...83676` |
| interactions:2:member:1 | 2 | interactions:2:parent | member | protein | toy_interactions | interactions | 2 | `ad3e2d...83676` |
| interactions:2:member:2 | 2 | interactions:2:parent | member | protein | toy_interactions | interactions | 2 | `ad3e2d...83676` |

### `entity_identifier.parquet`

| occurrence_id | identifier_type | identifier | source | dataset | _raw_record_id |
|---|---|---|---|---|---:|
| interactions:1:member:1 | genesymbol | TP53 | toy_interactions | interactions | 1 |
| interactions:1:member:2 | genesymbol | MDM2 | toy_interactions | interactions | 1 |
| interactions:2:member:1 | genesymbol | EGFR | toy_interactions | interactions | 2 |
| interactions:2:member:2 | genesymbol | GRB2 | toy_interactions | interactions | 2 |

### `membership.parquet`

| membership_id | parent_occurrence_id | member_occurrence_id | membership_role | source | dataset | _raw_record_id |
|---|---|---|---|---|---|---:|
| interactions:membership:1 | interactions:1:parent | interactions:1:member:1 | source | toy_interactions | interactions | 1 |
| interactions:membership:2 | interactions:1:parent | interactions:1:member:2 | target | toy_interactions | interactions | 1 |
| interactions:membership:3 | interactions:2:parent | interactions:2:member:1 | source | toy_interactions | interactions | 2 |
| interactions:membership:4 | interactions:2:parent | interactions:2:member:2 | target | toy_interactions | interactions | 2 |

### `entity_annotation.parquet`

| occurrence_id | term | value | source | dataset | _raw_record_id |
|---|---|---|---|---|---:|
| interactions:1:parent | effect | inhibits | toy_interactions | interactions | 1 |
| interactions:1:parent | pubmed | 12345678 | toy_interactions | interactions | 1 |
| interactions:1:parent | source_database | ToyPath | toy_interactions | interactions | 1 |
| interactions:2:parent | effect | binds | toy_interactions | interactions | 2 |
| interactions:2:parent | pubmed | 23456789 | toy_interactions | interactions | 2 |
| interactions:2:parent | source_database | ToyPath | toy_interactions | interactions | 2 |

### Silver snapshot output

```text
data/silver/toy_interactions/1/
  entity_occurrence.parquet
  entity_identifier.parquet
  entity_annotation.parquet
  membership.parquet
  membership_annotation.parquet
  delta/
  manifest.json
  inputs_module_hash.json

data/silver/toy_interactions/state/
  entity_occurrence.parquet
  ...

data/silver/toy_interactions/latest
```

The silver manifest records row counts and, on incremental-compatible sources, per-table added/removed lineage deltas.

---

## 4. Resolver mappings

### What happens

Before gold, resolver mapping tables are built or reused. They map local identifiers to canonical identifiers.

Example mapping rows used by this dummy source:

| identifier_type | identifier | canonical_identifier_type | canonical_identifier | taxonomy_id |
|---|---|---|---|---|
| genesymbol | TP53 | uniprot | P04637 | 9606 |
| genesymbol | MDM2 | uniprot | Q00987 | 9606 |
| genesymbol | EGFR | uniprot | P00533 | 9606 |
| genesymbol | GRB2 | uniprot | P62993 | 9606 |

---

## 5. Gold entities: canonicalize and deduplicate source entities

### What happens

`build_entities()` reads the silver tables and:

1. Extracts entity occurrences.
2. Computes a stable occurrence fingerprint from entity type + identifiers.
3. Pre-deduplicates identical fingerprints.
4. Canonicalizes identifiers using resolver mappings.
5. Computes stable `entity_key = sha256(canonical_identifier | canonical_identifier_type | taxonomy_id)`.
6. Reduces duplicate evidence into one canonical entity row.
7. Writes evidence and maps.

### Extracted entity evidence before canonicalization

| occurrence_id | fingerprint | entity_type | identifier_type | identifier | raw_record_id |
|---|---|---|---|---|---:|
| interactions:1:member:1 | `8683836665f3db431c1318688c612515` | protein | genesymbol | TP53 | 1 |
| interactions:1:member:2 | `3fdbb9485a3f20e835c0bc70f2e57f5c` | protein | genesymbol | MDM2 | 1 |
| interactions:2:member:1 | `d78d3c066ebdfb945998cb04abc8e928` | protein | genesymbol | EGFR | 2 |
| interactions:2:member:2 | `1927441edc5245913f39a0ca97f03d4a` | protein | genesymbol | GRB2 | 2 |

### `entity_evidence.parquet` after canonicalization

| source | occurrence_id | entity_key | canonical_identifier_type | canonical_identifier | raw_record_id |
|---|---|---|---|---|---:|
| toy_interactions | interactions:1:member:1 | `66e8dc657d3a...a41a9a2` | uniprot | P04637 | 1 |
| toy_interactions | interactions:1:member:2 | `2c421d96821b...cc853b` | uniprot | Q00987 | 1 |
| toy_interactions | interactions:2:member:1 | `cb12e2c902d9...c747a4` | uniprot | P00533 | 2 |
| toy_interactions | interactions:2:member:2 | `cb3c6d81fe0f...45f7b6` | uniprot | P62993 | 2 |

### `entity.parquet`

| entity_pk | entity_key | entity_type | canonical_identifier_type | canonical_identifier | taxonomy_id | sources |
|---:|---|---|---|---|---|---|
| 1 | `66e8dc657d3a...a41a9a2` | protein | uniprot | P04637 | 9606 | [toy_interactions] |
| 2 | `2c421d96821b...cc853b` | protein | uniprot | Q00987 | 9606 | [toy_interactions] |
| 3 | `cb12e2c902d9...c747a4` | protein | uniprot | P00533 | 9606 | [toy_interactions] |
| 4 | `cb3c6d81fe0f...45f7b6` | protein | uniprot | P62993 | 9606 | [toy_interactions] |

### `entity_occurrence_map.parquet`

| occurrence_id | _fingerprint | entity_pk |
|---|---|---:|
| interactions:1:member:1 | `8683836665f3db431c1318688c612515` | 1 |
| interactions:1:member:2 | `3fdbb9485a3f20e835c0bc70f2e57f5c` | 2 |
| interactions:2:member:1 | `d78d3c066ebdfb945998cb04abc8e928` | 3 |
| interactions:2:member:2 | `1927441edc5245913f39a0ca97f03d4a` | 4 |

---

## 6. Gold relations: project interactions using final entity keys

### What happens

`build_relations()` reads silver plus `entity_occurrence_map.parquet` / `entity_map.parquet` and:

1. Classifies rows as interaction, membership, annotation, or ignored.
2. Resolves source and target occurrence IDs to final entity PKs/keys.
3. Chooses a predicate from row type and effect annotations.
4. Computes `relation_key = sha256(subject_entity_key | predicate | object_entity_key | relation_category)`.
5. Writes deduplicated relation rows and evidence rows.

### `entity_relation_evidence.parquet`

| source | raw_record_id | subject_entity_key | predicate | object_entity_key | relation_category | relation_key | evidence |
|---|---:|---|---|---|---|---|---|
| toy_interactions | 1 | `66e8dc657d3a...a41a9a2` | negatively_regulates | `2c421d96821b...cc853b` | interaction | `c3321634a452...184b42` | PMID:12345678 |
| toy_interactions | 2 | `cb12e2c902d9...c747a4` | interacts_with | `cb3c6d81fe0f...45f7b6` | interaction | `3722a8936b6...67540d` | PMID:23456789 |

### `entity_relation.parquet`

| relation_pk | relation_key | subject_entity_pk | predicate | object_entity_pk | relation_category | sources |
|---:|---|---:|---|---:|---|---|
| 1 | `c3321634a452...184b42` | 1 | negatively_regulates | 2 | interaction | [toy_interactions] |
| 2 | `3722a8936b6...67540d` | 3 | interacts_with | 4 | interaction | [toy_interactions] |

### Per-source gold output

```text
data/gold/toy_interactions/
  entities/
    entity.parquet
    entity_evidence.parquet
    entity_map.parquet
    entity_occurrence_map.parquet
  relations/
    entity_relation.parquet
    entity_relation_evidence.parquet
  _delta/<build_id>/
    affected_entity_keys.parquet
    affected_relation_keys.parquet
    manifest.json
  _SUCCESS.json
  toy_interactions.zip
```

The gold delta scope is used by combine to update only affected keys when possible.

---

## 7. Combined layer: merge all gold sources

### What happens

`build_combined()` uses the DuckDB combine state store.

- If the state is empty, it bootstraps from all source-level gold outputs.
- If state exists and gold deltas provide affected keys, it updates only those entity and relation keys.

For this single dummy source, combined output initially mirrors the gold records but with cross-source merge semantics.

### `data/combined/latest/entities.parquet` excerpt

| entity_key | canonical_identifier | canonical_identifier_type | taxonomy_id | source_count | sources |
|---|---|---|---|---:|---|
| `66e8dc657d3a...a41a9a2` | P04637 | uniprot | 9606 | 1 | [toy_interactions] |
| `2c421d96821b...cc853b` | Q00987 | uniprot | 9606 | 1 | [toy_interactions] |
| `cb12e2c902d9...c747a4` | P00533 | uniprot | 9606 | 1 | [toy_interactions] |
| `cb3c6d81fe0f...45f7b6` | P62993 | uniprot | 9606 | 1 | [toy_interactions] |

### `data/combined/latest/relations.parquet` excerpt

| relation_key | subject_identifier | predicate | object_identifier | relation_category | source_count | sources |
|---|---|---|---|---|---:|---|
| `c3321634a452...184b42` | P04637 | negatively_regulates | Q00987 | interaction | 1 | [toy_interactions] |
| `3722a8936b6...67540d` | P00533 | interacts_with | P62993 | interaction | 1 | [toy_interactions] |

---

## 8. Optional Postgres load and run reports

### What happens

If a Postgres URI is configured, the combined layer is loaded to Postgres. On incremental runs, the loader can apply delta-oriented updates instead of a full bootstrap when the combine run is incremental and tables are not being dropped.

The pipeline also writes reports:

```text
data/reports/runs/<run_id>.json
data/reports/latest.json
data/reports/changelog.ndjson
```

Each report records selected sources, stages, task status, output dirs, reuse/executed/skipped state, and task metadata.

---

## End-to-end lineage summary

| Stage | Stable key produced/used | Purpose |
|---|---|---|
| Download | file `sha256` | Detect identical downloaded input |
| Bronze | `_raw_record_key` | Content-address a parsed raw row |
| Bronze | `_raw_record_id` | Stable compact row lineage ID reused across snapshots |
| Silver | `occurrence_id`, `membership_id` | Normalize raw records into canonical source tables |
| Silver | `_raw_record_key`, `_raw_record_id` | Carry raw lineage into every canonical row |
| Gold entities | entity fingerprint | Deduplicate equivalent entity descriptions before resolution |
| Gold entities | `entity_key` | Stable canonical entity business key |
| Gold relations | `relation_key` | Stable canonical relation business key |
| Combine | affected entity/relation keys | Targeted cross-source updates |
