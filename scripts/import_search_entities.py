#!/usr/bin/env python3
"""
Utility to load the search_entities_final.parquet dataset into Meilisearch
using the upstream meilisearch-importer CLI directly in Parquet mode.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Iterable

from omnipath_build.meilisearch_settings import MeilisearchSettings

def run_importer(
    importer_dir: Path,
    dataset_path: Path,
    meili_url: str,
    index_name: str,
    primary_key: str,
    api_key: str | None,
    batch_size: str | None,
    file_format: str,
) -> None:
    """Execute the meilisearch importer with the generated JSON file."""
    dataset_arg = str(dataset_path.resolve())
    cmd: list[str] = [
        "cargo",
        "run",
        "--release",
        "--",
        "--url",
        meili_url,
        "--index",
        index_name,
        "--primary-key",
        primary_key,
        "--files",
        dataset_arg,
        "--format",
        file_format,
    ]

    if api_key:
        cmd.extend(["--api-key", api_key])

    if batch_size:
        cmd.extend(["--batch-size", batch_size])

    subprocess.run(cmd, cwd=str(importer_dir), check=True)


def apply_settings(
    meili_url: str,
    index_name: str,
    api_key: str | None,
    settings: dict,
) -> None:
    payload = json.dumps(settings).encode("utf-8")
    req = urllib.request.Request(
        url=f"{meili_url}/indexes/{index_name}/settings",
        data=payload,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        if resp.status >= 400:
            raise RuntimeError(f"Failed to update settings: {resp.status} {resp.read()}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parquet-path",
        default="databases/omnipath/output/search_entities.parquet",
        type=Path,
        help="Path to search_entities.parquet.",
    )
    parser.add_argument(
        "--importer-path",
        default=Path("/Users/jschaul/Downloads/meilisearch-importer-main"),
        type=Path,
        help="Location of the meilisearch-importer checkout.",
    )
    parser.add_argument(
        "--meili-url",
        default="http://localhost:7700",
        help="URL of the running Meilisearch instance.",
    )
    parser.add_argument(
        "--index",
        default="search_entities",
        help="Name of the Meilisearch index that should receive the documents.",
    )
    parser.add_argument(
        "--primary-key",
        default="entity_id",
        help="Primary key field name for the Meilisearch index.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional Meilisearch API key (overrides MEILISEARCH_API_KEY).",
    )
    parser.add_argument(
        "--batch-size",
        default="100MB",
        help="Batch size forwarded to the importer CLI (e.g. '50MB').",
    )
    parser.add_argument(
        "--format",
        default="parquet",
        choices=("parquet", "ndjson", "json", "csv"),
        help="Input format passed directly to meilisearch-importer.",
    )
    parser.add_argument(
        "--skip-settings",
        action="store_true",
        help="Skip updating Meilisearch settings after the import.",
    )

    return parser.parse_args(argv)


def main(argv: Iterable[str]) -> None:
    args = parse_args(argv)
    parquet_path: Path = args.parquet_path
    importer_path: Path = Path(args.importer_path)
    api_key = args.api_key or os.environ.get("MEILISEARCH_API_KEY")

    if not parquet_path.exists():
        raise SystemExit(f"Missing Parquet input file: {parquet_path}")
    parquet_path = parquet_path.resolve()

    if not importer_path.exists():
        raise SystemExit(f"Invalid importer path: {importer_path}")

    print(f"Importing {parquet_path} into Meilisearch ...")
    run_importer(
        importer_dir=importer_path,
        dataset_path=parquet_path,
        meili_url=args.meili_url,
        index_name=args.index,
        primary_key=args.primary_key,
        api_key=api_key,
        batch_size=args.batch_size,
        file_format=args.format,
    )
    if not args.skip_settings:
        print("Applying index settings ...")
        apply_settings(
            meili_url=args.meili_url,
            index_name=args.index,
            api_key=api_key,
            settings=MeilisearchSettings.ENTITIES_SETTINGS,
        )
    print("Import completed successfully.")


if __name__ == "__main__":
    main(sys.argv[1:])
