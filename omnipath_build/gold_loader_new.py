#!/usr/bin/env python3
"""
Gold Loader (New) - Build gold tables from silver tables with updated schema.

This module orchestrates the gold table building process. Currently implements:
1. local_tables: Build per-source local tables from Entity records

Future steps will include:
2. entity_identifiers: Build entity identifier tables with provenance
3. global_tables: Build global evidence tables by joining local tables with entity mapping
4. aggregates: Summarise global evidence into dimension tables and bridges
"""

import logging
import polars as pl
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Import our modular functions from the gold_new/ directory
from omnipath_build.gold_new.build_local_tables import build_local_tables

__all__ = [
    'build_local_tables_step',
    'run_gold_loader_new',
]


def build_local_tables_step(
    data_root: Path,
    output_dir: Path,
    sources_df: pl.DataFrame,
) -> dict[str, pl.DataFrame]:
    """
    Build local tables per source.

    This step processes each source independently to create normalized tables:
    - local_entity: Per-source entity records
    - local_entity_identifier: Per-source entity identifiers
    - local_entity_annotation: Per-source entity annotations
    - local_membership: Per-source membership relationships
    - local_membership_annotation: Per-source membership annotations

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        sources_df: Sources DataFrame for source_id mapping

    Returns:
        Dictionary containing the local tables (empty as tables are saved per-source)
    """
    print("\n" + "=" * 70)
    print("STEP: Local Tables (Per-Source)")
    print("=" * 70)

    # Build local tables for all sources
    # Note: This saves per-source files to output_dir/local_tables/
    local_tables = build_local_tables(
        data_root=data_root,
        output_dir=output_dir,
        sources_df=sources_df,
    )

    print("\nLocal tables built successfully (per-source files saved)")

    return local_tables


def run_gold_loader_new(
    data_root: Path,
    output_dir: Path,
    step: Optional[str] = None,
) -> None:
    """
    Main orchestration function for building gold tables with new schema.

    Currently implemented steps:
    1. local_tables: Build per-source local tables

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        step: Optional specific step to run. If None, run all steps.
              Valid values: 'local_tables'
    """
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "GOLD LOADER PIPELINE (NEW)" + " " * 27 + "║")
    print("╚" + "=" * 68 + "╝")
    print(f"\nData root: {data_root}")
    print(f"Output directory: {output_dir}")
    if step:
        print(f"Running single step: {step}")
    print()

    logger.info(f"Starting gold loader pipeline - Data root: {data_root}, Output: {output_dir}")

    # Load sources table (required for all steps)
    sources_path = output_dir / "source.parquet"
    if not sources_path.exists():
        raise FileNotFoundError(
            f"Sources table not found: {sources_path}\n"
            "Please run the 'sources' step first to build the source table."
        )

    sources = pl.read_parquet(sources_path)
    print(f"Loaded {len(sources)} sources from {sources_path}")

    # If a specific step is requested, only run that step
    if step:
        if step == 'local_tables':
            build_local_tables_step(
                data_root,
                output_dir,
                sources_df=sources,
            )
        else:
            raise ValueError(f"Unknown step: {step}")
    else:
        # Run all implemented steps in order
        # Step 1: Local tables (per-source processing)
        build_local_tables_step(
            data_root,
            output_dir,
            sources_df=sources,
        )

        # Future steps will be added here as they are implemented

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 25 + "PIPELINE COMPLETE" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
