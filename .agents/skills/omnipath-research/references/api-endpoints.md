# OmniPath API endpoints

Base URL: `https://dev.omnipathdb.org/api`

## Ontology discovery

- `GET /ontologies`
- `POST /terms/search`
- `POST /terms`
- `GET /{ontology}/term/{id}`
- `GET /{ontology}/term/{id}/parents`
- `GET /{ontology}/term/{id}/children`
- `GET /{ontology}/term/{id}/ancestors?depth=N`
- `GET /{ontology}/term/{id}/descendants?depth=N`
- `GET /{ontology}/term/{id}/trajectories`
- `POST /tree`

Example `POST /terms/search` body:

```json
{
  "queries": ["seizure", "nucleus"],
  "limit": 5
}
```

## Resource downloads

- `GET /resources/{resource_id}/download`
- `POST /resources/download`

Prefer saving downloads into `omnipath-data/` so later analyses can reuse the same bundles.

Example `POST /resources/download` body:

```json
{
  "resource_ids": ["signor", "reactome"],
  "filename": "optional_bundle_name"
}
```
