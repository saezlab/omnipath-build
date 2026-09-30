# Entity resolution

`build-library` constructs anchor-based entities from hubs, compiles the compact reference, applies the maximum-ten-candidates admission policy, archives excluded keys in `ambiguous.parquet`, removes all compiler inputs, and atomically switches `current`.

The published generation contains two sharded indexes:

- `identifiers/`: normalized lookup key to zero or more candidate entities and their decision metadata.
- `entities/`: entity ID to taxon, preferred label, and complete precomputed identifier list.

`LibraryMatcher` creates lookup keys from observation evidence and reads the first index. The Rust kernel chooses entities. The matcher and final resource writer read accepted records from the second index. No enrichment computation or temporary Parquet batch occurs at runtime.

```sh
export PYPATH_DOWNLOAD_DATADIR="$PWD/data/pypath-data"
omnipath-build export-hubs --output-dir data/reference/hubs --max-records 0 --no-library
omnipath-build build-library --hubs-dir data/reference/hubs --output-dir data/reference/library
```

The assigned Parquet catalogue is a temporary compiler input and is deleted after successful publication. `anchor-components` remains a build dependency for entity assignment. See the [reference build guide](../../../REFERENCE.md).
