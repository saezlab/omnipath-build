# Resources parquet progress

## Achieved

- Added a new resource-index builder at `omnipath_build/pipeline/resources_index.py`.
- `resources.parquet` is now generated from the **current gold outputs** (`data_v2/gold/<source>/latest`).
- Added `primary_category` to `pypath.inputs_v2.base.ResourceConfig` and annotated current `inputs_v2` resources with it.
- Added/fixed missing resource metadata where needed, including a PubMed ID for `chembl`.
- Added resource-level summary of interaction participant type pairs, stored as canonical CV-accession/label pairs such as:
  - `MI:0326:Protein|MI:0328:Small Molecule`
- Updated the build pipeline so ontology/artifact outputs (e.g. `.obo`) are copied from silver into the corresponding gold version directory.
- Updated source discovery so ontology-only resources such as `omnipath_ontology` are included in the pipeline.
- Rebuilt ontology-producing sources with the Make target using `TEST_MODE=1`:
  - `omnipath_ontology`
  - `chebi`
  - `reactome`
  - `wikipathways`
- Verified that ontology artifacts now appear in gold and that `data_modalities` correctly includes `ontology`.

## Current output

Generated file:

- `data_v2/gold/resources.parquet`

Current columns:

- `resource_id`
- `resource_name`
- `description`
- `homepage_url`
- `license`
- `pubmed_id`
- `primary_category`
- `data_modalities`
- `interaction_participant_types`
- `entity_count`
- `interaction_count`
- `association_count`
- `identifier_count`
- `ontology_term_count`
- `total_size_bytes`
- `last_downloaded_at`
- `last_built_at`
- `build_status`

## Simplifications made

Removed from the design and implementation:

- `organisms`
- `downloadable`
- `artifact_count`

## Result

We now have a usable `resources.parquet` for a dataset browser UI, based on current gold outputs and including ontology resources/artifacts in the same gold snapshot.
