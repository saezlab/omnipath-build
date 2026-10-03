"""Explicit historical record-layout command for audited compatibility callers.

The production omnipath-postgres command uses the normalized current schema.
This module preserves the earlier load/audit/checkpoint options for fixtures and
callers that explicitly import the historical API.
"""

import argparse
from dataclasses import asdict
import json
import os

import duckdb
import psycopg

from .loader import finish_release, load_release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_manifest", nargs="?")
    parser.add_argument("--data-root")
    parser.add_argument("--database-url", default=os.environ.get("OMNIPATH_DATABASE_URL"))
    parser.add_argument("--schema", default="omnipath")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--finish", action="store_true")
    modes.add_argument("--checkpoint-base", action="store_true")
    parser.add_argument("--validate-source-records", action="store_true")
    parser.add_argument("--defer-constraints", action="store_true")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--duckdb-threads", type=int)
    parser.add_argument("--memory-limit")
    parser.add_argument("--temp-directory")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("Set OMNIPATH_DATABASE_URL or pass --database-url")
    if args.finish and (
        args.release_manifest is not None
        or args.data_root is not None
        or args.validate_source_records
        or args.defer_constraints
        or args.batch_size is not None
        or args.duckdb_threads is not None
        or args.memory_limit is not None
        or args.temp_directory is not None
    ):
        parser.error("--finish accepts only database/schema selection")
    if not args.finish and (args.release_manifest is None or args.data_root is None):
        parser.error("Loading requires release_manifest and --data-root")
    try:
        if args.finish:
            result = finish_release(args.database_url, schema=args.schema)
        else:
            result = load_release(
                args.data_root,
                args.release_manifest,
                args.database_url,
                schema=args.schema,
                batch_size=1024 if args.batch_size is None else args.batch_size,
                duckdb_threads=1 if args.duckdb_threads is None else args.duckdb_threads,
                memory_limit=args.memory_limit or "512MB",
                temp_directory=args.temp_directory,
                validate_source_records=args.validate_source_records,
                checkpoint_base=args.checkpoint_base,
                defer_constraints=args.defer_constraints,
            )
    except (ValueError, OSError, psycopg.Error, duckdb.Error) as exc:
        parser.exit(1, f"Release load failed: {exc}\n")
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
