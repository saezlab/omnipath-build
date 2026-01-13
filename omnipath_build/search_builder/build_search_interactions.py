"""Build Meilisearch interaction documents aggregated by member pairs.

Updated for new entity_instance schema:
- entity_annotation links to entity_instance, not entity directly
- membership has polymorphic columns (parent_entity_id/parent_instance_id, member_entity_id/member_instance_id)
- membership_annotation table is removed - annotations are now on member instances via entity_annotation
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import (
    EntityTypeCv,
    POSITIVE_SIGN_ACCESSIONS,
    NEGATIVE_SIGN_ACCESSIONS,
    SOURCE_ROLE_ACCESSIONS,
    TARGET_ROLE_ACCESSIONS,
    ACTIVATORY_PARAMETER_ACCESSIONS,
    INHIBITORY_PARAMETER_ACCESSIONS,
)

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

DIRECTION_STRUCT = pl.Struct([Field("direction", pl.Utf8), Field("sign", pl.Int8)])
DIRECTION_LIST_DTYPE = pl.List(DIRECTION_STRUCT)

def _get_id(df: pl.DataFrame, acc: str) -> int:
    try: return df.filter(pl.col("identifier") == acc)["entity_id"][0]
    except IndexError: raise ValueError(f"Accession {acc!r} not found")

def _fmt_term(id_col: str, lbl_col: str) -> pl.Expr:
    return pl.coalesce([pl.col(lbl_col), pl.lit("UNKNOWN")]) + ":" + pl.col(id_col).cast(pl.Utf8)

def _build_sign_df(pos_accs: frozenset[str], neg_accs: frozenset[str], name: str = "sign") -> pl.DataFrame:
    """Build DataFrame mapping accession to sign (-1, 1)."""
    accessions = list(pos_accs | neg_accs)
    if not accessions:
        return pl.DataFrame(schema={"accession": pl.Utf8, name: pl.Int8})

    # Create mapping directly from sets
    data = []
    for acc in pos_accs:
        data.append({"accession": acc, name: 1})
    for acc in neg_accs:
        data.append({"accession": acc, name: -1})
        
    return pl.DataFrame(data).with_columns(pl.col(name).cast(pl.Int8))

def _build_causal_traits() -> pl.DataFrame:
    """Build DataFrame mapping accession to causal traits (sign, is_source, is_target)."""
    # Simply map the constant sets
    data = []
    
    # Process all relevant accessions
    all_accs = POSITIVE_SIGN_ACCESSIONS | NEGATIVE_SIGN_ACCESSIONS | SOURCE_ROLE_ACCESSIONS | TARGET_ROLE_ACCESSIONS
    
    for acc in all_accs:
        data.append({
            "accession": acc,
            "sign": 1 if acc in POSITIVE_SIGN_ACCESSIONS else (-1 if acc in NEGATIVE_SIGN_ACCESSIONS else None),
            "is_source": acc in SOURCE_ROLE_ACCESSIONS,
            "is_target": acc in TARGET_ROLE_ACCESSIONS
        })

    if not data:
        return pl.DataFrame(schema={"accession": pl.Utf8, "sign": pl.Int8, "is_source": pl.Boolean, "is_target": pl.Boolean})

    return pl.DataFrame(data).with_columns([pl.col("sign").cast(pl.Int8), pl.col("is_source").cast(pl.Boolean), pl.col("is_target").cast(pl.Boolean)])

def _get_param_directions(doc_base: pl.DataFrame, df_ann_int: pl.DataFrame, param_signs: pl.DataFrame) -> pl.DataFrame:
    """Vectorized computation of parameter-based directions (Small Mol -> Protein)."""
    if param_signs.is_empty():
        return pl.DataFrame(schema={"pair_key": pl.Utf8, "direction": pl.Utf8, "sign": pl.Int8})

    # 1. Extract raw labels for vectorized string matching
    types_df = doc_base.select(
        "pair_key",
        pl.col("member_types").list.get(0).str.split(":").list.first().str.to_lowercase().str.strip_chars().alias("t_a"),
        pl.col("member_types").list.get(1).str.split(":").list.first().str.to_lowercase().str.strip_chars().alias("t_b")
    )

    # 2. Identify roles (Small Molecule -> Protein)
    is_sm_a = types_df["t_a"].str.contains("small molecule")
    is_sm_b = types_df["t_b"].str.contains("small molecule")
    is_pro_a = types_df["t_a"] == "protein"
    is_pro_b = types_df["t_b"] == "protein"

    dir_map = types_df.with_columns([
        pl.when(is_sm_a & is_pro_b).then(pl.lit("a-b"))
        .when(is_sm_b & is_pro_a).then(pl.lit("b-a"))
        .alias("direction")
    ]).filter(pl.col("direction").is_not_null()).select("pair_key", "direction")

    if dir_map.is_empty():
        return pl.DataFrame(schema={"pair_key": pl.Utf8, "direction": pl.Utf8, "sign": pl.Int8})

    # 3. Join with interaction annotations and signs
    return (
        df_ann_int
        .join(param_signs, left_on="ann_id", right_on="accession", how="inner")
        .join(dir_map, on="pair_key", how="inner")
        .select("pair_key", "direction", "sign")
    )


def _resolve_member_entity_id(mem: pl.DataFrame, inst: pl.DataFrame) -> pl.DataFrame:
    """Resolve member to entity_id, handling both direct entity and instance references."""
    return (
        mem
        .join(
            inst.select([
                pl.col("id").alias("member_instance_id"),
                pl.col("entity_id").alias("instance_entity_id")
            ]),
            on="member_instance_id",
            how="left"
        )
        .with_columns(
            pl.coalesce([
                pl.col("instance_entity_id"),
                pl.col("member_entity_id")
            ]).alias("member_id")
        )
    )


def _resolve_parent_entity_id(mem: pl.DataFrame, inst: pl.DataFrame) -> pl.DataFrame:
    """Resolve parent to entity_id, handling both direct entity and instance references."""
    return (
        mem
        .join(
            inst.select([
                pl.col("id").alias("parent_instance_id"),
                pl.col("entity_id").alias("parent_instance_entity_id")
            ]),
            on="parent_instance_id",
            how="left"
        )
        .with_columns(
            pl.coalesce([
                pl.col("parent_instance_entity_id"),
                pl.col("parent_entity_id")
            ]).alias("parent_id")
        )
    )


def build_search_interactions(global_tables_dir: Path, output_path: Path) -> Path:
    logger.info("=" * 80 + "\nBuilding Meilisearch interaction documents\n" + "=" * 80)

    # 1. Load Data
    tables = {n: pl.read_parquet(global_tables_dir / f"{n}.parquet")
              for n in ["entity", "membership", "entity_identifier", "entity_instance", "entity_annotation"]}
    logger.info("Loaded tables: %s", " ".join(f"{k}={len(v)}" for k, v in tables.items()))

    # Load CV term label mappings
    logger.info("Loading CV term label mappings...")
    if (global_tables_dir / "cv_terms.parquet").exists():
        cv_terms = pl.read_parquet(global_tables_dir / "cv_terms.parquet")
        logger.info(f"Loaded {len(cv_terms)} CV term labels")
    else:
        logger.warning(f"cv_terms.parquet not found in {global_tables_dir}. Labels might be missing.")
        cv_terms = pl.DataFrame(schema={"accession": pl.Utf8, "label": pl.Utf8})

    # Create polars DataFrames for joining
    entity_type_labels = cv_terms.select([
        pl.col("accession").alias("entity_type"),
        pl.col("label").alias("entity_type_label")
    ])

    annotation_labels = cv_terms.select([
        pl.col("accession").alias("cv_term_accession"),
        pl.col("label").alias("cv_term_label")
    ])

    ent = tables["entity"]
    mem_raw = tables["membership"]
    ent_id = tables["entity_identifier"]
    inst = tables["entity_instance"]
    ent_annot = tables["entity_annotation"]
    
    # Resolve polymorphic membership columns
    mem = _resolve_member_entity_id(mem_raw, inst)
    mem = _resolve_parent_entity_id(mem, inst)
    
    # 2. Identifiers & Constants
    # name_tid is now string "OM:0202"
    name_tid = "OM:0202"
    # cv_tid removal
    # intr_tid is now string accession
    intr_tid = EntityTypeCv.INTERACTION.value

    # 3. Identify Interaction Pairs
    mem_intr = mem.join(
        ent.filter(pl.col("entity_type") == intr_tid).select(pl.col("entity_id").alias("p_id")),
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
            "member_types": pl.List(pl.Utf8), "evidence": EVIDENCE_LIST_DTYPE,
            "directions": DIRECTION_LIST_DTYPE
        }).write_parquet(output_path)
        return output_path

    logger.info("Identified %s interaction entities with two members", len(pair_base))

    # 4. Build Base Documents (Member Types)
    # type_map: entity_id -> fmt (Label:Accession)

    type_map = (
        ent
        .join(entity_type_labels, on="entity_type", how="left")
        .with_columns(
            # Use label if available, else use accession:accession format
            pl.coalesce([
                pl.col("entity_type_label"),
                (pl.col("entity_type") + ":" + pl.col("entity_type"))
            ]).alias("fmt")
        )
        .select(pl.col("entity_id"), pl.col("fmt"))
    )

    doc_base = (
        pair_base.group_by("pair_key").agg([pl.col("member_a_id").first(), pl.col("member_b_id").first()])
        .join(type_map, left_on="member_a_id", right_on="entity_id").rename({"fmt": "ta"})
        .join(type_map, left_on="member_b_id", right_on="entity_id").rename({"fmt": "tb"})
        .select("pair_key", "member_a_id", "member_b_id", pl.concat_list("ta", "tb").alias("member_types"))
    )

    # 5. Prepare Annotations
    # In new schema, annotations come from two sources:
    # A. Interaction annotations: annotations on the interaction entity's instance
    # B. Member annotations: annotations on member entity instances (via member_instance_id in membership)
    
    # Get interaction entity -> instance mapping for interaction annotations
    interaction_instances = (
        inst
        .select([
            pl.col("entity_id").alias("interaction_id"),
            pl.col("id").alias("interaction_instance_id"),
        ])
    )
    
    # A. Interaction annotations (from interaction entity's instances)
    df_ann_int_raw = (
        pair_base.select(["interaction_id", "pair_key"])
        .join(interaction_instances, on="interaction_id", how="left")
        .join(
            ent_annot.select([
                pl.col("instance_id"),
                pl.col("cv_term_accession").alias("ann_id"),
                pl.col("value").alias("annotation_value"),
                # unit_accession if present, else null
                pl.col("unit_accession").alias("annotation_unit") if "unit_accession" in ent_annot.columns else pl.lit(None, pl.Utf8).alias("annotation_unit"),
            ]),
            left_on="interaction_instance_id",
            right_on="instance_id",
            how="inner"
        )
    )
    
    df_ann_int = df_ann_int_raw.select([
        pl.col("interaction_id"),
        pl.col("pair_key"),
        pl.lit("interaction").alias("cat"),
        pl.col("ann_id"),
        pl.col("annotation_value"),
        pl.col("annotation_unit"),
    ])

    # B. Member annotations (from member entity instances via membership)
    # Get member instance IDs from membership table
    mem_with_instances = (
        mem_intr
        .join(pair_base, on="interaction_id")
        .select([
            "interaction_id",
            "pair_key", 
            "member_id",
            "member_a_id",
            "member_b_id",
            "member_instance_id",
        ])
        .with_columns(
            pl.when(pl.col("member_id") == pl.col("member_a_id")).then(pl.lit("member_a"))
            .when(pl.col("member_id") == pl.col("member_b_id")).then(pl.lit("member_b"))
            .alias("cat")
        )
        .filter(pl.col("cat").is_not_null())
        .filter(pl.col("member_instance_id").is_not_null())
    )
    
    df_ann_mem = (
        mem_with_instances
        .join(
            ent_annot.select([
                pl.col("instance_id"),
                pl.col("cv_term_accession").alias("ann_id"),
                pl.col("value").alias("annotation_value"),
                pl.col("unit_accession").alias("annotation_unit") if "unit_accession" in ent_annot.columns else pl.lit(None, pl.Utf8).alias("annotation_unit"),
            ]),
            left_on="member_instance_id",
            right_on="instance_id",
            how="inner"
        )
        .select([
            "interaction_id",
            "pair_key",
            "cat",
            "ann_id",
            "annotation_value",
            "annotation_unit",
        ])
    )

    all_annots = pl.concat([df_ann_int, df_ann_mem])

    # 6. Resolve Labels & Format Maps
    evidence_rows = (
        all_annots
        .join(annotation_labels, left_on="ann_id", right_on="cv_term_accession", how="left")
        # Join again to get unit labels
        .join(
            annotation_labels.rename({"cv_term_accession": "unit_acc", "cv_term_label": "unit_label"}),
            left_on="annotation_unit",
            right_on="unit_acc",
            how="left"
        )
        .with_columns([
            # Use label if available, else use accession:accession format
            pl.coalesce([
                pl.col("cv_term_label"),
                (pl.col("ann_id") + ":" + pl.col("ann_id"))
            ]).alias("term"),
            pl.col("annotation_value").cast(pl.Utf8).alias("val"),
            # Format unit with label if available
            pl.when(pl.col("annotation_unit").is_not_null())
            .then(
                pl.coalesce([
                    pl.col("unit_label"),
                    pl.col("annotation_unit")
                ]) + ":" + pl.col("annotation_unit")
            )
            .alias("unit"),
            pl.int_range(1, pl.len() + 1).cast(pl.Utf8).over("interaction_id", "pair_key", "cat").alias("k")
        ])
        .group_by("interaction_id", "pair_key", "cat")
        .agg([
            pl.struct("k", pl.col("term").alias("value")).alias("terms"),
            pl.struct("k", pl.col("val").alias("value")).filter(pl.col("val").is_not_null()).alias("values"),
            pl.struct("k", pl.col("unit").alias("value")).filter(pl.col("unit").is_not_null()).alias("units")
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

    # 8. Compute Directions (Vectorized)
    logger.info("Computing direction and sign information")

    # A. Build Reference DataFrames
    causal_traits = _build_causal_traits()

    # Create lookup DataFrames instead of dicts for joins
    term_sign_df = _build_sign_df(POSITIVE_SIGN_ACCESSIONS, NEGATIVE_SIGN_ACCESSIONS, name="term_sign")
    param_sign_df = _build_sign_df(ACTIVATORY_PARAMETER_ACCESSIONS, INHIBITORY_PARAMETER_ACCESSIONS, name="sign")

    # B. Member Annotation Directions (Causal Traits)
    # Vectorized approach: Join -> Calculate Direction -> Select
    df_dirs_mem = pl.DataFrame(schema={"pair_key": pl.Utf8, "direction": pl.Utf8, "sign": pl.Int8})

    if not causal_traits.is_empty():
        df_dirs_mem = (
            df_ann_mem
            .join(causal_traits, left_on="ann_id", right_on="accession", how="inner")
            .with_columns([
                pl.when((pl.col("cat") == "member_a") & pl.col("is_source")).then(pl.lit("a-b"))
                .when((pl.col("cat") == "member_b") & pl.col("is_target")).then(pl.lit("a-b"))
                .when((pl.col("cat") == "member_b") & pl.col("is_source")).then(pl.lit("b-a"))
                .when((pl.col("cat") == "member_a") & pl.col("is_target")).then(pl.lit("b-a"))
                .alias("direction")
            ])
            .filter(pl.col("direction").is_not_null())
            .select("pair_key", "direction", "sign")
        )

    # C. Parameter-Based Directions (Small Molecule -> Protein)
    # Replaces the slow python loop
    df_dirs_param = _get_param_directions(doc_base, df_ann_int, param_sign_df)

    # D. Combine and Aggregate Signs
    all_dirs = pl.concat([df_dirs_mem, df_dirs_param])

    directions_df = pl.DataFrame(schema={"pair_key": pl.Utf8, "directions": DIRECTION_LIST_DTYPE})

    if not all_dirs.is_empty():
        # 1. Aggregate explicit signs (Member + Param)
        # Logic: If a pair+direction has both +1 and -1, it becomes 0. Else 1 or -1.
        base_signs = (
            all_dirs
            .group_by("pair_key", "direction")
            .agg([
                pl.col("sign").eq(1).any().alias("has_pos"),
                pl.col("sign").eq(-1).any().alias("has_neg")
            ])
            .with_columns(
                pl.when(pl.col("has_pos") & pl.col("has_neg")).then(pl.lit(0, dtype=pl.Int8))
                .when(pl.col("has_pos")).then(pl.lit(1, dtype=pl.Int8))
                .when(pl.col("has_neg")).then(pl.lit(-1, dtype=pl.Int8))
                .otherwise(pl.lit(None, dtype=pl.Int8))  # Keep None for term enrichment
                .alias("sign")
            )
        )

        # 2. Interaction Term Sign Enhancement (Fallback)
        # If direction exists but sign is null (or generic), check interaction terms
        if not term_sign_df.is_empty():
            # Calculate term sign per pair
            pair_term_signs = (
                df_ann_int
                .join(term_sign_df, left_on="ann_id", right_on="accession", how="inner")
                .group_by("pair_key")
                .agg([
                    pl.col("term_sign").eq(1).any().alias("has_pos"),
                    pl.col("term_sign").eq(-1).any().alias("has_neg")
                ])
                .with_columns(
                    pl.when(pl.col("has_pos") & pl.col("has_neg")).then(pl.lit(0, dtype=pl.Int8))
                    .when(pl.col("has_pos")).then(pl.lit(1, dtype=pl.Int8))
                    .when(pl.col("has_neg")).then(pl.lit(-1, dtype=pl.Int8))
                    .alias("combined_term_sign")
                )
                .select("pair_key", "combined_term_sign")
            )

            # Join back to directions and coalesce
            base_signs = (
                base_signs
                .join(pair_term_signs, on="pair_key", how="left")
                .with_columns(pl.coalesce(pl.col("sign"), pl.col("combined_term_sign")).alias("sign"))
                .select("pair_key", "direction", "sign")
            )

        # 3. Final Nesting
        directions_df = (
            base_signs
            .filter(pl.col("sign").is_not_null())  # Only keep entries where we successfully determined a sign
            .select([
                pl.col("pair_key"),
                pl.struct(["direction", "sign"]).alias("dir_entry")
            ])
            .group_by("pair_key")
            .agg(pl.col("dir_entry").alias("directions"))
        )

    logger.info("Computed directions for %s documents", len(directions_df))

    # 9. Final Join & Output
    result = (
        doc_base
        .join(final_evidence, on="pair_key", how="left")
        .join(directions_df, on="pair_key", how="left")
        .with_columns([
            pl.coalesce(pl.col("evidence"), pl.lit([], dtype=EVIDENCE_LIST_DTYPE)).alias("evidence"),
            pl.coalesce(pl.col("directions"), pl.lit([], dtype=DIRECTION_LIST_DTYPE)).alias("directions")
        ])
        .rename({"pair_key": "interaction_key"})
    )

    # 10. Add Flattened Fields for Meilisearch Filtering
    logger.info("Computing flattened filter fields for Meilisearch")

    # First compute direction/sign flags which can be done in-place
    result = result.with_columns([
        (pl.col("directions").list.len() > 0).alias("has_direction"),
        # Mixed sign (0) counts as both positive and negative
        (pl.col("directions").list.eval((pl.element().struct.field("sign") == 1) | (pl.element().struct.field("sign") == 0)).list.any()).alias("has_positive_sign"),
        (pl.col("directions").list.eval((pl.element().struct.field("sign") == -1) | (pl.element().struct.field("sign") == 0)).list.any()).alias("has_negative_sign"),
    ])

    # Flatten interaction annotation terms (requires explode + group_by pattern)
    # Exclude terms that have associated values - those are key-value pairs, not standalone terms
    temp_terms = (
        result.select([
            "interaction_key",
            "evidence"
        ])
        .explode("evidence")
        .select([
            "interaction_key",
            # Extract keys that have values (the "k" field from value entries)
            pl.col("evidence").struct.field("interaction_annotation_values")
            .list.eval(pl.element().struct.field("k"))
            .alias("value_keys"),
            # Extract all term entries
            pl.col("evidence").struct.field("interaction_annotation_terms")
            .alias("terms")
        ])
        .explode("terms")
        .select([
            "interaction_key",
            pl.col("value_keys"),
            pl.col("terms").struct.field("k").alias("term_key"),
            pl.col("terms").struct.field("value").alias("term_value")
        ])
        # Filter: keep terms whose key is NOT in the value_keys list
        .filter(
            pl.when(pl.col("value_keys").is_null())
            .then(pl.lit(True))
            .otherwise(~pl.col("term_key").is_in(pl.col("value_keys")))
        )
        .group_by("interaction_key")
        .agg(pl.col("term_value").unique().alias("interaction_annotation_terms"))
    )

    result = result.join(temp_terms, on="interaction_key", how="left")

    result.write_parquet(output_path)
    logger.info("Wrote %s interaction documents to %s", len(result), output_path)
    return output_path

def main():
    p = argparse.ArgumentParser(description="Build Meilisearch interaction documents")
    p.add_argument("--global-tables-dir", type=Path, default=Path("omnipath_build/data/gold"))
    p.add_argument("--output", type=Path, default=Path("omnipath_build/data/gold/search_interactions.parquet"))
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()
    
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    build_search_interactions(args.global_tables_dir, args.output)

if __name__ == "__main__":
    main()