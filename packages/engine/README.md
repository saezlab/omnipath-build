# OmniPath serving engine

Embedded DuckDB analytical engine over OmniPath's Parquet tables in a local data
directory (including a read-only mounted one). It answers the queries of the REST API
and explorer: entity search and details, grouped search, relation search, facets,
ontology browsing, statistics and Parquet exports.

Two packages run it:

- `omnipath-api` serves it over HTTP (`../api`).
- `omnipath-client` runs it on the user's machine over resources installed there
  (`../client`).

It depends on `omnipath-core`, DuckDB, PyArrow and Pydantic, and on no web framework.

```python
from omnipath_engine import ParquetServingEngine

engine = ParquetServingEngine("/path/to/data")
with engine.query_scope():
    found = engine.search_entities_api("TP53", limit=5)["entities"]
engine.close()
```

## Data directory

`resources/<resource>/<version>/` holds one resource version: the published tables and
the serving tables (`omnipath_core.schema`). A version is served when all of them are
present; raw evidence payloads (`evidence_payloads.parquet`) are optional. Optional
`releases/<version>.json` manifests pin resource versions, `query_policy.json` excludes
resources from queries, and `references/taxonomy` names taxa.

## Modules

- **`engine` / `store`**: inventory generations, request scopes, query caches and
  configured DuckDB connections. `memory_limit` and `threads` set each query
  connection; `OMNIPATH_QUERY_CONCURRENCY` sets how many run at once.
- **`queries`**: domain query services for entities, relations, ontology, evidence,
  facets, resources, statistics, selection and export.
- **`filters`**: the filter and cursor contracts the queries accept.
- **`annotations` / `shape`**: presentation adapters for compiled identifiers,
  annotations, entities and relations.

## Tests

```bash
uv run pytest packages/engine/tests -q
```
