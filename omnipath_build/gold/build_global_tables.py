#!/usr/bin/env python3
"""
Join canonical entity_id (record_to_global) into local tables,
resolve CV term accessions to IDs, and concatenate into global outputs.

Input:
  local_tables/
  record_to_global.parquet   (from build_entity_identifiers)
  cv_term.parquet            (CV terms with id and accession)
  entity_identifiers.parquet (for resolving is_member_of parents)

Output:
    entity_evidence.parquet      (with entity_type_id instead of entity_type)
    interaction_evidence.parquet (with interaction_type_id, detection_method_id, etc.)
    membership_evidence.parquet  (with role_id instead of role)

Note: All CV term accession strings are resolved to integer IDs at this stage.
"""

from __future__ import annotations
from pathlib import Path
import logging
import polars as pl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def join_global_ids(df: pl.DataFrame, mapping: pl.DataFrame, side: str):
    """Helper: join entity_id for entity_A or entity_B and drop local ID"""
    local_col = f"local_entity_id_{side}"
    return (
        df.join(
            mapping.rename({"local_entity_id": local_col}),
            on=["source_id", local_col],
            how="left"
        )
        .rename({"entity_id": f"entity_id_{side}"})
        .drop(local_col)
    )


def _empty_mapping(schema: dict[str, pl.datatypes.DataType]) -> pl.DataFrame:
    """Create an empty DataFrame with a predefined schema for joins."""
    return pl.DataFrame({
        name: pl.Series(name, [], dtype=dtype)
        for name, dtype in schema.items()
    })


def _map_cv_term_columns(
    df: pl.DataFrame,
    cv_term_df: pl.DataFrame,
    accession_cols: list[str]
) -> pl.DataFrame:
    """Map CV term accession columns to corresponding _id columns.

    Args:
        df: DataFrame with CV term accession columns
        cv_term_df: CV terms table with id and accession columns
        accession_cols: List of column names containing CV term accessions

    Returns:
        DataFrame with new {col}_id columns added and original accession columns dropped
    """
    result = df

    for col in accession_cols:
        if col not in df.columns:
            continue

        # Create mapping from accession to id for this column
        mapping = cv_term_df.select([
            pl.col("accession").alias(col),
            pl.col("id").alias(f"{col}_id")
        ])

        # Left join to add the _id column
        result = result.join(mapping, on=col, how="left")

        # Drop the original accession column
        result = result.drop(col)

    return result


def build_global_tables(
    local_dir: str | Path,
    mapping_file: str | Path,
    out_dir: str | Path,
    entity_identifiers_file: str | Path,
):

    local_dir = Path(local_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- load record_to_global mapping ----
    mapping = pl.read_parquet(mapping_file)
    logger.info(f"Loaded mapping with {len(mapping):,} rows")

    # ---- load entity identifiers ----
    entity_identifiers = pl.read_parquet(entity_identifiers_file)
    logger.info(f"Loaded entity identifiers with {len(entity_identifiers):,} rows")

    # ---- build CV term mapping from entity_identifiers ----
    # CV terms are now entities with id_type = "OM:0204" (CV_TERM_ACCESSION)
    # Extract CV term accessions and map them to entity_ids
    # Handle both id_type (string) and id_type_id (already resolved) cases
    if "id_type" in entity_identifiers.columns:
        cv_terms = (
            entity_identifiers
            .filter(pl.col("id_type") == "OM:0204")  # CV_TERM_ACCESSION
            .select([
                pl.col("entity_id").alias("id"),
                pl.col("id_value").alias("accession"),
            ])
            .unique(subset=["accession"])
        )
    else:
        # Already resolved: find the entity_id for "OM:0204" type and get all CV terms
        om0204_entity_id = (
            entity_identifiers
            .filter(pl.col("id_value") == "OM:0204")
            .select(pl.col("id_type_id").first())
            .item()
        )
        cv_terms = (
            entity_identifiers
            .filter(pl.col("id_type_id") == om0204_entity_id)
            .select([
                pl.col("entity_id").alias("id"),
                pl.col("id_value").alias("accession"),
            ])
            .unique(subset=["accession"])
        )
    logger.info(f"Built CV term mapping with {len(cv_terms):,} unique accessions")

    # ---- resolve id_type to id_type_id in entity_identifiers ----
    # Map id_type (string accession) to id_type_id (entity_id)
    # Only if id_type column exists (idempotency check)
    if "id_type" in entity_identifiers.columns:
        logger.info("Resolving id_type to id_type_id in entity_identifiers")
        entity_identifiers_resolved = _map_cv_term_columns(
            entity_identifiers,
            cv_terms,
            ["id_type"]
        )
        entity_identifiers_resolved.write_parquet(out_dir / "entity_identifiers.parquet")
        logger.info(f"✅ entity_identifiers: {len(entity_identifiers_resolved):,} rows (id_type resolved to id_type_id)")
    else:
        logger.info("entity_identifiers already has id_type_id (skipping resolution)")
        entity_identifiers.write_parquet(out_dir / "entity_identifiers.parquet")
        logger.info(f"✅ entity_identifiers: {len(entity_identifiers):,} rows")

    #
    # ============== ENTITY EVIDENCE ==============
    #
    files = sorted(local_dir.glob("local_entity_evidence_*.parquet"))
    logger.info(f"\nProcessing entity evidence ({len(files)} files)")
    entity_parts: list[pl.DataFrame] = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")
        entity_parts.append(df.join(mapping, on=["source_id", "local_entity_id"], how="left"))

    if entity_parts:
        entity_ev = pl.concat(entity_parts, how="diagonal_relaxed")
        entity_ev = entity_ev.with_row_index("id", offset=1)

        # Map entity_type (accession) -> entity_type_id and drop entity_type
        entity_output = _map_cv_term_columns(entity_ev, cv_terms, ["entity_type"])
        entity_output = entity_output.drop("local_entity_id")
    else:
        entity_output = pl.DataFrame()

    entity_output.write_parquet(out_dir / "entity_evidence.parquet")
    logger.info(f"✅ entity_evidence: {len(entity_output):,} rows")

    #
    # ============== INTERACTIONS ==============
    #
    files = sorted(local_dir.glob("local_interaction_evidence_*.parquet"))
    logger.info(f"\nProcessing interactions ({len(files)} files)")
    interaction_parts: list[pl.DataFrame] = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")
        df = join_global_ids(df, mapping, "a")
        df = join_global_ids(df, mapping, "b")
        interaction_parts.append(df)

    if interaction_parts:
        interactions = pl.concat(interaction_parts, how="diagonal_relaxed")
        interactions = interactions.with_row_index("id", offset=1)

        # Map CV term accessions to IDs and drop accession columns
        interaction_output = _map_cv_term_columns(
            interactions,
            cv_terms,
            ["interaction_type", "detection_method", "causal_mechanism", "causal_statement"]
        )
        interaction_output = interaction_output.drop("local_interaction_id")
    else:
        interaction_output = pl.DataFrame()

    interaction_output.write_parquet(out_dir / "interaction_evidence.parquet")
    logger.info(f"✅ interaction_evidence: {len(interaction_output):,} rows")

    #
    # ============== MEMBERSHIP EVIDENCE (UNIFIED) ==============
    #
    files = sorted(local_dir.glob("local_membership_*.parquet"))
    logger.info(f"\nProcessing unified membership evidence ({len(files)} files)")
    membership_parts: list[pl.DataFrame] = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")

        # Join parent entity with record_to_global to resolve parent_local_entity_id to global parent_entity_id
        df = df.join(
            mapping.rename({"local_entity_id": "parent_local_entity_id"}),
            on=["source_id", "parent_local_entity_id"],
            how="left"
        ).rename({"entity_id": "parent_entity_id"})

        # Join child entity with record_to_global
        df = df.join(
            mapping,
            on=["source_id", "local_entity_id"],
            how="left"
        )

        membership_parts.append(df)

    if membership_parts:
        membership = pl.concat(membership_parts, how="diagonal_relaxed")
        # Drop local columns
        membership = membership.drop(
            "parent_local_entity_id",
            "local_entity_id",
        )
        membership = membership.with_row_index("id", offset=1)

        # Map role (accession) -> role_id and drop role
        membership_output = _map_cv_term_columns(membership, cv_terms, ["role"])
        membership_output = membership_output.drop("local_membership_id")
    else:
        membership_output = pl.DataFrame()

    membership_output.write_parquet(out_dir / "membership_evidence.parquet")
    logger.info(f"✅ membership_evidence (unified): {len(membership_output):,} rows")

    # NOTE: is_member_of relationships are now unified into the membership_evidence table above
    # No separate is_member_of table is created

    logger.info("\n🎉 Global tables complete!\n")


if __name__ == "__main__":
    import typer
    typer.run(build_global_tables)
