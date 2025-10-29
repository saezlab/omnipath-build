"""Simplified entity identifier builder with clear per-source steps.

This module builds a unified entity identifier table by:
1. Deduplicating per source
2. Exploding identifiers and assigning entity IDs
3. Converting identifier types to IDs
4. Splitting into mergeable and non-mergeable buckets
5. Merging across sources using merge-friendly identifiers
6. Appending all results
"""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Iterable, Sequence
import logging
import polars as pl
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

__all__ = ['MERGE_SAFE_IDENTIFIER_TYPES', 'build_entity_identifiers_simple']

logger = logging.getLogger(__name__)

# Merge-safe identifier types (used for cross-source merging)
MERGE_SAFE_IDENTIFIER_TYPES = frozenset({
    IdentifierNamespaceCv.UNIPROT.value,
    IdentifierNamespaceCv.STANDARD_INCHI.value,
    IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
})


@dataclass(frozen=True)
class IdentifierExtractionContext:
    """Context for extracting identifiers from a source file."""
    path: Path
    origin: str


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _iter_parquet_files(root: Path) -> Iterable[Path]:
    """Iterate over all parquet files in subdirectories."""
    for d in sorted(root.glob('*')):
        if d.is_dir():
            yield from sorted(d.glob('*.parquet'))




def _load_source_data(data_root: Path) -> dict[str, pl.LazyFrame]:
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
    """Extract all identifiers from a single source file, preserving co-occurrence.

    Returns a DataFrame where each row represents one entity with all its identifiers
    grouped together.

    Returns None if the file doesn't contain entity identifiers.
    """
    cols = set(lf.collect_schema().names())
    lfs = []

    # Extract from main 'identifiers' column
    if 'identifiers' in cols:
        extracted = (
            lf.select([
                pl.col('source').alias('sub_source'),
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
                    pl.col(side).struct.field('source').alias('sub_source'),
                    pl.col(side).struct.field('identifiers').alias('identifiers')
                ])
                .filter(
                    pl.col('identifiers').is_not_null() &
                    (pl.col('identifiers').list.len() > 0)
                )
            )
            lfs.append(extracted)

    if not lfs:
        # No identifier columns found - this file doesn't contain entity identifiers
        return None

    combined = pl.concat(lfs, how='diagonal_relaxed')

    # Add source name (identifiers assumed to be already canonicalized)
    combined = combined.with_columns(pl.lit(source_name).alias('source_name'))

    return combined


# --------------------------------------------------------------------------- #
# Step 1: Deduplicate per source
# --------------------------------------------------------------------------- #

def step1_deduplicate_per_source(
    sources_data: dict[str, list[tuple[Path, pl.LazyFrame]]],
    output_dir: Path,
    compression: str = 'zstd',
) -> dict[str, Path]:
    """Step 1: Deduplicate entities per source, preserving identifier co-occurrence.

    For each source, extract all identifier groups and deduplicate based on
    the complete set of identifiers that appear together.

    Each row in the output represents one unique entity (identifier group).

    Returns:
        Dictionary mapping source_name to output parquet path
    """
    logger.info("=" * 80)
    logger.info("STEP 1: Deduplicate per source")
    logger.info("=" * 80)

    step1_dir = output_dir / 'step1_dedup_per_source'
    step1_dir.mkdir(parents=True, exist_ok=True)

    output_paths = {}

    for source_name, file_list in sorted(sources_data.items()):
        logger.info(f"\nProcessing source: {source_name}")
        logger.info(f"  Files: {len(file_list)}")

        # Extract identifier groups from all files for this source
        all_identifier_groups = []
        for _, lf in file_list:
            identifier_groups = _extract_identifiers_from_file(lf, source_name)
            if identifier_groups is not None:
                all_identifier_groups.append(identifier_groups)

        if not all_identifier_groups:
            logger.warning(f"  No identifier columns found in {source_name}, skipping")
            continue

        # Combine all identifier groups for this source
        combined = pl.concat(all_identifier_groups, how='diagonal_relaxed').collect()

        # Deduplicate based on the complete identifier set
        # Create a canonical string representation of the identifier list for comparison
        deduped = (
            combined
            .with_columns([
                # Create a canonical string representation by converting each identifier to "type:value"
                # and joining them (they're already in a consistent order within the source data)
                pl.col('identifiers').list.eval(
                    pl.concat_str([
                        pl.element().struct.field('type'),
                        pl.lit(':'),
                        pl.element().struct.field('value')
                    ])
                ).list.join('||').alias('identifier_key')
            ])
            .unique(subset=['source_name', 'sub_source', 'identifier_key'])
            .select(['source_name', 'sub_source', 'identifiers'])
        )

        # Save to parquet
        output_path = step1_dir / f'{source_name}.parquet'
        deduped.write_parquet(output_path, compression=compression, statistics=True)
        output_paths[source_name] = output_path

        total_identifiers = deduped.select(
            pl.col('identifiers').list.len().sum()
        ).item()
        logger.info(f"  Unique entity groups: {len(deduped):,}")
        logger.info(f"  Total identifiers: {total_identifiers:,}")
        logger.info(f"  Output: {output_path.relative_to(output_dir)}")

    logger.info(f"\nStep 1 complete. Processed {len(output_paths)} sources")
    return output_paths


# --------------------------------------------------------------------------- #
# Step 2: Explode identifiers and assign entity IDs
# --------------------------------------------------------------------------- #

def step2_assign_entity_ids(
    step1_paths: dict[str, Path],
    output_dir: Path,
    compression: str = 'zstd',
) -> dict[str, Path]:
    """Step 2: Assign integer entity IDs per source.

    For each source, assign each identifier group (entity) a unique integer ID.
    Then explode the identifier list so each identifier gets its own row,
    all sharing the same entity_id.

    Output schema: entity_id, source_name, sub_sources (array), id_type, id_value

    Returns:
        Dictionary mapping source_name to output parquet path
    """
    logger.info("=" * 80)
    logger.info("STEP 2: Assign entity IDs per source")
    logger.info("=" * 80)

    step2_dir = output_dir / 'step2_entity_ids'
    step2_dir.mkdir(parents=True, exist_ok=True)

    output_paths = {}

    for source_name, input_path in sorted(step1_paths.items()):
        logger.info(f"\nProcessing source: {source_name}")

        # Load deduplicated identifier groups
        df = pl.read_parquet(input_path)

        # Assign entity_id to each row (each row is one entity with multiple identifiers)
        # Then explode the identifiers list so each identifier gets its own row
        with_entity_id = (
            df
            .with_row_index('entity_id')
            .with_columns([
                # Collect all sub_sources into an array (for when we later merge entities)
                pl.col('sub_source').cast(pl.List(pl.String)).alias('sub_sources')
            ])
            .select([
                'entity_id',
                'source_name',
                'sub_sources',
                'identifiers',
            ])
            .explode('identifiers')
            .with_columns([
                pl.col('identifiers').struct.field('type').alias('id_type'),
                pl.col('identifiers').struct.field('value').alias('id_value'),
            ])
            .select([
                'entity_id',
                'source_name',
                'sub_sources',
                'id_type',
                'id_value',
            ])
        )

        # Save to parquet
        output_path = step2_dir / f'{source_name}.parquet'
        with_entity_id.write_parquet(output_path, compression=compression, statistics=True)
        output_paths[source_name] = output_path

        num_entities = with_entity_id.select('entity_id').n_unique()
        num_identifiers = len(with_entity_id)
        logger.info(f"  Entities: {num_entities:,}")
        logger.info(f"  Total identifiers: {num_identifiers:,}")
        logger.info(f"  Output: {output_path.relative_to(output_dir)}")

    logger.info(f"\nStep 2 complete. Processed {len(output_paths)} sources")
    return output_paths


# --------------------------------------------------------------------------- #
# Step 3: Replace id_type strings with IDs
# --------------------------------------------------------------------------- #

def step3_replace_type_with_ids(
    step2_paths: dict[str, Path],
    cv_terms: pl.DataFrame,
    output_dir: Path,
    compression: str = 'zstd',
) -> dict[str, Path]:
    """Step 3: Replace id_type accession strings with integer IDs.

    Join with cv_term table to get id_type_id and drop the text column
    to save memory.

    Output schema: entity_id, source_name, sub_sources, id_type_id, id_value

    Returns:
        Dictionary mapping source_name to output parquet path
    """
    logger.info("=" * 80)
    logger.info("STEP 3: Replace id_type strings with IDs")
    logger.info("=" * 80)

    step3_dir = output_dir / 'step3_type_ids'
    step3_dir.mkdir(parents=True, exist_ok=True)

    # Prepare CV terms lookup
    cv_lookup = cv_terms.select([
        pl.col('accession').alias('id_type'),
        pl.col('id').alias('id_type_id'),
    ])

    logger.info(f"CV terms available: {len(cv_lookup):,}")

    output_paths = {}

    for source_name, input_path in sorted(step2_paths.items()):
        logger.info(f"\nProcessing source: {source_name}")

        # Load entity identifiers
        df = pl.read_parquet(input_path)

        # Join with CV terms to get id_type_id
        with_type_ids = (
            df
            .join(cv_lookup, on='id_type', how='left')
            .select([
                'entity_id',
                'source_name',
                'sub_sources',
                'id_type_id',
                'id_value',
            ])
        )

        # Check for missing CV term mappings
        missing_count = with_type_ids.filter(pl.col('id_type_id').is_null()).height
        if missing_count > 0:
            logger.warning(f"  Missing CV term mappings: {missing_count:,} identifiers")
            # Show which types are missing
            missing_types = (
                df.join(cv_lookup, on='id_type', how='left')
                .filter(pl.col('id_type_id').is_null())
                .select('id_type')
                .unique()
            )
            logger.warning(f"  Missing types: {missing_types['id_type'].to_list()}")

        # Save to parquet
        output_path = step3_dir / f'{source_name}.parquet'
        with_type_ids.write_parquet(output_path, compression=compression, statistics=True)
        output_paths[source_name] = output_path

        logger.info(f"  Identifiers: {len(with_type_ids):,}")
        logger.info(f"  Output: {output_path.relative_to(output_dir)}")

    logger.info(f"\nStep 3 complete. Processed {len(output_paths)} sources")
    return output_paths


# --------------------------------------------------------------------------- #
# Step 4: Split into mergeable and non-mergeable buckets
# --------------------------------------------------------------------------- #

def step4_split_buckets(
    step3_paths: dict[str, Path],
    merge_safe_type_ids: frozenset[int],
    output_dir: Path,
    compression: str = 'zstd',
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Step 4: Split entities into mergeable (A) and non-mergeable (B) buckets.

    Bucket A: Entities that have at least one merge-friendly identifier
    Bucket B: Entities that don't have any merge-friendly identifiers

    Returns:
        (bucket_a_paths, bucket_b_paths) - dictionaries mapping source_name to paths
    """
    logger.info("=" * 80)
    logger.info("STEP 4: Split into mergeable and non-mergeable buckets")
    logger.info("=" * 80)
    logger.info(f"Merge-safe type IDs: {sorted(merge_safe_type_ids)}")

    bucket_a_dir = output_dir / 'step4_bucket_a_mergeable'
    bucket_b_dir = output_dir / 'step4_bucket_b_non_mergeable'
    bucket_a_dir.mkdir(parents=True, exist_ok=True)
    bucket_b_dir.mkdir(parents=True, exist_ok=True)

    bucket_a_paths = {}
    bucket_b_paths = {}

    for source_name, input_path in sorted(step3_paths.items()):
        logger.info(f"\nProcessing source: {source_name}")

        # Load entity identifiers
        df = pl.read_parquet(input_path)

        # Find entities with at least one merge-safe identifier
        entities_with_merge_safe = (
            df
            .filter(pl.col('id_type_id').is_in(list(merge_safe_type_ids)))
            .select('entity_id')
            .unique()
        )

        # Bucket A: entities with merge-safe identifiers
        bucket_a = df.filter(pl.col('entity_id').is_in(entities_with_merge_safe['entity_id']))

        # Bucket B: entities without merge-safe identifiers
        bucket_b = df.filter(~pl.col('entity_id').is_in(entities_with_merge_safe['entity_id']))

        # Save buckets
        if len(bucket_a) > 0:
            output_path_a = bucket_a_dir / f'{source_name}.parquet'
            bucket_a.write_parquet(output_path_a, compression=compression, statistics=True)
            bucket_a_paths[source_name] = output_path_a
            logger.info(f"  Bucket A (mergeable): {bucket_a.select('entity_id').n_unique():,} entities, {len(bucket_a):,} identifiers")

        if len(bucket_b) > 0:
            output_path_b = bucket_b_dir / f'{source_name}.parquet'
            bucket_b.write_parquet(output_path_b, compression=compression, statistics=True)
            bucket_b_paths[source_name] = output_path_b
            logger.info(f"  Bucket B (non-mergeable): {bucket_b.select('entity_id').n_unique():,} entities, {len(bucket_b):,} identifiers")

    logger.info(f"\nStep 4 complete.")
    logger.info(f"  Bucket A sources: {len(bucket_a_paths)}")
    logger.info(f"  Bucket B sources: {len(bucket_b_paths)}")

    return bucket_a_paths, bucket_b_paths


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def build_entity_identifiers_simple(
    data_root: Path,
    output_dir: Path,
    cv_terms: pl.DataFrame,
    merge_safe_types: Sequence[str] = MERGE_SAFE_IDENTIFIER_TYPES,
    compression: str = 'zstd',
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Build entity identifier tables with simplified per-source processing.

    Steps:
    1. Deduplicate per source
    2. Explode identifiers and assign entity IDs
    3. Replace id_type strings with IDs
    4. Split into mergeable and non-mergeable buckets

    Args:
        data_root: Root directory containing source parquet files
        output_dir: Directory to write output files
        cv_terms: DataFrame with CV terms (must have 'accession' and 'id' columns)
        merge_safe_types: Sequence of CV accessions for merge-safe identifier types
        compression: Compression algorithm for parquet files

    Returns:
        (bucket_a_paths, bucket_b_paths) - paths to mergeable and non-mergeable entities
    """
    data_root = Path(data_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get merge-safe type IDs from CV terms
    merge_safe_type_ids = frozenset(
        cv_terms.filter(pl.col('accession').is_in(list(merge_safe_types)))
        .select('id')
        .to_series()
        .to_list()
    )
    logger.info(f"Merge-safe identifier types: {list(merge_safe_types)}")
    logger.info(f"Merge-safe type IDs: {sorted(merge_safe_type_ids)}")

    # Load source data
    sources_data = _load_source_data(data_root)

    # Step 1: Deduplicate per source
    step1_paths = step1_deduplicate_per_source(sources_data, output_dir, compression)

    # Step 2: Assign entity IDs
    step2_paths = step2_assign_entity_ids(step1_paths, output_dir, compression)

    # Step 3: Replace type strings with IDs
    step3_paths = step3_replace_type_with_ids(step2_paths, cv_terms, output_dir, compression)

    # Step 4: Split into buckets
    bucket_a_paths, bucket_b_paths = step4_split_buckets(
        step3_paths, merge_safe_type_ids, output_dir, compression
    )

    logger.info("=" * 80)
    logger.info("Per-source processing complete!")
    logger.info("=" * 80)
    logger.info(f"Output directory: {output_dir}")

    return bucket_a_paths, bucket_b_paths
