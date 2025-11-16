"""CLI for building Meilisearch search entities from global tables."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .build_search_entities import build_search_entities


def main():
    """Run the search entity builder from command line."""
    parser = argparse.ArgumentParser(
        description="Build Meilisearch entity documents from global tables"
    )
    parser.add_argument(
        "--global-tables-dir",
        type=Path,
        required=True,
        help="Directory containing global table parquet files (entity.parquet, etc.)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("search_entities.parquet"),
        help="Output path for search entities parquet file (default: search_entities.parquet)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Run the builder
    output_path = build_search_entities(
        global_tables_dir=args.global_tables_dir,
        output_path=args.output,
    )

    print(f"\nSearch entities written to: {output_path}")
    print(f"Total size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
