#!/usr/bin/env python3
"""Build global tables from local tables using deterministic identity snapshots (IEM v2)."""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

try:
    from omnipath_build.search_builder.schema import CV_TERM_ACCESSION_TYPE
    from omnipath_build.utils.ontology_labels import get_default_resolver
except ImportError:
    import sys

    sys.path.append(str(Path(__file__).parent.parent.parent))
    from omnipath_build.search_builder.schema import CV_TERM_ACCESSION_TYPE
    from omnipath_build.utils.ontology_labels import get_default_resolver

__all__ = ['build_global_tables']

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _empty_key_table(cols: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame({k: pl.Series([], dtype=v) for k, v in cols.items()})


def build_global_tables(
    local_tables_dir: str | Path,
    record_identity_snapshot_file: str | Path,
    entity_identifier_snapshot_file: str | Path,
    instance_identity_snapshot_file: str | Path,
    output_dir: str | Path,
):
    """Build global tables from local tables and IEM v2 snapshots."""
    local_tables_dir = Path(local_tables_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info('=' * 80)
    logger.info('Loading identity snapshots')
    logger.info('=' * 80)

    record_identity = pl.read_parquet(record_identity_snapshot_file)
    entity_identifiers = pl.read_parquet(entity_identifier_snapshot_file)
    instance_identity = pl.read_parquet(instance_identity_snapshot_file)

    logger.info('Loaded record_identity_snapshot: %s rows', f'{len(record_identity):,}')
    logger.info('Loaded entity_identifier_snapshot: %s rows', f'{len(entity_identifiers):,}')
    logger.info('Loaded instance_identity_snapshot: %s rows', f'{len(instance_identity):,}')

    # =====================================================================
    # 1) entity
    # =====================================================================
    logger.info('\n%s', '=' * 80)
    logger.info('Processing entity table')
    logger.info('%s', '=' * 80)

    entity_files = sorted(
        f
        for f in local_tables_dir.rglob('local_entity_*.parquet')
        if 'annotation' not in f.name and 'identifier' not in f.name and 'membership' not in f.name and 'instance' not in f.name
    )

    entity_parts: list[pl.DataFrame] = []
    for f in entity_files:
        df = pl.read_parquet(f)
        if len(df) == 0:
            continue
        logger.info('  %s: %s rows', f.name, f'{len(df):,}')
        entity_parts.append(
            df.join(
                record_identity.select(['source_ref', 'local_entity_id', 'entity_key']),
                on=['source_ref', 'local_entity_id'],
                how='left',
            )
        )

    if entity_parts:
        entities_combined = pl.concat(entity_parts, how='diagonal_relaxed')
        entities_output = (
            entities_combined
            .group_by('entity_key')
            .agg(pl.col('entity_type').first().alias('entity_type'))
            .sort('entity_key')
        )
    else:
        logger.warning('No local_entity files found')
        entities_output = _empty_key_table({'entity_key': pl.Utf8, 'entity_type': pl.Utf8})

    entities_output.write_parquet(output_dir / 'entity.parquet')
    logger.info('✅ entity: %s rows', f'{len(entities_output):,}')

    # =====================================================================
    # 2) entity_identifier
    # =====================================================================
    logger.info('\n%s', '=' * 80)
    logger.info('Processing entity_identifier table')
    logger.info('%s', '=' * 80)

    # entity_identifiers already has deterministic IDs from IEM v2
    entity_identifiers_output = entity_identifiers
    entity_identifiers_output.write_parquet(output_dir / 'entity_identifier.parquet')
    logger.info('✅ entity_identifier: %s rows', f'{len(entity_identifiers_output):,}')

    # =====================================================================
    # 3) entity_instance
    # =====================================================================
    logger.info('\n%s', '=' * 80)
    logger.info('Processing entity_instance table')
    logger.info('%s', '=' * 80)

    if len(instance_identity) > 0:
        entity_instances_output = (
            instance_identity
            .select([
                pl.col('instance_key').alias('id'),
                pl.col('entity_key'),
                pl.col('source_ref'),
            ])
            .sort('id')
        )
    else:
        entity_instances_output = _empty_key_table({'id': pl.Utf8, 'entity_key': pl.Utf8, 'source_ref': pl.Utf8})

    entity_instances_output.write_parquet(output_dir / 'entity_instance.parquet')
    logger.info('✅ entity_instance: %s rows', f'{len(entity_instances_output):,}')

    # =====================================================================
    # 4) entity_annotation
    # =====================================================================
    logger.info('\n%s', '=' * 80)
    logger.info('Processing entity_annotation table')
    logger.info('%s', '=' * 80)

    annot_files = sorted(local_tables_dir.rglob('local_entity_annotation_*.parquet'))
    annot_parts: list[pl.DataFrame] = []

    for f in annot_files:
        df = pl.read_parquet(f)
        if len(df) == 0:
            continue
        logger.info('  %s: %s rows', f.name, f'{len(df):,}')

        mapped = df.join(
            instance_identity.select(['source_ref', 'local_entity_instance_id', 'instance_key']),
            on=['source_ref', 'local_entity_instance_id'],
            how='left',
        )

        if 'cv_term_accession' not in mapped.columns:
            mapped = mapped.with_columns(pl.lit(None, dtype=pl.Utf8).alias('cv_term_accession'))
        if 'unit_accession' not in mapped.columns:
            mapped = mapped.with_columns(pl.lit(None, dtype=pl.Utf8).alias('unit_accession'))

        annot_parts.append(mapped)

    if annot_parts:
        ann = pl.concat(annot_parts, how='diagonal_relaxed')
        entity_annots_output = (
            ann
            .drop([c for c in ['local_entity_instance_id', 'local_entity_annotation_id'] if c in ann.columns])
            .rename({'instance_key': 'instance_id'})
            .sort(['instance_id', 'cv_term_accession', 'source_ref'])
            .with_row_index('id', offset=1)
        )
    else:
        entity_annots_output = _empty_key_table(
            {
                'id': pl.Int64,
                'instance_id': pl.Utf8,
                'cv_term_accession': pl.Utf8,
                'value': pl.Utf8,
                'unit_accession': pl.Utf8,
                'source_ref': pl.Utf8,
            }
        )

    entity_annots_output.write_parquet(output_dir / 'entity_annotation.parquet')
    logger.info('✅ entity_annotation: %s rows', f'{len(entity_annots_output):,}')

    # =====================================================================
    # 5) membership
    # =====================================================================
    logger.info('\n%s', '=' * 80)
    logger.info('Processing membership table')
    logger.info('%s', '=' * 80)

    membership_files = sorted(local_tables_dir.rglob('local_membership_*.parquet'))
    membership_parts: list[pl.DataFrame] = []

    for f in membership_files:
        df = pl.read_parquet(f)
        if len(df) == 0:
            continue
        logger.info('  %s: %s rows', f.name, f'{len(df):,}')

        g = df

        if 'parent_entity_id' in g.columns:
            g = (
                g.join(
                    record_identity.select([
                        'source_ref',
                        pl.col('local_entity_id').alias('parent_entity_id'),
                        pl.col('entity_key').alias('parent_entity_key'),
                    ]),
                    on=['source_ref', 'parent_entity_id'],
                    how='left',
                )
                .drop('parent_entity_id')
                .rename({'parent_entity_key': 'parent_entity_id'})
            )
        else:
            g = g.with_columns(pl.lit(None, dtype=pl.Utf8).alias('parent_entity_id'))

        if 'member_entity_id' in g.columns:
            g = (
                g.join(
                    record_identity.select([
                        'source_ref',
                        pl.col('local_entity_id').alias('member_entity_id'),
                        pl.col('entity_key').alias('member_entity_key'),
                    ]),
                    on=['source_ref', 'member_entity_id'],
                    how='left',
                )
                .drop('member_entity_id')
                .rename({'member_entity_key': 'member_entity_id'})
            )
        else:
            g = g.with_columns(pl.lit(None, dtype=pl.Utf8).alias('member_entity_id'))

        if 'parent_instance_id' in g.columns:
            g = (
                g.join(
                    instance_identity.select([
                        'source_ref',
                        pl.col('local_entity_instance_id').alias('parent_instance_id'),
                        pl.col('instance_key').alias('parent_instance_key'),
                    ]),
                    on=['source_ref', 'parent_instance_id'],
                    how='left',
                )
                .drop('parent_instance_id')
                .rename({'parent_instance_key': 'parent_instance_id'})
            )
        else:
            g = g.with_columns(pl.lit(None, dtype=pl.Utf8).alias('parent_instance_id'))

        if 'member_instance_id' in g.columns:
            g = (
                g.join(
                    instance_identity.select([
                        'source_ref',
                        pl.col('local_entity_instance_id').alias('member_instance_id'),
                        pl.col('instance_key').alias('member_instance_key'),
                    ]),
                    on=['source_ref', 'member_instance_id'],
                    how='left',
                )
                .drop('member_instance_id')
                .rename({'member_instance_key': 'member_instance_id'})
            )
        else:
            g = g.with_columns(pl.lit(None, dtype=pl.Utf8).alias('member_instance_id'))

        membership_parts.append(g)

    if membership_parts:
        memberships_combined = pl.concat(membership_parts, how='diagonal_relaxed')
        drop_cols = [c for c in ['local_membership_id'] if c in memberships_combined.columns]
        memberships_output = (
            memberships_combined
            .drop(drop_cols)
            .sort(['parent_entity_id', 'parent_instance_id', 'member_entity_id', 'member_instance_id', 'source_ref'])
            .with_row_index('id', offset=1)
            .select(['id', 'parent_entity_id', 'parent_instance_id', 'member_entity_id', 'member_instance_id', 'source_ref'])
        )
    else:
        memberships_output = _empty_key_table(
            {
                'id': pl.Int64,
                'parent_entity_id': pl.Utf8,
                'parent_instance_id': pl.Utf8,
                'member_entity_id': pl.Utf8,
                'member_instance_id': pl.Utf8,
                'source_ref': pl.Utf8,
            }
        )

    memberships_output.write_parquet(output_dir / 'membership.parquet')
    logger.info('✅ membership: %s rows', f'{len(memberships_output):,}')

    # =====================================================================
    # 6) cv_terms
    # =====================================================================
    logger.info('\n%s', '=' * 80)
    logger.info('Building CV term label mappings')
    logger.info('%s', '=' * 80)

    accessions: set[str] = set()

    if not entities_output.is_empty() and 'entity_type' in entities_output.columns:
        accessions.update(entities_output['entity_type'].drop_nulls().unique().to_list())

    if not entity_identifiers_output.is_empty() and 'type_id' in entity_identifiers_output.columns:
        accessions.update(entity_identifiers_output['type_id'].drop_nulls().unique().to_list())

    if not entity_annots_output.is_empty():
        if 'cv_term_accession' in entity_annots_output.columns:
            accessions.update(entity_annots_output['cv_term_accession'].drop_nulls().unique().to_list())
        if 'unit_accession' in entity_annots_output.columns:
            accessions.update(entity_annots_output['unit_accession'].drop_nulls().unique().to_list())
        if {'cv_term_accession', 'value'}.issubset(entity_annots_output.columns):
            cv_value_accessions = (
                entity_annots_output
                .filter(pl.col('cv_term_accession') == CV_TERM_ACCESSION_TYPE)
                .select(pl.col('value').drop_nulls().unique())
                .to_series()
                .to_list()
            )
            accessions.update(cv_value_accessions)

    logger.info('Resolving labels for %s unique CV terms...', len(accessions))
    resolver = get_default_resolver()
    label_map = resolver.resolve_bulk(list(accessions))

    cv_terms = pl.DataFrame(
        [{'accession': acc, 'label': lbl} for acc, lbl in label_map.items()],
        schema={'accession': pl.Utf8, 'label': pl.Utf8},
    )
    cv_terms.write_parquet(output_dir / 'cv_terms.parquet')
    logger.info('✅ cv_terms: %s rows', f'{len(cv_terms):,}')

    logger.info('\n%s', '=' * 80)
    logger.info('🎉 Global tables complete!')
    logger.info('%s', '=' * 80)
