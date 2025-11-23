"""Build Meilisearch entity documents from global tables (Lazy/Streaming)."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import (
    build_cv_term_mapping,
    build_accession_to_entity_id_sets,
    build_entity_type_label_mapping,
)

__all__ = ["build_search_entities"]

logger = logging.getLogger(__name__)

# Schema Constants
ID_STRUCT = pl.Struct([Field('key', pl.Utf8), Field('value', pl.Utf8)])
ID_LIST_DTYPE = pl.List(ID_STRUCT)
STR_LIST = pl.List(pl.Utf8)
INT_LIST = pl.List(pl.Int64)

def _agg_lazy(
    ldf: pl.LazyFrame, 
    filter_col: str, 
    filter_ids: frozenset[int], 
    val_col: str, 
    out_col: str,
    group_col: str = "entity_id"
) -> pl.LazyFrame | None:
    """Helper to create a lazy aggregation plan."""
    if not filter_ids:
        return None
    
    plan = (
        ldf.filter(pl.col(filter_col).is_in(list(filter_ids)))
        .group_by(group_col)
        .agg(pl.col(val_col).unique().sort().alias(out_col))
    )
    
    if group_col != "entity_id":
        return plan.rename({group_col: "entity_id"})
    return plan

def build_search_entities(global_tables_dir: Path, output_path: Path) -> Path:
    logger.info("=" * 80 + "\nBuilding Meilisearch entity documents (Lazy Execution)\n" + "=" * 80)

    # 1. Load Metadata Eagerly (for logic/sets)
    # We need specific sets of IDs to build the filter logic
    logger.info("Loading metadata for mapping generation...")
    ident_file = global_tables_dir / "entity_identifier.parquet"
    
    cv_map_df = build_cv_term_mapping(ident_file)
    type_labels = build_entity_type_label_mapping(ident_file, cv_map_df)
    id_sets = build_accession_to_entity_id_sets(cv_map_df)
    
    logger.info("Metadata ready. CV Terms: %s", len(cv_map_df))

    # 2. Setup Lazy Scans
    # scan_parquet creates a LazyFrame; no data is read until 'sink_parquet' is called
    ent = pl.scan_parquet(global_tables_dir / "entity.parquet")
    ident = pl.scan_parquet(global_tables_dir / "entity_identifier.parquet")
    res = pl.scan_parquet(global_tables_dir / "entity_identifier_resource.parquet")
    mem = pl.scan_parquet(global_tables_dir / "membership.parquet")
    mem_annot = pl.scan_parquet(global_tables_dir / "membership_annotation.parquet")

    # Join membership with annotations to get values and types
    # IMPORTANT: Both membership and membership_annotation have annotation_value columns:
    # - membership.annotation_value = annotations on the pathway itself (e.g., URLs)
    # - membership_annotation.annotation_value = annotations on the membership relationship (e.g., step order "1")
    # We need to use the annotation_value from membership_annotation
    # To avoid ambiguity, we select and rename columns from membership_annotation before the join
    mem_annot_renamed = mem_annot.select([
        pl.col("membership_id"),
        pl.col("annotation_id").alias("annot_id"),
        pl.col("annotation_value").alias("annot_val"),
        pl.col("annotation_unit"),
        pl.col("source_id").alias("annot_source_id"),
    ])
    
    mem_joined = mem.join(mem_annot_renamed, left_on="id", right_on="membership_id", how="left")
    
    # Convert metadata for Lazy Joins
    cv_map_lz = cv_map_df.lazy()
    
    # 3. Base Entity Processing
    # Fix Identifier Types: Map string type_ids to int
    ident_schema = ident.collect_schema() # Lightweight schema fetch
    if ident_schema.get("type_id") == pl.Utf8:
        ident = ident.rename({"type_id": "acc"}).join(
            cv_map_lz.rename({"accession": "acc", "entity_id": "tid"}), 
            on="acc", how="left"
        ).drop("acc").rename({"tid": "type_id"}).with_columns(pl.col("type_id").cast(pl.Int64))

    # Base Table: Filter non-interactions & Format Entity Types
    cv_accs = cv_map_lz.select(pl.col("entity_id").alias("tid"), pl.col("accession").alias("acc"))
    
    base = (
        ent.filter(pl.col("entity_type_id") != id_sets['interaction_type'])
        .join(cv_accs, left_on="entity_type_id", right_on="tid", how="left")
        .select(
            "entity_id",
            (pl.col("acc").replace(type_labels, default=pl.col("acc")) + ":" + pl.col("entity_type_id").cast(pl.Utf8)).alias("entity_type")
        )
    )

    # 4. Define Aggregations (Lazy Plans)
    lazy_joins = []

    # 4a. Identifiers (Names, Synonyms, Genes)
    for k, col in [('names', 'names'), ('synonyms', 'synonyms'), ('gene_symbols', 'gene_symbols')]:
        lazy_joins.append(_agg_lazy(ident, "type_id", id_sets[k], "identifier", col))

    # 4b. Membership Text (Descriptions, References)
    # These are stored directly in membership.annotation_value, not in membership_annotation
    # So we use the original mem table, not mem_joined
    mem_valid = mem.filter(pl.col("annotation_value").is_not_null())
    for k, col in [('descriptions', 'descriptions'), ('references', 'references')]:
        lazy_joins.append(_agg_lazy(mem_valid, "parent_id", id_sets[k], "annotation_value", col, group_col="member_id"))

    # 4c. NCBI Tax ID
    if id_sets['ncbi_tax_id']:
        lazy_joins.append(
            mem.filter(pl.col("parent_id").is_in(list(id_sets['ncbi_tax_id'])))
            .group_by("member_id").agg(pl.col("annotation_value").first().alias("ncbi_tax_id"))
            .rename({"member_id": "entity_id"})
        )

    # 4d. Structural Memberships & Interactions
    # Join memberships to entity to check parent types
    mem_types = mem.join(ent.rename({"entity_id": "pid", "entity_type_id": "ptid"}), left_on="parent_id", right_on="pid")

    for k, col in [('complex_type', 'complexes'), ('cv_term_type', 'cv_terms'), ('pathway_type', 'pathways'), ('reaction_type', 'reactions')]:
        if id_sets[k]:
            lazy_joins.append(
                mem_types.filter(pl.col("ptid") == id_sets[k])
                .group_by("member_id").agg(pl.col("parent_id").unique().sort().alias(col))
                .rename({"member_id": "entity_id"})
            )

    # 4e. Interaction Counts
    lazy_joins.append(
        mem_types.filter(pl.col("ptid") == id_sets['interaction_type'])
        .group_by("member_id").agg(pl.col("parent_id").n_unique().alias("num_interactions"))
        .rename({"member_id": "entity_id"})
    )

    # 4e.2 Reactants & Products (By Role Annotation)
    # These are members of the Reaction entity (parent_id) with a specific Role annotation (annot_id)
    for k, col in [('reactants', 'reactants'), ('products', 'products')]:
        if id_sets[k]:
            lazy_joins.append(
                mem_joined.filter(pl.col("annot_id").is_in(list(id_sets[k])))
                .group_by("parent_id")
                .agg(pl.col("member_id").unique().sort().alias(col))
                .rename({"parent_id": "entity_id"})
            )

    # 4e.3 Stoichiometry & Pathway Steps (By Annotation Type)
    # Stoichiometry: Value is in annot_val, Member is the participant
    # We want a list of "MemberID:Stoichiometry" strings
    if id_sets['stoichiometry']:
        lazy_joins.append(
            mem_joined.filter(
                pl.col("annot_id").is_in(list(id_sets['stoichiometry'])) & 
                pl.col("annot_val").is_not_null()
            )
            .select(
                "parent_id",
                (pl.col("member_id").cast(pl.Utf8) + ":" + pl.col("annot_val")).alias("fmt_stoich")
            )
            .group_by("parent_id")
            .agg(pl.col("fmt_stoich").unique().sort().alias("stoichiometry"))
            .rename({"parent_id": "entity_id"})
        )

    # Pathway Steps: Value is step order, Member is the step/component
    # We want a list of "StepOrder:MemberID" strings (or similar)
    # Let's do "StepOrder:MemberID" to be sortable by order
    if id_sets['pathway_steps']:
        lazy_joins.append(
            mem_joined.filter(
                pl.col("annot_id").is_in(list(id_sets['pathway_steps'])) & 
                pl.col("annot_val").is_not_null()
            )
            .select(
                "parent_id",
                (pl.col("annot_val") + ":" + pl.col("member_id").cast(pl.Utf8)).alias("fmt_step")
            )
            .group_by("parent_id")
            .agg(pl.col("fmt_step").unique().sort().alias("pathway_steps"))
            .rename({"parent_id": "entity_id"})
        )

    # 4f. Sources
    # Find NAME type ID efficiently
    name_tid_list = cv_map_df.filter(pl.col("accession") == "OM:0202")["entity_id"].to_list()
    name_tid = name_tid_list[0] if name_tid_list else -1
    
    source_names = ident.filter(pl.col("type_id") == name_tid).group_by("entity_id").agg(pl.col("identifier").first().alias("s_name"))
    
    sources_plan = (
        res.join(ident.select("id", "entity_id"), left_on="entity_identifier_id", right_on="id")
        .rename({"entity_id": "eid", "source_entity_id": "sid"})
        .join(source_names, left_on="sid", right_on="entity_id", how="left")
        .select("eid", (pl.coalesce(pl.col("s_name"), pl.lit("Source")) + ":" + pl.col("sid").cast(pl.Utf8)).alias("fmt"))
        .group_by("eid").agg(pl.col("fmt").unique().sort().alias("sources"))
        .rename({"eid": "entity_id"})
    )
    lazy_joins.append(sources_plan)

    # 4g. JSON Identifiers
    excl = list(id_sets['names'] | id_sets['synonyms'] | id_sets['gene_symbols'])
    type_names = ident.filter(pl.col("type_id") == name_tid).select(pl.col("entity_id").alias("tid"), pl.col("identifier").alias("tname"))
    
    ids_json_plan = (
        ident.filter(~pl.col("type_id").is_in(excl))
        .join(type_names, left_on="type_id", right_on="tid", how="left")
        .join(cv_accs, left_on="type_id", right_on="tid", how="left")
        .select(
            "entity_id",
            (pl.coalesce(pl.col("tname"), pl.col("acc")) + ":" + pl.col("type_id").cast(pl.Utf8)).alias("k"),
            pl.col("identifier").alias("v")
        )
        .group_by("entity_id")
        .agg(pl.struct(pl.col("k").alias("key"), pl.col("v").alias("value")).unique().alias("identifiers"))
    )
    lazy_joins.append(ids_json_plan)

    # 5. Chain Joins (Lazy)
    # We chain the joins onto the base plan. Polars optimizes the execution graph.
    logger.info("Constructing lazy execution graph with %d joins...", len([x for x in lazy_joins if x is not None]))
    
    final_plan = base
    for plan in lazy_joins:
        if plan is not None:
            final_plan = final_plan.join(plan, on="entity_id", how="left")

    # 6. Fill Nulls & Streaming Write
    # Note: We must cast lists explicitly to avoid schema issues if a column is entirely null
    defaults = [
        pl.col(c).fill_null(pl.lit([], dtype=STR_LIST)) for c in ["names", "synonyms", "gene_symbols", "descriptions", "references", "sources", "stoichiometry", "pathway_steps"]
    ] + [
        pl.col(c).fill_null(pl.lit([], dtype=INT_LIST)) for c in ["complexes", "cv_terms", "pathways", "reactions", "reactants", "products"]
    ] + [
        pl.col("identifiers").fill_null(pl.lit([], dtype=ID_LIST_DTYPE)),
        pl.col("num_interactions").fill_null(0)
    ]

    final_plan = final_plan.with_columns(defaults).sort("entity_id")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Streaming results to %s (this may take a moment)...", output_path)
    
    # sink_parquet processes the data in chunks, drastically reducing RAM usage.
    final_plan.sink_parquet(output_path)
    
    logger.info("Done! Search entities written successfully.")
    return output_path

def main():
    parser = argparse.ArgumentParser(description="Build Meilisearch entity documents")
    parser.add_argument("--global-tables-dir", type=Path, default=Path("databases/omnipath/output"))
    parser.add_argument("--output", type=Path, default=Path("databases/omnipath/output/search_entities.parquet"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    build_search_entities(args.global_tables_dir, args.output)

if __name__ == "__main__":
    main()