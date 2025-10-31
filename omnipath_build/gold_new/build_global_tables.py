#!/usr/bin/env python3
"""
Join canonical entity_id (record_to_global) into local tables
and concatenate into global outputs.

Input:
  local_tables/
  record_to_global.parquet   (from build_entity_identifiers)

Output:
    entity_evidence.parquet
    interaction_evidence.parquet
    membership.parquet
    evidence_reference.parquet

Note: global entity_identifiers already exists and is not touched here.
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


def build_global_tables(local_dir: str | Path, mapping_file: str | Path, out_dir: str | Path):

    local_dir = Path(local_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- load record_to_global mapping ----
    mapping = pl.read_parquet(mapping_file)
    logger.info(f"Loaded mapping with {len(mapping):,} rows")

    #
    # ============== ENTITY EVIDENCE ==============
    #
    files_all = sorted(local_dir.glob("local_entity_evidence_*.parquet"))
    files = [f for f in files_all if "_reference_" not in f.name]
    skipped = len(files_all) - len(files)
    logger.info(f"\nProcessing entity evidence ({len(files)} files)")
    if skipped:
        logger.info(f"  Skipped {skipped} reference-only files")
    entity_parts: list[pl.DataFrame] = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")
        entity_parts.append(df.join(mapping, on=["source_id", "local_entity_id"], how="left"))

    if entity_parts:
        entity_ev = pl.concat(entity_parts, how="diagonal_relaxed")
        entity_ev = entity_ev.with_row_index("id", offset=1)
        entity_map = entity_ev.select(["source_id", "local_entity_id", "id"]).rename({"id": "entity_evidence_id"})
        entity_output = entity_ev.drop("local_entity_id")
    else:
        entity_output = pl.DataFrame()
        entity_map = _empty_mapping({
            "source_id": pl.Int64,
            "local_entity_id": pl.Int64,
            "entity_evidence_id": pl.Int64,
        })

    entity_output.write_parquet(out_dir / "entity_evidence.parquet")
    logger.info(f"✅ entity_evidence: {len(entity_output):,} rows")

    #
    # ============== INTERACTIONS ==============
    #
    files_all = sorted(local_dir.glob("local_interaction_evidence_*.parquet"))
    files = [f for f in files_all if "_reference_" not in f.name]
    skipped = len(files_all) - len(files)
    logger.info(f"\nProcessing interactions ({len(files)} files)")
    if skipped:
        logger.info(f"  Skipped {skipped} reference-only files")
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
        interaction_map = interactions.select(["source_id", "local_interaction_id", "id"]).rename({"id": "interaction_evidence_id"})
        interaction_output = interactions.drop("local_interaction_id")
    else:
        interaction_output = pl.DataFrame()
        interaction_map = _empty_mapping({
            "source_id": pl.Int64,
            "local_interaction_id": pl.Int64,
            "interaction_evidence_id": pl.Int64,
        })

    interaction_output.write_parquet(out_dir / "interaction_evidence.parquet")
    logger.info(f"✅ interaction_evidence: {len(interaction_output):,} rows")

    #
    # ============== MEMBERSHIP ==============
    #
    files = sorted(local_dir.glob("local_membership_*.parquet"))
    logger.info(f"\nProcessing membership ({len(files)} files)")
    membership_parts: list[pl.DataFrame] = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")

        df = df.join(
            mapping.rename({"local_entity_id": "parent_local_entity_id"}),
            on=["source_id", "parent_local_entity_id"],
            how="left"
        ).rename({"entity_id": "parent_entity_id"})

        df = df.join(
            mapping,
            on=["source_id", "local_entity_id"],
            how="left"
        )

        membership_parts.append(df)

    if membership_parts:
        membership = pl.concat(membership_parts, how="diagonal_relaxed")
        membership = membership.drop("parent_local_entity_id", "local_entity_id")
        membership = membership.with_row_index("id", offset=1)
        membership_map = membership.select(["source_id", "local_membership_id", "id"]).rename({"id": "membership_id"})
        membership_output = membership.drop("local_membership_id")
    else:
        membership_output = pl.DataFrame()
        membership_map = _empty_mapping({
            "source_id": pl.Int64,
            "local_membership_id": pl.Int64,
            "membership_id": pl.Int64,
        })

    membership_output.write_parquet(out_dir / "membership.parquet")
    logger.info(f"✅ membership: {len(membership_output):,} rows")

    #
    # ============== EVIDENCE REFERENCES ==============
    #
    logger.info("\nProcessing evidence references")
    evidence_parts: list[pl.DataFrame] = []

    entity_ref_files = sorted(local_dir.glob("local_entity_evidence_reference_*.parquet"))
    logger.info(f"  Entity reference files: {len(entity_ref_files)}")
    for f in entity_ref_files:
        df = pl.read_parquet(f)
        logger.info(f"    {f.name}: {len(df):,}")
        if not len(df):
            continue
        joined = df.join(entity_map, on=["source_id", "local_entity_id"], how="inner")
        if not len(joined):
            continue
        evidence_parts.append(
            joined.select(
                pl.col("reference_id"),
                pl.col("entity_evidence_id"),
                pl.lit(None, dtype=pl.Int64).alias("interaction_evidence_id"),
                pl.lit(None, dtype=pl.Int64).alias("membership_id"),
            )
        )

    interaction_ref_files = sorted(local_dir.glob("local_interaction_evidence_reference_*.parquet"))
    logger.info(f"  Interaction reference files: {len(interaction_ref_files)}")
    for f in interaction_ref_files:
        df = pl.read_parquet(f)
        logger.info(f"    {f.name}: {len(df):,}")
        if not len(df):
            continue
        joined = df.join(interaction_map, on=["source_id", "local_interaction_id"], how="inner")
        if not len(joined):
            continue
        evidence_parts.append(
            joined.select(
                pl.col("reference_id"),
                pl.lit(None, dtype=pl.Int64).alias("entity_evidence_id"),
                pl.col("interaction_evidence_id"),
                pl.lit(None, dtype=pl.Int64).alias("membership_id"),
            )
        )

    membership_ref_files = sorted(local_dir.glob("local_membership_reference_*.parquet"))
    if membership_ref_files:
        logger.info(f"  Membership reference files: {len(membership_ref_files)}")
    for f in membership_ref_files:
        df = pl.read_parquet(f)
        logger.info(f"    {f.name}: {len(df):,}")
        if not len(df):
            continue
        joined = df.join(membership_map, on=["source_id", "local_membership_id"], how="inner")
        if not len(joined):
            continue
        evidence_parts.append(
            joined.select(
                pl.col("reference_id"),
                pl.lit(None, dtype=pl.Int64).alias("entity_evidence_id"),
                pl.lit(None, dtype=pl.Int64).alias("interaction_evidence_id"),
                pl.col("membership_id"),
            )
        )

    if evidence_parts:
        evidence_reference = pl.concat(evidence_parts, how="diagonal_relaxed").unique()
        evidence_reference = evidence_reference.with_row_index("id", offset=1)
        evidence_reference = evidence_reference.select(
            [
                "id",
                "reference_id",
                "entity_evidence_id",
                "interaction_evidence_id",
                "membership_id",
            ]
        )
    else:
        evidence_reference = pl.DataFrame({
            "id": pl.Series("id", [], dtype=pl.Int64),
            "reference_id": pl.Series("reference_id", [], dtype=pl.Int64),
            "entity_evidence_id": pl.Series("entity_evidence_id", [], dtype=pl.Int64),
            "interaction_evidence_id": pl.Series("interaction_evidence_id", [], dtype=pl.Int64),
            "membership_id": pl.Series("membership_id", [], dtype=pl.Int64),
        })

    evidence_reference.write_parquet(out_dir / "evidence_reference.parquet")
    logger.info(f"✅ evidence_reference: {len(evidence_reference):,} rows")

    logger.info("\n🎉 Global tables complete!\n")


if __name__ == "__main__":
    import typer
    typer.run(build_global_tables)
