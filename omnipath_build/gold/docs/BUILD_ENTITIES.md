# `build_entities.py`

## Overview

`build_entities.py` is the **first script** in the Option B3 two-pass gold pipeline. It reads per-source silver parquet files, extracts every entity description (parents, members, ontology objects), canonicalizes identifiers, deduplicates by canonical ID, assigns final contiguous 1-indexed primary keys, and writes three output artifacts.

It replaces the first two steps of the old three-step pipeline (`projector.py` entity extraction + `canonicalize_projector.py` + `dedup_projector.py` entity dedup) with a single, self-contained pass.

## CLI

```bash
uv run python omnipath_build/gold/build_entities.py \
  --silver-dir    data_v2/silver/corum/1 \
  --output-dir    /tmp/corum/entities \
  --source-name   corum \
  --mapping-dir   id_resolver/data
```

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--silver-dir` | yes | — | Directory containing `.parquet` silver files (excludes `resource.parquet`) |
| `--output-dir` | yes | — | Directory to write `entity.parquet`, `entity_map.parquet`, and optionally `ontology_term.parquet` |
| `--source-name` | yes | — | Source name used for metadata, reporting, and the `sources` column |
| `--mapping-dir` | no | `id_resolver/data` | Root directory of resolver mapping files (proteins, chemicals, etc.) |

## High-level pipeline

```
silver parquet files
       |
       v
+------------------+
| 1. Extract all   |  <-- entity_extraction.py (streaming, row-wise Python)
|    entities +    |
|    ontology terms|
+------------------+
       |
       v
+------------------+
| 2. Pre-dedup by  |  <-- Python dict (fingerprint -> first occurrence)
|    fingerprint   |      Reduces resolver input by 3–12×
+------------------+
       |
       v
+------------------+
| 3. Build temp    |  <-- Polars DataFrames with 1-indexed temp PKs
|    DataFrames    |
+------------------+
       |
       v
+------------------+
| 4. Canonicalize  |  <-- reuses canonicalize_projector.py logic
|    identifiers   |
+------------------+
       |
       v
+------------------+
| 5. Dedup by      |  <-- reuses dedup_projector.py _reduce_entities
|    canonical ID  |
+------------------+
       |
       v
+------------------+
| 6. Build         |  <-- Polars joins
|    fingerprint   |      temp PK -> canonical ID -> final PK
|    -> final PK   |
|    map           |
+------------------+
       |
       v
   entity.parquet
   entity_map.parquet
   ontology_term.parquet  (optional)
   canonicalization_report.md
   canonicalization_summary.json
```

---

## Phase 1: Entity extraction

Entry point: `extract_from_silver_tables()` in `silver_entity_extraction.py`

### What is extracted

For every occurrence in the silver tables, the script classifies the occurrence and extracts:

1. **Parent entity** — for all rows except `ignored`, `ontology_term_only`, and `interaction_relation`
2. **Ontology term rows** — for `ontology_term_only` rows and `entity_with_ontology_backing` rows
3. **Ontology object entities** — from parent annotations that are pure ontology term annotations
4. **Member entities** — recursively from every `membership.member` sub-row

### Fingerprinting

Each extracted entity gets a stable 32-char fingerprint:

```python
compute_entity_fingerprint(entity_type, identifiers)
# SHA-256 of JSON({"type": entity_type, "ids": sorted((type, value) tuples)})
```

This fingerprint is the **bridge** between the entity script and the relation script. It is independent of ordering, so two silver rows describing the same protein with the same identifiers (but in different order) will produce the same fingerprint.

### Streaming

Extraction is streaming via `pyarrow.parquet.ParquetFile.iter_batches()`. Memory usage is bounded by batch size (default 10k rows), not total source size.

---

## Phase 2: Pre-dedup by fingerprint

Before building resolver input, entities are deduplicated by their stable fingerprint. This is a **performance optimization** that reduces resolver workload by 3–12× depending on source duplication.

### Why this is safe

Two entities with the same fingerprint have **identical type and identifiers**. They are indistinguishable to the resolver. The only difference between occurrences is their position in the silver file, which does not affect resolution.

### Merge rule

The **first occurrence** is kept; subsequent duplicates are dropped. `entity_attributes` from the first occurrence are preserved. The final canonicalization dedup (`_reduce_entities`) also keeps the first non-null attributes, so the end result is semantically identical.

### Occurrence tracking

A `fingerprint_occurrences` dict counts how many times each fingerprint appeared. This is reported as `entity_occurrences` vs `unique_fingerprints` in the summary.

### Example impact

| Source | Occurrences | Unique fingerprints | Reduction |
|--------|-------------|---------------------|-----------|
| corum | 22,955 | 7,575 | 3.0× |
| guidetopharma | 400 | 309 | 1.3× |
| uniprot | 759,230 | 65,400 | 11.6× |

---

## Phase 3: Build temporary DataFrames

Two Polars DataFrames are built from the **deduplicated** list:

### `temp_entities`

| Column | Type | Description |
|--------|------|-------------|
| `entity_pk` | `int64` | Temporary 1-indexed PK |
| `_fingerprint` | `string` | Stable fingerprint |
| `entity_type` | `string` | CV-term-formatted entity type |
| `taxonomy_id` | `string | null` | NCBI taxonomy ID |
| `entity_attributes` | `list<struct{term,value,unit}>` | Record-level attributes |
| `sources` | `list<string>` | Source name list |

### `temp_identifiers`

| Column | Type | Description |
|--------|------|-------------|
| `entity_pk` | `int64` | FK to temp_entities |
| `identifier` | `string` | Raw identifier value |
| `identifier_type` | `string` | CV-term-formatted identifier type |
| `source` | `string` | Source name |

---

## Phase 4: Canonicalization

Entry point: `_canonicalize_entities()`

This phase reuses the core logic from `canonicalize_projector.py`, adapted to work in-memory on the temporary DataFrames instead of reading/writing parquet files.

### Step 3a: Separate ontology entities

Ontology entities (`entity_type == OM:0012:Cv Term`) are routed around the resolver entirely. Their canonical identifier is taken directly from their `CV_TERM_ACCESSION` identifier.

### Step 3b: Build resolver input

For all non-ontology entities whose type is in `TARGET_ENTITY_TYPES` (proteins, chemicals, etc.), the script joins `temp_identifiers` with the eligible entity list to produce:

```
entity_pk | entity_type | taxonomy_id | id          | id_type
----------|-------------|-------------|-------------|--------
42        | MI:0326:Protein | 9606    | P12345      | MI:1097:Uniprot
42        | MI:0326:Protein | 9606    | ENSG000001  | MI:0349:Ensembl
```

### Step 3c: Resolve identifiers

`resolve_identifier_frame()` (from `id_resolver`) maps each `(id, id_type)` pair to a resolved backbone:

- **Proteins** → primary UniProt
- **Chemicals** → Standard InChI

It returns resolution status (`identity`, `mapped`, `unresolved`) and the resolved backbone.

### Step 3d: Repair protein resolutions

`_repair_protein_resolutions()` handles edge cases the resolver misses:

1. Direct primary UniProt match
2. Isoform stripped to primary
3. Secondary → primary mapping
4. Scoped protein reference lookup (same taxonomy)
5. Global protein reference lookup

This is the same 5-tier fallback logic used in the old canonicalizer.

### Step 3e: Detect ambiguous entities

An entity is **ambiguous** if its resolver-supported identifiers map to **more than one distinct backbone**. For example:

- Ensembl → UniProt `P12345`
- RefSeq → UniProt `Q67890`

These conflict. The entity is left unresolved and will receive a fallback ID.

For chemicals, conflicts are further classified:
- **Exact conflict** — different Standard InChI keys entirely
- **Near conflict** — same key after stripping stereo/protonation layers

### Step 3f: Select preferred backbones

The script groups resolved rows by `entity_pk` and keeps the entity only if **all** its resolved evidence agrees on exactly one backbone:

```python
# Proteins
preferred_uniprots = resolvable
    .filter(entity_type in PROTEIN_ENTITY_TYPES)
    .filter(resolved_id_type == UNIPROT_TYPE)
    .group_by(entity_pk)
    .agg(n_unique(resolved_id) == 1)

# Chemicals
preferred_inchis = resolvable
    .filter(entity_type in CHEMICAL_ENTITY_TYPES)
    .filter(resolved_id_type == STANDARD_INCHI_TYPE)
    .group_by(entity_pk)
    .agg(n_unique(resolved_id) == 1)
```

### Step 3g: Expand authoritative identifiers

For each preferred backbone, the script expands to the full authoritative identifier set:

- **Proteins**: primary UniProt + secondary UniProts + cross-references (Ensembl, RefSeq, HGNC, etc.) from `protein_reference_to_uniprot.parquet`
- **Chemicals**: Standard InChI + all mapped keys from chemical mapping files

### Step 3h: Build canonical rows

Canonical rows map each `entity_pk` to its preferred `(canonical_identifier, canonical_identifier_type)`. If no preferred backbone exists, the entity will get a fallback ID later.

### Step 3i: Build export keys

`_entity_export_keys()` joins every entity with its canonical row. If no canonical row exists, it generates a fallback:

```
entity_id     = f"{source_name}:entity:{local_entity_pk}"
entity_id_type = "omnipath:local_entity"
```

### Step 3j: Build identifier table

The final identifier table has five sources of rows:

1. **Source identifiers** — original raw identifiers from silver, tagged with `source:{source}`
2. **Resolver identifiers** — authoritative expanded identifiers, tagged with `resolver:canonicalization` or specific resolver source
3. **Fallback identifiers** — for unresolved entities, the fallback ID itself marked as canonical
4. **Canonical flag** — `is_canonical = True` for the preferred backbone identifier
5. **Aggregation** — grouped by `(entity_id, entity_id_type, identifier, identifier_type)`, keeping any canonical flag and unique source markers

### Step 3k: Update entity table

The entity table is updated with `entity_id` and `entity_id_type` (the export keys), replacing the temporary PK. The `_fingerprint` column is preserved for the next phase.

### Output of Phase 3

- `canonicalized_entities` — DataFrame with `entity_id`, `entity_id_type`, plus original columns
- `canonical_identifiers` — DataFrame with aggregated identifier rows
- `summary` — dict with counts (entities_seen, resolved_entities, ambiguous_entities, etc.)
- `ambiguous_entities` — list of dicts for conflict reporting

---

## Phase 5: Deduplicate by canonical ID

Entry point: `_reduce_entities()` (reused from `dedup_projector.py`)

### What it does

Entities that resolved to the **same canonical ID** (e.g., a protein seen in 73 different complexes) are merged into one row.

### Merge rules

| Column | Rule |
|--------|------|
| `entity_type` | `drop_nulls().first()` |
| `entity_attributes` | `drop_nulls().first()` |
| `taxonomy_id` | `drop_nulls().first()` |
| `sources` | `explode().drop_nulls().unique().sort()` |

### Canonical identifier selection

From the identifier table, the row with `is_canonical == True` is selected, sorted by `(entity_id_type, entity_id, identifier_type, identifier)`, and the first is taken as `canonical_identifier` / `canonical_identifier_type`.

### Non-canonical identifiers

All other identifiers for that entity are folded into a `list<struct{identifier, identifier_type}>` column.

### PK reassignment

After dedup, entities are sorted by `(entity_id_type, entity_id)` and reassigned contiguous 1-indexed PKs via `with_row_index('entity_pk', offset=1)`.

### Output

- `final_entities` — deduped entity DataFrame with final PKs
- `entity_key_map` — DataFrame mapping `(entity_id, entity_id_type)` → `entity_pk`

---

## Phase 6: Build fingerprint → final PK map

This is the **critical bridge** to `build_relations.py`.

```
temp_entities (temp_pk + fingerprint)
    |
    v
canonicalized_entities (temp_pk -> entity_id + entity_id_type)
    |
    v
entity_key_map (entity_id + entity_id_type -> final_pk)
    |
    v
fingerprint_map (fingerprint -> final_pk)
```

The join chain ensures that:
- Every fingerprint from the original extraction is mapped
- If two fingerprints resolved to the same canonical entity, they both map to the **same final PK**
- The relation script never needs to run the resolver or know about canonical IDs

---

## Phase 7: Write outputs

### `entity.parquet`

Final deduped entity table. Schema matches the old pipeline exactly:

```
entity_pk               int64
canonical_identifier    string
canonical_identifier_type string
identifiers             list<struct{identifier: string, identifier_type: string}>
entity_type             string
taxonomy_id             string
entity_attributes       list<struct{term: string, value: string, unit: string}>
sources                 list<string>
```

### `entity_map.parquet`

Bridge file consumed by `build_relations.py`:

```
_fingerprint    string
entity_pk       int64
```

### `ontology_term.parquet` (optional)

Only written if ontology term rows were extracted:

```
term_id          string
ontology_prefix  string
label            string
definition       string
synonyms         list<string>
source           string
```

### `canonicalization_report.md` + `canonicalization_summary.json`

Human-readable and machine-readable canonicalization reports, including conflict details.

---

## Why this design

| Concern | How it's handled |
|---------|-----------------|
| **Correctness** | Resolver sees all evidence per entity before dedup; no premature collapsing |
| **Memory** | Streaming extraction; only entity descriptions in memory, not relations |
| **Speed** | Polars for all columnar work (canonicalization, dedup, joins) |
| **Maintainability** | Reuses proven canonicalization/dedup logic from old pipeline |
| **Testability** | Each phase is independently inspectable via DataFrame |
| **Bridge to relations** | Fingerprint map means relations are built once with final PKs |

## Relationship to old pipeline

| Old step | New equivalent |
|----------|---------------|
| `projector.py` entity extraction | Phase 1 (extraction) + Phase 2 (pre-dedup) |
| `canonicalize_projector.py` | Phase 4 (same logic, in-memory, fewer rows) |
| `dedup_projector.py` entity dedup | Phase 5 (same `_reduce_entities`) |
| `projector.py` relation building | **Not here** — moved to `build_relations.py` |

The key improvement: **relations are never built, rewritten, or remapped**. They are constructed once in `build_relations.py` using the final PKs from this script.
