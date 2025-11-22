# Reactome Identifier Merge Debug Notes

This note captures the key commands and file paths needed to inspect why Reactome EntityReference nodes and reaction participants are not merging on Reactome IDs.

## Key file paths
- Reactome silver outputs: `databases/omnipath/data/reactome/`
  - `reactome_entity_references.parquet`
  - `reactome_reactions.parquet`
  - `reactome_pathways.parquet`
- Reactome local tables (after gold step): `databases/omnipath/output/local_tables/`
  - `local_entity_reactome.parquet`
  - `local_entity_identifier_reactome.parquet`
- Gold outputs (after full gold run): `databases/omnipath/output/`
  - `entity_identifier.parquet`
  - `entity_record_mapping.parquet`

## Reproduce/refresh Reactome silver
```bash
# Clear Reactome cache (stores parsed BioPAX):
rm /Users/jschaul/Library/Caches/pypath/reactome/*.pkl

# Re-run Reactome silver with override
uv run -m omnipath_build.database_manager silver --source reactome --override
```

## Inspect EntityReference identifiers (expect Reactome IDs)
```bash
python - <<'PY'
import polars as pl
refs = pl.read_parquet('databases/omnipath/data/reactome/reactome_entity_references.parquet')
exploded = refs.select(pl.col('identifiers').explode()).with_columns(
    pl.col('identifiers').struct.field('type').alias('type'),
    pl.col('identifiers').struct.field('value').alias('value'),
)
print(exploded.select(pl.col('type').unique()).sort('type'))
print("Reactome IDs:")
print(exploded.filter(pl.col('type').is_in(['OM:0130','OM:0131'])).head(10))
PY
```

## Inspect reaction participants’ identifiers (participants currently have stable IDs)
```bash
python - <<'PY'
import polars as pl
reac = pl.read_parquet('databases/omnipath/data/reactome/reactome_reactions.parquet')
exploded = (
    reac.select(pl.col('membership').explode())
        .drop_nulls()
        .select(pl.col('membership').struct.field('member'))
        .select(pl.col('member').struct.field('identifiers').explode())
        .drop_nulls()
        .with_columns(
            pl.col('identifiers').struct.field('type').alias('type'),
            pl.col('identifiers').struct.field('value').alias('value'),
        )
)
print(exploded.select(pl.col('type').unique()).sort('type'))
print("Sample OM:0130 participants:")
print(exploded.filter(pl.col('type')=='OM:0130').head(10))
PY
```

## Connect PhysicalEntity nodes to their EntityReference
BioPAX exposes a direct `bp:entityReference` edge from each PhysicalEntity to its EntityReference. This quick check shows which physical participants map to which references and whether the stable IDs sit on the PhysicalEntity or the EntityReference.

```bash
python - <<'PY'
from pypath.inputs_v2 import reactome

BP = reactome.BP
g = reactome._load_biopax_graph()
assert g is not None

pairs = []
for phys, ref in g.subject_objects(BP.entityReference):
    phys_x = reactome._extract_xrefs(g, phys, BP)
    ref_x = reactome._extract_xrefs(g, ref, BP)
    pairs.append({
        'physical': str(phys),
        'ref': str(ref),
        'phys_stable': ';'.join(phys_x.get('reactome_stable_id', [])),
        'ref_stable': ';'.join(ref_x.get('reactome_stable_id', [])),
        'ref_internal': ';'.join(ref_x.get('reactome_id', [])),
    })

print('Total mappings:', len(pairs))
print('Sample mappings (stable IDs on physical vs reference):')
for row in pairs[:10]:
    print(row)

print('\\nPhysical entities that have a stable ID but their reference lacks it:')
for row in pairs:
    if row['phys_stable'] and not row['ref_stable']:
        print(row)
        break
PY
```

## Inspect local Reactome identifiers post-gold (if gold has been run)
```bash
python - <<'PY'
import polars as pl
idf = pl.read_parquet('databases/omnipath/output/local_tables/local_entity_identifier_reactome.parquet')
print(idf.schema)
print(idf.head())
# Reactome stable IDs in local tables
print(idf.filter(pl.col('type_id')=='OM:0130').head())
PY
```

## Inspect gold entity identifiers for a specific Reactome ID (if gold has been run)
```bash
python - <<'PY'
import polars as pl
eid = 'R-HSA-69488'  # replace as needed
ident = pl.read_parquet('databases/omnipath/output/entity_identifier.parquet')
print(ident.filter(pl.col('identifier')==eid))
PY
```

## Notes on current code behavior
- Participants now carry Reactome stable IDs (`OM:0130`) via `_extract_reaction_participants`.
- EntityReference records currently emit only `OM:0131` (internal Reactome IDs) unless stable IDs are supplied.
- A supplement map `_REFERENCE_ID_SUPPLEMENT` collects stable IDs seen on participants keyed by `EntityReference` URI; entity references merge any collected stable IDs when emitting identifiers.
- If entity references still lack `OM:0130`, the issue is that stable IDs are not being associated with the same `entityReference` URI. Inspect BioPAX to confirm whether stable IDs live on the PhysicalEntity node vs. the EntityReference node.
