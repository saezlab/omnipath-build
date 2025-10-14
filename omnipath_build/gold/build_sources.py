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
]


def build_sources(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build sources table from all silver_entities files.

    Aggregates unique source values from all silver_entities files
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

        # Extract unique source values from this file
        sources = df.select([
            pl.col("source").alias("name")
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
