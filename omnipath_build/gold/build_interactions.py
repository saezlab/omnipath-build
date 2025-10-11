#!/usr/bin/env python3
"""
Build interactions table from silver_interactions files.

This module aggregates all unique interactions from silver_interactions,
maps entity identifiers to entity_ids from the clustering results,
and ensures entity pairs are sorted (entity_a_id <= entity_b_id).

Usage:
    python build_interactions.py --data-root /path/to/data --output-dir /path/to/output
"""

import polars as pl
from pathlib import Path
from glob import glob
import argparse
import sys

__all__ = [
    'build_interactions',
    'main',
]


def build_interactions(
    data_root: Path,
    output_dir: Path,
    entity_identifiers: pl.DataFrame = None
) -> pl.DataFrame:
    """
    Build interactions table from all silver_interactions files.

    Aggregates unique interactions, maps entity identifiers to entity_ids,
    and ensures sorted entity pairs (entity_a_id <= entity_b_id) for
    undirected interactions.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        entity_identifiers: Optional DataFrame with entity identifier mappings.
                           If None, will load from output_dir/entity_identifier.parquet

    Returns:
        DataFrame with columns: id, entity_a_id, entity_b_id,
                               type_namespace_name, type_name
    """
    print("\nStep 1: Loading entity identifier mappings...")

    # Load entity identifiers if not provided
    if entity_identifiers is None:
        entity_id_path = output_dir / "entity_identifier.parquet"
        if not entity_id_path.exists():
            raise FileNotFoundError(
                f"Entity identifier table not found: {entity_id_path}. "
                "Please run entity identifier clustering first."
            )
        entity_identifiers = pl.read_parquet(entity_id_path)

    print(f"  Loaded {len(entity_identifiers):,} entity identifiers")
    print(f"  Covering {entity_identifiers['entity_id'].n_unique():,} entities")

    print("\nStep 2: Finding all silver_interactions files...")
    pattern = str(data_root / "*" / "*" / "silver" / "silver_interactions.parquet")
    parquet_files = glob(pattern)

    if not parquet_files:
        print("  No silver_interactions files found - creating empty interactions table")
        return pl.DataFrame({
            "id": pl.Series([], dtype=pl.Int64),
            "entity_a_id": pl.Series([], dtype=pl.Int64),
            "entity_b_id": pl.Series([], dtype=pl.Int64),
            "type_namespace_name": pl.Series([], dtype=pl.Utf8),
            "type_name": pl.Series([], dtype=pl.Utf8),
        })

    print(f"  Found {len(parquet_files)} silver_interactions files")

    print("\nStep 3: Extracting interactions and mapping to entity_ids...")

    # Collect all interactions
    all_interactions = []

    for file_path in parquet_files:
        df = pl.scan_parquet(file_path)

        # Extract interaction data
        interactions = df.select([
            pl.col("entity_a_identifier"),
            pl.col("entity_a_identifier_type"),
            pl.col("entity_b_identifier"),
            pl.col("entity_b_identifier_type"),
            pl.lit("OmniPath").alias("type_namespace_name"),
            pl.col("interaction_type").alias("type_name"),
        ]).collect()

        if len(interactions) > 0:
            all_interactions.append(interactions)
            print(f"  {Path(file_path).parent.parent.parent.name}: {len(interactions):,} interaction(s)")

    if not all_interactions:
        print("  No interactions found in any files - creating empty interactions table")
        return pl.DataFrame({
            "id": pl.Series([], dtype=pl.Int64),
            "entity_a_id": pl.Series([], dtype=pl.Int64),
            "entity_b_id": pl.Series([], dtype=pl.Int64),
            "type_namespace_name": pl.Series([], dtype=pl.Utf8),
            "type_name": pl.Series([], dtype=pl.Utf8),
        })

    print("\nStep 4: Combining all interactions...")
    combined = pl.concat(all_interactions)
    print(f"  Total interactions (before mapping): {len(combined):,}")

    print("\nStep 5: Mapping entity A identifiers to entity_ids...")
    # Map entity_a to entity_id
    result = combined.join(
        entity_identifiers.select([
            pl.col("identifier"),
            pl.col("identifier_type_name"),
            pl.col("entity_id").alias("entity_a_id"),
        ]),
        left_on=["entity_a_identifier", "entity_a_identifier_type"],
        right_on=["identifier", "identifier_type_name"],
        how="left"
    )

    # Count unmapped entities
    unmapped_a = result.filter(pl.col("entity_a_id").is_null())
    if len(unmapped_a) > 0:
        print(f"  Warning: {len(unmapped_a):,} interactions have unmapped entity_a")

    print("\nStep 6: Mapping entity B identifiers to entity_ids...")
    # Map entity_b to entity_id
    result = result.join(
        entity_identifiers.select([
            pl.col("identifier"),
            pl.col("identifier_type_name"),
            pl.col("entity_id").alias("entity_b_id"),
        ]),
        left_on=["entity_b_identifier", "entity_b_identifier_type"],
        right_on=["identifier", "identifier_type_name"],
        how="left"
    )

    # Count unmapped entities
    unmapped_b = result.filter(pl.col("entity_b_id").is_null())
    if len(unmapped_b) > 0:
        print(f"  Warning: {len(unmapped_b):,} interactions have unmapped entity_b")

    print("\nStep 7: Filtering out interactions with unmapped entities...")
    # Keep only interactions where both entities are mapped
    result = result.filter(
        pl.col("entity_a_id").is_not_null() & pl.col("entity_b_id").is_not_null()
    )
    print(f"  Interactions with both entities mapped: {len(result):,}")

    print("\nStep 8: Sorting entity pairs (entity_a_id <= entity_b_id)...")
    # Ensure entity_a_id <= entity_b_id by swapping if needed
    result = result.with_columns([
        pl.when(pl.col("entity_a_id") <= pl.col("entity_b_id"))
          .then(pl.col("entity_a_id"))
          .otherwise(pl.col("entity_b_id"))
          .alias("entity_a_id_sorted"),
        pl.when(pl.col("entity_a_id") <= pl.col("entity_b_id"))
          .then(pl.col("entity_b_id"))
          .otherwise(pl.col("entity_a_id"))
          .alias("entity_b_id_sorted"),
    ])

    # Select final columns
    result = result.select([
        pl.col("entity_a_id_sorted").alias("entity_a_id"),
        pl.col("entity_b_id_sorted").alias("entity_b_id"),
        "type_namespace_name",
        "type_name",
    ])

    print("\nStep 9: Deduplicating interactions...")
    # Deduplicate by (entity_a_id, entity_b_id, type_namespace_name, type_name)
    result = result.unique(
        subset=["entity_a_id", "entity_b_id", "type_namespace_name", "type_name"]
    ).sort(["entity_a_id", "entity_b_id"])

    print(f"  Unique interactions: {len(result):,}")

    # Add id column (1-based index)
    result = result.with_row_index(name="id", offset=1)

    # Reorder columns
    result = result.select([
        "id",
        "entity_a_id",
        "entity_b_id",
        "type_namespace_name",
        "type_name",
    ])

    # Show distribution by type
    if len(result) > 0:
        print("\n  Distribution by interaction type:")
        type_dist = result.group_by("type_name").agg(
            pl.len().alias("count")
        ).sort("count", descending=True)

        for row in type_dist.iter_rows(named=True):
            print(f"    {row['type_name']}: {row['count']:,}")

    return result


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build interactions table from silver_interactions files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Build interactions table
    python build_interactions.py --data-root databases/omnipath/data --output-dir output/gold

Note: This requires entity_identifier.parquet to exist in the output directory.
      Run test_identifier_clustering.py first if needed.
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
    print("BUILD INTERACTIONS TABLE")
    print("=" * 70)
    print(f"Data root: {args.data_root}")
    print(f"Output directory: {args.output_dir}")

    # Build interactions table
    try:
        interactions = build_interactions(args.data_root, args.output_dir)

        # Save to output directory
        print("\nStep 10: Saving interactions table...")
        output_path = args.output_dir / "interaction.parquet"
        interactions.write_parquet(output_path)
        print(f"  Saved to: {output_path}")

        # Print summary
        print("\n" + "=" * 70)
        print("Summary:")
        print("=" * 70)
        print(f"  Total interactions: {len(interactions):,}")

        if len(interactions) > 0:
            print(f"  Unique entity pairs: {interactions.select(['entity_a_id', 'entity_b_id']).unique().height:,}")
            print(f"  Unique interaction types: {interactions['type_name'].n_unique()}")

            print("\n  Sample interactions (first 5):")
            for row in interactions.head(5).iter_rows(named=True):
                print(f"    {row['id']}: Entity {row['entity_a_id']} <-[{row['type_name']}]-> Entity {row['entity_b_id']}")

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
