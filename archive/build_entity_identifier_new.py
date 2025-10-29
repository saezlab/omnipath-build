from __future__ import annotations
from pathlib import Path
from collections.abc import Sequence
import polars as pl
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

MERGE_SAFE_IDENTIFIER_TYPES = [
    IdentifierNamespaceCv.UNIPROT.value,
    IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
    IdentifierNamespaceCv.STANDARD_INCHI.value,
]


# --------------------------------------------------------------------------- #
# Helper: extract all merge-safe keys
# --------------------------------------------------------------------------- #

def extract_merge_keys_expr() -> pl.Expr:
    """Extract all UniProt, InChIKey, and InChI values as merge_keys array."""
    return (
        pl.col("identifiers")
        .list.eval(
            pl.when(
                pl.element().struct.field("type").is_in(MERGE_SAFE_IDENTIFIER_TYPES)
            ).then(pl.element().struct.field("value"))
        )
        .list.filter(pl.element().is_not_null() & (pl.element() != ""))
        .list.unique()
    )


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #

def build_entity_registry(
    data_root: Path,
    merge_safe_types: Sequence[str] = MERGE_SAFE_IDENTIFIER_TYPES,
) -> pl.DataFrame:
    """
    Build a unified entity registry sequentially from multiple sources.

    - Extracts *all* merge-safe keys (UniProt / InChIKey / InChI)
    - Sequentially merges new sources into a growing registry
      if any overlap in merge_keys lists is found.
    - Keeps all rows without merge_keys as independent entities.
    """

    entity_registry: pl.DataFrame | None = None

    parquet_files = []
    for d in sorted(data_root.glob('*')):
        if d.is_dir():
            parquet_files.extend(sorted(d.glob('*.parquet')))

    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in subdirectories of {data_root}")

    print(f"Found {len(parquet_files)} silver_entities files to process")

    for path in parquet_files:
        print(f"\nProcessing source: {path.name}")
        df = pl.read_parquet(path)
        cols = set(df.columns)

        # Skip non-entity tables
        if 'identifiers' not in cols and 'entity_a' not in cols and 'entity_b' not in cols:
            print(f"  Skipping {path.name}: no entity columns found")
            continue

        # Handle interaction-style tables
        if 'entity_a' in cols or 'entity_b' in cols:
            entities = []
            if 'entity_a' in cols:
                entities.append(
                    df.select([
                        pl.col('entity_a').struct.field('source').alias('source'),
                        pl.col('entity_a').struct.field('identifiers').alias('identifiers'),
                    ])
                )
            if 'entity_b' in cols:
                entities.append(
                    df.select([
                        pl.col('entity_b').struct.field('source').alias('source'),
                        pl.col('entity_b').struct.field('identifiers').alias('identifiers'),
                    ])
                )
            df = pl.concat(entities, how='diagonal_relaxed')

        # Normalize source to list
        if 'source' in df.columns:
            if df.schema['source'] != pl.List(pl.String):
                df = df.with_columns(pl.col('source').cast(pl.List(pl.String)))

        # Derive merge_keys
        df = df.with_columns(extract_merge_keys_expr().alias("merge_keys"))

        total = df.height
        with_key = df.filter(pl.col("merge_keys").list.len() > 0).height
        print(f"  Total entities: {total}")
        print(f"  Entities with merge keys: {with_key} ({100*with_key/total:.1f}%)")

        # Deduplicate within source
        # Deduplicate within source (handles list[struct] columns safely)
        try:
            df = df.unique(maintain_order=True)
        except Exception:
            df = df.with_columns(pl.col("identifiers").cast(pl.Utf8).alias("_id_str"))
            df = df.unique(subset=["_id_str"], maintain_order=True).drop("_id_str")
        # Initialize registry
        if entity_registry is None:
            entity_registry = df
            continue

        print(f"  🔗 Merging {path.name} into existing registry...")

        # Split entities with and without merge keys
        reg_with = entity_registry.filter(pl.col("merge_keys").list.len() > 0)
        reg_without = entity_registry.filter(pl.col("merge_keys").list.len() == 0)
        df_with = df.filter(pl.col("merge_keys").list.len() > 0)
        df_without = df.filter(pl.col("merge_keys").list.len() == 0)

        # --- FIX: explode both sides for scalar join ---
        reg_exploded = reg_with.explode("merge_keys").rename({"merge_keys": "key"})
        df_exploded = df_with.explode("merge_keys").rename({"merge_keys": "key"})

        # Perform join on single string key
        joined = (
            reg_exploded.join(df_exploded, on="key", how="inner", suffix="_new")
            if not df_exploded.is_empty() and not reg_exploded.is_empty()
            else pl.DataFrame()
        )

        if joined.is_empty():
            print("  → No overlapping merge keys; appending all new entities.")
            entity_registry = pl.concat([entity_registry, df], how="diagonal_relaxed")
            continue

        # Collect matched keys for filtering
        matched_keys = joined.select("key").unique()

        # Rebuild merged set
        reg_matched = reg_exploded.join(matched_keys, on="key", how="inner")
        df_matched = df_exploded.join(matched_keys, on="key", how="inner")

        merged = (
            pl.concat([reg_matched, df_matched], how="diagonal_relaxed")
            .group_by("key", maintain_order=True)
            .agg([
                pl.concat_list("identifiers").alias("identifiers"),
                pl.concat_list("source").alias("source"),
            ])
            .with_columns([
                pl.col("identifiers").list.unique(),
                pl.col("source").list.unique(),
            ])
            .rename({"key": "merge_keys"})
            .with_columns(pl.col("merge_keys").map_elements(lambda x: [x], return_dtype=pl.List(pl.String)))
        )

        # Unmatched entries
        reg_unmatched = reg_with.join(reg_exploded, on="merge_keys", how="anti")
        df_unmatched = df_with.join(df_exploded, on="merge_keys", how="anti")

        # Combine all
        entity_registry = pl.concat(
            [reg_without, df_without, reg_unmatched, df_unmatched, merged],
            how="diagonal_relaxed",
        )

        print(f"  → Registry now contains {entity_registry.height:,} total entities")

    entity_registry = entity_registry.with_row_count("entity_id")
    print(f"\n✅ Entity registry built with {entity_registry.height:,} entities total.")
    return entity_registry


# --------------------------------------------------------------------------- #
# Example usage
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    root = Path("data/silver_entities")
    registry = build_entity_registry(root)
    registry.write_parquet("entity_registry.parquet", compression="zstd", statistics=True)
    print("💾 Saved entity_registry.parquet")