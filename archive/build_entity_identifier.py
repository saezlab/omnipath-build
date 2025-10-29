from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Iterable, Sequence
import polars as pl
from omnipath_build.utils.cv_term_enums import IdentifierNamespaceCv

__all__ = ['MERGE_SAFE_IDENTIFIER_TYPES', 'build_entity_identifiers']


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
    for d in sorted(root.glob('*')):
        if d.is_dir():
            yield from sorted(d.glob('*.parquet'))


def _canonicalize_expr() -> pl.Expr:
    return (
        pl.when(pl.col('type_accession') == IdentifierNamespaceCv.UNIPROT.value)
          .then(pl.col('identifier').str.to_uppercase().str.strip_chars())
        .when(pl.col('type_accession') == IdentifierNamespaceCv.STANDARD_INCHI.value)
          .then(pl.col('identifier').str.strip_chars().str.replace('^inchi=', 'InChI=', literal=False))
        .when(pl.col('type_accession') == IdentifierNamespaceCv.STANDARD_INCHI_KEY.value)
          .then(pl.col('identifier').str.to_uppercase().str.strip_chars())
        .otherwise(pl.col('identifier'))
    )


def _explode_identifiers(lf: pl.LazyFrame, source_expr: pl.Expr, id_expr: pl.Expr,
                         ctx: IdentifierExtractionContext) -> pl.LazyFrame:
    return (
        lf.select([source_expr.alias('source_name'), id_expr.alias('id_struct')])
          .explode('id_struct')
          .drop_nulls('id_struct')
          .with_columns([
              pl.col('id_struct').struct.field('type').alias('type_accession'),
              pl.col('id_struct').struct.field('value').alias('identifier'),
              pl.lit(ctx.origin).alias('origin'),
              pl.lit(str(ctx.path.relative_to(ctx.path.parents[1]))).alias('source_file'),
          ])
          .drop('id_struct')
          .with_columns(_canonicalize_expr().alias('identifier'))
          .unique(subset=['type_accession', 'identifier', 'source_name'])
          .with_columns(
              (pl.col('source_name').cast(pl.Utf8) + ':' + pl.lit(ctx.origin))
              .hash(seed=42)
              .alias('context_id')
          )
    )


def _collect_preliminary_identifiers_lazy(
    data_root: Path,
    cv_terms: pl.DataFrame,
    sources: pl.DataFrame,
    include_provenance: bool = True,
) -> pl.LazyFrame:
    lfs = []
    for path in _iter_parquet_files(data_root):
        lf = pl.scan_parquet(str(path))
        cols = set(lf.collect_schema().names())
        base = f'{path.parent.name}/{path.stem}'
        if 'identifiers' in cols:
            lfs.append(_explode_identifiers(lf, pl.col('source'), pl.col('identifiers'),
                                            IdentifierExtractionContext(path, f'{base}:entity')))
        for side in ('entity_a', 'entity_b'):
            if side in cols:
                lfs.append(_explode_identifiers(
                    lf, pl.col(side).struct.field('source'),
                    pl.col(side).struct.field('identifiers'),
                    IdentifierExtractionContext(path, f'{base}:{side}')
                ))

    if not lfs:
        return pl.LazyFrame(schema={'source_name': pl.Utf8, 'type_accession': pl.Utf8,
                                    'identifier': pl.Utf8, 'context_id': pl.UInt64})

    lf = pl.concat(lfs, how='diagonal_relaxed').filter(
        pl.col('identifier').is_not_null() & (pl.col('identifier').str.len_chars() > 0)
    )

    cv_lf = pl.from_pandas(cv_terms.select(['accession', 'id'])
                           .rename({'accession': 'type_accession', 'id': 'type_id'}).to_pandas()).lazy()
    src_lf = pl.from_pandas(sources.select(['name', 'id'])
                            .rename({'name': 'source_name', 'id': 'source_id'}).to_pandas()).lazy()
    lf = lf.join(cv_lf, on='type_accession', how='left')
    lf = lf.join(src_lf, on='source_name', how='left')

    if not include_provenance:
        lf = lf.drop(['origin', 'source_file'])
    return lf


# --------------------------------------------------------------------------- #
# Streaming dedup + integer entity clustering
# --------------------------------------------------------------------------- #

def _deduplicate_identifiers_lazy(
    preliminary: pl.LazyFrame,
    cv_terms: pl.DataFrame,
    merge_safe_type_ids: frozenset[int],
) -> pl.LazyFrame:
    """Global deduplication with integer entity IDs (streaming-friendly)."""
    cv_lf = pl.from_pandas(
        cv_terms.select(['id', 'accession'])
                .rename({'id': 'type_id', 'accession': 'type_accession'})
                .to_pandas()
    ).lazy()

    # record-level clusters
    ctx_links = (
        preliminary.select(['context_id', 'identifier'])
        .unique()
        .rename({'context_id': 'cluster_key'})
    )

    # merge-safe clusters
    merge_safe_links = (
        preliminary.filter(pl.col('type_id').is_in(list(merge_safe_type_ids)))
        .select(['identifier'])
        .unique()
        .with_columns(pl.lit(0).alias('dummy'))  # single group key
        .with_columns(pl.concat_str(['dummy', 'identifier']).hash(seed=7).alias('cluster_key'))
        .select(['cluster_key', 'identifier'])
    )

    # union both link sets
    cluster_links = pl.concat([ctx_links, merge_safe_links], how='diagonal_relaxed')

    # assign integer cluster IDs
    entity_map = (
        cluster_links.group_by('identifier')
        .agg(pl.col('cluster_key').min().alias('entity_group'))
        .with_columns(pl.col('entity_group').rank("dense").alias('entity_id'))
        .select(['identifier', 'entity_id'])
    )

    # join back and aggregate
    lf = (
        preliminary.join(entity_map.lazy(), on='identifier', how='left')
        .group_by(['type_id', 'identifier', 'entity_id'])
        .agg([
            pl.col('source_id').unique().sort().alias('source_ids'),
        ])
        .join(cv_lf, on='type_id', how='left')
        .with_row_count('id')
        .select([
            'id', 'entity_id', 'identifier',
            'type_id', 'source_ids',
        ])
    )

    return lf


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
    compression: str = 'zstd',
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build entity identifier tables (streaming & memory-efficient)."""
    data_root, output_dir = Path(data_root), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    merge_safe_type_ids = frozenset(
        cv_terms.filter(pl.col('accession').is_in(list(merge_safe_types)))
                .select('id').to_series().to_list()
    )

    prelim_lf = _collect_preliminary_identifiers_lazy(data_root, cv_terms, sources)
    dedup_lf = _deduplicate_identifiers_lazy(prelim_lf, cv_terms, merge_safe_type_ids)

    if persist:
        prelim_path = output_dir / 'entity_identifier_preliminary.parquet'
        final_path = output_dir / 'entity_identifier.parquet'
        prelim_lf.collect().write_parquet(prelim_path, compression=compression, statistics=True)
        dedup_lf.sink_parquet(str(final_path), compression=compression, statistics=True)

    return prelim_lf.collect(), dedup_lf.collect()