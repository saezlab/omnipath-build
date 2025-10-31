from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
import logging
import polars as pl
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

# --------------------------------------------------------------------------- #
# Module exports
# --------------------------------------------------------------------------- #
__all__ = [
    'build_local_tables',
]

# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)


def _iter_parquet_files(root: Path) -> Iterable[Path]:
    """Iterate over all parquet files in subdirectories."""
    for d in sorted(root.glob('*')):
        if d.is_dir():
            yield from sorted(d.glob('*.parquet'))


def _load_source_data(data_root: Path) -> dict[str, list[tuple[Path, pl.LazyFrame]]]:
    """Load all source data files into lazy frames, organized by source."""
    sources_data: dict[str, list[tuple[Path, pl.LazyFrame]]] = {}

    for path in _iter_parquet_files(data_root):
        source_name = path.parent.name
        lf = pl.scan_parquet(str(path))

        if source_name not in sources_data:
            sources_data[source_name] = []
        sources_data[source_name].append((path, lf))

    logger.info(f"Found {len(sources_data)} sources with parquet files")
    return sources_data


def _process_source_entities(
    source_name: str,
    source_files: list[tuple[Path, pl.LazyFrame]],
) -> tuple[pl.DataFrame, pl.DataFrame, int]:
    """
    Aggregate all entity records for a source with local entity IDs.

    This function:
    1. Processes interaction records and extracts entities with local IDs
    2. Processes standalone entity records with local IDs
    3. Returns entities, interactions, and next available ID

    Args:
        source_name: Name of the source
        source_files: List of (path, lazyframe) tuples for this source

    Returns:
        Tuple of (entities_df, interactions_df, next_local_id)
    """
    logger.info(f"Processing source: {source_name}")

    all_entity_records = []
    all_interaction_records = []
    next_local_id = 1

    # Step 1: Separate interaction and entity files
    interaction_files = []
    entity_files = []

    for path, lf in source_files:
        # Check if this is an interaction file by checking schema
        schema = lf.collect_schema()
        schema_names = schema.names()

        # Skip files that don't have entity or interaction schema
        # Entity files should have: identifiers, entity_type, etc.
        # Interaction files should have: entity_a, entity_b
        is_interaction = 'entity_a' in schema_names and 'entity_b' in schema_names
        is_entity = 'identifiers' in schema_names and 'entity_type' in schema_names

        if is_interaction:
            interaction_files.append((path, lf))
        elif is_entity:
            entity_files.append((path, lf))
        else:
            # Skip files that don't match entity or interaction schema (e.g., CV terms)
            logger.debug(f"    Skipping {path.name} - not an entity or interaction file")
            continue

    logger.info(f"  Found {len(interaction_files)} interaction files, {len(entity_files)} entity files")

    # Process interactions: assign local IDs to entities
    if interaction_files:
        for path, lf in interaction_files:
            df = lf.collect()

            if len(df) == 0:
                continue

            # Assign local entity IDs using the formula
            df_local = (
                df
                .with_row_index("interaction_row_id", offset=0)
                .with_columns([
                    (pl.col("interaction_row_id") * 2 + next_local_id).alias("local_entity_id_a"),
                    (pl.col("interaction_row_id") * 2 + next_local_id + 1).alias("local_entity_id_b"),
                ])
            )

            # Extract entity_a records
            entity_a = df_local.select([
                pl.col("local_entity_id_a").alias("local_entity_id"),
                pl.col("entity_a").struct.field("source").alias("source"),
                pl.col("entity_a").struct.field("entity_type").alias("entity_type"),
                pl.col("entity_a").struct.field("identifiers").alias("identifiers"),
                pl.col("entity_a").struct.field("members").alias("members"),
                pl.col("entity_a").struct.field("parent_accession").alias("parent_accession"),
                pl.col("entity_a").struct.field("annotations").alias("annotations"),
                pl.col("entity_a").struct.field("references").alias("references"),
                pl.col("entity_a").struct.field("secondary_source").alias("secondary_source"),
            ])

            # Extract entity_b records
            entity_b = df_local.select([
                pl.col("local_entity_id_b").alias("local_entity_id"),
                pl.col("entity_b").struct.field("source").alias("source"),
                pl.col("entity_b").struct.field("entity_type").alias("entity_type"),
                pl.col("entity_b").struct.field("identifiers").alias("identifiers"),
                pl.col("entity_b").struct.field("members").alias("members"),
                pl.col("entity_b").struct.field("parent_accession").alias("parent_accession"),
                pl.col("entity_b").struct.field("annotations").alias("annotations"),
                pl.col("entity_b").struct.field("references").alias("references"),
                pl.col("entity_b").struct.field("secondary_source").alias("secondary_source"),
            ])

            all_entity_records.append(entity_a)
            all_entity_records.append(entity_b)

            # Build interaction evidence (keep interaction metadata with local entity IDs)
            interaction_evidence = df_local.select([
                pl.col("local_entity_id_a"),
                pl.col("local_entity_id_b"),
                pl.col("interaction_type"),
                pl.col("detection_method"),
                pl.col("is_directed"),
                pl.col("direction"),
                pl.col("sign"),
                pl.col("causal_mechanism"),
                pl.col("causal_statement"),
                pl.col("sentence"),
                pl.col("interaction_annotations"),
                pl.col("references"),
            ])

            all_interaction_records.append(interaction_evidence)

            # Update next_local_id to continue after interaction entities
            next_local_id += len(df) * 2

            logger.info(f"    Processed {len(df):,} interactions from {path.name} -> {len(df) * 2:,} entities")

    # Step 2: Process standalone entity files
    if entity_files:
        for path, lf in entity_files:
            df = lf.collect()

            if len(df) == 0:
                continue

            # Drop local_entity_id if it exists (in case file already has it)
            if "local_entity_id" in df.columns:
                df = df.drop("local_entity_id")

            # Assign local entity IDs starting from next_local_id
            df_with_ids = df.with_row_index("local_entity_id", offset=next_local_id)

            all_entity_records.append(df_with_ids)

            next_local_id += len(df)

            logger.info(f"    Processed {len(df):,} entities from {path.name}")

    # Step 3: Combine all entity records
    if not all_entity_records:
        logger.warning(f"  No entity records found for source {source_name}")
        return pl.DataFrame(), pl.DataFrame(), next_local_id

    combined_entities = pl.concat(all_entity_records, how="diagonal_relaxed")

    logger.info(f"  Total entities for {source_name}: {len(combined_entities):,}")

    # Check if there are any members to track (we'll handle these in a second pass)
    has_members = combined_entities.filter(pl.col("members").is_not_null() & (pl.col("members").list.len() > 0))
    if len(has_members) > 0:
        total_members = has_members.select(pl.col("members").list.len().sum()).item()
        logger.info(f"  Entities with members: {len(has_members):,} (total members: {total_members:,})")

    # Combine all interaction records
    if all_interaction_records:
        combined_interactions = pl.concat(all_interaction_records, how="diagonal_relaxed")
        logger.info(f"  Total interactions for {source_name}: {len(combined_interactions):,}")
    else:
        combined_interactions = pl.DataFrame()

    return combined_entities, combined_interactions, next_local_id


def _build_interaction_evidence(
    interactions_df: pl.DataFrame,
    source_id: int,
    cv_term_df: pl.DataFrame,
) -> pl.DataFrame:
    """
    Build local_interaction_evidence table with CV term ID mapping.

    Args:
        interactions_df: DataFrame with interaction records
        source_id: Source ID
        cv_term_df: CV term DataFrame for term-to-ID mapping

    Returns:
        DataFrame with interaction evidence and mapped IDs
    """
    if len(interactions_df) == 0:
        return pl.DataFrame()

    # Create accession-to-ID mapping (terms are stored as accessions like "MI:0407")
    accession_to_id = {
        row['accession']: row['id']
        for row in cv_term_df.iter_rows(named=True)
    }

    # Map interaction_type and detection_method to IDs
    interaction_evidence = interactions_df.with_columns([
        pl.lit(source_id).alias("source_id"),
        # Map accessions to IDs (if they exist in CV terms, otherwise keep as null)
        pl.col("interaction_type").map_elements(
            lambda x: accession_to_id.get(x) if x else None,
            return_dtype=pl.Int64
        ).alias("interaction_type_id"),
        pl.col("detection_method").map_elements(
            lambda x: accession_to_id.get(x) if x else None,
            return_dtype=pl.Int64
        ).alias("detection_method_id"),
    ]).select([
        pl.col("source_id"),
        pl.col("local_entity_id_a"),
        pl.col("local_entity_id_b"),
        pl.col("interaction_type_id"),
        pl.col("detection_method_id"),
        pl.col("is_directed"),
        pl.col("direction"),
        pl.col("sign"),
        pl.col("causal_mechanism"),
        pl.col("causal_statement"),
        pl.col("sentence"),
        pl.col("interaction_annotations"),
        pl.col("references"),
    ])

    return interaction_evidence


def _process_members(
    entities_df: pl.DataFrame,
    source_name: str,
    next_local_id: int,
) -> tuple[pl.DataFrame, pl.DataFrame, int]:
    """
    Second pass: Process members from entities that have them.

    This creates:
    1. New entity records for each member (with new local_entity_ids)
    2. A membership table linking members to their parent entities

    Args:
        entities_df: DataFrame with parent entities (from first pass)
        source_name: Name of the source
        next_local_id: Next available local entity ID

    Returns:
        Tuple of (member_entities_df, membership_df, next_local_id)
    """
    # Filter entities that have members
    has_members = entities_df.filter(
        pl.col("members").is_not_null() & (pl.col("members").list.len() > 0)
    )

    if len(has_members) == 0:
        # No members, return empty dataframes
        return pl.DataFrame(), pl.DataFrame(), next_local_id

    logger.info(f"  Processing members for {len(has_members):,} parent entities")

    # Explode members
    exploded = has_members.select([
        pl.col("local_entity_id").alias("parent_local_entity_id"),
        pl.col("source"),
        pl.col("members"),
    ]).explode("members")

    # Extract member information and create new entity records
    member_entities = exploded.select([
        pl.col("source"),
        # Extract fields from the member struct
        pl.col("members").struct.field("identifier").alias("identifier"),
        pl.col("members").struct.field("identifier_type").alias("identifier_type"),
        pl.col("members").struct.field("role").alias("role"),
        pl.col("members").struct.field("stoichiometry").alias("stoichiometry"),
        pl.col("parent_local_entity_id"),
    ])

    # Assign local_entity_ids to member entities
    member_entities = member_entities.with_row_index("local_entity_id", offset=next_local_id)
    next_local_id += len(member_entities)

    # Build membership table (parent-child relationship)
    membership = member_entities.select([
        pl.col("local_entity_id"),
        pl.col("parent_local_entity_id"),
        pl.col("role"),
        pl.col("stoichiometry"),
    ])

    logger.info(f"    Created {len(member_entities):,} member entity records")
    logger.info(f"    Created {len(membership):,} membership relationships")

    return member_entities, membership, next_local_id


# --------------------------------------------------------------------------- #
# Main function
# --------------------------------------------------------------------------- #

def build_local_tables(
    data_root: Path,
    output_dir: Path,
    sources_df: pl.DataFrame,
    cv_term_df: pl.DataFrame,
) -> dict[str, pl.DataFrame]:
    """
    Build local tables per source from silver entity and interaction files.

    This function processes each source independently to create:
    - local_entity_evidence: Per-source entity records with annotations
    - local_entity_identifiers: Per-source entity identifiers
    - local_membership: Per-source membership relationships
    - local_interaction_evidence: Per-source interaction records

    Each source's entities are assigned sequential local_entity_id values
    (1 to N) that are unique within that source.

    Args:
        data_root: Path to data directory containing silver files
        output_dir: Path to output directory for gold tables
        sources_df: Sources DataFrame with columns (id, name) for source_id mapping
        cv_term_df: CV term DataFrame with columns (id, term) for type_id mapping

    Returns:
        Dictionary with keys: 'local_entity_evidence', 'local_entity_identifiers',
        'local_membership', 'local_interaction_evidence' (combined across all sources)
    """
    logger.info("=" * 70)
    logger.info("Building Local Tables (Per-Source Processing)")
    logger.info("=" * 70)

    # Load all source data
    sources_data = _load_source_data(data_root)

    # Create source name to ID mapping
    source_name_to_id = {
        row['name']: row['id']
        for row in sources_df.iter_rows(named=True)
    }

    # Create output directory for local tables
    local_tables_dir = output_dir / "local_tables"
    local_tables_dir.mkdir(exist_ok=True, parents=True)

    # Track statistics
    sources_processed = 0
    total_entities = 0
    total_interactions = 0
    total_membership = 0

    # Process each source independently
    for source_name, source_files in sources_data.items():
        if source_name not in source_name_to_id:
            logger.warning(f"Source '{source_name}' not found in sources table, skipping")
            continue

        source_id = source_name_to_id[source_name]

        logger.info(f"\n{'='*70}")
        logger.info(f"Processing source: {source_name} (id={source_id})")
        logger.info(f"{'='*70}")

        # First pass: Process entities and assign local_entity_ids
        entities_df, interactions_df, next_local_id = _process_source_entities(source_name, source_files)

        if len(entities_df) == 0:
            logger.warning(f"  No entities found for {source_name}, skipping")
            continue

        # Second pass: Process members (if any)
        member_entities_df, membership_df, next_local_id = _process_members(
            entities_df, source_name, next_local_id
        )

        # Build interaction evidence with CV term ID mapping
        interaction_evidence_df = _build_interaction_evidence(
            interactions_df, source_id, cv_term_df
        )

        # Combine parent entities and member entities
        all_entities = entities_df
        if len(member_entities_df) > 0:
            # Member entities need to be converted to full entity format
            # For now, we'll keep them separate and just track in membership table
            pass

        # Add source_id column
        all_entities = all_entities.with_columns([
            pl.lit(source_id).alias("source_id")
        ])

        # Build local tables for this source
        # local_entity_evidence (basic entity info)
        evidence = all_entities.select([
            pl.col("source_id"),
            pl.col("local_entity_id"),
            pl.col("entity_type"),
            pl.col("annotations"),
        ])

        # local_entity_identifiers (entity IDs)
        identifiers = all_entities.select([
            pl.col("source_id"),
            pl.col("local_entity_id"),
            pl.col("identifiers"),
        ])

        # Add source_id to membership table
        if len(membership_df) > 0:
            membership_df = membership_df.with_columns([
                pl.lit(source_id).alias("source_id")
            ])

        # Save per-source files
        evidence_path = local_tables_dir / f"local_entity_evidence_{source_name}.parquet"
        identifiers_path = local_tables_dir / f"local_entity_identifiers_{source_name}.parquet"

        evidence.write_parquet(evidence_path)
        identifiers.write_parquet(identifiers_path)

        logger.info(f"  Saved {source_name} evidence: {len(evidence):,} rows -> {evidence_path.name}")
        logger.info(f"  Saved {source_name} identifiers: {len(identifiers):,} rows -> {identifiers_path.name}")

        if len(membership_df) > 0:
            membership_path = local_tables_dir / f"local_membership_{source_name}.parquet"
            membership_df.write_parquet(membership_path)
            logger.info(f"  Saved {source_name} membership: {len(membership_df):,} rows -> {membership_path.name}")

        if len(interaction_evidence_df) > 0:
            interactions_path = local_tables_dir / f"local_interaction_evidence_{source_name}.parquet"
            interaction_evidence_df.write_parquet(interactions_path)
            logger.info(f"  Saved {source_name} interactions: {len(interaction_evidence_df):,} rows -> {interactions_path.name}")

        # Track statistics
        sources_processed += 1
        total_entities += len(evidence)
        total_interactions += len(interaction_evidence_df)
        total_membership += len(membership_df)

    # Summary
    logger.info(f"\n{'='*70}")
    logger.info("Local tables summary:")
    logger.info(f"  Sources processed: {sources_processed}")
    logger.info(f"  Total entities: {total_entities:,}")
    logger.info(f"  Total interactions: {total_interactions:,}")
    logger.info(f"  Total membership relationships: {total_membership:,}")
    logger.info(f"\nPer-source files saved to: {local_tables_dir}")

    # Return empty DataFrames (we're keeping tables source-specific)
    return {
        'local_entity_evidence': pl.DataFrame(),
        'local_entity_identifiers': pl.DataFrame(),
        'local_membership': pl.DataFrame(),
        'local_interaction_evidence': pl.DataFrame(),
    }
