# Legacy nested silver removal plan

## Goal

The active pipeline now uses one silver format: columnar silver tables written directly in each source silver directory. There is no legacy gold fallback and no optional compatibility mode.

Validated before cutover:

- UniProt entity parity: exact
- ChEBI entity parity: exact
- SIGNOR relation parity: exact
- SIGNOR relation speedup: ~1.65x on repeated runs

## Final architecture

```text
raw inputs
  -> existing Python mappers / EntityBuilder
  -> source-level silver tables
  -> gold entity build
       writes entity_occurrence_map.parquet
  -> gold relation build
  -> combined gold
```

Gold must not reconstruct nested silver parquet rows into Python dictionaries.

## Final silver contract

Each source silver directory contains:

```text
entity_occurrence.parquet
entity_identifier.parquet
entity_annotation.parquet
membership.parquet
membership_annotation.parquet
resource.parquet
```

Legacy dataset-specific nested entity parquet files such as `proteins.parquet`, `molecules.parquet`, `interactions.parquet`, and `complexes.parquet` are not part of the active gold contract.

## Implementation checklist

- [x] Remove `--normalized` CLI semantics from `commands.py`.
- [x] Write silver tables by default from `silver/build.py`.
- [x] Write silver tables directly in the source silver directory, not under `/normalized`.
- [x] Rename writer concepts to canonical silver table writer names.
- [x] Make `build_entities.py` require silver tables and remove nested fallback.
- [x] Make `build_relations.py` require silver tables plus `entity_occurrence_map.parquet`.
- [x] Update schema docs to describe the final silver contract.

## Remaining cleanup

1. Delete unused legacy nested extraction helpers once imports are confirmed unused:
   - nested row traversal in `gold/utils/entity_extraction.py`
   - active usage of `extract_all_from_silver()`
2. Delete any stale generated nested silver files from existing local data directories.
3. Update older benchmark/optimization docs or mark them historical.
4. Add smoke tests:
   - silver build writes the five source-level silver tables
   - gold entity build fails if silver tables are absent
   - gold relation build fails if `entity_occurrence_map.parquet` is absent
5. Re-run full builds for UniProt, ChEBI, and SIGNOR after cleanup.

## Success criteria

- No active gold code reads nested silver entity parquet.
- Default silver output is source-level silver tables plus `resource.parquet`.
- Default CLI path produces and consumes only the final silver table contract.
- UniProt, ChEBI, and SIGNOR retain previously validated outputs.
