#!/usr/bin/env python3
"""
Gold Loader - Build gold tables from silver tables.

This script orchestrates the entire gold table building process in 3 phases:
1. Phase 1: Cross-source processing (entity clustering, cv_terms, sources, references, interactions)
2. Phase 2: Evidence extraction (provenance, entity_evidence, membership, interaction_evidence)
   - Automatically combines data from all sources using pl.concat()
3. Phase 3: Compound properties (optional, requires RDKit)
   - Computes molecular properties from SMILES identifiers

All gold tables are final and ready to use after Phase 2.
Phase 3 is optional and adds computed compound properties.

Usage:
    python gold_loader.py --data-root /path/to/data --output-dir /path/to/output
    python gold_loader.py --phase 1  # Run only Phase 1
    python gold_loader.py --phase 2  # Run only Phase 2
    python gold_loader.py --phase 3  # Run only Phase 3 (requires Phase 1 output)
"""

import polars as pl
from pathlib import Path
import argparse
import sys
from typing import Optional

# Import our modular functions
from omnipath_build.gold.build_identifiers import cluster_identifiers
from omnipath_build.gold.build_cv_terms import build_cv_terms
from omnipath_build.gold.build_sources import build_sources
from omnipath_build.gold.build_references import build_references
from omnipath_build.gold.build_interactions import build_interactions
from omnipath_build.gold.build_provenance import build_provenance
from omnipath_build.gold.build_entity_evidence import build_entity_evidence
from omnipath_build.gold.build_membership import build_membership
from omnipath_build.gold.build_interaction_evidence import build_interaction_evidence
from omnipath_build.gold.build_compounds import build_compounds, RDKIT_AVAILABLE

__all__ = [
    'build_compounds_table',
    'build_cv_terms_table',
    'build_entity_evidence_table',
    'build_entity_identifier_table',
    'build_interaction_evidence_table',
    'build_interaction_table',
    'build_membership_table',
    'build_provenance_table',
    'build_references_table',
    'build_sources_table',
    'main',
    'run_gold_loader',
]


def build_entity_identifier_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 1, Step 1: Build entity_identifier table using clustering.

    This clusters all identifiers from:
    - silver_entities (identifier columns)
    - silver_entities.members (JSON)
    - silver_interactions (entity_a/b identifiers)

    Returns:
        DataFrame with columns: id, identifier, identifier_type_namespace_name,
                               identifier_type_name, entity_id
    """
    print("=" * 70)
    print("PHASE 1, STEP 1: Entity Identifier Clustering")
    print("=" * 70)

    result = cluster_identifiers(data_root)

    # Save to output directory
    output_path = output_dir / "entity_identifier.parquet"
    result.write_parquet(output_path)
    print(f"\nSaved entity_identifier table to: {output_path}")
    print(f"Total identifiers: {len(result):,}")
    print(f"Total entities (clusters): {result['entity_id'].n_unique():,}")

    return result


def build_cv_terms_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 1, Step 2: Build cv_terms table from all silver_cv_terms files
    AND auto-generate terms from silver table fields.

    Returns:
        DataFrame with cv_terms
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 2: CV Terms Aggregation")
    print("=" * 70)

    # Use the build_cv_terms module
    cv_namespace, cv_term = build_cv_terms(data_root, output_dir)

    # Save both tables
    print("\nStep 7: Saving tables to output directory...")
    namespace_path = output_dir / "cv_namespace.parquet"
    cv_term_path = output_dir / "cv_term.parquet"

    cv_namespace.write_parquet(namespace_path)
    cv_term.write_parquet(cv_term_path)

    print(f"  Saved cv_namespace to: {namespace_path}")
    print(f"  Saved cv_term to: {cv_term_path}")

    # Print statistics
    print("\n" + "=" * 70)
    print("Statistics:")
    print("=" * 70)
    print(f"  Total CV terms: {len(cv_term):,}")
    print(f"  Total namespaces: {len(cv_namespace):,}")

    # Distribution by namespace
    if len(cv_term) > 0:
        print("\n  Distribution by namespace:")
        namespace_dist = cv_term.group_by('namespace_id').agg(
            pl.len().alias('count')
        ).join(
            cv_namespace.select(['id', 'name']),
            left_on='namespace_id',
            right_on='id',
            how='left'
        ).select(['name', 'count']).sort('count', descending=True)

        for row in namespace_dist.iter_rows(named=True):
            print(f"    {row['name']}: {row['count']:,}")

    return cv_term


def build_sources_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 1, Step 3: Build sources table.

    Returns:
        DataFrame with sources
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 3: Sources Table")
    print("=" * 70)

    # Use the build_sources module
    sources = build_sources(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "source.parquet"
    sources.write_parquet(output_path)
    print(f"\nSaved source table to: {output_path}")
    print(f"Total sources: {len(sources):,}")

    return sources


def build_references_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 1, Step 4: Build references table.

    Returns:
        DataFrame with references
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 4: References Table")
    print("=" * 70)

    # Use the build_references module
    references = build_references(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "reference.parquet"
    references.write_parquet(output_path)
    print(f"\nSaved reference table to: {output_path}")
    print(f"Total references: {len(references):,}")

    return references


def build_interaction_table(
    data_root: Path,
    output_dir: Path,
    entity_identifiers: pl.DataFrame = None
) -> pl.DataFrame:
    """
    Phase 1, Step 5: Build interaction table with sorted entity pairs.

    Args:
        data_root: Path to data directory
        output_dir: Path to output directory
        entity_identifiers: Optional DataFrame with entity identifier mappings

    Returns:
        DataFrame with interactions
    """
    print("\n" + "=" * 70)
    print("PHASE 1, STEP 5: Interaction Table")
    print("=" * 70)

    # Use the build_interactions module
    interactions = build_interactions(data_root, output_dir, entity_identifiers)

    # Save to output directory
    output_path = output_dir / "interaction.parquet"
    interactions.write_parquet(output_path)
    print(f"\nSaved interaction table to: {output_path}")
    print(f"Total interactions: {len(interactions):,}")

    return interactions


def build_provenance_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 2, Step 1: Build provenance table.

    Returns:
        DataFrame with provenance records
    """
    print("\n" + "=" * 70)
    print("PHASE 2, STEP 1: Provenance Table")
    print("=" * 70)

    # Use the build_provenance module
    provenance = build_provenance(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "provenance.parquet"
    provenance.write_parquet(output_path)
    print(f"\nSaved provenance table to: {output_path}")
    print(f"Total provenance records: {len(provenance):,}")

    return provenance


def build_entity_evidence_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 2, Step 2: Build entity_evidence table.

    Returns:
        DataFrame with entity evidence records
    """
    print("\n" + "=" * 70)
    print("PHASE 2, STEP 2: Entity Evidence Table")
    print("=" * 70)

    # Use the build_entity_evidence module
    entity_evidence = build_entity_evidence(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "entity_evidence.parquet"
    entity_evidence.write_parquet(output_path)
    print(f"\nSaved entity_evidence table to: {output_path}")
    print(f"Total entity evidence records: {len(entity_evidence):,}")

    return entity_evidence


def build_membership_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 2, Step 3: Build membership table.

    Returns:
        DataFrame with membership records
    """
    print("\n" + "=" * 70)
    print("PHASE 2, STEP 3: Membership Table")
    print("=" * 70)

    # Use the build_membership module
    membership = build_membership(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "membership.parquet"
    membership.write_parquet(output_path)
    print(f"\nSaved membership table to: {output_path}")
    print(f"Total membership records: {len(membership):,}")

    return membership


def build_interaction_evidence_table(data_root: Path, output_dir: Path) -> pl.DataFrame:
    """
    Phase 2, Step 4: Build interaction_evidence table.

    Returns:
        DataFrame with interaction evidence records
    """
    print("\n" + "=" * 70)
    print("PHASE 2, STEP 4: Interaction Evidence Table")
    print("=" * 70)

    # Use the build_interaction_evidence module
    interaction_evidence = build_interaction_evidence(data_root, output_dir)

    # Save to output directory
    output_path = output_dir / "interaction_evidence.parquet"
    interaction_evidence.write_parquet(output_path)
    print(f"\nSaved interaction_evidence table to: {output_path}")
    print(f"Total interaction evidence records: {len(interaction_evidence):,}")

    return interaction_evidence


def build_compounds_table(
    output_dir: Path,
    entity_identifiers: Optional[pl.DataFrame] = None,
    compound_limit: Optional[int] = None,
) -> pl.DataFrame:
    """
    Phase 3: Build compound table with computed molecular properties.

    This computes chemical properties from SMILES identifiers using RDKit.
    Requires entity_identifier table from Phase 1.

    Args:
        output_dir: Path to output directory for gold tables
        entity_identifiers: Optional pre-loaded entity_identifier table
        compound_limit: Optional limit on number of compounds to process

    Returns:
        DataFrame with compound properties
    """
    print("\n" + "=" * 70)
    print("PHASE 3: Compound Properties")
    print("=" * 70)

    if not RDKIT_AVAILABLE:
        print("\n⚠️  Skipping compound properties - RDKit not available")
        print("   Install with: pip install rdkit")
        return pl.DataFrame()

    # Use the build_compounds module
    compounds = build_compounds(
        output_dir=output_dir,
        entity_identifiers=entity_identifiers,
        compound_limit=compound_limit,
        use_cache=True,
    )

    if len(compounds) == 0:
        print("\n⚠️  No compounds generated")
        return compounds

    # Save to output directory
    output_path = output_dir / "compound.parquet"
    compounds.write_parquet(output_path)
    print(f"\nSaved compound table to: {output_path}")
    print(f"Total compounds: {len(compounds):,}")

    return compounds


def run_gold_loader(
    data_root: Path,
    output_dir: Path,
    phase: Optional[str] = None,
    compound_limit: Optional[int] = None,
) -> None:
    """
    Main orchestration function for building gold tables.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        phase: Optional phase to run (1, 2, or 3). If None, run all phases.
        compound_limit: Optional limit on number of compounds to process in Phase 3
    """
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 20 + "GOLD LOADER PIPELINE" + " " * 28 + "║")
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

        # Step 1: Entity identifier clustering
        entity_identifiers = build_entity_identifier_table(data_root, output_dir)

        # Step 2: CV terms
        cv_terms = build_cv_terms_table(data_root, output_dir)

        # Step 3: Sources
        sources = build_sources_table(data_root, output_dir)

        # Step 4: References
        references = build_references_table(data_root, output_dir)

        # Step 5: Interactions
        interactions = build_interaction_table(data_root, output_dir, entity_identifiers)

    # PHASE 2: Per-source evidence extraction
    if phase is None or phase == "2":
        print("\n")
        print("┌" + "─" * 68 + "┐")
        print("│" + " " * 15 + "PHASE 2: PER-SOURCE EVIDENCE EXTRACTION" + " " * 14 + "│")
        print("└" + "─" * 68 + "┘")

        # Step 1: Provenance
        provenance = build_provenance_table(data_root, output_dir)

        # Step 2: Entity evidence
        entity_evidence = build_entity_evidence_table(data_root, output_dir)

        # Step 3: Membership
        membership = build_membership_table(data_root, output_dir)

        # Step 4: Interaction evidence
        interaction_evidence = build_interaction_evidence_table(data_root, output_dir)

    # PHASE 3: Compound properties (optional, requires RDKit)
    if phase is None or phase == "3":
        print("\n")
        print("┌" + "─" * 68 + "┐")
        print("│" + " " * 20 + "PHASE 3: COMPOUND PROPERTIES" + " " * 20 + "│")
        print("└" + "─" * 68 + "┘")

        # Load entity_identifiers if not already loaded
        entity_identifiers = None
        if phase == "3":
            entity_id_path = output_dir / "entity_identifier.parquet"
            if entity_id_path.exists():
                entity_identifiers = pl.read_parquet(entity_id_path)

        # Build compounds table
        compounds = build_compounds_table(
            output_dir=output_dir,
            entity_identifiers=entity_identifiers,
            compound_limit=compound_limit,
        )

    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 25 + "PIPELINE COMPLETE" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build gold tables from silver tables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run full pipeline (all phases)
    python gold_loader.py --data-root databases/omnipath/data --output-dir output/gold

    # Run only Phase 1 (cross-source processing)
    python gold_loader.py --data-root databases/omnipath/data --output-dir output/gold --phase 1

    # Run only Phase 2 (evidence extraction - requires Phase 1 output)
    python gold_loader.py --data-root databases/omnipath/data --output-dir output/gold --phase 2

    # Run only Phase 3 (compound properties - requires Phase 1 output and RDKit)
    python gold_loader.py --data-root databases/omnipath/data --output-dir output/gold --phase 3

    # Run Phase 3 with a limit (useful for testing)
    python gold_loader.py --phase 3 --compound-limit 1000
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

    parser.add_argument(
        '--phase',
        type=str,
        choices=['1', '2', '3'],
        help='Run only a specific phase (1=cross-source, 2=per-source evidence, 3=compound properties)'
    )

    parser.add_argument(
        '--compound-limit',
        type=int,
        help='Limit number of compounds to process in Phase 3 (default: no limit)'
    )

    args = parser.parse_args()

    # Validate data root exists
    if not args.data_root.exists():
        print(f"Error: Data root not found: {args.data_root}", file=sys.stderr)
        sys.exit(1)

    # Run the pipeline
    try:
        run_gold_loader(args.data_root, args.output_dir, args.phase, args.compound_limit)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
