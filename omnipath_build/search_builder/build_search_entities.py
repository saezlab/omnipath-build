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
    CV_TERM_ACCESSION_TYPE,
)

__all__ = ["build_search_entities"]

logger = logging.getLogger(__name__)

# Schema Constants
ID_STRUCT = pl.Struct([Field('key', pl.Utf8), Field('value', pl.Utf8)])
ID_LIST_DTYPE = pl.List(ID_STRUCT)
STR_LIST = pl.List(pl.Utf8)

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


def _rename_if_present_lf(lf: pl.LazyFrame, old: str, new: str) -> pl.LazyFrame:
    """Rename a column on a LazyFrame if present in schema."""
    schema = lf.collect_schema()
    if old in schema:
        return lf.rename({old: new})
    return lf


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
    ent = _rename_if_present_lf(pl.scan_parquet(global_tables_dir / "entity.parquet"), "entity_key", "entity_id")
    ident = _rename_if_present_lf(pl.scan_parquet(global_tables_dir / "entity_identifier.parquet"), "entity_key", "entity_id")
    res = pl.scan_parquet(global_tables_dir / "entity_identifier_resource.parquet")
    mem = pl.scan_parquet(global_tables_dir / "membership.parquet")

    # New tables in entity_instance schema
    inst = _rename_if_present_lf(pl.scan_parquet(global_tables_dir / "entity_instance.parquet"), "entity_key", "entity_id")
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
        pl.col("source_ref"),
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

    # 4b. Annotations (Descriptions)
    # These are now in entity_annotation table, linked via entity_instance
    # entity_annotation has: instance_id, cv_term_accession, value, unit_accession
    if id_sets['descriptions']:
        lazy_joins.append(
            ent_annot_with_entity
            .filter(pl.col("cv_term_accession").is_in(list(id_sets['descriptions'])))
            .filter(pl.col("value").is_not_null())
            .group_by("annot_entity_id")
            .agg(pl.col("value").unique().sort().alias("descriptions"))
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

    # 4c.2 CV terms from entity annotations (ontology-specific arrays only)
    # CV_TERM_ACCESSION annotations have the actual CV term accession in the value field.
    cv_terms_base = (
        ent_annot_with_entity
        .filter(pl.col("cv_term_accession") == CV_TERM_ACCESSION_TYPE)
        .filter(pl.col("value").is_not_null())
        .join(
            pl.DataFrame(cv_terms.select(["accession", "label"])).lazy().rename({"label": "cv_term_label"}),
            left_on="value",
            right_on="accession",
            how="left",
        )
        .with_columns(
            pl.coalesce([
                pl.col("cv_term_label"),
                (pl.col("value") + ":" + pl.col("value"))
            ]).alias("cv_term_formatted")
        )
    )

    for prefix, out_col in [
        ("GO:", "cv_terms_go"),
        ("MI:", "cv_terms_mi"),
        ("OM:", "cv_terms_om"),
        ("HP:", "cv_terms_hp"),
        ("KW:", "cv_terms_kw"),
    ]:
        lazy_joins.append(
            cv_terms_base
            .filter(pl.col("value").str.starts_with(prefix))
            .group_by("annot_entity_id")
            .agg(pl.col("cv_term_formatted").unique().sort().alias(out_col))
            .rename({"annot_entity_id": "entity_id"})
        )

    # 4d. Interaction Counts
    # Join memberships to entity to check parent interaction type.
    mem_types = mem_simple.join(
        ent.rename({"entity_id": "pid", "entity_type": "ptype"}),
        left_on="parent_id", right_on="pid"
    )

    lazy_joins.append(
        mem_types.filter(pl.col("ptype") == id_sets['interaction_type'])
        .group_by("member_id").agg(pl.col("parent_id").n_unique().alias("num_interactions"))
        .rename({"member_id": "entity_id"})
    )

    # 4e. Sources (direct source_ref provenance)
    sources_plan = (
        res.join(ident.select('id', 'entity_id'), left_on='entity_identifier_id', right_on='id')
        .select(
            pl.col('entity_id').alias('eid'),
            'source_ref',
        )
        .group_by('eid').agg(pl.col('source_ref').drop_nulls().unique().sort().alias('sources'))
        .rename({'eid': 'entity_id'})
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
        pl.col(c).fill_null(pl.lit([], dtype=STR_LIST)) for c in [
            "names",
            "synonyms",
            "gene_symbols",
            "descriptions",
            "sources",
            "cv_terms_go",
            "cv_terms_mi",
            "cv_terms_om",
            "cv_terms_hp",
            "cv_terms_kw",
        ]
    ] + [
        pl.col("identifiers").fill_null(pl.lit([], dtype=ID_LIST_DTYPE)),
        pl.col("num_interactions").fill_null(0)
    ]

    final_plan = (
        final_plan
        .with_columns(defaults)
        .sort("entity_id")
    )

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
