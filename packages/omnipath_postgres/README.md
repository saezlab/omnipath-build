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

The data root contains resources/<source>/<version>/. The loader validates schemas, row counts, sizes and SHA-256 checksums before connecting. It loads tables, indexes, derived tables and release metadata in one transaction and rechecks the selected files before committing. Any failure rolls back the new schema. Existing destination schemas are rejected.

Full published text keys are retained. Per-resource tables keep complete nested entity and relation records, identifier occurrences, evidence, annotations and quantities. Full raw source payloads remain in the immutable Parquets and are not stored in PostgreSQL. Their schemas, checksums, row counts, JSON bodies and owner references are still validated during import. `counts` reports stored records; `validated_payload_rows` separately reports the source payload rows checked and discarded. Canonical entity and statement views combine identical keys across resources, retaining resource provenance and aggregating evidence. The relation view selects graph statements; ontology statements stay available through the statement view and ontology tables. Different relation keys remain separate even when endpoints and predicates match.

The identifier lookup, endpoint indexes, direct graph relation counts and typed quantity view adapt useful parts of the previous PostgreSQL implementation. Source counts and resource overlap retain release-visible provenance.

Recognized ontology predicates become directional subclass or part-of edges. Closure tables retain shortest paths both within each resource and across the release, handle cycles and omit self ancestry. Entity hierarchy counts describe the loaded release; original resource records retain their published counts.

Reaction derivation groups explicit input, output and catalyst relations by source event. It reads evidence-owned source attributes for direction, compartment and coefficients, preserves typed and symbolic stoichiometry, and records unknown or conflicting context. Each eligible occurrence must carry a source-record SHA-256 reference; the derivation rejects missing or conflicting hashes instead of silently dropping legacy payload-only context. When a matching nonnull raw source row exists, import checks its exact text SHA and any declared source-record type against the evidence attributes before discarding the body. Matches use relation key, source and row ID within the pinned resource version, including null values. Missing raw rows remain allowed; missing type metadata stays an explicit diagnostic. Source-record type metadata keeps unsupported input shapes visible. Rebuilds read only PostgreSQL annotations and evidence, including after Parquet files are unavailable. Source parsers and entity resolution are not rerun.

See the [subset adapters](../omnipath_subsets/README.md) for MetSigDB, network presets and COSMOS. Full product comparisons and web backend benchmarks remain.

PostgreSQL 14 or newer is required; no optional database extensions are needed. For an isolated integration check, install PostgreSQL binaries and run make test-postgres. Tests start a temporary Unix-socket-only cluster, load small local fixtures and shut it down afterward.
