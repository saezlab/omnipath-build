# OmniPath PostgreSQL

Load an explicit release of already resolved Parquet resources. The loader uses DuckDB to stream rows into PostgreSQL and never runs entity resolution or source parsers.

Create a release manifest with exact resource versions:

```json
{"schema_version": 1, "version": "2026.09", "resources": {"signor": "0.1.1"}}
```

Load it into a new schema:

```sh
export OMNIPATH_DATABASE_URL='postgresql:///omnipath'
uv run --frozen omnipath-postgres release.json \
  --data-root data/migration-smoke --schema release_2026_09
```

The data root contains resources/<source>/<version>/. The loader validates schemas, row counts, sizes and SHA-256 checksums before connecting. It loads tables, indexes, derived counts and release metadata in one transaction and rechecks the selected files before committing. Any failure rolls back the new schema. Existing destination schemas are rejected.

Full published text keys are retained. Per-resource tables keep complete nested entity and relation records, identifier occurrences, evidence, annotations and quantities. Payload JSON retains its original text. Canonical entity and relation views combine identical keys across resources, retaining resource provenance and aggregating evidence. Different relation keys remain separate even when endpoints and predicates match.

The identifier lookup, endpoint indexes, direct relation counts and typed quantity view adapt useful parts of the previous PostgreSQL implementation. Ontology expansion and compatibility with MetSigDB, network views, COSMOS and the existing web backend require the next validation milestone.

PostgreSQL 14 or newer is required; no optional database extensions are needed. For an isolated integration check, install PostgreSQL binaries and run make test-postgres. Tests start a temporary Unix-socket-only cluster, load small local fixtures and shut it down afterward.
