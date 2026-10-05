# Runtime matching policy

`build-library` constructs anchor-based entities from hubs, compiles the compact reference, applies the maximum-ten-candidates admission policy, archives excluded keys in `ambiguous.parquet`, builds the independently supported gene component, retires temporary compiler inputs, and atomically switches `current`.

The published generation contains two base indexes and a required gene component:

- `identifiers/`: normalized lookup key to zero or more candidate entities and their decision metadata.
- `entities/`: entity ID to taxon, preferred label, and complete precomputed identifier list.
- `gene-role-index/identifiers/`: supported gene-role candidates from direct gene claims and explicit, uniquely linked products, with organism and ambiguity guards.
- `gene-role-index/records/`: justified gene aliases and labels removed by the earlier product-count cutoff; sequence-specific aliases and protein records stay separate.

`LibraryMatcher` reads gene-role keys through the verified component and other keys through the base index. The Rust kernel resolves gene and product evidence without inferring a protein from a gene. Accepted records come from the base entity index, with gene aliases restored by the record component. No Parquet scan or join occurs during resolution. The component binds the exact base manifest and source checksums; build provenance records its identity. Missing required or damaged components fail rather than silently falling back.

```sh
export PYPATH_DOWNLOAD_DATADIR="$PWD/data/pypath-data"
omnipath-build export-hubs --output-dir data/reference/hubs --max-records 0 --no-library
omnipath-build build-library --hubs-dir data/reference/hubs --output-dir data/reference/library
```

The assigned Parquet catalogue is a temporary compiler input and is deleted after successful publication. `anchor-components` remains a build dependency for entity assignment. See [the full resolver design](../../../../docs/reference-resolver.md).

Runtime matching stays in `omnipath_resolver`: it imports neither parsers nor
reference compilers. `omnipath_build.reference` owns construction and publication.
