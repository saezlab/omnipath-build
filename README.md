# OmniPath build

Build independently versioned, resolved Parquet resources from pypath `inputs_v2`.
This migration carries over the prototype's schemas, Biolink mappings and entity
resolution. PostgreSQL loading and product extraction will consume these resolved
artifacts in later milestones.

## Packages

- `packages/omnipath_core`: shared schemas, vocabulary, identity and version metadata.
- `packages/omnipath_resolver`: native resolution policy and reference kernels.
- `packages/omnipath_build`: reference preparation, resource builds and publication.
- `pypath/`: source parsers and mappings, on the `parquet-migration` branch.
- `legacy/postgres/`: the previous PostgreSQL implementation and tests, retained
  for migration into `omnipath_postgres` and `omnipath_subsets`.

The active workspace excludes the legacy package so imports use the new pipeline.
API, web and client packages are outside this first milestone.

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
The pypath migration branch starts from `silver_schema_improvement` at
`51aedf4a0f37ef66dce31274d614866a0f04ebb2`.
