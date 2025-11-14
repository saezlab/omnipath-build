#!/usr/bin/env python3
"""
Gold Loader (New) - Build gold tables from silver tables with updated schema.

This module orchestrates the gold table building process. Currently implements:
1. local_tables: Build per-source local tables from Entity records
2. entity_identifiers: Build entity identifier tables with provenance using graph-based resolution
3. global_tables: Build global evidence tables by joining local tables with entity mapping

Future steps will include:
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
from omnipath_build.gold.build_local_tables import build_local_tables
from omnipath_build.gold.build_entity_identifiers import build_entity_identifiers
from omnipath_build.gold.build_global_tables import build_global_tables

__all__ = [
    'build_local_tables_step',
    'build_entity_identifiers_step',
    'build_global_tables_step',
    'run_gold_loader_new',
]


def build_local_tables_step(
    data_root: Path,
    output_dir: Path,
) -> dict[str, pl.DataFrame]:
    """
    Build local tables per source.

    This step processes each source independently to create normalized tables:
    - local_entity: Per-source entity records
    - local_entity_identifier: Per-source entity identifiers
    - local_entity_annotation: Per-source entity annotations
    - local_membership: Per-source membership relationships
    - local_membership_annotation: Per-source membership annotations

    Source IDs are auto-generated from discovered source names.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

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
    )

    print("\nLocal tables built successfully (per-source files saved)")

    return local_tables


def build_entity_identifiers_step(
    output_dir: Path,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """
    Build entity identifiers using graph-based equivalence detection.

    This step resolves entities across sources by:
    1. Loading local entity identifier tables from output_dir/local_tables/
    2. Building edges from merge-safe identifiers (InChI, InChIKey, Uniprot)
    3. Using UnionFind to assign canonical entity_id across all sources
    4. Creating a mapping from (source_id, local_entity_id) -> entity_id
    5. Building a unified identifier table with source provenance

    Args:
        output_dir: Path to output directory containing local_tables/

    Returns:
        Tuple of (record_to_global, entity_identifiers, entity_identifier_resource) DataFrames:
        - record_to_global: Maps (source_id, local_entity_id) to entity_id
        - entity_identifiers: Maps (id, entity_id, type_id, identifier)
        - entity_identifier_resource: Maps (id, entity_identifier_id, source_entity_id)
    """
    print("\n" + "=" * 70)
    print("STEP: Entity Identifiers (Cross-Source Resolution)")
    print("=" * 70)

    local_tables_dir = output_dir / "local_tables"
    if not local_tables_dir.exists():
        raise FileNotFoundError(
            f"Local tables directory not found: {local_tables_dir}\n"
            "Please run the 'local_tables' step first."
        )

    # Build entity identifiers
    record_to_global, entity_identifiers, entity_identifier_resource = build_entity_identifiers(
        local_tables_dir=local_tables_dir,
    )

    # Save the results
    record_to_global_path = output_dir / "entity_record_mapping.parquet"
    entity_identifiers_path = output_dir / "entity_identifier.parquet"
    entity_identifier_resource_path = output_dir / "entity_identifier_resource.parquet"

    record_to_global.write_parquet(record_to_global_path)
    entity_identifiers.write_parquet(entity_identifiers_path)
    entity_identifier_resource.write_parquet(entity_identifier_resource_path)

    print(f"\nSaved entity record mapping: {record_to_global_path}")
    print(f"  Rows: {len(record_to_global):,}")
    print(f"\nSaved entity identifiers: {entity_identifiers_path}")
    print(f"  Rows: {len(entity_identifiers):,}")
    print(f"  Unique entities: {entity_identifiers['entity_id'].n_unique():,}")
    print(f"\nSaved entity identifier resources: {entity_identifier_resource_path}")
    print(f"  Rows: {len(entity_identifier_resource):,}")

    return record_to_global, entity_identifiers, entity_identifier_resource


def build_global_tables_step(
    output_dir: Path,
) -> None:
    """
    Build global tables from local tables and entity resolution.

    This step joins local tables with entity mappings to create global tables:
    1. Loads record_to_global mapping (source_id, local_entity_id) -> entity_id
    2. Loads entity_identifiers with CV term information
    3. Processes each local table type:
       - entity: Maps entity_type to entity_type_id
       - entity_identifier: Resolves id_type to id_type_id
       - entity_annotation: Maps annotation_id to entity IDs
       - membership: Resolves parent/member IDs to entity IDs
       - membership_annotation: Maps annotation_id to entity IDs
    4. Aggregates across sources and assigns global sequential IDs

    Args:
        output_dir: Path to output directory containing local_tables/,
                   entity_record_mapping.parquet, and entity_identifier.parquet

    Outputs:
        Writes global tables to output_dir/:
        - entity.parquet
        - entity_identifier.parquet (updated with id_type_id)
        - entity_annotation.parquet
        - membership.parquet
        - membership_annotation.parquet
    """
    print("\n" + "=" * 70)
    print("STEP: Global Tables (Cross-Source Aggregation)")
    print("=" * 70)

    local_tables_dir = output_dir / "local_tables"
    record_to_global_file = output_dir / "entity_record_mapping.parquet"
    entity_identifiers_file = output_dir / "entity_identifier.parquet"

    # Verify prerequisites exist
    if not local_tables_dir.exists():
        raise FileNotFoundError(
            f"Local tables directory not found: {local_tables_dir}\n"
            "Please run the 'local_tables' step first."
        )
    if not record_to_global_file.exists():
        raise FileNotFoundError(
            f"Entity record mapping not found: {record_to_global_file}\n"
            "Please run the 'entity_identifiers' step first."
        )
    if not entity_identifiers_file.exists():
        raise FileNotFoundError(
            f"Entity identifiers not found: {entity_identifiers_file}\n"
            "Please run the 'entity_identifiers' step first."
        )

    # Build global tables
    build_global_tables(
        local_tables_dir=local_tables_dir,
        record_to_global_file=record_to_global_file,
        entity_identifiers_file=entity_identifiers_file,
        output_dir=output_dir,
    )

    print("\nGlobal tables built successfully")


def run_gold_loader_new(
    data_root: Path,
    output_dir: Path,
    step: Optional[str] = None,
) -> None:
    """
    Main orchestration function for building gold tables with new schema.

    Currently implemented steps:
    1. local_tables: Build per-source local tables
    2. entity_identifiers: Build entity identifier resolution using graph-based equivalence
    3. global_tables: Build global tables from local tables and entity resolution

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        step: Optional specific step to run. If None, run all steps.
              Valid values: 'local_tables', 'entity_identifiers', 'global_tables'
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

    # If a specific step is requested, only run that step
    if step:
        if step == 'local_tables':
            build_local_tables_step(
                data_root,
                output_dir,
            )
        elif step == 'entity_identifiers':
            build_entity_identifiers_step(
                output_dir,
            )
        elif step == 'global_tables':
            build_global_tables_step(
                output_dir,
            )
        else:
            raise ValueError(f"Unknown step: {step}")
    else:
        # Run all implemented steps in order
        # Step 1: Local tables (per-source processing)
        build_local_tables_step(
            data_root,
            output_dir,
        )

        # Step 2: Entity identifiers (cross-source resolution)
        build_entity_identifiers_step(
            output_dir,
        )

        # Step 3: Global tables (cross-source aggregation)
        build_global_tables_step(
            output_dir,
        )

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 25 + "PIPELINE COMPLETE" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
