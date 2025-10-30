#!/usr/bin/env python3
"""
Gold Loader (New) - Build gold tables from silver tables with updated schema.

This module orchestrates the entire gold table building process in three
phases:
1. Phase 1: Cross-source processing (entity clustering, cv_terms, sources,
   references, interactions)
2. Phase 2: Evidence extraction (entity_evidence, membership,
   interaction_evidence) - Automatically combines data from all sources using
   ``pl.concat``.
3. Phase 3: Compound properties (optional, requires RDKit) - Computes
   molecular properties from SMILES identifiers.

All gold tables are final and ready to use after Phase 2. Phase 3 is optional
and adds computed compound properties.

This version works with the updated silver schema that uses structured
identifiers, members, and references fields.
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
# These have been adapted to work with the new silver schema
from omnipath_build.gold_new.build_sources import build_sources
from omnipath_build.gold_new.build_cv_terms import build_cv_terms
from omnipath_build.gold_new.build_entity_identifiers import build_entity_identifier_unified

#from omnipath_build.gold_new.build_entity_identifier_duckdb import build_entity_identifiers_duckdb
__all__ = [
    'build_sources_table',
    'build_cv_terms_tables',
    'build_entity_identifier_tables',
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


def build_cv_terms_tables(
    data_root: Path, output_dir: Path
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Phase 1, Step 2: Build CV namespace and term tables.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        Tuple of (cv_namespace_df, cv_term_df)
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 2: CV Terms Tables")
    print("=" * 70)

    cv_namespace, cv_term = build_cv_terms(data_root, output_dir)

    print(f"\nTotal namespaces: {len(cv_namespace):,}")
    print(f"Total CV terms: {len(cv_term):,}")

    return cv_namespace, cv_term


def build_entity_identifier_tables(
    data_root: Path,
    output_dir: Path,
    cv_term_df: pl.DataFrame | None = None,
    sources_df: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """
    Phase 1, Step 3: Build entity identifier tables (unified with provenance).

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        cv_term_df: Optional CV term DataFrame for type_id mapping
        sources_df: Optional sources DataFrame for source_id mapping

    Returns:
        Tuple of (safe_clusters, record_to_global, final_identifiers)
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 3: Entity Identifier Tables")
    print("=" * 70)

    safe_clusters, record_to_global, final_identifiers = build_entity_identifier_unified(
        data_root=data_root,
        cv_term_df=cv_term_df,
        sources_df=sources_df,
    )

    # Compute quick stats
    n_entities = record_to_global.select(pl.col('entity_id').n_unique()).item() if len(record_to_global) > 0 else 0
    n_records = len(record_to_global)

    print(f"\nTotal unified entities: {n_entities:,}")
    print(f"Record→global mappings: {n_records:,}")
    print(f"Final identifiers: {len(final_identifiers):,}")

    # Write outputs
    clusters_path = output_dir / "entity_identifier_safe_clusters.parquet"
    mapping_path = output_dir / "entity_identifier_record_to_global.parquet"
    final_ids_path = output_dir / "entity_identifiers.parquet"

    safe_clusters.write_parquet(clusters_path)
    record_to_global.write_parquet(mapping_path)
    final_identifiers.write_parquet(final_ids_path)

    print(f"\nSaved: {clusters_path}")
    print(f"Saved: {mapping_path}")
    print(f"Saved: {final_ids_path}")

    return safe_clusters, record_to_global, final_identifiers


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

    logger.info(f"Starting gold loader pipeline - Data root: {data_root}, Output: {output_dir}")

    # PHASE 1: Cross-source processing
    if phase is None or phase == "1":
        print("\n")
        print("┌" + "─" * 68 + "┐")
        print("│" + " " * 18 + "PHASE 1: CROSS-SOURCE PROCESSING" + " " * 18 + "│")
        print("└" + "─" * 68 + "┘")

        # Step 1: Sources
        sources = build_sources_table(data_root, output_dir)

        # Step 2: CV terms
        cv_namespace, cv_term = build_cv_terms_tables(data_root, output_dir)

        # Step 3: Entity identifiers (pass cv_term and sources for efficient integer usage)
        safe_clusters, record_to_global, final_identifiers = build_entity_identifier_tables(
            data_root, output_dir, cv_term_df=cv_term, sources_df=sources
        )

        # TODO: Add remaining Phase 1 steps as we adapt them
        # - References
        # - Interactions

    # TODO: PHASE 2: Per-source evidence extraction
    # TODO: PHASE 3: Compound properties

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 25 + "PIPELINE COMPLETE" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
