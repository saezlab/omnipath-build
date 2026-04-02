# Standalone ID resolver

Small, source-authoritative mapping outputs built from narrow `pypath.inputs_v2` translation datasets.

## Scope

- proteins
  - reference identifier -> primary UniProt
  - secondary UniProt -> primary UniProt
- small molecules
  - source-native identifier -> Standard InChI

No reconciliation or cross-reference conflict logic lives here.

## Design rule

Keep the implementation as simple as the source data allows.

- do not add cleanup or normalization unless actual source data requires it
- keep source-specific logic in `pypath.inputs_v2`
- keep `id_resolver` as a thin materialization layer

## Layout

- `id_resolver/data/`
  - `proteins/`
  - `chemicals/`
- `pypath-data/`
  - shared raw download cache at the project root
- `id_resolver/build/`
  - `paths.py`
  - `parquet.py`
  - `sources/proteins.py`
  - `sources/chemicals.py`
  - `mapping_tables.py`

Raw downloads are reused from `pypath-data/`.
Outputs are written to `id_resolver/data/proteins/` and `id_resolver/data/chemicals/` by default.

## Outputs

### Proteins
- `protein_reference_to_uniprot.parquet`
  - `key_type`, `key_value`, `taxonomy_id`, `primary_uniprot`
- `uniprot_secondary_to_primary.parquet`
  - `secondary_uniprot`, `primary_uniprot`

### Chemicals
- `chebi.parquet`
- `hmdb.parquet`
- `lipidmaps.parquet`
- `swisslipids.parquet`

Chemical parquet columns:
- `source`, `key_type`, `key_value`, `standard_inchi`

## Implementation model

Source-specific extraction lives in `pypath.inputs_v2` as narrow translation datasets:

- `uniprot.resource.reference_id_translation`
- `uniprot.resource.secondary_to_primary`
- `chebi.resource.id_translation`
- `hmdb.resource.id_translation`
- `lipidmaps.resource.id_translation`
- `swisslipids.resource.id_translation`

`id_resolver` just executes those datasets and writes parquet, using PyArrow chunked writing.

## CLI

```bash
uv run python -m id_resolver.build.mapping_tables uniprot
uv run python -m id_resolver.build.mapping_tables chebi hmdb lipidmaps swisslipids
```

Options:
- `--output-dir <path>`
- `--taxonomy-id <id>` for UniProt, repeatable
- `--max-records <n>` for development smoke tests where supported
