#!/usr/bin/env python3
"""
Build sources table from silver_entities files.

This module aggregates all unique source databases from silver_entities
and creates the gold sources table.

Usage:
    python build_sources.py --data-root /path/to/data --output-dir /path/to/output
"""

import polars as pl
from pathlib import Path
from glob import glob
import argparse
import sys

__all__ = [
    'build_sources',
    'main',
]


def build_sources(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build sources table from all silver_entities files.

    Aggregates unique source_database values from all silver_entities files
    and creates a sources table with name, url, and description columns.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        DataFrame with columns: id, name, url, description
    """
    print("\nStep 1: Finding all silver_entities files...")
    pattern = str(data_root / "*" / "*" / "silver" / "silver_entities.parquet")
    parquet_files = glob(pattern)

    if not parquet_files:
        raise FileNotFoundError(f"No silver_entities files found at {pattern}")

    print(f"  Found {len(parquet_files)} silver_entities files")

    print("\nStep 2: Extracting unique source databases...")
    # Collect all unique source databases
    all_sources = []

    for file_path in parquet_files:
        df = pl.scan_parquet(file_path)

        # Extract unique source_database values from this file
        sources = df.select([
            pl.col("source_database").alias("name")
        ]).unique().collect()

        all_sources.append(sources)
        print(f"  {Path(file_path).parent.parent.parent.name}: {len(sources)} unique source(s)")

    print("\nStep 3: Combining and deduplicating sources...")
    # Combine all sources and deduplicate
    combined = pl.concat(all_sources).unique(subset=["name"]).sort("name")

    # Add url and description columns (null for now, can be populated later)
    result = combined.with_columns([
        pl.lit(None, dtype=pl.Utf8).alias("url"),
        pl.lit(None, dtype=pl.Utf8).alias("description")
    ])

    # Add id column (1-based index)
    result = result.with_row_index(name="id", offset=1)

    # Reorder columns: id, name, url, description
    result = result.select(["id", "name", "url", "description"])

    print(f"  Total unique sources: {len(result)}")

    return result


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build sources table from silver_entities files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Build sources table
    python build_sources.py --data-root databases/omnipath/data --output-dir output/gold
        """
    )

    parser.add_argument(
        '--data-root',
        type=Path,
        default=Path("databases/omnipath/data"),
        help='Path to data directory containing silver files (default: databases/omnipath/data)'
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path("output/gold"),
        help='Path to output directory for gold tables (default: output/gold)'
    )

    args = parser.parse_args()

    # Validate data root exists
    if not args.data_root.exists():
        print(f"Error: Data root not found: {args.data_root}", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("BUILD SOURCES TABLE")
    print("=" * 70)
    print(f"Data root: {args.data_root}")
    print(f"Output directory: {args.output_dir}")

    # Build sources table
    try:
        sources = build_sources(args.data_root, args.output_dir)

        # Save to output directory
        print("\nStep 4: Saving sources table...")
        output_path = args.output_dir / "source.parquet"
        sources.write_parquet(output_path)
        print(f"  Saved to: {output_path}")

        # Print summary
        print("\n" + "=" * 70)
        print("Summary:")
        print("=" * 70)
        print(f"  Total sources: {len(sources)}")
        print("\n  Sources list:")
        for row in sources.iter_rows(named=True):
            print(f"    {row['id']}: {row['name']}")

        print("\n" + "=" * 70)
        print("DONE")
        print("=" * 70)

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
