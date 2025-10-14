#!/usr/bin/env python3
"""
Build provenance table from silver data.

The provenance table links sources and references, providing the provenance
for each piece of evidence in the database.

Schema: (source_id, primary_source_id, reference_id)
- source_id: The immediate source (e.g., IntAct)
- primary_source_id: The original source (e.g., a specific paper's dataset)
- reference_id: The reference (e.g., PubMed ID)

For entity data without references, reference_id is NULL.

Usage:
    python build_provenance.py --data-root /path/to/data --output-dir /path/to/output
"""

import polars as pl
from pathlib import Path
from glob import glob
import argparse
import sys

__all__ = [
    'build_provenance',
]


def build_provenance(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build provenance table from silver data.

    This function:
    1. Reads silver_entities for entity provenance (source, no reference)
    2. Reads silver_interactions for interaction provenance (source, primary_source, reference)
    3. Combines and deduplicates
    4. Maps to source_id and reference_id using the gold tables

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory (to read source and reference tables)

    Returns:
        DataFrame with columns: id, source_id, primary_source_id, reference_id
    """
    print("\nStep 1: Loading source and reference tables...")

    # Load source table
    source_path = output_dir / "source.parquet"
    if not source_path.exists():
        raise FileNotFoundError(f"Source table not found at {source_path}. Run Phase 1 first.")
    sources = pl.read_parquet(source_path)
    print(f"  Loaded {len(sources)} sources")

    # Load reference table
    reference_path = output_dir / "reference.parquet"
    references = None
    if reference_path.exists():
        references = pl.read_parquet(reference_path)
        print(f"  Loaded {len(references)} references")
    else:
        print(f"  No reference table found (expected if no interaction sources yet)")

    print("\nStep 2: Collecting provenance from silver_entities...")
    # Pattern for entity files
    entity_pattern = str(data_root / "*" / "*" / "silver" / "silver_entities.parquet")
    entity_files = glob(entity_pattern)

    entity_provenance_records = []
    if entity_files:
        print(f"  Found {len(entity_files)} silver_entities files")

        for file in entity_files:
            df = pl.scan_parquet(file).select([
                pl.col("source").alias("source_name"),
                pl.col("source").alias("primary_source_name"),
                pl.lit(None, dtype=pl.Utf8).alias("reference_value")
            ]).unique().collect()

            entity_provenance_records.append(df)

        entity_provenance = pl.concat(entity_provenance_records).unique()
        print(f"  Found {len(entity_provenance)} unique entity provenance records")
    else:
        print(f"  No silver_entities files found")
        entity_provenance = pl.DataFrame({
            "source_name": [],
            "primary_source_name": [],
            "reference_value": []
        })

    print("\nStep 3: Collecting provenance from silver_interactions...")
    # Pattern for interaction files
    interaction_pattern = str(data_root / "*" / "*" / "silver" / "silver_interactions.parquet")
    interaction_files = glob(interaction_pattern)

    interaction_provenance_records = []
    if interaction_files:
        print(f"  Found {len(interaction_files)} silver_interactions files")

        for file in interaction_files:
            df = pl.scan_parquet(file).select([
                pl.col("source").alias("source_name"),
                pl.col("primary_source").alias("primary_source_name"),
                pl.col("reference_value")
            ]).unique().collect()

            interaction_provenance_records.append(df)

        interaction_provenance = pl.concat(interaction_provenance_records).unique()
        print(f"  Found {len(interaction_provenance)} unique interaction provenance records")
    else:
        print(f"  No silver_interactions files found (expected if no interaction sources yet)")
        interaction_provenance = pl.DataFrame({
            "source_name": [],
            "primary_source_name": [],
            "reference_value": []
        })

    print("\nStep 4: Combining and deduplicating provenance records...")
    # Combine entity and interaction provenance
    if len(entity_provenance) > 0 or len(interaction_provenance) > 0:
        all_provenance = pl.concat([entity_provenance, interaction_provenance]).unique()
    else:
        all_provenance = pl.DataFrame({
            "source_name": [],
            "primary_source_name": [],
            "reference_value": []
        })

    print(f"  Total unique provenance records: {len(all_provenance)}")

    print("\nStep 5: Mapping to source_id and reference_id...")

    # Map source_name to source_id
    result = all_provenance.join(
        sources.select([pl.col("id").alias("source_id"), "name"]),
        left_on="source_name",
        right_on="name",
        how="left"
    )

    # Map primary_source_name to primary_source_id
    result = result.join(
        sources.select([pl.col("id").alias("primary_source_id"), pl.col("name").alias("primary_name")]),
        left_on="primary_source_name",
        right_on="primary_name",
        how="left"
    )

    # Map reference_value to reference_id (if references exist)
    if references is not None and len(references) > 0:
        result = result.join(
            references.select([pl.col("id").alias("reference_id"), "identifier"]),
            left_on="reference_value",
            right_on="identifier",
            how="left"
        )
    else:
        result = result.with_columns([
            pl.lit(None, dtype=pl.Int64).alias("reference_id")
        ])

    # Check for unmapped sources
    unmapped_sources = result.filter(pl.col("source_id").is_null())
    if len(unmapped_sources) > 0:
        print(f"  WARNING: {len(unmapped_sources)} provenance records have unmapped sources:")
        print(unmapped_sources.select(["source_name"]).unique())

    unmapped_primary = result.filter(pl.col("primary_source_id").is_null())
    if len(unmapped_primary) > 0:
        print(f"  WARNING: {len(unmapped_primary)} provenance records have unmapped primary sources:")
        print(unmapped_primary.select(["primary_source_name"]).unique())

    # Select final columns
    result = result.select([
        "source_id",
        "primary_source_id",
        "reference_id"
    ]).unique()

    # Add id column
    result = result.with_row_index(name="id", offset=1)

    # Reorder columns
    result = result.select(["id", "source_id", "primary_source_id", "reference_id"])

    print(f"  Final provenance records: {len(result)}")

    return result
