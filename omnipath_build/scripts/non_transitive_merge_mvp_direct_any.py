#!/usr/bin/env python3
"""MVP v2: non-transitive direct-any merge-safe linking (SQL-OR style).

This implements direct links using any shared merge-safe key:
  (id_type, id_value, entity_bucket, tax_partition)
No transitive closure / connected-components collapse is performed.
"""

from __future__ import annotations

from pathlib import Path
import polars as pl

from omnipath_build.gold import build_entity_identifiers as bei


def _find_latest_per_source_dir(root: Path = Path('data')) -> Path:
    cands = sorted(root.glob('v-*/build/per_source'))
    if not cands:
        raise FileNotFoundError('No data/v-*/build/per_source found')
    return cands[-1]


def run() -> None:
    per_source_dir = _find_latest_per_source_dir()

    source_rows: list[dict] = []
    local_entities_parts: list[pl.DataFrame] = []
    ms_parts: list[pl.DataFrame] = []

    for source_dir in sorted(per_source_dir.iterdir()):
        lt = source_dir / 'gold' / 'local_tables'
        if not lt.exists():
            continue

        for sd in bei._load_local_tables(lt):
            prepared = bei._prepare_local_entities_for_source(
                sd.source_id,
                sd.identifiers,
                sd.entity_metadata,
                bei.MERGE_UNSAFE_IDENTIFIER_TYPES,
            )
            if prepared is None:
                continue
            local_ms_edges, local_all_edges = prepared

            local_entities = local_all_edges.select(['source_id', 'local_entity_id']).unique()
            local_entities_parts.append(local_entities)

            source_rows.append(
                {
                    'source_id': int(sd.source_id),
                    'source_name': sd.source_name,
                    'records_total': int(len(local_entities)),
                    'records_with_merge_safe': int(local_ms_edges.select('local_entity_id').n_unique()) if len(local_ms_edges) else 0,
                }
            )

            if len(local_ms_edges):
                ms_parts.append(
                    local_ms_edges.select(
                        ['source_id', 'local_entity_id', 'id_type', 'id_value', 'entity_bucket', 'tax_partition']
                    ).unique()
                )

    source_df = pl.DataFrame(source_rows).unique(subset=['source_id']).sort('source_name')
    local_entities_all = pl.concat(local_entities_parts, how='diagonal_relaxed').unique()
    ms_all = pl.concat(ms_parts, how='diagonal_relaxed').unique() if ms_parts else pl.DataFrame()

    # Direct-any links (non-transitive): self-join on merge-safe key, cross-source only
    left = ms_all.rename({'source_id': 'source_a', 'local_entity_id': 'local_a'})
    right = ms_all.rename({'source_id': 'source_b', 'local_entity_id': 'local_b'})

    pairs = (
        left.join(
            right,
            on=['id_type', 'id_value', 'entity_bucket', 'tax_partition'],
            how='inner',
            suffix='_r',
        )
        .filter(
            (pl.col('source_a') < pl.col('source_b'))
            | ((pl.col('source_a') == pl.col('source_b')) & (pl.col('local_a') < pl.col('local_b')))
        )
    )

    cross_pairs = pairs.filter(pl.col('source_a') != pl.col('source_b')).select(
        [
            'source_a',
            'local_a',
            'source_b',
            'local_b',
            'id_type',
            'id_value',
            'entity_bucket',
            'tax_partition',
        ]
    )

    # Endpoint records participating in at least one cross-source direct link
    endpoints = pl.concat(
        [
            cross_pairs.select(pl.col('source_a').alias('source_id'), pl.col('local_a').alias('local_entity_id')),
            cross_pairs.select(pl.col('source_b').alias('source_id'), pl.col('local_b').alias('local_entity_id')),
        ],
        how='diagonal_relaxed',
    ).unique()

    direct_counts = (
        endpoints.group_by('source_id')
        .agg(pl.len().alias('records_with_cross_source_direct_link'))
    )

    direct_per_source = (
        source_df.join(direct_counts, on='source_id', how='left')
        .with_columns(pl.col('records_with_cross_source_direct_link').fill_null(0).cast(pl.Int64))
        .with_columns(
            [
                (pl.col('records_with_merge_safe') / pl.col('records_total') * 100).round(2).alias('merge_safe_pct'),
                (
                    pl.col('records_with_cross_source_direct_link') / pl.col('records_total') * 100
                ).round(2).alias('cross_source_direct_link_pct'),
            ]
        )
        .select(
            [
                'source_id',
                'source_name',
                'records_total',
                'records_with_merge_safe',
                'merge_safe_pct',
                'records_with_cross_source_direct_link',
                'cross_source_direct_link_pct',
            ]
        )
        .sort('source_name')
    )

    # Previous MVP (primary-only) comparison if available
    prior_csv = Path('data/_analysis/non_transitive_mvp/non_transitive_per_source_summary.csv')
    if prior_csv.exists():
        prev = pl.read_csv(prior_csv).select(
            [
                'source_id',
                pl.col('records_in_cross_source_group').alias('primary_only_cross_source_records'),
                pl.col('cross_source_group_pct').alias('primary_only_cross_source_pct'),
            ]
        )
        direct_per_source = direct_per_source.join(prev, on='source_id', how='left')

    # UF comparison
    uf_map_path = per_source_dir.parent / 'combined' / 'gold' / 'entity_record_mapping.parquet'
    uf_summary = {}
    uf_per_source = None
    if uf_map_path.exists():
        uf_map = pl.read_parquet(uf_map_path).select(['source_id', 'local_entity_id', 'entity_id'])
        uf_groups = (
            uf_map.group_by('entity_id')
            .agg([pl.len().alias('n_members'), pl.col('source_id').n_unique().alias('n_sources')])
        )
        uf_cross_entities = uf_groups.filter(pl.col('n_sources') > 1).select('entity_id')
        uf_cross_records = uf_map.join(uf_cross_entities, on='entity_id', how='inner').select(
            ['source_id', 'local_entity_id']
        )
        uf_per_source = (
            uf_cross_records.group_by('source_id')
            .agg(pl.len().alias('uf_cross_source_records'))
        )
        direct_per_source = (
            direct_per_source.join(uf_per_source, on='source_id', how='left')
            .with_columns(
                (pl.col('uf_cross_source_records') / pl.col('records_total') * 100).round(2).alias('uf_cross_source_pct')
            )
        )

        uf_summary = {
            'uf_records_in_cross_source_groups': int(len(uf_cross_records)),
            'uf_cross_source_groups': int(uf_groups.filter(pl.col('n_sources') > 1).height),
        }
    else:
        uf_summary = {
            'uf_records_in_cross_source_groups': None,
            'uf_cross_source_groups': None,
        }

    # Global metrics
    global_summary = {
        'records_total': int(len(local_entities_all)),
        'records_with_merge_safe': int(ms_all.select(['source_id', 'local_entity_id']).unique().height),
        'cross_source_direct_pairs': int(len(cross_pairs)),
        'records_with_cross_source_direct_link': int(len(endpoints)),
    }

    # Per-type contribution to direct pairs
    direct_pairs_by_type = (
        cross_pairs.group_by('id_type')
        .agg(pl.len().alias('cross_source_direct_pairs'))
        .sort('cross_source_direct_pairs', descending=True)
    )

    out_dir = Path('data/_analysis/non_transitive_mvp_direct_any')
    out_dir.mkdir(parents=True, exist_ok=True)

    cross_pairs.write_parquet(out_dir / 'direct_cross_source_pairs.parquet')
    endpoints.write_parquet(out_dir / 'records_with_cross_source_direct_link.parquet')
    direct_per_source.write_csv(out_dir / 'direct_any_per_source_summary.csv')
    direct_pairs_by_type.write_csv(out_dir / 'direct_any_pairs_by_id_type.csv')

    # Markdown summary
    md = []
    md.append('# Non-transitive direct-any merge-safe link MVP results')
    md.append('')
    md.append('- Matching rule: records are directly linked if they share any merge-safe `(id_type,id_value,entity_bucket,tax_partition)` key.')
    md.append('- No transitive closure (no Union-Find).')
    md.append('')
    md.append('## Global summary')
    md.append('')
    md.append(f"- Total records: **{global_summary['records_total']:,}**")
    md.append(f"- Records with merge-safe keys: **{global_summary['records_with_merge_safe']:,}**")
    md.append(f"- Cross-source direct pairs: **{global_summary['cross_source_direct_pairs']:,}**")
    md.append(
        f"- Records participating in >=1 cross-source direct link: **{global_summary['records_with_cross_source_direct_link']:,}**"
    )
    if uf_summary['uf_records_in_cross_source_groups'] is not None:
        md.append(
            f"- UF records in cross-source groups: **{uf_summary['uf_records_in_cross_source_groups']:,}**"
        )
        md.append(
            f"- Δ vs UF (direct-any records): **{global_summary['records_with_cross_source_direct_link'] - uf_summary['uf_records_in_cross_source_groups']:+,}**"
        )
    md.append('')
    md.append('## Per-source comparison')
    md.append('')
    hdr = [
        'source',
        'records_total',
        'merge-safe %',
        'direct-any cross-source records',
        'direct-any %',
        'primary-only cross-source records',
        'primary-only %',
        'UF cross-source records',
        'UF %',
    ]
    md.append('| ' + ' | '.join(hdr) + ' |')
    md.append('|' + '|'.join(['---'] * len(hdr)) + '|')
    for r in direct_per_source.iter_rows(named=True):
        md.append(
            '| ' + ' | '.join(
                [
                    str(r.get('source_name', '')),
                    f"{int(r.get('records_total', 0)):,}",
                    f"{float(r.get('merge_safe_pct', 0.0)):.2f}%",
                    f"{int(r.get('records_with_cross_source_direct_link', 0)):,}",
                    f"{float(r.get('cross_source_direct_link_pct', 0.0)):.2f}%",
                    f"{int(r.get('primary_only_cross_source_records', 0) or 0):,}",
                    f"{float(r.get('primary_only_cross_source_pct', 0.0) or 0.0):.2f}%",
                    f"{int(r.get('uf_cross_source_records', 0) or 0):,}",
                    f"{float(r.get('uf_cross_source_pct', 0.0) or 0.0):.2f}%",
                ]
            )
            + ' |'
        )

    md.append('')
    md.append('## Top identifier types driving direct cross-source links')
    md.append('')
    md.append('| id_type | direct cross-source pairs |')
    md.append('|---|---:|')
    for r in direct_pairs_by_type.head(15).iter_rows(named=True):
        md.append(f"| {r['id_type']} | {int(r['cross_source_direct_pairs']):,} |")

    md_path = Path('docs/non_transitive_merge_mvp_direct_any_results.md')
    md_path.write_text('\n'.join(md))

    print(f'Wrote: {md_path}')
    print(f'Wrote artifacts in: {out_dir}')


if __name__ == '__main__':
    run()
