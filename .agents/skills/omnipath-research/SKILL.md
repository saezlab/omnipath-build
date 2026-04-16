---
name: omnipath-research
description: Answer biological research questions with the OmniPath API. Use for ontology-guided term discovery, resource selection, source-specific dataset downloads, and understanding the returned resource schemas well enough to analyze and join datasets.
compatibility: Requires access to an OmniPath API deployment.
---

# omnipath-research

Use this skill for biology questions that should be answered with the OmniPath API.

## Covers

- translating plain-language concepts into ontology terms
- picking relevant resources
- downloading source-specific parquet bundles
- understanding schemas well enough to analyze and join datasets

## Workflow

1. **Plan the data need**
   - identify the main entity class
   - note any concepts that should be ontology-resolved
   - decide whether one resource or several are needed
2. **Resolve terms**
   - prefer ontology search over guessing accessions
   - inspect terms and expand descendants/ancestors only if useful
3. **Choose resources**
   - use `references/resources.md`
4. **Download artifacts**
   - save downloaded bundles under `omnipath-data/` and reuse them when possible instead of redownloading
   - ontology endpoints and download endpoints are listed in `references/api-endpoints.md`
5. **Analyze and join**
   - treat each resource as source-specific
   - join using the canonical identifier columns already present in each table
   - use `entity_identifiers.parquet` only for alternate-ID mapping

## Schema expectations

Use:
- `references/schema-guide.md` for file roles and join patterns
- `references/api-endpoints.md` for endpoint details
- `references/resources.md` for resource selection

## Key rules

- choose resources deliberately instead of downloading everything
- keep downloaded bundles under `omnipath-data/` so repeated analyses can reuse them
- use `entity_annotation.parquet` for entity-level term annotations
- use `interaction_annotation.parquet` for interaction-level term annotations
- use `interaction.parquet` / `interaction_evidence.parquet` for compact vs provenance-bearing interactions
- use `association.parquet` / `association_evidence.parquet` for compact vs provenance-bearing parent-member relationships

## Report back

For each analysis, report:
1. ontology terms used
2. resources chosen and why
3. endpoints used
4. artifact files used
5. important caveats about scope or coverage
