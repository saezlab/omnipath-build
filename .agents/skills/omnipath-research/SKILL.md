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

Example:

```bash
curl -sS -X POST http://localhost:8081/terms/search \
  -H 'Content-Type: application/json' \
  -d '{"queries": ["seizure", "nucleus", "phosphorylation"], "limit": 5}'
```

Then inspect the chosen terms and, if useful, expand descendants.

### Step 3. Choose resources
Use `references/resources.md` to decide which datasets best match the question.

Examples:
- SIGNOR for causal signaling interactions
- Reactome and WikiPathways for pathway membership
- BindingDB for chemical-protein binding interactions

### Step 4. Download the needed resource artifacts
Download one or more resource bundles and analyze them locally.

## The schemas the agent should expect

See:
- `references/schema-guide.md`
- `references/api-endpoints.md`
- `references/resources.md`

## Common user stories

### A. “Get all associated proteins with the term Seizure, then get all their interactions within SIGNOR.”
1. Search `seizure` and confirm the right HPO term.
2. Choose resources that can provide seizure-associated proteins.
3. Download the relevant resource artifacts.
4. Build the protein cohort from entity annotations and identifiers.
5. Download or inspect SIGNOR interactions for those proteins.

### B. “Get enriched pathways from WikiPathways and Reactome for a given protein set.”
1. Start from the given protein set.
2. Choose `wikipathways` and `reactome`.
3. Download their artifacts.
4. Use association data to find pathway memberships.
5. Summarize pathway overlap and enrichment by resource.

### C. “Get enriched ChEBI terms for this metabolite set and check their interactions in BindingDB.”
1. Start from the metabolite set.
2. Resolve useful ontology terms if needed.
3. Choose chemical-focused resources and BindingDB.
4. Download the relevant artifacts.
5. Match metabolites through identifiers and inspect interactions.

## Important rules

- Prefer ontology search over guessing ontology accessions.
- Choose resources deliberately instead of downloading everything.
- Treat each resource as source-specific.
- Use only resource-specific artifacts.
- When joining across source-specific files, rely on resolved canonical identifiers where available.

## What to report back

For each analysis, report:
1. the ontology terms used
2. the resources chosen and why
3. the endpoints used
4. the artifact files used
5. any important caveats about scope or coverage
