# Standalone ID resolver

Small, source-authoritative mapping outputs built from narrow `pypath.inputs_v2` translation datasets.

## Scope

- proteins
  - any supported protein identifier -> primary UniProt
- small molecules
  - any supported chemical identifier -> Standard InChI

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
- `protein_identifier_lookup.parquet`
  - `source`, `key_type`, `key_value`, `taxonomy_id`, `primary_uniprot`, `mapping_type`

### Chemicals
- `chemical_identifier_lookup.parquet`
  - `source`, `key_type`, `key_value`, `standard_inchi`

## Implementation model

Source-specific extraction lives in `pypath.inputs_v2` as narrow translation datasets:

- `uniprot.resource.reference_id_translation`
- `uniprot.resource.secondary_to_primary`
- `chebi.resource.id_translation`
- `hmdb.resource.id_translation`
- `lipidmaps.resource.id_translation`
- `swisslipids.resource.id_translation`

`id_resolver` executes those datasets and writes only the long lookup tables used by the resolver, using PyArrow chunked writing.

## CLI

```bash
uv run python -m id_resolver.build.mapping_tables uniprot
uv run python -m id_resolver.build.mapping_tables chebi hmdb lipidmaps swisslipids
```

Options:
- `--output-dir <path>`
- `--taxonomy-id <id>` for UniProt, repeatable
- `--max-records <n>` for development smoke tests where supported
