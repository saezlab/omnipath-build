#!/usr/bin/env python3
"""Build global tables from local tables using entity resolution.

This script consumes:
1. local_entity_*.parquet files (from build_local_tables.py)
2. record_to_global.parquet (from build_entity_identifiers.py)
3. entity_identifiers.parquet (from build_entity_identifiers.py)

And produces global tables with:
- Local entity IDs replaced by canonical entity IDs
- CV term accessions resolved to entity IDs
- Source provenance in separate resource tables
- Global sequential IDs assigned

Output tables:
  entity.parquet                      (entity_id, entity_type_id)
  entity_identifier.parquet           (entity_identifier_id, entity_id, type_id, identifier)
  entity_annotation.parquet           (entity_id, annotation_id, annotation_value, annotation_unit, sources)
  membership.parquet                  (parent_id, member_id, sources)
  membership_annotation.parquet       (membership_id, annotation_id, annotation_value, annotation_unit, sources)

Note: entity_identifier_resource.parquet is created by build_entity_identifiers.py and requires no transformation
"""

from __future__ import annotations
from pathlib import Path
import logging
import polars as pl

__all__ = ["build_global_tables"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _build_cv_term_mapping(entity_identifiers: pl.DataFrame) -> pl.DataFrame:
    """Build CV term mapping from entity_identifiers.

    CV terms are entities with type_id = "OM:0204" (CV_TERM_ACCESSION).

    Args:
        entity_identifiers: DataFrame with [entity_identifier_id, entity_id, type_id, identifier]

    Returns:
        DataFrame with [accession, cv_term_entity_id] mapping
    """
    # Filter by type_id string accession "OM:0204" (CV_TERM_ACCESSION)
    # At this stage, type_id is still a string accession, not yet converted to integer entity_id
    cv_terms = (
        entity_identifiers
        .filter(pl.col("type_id") == "OM:0204")
        .select([
            pl.col("identifier").alias("accession"),
            pl.col("entity_id").alias("cv_term_entity_id"),
        ])
        .unique(subset=["accession"])
    )

    logger.info(f"Built CV term mapping with {len(cv_terms):,} unique accessions")
    return cv_terms


def _map_cv_term_column(
    df: pl.DataFrame,
    cv_term_mapping: pl.DataFrame,
    accession_col: str,
) -> pl.DataFrame:
    """Map a single CV term accession column to its corresponding entity ID.

    Args:
        df: DataFrame with CV term accession column
        cv_term_mapping: CV term mapping DataFrame [accession, cv_term_entity_id]
        accession_col: Name of the column containing CV term accessions

    Returns:
        DataFrame with new {col}_id column added and original accession column dropped
    """
    if accession_col not in df.columns:
        return df

    # Create mapping with correct column name
    mapping = cv_term_mapping.select([
        pl.col("accession").alias(accession_col),
        pl.col("cv_term_entity_id").alias(f"{accession_col}_id"),
    ])

    # Left join to add the _id column
    result = df.join(mapping, on=accession_col, how="left")

    # Drop the original accession column
    result = result.drop(accession_col)

    return result


def _map_cv_term_columns(
    df: pl.DataFrame,
    cv_term_mapping: pl.DataFrame,
    accession_cols: list[str],
) -> pl.DataFrame:
    """Map multiple CV term accession columns to corresponding entity IDs.

    Args:
        df: DataFrame with CV term accession columns
        cv_term_mapping: CV term mapping DataFrame [accession, cv_term_entity_id]
        accession_cols: List of column names containing CV term accessions

    Returns:
        DataFrame with new {col}_id columns added and original accession columns dropped
    """
    result = df
    for col in accession_cols:
        result = _map_cv_term_column(result, cv_term_mapping, col)
    return result


# --------------------------------------------------------------------------- #
# Main processing function
# --------------------------------------------------------------------------- #

def build_global_tables(
    local_tables_dir: str | Path,
    record_to_global_file: str | Path,
    entity_identifiers_file: str | Path,
    output_dir: str | Path,
):
    """Build global tables from local tables.

    Args:
        local_tables_dir: Directory containing local_*.parquet files
        record_to_global_file: Path to record_to_global.parquet mapping file
        entity_identifiers_file: Path to entity_identifiers.parquet file
        output_dir: Output directory for global tables
    """
    local_tables_dir = Path(local_tables_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # 1. LOAD MAPPINGS
    # ========================================================================

    logger.info("=" * 80)
    logger.info("Loading mappings")
    logger.info("=" * 80)

    # Load record_to_global mapping (source_id, local_entity_id) -> entity_id
    record_to_global = pl.read_parquet(record_to_global_file)
    logger.info(f"Loaded record_to_global: {len(record_to_global):,} rows")

    # Load entity identifiers (includes CV terms)
    entity_identifiers = pl.read_parquet(entity_identifiers_file)
    logger.info(f"Loaded entity_identifiers: {len(entity_identifiers):,} rows")

    # Check if type_id has already been converted to type_id_id (rerun scenario)
    already_processed = "type_id_id" in entity_identifiers.columns and "type_id" not in entity_identifiers.columns
    if already_processed:
        # Already processed - the type_id is already an integer entity_id
        # Build CV term mapping directly from integer type_id
        logger.info("Detected already-processed entity_identifiers (has type_id_id)")
        cv_term_mapping = (
            entity_identifiers
            .rename({"type_id_id": "type_id"})
            .filter(pl.col("type_id").is_not_null())  # CV terms have a type_id
            .select([
                pl.col("identifier").alias("accession"),
                pl.col("entity_id").alias("cv_term_entity_id"),
            ])
            .unique(subset=["accession"])
        )
        # Also rename for the rest of the processing
        entity_identifiers = entity_identifiers.rename({"type_id_id": "type_id"})
    else:
        # Build CV term mapping from string type_id accessions
        cv_term_mapping = _build_cv_term_mapping(entity_identifiers)

    logger.info(f"Built CV term mapping with {len(cv_term_mapping):,} unique accessions")

    # ========================================================================
    # 2. PROCESS ENTITY TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity table")
    logger.info("=" * 80)

    # Get only base entity files, excluding annotation and identifier files
    entity_files = sorted([
        f for f in local_tables_dir.glob("local_entity_*.parquet")
        if "annotation" not in f.name and "identifier" not in f.name and "membership" not in f.name
    ])
    logger.info(f"Found {len(entity_files)} entity files")

    entity_parts = []
    for f in entity_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # Join with record_to_global to get entity_id
        df_global = df.join(
            record_to_global,
            on=["source_id", "local_entity_id"],
            how="left"
        )

        entity_parts.append(df_global)

    if entity_parts:
        # Combine all sources
        entities_combined = pl.concat(entity_parts, how="diagonal_relaxed")

        # Aggregate by entity_id to get entity_type
        entities_global = (
            entities_combined
            .group_by("entity_id")
            .agg([
                pl.col("entity_type").first().alias("entity_type"),  # Should be same across sources
            ])
            .sort("entity_id")
        )

        # Map entity_type (accession) -> entity_type_id
        entities_output = _map_cv_term_columns(entities_global, cv_term_mapping, ["entity_type"])

        entities_output.write_parquet(output_dir / "entity.parquet")
        logger.info(f"✅ entity: {len(entities_output):,} rows")
    else:
        logger.warning("No entity files found")
        pl.DataFrame().write_parquet(output_dir / "entity.parquet")

    # ========================================================================
    # 3. PROCESS ENTITY IDENTIFIER TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing entity_identifier table")
    logger.info("=" * 80)

    # Map type_id (CV term accession) to entity_id (skip if already processed)
    if already_processed:
        # type_id is already an integer entity_id, keep it as type_id
        entity_identifiers_output = entity_identifiers
        logger.info("Skipping type_id mapping (already processed)")
    else:
        entity_identifiers_mapped = _map_cv_term_column(
            entity_identifiers,
            cv_term_mapping,
            "type_id"
        )
        # Rename type_id_id back to type_id for consistency
        entity_identifiers_output = entity_identifiers_mapped.rename({"type_id_id": "type_id"})

    # Save the updated entity_identifier table
    entity_identifiers_output.write_parquet(output_dir / "entity_identifier.parquet")
    logger.info(f"✅ entity_identifier: {len(entity_identifiers_output):,} rows")

    # Note: entity_identifier_resource.parquet is already created by build_entity_identifiers.py
    # and requires no further transformation (all IDs are already correct)

    # ========================================================================
    # 3.5. BUILD SOURCE_ID -> SOURCE_ENTITY_ID MAPPING
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Building source_id -> source_entity_id mapping")
    logger.info("=" * 80)

    # Build mapping from source_id to source_entity_id
    # Sources always have local_entity_id = 1 (guaranteed by build_local_tables.py)
    source_mapping = (
        record_to_global
        .filter(pl.col("local_entity_id") == 1)
        .select(["source_id", pl.col("entity_id").alias("source_entity_id")])
    )
    logger.info(f"Built source mapping: {len(source_mapping):,} sources")

    # ========================================================================
    # 4. PROCESS MEMBERSHIP TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing membership table")
    logger.info("=" * 80)

    # Get membership files, but exclude annotation files
    membership_files = sorted([
        f for f in local_tables_dir.glob("local_membership_*.parquet")
        if "annotation" not in f.name
    ])
    logger.info(f"Found {len(membership_files)} membership files")

    membership_parts = []
    for f in membership_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # parent_id is always a local_entity_id (integer)
        # CV terms are now created as local entities, so no special handling needed

        # Join parent_id with record_to_global
        df_with_parent = df.join(
            record_to_global.rename({"local_entity_id": "parent_id"}),
            on=["source_id", "parent_id"],
            how="left"
        ).rename({"entity_id": "parent_entity_id"})

        # Join member_id with record_to_global
        df_with_member = df_with_parent.join(
            record_to_global.rename({"local_entity_id": "member_id"}),
            on=["source_id", "member_id"],
            how="left"
        ).rename({"entity_id": "member_entity_id"})

        # Join annotation_unit_id with record_to_global (if present)
        if "annotation_unit_id" in df.columns:
            # Cast annotation_unit_id to Int64 to ensure type compatibility
            df_with_member = df_with_member.with_columns(
                pl.col("annotation_unit_id").cast(pl.Int64)
            )
            df_global = df_with_member.join(
                record_to_global.rename({"local_entity_id": "annotation_unit_id"}),
                on=["source_id", "annotation_unit_id"],
                how="left"
            ).rename({"entity_id": "annotation_unit_entity_id"}).drop("annotation_unit_id")
        else:
            # Add null column if not present to ensure schema consistency
            df_global = df_with_member.with_columns(pl.lit(None, dtype=pl.Int64).alias("annotation_unit_entity_id"))

        membership_parts.append(df_global)

    membership_to_global = None  # Will store mapping from (source_id, local_membership_id) -> global membership_id

    if membership_parts:
        # Combine all sources
        memberships_combined = pl.concat(membership_parts, how="diagonal_relaxed")

        # Map source_id to source_entity_id
        rename_dict = {
            "parent_entity_id": "parent_id",
            "member_entity_id": "member_id",
        }
        if "annotation_unit_entity_id" in memberships_combined.columns:
            rename_dict["annotation_unit_entity_id"] = "annotation_unit"

        memberships_with_source_entity = (
            memberships_combined
            .join(source_mapping, on="source_id", how="left")
            .drop(["parent_id", "member_id"])
            .rename(rename_dict)
            .sort(["parent_id", "member_id", "source_entity_id"])
        )

        # Assign global sequential IDs
        memberships_with_id = memberships_with_source_entity.with_row_index("id", offset=1)

        # Create mapping from (source_id, local_membership_id) to global membership_id
        membership_to_global = (
            memberships_with_id
            .select(["source_id", "local_membership_id", "id"])
            .rename({"id": "membership_id"})
        )

        # Drop local_membership_id and source_id, rename source_entity_id to source_id for final output
        memberships_output = (
            memberships_with_id
            .drop(["local_membership_id", "source_id"])
            .rename({"source_entity_id": "source_id"})
        )

        memberships_output.write_parquet(output_dir / "membership.parquet")
        logger.info(f"✅ membership: {len(memberships_output):,} rows")
    else:
        logger.warning("No membership files found")
        pl.DataFrame().write_parquet(output_dir / "membership.parquet")

    # ========================================================================
    # 5. PROCESS MEMBERSHIP ANNOTATION TABLE
    # ========================================================================

    logger.info("\n" + "=" * 80)
    logger.info("Processing membership_annotation table")
    logger.info("=" * 80)

    membership_annot_files = sorted(local_tables_dir.glob("local_membership_annotation_*.parquet"))
    logger.info(f"Found {len(membership_annot_files)} membership_annotation files")

    membership_annot_parts = []
    for f in membership_annot_files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,} rows")

        # Join with membership_to_global to get global membership_id
        if membership_to_global is not None:
            df_global = df.join(
                membership_to_global,
                on=["source_id", "local_membership_id"],
                how="left"
            )

            # Map annotation_id (local_entity_id) -> annotation_id (global entity_id)
            # annotation_id is a local_entity_id that needs to be mapped to global entity_id
            if "annotation_id" in df.columns:
                # Cast annotation_id to Int64 to ensure type compatibility
                df_global = df_global.with_columns(
                    pl.col("annotation_id").cast(pl.Int64)
                )
                df_global = df_global.join(
                    record_to_global.rename({"local_entity_id": "annotation_id"}),
                    on=["source_id", "annotation_id"],
                    how="left"
                ).rename({"entity_id": "annotation_id_entity_id"}).drop("annotation_id")

            # Map annotation_unit (local_entity_id) -> annotation_unit (global entity_id)
            # annotation_unit is a local_entity_id that needs to be mapped to global entity_id
            if "annotation_unit" in df.columns:
                # Cast annotation_unit to Int64 to ensure type compatibility
                df_global = df_global.with_columns(
                    pl.col("annotation_unit").cast(pl.Int64)
                )
                df_global = df_global.join(
                    record_to_global.rename({"local_entity_id": "annotation_unit"}),
                    on=["source_id", "annotation_unit"],
                    how="left"
                ).rename({"entity_id": "annotation_unit_entity_id"}).drop("annotation_unit")
            else:
                df_global = df_global.with_columns(pl.lit(None, dtype=pl.Int64).alias("annotation_unit_entity_id"))

            membership_annot_parts.append(df_global)
        else:
            logger.warning(f"  Skipping {f.name} - no membership mapping available")

    if membership_annot_parts:
        # Combine all sources
        membership_annots_combined = pl.concat(membership_annot_parts, how="diagonal_relaxed")

        # Map source_id to source_entity_id and rename columns
        rename_dict = {"source_entity_id": "source_id"}
        if "annotation_id_entity_id" in membership_annots_combined.columns:
            rename_dict["annotation_id_entity_id"] = "annotation_id"
        if "annotation_unit_entity_id" in membership_annots_combined.columns:
            rename_dict["annotation_unit_entity_id"] = "annotation_unit"

        membership_annots_with_source_entity = (
            membership_annots_combined
            .join(source_mapping, on="source_id", how="left")
            .drop(["source_id", "local_membership_id", "local_membership_annotation_id"])
            .rename(rename_dict)
            .sort(["membership_id", "source_id"])
        )

        # Assign global sequential IDs
        membership_annots_output = membership_annots_with_source_entity.with_row_index("id", offset=1)

        membership_annots_output.write_parquet(output_dir / "membership_annotation.parquet")
        logger.info(f"✅ membership_annotation: {len(membership_annots_output):,} rows")
    else:
        logger.warning("No membership_annotation files found or no membership mapping available")
        pl.DataFrame().write_parquet(output_dir / "membership_annotation.parquet")

    logger.info("\n" + "=" * 80)
    logger.info("🎉 Global tables complete!")
    logger.info("=" * 80)

