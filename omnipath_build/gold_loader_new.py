#!/usr/bin/env python3
"""
Gold Loader (New) - Build gold tables from silver tables with updated schema.

This module orchestrates the entire gold table building process through the
following steps:
1. sources: Build sources table
2. cv_terms: Build CV namespace and term tables
3. references: Build references table
4. local_tables: Build per-source local tables
5. entity_identifiers: Build entity identifier tables with provenance
6. global_tables: Build global evidence tables by joining local tables with entity mapping

All steps can be run individually or together. The global_tables step requires
all previous steps to have completed.

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
from omnipath_build.gold_new.build_compounds import build_compounds

#from omnipath_build.gold_new.build_entity_identifier_duckdb import build_entity_identifiers_duckdb
__all__ = [
    'build_sources_table',
    'build_cv_terms_tables',
    'build_local_tables_step',
    'build_entity_identifier_tables',
    'build_references_table',
    'build_global_tables_step',
    'build_compounds_table',
    'run_gold_loader_new',
]


def build_sources_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Build sources table.

    This function works with the new silver schema where sources are still
    stored as simple string values in the 'source' column.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        DataFrame with columns: id, name, url, description
    """
    print("=" * 70)
    print("STEP: Sources Table")
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
    Build CV namespace and term tables.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        Tuple of (cv_namespace_df, cv_term_df)
    """
    print("\n" + "=" * 70)
    print("STEP: CV Terms Tables")
    print("=" * 70)

    cv_namespace, cv_term = build_cv_terms(data_root, output_dir)

    print(f"\nTotal namespaces: {len(cv_namespace):,}")
    print(f"Total CV terms: {len(cv_term):,}")

    return cv_namespace, cv_term


def build_local_tables_step(
    data_root: Path,
    output_dir: Path,
    sources_df: pl.DataFrame,
    references_df: pl.DataFrame | None = None,
) -> dict[str, pl.DataFrame]:
    """
    Build local tables per source.

    This step processes each source independently to create:
    - local_entity_evidence: Per-source entity records with annotations
    - local_entity_identifiers: Per-source entity identifiers
    - local_membership: Per-source membership relationships
    - local_interaction_evidence: Per-source interaction records
    - local_is_member_of: Per-source entity hierarchy relationships

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        sources_df: Sources DataFrame for source_id mapping
        references_df: References DataFrame (optional, will load from disk if not provided)

    Returns:
        Dictionary containing the local tables
    """
    print("\n" + "=" * 70)
    print("STEP: Local Tables (Per-Source)")
    print("=" * 70)

    if references_df is None:
        references_path = output_dir / "references.parquet"
        if not references_path.exists():
            raise FileNotFoundError(
                "References table not found. Run the references step before local tables."
            )
        references_df = pl.read_parquet(references_path)

    # Build local tables for all sources
    # Note: This saves per-source files to output_dir/local_tables/
    # and returns empty DataFrames (we keep tables source-specific)
    local_tables = build_local_tables(
        data_root=data_root,
        output_dir=output_dir,
        sources_df=sources_df,
        references_df=references_df,
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
    Build entity identifier tables (unified with provenance).

    Args:
        data_root: Path to data directory containing silver files (not used - kept for compatibility)
        output_dir: Path to output directory for gold tables
        cv_term_df: Optional CV term DataFrame for type_id mapping
        sources_df: Optional sources DataFrame for source_id mapping (not used)

    Returns:
        Tuple of (record_to_global, final_identifiers)
    """
    print("\n" + "=" * 70)
    print("STEP: Entity Identifier Tables")
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
    data_root: Path, output_dir: Path
) -> pl.DataFrame:
    """
    Build references table.

    This function aggregates all unique references from silver files
    (both entities and interactions) and creates the gold references table.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables

    Returns:
        DataFrame with columns: id, type, value (type is CV term accession string)
    """
    print("\n" + "=" * 70)
    print("STEP: References Table")
    print("=" * 70)

    # Use the existing build_references module
    references = build_references(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "references.parquet"
    references.write_parquet(output_path)
    print(f"\nSaved references table to: {output_path}")
    print(f"Total references: {len(references):,}")

    return references


def build_global_tables_step(output_dir: Path) -> None:
    """
    Build global evidence tables.

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
    print("STEP: Global Evidence Tables")
    print("=" * 70)

    local_tables_dir = output_dir / "local_tables"
    mapping_file = output_dir / "entity_identifier_record_to_global.parquet"

    # Verify required inputs exist
    if not local_tables_dir.exists():
        raise FileNotFoundError(f"Local tables directory not found: {local_tables_dir}")
    if not mapping_file.exists():
        raise FileNotFoundError(f"Entity mapping file not found: {mapping_file}")

    # Build global tables
    build_global_tables(
        local_dir=local_tables_dir,
        mapping_file=mapping_file,
        out_dir=output_dir
    )

    print(f"\nGlobal tables saved to: {output_dir}")
    print("Generated files:")
    for table_file in sorted(output_dir.glob("*.parquet")):
        print(f"  - {table_file.name}")


def build_compounds_table(
    output_dir: Path,
    cv_term_df: pl.DataFrame,
    compound_limit: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[Path] = None,
) -> pl.DataFrame:
    """
    Build compounds table with computed molecular properties.

    This function computes chemical properties (molecular weight, formula, etc.)
    from chemical structure identifiers (Standard InChI or SMILES) in the
    entity_identifiers table.

    Prefers Standard InChI over SMILES to avoid duplicate computation for
    entities that have both identifiers.

    Args:
        output_dir: Path to output directory containing entity_identifiers.parquet
        cv_term_df: CV terms DataFrame for looking up identifier type_ids
        compound_limit: Optional limit on number of compounds to process (for testing)
        use_cache: Whether to use cached compound properties (default: True)
        cache_dir: Optional directory for caching computed properties

    Returns:
        DataFrame with compound properties
    """
    print("\n" + "=" * 70)
    print("STEP: Compounds Table")
    print("=" * 70)

    # Load entity_identifiers table
    entity_ids_path = output_dir / "entity_identifiers.parquet"
    if not entity_ids_path.exists():
        raise FileNotFoundError(f"Entity identifiers table not found: {entity_ids_path}")

    entity_identifiers = pl.read_parquet(entity_ids_path)
    print(f"Loaded entity identifiers from: {entity_ids_path}")

    # Build compounds table
    # Note: cache_dir should be output_dir so it uses compound.parquet as cache
    if cache_dir is None:
        cache_dir = output_dir

    compounds = build_compounds(
        entity_identifiers=entity_identifiers,
        cv_term_df=cv_term_df,
        compound_limit=compound_limit,
        use_cache=use_cache,
        cache_dir=cache_dir,
    )

    # Save to output directory
    if len(compounds) > 0:
        output_path = output_dir / "compound.parquet"
        compounds.write_parquet(output_path)
        print(f"\nSaved compound table to: {output_path}")
        print(f"Total compounds: {len(compounds):,}")
    else:
        print("\n⚠️  No compounds generated (RDKit not available or no structure identifiers found)")

    return compounds


def run_gold_loader_new(
    data_root: Path,
    output_dir: Path,
    step: Optional[str] = None,
) -> None:
    """
    Main orchestration function for building gold tables with new schema.

    Steps:
    1. sources: Build sources table
    2. cv_terms: Build CV namespace and term tables
    3. references: Build references table
    4. local_tables: Build per-source local tables (requires references)
    5. entity_identifiers: Build entity identifier tables with provenance
    6. global_tables: Build global evidence tables by joining local tables with entity mapping
    7. compounds: Build compound properties table from chemical structure identifiers

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        step: Optional specific step to run. If None, run all steps.
              Valid values: 'sources', 'cv_terms', 'local_tables', 'entity_identifiers', 'references', 'global_tables', 'compounds'
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
        if step == 'sources':
            build_sources_table(data_root, output_dir)
        elif step == 'cv_terms':
            build_cv_terms_tables(data_root, output_dir)
        elif step == 'local_tables':
            # Load dependencies
            cv_term = pl.read_parquet(output_dir / "cv_term.parquet")
            sources = pl.read_parquet(output_dir / "source.parquet")
            references = pl.read_parquet(output_dir / "references.parquet")
            build_local_tables_step(
                data_root,
                output_dir,
                sources_df=sources,
                #cv_term_df=cv_term,
                references_df=references,
            )
        elif step == 'entity_identifiers':
            # Load dependencies
            cv_term = pl.read_parquet(output_dir / "cv_term.parquet")
            sources = pl.read_parquet(output_dir / "source.parquet")
            build_entity_identifier_tables(
                data_root, output_dir, cv_term_df=cv_term, sources_df=sources
            )
        elif step == 'references':
            build_references_table(data_root, output_dir)
        elif step == 'global_tables':
            # Build global tables by joining local tables with entity mapping
            build_global_tables_step(output_dir)
        elif step == 'compounds':
            # Load dependencies
            cv_term = pl.read_parquet(output_dir / "cv_term.parquet")
            build_compounds_table(output_dir, cv_term_df=cv_term)
        else:
            raise ValueError(f"Unknown step: {step}")
    else:
        # Run all steps in order
        # Step 1: Sources
        sources = build_sources_table(data_root, output_dir)

        # Step 2: CV terms
        cv_namespace, cv_term = build_cv_terms_tables(data_root, output_dir)

        # Step 3: References (needed for local table joins)
        references = build_references_table(data_root, output_dir)

        # Step 4: Local tables (per-source processing)
        local_tables = build_local_tables_step(
            data_root,
            output_dir,
            sources_df=sources,
            #cv_term_df=cv_term,
            references_df=references,
        )

        # Step 5: Entity identifiers (pass cv_term for efficient integer usage)
        record_to_global, final_identifiers = build_entity_identifier_tables(
            data_root, output_dir, cv_term_df=cv_term, sources_df=sources
        )

        # Step 6: Global tables (join local tables with entity mapping)
        build_global_tables_step(output_dir)

        # Step 7: Compounds (compute molecular properties from structures)
        build_compounds_table(output_dir, cv_term_df=cv_term)

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 25 + "PIPELINE COMPLETE" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
