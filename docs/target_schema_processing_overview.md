# Target schema processing overview

This document summarizes the current processing flow from per-source inputs to:

- per-source gold target-schema packages
- mapping-table materialization
- preferred-canonical ID mapping
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

### 3. Materialize mapping tables
Reference mapping tables are materialized for downstream canonical-ID normalization.

Script:
- `scripts/materialize_target_schema_mapping_tables.py`

Outputs under `data_v2/target_schema/_mapping_tables/`:
- `uniprot_reference_mappings.parquet`
- `uniprot_secondary_to_primary.parquet`
- `chemical_reference_to_standard_inchi.parquet`

Also creates staged chemical reference pairs source-by-source under:
- `data_v2/target_schema/_mapping_tables/staging/chemical_reference_pairs/`

### 4. Apply mapping tables to per-source target schema
Per-source entities are enriched with preferred canonical identifiers when uniquely resolvable.

Script:
- `scripts/apply_target_schema_identifier_mapping.py`

Preferred canonical identifiers:
- proteins / genes / RNA / DNA -> `MI:1097:Uniprot`
- small molecules / lipids -> `MI:2010:Standard Inchi`

Current mapping behavior:
- unique mappings are applied automatically
- ambiguous mappings are not applied automatically
- after adding inferred identifiers, within-source deduplication is run again

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

### Materialize mapping tables
```bash
uv run python scripts/materialize_target_schema_mapping_tables.py
```

### Apply mapping to one source
```bash
uv run python scripts/apply_target_schema_identifier_mapping.py <source>
```

Example:
```bash
uv run python scripts/apply_target_schema_identifier_mapping.py mebocost
```

### Apply mapping to multiple sources
```bash
uv run python scripts/apply_target_schema_identifier_mapping.py signor reactome wikipathways
```

### Apply mapping in dry-run mode with unresolved-canonical report
```bash
uv run python scripts/apply_target_schema_identifier_mapping.py \
  --dry-run \
  --report-path docs/preferred_canonical_report.md \
  <source>
```

Example:
```bash
uv run python scripts/apply_target_schema_identifier_mapping.py \
  --dry-run \
  --report-path docs/preferred_canonical_report.md \
  mebocost
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
uv run python scripts/silver_to_target_schema.py <source> --output-root data_v2/target_schema
uv run python scripts/materialize_target_schema_mapping_tables.py
uv run python scripts/apply_target_schema_identifier_mapping.py <source>
```

If global aggregation should also be refreshed:

```bash
rm -rf data_v2/target_schema_global
uv run python scripts/build_global_entity_identifiers.py
```

## Recommended full refresh order

```bash
# 1. convert one or more sources to target schema
uv run python scripts/silver_to_target_schema.py signor reactome hmdb chebi --output-root data_v2/target_schema

# 2. rebuild mapping tables
uv run python scripts/materialize_target_schema_mapping_tables.py

# 3. apply preferred-canonical mapping to all sources
uv run python scripts/apply_target_schema_identifier_mapping.py

# 4. rebuild global identifier aggregation
rm -rf data_v2/target_schema_global
uv run python scripts/build_global_entity_identifiers.py
```

## Notes on preferred canonical mapping

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
- mapping table build: `omnipath_build/target_schema/id_mapping_tables.py`
- mapping table materialization: `scripts/materialize_target_schema_mapping_tables.py`
- per-source mapping application: `scripts/apply_target_schema_identifier_mapping.py`
- global aggregation: `scripts/build_global_entity_identifiers.py`

## Useful reports

- overall mapping investigation: `docs/target_schema_identifier_mapping_report.md`
- unresolved preferred canonical IDs: `docs/preferred_canonical_report.md`
