#!/usr/bin/env python3
"""Gold Loader (New) - Build gold tables from silver tables with updated schema.

This module orchestrates the gold table building process. Currently implements:
1. local_tables: Build per-source local tables from Entity records
2. entity_identifiers: Build entity identifier tables with provenance using graph-based resolution
3. global_tables: Build global evidence tables by joining local tables with entity mapping

Future steps will include:
4. aggregates: Summarise global evidence into dimension tables and bridges
"""

from typing import Optional
import logging
from pathlib import Path

import polars as pl

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# Import our modular functions from the gold/ directory
from omnipath_build.gold.build_local_tables import build_local_tables
from omnipath_build.gold.build_global_tables import build_global_tables
from omnipath_build.gold.build_entity_identifiers_v2 import (
    build_entity_identifiers_v2,
)

__all__ = [
    'build_local_tables_step',
    'build_entity_identifiers_step',
    'build_global_tables_step',
    'run_gold_loader_new',
]


def build_local_tables_step(
    data_root: Path,
    output_dir: Path,
    source: Optional[str] = None,
) -> dict[str, pl.DataFrame]:
    """Build local tables per source.

    This step processes each source independently to create normalized tables:
    - local_entity: Per-source entity records
    - local_entity_identifier: Per-source entity identifiers
    - local_entity_instance: Per-source entity instances (for entities with annotations)
    - local_entity_annotation: Per-source entity annotations (linked to instances)
    - local_membership: Per-source membership relationships (polymorphic entity/instance columns)

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        source: Optional single source name to process

    Returns:
        Dictionary containing the local tables (empty as tables are saved per-source)
    """
    print('\n' + '=' * 70)
    print('STEP: Local Tables (Per-Source)')
    print('=' * 70)

    if not source:
        raise ValueError('local_tables step requires --source in per-source stage layout')

    source_filter = {source.split('.')[-1]}

    # Build local tables for all (or selected) sources
    # Note: This saves per-source files to output_dir/local_tables/
    local_tables = build_local_tables(
        data_root=data_root,
        output_dir=output_dir,
        source_filter=source_filter,
        single_source_name=source.split('.')[-1] if source else None,
    )

    print('\nLocal tables built successfully (per-source files saved)')

    return local_tables


def build_entity_identifiers_step(
    output_dir: Path,
    local_tables_dir: Optional[Path] = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Build entity identifiers using graph-based equivalence detection.

    This step resolves entities across sources by:
    1. Loading local entity identifier tables from output_dir/local_tables/
    2. Building deterministic entity/instance keys via IEM v2
    3. Building unified identifier tables with source provenance

    Args:
        output_dir: Path to output directory containing local_tables/

    Returns:
        Tuple of (record_identity_snapshot, entity_identifier_snapshot, entity_identifier_resource, instance_identity_snapshot) DataFrames:
        - record_identity_snapshot: Maps (source_ref, local_entity_id) to entity_key
        - entity_identifier_snapshot: Maps (id, entity_key, type_id, identifier)
        - entity_identifier_resource: Maps (id, entity_identifier_id, source_ref)
        - instance_identity_snapshot: Maps (source_ref, local_entity_instance_id) to (instance_key, entity_key)
    """
    print('\n' + '=' * 70)
    print('STEP: Entity Identifiers (Cross-Source Resolution)')
    print('=' * 70)

    local_tables_dir = local_tables_dir or (output_dir / 'local_tables')
    if not local_tables_dir.exists():
        raise FileNotFoundError(
            f'Local tables directory not found: {local_tables_dir}\n'
            "Please run the 'local_tables' step first."
        )

    # Build entity identity snapshots (IEM v2)
    (
        record_identity_snapshot,
        entity_identifiers,
        entity_identifier_resource,
        instance_identity_snapshot,
    ) = build_entity_identifiers_v2(
        local_tables_dir=local_tables_dir,
    )

    # Save the results
    record_identity_snapshot_path = output_dir / 'record_identity_snapshot.parquet'
    entity_identifiers_path = output_dir / 'entity_identifier_snapshot.parquet'
    entity_identifier_resource_path = output_dir / 'entity_identifier_resource.parquet'
    instance_identity_snapshot_path = output_dir / 'instance_identity_snapshot.parquet'

    record_identity_snapshot.write_parquet(record_identity_snapshot_path)
    entity_identifiers.write_parquet(entity_identifiers_path)
    entity_identifier_resource.write_parquet(entity_identifier_resource_path)
    instance_identity_snapshot.write_parquet(instance_identity_snapshot_path)

    print(f'\nSaved record identity snapshot: {record_identity_snapshot_path}')
    print(f'  Rows: {len(record_identity_snapshot):,}')
    print(f'\nSaved entity identifier snapshot: {entity_identifiers_path}')
    print(f'  Rows: {len(entity_identifiers):,}')
    if len(entity_identifiers) > 0:
        print(f"  Unique entities: {entity_identifiers['entity_key'].n_unique():,}")
    print(f'\nSaved entity identifier resources: {entity_identifier_resource_path}')
    print(f'  Rows: {len(entity_identifier_resource):,}')
    print(f'\nSaved instance identity snapshot: {instance_identity_snapshot_path}')
    print(f'  Rows: {len(instance_identity_snapshot):,}')

    return (
        record_identity_snapshot,
        entity_identifiers,
        entity_identifier_resource,
        instance_identity_snapshot,
    )


def build_global_tables_step(
    output_dir: Path,
    local_tables_dir: Optional[Path] = None,
) -> None:
    """Build global tables from local tables and entity resolution.

    This step joins local tables with identity snapshots to create global tables:
    1. Loads record identity snapshot (source_ref, local_entity_id) -> entity_key
    2. Loads entity identifier snapshot with CV term information
    3. Loads instance identity snapshot for entity instances
    4. Processes each local table type:
       - entity: Maps entity_type to entity_type_id
       - entity_identifier: Resolves id_type to id_type_id
       - entity_instance: Maps to global instance IDs and entity IDs
       - entity_annotation: Maps instance_id and cv_term_entity_id
       - membership: Resolves parent/member entity/instance IDs
    5. Aggregates across sources and assigns global sequential IDs

    Args:
        output_dir: Path to output directory containing local_tables/,
                   entity_record_mapping.parquet, entity_identifier.parquet,
                   and instance_to_global.parquet

    Outputs:
        Writes global tables to output_dir/:
        - entity.parquet
        - entity_identifier.parquet (updated with id_type_id)
        - entity_instance.parquet
        - entity_annotation.parquet (linked to instances)
        - membership.parquet (polymorphic entity/instance columns)
    """
    print('\n' + '=' * 70)
    print('STEP: Global Tables (Cross-Source Aggregation)')
    print('=' * 70)

    local_tables_dir = local_tables_dir or (output_dir / 'local_tables')
    record_identity_snapshot_file = output_dir / 'record_identity_snapshot.parquet'
    entity_identifier_snapshot_file = output_dir / 'entity_identifier_snapshot.parquet'
    instance_identity_snapshot_file = output_dir / 'instance_identity_snapshot.parquet'

    # Verify prerequisites exist
    if not local_tables_dir.exists():
        raise FileNotFoundError(
            f'Local tables directory not found: {local_tables_dir}\n'
            "Please run the 'local_tables' step first."
        )
    if not record_identity_snapshot_file.exists():
        raise FileNotFoundError(
            f'Record identity snapshot not found: {record_identity_snapshot_file}\n'
            "Please run the 'entity_identifiers' step first."
        )
    if not entity_identifier_snapshot_file.exists():
        raise FileNotFoundError(
            f'Entity identifier snapshot not found: {entity_identifier_snapshot_file}\n'
            "Please run the 'entity_identifiers' step first."
        )
    if not instance_identity_snapshot_file.exists():
        raise FileNotFoundError(
            f'Instance identity snapshot not found: {instance_identity_snapshot_file}\n'
            "Please run the 'entity_identifiers' step first."
        )

    # Build global tables
    build_global_tables(
        local_tables_dir=local_tables_dir,
        record_identity_snapshot_file=record_identity_snapshot_file,
        entity_identifier_snapshot_file=entity_identifier_snapshot_file,
        instance_identity_snapshot_file=instance_identity_snapshot_file,
        output_dir=output_dir,
    )

    print('\nGlobal tables built successfully')


def run_gold_loader_new(
    data_root: Path,
    output_dir: Path,
    step: Optional[str] = None,
    source: Optional[str] = None,
    local_tables_dir: Optional[Path] = None,
) -> None:
    """Main orchestration function for building gold tables with new schema.

    Currently implemented steps:
    1. local_tables: Build per-source local tables
    2. entity_identifiers: Build entity identifier resolution using graph-based equivalence
    3. global_tables: Build global tables from local tables and entity resolution

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        step: Optional specific step to run. If None, run all steps.
              Valid values: 'local_tables', 'entity_identifiers', 'global_tables'
        source: Optional source filter (applies to local_tables step)
    """
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    print('\n')
    print('╔' + '=' * 68 + '╗')
    print('║' + ' ' * 15 + 'GOLD LOADER PIPELINE (NEW)' + ' ' * 27 + '║')
    print('╚' + '=' * 68 + '╝')
    print(f'\nData root: {data_root}')
    print(f'Output directory: {output_dir}')
    if step:
        print(f'Running single step: {step}')
    if source:
        print(f'Source filter: {source}')
    if local_tables_dir:
        print(f'Local tables dir override: {local_tables_dir}')
    print()

    logger.info(
        f'Starting gold loader pipeline - Data root: {data_root}, Output: {output_dir}'
    )

    # If a specific step is requested, only run that step
    if step:
        if step == 'local_tables':
            build_local_tables_step(
                data_root,
                output_dir,
                source=source,
            )
        elif step == 'entity_identifiers':
            build_entity_identifiers_step(
                output_dir,
                local_tables_dir=local_tables_dir,
            )
        elif step == 'global_tables':
            build_global_tables_step(
                output_dir,
                local_tables_dir=local_tables_dir,
            )
        else:
            raise ValueError(f'Unknown step: {step}')
    else:
        # Run all implemented steps in order
        # Step 1: Local tables (per-source processing)
        build_local_tables_step(
            data_root,
            output_dir,
            source=source,
        )

        # Step 2: Entity identifiers (cross-source resolution)
        build_entity_identifiers_step(
            output_dir,
            local_tables_dir=local_tables_dir,
        )

        # Step 3: Global tables (cross-source aggregation)
        build_global_tables_step(
            output_dir,
            local_tables_dir=local_tables_dir,
        )

    print('\n')
    print('╔' + '=' * 68 + '╗')
    print('║' + ' ' * 25 + 'PIPELINE COMPLETE' + ' ' * 26 + '║')
    print('╚' + '=' * 68 + '╝')
    print()
