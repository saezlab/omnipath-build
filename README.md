# OmniPath build

Build resolved, independently versioned Parquet resources from pypath `inputs_v2`.
Serve those files through the API and explorer, or load an explicitly pinned
release into PostgreSQL. Entity resolution happens before Parquet publication.
API Latest and PostgreSQL's monthly snapshots have independent schedules.

## Setup

For development, install Python 3.11+, uv, Rust, Node.js 22 and pnpm, then run:

```sh
make setup
make help
```

Setup uses the locked dependencies and committed submodule revisions, and compiles
the native reference helper (`make native-reference` to compile it separately). A serving
container needs no resolver, reference rebuild or sibling prototype checkout.

## Run existing data

```sh
make dev DATA_ROOT=/absolute/path/to/existing/data
# API: http://127.0.0.1:8085; explorer: http://127.0.0.1:5173
```

For containers, set an external artifact directory in an environment file:

```sh
cp deploy/serving.env.example deploy/serving.env
# Edit DATA_DIR and the public URLs in deploy/serving.env.
make serving-build COMPOSE_ENV=deploy/serving.env
make serving-up COMPOSE_ENV=deploy/serving.env
make serving-status COMPOSE_ENV=deploy/serving.env
```

These commands start only the read-only API, web app and file service. Existing
Parquets, serving indexes, references and caches can remain outside the checkout.
See [deployment](deploy/README.md) for HTTPS routing, parallel cutover and rollback.

## Build and publish resources

For a small offline example, use `make sample`; it processes at most 20 fixture
records under `data/migration-smoke/`. It does not download source datasets.

A real build uses an existing reference library:

```sh
OMNIPATH_LIBRARY_DIR=/path/to/reference/library \
  make build SOURCE=signor VERSION=1.0.0 MAX_RECORDS=20 DATA_ROOT=data
```

The limit applies per dataset; a source parser may still download its full input.
Versions are immutable. Prepare hubs and the reference only when needed, using
`make hubs HUB_ARGS="..."` and `make reference REFERENCE_ARGS="..."`; see the
[resource pipeline](packages/omnipath_build/README.md),
[reference guide](docs/reference-resolver.md) and
[orchestration guide](packages/omnipath_build/ORCHESTRATION.md).

A named snapshot lists exact resource versions:

```json
{"schema_version": 1, "version": "2026.09", "resources": {"signor": "1.0.0"}}
```

Publish it from existing local artifacts with
`make publish-release RELEASE_MANIFEST=release.json DATA_ROOT=data`.
Publication validates availability and adds a cached taxonomy reference when
provisioned. It runs no source build. Optional acceleration indexes are prepared
separately with `make serving-indexes DATA_ROOT=data`; existing matching indexes
are reused. API Latest continues selecting each resource's newest version.

## PostgreSQL and products

Use PostgreSQL 18 with `roaringbitmap` and `pg_trgm`; the image definition is in
[postgres/](postgres/). Set `OMNIPATH_DATABASE_URL` for the intended database.
Load a new schema from a local directory or an HTTPS endpoint:

```sh
make load-postgres RELEASE_MANIFEST=release.json DATA_ROOT=/path/to/data \
  POSTGRES_SCHEMA=release_2026_09

make load-postgres \
  RELEASE_MANIFEST=https://data.example.org/releases/2026.09.json \
  DATA_ROOT=https://data.example.org POSTGRES_SCHEMA=release_2026_09
```

The release is explicit; the loader never substitutes Latest or repeats entity
resolution. HTTP input requires byte-range support. Checksums, schemas, sizes
and row counts are checked. DuckDB stages entity/relation inputs locally once,
then projects relational tables and streams CSV bytes through PostgreSQL COPY.
No persistent downloaded-Parquet cache is kept. Working storage can be selected
with `POSTGRES_ARGS="--temp-directory /path/to/spool --memory-limit 4GB"`.

The validated base commits before downstream work. Completed phases and products
are durable; retrying skips them. Raw source payloads and nested record JSON stay
in Parquet. Main's relational tables and indexes hold the scientific data.

```sh
# Optional: import only the base first.
make load-postgres RELEASE_MANIFEST=release.json DATA_ROOT=data \
  POSTGRES_SCHEMA=release_2026_09 POSTGRES_ARGS=--base-only
# Finish remaining derivations and products without reading Parquet or repeating COPY.
make finish-postgres POSTGRES_SCHEMA=release_2026_09
make build-subsets POSTGRES_SCHEMA=release_2026_09 SUBSET_PRODUCTS=cosmos
```

See the [PostgreSQL guide](packages/omnipath_postgres/README.md) for schema,
checkpoint and product details. The historical `omnipath-subsets` CLI is for the
older resource-record schemas; current main-layout builds use PostgreSQL's
integrated product pipeline.

## Packages and checks

| Package | Responsibility |
| --- | --- |
| [core](packages/omnipath_core/README.md) | Schemas, vocabulary, IDs and versions |
| [resolver](packages/omnipath_resolver/README.md) | Reference indexes and identifier matching |
| [build](packages/omnipath_build/README.md) | Reference preparation and resource publication |
| [postgres](packages/omnipath_postgres/README.md) | Main relational layout, indexes, derivations and products |
| [api](packages/omnipath_api/README.md) | Parquet queries, inspection and exports |
| [web](packages/omnipath_web/README.md) | Explorer and API proxy |

The root Makefile delegates to package commands; implementation stays inside
packages. `make check`, `make test`, `make check-web` and `make test-api` cover
local checks. PostgreSQL fixtures require explicit `make test-postgres`.
The [versioning policy](VERSIONING.md) describes independent publication schedules.
The Python client remains a later milestone; `legacy/` is excluded from the
active workspace.
