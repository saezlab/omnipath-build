#!/usr/bin/env python3
"""
Gold Loader (New) - Build gold tables from silver tables with updated schema.

This module orchestrates the entire gold table building process in three
phases:
1. Phase 1: Cross-source processing (entity clustering, cv_terms, sources,
   references, interactions)
2. Phase 2: Evidence extraction (provenance, entity_evidence, membership,
   interaction_evidence) - Automatically combines data from all sources using
   ``pl.concat``.
3. Phase 3: Compound properties (optional, requires RDKit) - Computes
   molecular properties from SMILES identifiers.

All gold tables are final and ready to use after Phase 2. Phase 3 is optional
and adds computed compound properties.

This version works with the updated silver schema that uses structured
identifiers, members, and references fields.
"""

import polars as pl
from pathlib import Path
from typing import Optional

# Import our modular functions from the gold_new/ directory
# These have been adapted to work with the new silver schema
from omnipath_build.gold_new.build_sources import build_sources

__all__ = [
    'build_sources_table',
    'run_gold_loader_new',
]


def build_sources_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 1, Step 1: Build sources table.

    This function works with the new silver schema where sources are still
    stored as simple string values in the 'source' column.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        DataFrame with columns: id, name, url, description
    """
    print("=" * 70)
    print("PHASE 1, STEP 1: Sources Table")
    print("=" * 70)

    # Use the existing build_sources module (it should still work)
    sources = build_sources(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "source.parquet"
    sources.write_parquet(output_path)
    print(f"\nSaved source table to: {output_path}")
    print(f"Total sources: {len(sources):,}")

    return sources


def run_gold_loader_new(
    data_root: Path,
    output_dir: Path,
    phase: Optional[str] = None,
) -> None:
    """
    Main orchestration function for building gold tables with new schema.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        phase: Optional phase to run (1, 2, or 3). If None, run all phases.
    """
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "GOLD LOADER PIPELINE (NEW)" + " " * 27 + "║")
    print("╚" + "=" * 68 + "╝")
    print(f"\nData root: {data_root}")
    print(f"Output directory: {output_dir}")
    print()

    # PHASE 1: Cross-source processing
    if phase is None or phase == "1":
        print("\n")
        print("┌" + "─" * 68 + "┐")
        print("│" + " " * 18 + "PHASE 1: CROSS-SOURCE PROCESSING" + " " * 18 + "│")
        print("└" + "─" * 68 + "┘")

        # Step 1: Sources
        sources = build_sources_table(data_root, output_dir)

        # TODO: Add remaining Phase 1 steps as we adapt them
        # - Entity identifier clustering
        # - CV terms
        # - References
        # - Interactions

    # TODO: PHASE 2: Per-source evidence extraction
    # TODO: PHASE 3: Compound properties

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 25 + "PIPELINE COMPLETE" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
