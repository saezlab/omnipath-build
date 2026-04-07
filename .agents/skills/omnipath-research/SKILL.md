---
name: omnipath-research
description: Answer biological research questions with the OmniPath API. Use for ontology-guided term discovery, resource selection, source-specific dataset downloads, and understanding the returned resource schemas well enough to analyze and join datasets.
compatibility: Requires access to an OmniPath API deployment.
---

# omnipath-research

Use this skill when a user asks a biology question that should be answered with the OmniPath API.

## What this skill covers

- finding the right ontology terms from plain-language concepts
- choosing which resources are relevant for a question
- downloading the right source-specific datasets
- understanding the returned parquet schemas well enough to analyze and join them

## API surfaces to use

### 1. Ontology endpoints
Use these to turn user concepts into concrete ontology terms.

Main endpoints:
- `GET /ontologies`
- `POST /terms/search`
- `POST /terms`
- `GET /{ontology}/term/{id}`
- `GET /{ontology}/term/{id}/parents`
- `GET /{ontology}/term/{id}/children`
- `GET /{ontology}/term/{id}/ancestors`
- `GET /{ontology}/term/{id}/descendants`
- `GET /{ontology}/term/{id}/trajectories`
- `POST /tree`

### 2. Resource downloads
Use these when you want the actual source-specific parquet artifacts.

Main endpoints:
- `GET /resources/{resource_id}/download`
- `POST /resources/download`

Use `references/resources.md` as the quick guide for choosing likely resources.
## Standard workflow

### Step 1. Translate the question into a data plan
Identify:
- the main entity class: proteins, metabolites, pathways, complexes, phenotypes
- any biology terms that should be resolved through ontologies
- which resource types are likely relevant
- whether the task needs one specific database or several

### Step 2. Resolve concepts into ontology terms
If the user gives words instead of accessions, search first.

Then inspect the chosen terms and, if useful, expand descendants.

### Step 3. Choose resources
Use `references/resources.md` to decide which datasets best match the question.

### Step 4. Download the needed resource artifacts
Download one or more resource bundles and analyze them locally.

### Step 5. Join across resources in the standard way
When a question requires combining cohorts or memberships across source-specific datasets:
1. filter `entity_identifiers_resolved.parquet` to rows with `is_canonical = true`
2. join across resources on `(identifier, identifier_type)`
3. join back to `entities.parquet`, `annotations.parquet`, `interactions.parquet`, or `associations.parquet` as needed

This is the default cross-resource join pattern. Adding identifiers directly to `entities.parquet` would only be a denormalized convenience, not a requirement.

## The schemas the agent should expect

See:
- `references/schema-guide.md`
- `references/api-endpoints.md`
- `references/resources.md`

## Important rules

- Prefer ontology search over guessing ontology accessions.
- Choose resources deliberately instead of downloading everything.
- Treat each resource as source-specific.
- Use only resource-specific artifacts.
- When joining across source-specific files, rely on resolved canonical identifiers where available.
- Use `annotations.parquet` to inspect term-like annotations attached to entities or interactions.
- Use `associations.parquet` to inspect parent-member relationships where present.

## What to report back

For each analysis, report:
1. the ontology terms used
2. the resources chosen and why
3. the endpoints used
4. the artifact files used
5. any important caveats about scope or coverage
