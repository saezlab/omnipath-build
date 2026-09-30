# Compact resolver kernel

The extension applies the biological decision policy to values fetched from the compact two-index reference. Python reads candidate postings for normalized identifier keys, Rust resolves conflicts and gene-to-protein expansion, and Python fetches the accepted entity records. Runtime resolution does not scan or create Parquet files.

Full InChIKeys take precedence for chemicals. Protein accessions remain separate. Gene identifiers may map to several protein entities; the kernel preserves valid fan-out and prefers reviewed products where the policy requires it. Taxon-qualified symbols retain their organism scope. Conflicting evidence remains unresolved.

Build and verify:

```sh
uv sync --frozen --all-packages
cargo test --locked --manifest-path packages/omnipath_resolver/Cargo.toml
cargo test --locked --manifest-path packages/omnipath_resolver/rust/reference/Cargo.toml --features parquet-input
uv run pytest packages/omnipath_build/tests/test_resolver.py packages/omnipath_build/tests/test_compact_index.py
```

The `parquet-input` feature remains because `anchor-components` reads assignment edges while building entities from hubs. It is not a runtime resolver path.
