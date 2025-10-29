"""Build entity identifiers using graph-based equivalence detection.

Three-step process:
1. Extract merge-safe identifiers per source (InChI, InChIKey, Uniprot)
2. Build per-source edges (connect all identifiers that co-occur in a record)
3. Merge all edges into one global graph for entity resolution
"""
from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
import logging
import polars as pl
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv


# --------------------------------------------------------------------------- #
# Union-Find for tracking connected components
# --------------------------------------------------------------------------- #

class UnionFind:
    """Union-Find data structure for tracking connected components."""

    def __init__(self):
        self.parent = {}
        self.rank = {}
        self._num_components = 0

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        """Find the root of x with path compression."""
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
            self._num_components += 1
            return x

        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: tuple[str, str], y: tuple[str, str]) -> bool:
        """Union two elements. Returns True if they were in different components."""
        root_x = self.find(x)
        root_y = self.find(y)

        if root_x == root_y:
            return False

        # Union by rank
        if self.rank[root_x] < self.rank[root_y]:
            self.parent[root_x] = root_y
        elif self.rank[root_x] > self.rank[root_y]:
            self.parent[root_y] = root_x
        else:
            self.parent[root_y] = root_x
            self.rank[root_x] += 1

        self._num_components -= 1
        return True

    @property
    def num_components(self) -> int:
        """Get the current number of connected components."""
        return self._num_components

    @property
    def num_nodes(self) -> int:
        """Get the total number of nodes."""
        return len(self.parent)

__all__ = ['MERGE_SAFE_IDENTIFIER_TYPES', 'build_entity_identifiers']

logger = logging.getLogger(__name__)

# Merge-safe identifier types (used for cross-source merging)
MERGE_SAFE_IDENTIFIER_TYPES = frozenset({
    IdentifierNamespaceCv.UNIPROT.value,
    IdentifierNamespaceCv.STANDARD_INCHI.value,
    IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
})


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _iter_parquet_files(root: Path) -> Iterable[Path]:
    """Iterate over all parquet files in subdirectories."""
    for d in sorted(root.glob('*')):
        if d.is_dir():
            yield from sorted(d.glob('*.parquet'))




def _load_source_data(data_root: Path) -> dict[str, list[tuple[Path, pl.LazyFrame]]]:
    """Load all source data files into lazy frames, organized by source."""
    sources_data = {}

    for path in _iter_parquet_files(data_root):
        source_name = path.parent.name
        lf = pl.scan_parquet(str(path))

        if source_name not in sources_data:
            sources_data[source_name] = []
        sources_data[source_name].append((path, lf))

    logger.info(f"Found {len(sources_data)} sources with parquet files")
    return sources_data


def _extract_identifiers_from_file(
    lf: pl.LazyFrame,
    source_name: str
) -> pl.LazyFrame | None:
    """Extract identifiers from a single source file.

    Returns a LazyFrame where each row represents one record with its identifiers list.
    Returns None if the file doesn't contain entity identifiers.
    """
    cols = set(lf.collect_schema().names())
    lfs = []

    # Extract from main 'identifiers' column
    if 'identifiers' in cols:
        extracted = (
            lf.select([
                pl.col('identifiers')
            ])
            .filter(
                pl.col('identifiers').is_not_null() &
                (pl.col('identifiers').list.len() > 0)
            )
        )
        lfs.append(extracted)

    # Extract from entity_a and entity_b
    for side in ('entity_a', 'entity_b'):
        if side in cols:
            extracted = (
                lf.select([
                    pl.col(side).struct.field('identifiers').alias('identifiers')
                ])
                .filter(
                    pl.col('identifiers').is_not_null() &
                    (pl.col('identifiers').list.len() > 0)
                )
            )
            lfs.append(extracted)

    if not lfs:
        return None

    combined = pl.concat(lfs, how='diagonal_relaxed')
    combined = combined.with_columns(pl.lit(source_name).alias('source_name'))

    return combined


# --------------------------------------------------------------------------- #
# Step 1: Extract merge-safe identifiers per source
# --------------------------------------------------------------------------- #

def _extract_merge_safe_identifiers_for_source(
    source_name: str,
    file_list: list[tuple[Path, pl.LazyFrame]],
    merge_safe_types: frozenset[str],
) -> pl.DataFrame | None:
    """Extract merge-safe identifiers for a single source.

    Args:
        source_name: Name of the source
        file_list: List of (path, LazyFrame) tuples for this source
        merge_safe_types: Frozenset of merge-safe identifier type accessions

    Returns:
        DataFrame with columns [source_name, identifiers] or None if no identifiers found
    """
    merge_safe_set = set(merge_safe_types)

    # Extract identifier groups from all files for this source
    all_identifiers = []
    for _, lf in file_list:
        identifiers = _extract_identifiers_from_file(lf, source_name)
        if identifiers is not None:
            all_identifiers.append(identifiers)

    if not all_identifiers:
        return None

    # Combine all identifier groups for this source
    combined = pl.concat(all_identifiers, how='diagonal_relaxed').collect()

    # Filter to keep only merge-safe identifiers
    # Each record keeps only the identifiers whose 'type' is in merge_safe_types
    filtered = (
        combined
        .with_columns([
            pl.col('identifiers').list.filter(
                pl.element().struct.field('type').is_in(list(merge_safe_set))
            ).alias('identifiers')
        ])
        .filter(pl.col('identifiers').list.len() > 0)  # Drop records with no merge-safe IDs
    )

    # Deduplicate: remove duplicate identifier lists
    deduped = filtered.unique(subset=['source_name', 'identifiers'])

    return deduped


# --------------------------------------------------------------------------- #
# Step 2: Build edges from identifier co-occurrences
# --------------------------------------------------------------------------- #

def _build_edges_from_identifiers(
    df: pl.DataFrame,
    source_name: str,
) -> pl.DataFrame:
    """Build edges from a DataFrame of identifier lists.

    For each record, creates edges between all pairs of identifiers that co-occur.

    Args:
        df: DataFrame with columns [source_name, identifiers]
        source_name: Name of the source (for logging)

    Returns:
        DataFrame with edges [id_a, id_b, source_name]
    """
    # For each record, create all pairwise edges between identifiers
    edges = (
        df
        .with_row_index('record_id')
        .explode('identifiers')
        .select([
            'record_id',
            'source_name',
            pl.col('identifiers').alias('identifier')
        ])
    )

    # Self-join to create all pairs within each record
    edges_paired = (
        edges
        .join(edges, on=['record_id', 'source_name'], suffix='_b')
        .filter(
            # Keep only distinct pairs (avoid self-loops and duplicates)
            # Use lexicographic ordering to avoid duplicate pairs
            (pl.col('identifier').struct.field('type') < pl.col('identifier_b').struct.field('type')) |
            (
                (pl.col('identifier').struct.field('type') == pl.col('identifier_b').struct.field('type')) &
                (pl.col('identifier').struct.field('value') < pl.col('identifier_b').struct.field('value'))
            )
        )
        .select([
            'source_name',
            pl.col('identifier').alias('id_a'),
            pl.col('identifier_b').alias('id_b'),
        ])
        .unique()
    )

    return edges_paired


def _merge_edges_into_graph(
    global_graph: pl.DataFrame | None,
    new_edges: pl.DataFrame,
    union_find: UnionFind,
) -> pl.DataFrame:
    """Merge new edges into the global graph and update connected components.

    Args:
        global_graph: Current global graph (or None if first source)
        new_edges: New edges to merge [source_name, id_a, id_b]
        union_find: UnionFind structure for tracking connected components

    Returns:
        Updated global graph [id_a, id_b, sources]
    """
    # Update union-find with new edges
    for row in new_edges.iter_rows(named=True):
        id_a = (row['id_a']['type'], row['id_a']['value'])
        id_b = (row['id_b']['type'], row['id_b']['value'])
        union_find.union(id_a, id_b)

    if global_graph is None:
        # First source: just group the edges
        return (
            new_edges
            .group_by(['id_a', 'id_b'])
            .agg([
                pl.col('source_name').unique().alias('sources')
            ])
        )

    # Combine with existing graph
    # First, expand existing graph back to edge format
    existing_edges = (
        global_graph
        .explode('sources')
        .select([
            pl.col('sources').alias('source_name'),
            'id_a',
            'id_b',
        ])
    )

    # Combine old and new edges
    combined = pl.concat([existing_edges, new_edges], how='diagonal_relaxed')

    # Re-aggregate
    merged = (
        combined
        .group_by(['id_a', 'id_b'])
        .agg([
            pl.col('source_name').unique().alias('sources')
        ])
    )

    return merged


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def build_entity_identifiers(
    data_root: Path,
    merge_safe_types: frozenset[str] = MERGE_SAFE_IDENTIFIER_TYPES,
) -> tuple[pl.DataFrame, int]:
    """Build entity identifiers using graph-based equivalence detection.

    Processes sources iteratively to minimize memory usage:
    1. For each source: extract merge-safe identifiers
    2. For each source: build edges from co-occurring identifiers
    3. Incrementally merge edges into global graph
    4. Release source data after processing

    Args:
        data_root: Root directory containing source parquet files
        merge_safe_types: Frozenset of CV accessions for merge-safe identifier types

    Returns:
        Tuple of (edges_df, num_entities) where:
        - edges_df: DataFrame with global edges [id_a, id_b, sources]
          Each edge represents an equivalence between two identifiers
        - num_entities: Number of unique entities (connected components)
    """
    data_root = Path(data_root)

    logger.info("=" * 80)
    logger.info("Building entity identifiers (iterative mode)")
    logger.info("=" * 80)
    logger.info(f"Data root: {data_root}")
    logger.info(f"Merge-safe types: {sorted(merge_safe_types)}")

    # Load source data
    sources_data = _load_source_data(data_root)
    num_sources = len(sources_data)
    logger.info(f"Found {num_sources} sources to process")

    # Initialize global graph and union-find
    global_graph: pl.DataFrame | None = None
    union_find = UnionFind()
    sources_processed = 0

    # Track merge statistics per source
    merge_stats = []

    # Process each source iteratively
    for source_name, file_list in sorted(sources_data.items()):
        logger.info("=" * 80)
        logger.info(f"Processing source [{sources_processed + 1}/{num_sources}]: {source_name}")
        logger.info("=" * 80)

        # Step 1: Extract merge-safe identifiers
        identifiers_df = _extract_merge_safe_identifiers_for_source(
            source_name, file_list, merge_safe_types
        )

        if identifiers_df is None:
            logger.warning(f"  No identifier columns found in {source_name}, skipping")
            sources_processed += 1
            continue

        num_records = len(identifiers_df)
        logger.info(f"  Records: {num_records:,}")

        # Track components before processing this source
        components_before = union_find.num_components

        # Step 2: Build edges from co-occurring identifiers
        edges = _build_edges_from_identifiers(identifiers_df, source_name)

        # Step 3: Merge into global graph
        global_graph = _merge_edges_into_graph(global_graph, edges, union_find)

        # Track components after processing this source
        components_after = union_find.num_components
        new_components = components_after - components_before
        merged_records = num_records - new_components

        logger.info(f"  Entities: {components_after:,} (+{new_components:,})")
        logger.info(f"  Merged: {merged_records:,} ({merged_records / num_records * 100:.1f}%)")

        # Store stats
        merge_stats.append({
            'source_name': source_name,
            'num_records': num_records,
            'merged_records': merged_records,
            'merge_percentage': (merged_records / num_records * 100) if num_records > 0 else 0
        })

        sources_processed += 1

        # Free memory
        del identifiers_df
        del edges

    logger.info("=" * 80)
    logger.info("Entity identifier building complete!")
    logger.info("=" * 80)

    if global_graph is None:
        logger.warning("No edges were created from any source")
        empty_df = pl.DataFrame(schema={'id_a': pl.Struct, 'id_b': pl.Struct, 'sources': pl.List(pl.Utf8)})
        return empty_df, 0

    num_components = union_find.num_components
    return global_graph, num_components
