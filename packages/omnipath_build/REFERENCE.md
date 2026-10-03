# Reference build and resolver

The production reference is one compact, immutable generation built from one set of hubs. Entity assignment uses chemical anchors such as full InChIKey and primary UniProt accessions, plus the documented gene and source-specific rules. The temporary assigned catalogue is compiled into:

1. an identifier index from normalized lookup keys to complete candidate lists;
2. an entity index from entity IDs to taxon, preferred label, and all admitted identifiers.

Identifiers with more than ten candidates are absent from the runtime index and recorded with their candidates in `ambiguous.parquet`. Resource resolution reads the identifier index, runs the shared Rust decision policy, and reads accepted entity records. Alias enrichment is already stored on disk.

From the repository root, `make setup` installs dependencies and compiles the
`anchor-components` helper. To compile only that helper, use `make native-reference`;
it does not build a reference or process resources. The worker image includes it.

```sh
export PYPATH_DOWNLOAD_DATADIR="$PWD/data/pypath-data"
omnipath-build export-hubs --output-dir data/reference/hubs --max-records 0 --no-library
omnipath-build build-library --hubs-dir data/reference/hubs --output-dir data/reference/library
```

Publication switches `data/reference/library/current` only after all 512 LMDB shards, dictionaries, candidate policy, and manifest have completed. Compiler inputs are deleted after publication. Workers need the published compact generation; they do not need the assigned Parquet catalogue.

See the [entity resolution guide](src/omnipath_build/canonical/README.md) for the runtime index layout and matching flow.
