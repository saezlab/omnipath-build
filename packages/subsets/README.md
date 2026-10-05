# OmniPath subsets

Build MetSigDB memberships, network presets and COSMOS reaction networks from
one fixed PostgreSQL release loaded by omnipath-postgres. Products use published
identities and aliases; no resource builds or identifier resolution run.

## Build selected products

```sh
export OMNIPATH_DATABASE_URL='postgresql:///omnipath'
uv run --frozen omnipath-subsets build --schema release_2026_09
uv run --frozen omnipath-subsets build --schema release_2026_09 --products metsigdb
```

The command requires exactly one release in the target schema. It takes the
same schema advisory lock as the loader, rebuilds the selected products in one
transaction and records their counts, release ID and canonical manifest SHA-256
in `subset_build_metadata`. A failure rolls back every selected rebuild,
including changes to existing products. The Python entry point is
`omnipath_subsets.build.build_subsets`.

For long product builds, add `--checkpoint-products` (Python:
`checkpoint_products=True`). Each product's tables and metadata commit together;
a later failure or cancellation rolls back only the current product. A session
advisory lock spans all commits, and each product checks the same single release
ID and manifest digest before and after its work. Resume by selecting only the
remaining products with `--products`; existing metadata is not skipped
implicitly.

```sh
uv run --frozen omnipath-subsets build --schema release_2026_09 --checkpoint-products
uv run --frozen omnipath-subsets build --schema release_2026_09 --checkpoint-products --products network_views cosmos
```

The optional Python `on_product_committed(product, result)` callback requires
checkpoint mode and runs only after the durable commit. `result` is a
`BuildResult` snapshot containing release identity and the stats/timings of
products committed by this call. A callback failure stops further builds while
preserving that commit; `subset_build_metadata` is authoritative if observer
persistence fails.

The maintained scientific implementations live in `omnipath_subsets.metsigdb`,
`omnipath_subsets.network_views` and `omnipath_subsets.cosmos`. The standalone
command and PostgreSQL importer share `omnipath_subsets.runner`; they publish
`subset_build_metadata` and `parquet_phase` in the same product transaction.
Explicit selection rebuilds each product. Use `omnipath-postgres --finish` to
skip products already committed for the loaded monthly release.

## Products

- **MetSigDB:** metabolite memberships in Reactome, WikiPathways, KEGG, MACdb
  and ClassyFire sets, read from the normalized entity, evidence and ontology tables.
- **Network views:** the MetaLinksDB, LIANA and Reactions preset definitions and
  registry. Registering a preset does not create a materialized network; consumers
  apply the stored recipe.
- **COSMOS:** metabolite/enzyme edges projected from the shared reaction tables,
  including reversible reactions and labels drawn from published identifiers.
  GeneID-centered catalysts keep their GeneID label and `entrez` namespace,
  including records whose source reported a protein. UniProt catalogue aliases
  of a gene do not select a protein participant or create extra reaction nodes.
  Canonical UniProt product identities keep their UniProt labels. The optional
  legacy utils translation also preserves GeneID and accepts other catalyst
  mappings only when exactly one UniProt target answers. Connectors use the
  same identifier and namespace as their reaction node.

All three products use the current PostgreSQL schema. The earlier resource-record
subset implementations and their verification command have been removed.

## Validation scope

```sh
make test-subsets
```

Tests use disposable PostgreSQL and bounded synthetic fixtures. Current product
parity tests under `packages/postgres/tests` compare complete rows, scientific
semantics, preset definitions and database objects with frozen reference code.
Runner tests cover release identity, locking, durable product checkpoints,
rollback, resume and explicit rebuild. These developer tests do not add a
verification or rebuild phase to production runs.
