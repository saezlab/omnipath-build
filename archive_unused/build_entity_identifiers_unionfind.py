from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Iterable, Sequence
import logging
import polars as pl
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Union-Find Data Structure
# --------------------------------------------------------------------------- #

class UnionFind:
    """Union-Find (Disjoint Set Union) data structure with path compression and union by rank."""

    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        """Find the root of x with path compression."""
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
            return x

        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])  # Path compression
        return self.parent[x]

    def union(self, x, y):
        """Union two sets by rank."""
        root_x = self.find(x)
        root_y = self.find(y)

        if root_x == root_y:
            return

        # Union by rank
        if self.rank[root_x] < self.rank[root_y]:
            self.parent[root_x] = root_y
        elif self.rank[root_x] > self.rank[root_y]:
            self.parent[root_y] = root_x
        else:
            self.parent[root_y] = root_x
            self.rank[root_x] += 1

    def get_component_map(self):
        """Return a dict mapping each element to its root (canonical representative)."""
        return {x: self.find(x) for x in self.parent}

__all__ = ["MERGE_SAFE_IDENTIFIER_TYPES", "build_entity_identifiers"]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

MERGE_SAFE_IDENTIFIER_TYPES = frozenset({
    IdentifierNamespaceCv.UNIPROT.value,
    IdentifierNamespaceCv.STANDARD_INCHI.value,
    IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
})


@dataclass(frozen=True)
class IdentifierExtractionContext:
    path: Path
    origin: str


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _iter_parquet_files(root: Path) -> Iterable[Path]:
    """Recursively yield parquet files under a directory."""
    logger.debug(f"Scanning for parquet files in {root}")
    file_count = 0
    for d in sorted(root.glob("*")):
        if d.is_dir():
            dir_files = list(sorted(d.glob("*.parquet")))
            file_count += len(dir_files)
            yield from dir_files
    logger.info(f"Found {file_count} parquet files to process")

def _explode_identifiers(
    lf: pl.LazyFrame,
    source_expr: pl.Expr,
    id_expr: pl.Expr,
    ctx: IdentifierExtractionContext,
) -> pl.LazyFrame:
    """Explode and normalize identifier lists in a LazyFrame."""
    return (
        lf.select([source_expr.alias("source_name"), id_expr.alias("id_struct")])
        .explode("id_struct")
        .drop_nulls("id_struct")
        .with_columns([
            pl.col("id_struct").struct.field("type").alias("type_accession"),
            pl.col("id_struct").struct.field("value").alias("identifier"),
            pl.lit(ctx.origin).alias("origin"),
            pl.lit(str(ctx.path.relative_to(ctx.path.parents[1]))).alias("source_file"),
        ])
        .drop("id_struct")
        .with_columns(pl.col("identifier"))
        .unique(subset=["type_accession", "identifier", "source_name"])
        .with_columns(
            (pl.col("source_name").cast(pl.Utf8) + ":" + pl.lit(ctx.origin))
            .hash(seed=42)
            .alias("context_id")
        )
    )


def _collect_preliminary_identifiers_lazy(
    data_root: Path,
    cv_terms: pl.DataFrame,
    sources: pl.DataFrame,
    include_provenance: bool = True,
) -> pl.LazyFrame:
    """Read and flatten all identifier records from input parquet files."""
    logger.info("Phase 1: Collecting preliminary identifiers from parquet files")
    lfs = []
    file_count = 0
    for path in _iter_parquet_files(data_root):
        file_count += 1
        lf = pl.scan_parquet(str(path))
        cols = set(lf.collect_schema().names())
        base = f"{path.parent.name}/{path.stem}"
        if "identifiers" in cols:
            logger.debug(f"Processing identifiers from {base}")
            lfs.append(
                _explode_identifiers(
                    lf,
                    pl.col("source"),
                    pl.col("identifiers"),
                    IdentifierExtractionContext(path, f"{base}:entity"),
                )
            )
        for side in ("entity_a", "entity_b"):
            if side in cols:
                logger.debug(f"Processing {side} identifiers from {base}")
                lfs.append(
                    _explode_identifiers(
                        lf,
                        pl.col(side).struct.field("source"),
                        pl.col(side).struct.field("identifiers"),
                        IdentifierExtractionContext(path, f"{base}:{side}"),
                    )
                )

    logger.info(f"Processed {file_count} parquet files, created {len(lfs)} identifier streams")

    if not lfs:
        logger.warning("No identifier data found in parquet files")
        return pl.LazyFrame(
            schema={
                "source_name": pl.Utf8,
                "type_accession": pl.Utf8,
                "identifier": pl.Utf8,
                "context_id": pl.UInt64,
            }
        )

    logger.info("Concatenating identifier streams and filtering nulls")
    lf = pl.concat(lfs, how="diagonal_relaxed").filter(
        pl.col("identifier").is_not_null() & (pl.col("identifier").str.len_chars() > 0)
    )

    logger.info("Joining with CV terms and sources to get internal IDs")
    cv_lf = pl.from_pandas(
        cv_terms.select(["accession", "id"])
        .rename({"accession": "type_accession", "id": "type_id"})
        .to_pandas()
    ).lazy()
    src_lf = pl.from_pandas(
        sources.select(["name", "id"])
        .rename({"name": "source_name", "id": "source_id"})
        .to_pandas()
    ).lazy()

    lf = lf.join(cv_lf, on="type_accession", how="left")
    lf = lf.join(src_lf, on="source_name", how="left")

    if not include_provenance:
        logger.debug("Dropping provenance columns")
        lf = lf.drop(["origin", "source_file"])

    logger.info("Phase 1 complete: preliminary identifiers collected")
    return lf


# --------------------------------------------------------------------------- #
# Global entity clustering with merge-safe identifiers
# --------------------------------------------------------------------------- #

def _deduplicate_identifiers_lazy(
    preliminary: pl.LazyFrame,
    cv_terms: pl.DataFrame,
    merge_safe_type_ids: frozenset[int],
) -> pl.LazyFrame:
    """
    Perform entity-level deduplication:
      - merge-safe IDs define global connectivity
      - context_id groups define within-source linkage
      - all connected IDs get a shared integer entity_id
    """
    logger.info("Phase 2: Starting entity deduplication and clustering")

    cv_lf = pl.from_pandas(
        cv_terms.select(["id", "accession"])
        .rename({"id": "type_id", "accession": "type_accession"})
        .to_pandas()
    ).lazy()

    # Each record context (e.g. a molecule row in source) is a local "mini cluster".
    logger.info("Creating context-based linkage (within-source clustering)")
    ctx_links = (
        preliminary.select(["context_id", "identifier"])
        .unique()
        .rename({"context_id": "cluster_key"})
    )

    # Merge-safe identifiers form the global linking layer
    logger.info(f"Creating merge-safe identifier links (types: {merge_safe_type_ids})")
    merge_safe_links = (
        preliminary.filter(pl.col("type_id").is_in(list(merge_safe_type_ids)))
        .select(["identifier"])
        .unique()
        .with_columns(pl.lit(0).alias("dummy"))
        .with_columns(pl.concat_str(["dummy", "identifier"]).hash(seed=7).alias("cluster_key"))
        .select(["cluster_key", "identifier"])
    )

    # Combine both local and global link sets
    logger.info("Combining local and global link sets")
    cluster_links = pl.concat([ctx_links, merge_safe_links], how="diagonal_relaxed")

    # Compute connected components using Union-Find
    logger.info("Computing connected components using Union-Find")

    # Collect the edges to process with Union-Find
    edges_df = cluster_links.select(["identifier", "cluster_key"]).unique().collect()

    # Build Union-Find structure and track all identifiers
    uf = UnionFind()
    all_identifiers = set()

    # Process each edge: union identifier with its cluster_key
    # We tag cluster_keys with a prefix to distinguish them from identifiers
    for row in edges_df.iter_rows():
        identifier, cluster_key = row
        all_identifiers.add(identifier)
        # Tag cluster_key to distinguish it from actual identifiers
        uf.union(identifier, f"__cluster_{cluster_key}")

    logger.info(f"Union-Find processed {edges_df.height} edges, {len(all_identifiers)} unique identifiers")

    # Get the component mapping for actual identifiers only
    identifier_to_root = {
        identifier: uf.find(identifier)
        for identifier in all_identifiers
    }

    # Convert to DataFrame and assign dense entity IDs
    entity_map = (
        pl.DataFrame({
            "identifier": list(identifier_to_root.keys()),
            "entity_group": list(identifier_to_root.values()),
        })
        .with_columns(pl.col("entity_group").rank("dense").alias("entity_id_unified"))
        .select(["identifier", "entity_id_unified"])
        .lazy()
    )

    # Attach the unified entity_id to all identifiers
    logger.info("Attaching unified entity IDs and aggregating by source")
    dedup = (
        preliminary.join(entity_map.lazy(), on="identifier", how="left")
        .group_by(["type_id", "identifier", "entity_id_unified"])
        .agg(pl.col("source_id").unique().sort().alias("source_ids"))
        .join(cv_lf, on="type_id", how="left")
        .with_row_count("id")
        .select(["id", "entity_id_unified", "identifier", "type_id", "source_ids"])
    )

    logger.info("Phase 2 complete: entity deduplication finished")
    return dedup


# --------------------------------------------------------------------------- #
# Statistics and Analysis
# --------------------------------------------------------------------------- #

def _log_entity_sharing_statistics(dedup_df: pl.DataFrame, sources: pl.DataFrame) -> None:
    """
    Log detailed statistics about entity sharing across sources.

    Shows:
    - How many entities are unique to each source
    - How many entities are shared across multiple sources
    - Distribution of entity sharing patterns
    """
    logger.info("=" * 80)
    logger.info("Entity Sharing Analysis")
    logger.info("=" * 80)

    # Create a mapping from source_id to source_name
    source_map = dict(zip(sources["id"].to_list(), sources["name"].to_list()))

    # Explode source_ids to get one row per entity-source pair
    entity_sources = (
        dedup_df.select(["entity_id_unified", "source_ids"])
        .unique(subset=["entity_id_unified"])
        .explode("source_ids")
        .with_columns(pl.col("source_ids").alias("source_id"))
    )

    # Count sources per entity
    entities_by_source_count = (
        entity_sources.group_by("entity_id_unified")
        .agg(pl.col("source_id").n_unique().alias("num_sources"))
    )

    # Distribution of entity sharing
    sharing_distribution = (
        entities_by_source_count.group_by("num_sources")
        .agg(pl.count().alias("num_entities"))
        .sort("num_sources")
    )

    logger.info("Entity sharing distribution:")
    total_entities = sharing_distribution["num_entities"].sum()
    for row in sharing_distribution.iter_rows(named=True):
        num_sources = row["num_sources"]
        num_entities = row["num_entities"]
        pct = 100.0 * num_entities / total_entities
        if num_sources == 1:
            logger.info(f"  {num_entities:>10,} entities ({pct:5.1f}%) are unique to a single source")
        else:
            logger.info(f"  {num_entities:>10,} entities ({pct:5.1f}%) are shared across {num_sources} sources")

    # Per-source statistics
    logger.info("")
    logger.info("Per-source entity statistics:")

    source_stats = (
        entity_sources.join(
            entities_by_source_count, on="entity_id_unified", how="left"
        )
        .group_by("source_id")
        .agg([
            pl.count().alias("total_entities"),
            (pl.col("num_sources") == 1).sum().alias("unique_entities"),
            (pl.col("num_sources") > 1).sum().alias("shared_entities"),
        ])
        .sort("source_id")
    )

    for row in source_stats.iter_rows(named=True):
        source_id = row["source_id"]
        source_name = source_map.get(source_id, f"Unknown (ID: {source_id})")
        total = row["total_entities"]
        unique = row["unique_entities"]
        shared = row["shared_entities"]
        unique_pct = 100.0 * unique / total if total > 0 else 0
        shared_pct = 100.0 * shared / total if total > 0 else 0

        logger.info(f"  {source_name}:")
        logger.info(f"    Total entities:  {total:>10,}")
        logger.info(f"    Unique only:     {unique:>10,} ({unique_pct:5.1f}%)")
        logger.info(f"    Shared:          {shared:>10,} ({shared_pct:5.1f}%)")

    # Summary statistics
    logger.info("")
    unique_to_single = sharing_distribution.filter(pl.col("num_sources") == 1)["num_entities"].sum()
    shared_across_multiple = sharing_distribution.filter(pl.col("num_sources") > 1)["num_entities"].sum()

    logger.info("Summary:")
    logger.info(f"  Total unique entities: {total_entities:,}")
    logger.info(f"  Entities unique to one source: {unique_to_single:,} ({100.0 * unique_to_single / total_entities:.1f}%)")
    logger.info(f"  Entities merged across sources: {shared_across_multiple:,} ({100.0 * shared_across_multiple / total_entities:.1f}%)")

    # Calculate how many entities were "saved" by merging
    total_before_merge = entity_sources.height
    entities_saved = total_before_merge - total_entities
    reduction_pct = 100.0 * entities_saved / total_before_merge if total_before_merge > 0 else 0

    logger.info(f"  Total entity-source pairs before merging: {total_before_merge:,}")
    logger.info(f"  Reduction due to merging: {entities_saved:,} ({reduction_pct:.1f}%)")

    logger.info("=" * 80)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def build_entity_identifiers(
    data_root: Path,
    output_dir: Path,
    cv_terms: pl.DataFrame,
    sources: pl.DataFrame,
    merge_safe_types: Sequence[str] = MERGE_SAFE_IDENTIFIER_TYPES,
    persist: bool = True,
    compression: str = "zstd",
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Build unified entity identifier tables:
      1. Flatten and canonicalize all identifiers (preliminary)
      2. Cluster entities using merge-safe identifiers + union-find logic
      3. Propagate entity IDs to all identifiers and aggregate by source
    """
    logger.info("=" * 80)
    logger.info("Starting entity identifier build process")
    logger.info(f"Data root: {data_root}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Merge-safe identifier types: {list(merge_safe_types)}")
    logger.info("=" * 80)

    data_root, output_dir = Path(data_root), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Map merge-safe types to internal numeric IDs
    logger.info("Mapping merge-safe identifier types to internal IDs")
    merge_safe_type_ids = frozenset(
        cv_terms.filter(pl.col("accession").is_in(list(merge_safe_types)))
        .select("id")
        .to_series()
        .to_list()
    )
    logger.info(f"Merge-safe type IDs: {sorted(merge_safe_type_ids)}")

    # Phase 1 — flatten identifiers
    prelim_lf = _collect_preliminary_identifiers_lazy(data_root, cv_terms, sources)

    # Phase 2 — deduplicate and unify entities
    dedup_lf = _deduplicate_identifiers_lazy(prelim_lf, cv_terms, merge_safe_type_ids)

    # Optionally persist results
    if persist:
        logger.info("Persisting results to disk")
        prelim_path = output_dir / "entity_identifier_preliminary.parquet"
        final_path = output_dir / "entity_identifier.parquet"

        logger.info(f"Writing preliminary identifiers to {prelim_path}")
        prelim_df = prelim_lf.collect()
        logger.info(f"Preliminary identifiers shape: {prelim_df.shape}")
        prelim_df.write_parquet(prelim_path, compression=compression, statistics=True)

        logger.info(f"Writing deduplicated identifiers to {final_path}")
        dedup_lf.sink_parquet(str(final_path), compression=compression, statistics=True)
        dedup_df = pl.read_parquet(final_path)
        logger.info(f"Deduplicated identifiers shape: {dedup_df.shape}")
        logger.info(f"Number of unique entities: {dedup_df['entity_id_unified'].n_unique()}")
    else:
        logger.info("Collecting results (not persisting)")
        prelim_df = prelim_lf.collect()
        dedup_df = dedup_lf.collect()
        logger.info(f"Preliminary identifiers shape: {prelim_df.shape}")
        logger.info(f"Deduplicated identifiers shape: {dedup_df.shape}")
        logger.info(f"Number of unique entities: {dedup_df['entity_id_unified'].n_unique()}")

    # Detailed entity sharing analysis
    _log_entity_sharing_statistics(dedup_df, sources)

    logger.info("=" * 80)
    logger.info("Entity identifier build process complete")
    logger.info("=" * 80)

    return prelim_df, dedup_df