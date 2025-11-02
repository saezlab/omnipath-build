# Concise rewrite of build_local_tables pipeline
from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
import logging
import polars as pl

__all__ = ["build_local_tables"]
logger = logging.getLogger(__name__)

# --- helpers -----------------------------------------------------------------

def _iter_parquet_files(root: Path) -> Iterable[Path]:
    for d in sorted(root.glob("*")):
        if d.is_dir():
            yield from sorted(d.glob("*.parquet"))


def _load_source_data(root: Path) -> dict[str, list[tuple[Path, pl.LazyFrame]]]:
    out: dict[str, list[tuple[Path, pl.LazyFrame]]] = {}
    for p in _iter_parquet_files(root):
        out.setdefault(p.parent.name, []).append((p, pl.scan_parquet(str(p))))
    logger.info(f"Found {len(out)} sources")
    return out


def _split_files(files):
    inter, ent = [], []
    for p, lf in files:
        names = lf.collect_schema().names()
        if {"entity_a", "entity_b"} <= set(names):
            inter.append((p, lf))
        elif {"identifiers", "entity_type"} <= set(names):
            ent.append((p, lf))
    return inter, ent


def _process_files(files, next_id: int):
    ents, inters = [], []
    for p, lf in files:
        df = lf.collect()
        if not len(df):
            continue
        logger.info(f"    Processed {len(df):,} interactions from {p.name} -> {len(df)*2:,} entities")
        df = df.with_row_index("row", offset=0)

        # Check if is_member_of field exists in the struct
        schema = df.schema
        entity_a_fields = schema["entity_a"].fields
        has_is_member_of = any(f.name == "is_member_of" for f in entity_a_fields)

        # Build select expressions with conditional is_member_of
        select_expr_a = [
            (pl.col("row") * 2 + next_id).alias("local_entity_id"),
            pl.col("entity_a").struct.field("source"),
            pl.col("entity_a").struct.field("entity_type"),
            pl.col("entity_a").struct.field("identifiers"),
            pl.col("entity_a").struct.field("members"),
            pl.col("entity_a").struct.field("parent_accession"),
            pl.col("entity_a").struct.field("annotations"),
            pl.col("entity_a").struct.field("references"),
            pl.col("entity_a").struct.field("secondary_source"),
        ]

        select_expr_b = [
            (pl.col("row") * 2 + next_id + 1).alias("local_entity_id"),
            pl.col("entity_b").struct.field("source"),
            pl.col("entity_b").struct.field("entity_type"),
            pl.col("entity_b").struct.field("identifiers"),
            pl.col("entity_b").struct.field("members"),
            pl.col("entity_b").struct.field("parent_accession"),
            pl.col("entity_b").struct.field("annotations"),
            pl.col("entity_b").struct.field("references"),
            pl.col("entity_b").struct.field("secondary_source"),
        ]

        if has_is_member_of:
            select_expr_a.append(pl.col("entity_a").struct.field("is_member_of"))
            select_expr_b.append(pl.col("entity_b").struct.field("is_member_of"))
        else:
            # Add empty list column if field doesn't exist
            select_expr_a.append(pl.lit([]).alias("is_member_of"))
            select_expr_b.append(pl.lit([]).alias("is_member_of"))

        a = df.select(select_expr_a)
        b = df.select(select_expr_b)

        ents += [a, b]
        inters.append(
            df.select(
                (pl.col("row") * 2 + next_id).alias("local_entity_id_a"),
                (pl.col("row") * 2 + next_id + 1).alias("local_entity_id_b"),
                "interaction_type", "detection_method", "causal_mechanism",
                "causal_statement", "sentence", "interaction_annotations", "references",
            )
        )
        next_id += len(df) * 2
    return ents, inters, next_id


def _process_entities(files, next_id: int):
    ents = []
    for p, lf in files:
        df = lf.collect()
        if not len(df):
            continue
        logger.info(f"    Processed {len(df):,} entities from {p.name}")
        df = df.drop("local_entity_id") if "local_entity_id" in df.columns else df
        ents.append(df.with_row_index("local_entity_id", offset=next_id))
        next_id += len(df)
    return ents, next_id


def _process_members(df: pl.DataFrame, next_id: int):
    has = df.filter(pl.col("members").list.len() > 0)
    if not len(has):
        return pl.DataFrame(), pl.DataFrame(), next_id

    total_members = has.select(pl.col("members").list.len().sum()).item()
    logger.info(f"  Entities with members: {len(has):,} (total members: {total_members:,})")

    exploded = has.select(["local_entity_id", "source", "members"]).explode("members")

    # Member entities – identifiers must match upstream ents schema: list[struct]
    m = exploded.select(
        pl.col("source"),
        pl.lit("complex-component").alias("entity_type"),

        pl.when(pl.col("members").is_not_null())
         .then(
             pl.concat_list([
                 pl.struct([
                     pl.col("members").struct.field("identifier_type").alias("type"),
                     pl.col("members").struct.field("identifier").alias("value")
                 ])
             ])
         )
         .otherwise(pl.lit(None))
         .alias("identifiers"),

        pl.lit(None).alias("annotations"),
        pl.col("local_entity_id").alias("parent_local_entity_id"),
        pl.col("members").struct.field("role").alias("role"),
        pl.col("members").struct.field("stoichiometry").alias("stoichiometry"),
    ).with_row_index("local_entity_id", offset=next_id)

    next_id += len(m)

    # Keep role as accession string (no ID mapping)
    membership = m.select(
        "local_entity_id",
        "parent_local_entity_id",
        "role",
        "stoichiometry"
    ).with_row_index("local_membership_id", offset=1)

    logger.info(f"    Created {len(m):,} member entity records")
    logger.info(f"    Created {len(membership):,} membership relationships")

    return m, membership, next_id


def _extract_is_member_of(df: pl.DataFrame) -> pl.DataFrame:
    """Extract is_member_of relationships from entities.

    Returns DataFrame with columns:
        - local_entity_id
        - parent_identifier
        - parent_identifier_type
        - role
    """
    # Check if is_member_of column exists and has any data
    if "is_member_of" not in df.columns:
        return pl.DataFrame()

    has = df.filter(
        pl.col("is_member_of").is_not_null() &
        (pl.col("is_member_of").list.len() > 0)
    )
    if not len(has):
        return pl.DataFrame()

    total_relationships = has.select(pl.col("is_member_of").list.len().sum()).item()
    logger.info(f"  Entities with is_member_of: {len(has):,} (total relationships: {total_relationships:,})")

    # Explode is_member_of list to get one row per parent relationship
    exploded = has.select(["local_entity_id", "is_member_of"]).explode("is_member_of")

    # Extract fields from the struct and keep as accession strings
    relationships = exploded.select(
        pl.col("local_entity_id"),
        pl.col("is_member_of").struct.field("identifier").alias("parent_identifier"),
        pl.col("is_member_of").struct.field("identifier_type").alias("parent_identifier_type"),
        pl.col("is_member_of").struct.field("role").alias("role"),
    )

    logger.info(f"    Extracted {len(relationships):,} is_member_of relationships")

    return relationships


def _collect_reference_links(
    df: pl.DataFrame,
    id_col: str,
    ref_lookup: pl.DataFrame,
) -> pl.DataFrame:
    """Explode `references` column and map to reference_id for evidence tables."""
    if not len(df) or "references" not in df.columns or not len(ref_lookup):
        return pl.DataFrame()

    exploded = (
        df.select(["source_id", id_col, "references"])
        .filter(pl.col("references").list.len() > 0)
        .explode("references")
        .select(
            "source_id",
            id_col,
            pl.col("references").struct.field("type").alias("type"),
            pl.col("references").struct.field("value").alias("value"),
        )
        .filter(pl.col("type").is_not_null() & pl.col("value").is_not_null())
    )

    if not len(exploded):
        return pl.DataFrame()

    # Join with ref_lookup on type (accession string) and value
    joined = exploded.join(ref_lookup, on=["type", "value"], how="inner")
    if not len(joined):
        return pl.DataFrame()

    return joined.select("source_id", id_col, "reference_id").unique()

# --- main --------------------------------------------------------------------

def build_local_tables(
    data_root: Path,
    output_dir: Path,
    sources_df: pl.DataFrame,
    references_df: pl.DataFrame,
):
    data = _load_source_data(data_root)
    name2id = {r["name"]: r["id"] for r in sources_df.iter_rows(named=True)}
    ref_lookup = (
        references_df.rename({"id": "reference_id"})
        .select(["reference_id", "type", "value"])
    )
    d = output_dir / "local_tables"; d.mkdir(parents=True, exist_ok=True)

    for sname, files in data.items():
        if sname not in name2id:
            continue
        sid = name2id[sname]
        nid = 1

        logger.info("\n" + "="*70)
        logger.info(f"Processing source: {sname} (id={sid})")
        logger.info("="*70)

        inter_files, ent_files = _split_files(files)
        logger.info(f"  Found {len(inter_files)} interaction files, {len(ent_files)} entity files")

        ents_i, inters, nid = _process_files(inter_files, nid)
        ents_e, nid = _process_entities(ent_files, nid)

        combined = ents_i + ents_e
        if not combined:
            logger.warning(f"  No entity records for {sname}, skipping")
            continue

        ents = pl.concat(combined, how="diagonal_relaxed")
        logger.info(f"  Total base entities for {sname}: {len(ents):,}")

        total_interactions = sum(len(i) for i in inters) if inters else 0
        if total_interactions:
            logger.info(f"  Total interactions for {sname}: {total_interactions:,}")

        # ✅ INCLUDE MEMBER ENTITIES
        ments, memb, nid = _process_members(ents, nid)
        if len(ments):
            ents = pl.concat([ents, ments], how="diagonal_relaxed")
            logger.info(f"  Total entities including members: {len(ents):,}")

        # Add source_id
        ents = ents.with_columns(pl.lit(sid).alias("source_id"))

        # Extract is_member_of relationships (keep accessions, no ID mapping)
        is_member_of_rels = _extract_is_member_of(ents)

        # Build local reference bridge for entity evidence
        entity_refs = _collect_reference_links(
            ents,
            id_col="local_entity_id",
            ref_lookup=ref_lookup,
        )

        # ✅ Evidence & Identifiers now include new member rows
        # Keep entity_type as accession string (no ID mapping)
        ev = ents.select("source_id", "local_entity_id", "entity_type", "annotations")
        ids = ents.select("source_id", "local_entity_id", "identifiers")

        # Save
        ev.write_parquet(d / f"local_entity_evidence_{sname}.parquet")
        ids.write_parquet(d / f"local_entity_identifiers_{sname}.parquet")

        if len(entity_refs):
            entity_refs.write_parquet(
                d / f"local_entity_evidence_reference_{sname}.parquet"
            )
            logger.info(f"  Saved {sname} entity references: {len(entity_refs):,} rows")

        logger.info(f"  Saved evidence ({len(ev):,}) & identifiers ({len(ids):,}) for {sname}")

        if len(memb):
            memb.with_columns(pl.lit(sid).alias("source_id")).write_parquet(
                d / f"local_membership_{sname}.parquet"
            )
            logger.info(f"  Saved {sname} membership: {len(memb):,} rows")

        if len(is_member_of_rels):
            is_member_of_rels.with_columns(pl.lit(sid).alias("source_id")).write_parquet(
                d / f"local_is_member_of_{sname}.parquet"
            )
            logger.info(f"  Saved {sname} is_member_of: {len(is_member_of_rels):,} rows")

        if inters:
            inters = pl.concat(inters, how="diagonal_relaxed")
            # Add source_id and keep CV term fields as accessions
            inters = inters.with_columns(pl.lit(sid).alias("source_id"))
            inters = inters.with_row_index("local_interaction_id", offset=1)

            interaction_refs = _collect_reference_links(
                inters,
                id_col="local_interaction_id",
                ref_lookup=ref_lookup,
            )

            # Select columns for interaction evidence (keep CV terms as accessions)
            inters = inters.select(
                "source_id", "local_entity_id_a", "local_entity_id_b", "local_interaction_id",
                "interaction_type", "detection_method", "causal_mechanism",
                "causal_statement", "sentence", "interaction_annotations",
            )

            inters.write_parquet(d / f"local_interaction_evidence_{sname}.parquet")
            logger.info(f"  Saved {sname} interactions: {len(inters):,} rows")

            if len(interaction_refs):
                interaction_refs.write_parquet(
                    d / f"local_interaction_evidence_reference_{sname}.parquet"
                )
                logger.info(
                    f"  Saved {sname} interaction references: {len(interaction_refs):,} rows"
                )
