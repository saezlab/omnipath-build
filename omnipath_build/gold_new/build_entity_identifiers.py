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
    """Union-Find data structure for tracking connected components.

    Nodes are identified by (type_id, id_value) tuples where:
    - type_id can be either an int (when CV terms are used) or str (fallback)
    - id_value is always a str
    """

    def __init__(self):
        self.parent: dict[tuple[int | str, str], tuple[int | str, str]] = {}
        self.rank: dict[tuple[int | str, str], int] = {}
        self._num_components = 0

    def find(self, x: tuple[int | str, str]) -> tuple[int | str, str]:
        """Find the root of x with path compression."""
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
            self._num_components += 1
            return x

        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: tuple[int | str, str], y: tuple[int | str, str]) -> bool:
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

__all__ = [
    'MERGE_SAFE_IDENTIFIER_TYPES',
    'build_entity_identifier_unified',
]

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
    sources_data: dict[str, list[tuple[Path, pl.LazyFrame]]] = {}

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

    # Extract from members (complex components)
    # Each member represents a separate entity, so we explode and convert to identifier format
    if 'members' in cols:
        extracted = (
            lf.select([
                pl.col('members')
            ])
            .filter(
                pl.col('members').is_not_null() &
                (pl.col('members').list.len() > 0)
            )
            .explode('members')
            .filter(
                pl.col('members').struct.field('identifier_type').is_not_null() &
                pl.col('members').struct.field('identifier').is_not_null()
            )
            # Convert each member identifier to the standard identifier list format
            # We need to create a list containing a single struct element
            .select([
                pl.concat_list([
                    pl.struct([
                        pl.col('members').struct.field('identifier_type').alias('type'),
                        pl.col('members').struct.field('identifier').alias('value'),
                    ])
                ]).alias('identifiers')
            ])
        )
        lfs.append(extracted)

    if not lfs:
        return None

    combined = pl.concat(lfs, how='diagonal_relaxed')
    combined = combined.with_columns(pl.lit(source_name).alias('source_name'))

    return combined


# --------------------------------------------------------------------------- #
# Step 1: Extract identifiers (all + merge-safe) and assign local IDs
# --------------------------------------------------------------------------- #

def _prepare_local_entities_for_source(
    source_name: str,
    file_list: list[tuple[Path, pl.LazyFrame]],
    merge_safe_types: frozenset[str],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame] | None:
    """Prepare per-source local entities and edges.

    Returns a tuple of:
    - deduped_local (source_name, local_entity_id, merge_safe_identifiers)
    - local_ms_edges (source_name, local_entity_id, id_type, id_value)
    - local_all_edges (source_name, local_entity_id, id_type, id_value)

    or None if the source has no identifiers.
    """
    merge_safe_set = set(merge_safe_types)

    # Extract identifier groups from all files for this source
    all_identifiers_lfs: list[pl.LazyFrame] = []
    for _, lf in file_list:
        identifiers = _extract_identifiers_from_file(lf, source_name)
        if identifiers is not None:
            all_identifiers_lfs.append(identifiers)

    if not all_identifiers_lfs:
        return None

    # Combine all identifier groups for this source (all identifiers per record)
    combined_all = pl.concat(all_identifiers_lfs, how='diagonal_relaxed').collect()

    # Build a frame with both all and merge-safe identifiers per original record
    # Drop identifiers where type or value is null; cast values to string for consistency
    combined = (
        combined_all
        .with_columns([
            pl.col('identifiers').list.eval(
                pl.when(
                    pl.element().struct.field('type').is_not_null()
                    & pl.element().struct.field('value').is_not_null()
                )
                .then(pl.element())
                .otherwise(None)
            ).list.drop_nulls().alias('all_identifiers'),
            pl.col('identifiers').list.eval(
                pl.when(
                    pl.element().struct.field('type').is_in(list(merge_safe_set))
                    & pl.element().struct.field('value').is_not_null()
                )
                .then(pl.element())
                .otherwise(None)
            ).list.drop_nulls().alias('merge_safe_identifiers'),
        ])
    )

    # Deduplicate by full identifier list (keeps non-merge-safe-only entities)
    deduped_local = (
        combined
        .select(['source_name', 'all_identifiers', 'merge_safe_identifiers'])
        .unique(subset=['source_name', 'all_identifiers'])
        .with_row_index('local_entity_idx')
        .with_columns([
            (pl.col('source_name') + '_' + pl.col('local_entity_idx').cast(pl.Utf8)).alias('local_entity_id')
        ])
    )

    # Map local_entity_id -> merge-safe identifiers (flattened) and drop any residual nulls; cast values to Utf8
    local_ms_edges = (
        deduped_local
        .explode('merge_safe_identifiers')
        .select([
            'source_name',
            'local_entity_id',
            pl.col('merge_safe_identifiers').struct.field('type').cast(pl.Utf8).alias('id_type'),
            pl.col('merge_safe_identifiers').struct.field('value').cast(pl.Utf8).alias('id_value'),
        ])
        .filter(pl.col('id_type').is_not_null() & pl.col('id_value').is_not_null())
        .unique()
    )

    # Map local_entity_id -> ALL identifiers by joining back original records via all_identifiers
    local_all_edges = (
        combined
        .join(
            deduped_local.select('source_name', 'all_identifiers', 'local_entity_id'),
            on=['source_name', 'all_identifiers'],
            how='inner'
        )
        .select([
            'source_name',
            'local_entity_id',
            pl.col('all_identifiers').alias('identifiers')
        ])
        .explode('identifiers')
        .select([
            'source_name',
            'local_entity_id',
            pl.col('identifiers').struct.field('type').cast(pl.Utf8).alias('id_type'),
            pl.col('identifiers').struct.field('value').cast(pl.Utf8).alias('id_value'),
        ])
        .filter(pl.col('id_type').is_not_null() & pl.col('id_value').is_not_null())
        .unique()
    )

    return deduped_local, local_ms_edges, local_all_edges


# --------------------------------------------------------------------------- #
# Step 2: Build edges from identifier co-occurrences
# --------------------------------------------------------------------------- #

def _build_edges_from_identifiers(
    df: pl.DataFrame,
    source_name: str,
    identifiers_col: str = 'identifiers',
    cv_id_type_mapping: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Build edges from a DataFrame of identifier lists.

    For each record, creates edges between all pairs of identifiers that co-occur.

    Args:
        df: DataFrame with columns [source_name, <identifiers_col>]
        source_name: Name of the source (for logging)
        identifiers_col: Column name containing list[struct{type,value}]
        cv_id_type_mapping: Optional CV term mapping to convert id_type to type_id

    Returns:
        DataFrame with edges [id_a, id_b, source_name]
        id_a and id_b are structs with 'type' (int or str) and 'value' (str)
    """
    if identifiers_col != 'identifiers' and identifiers_col in df.columns:
        work_df = df.rename({identifiers_col: 'identifiers'})
    else:
        work_df = df

    # For each record, create normalized rows per identifier
    edges = (
        work_df
        .with_row_index('record_id')
        .explode('identifiers')
        .select([
            'record_id',
            'source_name',
            pl.col('identifiers').struct.field('type').cast(pl.Utf8).alias('id_type'),
            pl.col('identifiers').struct.field('value').cast(pl.Utf8).alias('id_value'),
        ])
        .filter(
            pl.col('id_type').is_not_null()
            & pl.col('id_value').is_not_null()
            & (pl.col('id_value').str.len_chars() > 0)
        )
    )

    # Join with CV mapping if provided
    if cv_id_type_mapping is not None:
        edges = (
            edges
            .join(cv_id_type_mapping, on='id_type', how='left')
            .select(['record_id', 'source_name', 'type_id', 'id_value'])
        )
        type_col = 'type_id'
    else:
        type_col = 'id_type'

    # Self-join to create all distinct pairs within each record
    edges_paired = (
        edges
        .join(edges, on=['record_id', 'source_name'], suffix='_b')
        .filter(
            (pl.col(type_col) < pl.col(f'{type_col}_b'))
            | (
                (pl.col(type_col) == pl.col(f'{type_col}_b'))
                & (pl.col('id_value') < pl.col('id_value_b'))
            )
        )
        .select([
            'source_name',
            pl.struct([
                pl.col(type_col).alias('type'),
                pl.col('id_value').alias('value'),
            ]).alias('id_a'),
            pl.struct([
                pl.col(f'{type_col}_b').alias('type'),
                pl.col('id_value_b').alias('value'),
            ]).alias('id_b'),
        ])
        .unique()
    )

    return edges_paired


def _merge_edges_into_graph(
    global_graph: pl.DataFrame | None,
    new_edges: pl.DataFrame,
    union_find: UnionFind,
    nodes_to_register: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Merge new edges into the global graph and update connected components.

    Args:
        global_graph: Current global graph (or None if first source)
        new_edges: New edges to merge [source_name, id_a, id_b]
        union_find: UnionFind structure for tracking connected components
        nodes_to_register: Optional DataFrame with columns [id_type, id_value] to ensure singletons are registered

    Returns:
        Updated global graph [id_a, id_b, sources]
    """
    # Register singleton nodes (if provided)
    if nodes_to_register is not None and len(nodes_to_register) > 0:
        for t, v in nodes_to_register.iter_rows():
            union_find.find((t, v))

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
# Utilities to convert union-find to mapping
# --------------------------------------------------------------------------- #

def _union_find_to_safe_clusters(uf: UnionFind) -> pl.DataFrame:
    """Convert UnionFind state into a mapping of (type_id,id_value) -> entity_id.

    Returns DataFrame columns: [type_id, id_value, entity_id]
    entity_id is a sequential integer starting at 1.
    type_id can be either int (when CV terms used) or str (fallback).
    """
    # Ensure path compression
    roots: dict[tuple[int | str, str], tuple[int | str, str]] = {}
    for node in list(uf.parent.keys()):
        roots[node] = uf.find(node)

    # Map unique roots to sequential ints
    unique_roots = sorted(set(roots.values()))
    root_to_entity = {root: i + 1 for i, root in enumerate(unique_roots)}

    rows = [
        {
            'type_id': t,
            'id_value': v,
            'entity_id': root_to_entity[roots[(t, v)]],
        }
        for (t, v) in roots.keys()
    ]
    return pl.DataFrame(rows)

# --------------------------------------------------------------------------- #
# Public entry point (full unified table with provenance)
# --------------------------------------------------------------------------- #

def build_entity_identifier_unified(
    data_root: Path,
    merge_safe_types: frozenset[str] = MERGE_SAFE_IDENTIFIER_TYPES,
    cv_term_df: pl.DataFrame | None = None,
    sources_df: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Build unified identifier tables with canonical entity IDs and provenance.

    Args:
        data_root: Root directory containing source data
        merge_safe_types: Set of identifier types safe for cross-source merging
        cv_term_df: Optional DataFrame with CV terms (columns: 'id', 'accession')
                    If provided, type_id (integer) will be used throughout for efficiency
        sources_df: Optional DataFrame with sources (columns: 'id', 'name')
                    If provided, source_id (integer) will be used throughout for efficiency

    Returns a tuple of:
      - safe_clusters: (type_id, id_value) -> entity_id mapping
      - record_to_global: (source_id, local_entity_id) -> entity_id
      - final_identifiers: (entity_id, type_id, id_value) with source_ids provenance
    """
    data_root = Path(data_root)
    sources_data = _load_source_data(data_root)

    # Prepare CV term mapping if provided
    cv_id_type_mapping: pl.DataFrame | None = None
    if cv_term_df is not None:
        cv_id_type_mapping = cv_term_df.select([
            pl.col('accession').alias('id_type'),
            pl.col('id').alias('type_id')
        ])
        logger.info(f"Using CV term mapping with {len(cv_id_type_mapping)} identifier types")

    # Prepare source mapping if provided
    source_name_to_id: pl.DataFrame | None = None
    if sources_df is not None:
        source_name_to_id = sources_df.select([
            pl.col('name').alias('source_name'),
            pl.col('id').alias('source_id')
        ])
        logger.info(f"Using source mapping with {len(source_name_to_id)} sources")

    union_find = UnionFind()
    all_local_ms_edges: list[pl.DataFrame] = []
    all_local_all_edges: list[pl.DataFrame] = []

    num_sources = len(sources_data)
    sources_processed = 0

    for source_name, file_list in sorted(sources_data.items()):
        logger.info("=" * 80)
        logger.info(f"Processing source [{sources_processed + 1}/{num_sources}]: {source_name}")
        logger.info("=" * 80)

        prepared = _prepare_local_entities_for_source(source_name, file_list, merge_safe_types)
        if prepared is None:
            logger.warning(f"  No identifier columns found in {source_name}, skipping")
            sources_processed += 1
            continue
        deduped_local, local_ms_edges, local_all_edges = prepared

        # Join with CV terms to replace id_type with type_id (if mapping provided)
        if cv_id_type_mapping is not None:
            local_ms_edges = (
                local_ms_edges
                .join(cv_id_type_mapping, on='id_type', how='left')
                .select(['source_name', 'local_entity_id', 'type_id', 'id_value'])
            )
            local_all_edges = (
                local_all_edges
                .join(cv_id_type_mapping, on='id_type', how='left')
                .select(['source_name', 'local_entity_id', 'type_id', 'id_value'])
            )

        # Join with sources to replace source_name with source_id (if mapping provided)
        if source_name_to_id is not None:
            local_ms_edges = (
                local_ms_edges
                .join(source_name_to_id, on='source_name', how='left')
                .select(['source_id', 'local_entity_id',
                        'type_id' if cv_id_type_mapping is not None else 'id_type',
                        'id_value'])
            )
            local_all_edges = (
                local_all_edges
                .join(source_name_to_id, on='source_name', how='left')
                .select(['source_id', 'local_entity_id',
                        'type_id' if cv_id_type_mapping is not None else 'id_type',
                        'id_value'])
            )

        # Local entity stats
        n_local = len(deduped_local)
        n_with_ms = len(deduped_local.filter(pl.col('merge_safe_identifiers').list.len() > 0))
        n_without_ms = n_local - n_with_ms
        logger.info(f"  Local entities: {n_local:,} (with MS: {n_with_ms:,}, without MS: {n_without_ms:,})")

        # Register nodes; build and merge pairwise edges
        # Use type_id (or id_type if no CV mapping) as the first element of the tuple
        type_col = 'type_id' if cv_id_type_mapping is not None else 'id_type'
        ms_nodes = local_ms_edges.select([type_col, 'id_value']).unique()
        edges = _build_edges_from_identifiers(
            deduped_local.rename({'merge_safe_identifiers': 'identifiers'}),
            source_name,
            identifiers_col='identifiers',
            cv_id_type_mapping=cv_id_type_mapping,
        )
        components_before = union_find.num_components
        _merge_edges_into_graph(global_graph=None, new_edges=edges, union_find=union_find, nodes_to_register=ms_nodes)
        components_after = union_find.num_components
        logger.info(f"  Merge-safe nodes: {len(ms_nodes):,}, edges: {len(edges):,}")
        logger.info(f"  Entities after UF: {components_after:,} (+{components_after - components_before:,})")

        all_local_ms_edges.append(local_ms_edges)
        all_local_all_edges.append(local_all_edges)
        sources_processed += 1

    # Build safe cluster mapping
    safe_clusters = _union_find_to_safe_clusters(union_find)

    if not all_local_ms_edges or not all_local_all_edges:
        # No data
        return safe_clusters, pl.DataFrame(), pl.DataFrame()

    local_ms_edges_all = pl.concat(all_local_ms_edges, how='diagonal_relaxed')
    local_all_edges_all = pl.concat(all_local_all_edges, how='diagonal_relaxed')

    # Map local entities to global entity IDs via merge-safe identifiers
    # By design, there are no conflicts, so each local entity maps to exactly one entity_id
    # Use type_id (or id_type if no CV mapping) and source_id (or source_name if no sources mapping)
    join_cols = ['type_id', 'id_value'] if cv_id_type_mapping is not None else ['id_type', 'id_value']
    source_col = 'source_id' if source_name_to_id is not None else 'source_name'
    record_to_global = (
        local_ms_edges_all
        .join(safe_clusters, on=join_cols, how='inner')
        .group_by([source_col, 'local_entity_id'])
        .agg([
            pl.col('entity_id').first().alias('entity_id'),
        ])
    )

    # Include local entities with no merge-safe IDs as first-class entities
    all_local_entities = local_all_edges_all.select([source_col, 'local_entity_id']).unique()
    unresolved = all_local_entities.join(
        record_to_global.select([source_col, 'local_entity_id']),
        on=[source_col, 'local_entity_id'],
        how='anti'
    )

    # Assign new entity IDs after the max existing entity_id from safe clusters
    max_ent = 0
    if len(safe_clusters) > 0:
        max_val = safe_clusters.select(pl.col('entity_id').max()).item()
        max_ent = int(max_val) if max_val is not None else 0

    if len(unresolved) > 0:
        unresolved = (
            unresolved
            .with_row_index('row_idx')
            .with_columns([
                (pl.col('row_idx') + max_ent + 1).cast(pl.Int64).alias('entity_id'),
            ])
            .drop('row_idx')
        )
        record_to_global = pl.concat([record_to_global, unresolved], how='diagonal_relaxed')
        logger.info(f"Added unresolved local entities (no MS): {len(unresolved):,}")

    # Propagate entity_id to all identifiers and aggregate provenance
    # Use type_id (or id_type if no CV mapping) and source_id (or source_name if no sources mapping)
    type_col = 'type_id' if cv_id_type_mapping is not None else 'id_type'
    source_col = 'source_id' if source_name_to_id is not None else 'source_name'
    final_identifiers = (
        local_all_edges_all
        .join(
            record_to_global.select([source_col, 'local_entity_id', 'entity_id']),
            on=[source_col, 'local_entity_id'],
            how='inner'
        )
        .group_by(['entity_id', type_col, 'id_value'])
        .agg([
            pl.col(source_col).unique().sort().alias('sources')
        ])
        .sort(['entity_id', type_col, 'id_value'])
    )

    return safe_clusters, record_to_global, final_identifiers
