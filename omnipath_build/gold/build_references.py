#!/usr/bin/env python3
"""
Build references table from silver_interactions files.

This module aggregates all unique references from silver_interactions
and creates the gold references table.

Usage:
    python build_references.py --data-root /path/to/data --output-dir /path/to/output
"""

import polars as pl
from pathlib import Path
from glob import glob
import argparse
import sys

__all__ = [
    'build_references',
    'main',
]


def build_references(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build references table from all silver_interactions files.

    Aggregates unique reference_value and reference_type pairs from all
    silver_interactions files and creates a references table.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        DataFrame with columns: id, identifier, citation, published_year,
                               journal, title, type_namespace_name, type_name
    """
    print("\nStep 1: Finding all silver_interactions files...")
    pattern = str(data_root / "*" / "*" / "silver" / "silver_interactions.parquet")
    parquet_files = glob(pattern)

    if not parquet_files:
        print("  No silver_interactions files found - creating empty references table")
        # Return empty DataFrame with correct schema
        return pl.DataFrame({
            "id": pl.Series([], dtype=pl.Int64),
            "identifier": pl.Series([], dtype=pl.Utf8),
            "citation": pl.Series([], dtype=pl.Utf8),
            "published_year": pl.Series([], dtype=pl.Int32),
            "journal": pl.Series([], dtype=pl.Utf8),
            "title": pl.Series([], dtype=pl.Utf8),
            "type_namespace_name": pl.Series([], dtype=pl.Utf8),
            "type_name": pl.Series([], dtype=pl.Utf8),
        })

    print(f"  Found {len(parquet_files)} silver_interactions files")

    print("\nStep 2: Extracting unique references...")
    # Collect all unique references
    all_references = []

    for file_path in parquet_files:
        df = pl.scan_parquet(file_path)

        # Extract unique (reference_value, reference_type) pairs
        # Filter out nulls
        references = df.select([
            pl.col("reference_value").alias("identifier"),
            pl.lit("OmniPath").alias("type_namespace_name"),
            pl.col("reference_type").alias("type_name"),
        ]).filter(
            pl.col("identifier").is_not_null()
        ).unique().collect()

        if len(references) > 0:
            all_references.append(references)
            print(f"  {Path(file_path).parent.parent.parent.name}: {len(references)} unique reference(s)")

    if not all_references:
        print("  No references found in any files - creating empty references table")
        return pl.DataFrame({
            "id": pl.Series([], dtype=pl.Int64),
            "identifier": pl.Series([], dtype=pl.Utf8),
            "citation": pl.Series([], dtype=pl.Utf8),
            "published_year": pl.Series([], dtype=pl.Int32),
            "journal": pl.Series([], dtype=pl.Utf8),
            "title": pl.Series([], dtype=pl.Utf8),
            "type_namespace_name": pl.Series([], dtype=pl.Utf8),
            "type_name": pl.Series([], dtype=pl.Utf8),
        })

    print("\nStep 3: Combining and deduplicating references...")
    # Combine all references and deduplicate
    combined = pl.concat(all_references).unique(
        subset=["identifier", "type_namespace_name", "type_name"]
    ).sort("identifier")

    # Add placeholder columns for citation metadata (null for now)
    result = combined.with_columns([
        pl.lit(None, dtype=pl.Utf8).alias("citation"),
        pl.lit(None, dtype=pl.Int32).alias("published_year"),
        pl.lit(None, dtype=pl.Utf8).alias("journal"),
        pl.lit(None, dtype=pl.Utf8).alias("title"),
    ])

    # Add id column (1-based index)
    result = result.with_row_index(name="id", offset=1)

    # Reorder columns
    result = result.select([
        "id",
        "identifier",
        "citation",
        "published_year",
        "journal",
        "title",
        "type_namespace_name",
        "type_name",
    ])

    print(f"  Total unique references: {len(result)}")

    # Show distribution by type
    if len(result) > 0:
        print("\n  Distribution by reference type:")
        type_dist = result.group_by("type_name").agg(
            pl.len().alias("count")
        ).sort("count", descending=True)

        for row in type_dist.iter_rows(named=True):
            print(f"    {row['type_name']}: {row['count']:,}")

    return result


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build references table from silver_interactions files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Build references table
    python build_references.py --data-root databases/omnipath/data --output-dir output/gold
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
    print("BUILD REFERENCES TABLE")
    print("=" * 70)
    print(f"Data root: {args.data_root}")
    print(f"Output directory: {args.output_dir}")

    # Build references table
    try:
        references = build_references(args.data_root, args.output_dir)

        # Save to output directory
        print("\nStep 4: Saving references table...")
        output_path = args.output_dir / "reference.parquet"
        references.write_parquet(output_path)
        print(f"  Saved to: {output_path}")

        # Print summary
        print("\n" + "=" * 70)
        print("Summary:")
        print("=" * 70)
        print(f"  Total references: {len(references)}")

        if len(references) > 0:
            print("\n  Sample references (first 5):")
            for row in references.head(5).iter_rows(named=True):
                print(f"    [{row['type_name']}] {row['identifier'][:60]}...")

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
