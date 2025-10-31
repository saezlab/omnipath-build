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
from omnipath_build.gold_new.build_local_tables import build_local_tables
from omnipath_build.gold_new.build_entity_identifiers import build_entity_identifiers
from omnipath_build.gold_new.build_references import build_references
from omnipath_build.gold_new.build_global_tables import build_global_tables

#from omnipath_build.gold_new.build_entity_identifier_duckdb import build_entity_identifiers_duckdb
__all__ = [
    'build_sources_table',
    'build_cv_terms_tables',
    'build_local_tables_step',
    'build_entity_identifier_tables',
    'build_references_table',
    'build_global_tables_step',
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


def build_local_tables_step(
    data_root: Path,
    output_dir: Path,
    sources_df: pl.DataFrame,
    cv_term_df: pl.DataFrame,
) -> dict[str, pl.DataFrame]:
    """
    Phase 1, Step 3: Build local tables per source.

    This step processes each source independently to create:
    - local_entity_evidence: Per-source entity records with annotations
    - local_entity_identifiers: Per-source entity identifiers
    - local_membership: Per-source membership relationships
    - local_interaction_evidence: Per-source interaction records

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        sources_df: Sources DataFrame for source_id mapping
        cv_term_df: CV term DataFrame for type_id mapping

    Returns:
        Dictionary containing the four local tables
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 3: Local Tables (Per-Source)")
    print("=" * 70)

    # Build local tables for all sources
    # Note: This saves per-source files to output_dir/local_tables/
    # and returns empty DataFrames (we keep tables source-specific)
    local_tables = build_local_tables(
        data_root=data_root,
        output_dir=output_dir,
        sources_df=sources_df,
        cv_term_df=cv_term_df,
    )

    print("\nLocal tables built successfully (per-source files saved)")

    return local_tables


def build_entity_identifier_tables(
    data_root: Path,
    output_dir: Path,
    cv_term_df: pl.DataFrame | None = None,
    sources_df: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Phase 1, Step 4: Build entity identifier tables (unified with provenance).

    Args:
        data_root: Path to data directory containing silver files (not used - kept for compatibility)
        output_dir: Path to output directory for gold tables
        cv_term_df: Optional CV term DataFrame for type_id mapping
        sources_df: Optional sources DataFrame for source_id mapping (not used)

    Returns:
        Tuple of (record_to_global, final_identifiers)
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 3: Entity Identifier Tables")
    print("=" * 70)

    # Build from local tables (pre-built by build_local_tables_step)
    local_tables_dir = output_dir / "local_tables"
    record_to_global, final_identifiers = build_entity_identifiers(
        local_tables_dir=local_tables_dir,
        cv_term_df=cv_term_df,
    )

    # Compute quick stats
    n_entities = record_to_global.select(pl.col('entity_id').n_unique()).item() if len(record_to_global) > 0 else 0
    n_records = len(record_to_global)

    print(f"\nTotal unified entities: {n_entities:,}")
    print(f"Record→global mappings: {n_records:,}")
    print(f"Final identifiers: {len(final_identifiers):,}")

    # Write outputs (note: safe_clusters table is no longer generated)
    mapping_path = output_dir / "entity_identifier_record_to_global.parquet"
    final_ids_path = output_dir / "entity_identifiers.parquet"

    record_to_global.write_parquet(mapping_path)
    final_identifiers.write_parquet(final_ids_path)

    print(f"Saved: {mapping_path}")
    print(f"Saved: {final_ids_path}")

    return record_to_global, final_identifiers


def build_references_table(
    data_root: Path, output_dir: Path, cv_term_df: pl.DataFrame
) -> pl.DataFrame:
    """
    Phase 1, Step 5: Build references table.

    This function aggregates all unique references from silver files
    (both entities and interactions) and creates the gold references table.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        cv_term_df: CV term DataFrame for reference type_id mapping

    Returns:
        DataFrame with columns: id, type_id, value
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 4: References Table")
    print("=" * 70)

    # Use the existing build_references module
    references = build_references(data_root, output_dir, cv_term_df)

    # Save to output directory
    output_path = output_dir / "references.parquet"
    references.write_parquet(output_path)
    print(f"\nSaved references table to: {output_path}")
    print(f"Total references: {len(references):,}")

    return references


def build_global_tables_step(output_dir: Path) -> None:
    """
    Phase 2, Step 1: Build global evidence tables.

    This function joins local tables with the entity_id mapping to create
    global evidence tables that combine data from all sources.

    Creates three global tables:
    - entity_evidence.parquet: Entity records with global entity_id
    - interaction_evidence.parquet: Interaction records with global entity_ids
    - membership.parquet: Membership relationships with global entity_ids

    Args:
        output_dir: Path to output directory containing local tables and mapping
    """
    print("\n" + "=" * 70)
    print("PHASE 2, STEP 1: Global Evidence Tables")
    print("=" * 70)

    local_tables_dir = output_dir / "local_tables"
    mapping_file = output_dir / "entity_identifier_record_to_global.parquet"
    global_tables_dir = output_dir / "global_tables"

    # Verify required inputs exist
    if not local_tables_dir.exists():
        raise FileNotFoundError(f"Local tables directory not found: {local_tables_dir}")
    if not mapping_file.exists():
        raise FileNotFoundError(f"Entity mapping file not found: {mapping_file}")

    # Build global tables
    build_global_tables(
        local_dir=local_tables_dir,
        mapping_file=mapping_file,
        out_dir=global_tables_dir
    )

    print(f"\nGlobal tables saved to: {global_tables_dir}")
    print("Generated files:")
    for table_file in sorted(global_tables_dir.glob("*.parquet")):
        print(f"  - {table_file.name}")


def run_gold_loader_new(
    data_root: Path,
    output_dir: Path,
    phase: Optional[str] = None,
    step: Optional[str] = None,
) -> None:
    """
    Main orchestration function for building gold tables with new schema.

    Phases:
    1. Cross-source processing: sources, cv_terms, local_tables, entity_identifiers, references
    2. Evidence extraction: Build global tables by joining local tables with entity mapping
    3. Compound properties: (TODO) Compute molecular properties from SMILES

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        phase: Optional phase to run (1, 2, or 3). If None, run all phases.
        step: Optional specific step to run within phase 1. If provided, only that step runs.
              Valid values: 'sources', 'cv_terms', 'local_tables', 'entity_identifiers', 'references'
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

    # PHASE 1: Cross-source processing
    if phase is None or phase == "1":
        print("\n")
        print("┌" + "─" * 68 + "┐")
        print("│" + " " * 18 + "PHASE 1: CROSS-SOURCE PROCESSING" + " " * 18 + "│")
        print("└" + "─" * 68 + "┘")

        # If a specific step is requested, only run that step
        if step:
            if step == 'sources':
                build_sources_table(data_root, output_dir)
            elif step == 'cv_terms':
                build_cv_terms_tables(data_root, output_dir)
            elif step == 'local_tables':
                # Load dependencies
                cv_term = pl.read_parquet(output_dir / "cv_term.parquet")
                sources = pl.read_parquet(output_dir / "source.parquet")
                build_local_tables_step(
                    data_root, output_dir, sources_df=sources, cv_term_df=cv_term
                )
            elif step == 'entity_identifiers':
                # Load dependencies
                cv_term = pl.read_parquet(output_dir / "cv_term.parquet")
                sources = pl.read_parquet(output_dir / "source.parquet")
                build_entity_identifier_tables(
                    data_root, output_dir, cv_term_df=cv_term, sources_df=sources
                )
            elif step == 'references':
                # Load dependencies
                cv_term = pl.read_parquet(output_dir / "cv_term.parquet")
                build_references_table(data_root, output_dir, cv_term_df=cv_term)
        else:
            # Run all steps in order
            # Step 1: Sources
            sources = build_sources_table(data_root, output_dir)

            # Step 2: CV terms
            cv_namespace, cv_term = build_cv_terms_tables(data_root, output_dir)

            # Step 3: Local tables (per-source processing)
            local_tables = build_local_tables_step(
                data_root, output_dir, sources_df=sources, cv_term_df=cv_term
            )

            # Step 4: Entity identifiers (pass cv_term for efficient integer usage)
            record_to_global, final_identifiers = build_entity_identifier_tables(
                data_root, output_dir, cv_term_df=cv_term, sources_df=sources
            )

            # Step 5: References
            references = build_references_table(data_root, output_dir, cv_term_df=cv_term)

            # TODO: Add remaining Phase 1 steps as we adapt them
            # - Interactions

    # PHASE 2: Evidence extraction (join local tables with global entity_ids)
    if phase is None or phase == "2":
        print("\n")
        print("┌" + "─" * 68 + "┐")
        print("│" + " " * 18 + "PHASE 2: EVIDENCE EXTRACTION" + " " * 22 + "│")
        print("└" + "─" * 68 + "┘")

        # Build global tables by joining local tables with entity mapping
        build_global_tables_step(output_dir)

    # TODO: PHASE 3: Compound properties

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 25 + "PIPELINE COMPLETE" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
