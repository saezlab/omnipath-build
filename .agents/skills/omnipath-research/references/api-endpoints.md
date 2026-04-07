# OmniPath API endpoints for research workflows

## 1. Ontology discovery and term lookup

### List available ontologies
- `GET /ontologies`

### Search ontology terms by name or synonym
- `POST /terms/search`

Request shape:

```json
{
  "queries": ["seizure", "nucleus"],
  "limit": 5
}
```

### Resolve known ontology accessions
- `POST /terms`

### Inspect one term
- `GET /{ontology}/term/{id}`

### Navigate ontology structure
- `GET /{ontology}/term/{id}/parents`
- `GET /{ontology}/term/{id}/children`
- `GET /{ontology}/term/{id}/ancestors?depth=N`
- `GET /{ontology}/term/{id}/descendants?depth=N`
- `GET /{ontology}/term/{id}/trajectories`
- `POST /tree`

Use these endpoints to turn user concepts into concrete ontology terms.

## 2. Resource downloads

### Download one source-specific resource bundle
- `GET /resources/{resource_id}/download`

### Download multiple resource bundles together
- `POST /resources/download`

Request shape:

```json
{
  "resource_ids": ["signor", "reactome"],
  "filename": "optional_bundle_name"
}
```

## 3. Working pattern to prefer

### If the question starts from biology terms
1. search ontology terms
2. inspect descendants or ancestors if needed
3. choose relevant resources
4. download the needed resource-specific artifacts
5. analyze locally

### If the question starts from a named database
1. choose the resource
2. download the needed artifact bundle
3. analyze locally
