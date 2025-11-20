"""Build Meilisearch interaction documents aggregated by member pairs."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import EntityTypeCv

__all__ = ["build_search_interactions"]

logger = logging.getLogger(__name__)

# --- Schema Definitions ---
MAP_ENTRY_STRUCT = pl.Struct([Field("key", pl.Utf8), Field("value", pl.Utf8)])
MAP_LIST_DTYPE = pl.List(MAP_ENTRY_STRUCT)
ANNOTATION_COLS = ["terms", "values", "units"]
CATEGORIES = ["interaction", "member_a", "member_b"]
ALL_ANNOT_FIELDS = [f"{c}_annotation_{t}" for c in CATEGORIES for t in ANNOTATION_COLS]

EVIDENCE_STRUCT = pl.Struct([Field(c, MAP_LIST_DTYPE) for c in ALL_ANNOT_FIELDS])
EVIDENCE_LIST_DTYPE = pl.List(EVIDENCE_STRUCT)

def _get_id(df: pl.DataFrame, acc: str) -> int:
    try: return df.filter(pl.col("identifier") == acc)["entity_id"][0]
    except IndexError: raise ValueError(f"Accession {acc!r} not found")

def _fmt_term(id_col: str, lbl_col: str) -> pl.Expr:
    return pl.coalesce([pl.col(lbl_col), pl.lit("UNKNOWN")]) + ":" + pl.col(id_col).cast(pl.Utf8)

def build_search_interactions(global_tables_dir: Path, output_path: Path) -> Path:
    logger.info("=" * 80 + "\nBuilding Meilisearch interaction documents\n" + "=" * 80)

    # 1. Load Data
    tables = {n: pl.read_parquet(global_tables_dir / f"{n}.parquet") 
              for n in ["entity", "membership", "membership_annotation", "entity_identifier"]}
    logger.info("Loaded tables: %s", " ".join(f"{k}={len(v)}" for k, v in tables.items()))

    ent, mem, mem_ann, ent_id = tables["entity"], tables["membership"], tables["membership_annotation"], tables["entity_identifier"]
    
    # 2. Identifiers & Constants
    name_tid = _get_id(ent_id, "OM:0202")
    cv_tid = _get_id(ent_id, "OM:0201")
    intr_tid = _get_id(ent_id, EntityTypeCv.INTERACTION.value)

    # 3. Identify Interaction Pairs
    mem_intr = mem.join(
        ent.filter(pl.col("entity_type_id") == intr_tid).select(pl.col("entity_id").alias("p_id")),
        left_on="parent_id", right_on="p_id"
    ).rename({"parent_id": "interaction_id"})

    pair_base = (
        mem_intr.group_by("interaction_id")
        .agg(pl.col("member_id").unique().sort().alias("m"))
        .filter(pl.col("m").list.len() == 2)
        .select([
            pl.col("interaction_id"),
            pl.col("m").list.get(0).alias("member_a_id"),
            pl.col("m").list.get(1).alias("member_b_id"),
            (pl.col("m").list.get(0).cast(pl.Utf8) + "-" + pl.col("m").list.get(1).cast(pl.Utf8)).alias("pair_key")
        ])
    )

    if pair_base.is_empty():
        logger.warning("No interactions with two members found; writing empty file.")
        pl.DataFrame(schema={
            "interaction_key": pl.Utf8, "member_a_id": pl.Int64, "member_b_id": pl.Int64,
            "member_types": pl.List(pl.Utf8), "evidence": EVIDENCE_LIST_DTYPE
        }).write_parquet(output_path)
        return output_path

    logger.info("Identified %s interaction entities with two members", len(pair_base))

    # 4. Build Base Documents (Member Types)
    type_map = ent.join(
        ent_id.filter(pl.col("type_id") == name_tid), left_on="entity_type_id", right_on="entity_id"
    ).select(pl.col("entity_id"), _fmt_term("entity_type_id", "identifier").alias("fmt"))

    doc_base = (
        pair_base.group_by("pair_key").agg([pl.col("member_a_id").first(), pl.col("member_b_id").first()])
        .join(type_map, left_on="member_a_id", right_on="entity_id").rename({"fmt": "ta"})
        .join(type_map, left_on="member_b_id", right_on="entity_id").rename({"fmt": "tb"})
        .select("pair_key", "member_a_id", "member_b_id", pl.concat_list("ta", "tb").alias("member_types"))
    )

    # 5. Prepare Annotations
    df_ann_int = mem.join(pair_base, left_on="member_id", right_on="interaction_id").select(
        pl.col("member_id").alias("interaction_id"), "pair_key", pl.lit("interaction").alias("cat"),
        pl.col("parent_id").alias("ann_id"), "annotation_value", "annotation_unit"
    )

    df_ann_mem = mem_ann.join(
        mem_intr.join(pair_base, on="interaction_id")
        .rename({"id": "membership_id"})
        .with_columns(
            pl.when(pl.col("member_id") == pl.col("member_a_id")).then(pl.lit("member_a"))
            .when(pl.col("member_id") == pl.col("member_b_id")).then(pl.lit("member_b"))
            .alias("cat")
        ).filter(pl.col("cat").is_not_null()),
        on="membership_id"
    ).select("interaction_id", "pair_key", "cat", pl.col("annotation_id").alias("ann_id"), "annotation_value", "annotation_unit")

    all_annots = pl.concat([df_ann_int, df_ann_mem])

    # 6. Resolve Labels & Format Maps
    # Convert to list to avoid is_in DeprecationWarning
    unique_ids = pl.concat([all_annots["ann_id"], all_annots["annotation_unit"]]).drop_nulls().unique().to_list()
    logger.info("Resolving annotation metadata for %s unique annotation IDs", len(unique_ids))

    labels = (
        ent_id.filter(pl.col("entity_id").is_in(unique_ids))
        .group_by("entity_id")
        .agg([
            pl.col("identifier").filter(pl.col("type_id") == name_tid).first().alias("n"),
            pl.col("identifier").filter(pl.col("type_id") == cv_tid).first().alias("c")
        ])
        .select(pl.col("entity_id"), pl.coalesce("n", "c", pl.lit("UNKNOWN")).alias("lbl"))
    )

    evidence_rows = (
        all_annots
        .join(labels, left_on="ann_id", right_on="entity_id", how="left")
        .join(labels, left_on="annotation_unit", right_on="entity_id", how="left", suffix="_u")
        .with_columns([
            _fmt_term("ann_id", "lbl").alias("term"),
            pl.col("annotation_value").cast(pl.Utf8).alias("val"),
            _fmt_term("annotation_unit", "lbl_u").alias("unit"),
            pl.int_range(1, pl.len() + 1).cast(pl.Utf8).over("interaction_id", "pair_key", "cat").alias("k")
        ])
        .group_by("interaction_id", "pair_key", "cat")
        .agg([
            pl.struct("k", pl.col("term").alias("value")).alias("terms"),
            pl.struct("k", pl.col("val").alias("value")).filter(pl.col("val").is_not_null()).alias("values"),
            pl.struct("k", pl.col("unit").alias("value")).filter(pl.col("unit") != "UNKNOWN:unknown").alias("units")
        ])
        .pivot(on="cat", index=["interaction_id", "pair_key"], values=["terms", "values", "units"], aggregate_function="first")
    )

    # 7. Construct Evidence Structure
    # Map Target Field Name -> Pivot Column Name (e.g. 'interaction_annotation_terms' -> 'terms_interaction')
    target_to_source = {f"{c}_annotation_{t}": f"{t}_{c}" for c in CATEGORIES for t in ANNOTATION_COLS}
    available_cols = set(evidence_rows.columns)
    
    evidence_struct_expr = pl.struct([
        pl.coalesce([
            pl.col(src) if src in available_cols else pl.lit(None, dtype=MAP_LIST_DTYPE),
            pl.lit([], dtype=MAP_LIST_DTYPE)
        ]).alias(target)
        for target, src in target_to_source.items()
    ])

    final_evidence = (
        evidence_rows.select(["pair_key", evidence_struct_expr.alias("entry")])
        .group_by("pair_key")
        .agg(pl.col("entry").alias("evidence"))
    )

    total_ev = final_evidence.select(pl.col("evidence").list.len().sum()).item() if not final_evidence.is_empty() else 0
    logger.info("Aggregated %s evidence entries across %s documents", total_ev, len(final_evidence))

    # 8. Final Join & Output
    result = doc_base.join(final_evidence, on="pair_key", how="left").with_columns(
        pl.coalesce(pl.col("evidence"), pl.lit([], dtype=EVIDENCE_LIST_DTYPE)).alias("evidence")
    ).rename({"pair_key": "interaction_key"})

    result.write_parquet(output_path)
    logger.info("Wrote %s interaction documents to %s", len(result), output_path)
    return output_path

def main():
    p = argparse.ArgumentParser(description="Build Meilisearch interaction documents")
    p.add_argument("--global-tables-dir", type=Path, default=Path("databases/omnipath/output"))
    p.add_argument("--output", type=Path, default=Path("databases/omnipath/output/search_interactions.parquet"))
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()
    
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    build_search_interactions(args.global_tables_dir, args.output)

if __name__ == "__main__":
    main()