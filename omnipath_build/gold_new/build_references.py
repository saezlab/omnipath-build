#!/usr/bin/env python3
"""
Build references table from silver files.

This module aggregates all unique references from silver_entities and
silver_interactions files and creates the gold references table.

References are structured as:
- type: reference type (e.g., 'pubmed', 'doi', 'pmc')
- value: the actual reference identifier

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
]


def build_references(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build references table from all silver files (both entities and interactions).

    Aggregates unique reference (type, value) pairs from all silver files
    and creates a references table with minimal columns.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        DataFrame with columns: id, type, value (type is CV term accession string)
    """
    print("\nStep 1: Finding all silver files...")
    # Silver files are in data/source/function_name.parquet
    pattern = str(data_root / "*" / "*.parquet")
    parquet_files = glob(pattern)

    if not parquet_files:
        raise FileNotFoundError(f"No silver files found at {pattern}")

    print(f"  Found {len(parquet_files)} silver files")

    print("\nStep 2: Extracting all references from silver files...")
    all_references = []

    for file_path in parquet_files:
        df = pl.scan_parquet(file_path)

        # Check if the file has a 'references' column
        try:
            # Extract references - unnest the list of structs
            refs = df.select([
                pl.col("references")
            ]).explode("references").select([
                pl.col("references").struct.field("type").alias("type_name"),
                pl.col("references").struct.field("value").alias("value")
            ]).collect()

            # Filter out null references
            refs = refs.filter(
                pl.col("type_name").is_not_null() &
                pl.col("value").is_not_null()
            )

            if len(refs) > 0:
                all_references.append(refs)
                source_name = Path(file_path).parent.name
                print(f"  {source_name}/{Path(file_path).name}: {len(refs)} reference(s)")
        except Exception as e:
            # Skip files that don't have references column
            continue

    if not all_references:
        raise ValueError("No references found in any silver files")

    print("\nStep 3: Combining and deduplicating references...")
    # Combine all references and deduplicate by (type_name, value)
    combined = pl.concat(all_references).unique(subset=["type_name", "value"]).sort(["type_name", "value"])

    print(f"  Total unique references: {len(combined)}")

    # Add id column (1-based index)
    result = combined.with_row_index(name="id", offset=1)

    # Rename type_name to type and select final columns: id, type, value
    # Keep type as accession string (defer ID mapping to global stage)
    result = result.select([
        "id",
        pl.col("type_name").alias("type"),
        "value"
    ])

    print(f"  Final reference count: {len(result)}")
    print(f"\n  References by type:")
    type_summary = result.group_by("type").agg(pl.count().alias("count")).sort("count", descending=True)
    for row in type_summary.iter_rows(named=True):
        print(f"    {row['type']}: {row['count']}")

    return result