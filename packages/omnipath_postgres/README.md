# OmniPath PostgreSQL

Load an explicit release of already resolved Parquet resources. DuckDB projects nested Parquet records into the five PostgreSQL base tables and serializes their JSON columns in SQL. It writes bounded CSV chunks, which Python forwards as bytes through PostgreSQL COPY. The loader does not convert projected rows into Python objects or rerun entity resolution or source parsers.

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

The data root contains resources/<source>/<version>/. The loader validates schemas, row counts, sizes and SHA-256 checksums before connecting. It loads tables, indexes, derived tables and release metadata in one transaction and rechecks the selected files before committing. Any failure rolls back the new schema. Existing destination schemas are rejected. Parent tables are copied before child tables. Foreign keys are immediate, and each CSV chunk gets its own COPY statement inside the same transaction, so pending checks do not grow with the whole release. CSV rotation targets 16 MB; the last vector or a large record can exceed that threshold. Typed projection and integrity checks on all loaded records remain mandatory. Raw source records are not consumed by the normal PostgreSQL load.

DuckDB defaults to one thread and 512 MB working memory, configurable with --duckdb-threads and --memory-limit. CSV staging and spills use a private temporary directory next to the release manifest; --temp-directory selects another filesystem. Only one output table is staged at a time, and its files are removed after COPY or failure. The load result reports per-resource SQL-validation, staging and COPY times. --batch-size controls only the optional source-record audit, capped at 1024.

Full published text keys are retained. Per-resource tables keep complete nested entity and relation records, identifier occurrences, evidence, annotations and quantities. Full raw source payloads remain in the immutable Parquets and are not stored in PostgreSQL. Their schemas, file checksums, sizes and footer row counts remain part of the pinned artifact checks. The normal loader trusts published resource builds and does not decode discarded raw bodies. Use `--validate-source-records` (Python: `validate_source_records=True`) for an additional audit of raw JSON, source-owner pointers and reaction source hashes. This audit uses dictionary-preserving Arrow reads and narrow PostgreSQL lookup statistics; every occurrence receives its own pointer and owner checks. `counts` reports stored records; `validate_source_records` reports the selected audit mode and `validated_payload_rows` reports only rows actually checked. That mapping is empty for the default load. Canonical entity and statement views combine identical keys across resources, retaining resource provenance and aggregating evidence. The relation view selects graph statements; ontology statements stay available through the statement view and ontology tables. Different relation keys remain separate even when endpoints and predicates match.

The identifier lookup, endpoint indexes, direct graph relation counts and typed quantity view adapt useful parts of the previous PostgreSQL implementation. Source counts and resource overlap retain release-visible provenance.

Identifier values remain complete TEXT values in the base table, nested records and `entity_identifier_lookup` view. The nonunique alias-search index uses `(ns, "left"(lower(id), 256) text_pattern_ops, entity_key)` so very long identifiers fit PostgreSQL's B-tree entry limit. Exact indexed searches combine `"left"(lower(id), 256) = "left"(lower(:id), 256)` with `lower(id) = lower(:id)` to distinguish shared prefixes. For literal prefix searches, construct the bounded LIKE pattern from the first 256 characters of the lowered, **unescaped** prefix text, then escape LIKE metacharacters and append `%`. Apply a second predicate against `lower(id)` using the full lowered, escaped prefix followed by `%`. The lookup view and product queries retain complete IDs.

Recognized ontology predicates become directional subclass or part-of edges. Closure tables retain shortest paths both within each resource and across the release, handle cycles and omit self ancestry. Entity hierarchy counts describe the loaded release; original resource records retain their published counts.

Reaction derivation groups explicit input, output and catalyst relations by source event. It reads evidence-owned source attributes for direction, compartment and coefficients, preserves typed and symbolic stoichiometry, and records unknown or conflicting context. Each eligible occurrence must carry a source-record SHA-256 reference; the derivation rejects missing or conflicting hashes instead of silently dropping legacy payload-only context. During the optional source-record audit, a matching nonnull raw source row is checked against the evidence attributes for its exact text SHA and any declared source-record type. Matches use relation key, source and row ID within the pinned resource version, including null values. Missing raw rows remain allowed; missing type metadata stays an explicit diagnostic. Source-record type metadata keeps unsupported input shapes visible. Rebuilds read only PostgreSQL annotations and evidence, including after Parquet files are unavailable. Source parsers and entity resolution are not rerun.

See the [subset adapters](../omnipath_subsets/README.md) for MetSigDB, network presets and COSMOS. Full product comparisons and web backend benchmarks remain.

PostgreSQL 14 or newer is required; no optional database extensions are needed. For an isolated integration check, install PostgreSQL binaries and run make test-postgres. Tests start a temporary Unix-socket-only cluster, load small local fixtures and shut it down afterward.
