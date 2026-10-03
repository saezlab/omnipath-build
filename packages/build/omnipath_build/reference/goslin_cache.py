"""Persistent exact-name results, isolated by normalization code and grammar content."""

from __future__ import annotations

import argparse
from pathlib import Path

from omnipath_resolver.goslin_cache import NormalizationCache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-parquet", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--expected-fingerprint")
    args = parser.parse_args()
    import json
    import pyarrow.parquet as pq
    from omnipath_resolver.goslin import normalize

    count = 0
    with NormalizationCache(args.cache_dir, normalize) as cache:
        if args.expected_fingerprint and cache.fingerprint != args.expected_fingerprint:
            raise RuntimeError("Normalizer or grammars changed since parsing began")
        for batch in pq.ParquetFile(args.import_parquet).iter_batches(batch_size=4096):
            cache.store(batch.to_pylist())
            count += batch.num_rows
            if count % 65536 == 0:
                print(
                    json.dumps({"event": "goslin_cache_import", "names": count}),
                    flush=True,
                )
        print(
            json.dumps(
                {
                    "event": "goslin_cache_import_complete",
                    "names": count,
                    "cache": str(cache.path),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
