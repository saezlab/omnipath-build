# OmniPath PostgreSQL

Load an explicit release of already resolved Parquet resources into the current main branch's physical relational model. Entity resolution, resource parsing, Parquet schemas and independent resource updates stay upstream. PostgreSQL keeps its separately selected release and monthly publication schedule.

```json
{"schema_version": 1, "version": "2026.09", "resources": {"signor": "0.1.1"}}
```

```sh
export OMNIPATH_DATABASE_URL='postgresql:///omnipath'
uv run --frozen omnipath-postgres release.json \
  --data-root data --schema release_2026_09
```

The data root contains `resources/<source>/<version>/`. Loading requires a new destination schema. The CLI and package-level `load_release` use the main layout: UUID canonical `entity` and `relation` tables, numeric vocabulary/source/dataset dictionaries, global identifier and annotation dictionaries, source-partitioned evidence and links, and main's constraints and indexes. Small additive tables retain exact published keys, resource versions, qualified claims, repeated occurrences, taxonomy and typed quantities. Full source bodies and nested record JSON stay out of PostgreSQL.

DuckDB prepares relational tables once in an on-disk database and writes CSV chunks. Python forwards their bytes through PostgreSQL COPY without converting biological rows into Python objects. Source artifacts are read but never rewritten. Main keys, foreign keys and deferred indexes are restored and validated before the base checkpoint is advertised. Artifact schemas, sizes, row counts and SHA256 checksums and COPY counts remain build checks.

Main's downstream implementations provide search and ontology tables, chemical structure groups, classifications, interactions and resource facts, labels, bitmap filters, overlap summaries, resource metadata, MetSigDB, network presets and COSMOS. Biolink readers adapt predicates, scopes and quantities; the original main table and index contracts remain the reference. The [column map](../../docs/postgres-parquet-column-map.md) and [parity checklist](../../docs/postgres-main-parity-checklist.md) document unavailable original resolver/occurrence facts and deliberate adaptations. Published identities are explicitly marked `published`; they are never falsely marked as originally matched. MetSigDB uses the previously agreed published-chemical policy.

The verified base commits before downstream work. Each complete product commits together with its phase record; a failed product rolls back its unfinished work. Completed products are retained on a retry:

```sh
# Stop after base tables, constraints and indexes.
uv run --frozen omnipath-postgres release.json --data-root data \
  --schema release_2026_09 --base-only

# Finish derivations and remaining products from PostgreSQL; no repeated COPY.
uv run --frozen omnipath-postgres --finish --schema release_2026_09

# Resume only selected products; completed ones are skipped.
uv run --frozen omnipath-postgres --finish --schema release_2026_09 --products cosmos
```

The Python API accepts `base_only=True` and `products=('cosmos',)`. `finish_release(database_url, schema=...)` uses stored pins and durable phase records. A schema session advisory lock spans commits and prevents overlapping builds. `--checkpoint-base` and `--defer-constraints` remain accepted for CLI compatibility; both behaviors are already enabled in the main layout. An interrupted unvalidated COPY is not a resumable base.

DuckDB defaults to one thread and 1 GB memory. Use `--duckdb-threads`, `--memory-limit` and `--temp-directory` to choose working limits and staging storage. Temporary CSVs and DuckDB files are removed after loading or failure. Returned timings separate artifact checks, projection, CSV staging, COPY, constraints and durable phases.

Use PostgreSQL 18 with main's [PostgreSQL image](../../postgres/Dockerfile) or an equivalent image providing `roaringbitmap` and `pg_trgm`. The development parity tests compare actual catalog definitions and duplicate-sensitive scientific fixtures with the frozen main source. They are bounded development checks; loading has no mandatory exhaustive verification, rebuild-and-rollback run or automatic safety phase.

The historical resource-record implementation remains in internal modules for older schema tests and compatibility during migration; it is not the default CLI or package API. Existing historical schemas require their historical readers. See [the alignment plan](../../docs/postgres-main-parity-plan.md) for the migration boundary.
