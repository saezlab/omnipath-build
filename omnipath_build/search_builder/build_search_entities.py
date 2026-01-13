"""Build Meilisearch entity documents from global tables (Lazy/Streaming).

Updated for new entity_instance schema:
- entity_annotation links to entity_instance, not entity directly
- membership has polymorphic columns (parent_entity_id/parent_instance_id, member_entity_id/member_instance_id)
- membership_annotation table is removed
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl
from polars import Field

from .schema import (
    get_cv_term_accession_sets,
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
    filter_ids: frozenset[str], 
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


def _resolve_member_entity_id(mem_lf: pl.LazyFrame, inst_lf: pl.LazyFrame) -> pl.LazyFrame:
    """Resolve member to entity_id, handling both direct entity and instance references.
    
    In the new schema, membership can point to either:
    - member_entity_id: direct entity reference
    - member_instance_id: instance that references an entity
    
    Returns membership with resolved member_entity_id column.
    """
    # Join with instances to resolve instance -> entity_id
    return (
        mem_lf
        .join(
            inst_lf.select([
                pl.col("id").alias("member_instance_id"),
                pl.col("entity_id").alias("instance_entity_id")
            ]),
            on="member_instance_id",
            how="left"
        )
        .with_columns(
            # Use instance's entity_id if member_instance_id is set, else use direct member_entity_id
            pl.coalesce([
                pl.col("instance_entity_id"),
                pl.col("member_entity_id")
            ]).alias("resolved_member_entity_id")
        )
    )


def _resolve_parent_entity_id(mem_lf: pl.LazyFrame, inst_lf: pl.LazyFrame) -> pl.LazyFrame:
    """Resolve parent to entity_id, handling both direct entity and instance references."""
    return (
        mem_lf
        .join(
            inst_lf.select([
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
            ]).alias("resolved_parent_entity_id")
        )
    )


def build_search_entities(global_tables_dir: Path, output_path: Path) -> Path:
    logger.info("=" * 80 + "\nBuilding Meilisearch entity documents (Lazy Execution)\n" + "=" * 80)

    # 1. Load Metadata Eagerly (for logic/sets)
    # We need specific sets of Accessions to build the filter logic
    logger.info("Loading metadata for mapping generation...")
    # cv_map_df removed - we use static sets now
    id_sets = get_cv_term_accession_sets()

    # Load CV term label mappings
    logger.info("Loading CV term label mappings...")
    if (global_tables_dir / "cv_terms.parquet").exists():
        cv_terms = pl.read_parquet(global_tables_dir / "cv_terms.parquet")
        logger.info(f"Loaded {len(cv_terms)} CV term labels")
    else:
        logger.warning(f"cv_terms.parquet not found in {global_tables_dir}")
        cv_terms = pl.DataFrame(schema={"accession": pl.Utf8, "label": pl.Utf8})

    # Create polars DataFrames for joining
    entity_type_labels = cv_terms.select([
        pl.col("accession").alias("entity_type"),
        pl.col("label").alias("entity_type_label")
    ])

    identifier_type_labels = cv_terms.select([
        pl.col("accession").alias("type_id"),
        pl.col("label").alias("type_id_label")
    ])

    logger.info("Metadata ready.")

    # 2. Setup Lazy Scans
    # scan_parquet creates a LazyFrame; no data is read until 'sink_parquet' is called
    ent = pl.scan_parquet(global_tables_dir / "entity.parquet")
    ident = pl.scan_parquet(global_tables_dir / "entity_identifier.parquet")
    res = pl.scan_parquet(global_tables_dir / "entity_identifier_resource.parquet")
    mem = pl.scan_parquet(global_tables_dir / "membership.parquet")
    
    # New tables in entity_instance schema
    inst = pl.scan_parquet(global_tables_dir / "entity_instance.parquet")
    ent_annot = pl.scan_parquet(global_tables_dir / "entity_annotation.parquet")

    # Resolve membership entity IDs (handle polymorphic columns)
    mem_resolved = _resolve_member_entity_id(mem, inst)
    mem_resolved = _resolve_parent_entity_id(mem_resolved, inst)
    
    # Create simplified membership view with resolved entity IDs
    # For backwards compatibility with existing logic
    mem_simple = mem_resolved.select([
        pl.col("id"),
        pl.col("resolved_parent_entity_id").alias("parent_id"),
        pl.col("resolved_member_entity_id").alias("member_id"),
        pl.col("member_instance_id"),  # Keep for annotation lookups
        pl.col("source_id"),
    ])

    # Join entity annotations with instances to get entity_id
    # entity_annotation -> entity_instance -> entity
    ent_annot_with_entity = (
        ent_annot
        .join(
            inst.select([
                pl.col("id").alias("instance_id"),
                pl.col("entity_id").alias("annot_entity_id")
            ]),
            on="instance_id",
            how="left"
        )
    )
    
    
    # 3. Base Entity Processing
    # Entities: filter non-interactions
    # entity_type in entity table is already string accession.
    # We assume entity_type column holds the accession (e.g. "MI:0326").

    base = (
        ent.filter(pl.col("entity_type") != id_sets['interaction_type'])
        .join(
            pl.DataFrame(entity_type_labels).lazy(),
            on="entity_type",
            how="left"
        )
        .with_columns(
            # Use label if available, else use accession:accession format
            pl.coalesce([
                pl.col("entity_type_label"),
                (pl.col("entity_type") + ":" + pl.col("entity_type"))
            ]).alias("entity_type_formatted")
        )
        .select(
            "entity_id",
            pl.col("entity_type_formatted").alias("entity_type")
        )
    )

    # 4. Define Aggregations (Lazy Plans)
    lazy_joins = []

    # 4a. Identifiers (Names, Synonyms, Genes)
    for k, col in [('names', 'names'), ('synonyms', 'synonyms'), ('gene_symbols', 'gene_symbols')]:
        lazy_joins.append(_agg_lazy(ident, "type_id", id_sets[k], "identifier", col))

    # 4b. Annotations (Descriptions, References)
    # These are now in entity_annotation table, linked via entity_instance
    # entity_annotation has: instance_id, cv_term_accession, value, unit_accession
    for k, col in [('descriptions', 'descriptions'), ('references', 'references')]:
        if id_sets[k]:
            lazy_joins.append(
                ent_annot_with_entity
                .filter(pl.col("cv_term_accession").is_in(list(id_sets[k])))
                .filter(pl.col("value").is_not_null())
                .group_by("annot_entity_id")
                .agg(pl.col("value").unique().sort().alias(col))
                .rename({"annot_entity_id": "entity_id"})
            )

    # 4c. NCBI Tax ID - now from entity_annotation
    # id_sets['ncbi_tax_id'] is a set of Accessions.
    if id_sets['ncbi_tax_id']:
        lazy_joins.append(
            ent_annot_with_entity
            .filter(pl.col("cv_term_accession").is_in(list(id_sets['ncbi_tax_id'])))
            .group_by("annot_entity_id")
            .agg(pl.col("value").first().alias("ncbi_tax_id"))
            .rename({"annot_entity_id": "entity_id"})
        )

    # 4d. Structural Memberships & Interactions
    # Join memberships to entity to check parent types
    # ent now has entity_type as accession string
    mem_types = mem_simple.join(
        ent.rename({"entity_id": "pid", "entity_type": "ptype"}), 
        left_on="parent_id", right_on="pid"
    )

    for k, col in [('complex_type', 'complexes'), ('cv_term_type', 'cv_terms'), ('pathway_type', 'pathways'), ('reaction_type', 'reactions')]:
        if id_sets[k]:
            lazy_joins.append(
                mem_types.filter(pl.col("ptype") == id_sets[k])
                .group_by("member_id").agg(pl.col("parent_id").unique().sort().alias(col))
                .rename({"member_id": "entity_id"})
            )

    # 4e. Interaction Counts
    lazy_joins.append(
        mem_types.filter(pl.col("ptype") == id_sets['interaction_type'])
        .group_by("member_id").agg(pl.col("parent_id").n_unique().alias("num_interactions"))
        .rename({"member_id": "entity_id"})
    )

    # 4e.2 Reactants & Products (By Role Annotation on member instances)
    # In new schema, role annotations are on the member's entity_instance
    # We need to join membership with member_instance_id to entity_annotation
    mem_with_annot = (
        mem_simple
        .filter(pl.col("member_instance_id").is_not_null())
        .join(
            ent_annot.select([
                pl.col("instance_id"),
                pl.col("cv_term_accession").alias("annot_acc"),
                pl.col("value").alias("annot_val"),
            ]),
            left_on="member_instance_id",
            right_on="instance_id",
            how="left"
        )
    )
    
    for k, col in [('reactants', 'reactants'), ('products', 'products')]:
        if id_sets[k]:
            lazy_joins.append(
                mem_with_annot.filter(pl.col("annot_acc").is_in(list(id_sets[k])))
                .group_by("parent_id")
                .agg(pl.col("member_id").unique().sort().alias(col))
                .rename({"parent_id": "entity_id"})
            )

    # 4e.3 Stoichiometry & Pathway Steps (By Annotation Type on member instances)
    if id_sets['stoichiometry']:
        lazy_joins.append(
            mem_with_annot.filter(
                pl.col("annot_acc").is_in(list(id_sets['stoichiometry'])) & 
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

    # Pathway Steps
    if id_sets['pathway_steps']:
        lazy_joins.append(
            mem_with_annot.filter(
                pl.col("annot_acc").is_in(list(id_sets['pathway_steps'])) & 
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
    # We no longer look up entity_ids for types, we assume name_tid is valid? 
    # But wait, type_id is now string. We need to look up type_id string.
    # OM:0202 is NAME.
    name_tid = "OM:0202"
    
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

    ids_json_plan = (
        ident.filter(~pl.col("type_id").is_in(excl))
        .join(
            pl.DataFrame(identifier_type_labels).lazy(),
            on="type_id",
            how="left"
        )
        .with_columns(
            # Use label if available, else use accession:accession format
            pl.coalesce([
                pl.col("type_id_label"),
                (pl.col("type_id") + ":" + pl.col("type_id"))
            ]).alias("k")
        )
        .select(
            "entity_id",
            pl.col("k"),
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
    parser.add_argument("--global-tables-dir", type=Path, default=Path("omnipath_build/data/gold"))
    parser.add_argument("--output", type=Path, default=Path("omnipath_build/data/gold/search_entities.parquet"))
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