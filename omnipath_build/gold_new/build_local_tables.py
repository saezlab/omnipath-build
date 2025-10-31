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
        a = df.select(
            (pl.col("row") * 2 + next_id).alias("local_entity_id"),
            pl.col("entity_a").struct.field("source"), pl.col("entity_a").struct.field("entity_type"), pl.col("entity_a").struct.field("identifiers"), pl.col("entity_a").struct.field("members"), pl.col("entity_a").struct.field("parent_accession"), pl.col("entity_a").struct.field("annotations"), pl.col("entity_a").struct.field("references"), pl.col("entity_a").struct.field("secondary_source"),
        )
        b = df.select(
            (pl.col("row") * 2 + next_id + 1).alias("local_entity_id"),
            pl.col("entity_b").struct.field("source"), pl.col("entity_b").struct.field("entity_type"), pl.col("entity_b").struct.field("identifiers"), pl.col("entity_b").struct.field("members"), pl.col("entity_b").struct.field("parent_accession"), pl.col("entity_b").struct.field("annotations"), pl.col("entity_b").struct.field("references"), pl.col("entity_b").struct.field("secondary_source"),
        )
        ents += [a, b]
        inters.append(
            df.select(
                (pl.col("row") * 2 + next_id).alias("local_entity_id_a"),
                (pl.col("row") * 2 + next_id + 1).alias("local_entity_id_b"),
                "interaction_type", "detection_method", "is_directed", "direction",
                "sign", "causal_mechanism", "causal_statement", "sentence",
                "interaction_annotations", "references",
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
    logger.info(f"  Entities with members: {len(has):,} (total members: {has.select(pl.col('members').list.len().sum()).item():,})")
    e = has.select(["local_entity_id", "source", "members"]).explode("members")
    m = e.select(
        pl.col("source"),
        pl.col("members").struct.field("identifier").alias("identifier"),
        pl.col("members").struct.field("identifier_type").alias("identifier_type"),
        pl.col("members").struct.field("role"),
        pl.col("members").struct.field("stoichiometry"),
        pl.col("local_entity_id").alias("parent_local_entity_id"),
    ).with_row_index("local_entity_id", offset=next_id)
    next_id += len(m)
    membership = m.select(
        "local_entity_id", "parent_local_entity_id", "role", "stoichiometry"
    )
    logger.info(f"  Processing members for {len(has):,} parent entities")
    logger.info(f"    Created {len(m):,} member entity records")
    logger.info(f"    Created {len(membership):,} membership relationships")
    return m, membership, next_id


def _map_terms(df: pl.DataFrame, sid: int, cv: pl.DataFrame) -> pl.DataFrame:
    if not len(df):
        return df
    amap = {r["accession"]: r["id"] for r in cv.iter_rows(named=True)}
    f = lambda x: amap.get(x) if x else None
    return df.with_columns(
        pl.lit(sid).alias("source_id"),
        pl.col("interaction_type").map_elements(f, return_dtype=pl.Int64).alias("interaction_type_id"),
        pl.col("detection_method").map_elements(f, return_dtype=pl.Int64).alias("detection_method_id"),
    ).select(
        "source_id", "local_entity_id_a", "local_entity_id_b",
        "interaction_type_id", "detection_method_id", "is_directed", "direction",
        "sign", "causal_mechanism", "causal_statement", "sentence",
        "interaction_annotations", "references",
    )

# --- main --------------------------------------------------------------------

def build_local_tables(data_root: Path, output_dir: Path, sources_df: pl.DataFrame, cv_term_df: pl.DataFrame):
    data = _load_source_data(data_root)
    name2id = {r["name"]: r["id"] for r in sources_df.iter_rows(named=True)}
    d = output_dir / "local_tables"; d.mkdir(parents=True, exist_ok=True)

    for sname, files in data.items():
        if sname not in name2id:
            continue
        sid = name2id[sname]; nid = 1

        logger.info("\n" + "="*70)
        logger.info(f"Processing source: {sname} (id={sid})")
        logger.info("="*70)
        logger.info(f"Processing source: {sname}")

        inter_files, ent_files = _split_files(files)
        logger.info(f"  Found {len(inter_files)} interaction files, {len(ent_files)} entity files")

        ents_i, inters, nid = _process_files(inter_files, nid)
        ents_e, nid = _process_entities(ent_files, nid)

        # Guard: skip sources with no entity data
        combined = ents_i + ents_e
        if not combined:
            logger.warning(f"  No entity records found for source {sname}")
            logger.warning(f"  No entities found for {sname}, skipping")
            continue

        ents = pl.concat(combined, how="diagonal_relaxed").with_columns(pl.lit(sid).alias("source_id"))
        logger.info(f"  Total entities for {sname}: {len(ents):,}")

        total_interactions = sum(len(i) for i in inters) if inters else 0
        if total_interactions:
            logger.info(f"  Total interactions for {sname}: {total_interactions:,}")

        ments, memb, nid = _process_members(ents, nid)
        inters = pl.concat(inters, how="diagonal_relaxed") if inters else pl.DataFrame()
        inters = _map_terms(inters, sid, cv_term_df)
        ev = ents.select("source_id", "local_entity_id", "entity_type", "annotations")
        ids = ents.select("source_id", "local_entity_id", "identifiers")

        ev.write_parquet(d / f"local_entity_evidence_{sname}.parquet")
        logger.info(f"  Saved {sname} evidence: {len(ev):,} rows -> local_entity_evidence_{sname}.parquet")

        ids.write_parquet(d / f"local_entity_identifiers_{sname}.parquet")
        logger.info(f"  Saved {sname} identifiers: {len(ids):,} rows -> local_entity_identifiers_{sname}.parquet")

        if len(memb):
            memb.with_columns(pl.lit(sid).alias("source_id")).write_parquet(d / f"local_membership_{sname}.parquet")
            logger.info(f"  Saved {sname} membership: {len(memb):,} rows -> local_membership_{sname}.parquet")

        if len(inters):
            inters.write_parquet(d / f"local_interaction_evidence_{sname}.parquet")
            logger.info(f"  Saved {sname} interactions: {len(inters):,} rows -> local_interaction_evidence_{sname}.parquet")
