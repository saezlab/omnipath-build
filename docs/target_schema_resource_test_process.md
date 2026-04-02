# Target-schema resource test process

Use this process to test one source at a time with the new `id_resolver`-backed normalization flow.

## Prerequisites

Resolver mappings are read from:

- `id_resolver/data/`

Make sure the needed mapping parquet files are present for the namespaces used by the source.

## 1. Build one source

Example:

```bash
uv run --no-sync python scripts/target_schema_pipeline.py source signor
```

This runs:
- silver build
- target-schema conversion
- existing per-source dedup
- identifier normalization using `id_resolver/data`

## 2. Inspect the normalization summary

The pipeline prints a summary like:
- `entities_seen`
- `eligible_entities`
- `resolved_entities`
- `identifier_rows_added`
- `entities_updated`

Record those values for the source under test.

## 3. Inspect entity/identifier counts and canonical types

Replace `signor` with the source name being tested.

```bash
uv run --no-sync python - <<'PY'
import polars as pl
base='data_v2/gold/signor'
ent=pl.read_parquet(f'{base}/entities.parquet')
ids=pl.read_parquet(f'{base}/entity_identifiers.parquet')

print('entities', ent.height)
print('identifiers', ids.height)
print(ent.group_by('canonical_identifier_type').len().sort('len', descending=True).head(20))
PY
```

## 4. Inspect unresolved canonicals for one namespace

Example: unresolved ChEBI canonicals in `signor`.

```bash
uv run --no-sync python - <<'PY'
import polars as pl
base='data_v2/gold/signor'
ent=pl.read_parquet(f'{base}/entities.parquet')

unmapped = ent.filter(
    (pl.col('entity_type')=='MI:0328:Small Molecule') &
    (pl.col('canonical_identifier_type')=='MI:0474:Chebi')
)
print(unmapped.select(['entity_id','canonical_identifier']).head(25))
print('count', unmapped.height)
PY
```

## 5. Check whether unresolved IDs are absent from the resolver table

Example: unresolved ChEBI IDs.

```bash
uv run --no-sync python - <<'PY'
import polars as pl
resolver = pl.read_parquet('id_resolver/data/chemicals/chebi.parquet') \
    .select(['key_value']).rename({'key_value':'chebi'})
ent = pl.read_parquet('data_v2/gold/signor/entities.parquet')

unmapped = ent.filter(
    (pl.col('entity_type')=='MI:0328:Small Molecule') &
    (pl.col('canonical_identifier_type')=='MI:0474:Chebi')
).select(pl.col('canonical_identifier').alias('chebi')).unique()

missing = unmapped.join(resolver, on='chebi', how='anti')
print('unique unresolved', unmapped.height)
print('missing from resolver', missing.height)
print(missing.head(20))
PY
```

## 6. Interpretation

- If unresolved IDs are missing from `id_resolver/data`, the normalization logic is not at fault.
- If unresolved IDs are present in `id_resolver/data`, investigate the normalization logic for that namespace.
- For proteins, also check whether missing `taxonomy_id` could explain unresolved gene-symbol mappings.

## Running this across all `inputs_v2` sources

To apply the same per-source process to every top-level resource module in
`pypath/pypath/inputs_v2`, use:

```bash
uv run --no-sync python scripts/target_schema_all_sources_report.py
```

By default this reuses existing `data_v2/silver/*` outputs and does **not**
rebuild silver.

Useful variants:

```bash
# See which sources will be processed
uv run --no-sync python scripts/target_schema_all_sources_report.py --list-sources

# Rebuild silver before running gold+normalization
uv run --no-sync python scripts/target_schema_all_sources_report.py --build-silver

# Fast smoke run for high-volume sources when rebuilding silver
uv run --no-sync python scripts/target_schema_all_sources_report.py --build-silver --silver-test-mode
```

Outputs:
- `reports/target_schema_all_sources_report.json`
- `reports/target_schema_all_sources_report.md`

Each source is processed independently so one failure does not stop the rest.
The report captures:
- build status
- normalization summary
- entity count
- identifier count
- canonical identifier type counts
- entity type counts

## Notes

Current resolver-backed normalization targets:
- proteins -> `MI:1097:Uniprot`
- small molecules / lipids -> `MI:2010:Standard Inchi`

The current pipeline order is:
1. silver build
2. target-schema conversion
3. per-source dedup
4. `id_resolver` normalization
