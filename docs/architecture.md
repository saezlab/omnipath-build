# Current architecture

Resource builds resolve entities before publication. Each immutable resource
version contains entities, relations, source payloads and a build manifest.
Named Parquet releases pin resource versions; API Latest follows independent
resource updates. PostgreSQL chooses its own explicit release on its monthly schedule.

| Owner | Responsibility |
| --- | --- |
| core | Published schemas, vocabulary, identities, manifest contracts and version validation |
| resolver | Runtime matching, normalization, index lookup, enrichment and the native policy kernel |
| build | Inputs, reference construction, resource assembly and atomic publication |
| postgres | Pinned local/HTTPS input, DuckDB projection, COPY, relational schema/indexes and shared derivations |
| subsets | MetSigDB, network views, COSMOS and their shared transaction/checkpoint runner |
| api | Inventory snapshots, bounded DuckDB queries, exports and optional job adapters |
| web | Explorer, generated HTTP/vocabulary types and shipped documentation |

The package dependency direction is postgres -> subsets -> core; subsets does
not import PostgreSQL build internals. Build depends on resolver and core.
Serving requires core and API only; a separately installed worker also uses build.
Public Python names remain `omnipath_*`; outer project folders use short names.
Only Python's extra `src` level was removed; Rust and Svelte use their usual layout.

PostgreSQL never repeats entity resolution. It stages resolved entity/relation
Parquets in DuckDB, projects the established relational tables, streams COPY,
restores constraints/indexes, then commits the base. Shared derivations and each
complete subset have durable checkpoints. The standalone subsets command explicitly
rebuilds selected products. PostgreSQL finish resumes unfinished phases/products.
Both use one runner with release identity checks and a schema lock across product
commits. Products publish their tables and metadata atomically.

Frozen legacy source is an independent test oracle. The earlier PostgreSQL loader
remains under `omnipath_postgres.compatibility.record_layout`; subsets has only the
current implementation. Normal commands use the current layout.
Developer fixture tests and wheel checks are separate from production execution;
there is no mandatory full-release rebuild/rollback verification phase.

Artifacts can live on another server: PostgreSQL accepts an explicit HTTPS release
and file root with byte ranges. It hashes artifacts before loading and again before
the base checkpoint; projection reads add traffic. The API currently inventories a
local mounted artifact directory. Public download URLs need not be on that server.
The Parquet contract is the boundary between systems, not a shared database or cache.

Start from [the root workflow](../README.md), [reference methods](reference-resolver.md),
[PostgreSQL](../packages/postgres/README.md) or [serving operations](../deploy/README.md).
The numbered [old pipeline notes](pipeline/README.md) are historical.
