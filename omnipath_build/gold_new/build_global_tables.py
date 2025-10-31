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
    files = sorted(local_dir.glob("local_entity_evidence_*.parquet"))
    logger.info(f"\nProcessing entity evidence ({len(files)} files)")
    parts = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")
        df = df.join(mapping, on=["source_id", "local_entity_id"], how="left")
        # Drop local_entity_id, keep only global entity_id
        df = df.drop("local_entity_id")
        parts.append(df)

    entity_ev = pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()
    entity_ev.write_parquet(out_dir / "entity_evidence.parquet")
    logger.info(f"✅ entity_evidence: {len(entity_ev):,} rows")

    #
    # ============== INTERACTIONS ==============
    #
    files = sorted(local_dir.glob("local_interaction_evidence_*.parquet"))
    logger.info(f"\nProcessing interactions ({len(files)} files)")
    parts = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")
        df = join_global_ids(df, mapping, "a")
        df = join_global_ids(df, mapping, "b")
        parts.append(df)

    interactions = pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()
    interactions.write_parquet(out_dir / "interaction_evidence.parquet")
    logger.info(f"✅ interaction_evidence: {len(interactions):,} rows")

    #
    # ============== MEMBERSHIP ==============
    #
    files = sorted(local_dir.glob("local_membership_*.parquet"))
    logger.info(f"\nProcessing membership ({len(files)} files)")
    parts = []

    for f in files:
        df = pl.read_parquet(f)
        logger.info(f"  {f.name}: {len(df):,}")

        # Map parent_local_entity_id to parent_entity_id
        df = df.join(
            mapping.rename({"local_entity_id": "parent_local_entity_id"}),
            on=["source_id", "parent_local_entity_id"],
            how="left"
        ).rename({"entity_id": "parent_entity_id"})

        # Map local_entity_id (child) to entity_id
        df = df.join(
            mapping,
            on=["source_id", "local_entity_id"],
            how="left"
        )  # This adds entity_id column for the child

        # Drop local IDs, keep only global entity_ids
        df = df.drop("parent_local_entity_id", "local_entity_id")

        parts.append(df)

    membership = pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()
    membership.write_parquet(out_dir / "membership.parquet")
    logger.info(f"✅ membership: {len(membership):,} rows")

    logger.info("\n🎉 Global tables complete!\n")


if __name__ == "__main__":
    import typer
    typer.run(build_global_tables)