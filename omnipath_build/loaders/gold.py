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
from pathlib import Path
from typing import Optional

import polars as pl

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# Import our modular functions from the gold/ directory
from omnipath_build.gold.build_entity_identifiers import build_entity_identifiers
from omnipath_build.gold.build_global_tables import build_global_tables
from omnipath_build.gold.build_local_tables import build_local_tables

__all__ = [
    'build_local_tables_step',
    'build_entity_identifiers_step',
    'build_global_tables_step',
    'run_gold_loader_new',
]


def _load_source_id_map(path: Path | None) -> dict[str, int] | None:
    """Load a source name -> source ID map from TSV.

    Expected format (header optional):
        source_id<TAB>source
    """
    if path is None:
        return None

    if not path.exists():
        raise FileNotFoundError(f'Source ID map not found: {path}')

    mapping: dict[str, int] = {}
    with path.open('r', encoding='utf-8') as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) != 2:
                continue
            if parts[0] == 'source_id' and parts[1] == 'source':
                continue
            source_id, source_name = parts
            source_id_int = int(source_id)
            mapping[source_name] = source_id_int
            source_leaf = source_name.split('.')[-1]
            if source_leaf in mapping and mapping[source_leaf] != source_id_int:
                raise ValueError(
                    f'Ambiguous source leaf in map for {source_leaf}: '
                    f'{mapping[source_leaf]} vs {source_id_int}'
                )
            mapping[source_leaf] = source_id_int

    if not mapping:
        raise ValueError(f'No valid source mappings found in: {path}')

    return mapping


def build_local_tables_step(
    data_root: Path,
    output_dir: Path,
    source: Optional[str] = None,
    source_id_map_file: Optional[Path] = None,
) -> dict[str, pl.DataFrame]:
    """
    Build local tables per source.

    This step processes each source independently to create normalized tables:
    - local_entity: Per-source entity records
    - local_entity_identifier: Per-source entity identifiers
    - local_entity_instance: Per-source entity instances (for entities with annotations)
    - local_entity_annotation: Per-source entity annotations (linked to instances)
    - local_membership: Per-source membership relationships (polymorphic entity/instance columns)

    Source IDs are auto-generated from discovered source names.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        source: Optional single source name to process
        source_id_map_file: Optional TSV path with deterministic source ID mapping

    Returns:
        Dictionary containing the local tables (empty as tables are saved per-source)
    """
    print('\n' + '=' * 70)
    print('STEP: Local Tables (Per-Source)')
    print('=' * 70)

    if not source:
        raise ValueError('local_tables step requires --source in per-source stage layout')

    source_id_map = _load_source_id_map(source_id_map_file)
    source_filter = {source.split('.')[-1]}

    # Build local tables for all (or selected) sources
    # Note: This saves per-source files to output_dir/local_tables/
    local_tables = build_local_tables(
        data_root=data_root,
        output_dir=output_dir,
        source_id_map=source_id_map,
        source_filter=source_filter,
        single_source_name=source.split('.')[-1] if source else None,
    )

    print('\nLocal tables built successfully (per-source files saved)')

    return local_tables


def build_entity_identifiers_step(
    output_dir: Path,
    local_tables_dir: Optional[Path] = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """
    Build entity identifiers using graph-based equivalence detection.

    This step resolves entities across sources by:
    1. Loading local entity identifier tables from output_dir/local_tables/
    2. Building edges from merge-safe identifiers (InChI, InChIKey, Uniprot)
    3. Using UnionFind to assign canonical entity_id across all sources
    4. Creating a mapping from (source_id, local_entity_id) -> entity_id
    5. Building a unified identifier table with source provenance
    6. Loading entity instances and mapping to global instance IDs

    Args:
        output_dir: Path to output directory containing local_tables/

    Returns:
        Tuple of (record_to_global, entity_identifiers, entity_identifier_resource, instance_to_global) DataFrames:
        - record_to_global: Maps (source_id, local_entity_id) to entity_id
        - entity_identifiers: Maps (id, entity_id, type_id, identifier)
        - entity_identifier_resource: Maps (id, entity_identifier_id, source_entity_id)
        - instance_to_global: Maps (source_id, local_entity_instance_id) to (instance_id, entity_id)
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

    # Build entity identifiers
    (
        record_to_global,
        entity_identifiers,
        entity_identifier_resource,
        instance_to_global,
    ) = build_entity_identifiers(
        local_tables_dir=local_tables_dir,
    )

    # Save the results
    record_to_global_path = output_dir / 'entity_record_mapping.parquet'
    entity_identifiers_path = output_dir / 'entity_identifier.parquet'
    entity_identifier_resource_path = output_dir / 'entity_identifier_resource.parquet'
    instance_to_global_path = output_dir / 'instance_to_global.parquet'

    record_to_global.write_parquet(record_to_global_path)
    entity_identifiers.write_parquet(entity_identifiers_path)
    entity_identifier_resource.write_parquet(entity_identifier_resource_path)
    instance_to_global.write_parquet(instance_to_global_path)

    print(f'\nSaved entity record mapping: {record_to_global_path}')
    print(f'  Rows: {len(record_to_global):,}')
    print(f'\nSaved entity identifiers: {entity_identifiers_path}')
    print(f'  Rows: {len(entity_identifiers):,}')
    if len(entity_identifiers) > 0:
        print(f"  Unique entities: {entity_identifiers['entity_id'].n_unique():,}")
    print(f'\nSaved entity identifier resources: {entity_identifier_resource_path}')
    print(f'  Rows: {len(entity_identifier_resource):,}')
    print(f'\nSaved instance to global mapping: {instance_to_global_path}')
    print(f'  Rows: {len(instance_to_global):,}')

    return (
        record_to_global,
        entity_identifiers,
        entity_identifier_resource,
        instance_to_global,
    )


def build_global_tables_step(
    output_dir: Path,
    local_tables_dir: Optional[Path] = None,
) -> None:
    """
    Build global tables from local tables and entity resolution.

    This step joins local tables with entity mappings to create global tables:
    1. Loads record_to_global mapping (source_id, local_entity_id) -> entity_id
    2. Loads entity_identifiers with CV term information
    3. Loads instance_to_global mapping for entity instances
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
    record_to_global_file = output_dir / 'entity_record_mapping.parquet'
    entity_identifiers_file = output_dir / 'entity_identifier.parquet'
    instance_to_global_file = output_dir / 'instance_to_global.parquet'

    # Verify prerequisites exist
    if not local_tables_dir.exists():
        raise FileNotFoundError(
            f'Local tables directory not found: {local_tables_dir}\n'
            "Please run the 'local_tables' step first."
        )
    if not record_to_global_file.exists():
        raise FileNotFoundError(
            f'Entity record mapping not found: {record_to_global_file}\n'
            "Please run the 'entity_identifiers' step first."
        )
    if not entity_identifiers_file.exists():
        raise FileNotFoundError(
            f'Entity identifiers not found: {entity_identifiers_file}\n'
            "Please run the 'entity_identifiers' step first."
        )
    if not instance_to_global_file.exists():
        raise FileNotFoundError(
            f'Instance to global mapping not found: {instance_to_global_file}\n'
            "Please run the 'entity_identifiers' step first."
        )

    # Build global tables
    build_global_tables(
        local_tables_dir=local_tables_dir,
        record_to_global_file=record_to_global_file,
        entity_identifiers_file=entity_identifiers_file,
        instance_to_global_file=instance_to_global_file,
        output_dir=output_dir,
    )

    print('\nGlobal tables built successfully')


def run_gold_loader_new(
    data_root: Path,
    output_dir: Path,
    step: Optional[str] = None,
    source: Optional[str] = None,
    source_id_map_file: Optional[Path] = None,
    local_tables_dir: Optional[Path] = None,
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
        source: Optional source filter (applies to local_tables step)
        source_id_map_file: Optional TSV mapping for deterministic source IDs
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
    if source_id_map_file:
        print(f'Source ID map: {source_id_map_file}')
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
                source_id_map_file=source_id_map_file,
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
            source_id_map_file=source_id_map_file,
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
