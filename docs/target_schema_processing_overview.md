# Target schema processing overview

This document summarizes the current processing flow from per-source inputs to:

- per-source gold target-schema packages
- `id_resolver` mapping-table materialization
- preferred-canonical ID normalization
- global identifier aggregation

## Current recommended CLI

Use the consolidated pipeline CLI:

```bash
uv run python scripts/target_schema_pipeline.py source <source...> [--silver-test-mode] [--with-global]
uv run python scripts/target_schema_pipeline.py mappings
uv run python scripts/target_schema_pipeline.py global [<source...>]
uv run python scripts/target_schema_pipeline.py all <source...> [--silver-test-mode]
```

Make targets are also available:

```bash
make target-schema-source SOURCES="signor reactome"
make target-schema-mappings
make target-schema-global SOURCES="signor reactome"
make target-schema-all SOURCES="signor reactome" TEST_MODE=1
```

Default directories:

- silver: `data_v2/silver/<source>/`
- per-source gold: `data_v2/gold/<source>/`
- mapping tables: `data_v2/gold/_mapping_tables/`
- global outputs: `data_v2/gold/_global/`

## Pipeline summary

### 1. Source inputs -> silver
Each source is first built into silver parquet files.

Relevant code:
- `omnipath_build/package_emitter/*`
- source definitions in `pypath/pypath/inputs_v2/*`

### 2. Silver -> per-source target schema
The silver data are converted into one target-schema package per source.

Script:
- `scripts/silver_to_target_schema.py`

Outputs per source under `data_v2/target_schema/<source>/`:
- `entities.parquet`
- `entity_identifiers.parquet`
- `interactions.parquet`
- `associations.parquet`
- `annotations.parquet`

This step also runs within-source deduplication via:
- `scripts/target_schema_entity_dedup.py`

### 3. Materialize resolver mapping tables
Authoritative resolver mapping tables are materialized with `id_resolver` for downstream normalization.

Command:
- `uv run python scripts/target_schema_pipeline.py mappings`

Outputs under `data_v2/gold/_mapping_tables/`:
- `proteins/protein_reference_to_uniprot.parquet`
- `proteins/uniprot_secondary_to_primary.parquet`
- `chemicals/chebi.parquet`
- `chemicals/hmdb.parquet`
- `chemicals/lipidmaps.parquet`
- `chemicals/swisslipids.parquet`

### 4. Normalize per-source target schema with `id_resolver`
Per-source entities are enriched with preferred canonical identifiers when uniquely resolvable.

This normalization now runs inside:
- `scripts/target_schema_pipeline.py source ...`
- `scripts/target_schema_pipeline.py all ...`

Preferred canonical identifiers:
- proteins -> `MI:1097:Uniprot`
- small molecules / lipids -> `MI:2010:Standard Inchi`

Current normalization behavior:
- unique mappings are applied automatically
- normalization runs after the existing per-source dedup step
- no second dedup pass is run yet

### 5. Build global identifier aggregation
After per-source normalization, identifiers are aggregated across sources into a global identifier table.

Script:
- `scripts/build_global_entity_identifiers.py`

Outputs under `data_v2/target_schema_global/`:
- `global_entity_identifiers.parquet`
- `source_entity_to_global_entity.parquet`

`global_entity_identifiers.parquet` columns:
- `global_entity_id`
- `identifier`
- `identifier_type`
- `taxonomy_id`
- `is_canonical`
- `sources`

Global entity identity is currently grouped by:
- canonical identifier
- canonical identifier type
- taxonomy ID

## Main scripts and purpose

### Convert silver to target schema
```bash
uv run python scripts/silver_to_target_schema.py <source> --output-root data_v2/target_schema
```

Example:
```bash
uv run python scripts/silver_to_target_schema.py signor --output-root data_v2/target_schema
```

### Materialize resolver mapping tables
```bash
uv run python scripts/target_schema_pipeline.py mappings
```

### Build and normalize one source
```bash
uv run python scripts/target_schema_pipeline.py source <source>
```

Example:
```bash
uv run python scripts/target_schema_pipeline.py source mebocost
```

### Build and normalize multiple sources
```bash
uv run python scripts/target_schema_pipeline.py source signor reactome wikipathways
```

### Build global identifier aggregation
```bash
uv run python scripts/build_global_entity_identifiers.py
```

Or for selected sources:
```bash
uv run python scripts/build_global_entity_identifiers.py signor reactome uniprot hmdb
```

## Recommended per-source processing order

For one source:

```bash
uv run python scripts/target_schema_pipeline.py mappings
uv run python scripts/target_schema_pipeline.py source <source>
```

If global aggregation should also be refreshed:

```bash
rm -rf data_v2/target_schema_global
uv run python scripts/build_global_entity_identifiers.py
```

## Recommended full refresh order

```bash
# 1. rebuild resolver mapping tables
uv run python scripts/target_schema_pipeline.py mappings

# 2. build and normalize one or more sources
uv run python scripts/target_schema_pipeline.py source signor reactome hmdb chebi mebocost

# 3. rebuild global identifier aggregation
uv run python scripts/target_schema_pipeline.py global
```

## Notes on preferred canonical normalization

### Proteins
Preferred canonical target:
- `MI:1097:Uniprot`

Mapping routes used:
- direct canonical UniProt
- UniProt isoform normalization (`P12345-2 -> P12345`)
- UniProt secondary accession -> primary accession
- fallback unique mapping from entry name / Entrez / Ensembl / gene symbol

### Chemicals
Preferred canonical target:
- `MI:2010:Standard Inchi`

Mapping routes used:
- direct Standard InChI
- unique mapping from chemical reference IDs such as:
  - ChEBI
  - HMDB
  - LipidMaps
  - SwissLipids
  - PubChem
  - ChEMBL Compound
  - DrugBank
  - KEGG Compound
  - BindingDB

Ambiguous mappings are not auto-applied.

## Key code locations

- target schema conversion: `scripts/silver_to_target_schema.py`
- within-source dedup: `scripts/target_schema_entity_dedup.py`
- resolver mapping table materialization: `scripts/target_schema_pipeline.py mappings`
- per-source normalization: `id_resolver/resolve/target_schema.py`
- global aggregation: `scripts/build_global_entity_identifiers.py`

## Useful reports

- overall mapping investigation: `docs/target_schema_identifier_mapping_report.md`
- unresolved preferred canonical IDs: `docs/preferred_canonical_report.md`
