"""Command-line entry point for a single pinned PostgreSQL release."""

import argparse
from dataclasses import asdict
import json
import os

import duckdb
import psycopg

from .loader import load_release


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_manifest", help="Explicit release JSON with resource version pins")
    parser.add_argument("--data-root", required=True, help="Directory containing resources/")
    parser.add_argument("--database-url", default=os.environ.get("OMNIPATH_DATABASE_URL"))
    parser.add_argument("--schema", default="omnipath", help="New destination schema")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1024,
        help="Optional source-record audit batch (capped at 1024)",
    )
    parser.add_argument(
        "--validate-source-records",
        action="store_true",
        help="Audit discarded raw source records and their owner/provenance references",
    )
    parser.add_argument("--duckdb-threads", type=int, default=1)
    parser.add_argument("--memory-limit", default="512MB", help="DuckDB working-memory limit")
    parser.add_argument(
        "--temp-directory",
        help="Filesystem for CSV staging and DuckDB spills; defaults next to manifest",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("Set OMNIPATH_DATABASE_URL or pass --database-url")
    try:
        result = load_release(
            args.data_root,
            args.release_manifest,
            args.database_url,
            schema=args.schema,
            batch_size=args.batch_size,
            duckdb_threads=args.duckdb_threads,
            memory_limit=args.memory_limit,
            temp_directory=args.temp_directory,
            validate_source_records=args.validate_source_records,
        )
    except (ValueError, OSError, psycopg.Error, duckdb.Error) as exc:
        parser.exit(1, f"Release load failed: {exc}\n")
    print(json.dumps(asdict(result), indent=2))
    return 0
