# OmniPath build

Build independently versioned, resolved Parquet resources from pypath `inputs_v2`.
This migration carries over the prototype's schemas, Biolink mappings and entity
resolution. PostgreSQL loads pinned releases of these resolved artifacts;
product adapters derive MetSigDB, network presets and COSMOS from that loaded release.

## Packages

- `packages/omnipath_core`: shared schemas, vocabulary, identity and version metadata.
- `packages/omnipath_resolver`: native resolution policy and reference kernels.
- `packages/omnipath_build`: reference preparation, resource builds and publication.
- `packages/omnipath_postgres`: pinned release loading, query indexes, ontology closure and reaction contexts.
- `packages/omnipath_subsets`: MetSigDB, network presets and COSMOS adapters.
- `packages/omnipath_api`: DuckDB queries, resource inspection and exports over Parquet.
- `packages/omnipath_web`: SvelteKit explorer and API proxy.
- `pypath/`: source parsers and mappings, on the `parquet-migration` branch.
- `legacy/postgres/`: the previous PostgreSQL implementation and tests, retained
  for comparison while validating downstream compatibility.

The active workspace excludes the legacy package so imports use the new pipeline.
The Parquet API and web explorer are integrated. The Python client remains a later milestone.

## Setup

Install Python 3.11 or newer, uv and a Rust toolchain, then run:

```sh
git submodule update --init --recursive
uv sync --frozen --all-packages
```

The lockfile resolves pypath from this repository's submodule and shared core from
this workspace. No sibling prototype checkout is needed. Submodule updates use
committed revisions; they do not advance remote branches.

## Bounded offline milestone check

```sh
make sample
```

The sample uses a small local SIGNOR fixture and a synthetic compact reference.
It exercises the real parser, RelationBuilder, native resolver, Parquet pipeline,
immutable version publication and DuckDB inspection without downloading datasets.
It processes at most 20 source records and writes artifacts plus an inspection
report under `data/migration-smoke/`. For another run, choose a new immutable
version with `make sample VERSION=0.1.1`.

## PostgreSQL release loading

Create a release manifest with exact resource versions:

```json
{"schema_version": 1, "version": "2026.09", "resources": {"signor": "0.1.1"}}
```

Then load the existing bounded sample into a fresh schema:

```sh
export OMNIPATH_DATABASE_URL='postgresql:///omnipath'
make load-postgres RELEASE_MANIFEST=release.json \
  DATA_ROOT=data/migration-smoke POSTGRES_SCHEMA=release_2026_09
```

The loader verifies artifact schemas and checksums, streams the resolved records
through DuckDB and copies them into PostgreSQL. Tables, indexes, ontology and
reaction derivations, counts and release metadata commit together; a failed load leaves no partial
schema. Existing schemas cannot be overwritten. Loading does not run parsers
or identifier resolution.

For a disposable local PostgreSQL integration check:

```sh
make test-postgres
# If needed: OMNIPATH_POSTGRES_BIN=/path/to/postgresql/bin make test-postgres
```

These tests use small local fixtures and the existing 20-record sample. They
start a temporary Unix-socket-only cluster and shut it down afterward.

Full published keys, duplicate evidence occurrences, quantities, qualifiers,
original payload text and per-resource records are preserved. Canonical views
combine identical keys, aggregate evidence and expose consensus taxa. Identifier
lookup, graph relation counts, ontology closure, source counts,
resource overlap and source-scoped reaction participants are implemented.
Representative small fixtures validate the adapters; full product comparisons
and web query benchmarks remain.

See the [PostgreSQL package](packages/omnipath_postgres/README.md).

## Parquet API and web explorer

```sh
pnpm --dir packages/omnipath_web install --frozen-lockfile
OMNIPATH_DATA_ROOT=data/migration-smoke ./run_all.sh
```

This starts the local API on port 8085 and explorer on port 5173, using the
existing bounded sample. Alternatively, run `make api DATA_ROOT=data/migration-smoke`
and `make web` in separate terminals.

The API default is Latest: each resource's newest published Parquet version.
Resources update independently. Optional named Parquet snapshots retain their
existing pins. PostgreSQL monthly snapshots are built separately and do not
control which versions the API or explorer serves. Existing versioning behavior
is preserved; see [versioning](VERSIONING.md).

The explorer includes search, filtering, details, evidence, ontology navigation,
selection and exports. Serving defaults to read-only; resource builds require
the separately configured worker. Starting this stack runs no source builds or
PostgreSQL loads.

See the [API](packages/omnipath_api/README.md) and
[explorer](packages/omnipath_web/README.md). Use `make test-api` for API fixtures;
web checks are listed in the explorer README. See the
[serving milestone](docs/parquet-serving-milestone.md) for verification and the
real fixture browser check.

## Products from a loaded release

```sh
make build-subsets POSTGRES_SCHEMA=release_2026_09
# Or choose one product:
make build-subsets POSTGRES_SCHEMA=release_2026_09 SUBSET_PRODUCTS=metsigdb
make test-subsets
```

Products read published entities, aliases and evidence from PostgreSQL. A build
records the release ID and exact manifest digest; all selected products commit
together. A failed rebuild preserves the previous product tables.

MetSigDB uses published chemical entities, including fallback identities. It
does not repeat the legacy matched-only filter or change Parquet. COSMOS keeps
source reaction events separate. Network presets preserve qualified statement
keys and report missing resources and compatibility limits.

See the [subset package](packages/omnipath_subsets/README.md) and
[milestone notes](docs/parquet-subsets-milestone.md).

## Resource builds

For a real source, supply a prepared reference from the prototype resolver:

```sh
OMNIPATH_LIBRARY_DIR=/path/to/reference/library \
  make build SOURCE=signor VERSION=0.1.0 MAX_RECORDS=20 DATA_ROOT=data
```

`MAX_RECORDS` defaults to 20 and applies per dataset. Some upstream parsers may
prepare a complete download before yielding records; the record cap limits
processing, not download size. The offline sample avoids this preparation.
Successful builds publish under `data/resources/<source>/<version>/`, with the
three Parquet tables, resolution diagnostics and a build manifest. Published
resource versions cannot be overwritten.

See the [resource pipeline](packages/omnipath_build/README.md),
[reference resolver](docs/reference-resolver.md) and
[orchestration guide](packages/omnipath_build/ORCHESTRATION.md).

## Checks

```sh
make check
cargo build --release --locked \
  --manifest-path packages/omnipath_resolver/rust/reference/Cargo.toml \
  --features parquet-input --bins
make test
make test-pypath
```

The reference binary supports the imported offline fixture tests. Unit tests use
local fixtures; integration tests require explicit opt-in.

## Migration provenance

The three workspace packages were imported from
`omnipath-metabo-dev-prototype` at revision
`86f978e39bd1f7a40180714acf03747f3426e87c`.
The API and web explorer were imported from the same prototype revision.
The pypath migration branch starts from `silver_schema_improvement` at
`51aedf4a0f37ef66dce31274d614866a0f04ebb2`.
