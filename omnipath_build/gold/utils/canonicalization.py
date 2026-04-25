from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
from id_resolver.resolve import (
    CHEMICAL_ENTITY_TYPES,
    PROTEIN_ENTITY_TYPES,
    RESOLUTION_SOURCE_COLUMN,
    RESOLUTION_STATUS_COLUMN,
    RESOLVED_ID_COLUMN,
    RESOLVED_ID_TYPE_COLUMN,
    STANDARD_INCHI_TYPE,
    UNIPROT_TYPE,
)

from omnipath_build.gold.utils.canonical import canonical_priority_rank
from omnipath_build.gold.utils.cv_terms import CV_LABELS, format_cv_term
from omnipath_build.gold.utils.schema import (
    CV_TERM_ENTITY_TYPE,
    ONTOLOGY_IDENTIFIER_TERM,
)


def _raw_accession(type_value: str | None) -> str | None:
    if type_value is None:
        return None
    text = str(type_value)
    parts = text.split(':')
    if len(parts) >= 3:
        return ':'.join(parts[:2])
    return text


def _display_label(type_value: str | None) -> str:
    if type_value is None:
        return '–'
    raw_accession = _raw_accession(type_value)
    if raw_accession is not None:
        label = CV_LABELS.get(raw_accession)
        if label:
            return label
    text = str(type_value)
    parts = text.split(':')
    if len(parts) >= 3 and parts[2]:
        return parts[2]
    return text


def _format_inchi_key(inchi: str, *, strip_stereo: bool = False, strip_protonation: bool = False) -> str:
    if not inchi:
        return inchi
    stereo_layers = {'b', 't', 'm', 's'}
    protonation_layers = {'p', 'q'}
    parts = str(inchi).split('/')
    kept = [parts[0]]
    for part in parts[1:]:
        if not part:
            continue
        layer = part[0]
        if strip_stereo and layer in stereo_layers:
            continue
        if strip_protonation and layer in protonation_layers:
            continue
        kept.append(part)
    return '/'.join(kept)


def _chemical_conflict_class(resolved_ids: list[str]) -> tuple[str, str | None, str | None]:
    unique_ids = sorted({str(value) for value in resolved_ids if value is not None})
    if len(unique_ids) <= 1:
        return 'exact', None, unique_ids[0] if unique_ids else None

    combined_keys = {_format_inchi_key(value, strip_stereo=True, strip_protonation=True) for value in unique_ids}
    if len(combined_keys) != 1:
        return 'exact', None, None

    stereo_keys = {_format_inchi_key(value, strip_stereo=True, strip_protonation=False) for value in unique_ids}
    protonation_keys = {_format_inchi_key(value, strip_stereo=False, strip_protonation=True) for value in unique_ids}
    if len(stereo_keys) == 1 and len(protonation_keys) > 1:
        return 'near', 'stereo only', next(iter(combined_keys))
    if len(protonation_keys) == 1 and len(stereo_keys) > 1:
        return 'near', 'protonation only', next(iter(combined_keys))
    if len(stereo_keys) > 1 and len(protonation_keys) > 1:
        return 'near', 'stereo and protonation', next(iter(combined_keys))
    return 'near', 'mixed stereo/protonation', next(iter(combined_keys))


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return 'None.\n'

    def _cell(value: Any) -> str:
        text = '–' if value is None or str(value) == '' else str(value)
        return text.replace('|', '\\|').replace('\n', '<br>')

    header_line = '| ' + ' | '.join(headers) + ' |'
    divider_line = '| ' + ' | '.join('---' for _ in headers) + ' |'
    body_lines = ['| ' + ' | '.join(_cell(value) for value in row) + ' |' for row in rows]
    return '\n'.join([header_line, divider_line, *body_lines]) + '\n'


def _scan_protein_reference(mapping_dir: Path) -> pl.LazyFrame:
    return pl.scan_parquet(mapping_dir / 'proteins' / 'protein_reference_to_uniprot.parquet').select([
        pl.col('key_type').cast(pl.Utf8),
        pl.col('key_value').cast(pl.Utf8),
        pl.col('taxonomy_id').cast(pl.Utf8),
        pl.col('primary_uniprot').cast(pl.Utf8),
    ])


def _scan_uniprot_secondary(mapping_dir: Path) -> pl.LazyFrame:
    return pl.scan_parquet(mapping_dir / 'proteins' / 'uniprot_secondary_to_primary.parquet').select([
        pl.col('secondary_uniprot').cast(pl.Utf8),
        pl.col('primary_uniprot').cast(pl.Utf8),
    ])


def _protein_identifier_rows(preferred_uniprots: pl.DataFrame, mapping_dir: Path) -> pl.DataFrame:
    if preferred_uniprots.is_empty():
        return pl.DataFrame({
            'entity_pk': pl.Series([], dtype=pl.Int64),
            'identifier': pl.Series([], dtype=pl.Utf8),
            'identifier_type': pl.Series([], dtype=pl.Utf8),
        })

    protein_reference = _scan_protein_reference(mapping_dir).collect()
    reference_rows = (
        preferred_uniprots
        .join(protein_reference, on='primary_uniprot', how='inner')
        .filter(
            pl.col('entity_taxonomy_id').is_null()
            | (pl.col('taxonomy_id') == pl.col('entity_taxonomy_id'))
        )
        .select([
            'entity_pk',
            pl.col('key_value').alias('identifier'),
            pl.col('key_type').alias('identifier_type'),
        ])
    )

    secondary_rows = (
        preferred_uniprots
        .join(_scan_uniprot_secondary(mapping_dir).collect(), on='primary_uniprot', how='inner')
        .select([
            'entity_pk',
            pl.col('secondary_uniprot').alias('identifier'),
            pl.lit(UNIPROT_TYPE).alias('identifier_type'),
        ])
    )

    direct_primary = preferred_uniprots.select([
        'entity_pk',
        pl.col('primary_uniprot').alias('identifier'),
        pl.lit(UNIPROT_TYPE).alias('identifier_type'),
    ])

    return pl.concat([reference_rows, secondary_rows, direct_primary], how='vertical_relaxed').unique()


def _chemical_mapping_paths(mapping_dir: Path) -> list[Path]:
    chemicals_dir = mapping_dir / 'chemicals'
    return sorted(path for path in chemicals_dir.glob('*.parquet') if path.is_file())


def _repair_protein_resolutions(resolved: pl.DataFrame, mapping_dir: Path) -> pl.DataFrame:
    protein_rows = resolved.filter(
        pl.col('entity_type').is_in(list(PROTEIN_ENTITY_TYPES))
        & (pl.col(RESOLUTION_STATUS_COLUMN) == 'unresolved')
    )
    if protein_rows.is_empty():
        return resolved

    original_columns = resolved.columns
    protein_rows = protein_rows.with_row_index('_repair_idx').with_columns([
        pl.when(pl.col('id_type') == UNIPROT_TYPE)
        .then(pl.col('id').str.replace(r'-\d+$', ''))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
        .alias('_base_uniprot'),
    ])

    protein_reference = _scan_protein_reference(mapping_dir).collect()
    primary_uniprots = protein_reference.select('primary_uniprot').unique()
    uniprot_secondary = _scan_uniprot_secondary(mapping_dir).collect()

    candidate_frames: list[pl.DataFrame] = []

    direct_primary = (
        protein_rows
        .filter(pl.col('id_type') == UNIPROT_TYPE)
        .join(primary_uniprots.rename({'primary_uniprot': '_matched_primary'}), left_on='id', right_on='_matched_primary', how='inner')
        .with_columns([
            pl.col('id').alias(RESOLVED_ID_COLUMN),
            pl.lit(UNIPROT_TYPE).alias(RESOLVED_ID_TYPE_COLUMN),
            pl.lit('identity').alias(RESOLUTION_STATUS_COLUMN),
            pl.lit('uniprot_primary').alias(RESOLUTION_SOURCE_COLUMN),
            pl.lit(1).alias('_candidate_rank'),
        ])
        .select(protein_rows.columns + ['_candidate_rank'])
    )
    if not direct_primary.is_empty():
        candidate_frames.append(direct_primary)

    isoform_primary = (
        protein_rows
        .filter(pl.col('id_type') == UNIPROT_TYPE)
        .filter(pl.col('_base_uniprot').is_not_null() & (pl.col('_base_uniprot') != pl.col('id')))
        .join(primary_uniprots.rename({'primary_uniprot': '_matched_primary'}), left_on='_base_uniprot', right_on='_matched_primary', how='inner')
        .with_columns([
            pl.col('_base_uniprot').alias(RESOLVED_ID_COLUMN),
            pl.lit(UNIPROT_TYPE).alias(RESOLVED_ID_TYPE_COLUMN),
            pl.lit('mapped').alias(RESOLUTION_STATUS_COLUMN),
            pl.lit('uniprot_isoform').alias(RESOLUTION_SOURCE_COLUMN),
            pl.lit(2).alias('_candidate_rank'),
        ])
        .select(protein_rows.columns + ['_candidate_rank'])
    )
    if not isoform_primary.is_empty():
        candidate_frames.append(isoform_primary)

    secondary_rows = (
        protein_rows
        .filter(pl.col('id_type') == UNIPROT_TYPE)
        .join(uniprot_secondary.rename({'secondary_uniprot': 'id', 'primary_uniprot': '_secondary_primary'}), on='id', how='inner')
        .with_columns([
            pl.col('_secondary_primary').alias(RESOLVED_ID_COLUMN),
            pl.lit(UNIPROT_TYPE).alias(RESOLVED_ID_TYPE_COLUMN),
            pl.lit('mapped').alias(RESOLUTION_STATUS_COLUMN),
            pl.lit('uniprot_secondary').alias(RESOLUTION_SOURCE_COLUMN),
            pl.lit(3).alias('_candidate_rank'),
        ])
        .select(protein_rows.columns + ['_candidate_rank'])
    )
    if not secondary_rows.is_empty():
        candidate_frames.append(secondary_rows)

    scoped_reference = (
        protein_rows
        .join(
            protein_reference.select([
                'key_type',
                'key_value',
                'taxonomy_id',
                pl.col('primary_uniprot').alias('_scoped_primary'),
            ]),
            left_on=['id_type', 'id', 'taxonomy_id'],
            right_on=['key_type', 'key_value', 'taxonomy_id'],
            how='inner',
        )
        .with_columns([
            pl.col('_scoped_primary').alias(RESOLVED_ID_COLUMN),
            pl.lit(UNIPROT_TYPE).alias(RESOLVED_ID_TYPE_COLUMN),
            pl.lit('mapped').alias(RESOLUTION_STATUS_COLUMN),
            pl.lit('uniprot_reference').alias(RESOLUTION_SOURCE_COLUMN),
            pl.lit(4).alias('_candidate_rank'),
        ])
        .select(protein_rows.columns + ['_candidate_rank'])
    )
    if not scoped_reference.is_empty():
        candidate_frames.append(scoped_reference)

    global_reference = (
        protein_rows
        .join(
            protein_reference.select([
                'key_type',
                'key_value',
                pl.col('primary_uniprot').alias('_global_primary'),
            ]),
            left_on=['id_type', 'id'],
            right_on=['key_type', 'key_value'],
            how='inner',
        )
        .with_columns([
            pl.col('_global_primary').alias(RESOLVED_ID_COLUMN),
            pl.lit(UNIPROT_TYPE).alias(RESOLVED_ID_TYPE_COLUMN),
            pl.lit('mapped').alias(RESOLUTION_STATUS_COLUMN),
            pl.lit('uniprot_reference').alias(RESOLUTION_SOURCE_COLUMN),
            pl.lit(5).alias('_candidate_rank'),
        ])
        .select(protein_rows.columns + ['_candidate_rank'])
    )
    if not global_reference.is_empty():
        candidate_frames.append(global_reference)

    if not candidate_frames:
        return resolved

    candidates = (
        pl.concat(candidate_frames, how='vertical_relaxed')
        .sort(['_repair_idx', '_candidate_rank'])
        .unique(subset=['_repair_idx', RESOLVED_ID_COLUMN, RESOLVED_ID_TYPE_COLUMN], keep='first')
    )
    repaired_indexes = set(candidates.get_column('_repair_idx').to_list())

    unrepaired = (
        protein_rows
        .filter(~pl.col('_repair_idx').is_in(sorted(repaired_indexes)))
        .drop(['_repair_idx', '_base_uniprot'], strict=False)
    )
    repaired = candidates.drop(['_repair_idx', '_base_uniprot', '_candidate_rank'], strict=False).select(original_columns)

    return pl.concat([
        resolved.filter(~(
            pl.col('entity_type').is_in(list(PROTEIN_ENTITY_TYPES))
            & (pl.col(RESOLUTION_STATUS_COLUMN) == 'unresolved')
        )),
        unrepaired.select(original_columns),
        repaired,
    ], how='vertical_relaxed')


def _chemical_identifier_rows(preferred_inchis: pl.DataFrame, mapping_dir: Path) -> pl.DataFrame:
    if preferred_inchis.is_empty():
        return pl.DataFrame({
            'entity_pk': pl.Series([], dtype=pl.Int64),
            'identifier': pl.Series([], dtype=pl.Utf8),
            'identifier_type': pl.Series([], dtype=pl.Utf8),
        })

    rows: list[pl.DataFrame] = [
        preferred_inchis.select([
            'entity_pk',
            pl.col('standard_inchi').alias('identifier'),
            pl.lit(STANDARD_INCHI_TYPE).alias('identifier_type'),
        ])
    ]

    for path in _chemical_mapping_paths(mapping_dir):
        mapping_rows = pl.read_parquet(path).select([
            pl.col('key_type').cast(pl.Utf8),
            pl.col('key_value').cast(pl.Utf8),
            pl.col('standard_inchi').cast(pl.Utf8),
        ])
        rows.append(
            preferred_inchis
            .join(mapping_rows, on='standard_inchi', how='inner')
            .select([
                'entity_pk',
                pl.col('key_value').alias('identifier'),
                pl.col('key_type').alias('identifier_type'),
            ])
        )

    return pl.concat(rows, how='vertical_relaxed').unique()


def _empty_identifier_rows() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_id': pl.Series([], dtype=pl.Utf8),
        'entity_id_type': pl.Series([], dtype=pl.Utf8),
        'identifier': pl.Series([], dtype=pl.Utf8),
        'identifier_type': pl.Series([], dtype=pl.Utf8),
        'is_canonical': pl.Series([], dtype=pl.Boolean),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _canonical_identifier_rows(identifier_rows: pl.DataFrame) -> pl.DataFrame:
    if identifier_rows.is_empty():
        return pl.DataFrame({
            'entity_pk': pl.Series([], dtype=pl.Int64),
            'canonical_identifier': pl.Series([], dtype=pl.Utf8),
            'canonical_identifier_type': pl.Series([], dtype=pl.Utf8),
        })

    return (
        identifier_rows
        .with_columns([
            pl.col('identifier_type').map_elements(_raw_accession, return_dtype=pl.Utf8).alias('_identifier_type_id_raw'),
            pl.col('identifier_type').map_elements(lambda x: canonical_priority_rank(_raw_accession(x)), return_dtype=pl.Int64).alias('_priority_rank'),
        ])
        .sort(['entity_pk', '_priority_rank', '_identifier_type_id_raw', 'identifier'])
        .group_by('entity_pk')
        .agg([
            pl.col('identifier').first().alias('canonical_identifier'),
            pl.col('identifier_type').first().alias('canonical_identifier_type'),
        ])
    )


def _fallback_entity_id(source_name: str, local_entity_pk: int) -> str:
    return f'{source_name}:entity:{local_entity_pk}'


FALLBACK_ENTITY_ID_TYPE = 'omnipath:local_entity'

ONTOLOGY_ENTITY_TYPE_LABEL = format_cv_term(CV_TERM_ENTITY_TYPE)
ONTOLOGY_IDENTIFIER_TYPE_LABEL = format_cv_term(ONTOLOGY_IDENTIFIER_TERM)


def _entity_export_keys(
    entities: pl.DataFrame,
    canonical_rows: pl.DataFrame,
    *,
    source_name: str,
) -> pl.DataFrame:
    return (
        entities
        .select('entity_pk')
        .join(canonical_rows, on='entity_pk', how='left')
        .with_columns([
            pl.when(pl.col('canonical_identifier').is_not_null() & (pl.col('canonical_identifier') != ''))
            .then(pl.col('canonical_identifier'))
            .otherwise(
                pl.col('entity_pk').map_elements(lambda x: _fallback_entity_id(source_name, int(x)), return_dtype=pl.Utf8)
            )
            .alias('export_entity_id'),
            pl.when(pl.col('canonical_identifier_type').is_not_null() & (pl.col('canonical_identifier_type') != ''))
            .then(pl.col('canonical_identifier_type'))
            .otherwise(pl.lit(FALLBACK_ENTITY_ID_TYPE))
            .alias('export_entity_id_type'),
        ])
        .select([
            pl.col('entity_pk').alias('local_entity_pk'),
            pl.col('export_entity_id').alias('export_entity_id'),
            pl.col('export_entity_id_type').alias('export_entity_id_type'),
        ])
    )


def _resolver_source_rows(
    resolvable: pl.DataFrame,
    preferred_uniprots: pl.DataFrame,
    preferred_inchis: pl.DataFrame,
) -> pl.DataFrame:
    preferred_backbones = pl.concat([
        preferred_uniprots.select([
            'entity_pk',
            pl.col('primary_uniprot').alias('canonical_identifier'),
            pl.lit(UNIPROT_TYPE).alias('canonical_identifier_type'),
        ]),
        preferred_inchis.select([
            'entity_pk',
            pl.col('standard_inchi').alias('canonical_identifier'),
            pl.lit(STANDARD_INCHI_TYPE).alias('canonical_identifier_type'),
        ]),
    ], how='vertical_relaxed')

    if preferred_backbones.is_empty() or resolvable.is_empty():
        return pl.DataFrame({
            'entity_pk': pl.Series([], dtype=pl.Int64),
            'source_marker': pl.Series([], dtype=pl.Utf8),
        })

    return (
        resolvable
        .join(preferred_backbones, on='entity_pk', how='inner')
        .filter(
            (pl.col(RESOLVED_ID_COLUMN) == pl.col('canonical_identifier'))
            & (pl.col(RESOLVED_ID_TYPE_COLUMN) == pl.col('canonical_identifier_type'))
            & pl.col(RESOLUTION_SOURCE_COLUMN).is_not_null()
            & (pl.col(RESOLUTION_SOURCE_COLUMN) != '')
        )
        .select([
            'entity_pk',
            pl.format('resolver:{}', pl.col(RESOLUTION_SOURCE_COLUMN)).alias('source_marker'),
        ])
        .unique()
    )


def _aggregate_identifier_rows(identifier_rows: pl.DataFrame) -> pl.DataFrame:
    if identifier_rows.is_empty():
        return _empty_identifier_rows()

    return (
        identifier_rows
        .group_by(['entity_id', 'entity_id_type', 'identifier', 'identifier_type'])
        .agg([
            pl.col('is_canonical').any().alias('is_canonical'),
            pl.col('source_marker').drop_nulls().unique().sort().alias('sources'),
        ])
        .sort(['entity_id_type', 'entity_id', 'identifier_type', 'identifier'])
    )


def _build_ambiguous_entity_report(ambiguous_entities: list[dict[str, Any]]) -> str:
    backbone_conflicts = [item for item in ambiguous_entities if item.get('conflict_class') == 'exact']
    near_conflicts = [item for item in ambiguous_entities if item.get('conflict_class') == 'near']

    lines = [
        '## Conflict details',
        '',
        '### Backbone conflicts',
        '',
    ]
    exact_rows = [
        [
            str(item.get('entity_pk', '–')),
            item.get('entity_type_label', '–'),
            item.get('taxonomy_id') or '–',
            ', '.join(item.get('evidence_type_labels', [])) or '–',
            '<br>'.join(item.get('resolved_backbone_labels', [])) or '–',
        ]
        for item in backbone_conflicts
    ]
    lines.append(_markdown_table(
        ['entity_pk', 'entity_type', 'taxonomy', 'identifier types', 'resolved backbones'],
        exact_rows,
    ).rstrip())
    lines.extend([
        '',
        '### Near conflicts',
        '',
    ])
    near_rows = [
        [
            str(item.get('entity_pk', '–')),
            item.get('entity_type_label', '–'),
            item.get('taxonomy_id') or '–',
            item.get('near_conflict_subtype') or '–',
            ', '.join(item.get('evidence_type_labels', [])) or '–',
            item.get('comparison_backbone') or '–',
            '<br>'.join(item.get('resolved_backbone_labels', [])) or '–',
        ]
        for item in near_conflicts
    ]
    lines.append(_markdown_table(
        ['entity_pk', 'entity_type', 'taxonomy', 'near conflict type', 'identifier types', 'comparison backbone', 'resolved backbones'],
        near_rows,
    ).rstrip())

    evidence_rows: list[list[str]] = []
    for item in ambiguous_entities:
        for evidence in item.get('evidence_rows', []):
            evidence_rows.append([
                str(item.get('entity_pk', '–')),
                item.get('conflict_class', '–'),
                evidence.get('identifier_type_label') or '–',
                evidence.get('identifier') or '–',
                evidence.get('resolved_backbone_label') or '–',
            ])
    lines.extend([
        '',
        '### Evidence mapping',
        '',
        _markdown_table(
            ['entity_pk', 'conflict_class', 'identifier type', 'identifier', 'resolved backbone'],
            evidence_rows,
        ).rstrip(),
        '',
    ])
    return '\n'.join(lines)


def _collect_ambiguous_entities(resolved: pl.DataFrame) -> list[dict[str, Any]]:
    if resolved.is_empty():
        return []

    ambiguous_frames: list[pl.DataFrame] = []
    for entity_types, backbone_type in (
        (PROTEIN_ENTITY_TYPES, UNIPROT_TYPE),
        (CHEMICAL_ENTITY_TYPES, STANDARD_INCHI_TYPE),
    ):
        frame = (
            resolved
            .filter(pl.col('entity_type').is_in(list(entity_types)))
            .filter(pl.col(RESOLUTION_STATUS_COLUMN).is_in(['identity', 'mapped']))
            .filter(pl.col(RESOLVED_ID_TYPE_COLUMN) == backbone_type)
            .group_by(['entity_pk', 'entity_type', 'taxonomy_id'])
            .agg([
                pl.struct(['id_type', 'id']).unique().alias('_raw_pairs'),
                pl.struct(['id_type', 'id', RESOLVED_ID_COLUMN]).unique().alias('_resolution_pairs'),
                pl.col(RESOLVED_ID_COLUMN).unique().sort().alias('_resolved_ids'),
            ])
            .with_columns([
                pl.col('_resolved_ids').list.len().alias('_resolved_count'),
                pl.lit(backbone_type).alias('backbone_type'),
            ])
            .filter(pl.col('_resolved_count') > 1)
        )
        if not frame.is_empty():
            ambiguous_frames.append(frame)

    if not ambiguous_frames:
        return []

    ambiguous = pl.concat(ambiguous_frames, how='vertical_relaxed').sort(['entity_pk', 'backbone_type'])
    rows: list[dict[str, Any]] = []
    for row in ambiguous.to_dicts():
        entity_type = row.get('entity_type')
        backbone_type = row.get('backbone_type')
        resolved_ids = [str(value) for value in (row.get('_resolved_ids') or []) if value is not None]
        if backbone_type == STANDARD_INCHI_TYPE:
            conflict_class, near_conflict_subtype, comparison_backbone = _chemical_conflict_class(resolved_ids)
        else:
            conflict_class, near_conflict_subtype, comparison_backbone = 'exact', None, None

        evidence_type_labels = sorted({
            _display_label(pair.get('id_type'))
            for pair in (row.get('_raw_pairs') or [])
            if pair.get('id_type') is not None
        })

        producers_by_backbone: dict[str, set[str]] = {}
        evidence_rows: list[dict[str, str]] = []
        for pair in sorted(
            row.get('_resolution_pairs') or [],
            key=lambda item: (str(item.get('id_type') or ''), str(item.get('id') or ''), str(item.get(RESOLVED_ID_COLUMN) or '')),
        ):
            backbone_value = pair.get(RESOLVED_ID_COLUMN)
            if backbone_value is None:
                continue
            identifier_type_label = _display_label(pair.get('id_type'))
            producers_by_backbone.setdefault(str(backbone_value), set()).add(
                f"{identifier_type_label} -> {pair.get('id')}"
            )
            evidence_rows.append({
                'identifier_type_label': identifier_type_label,
                'identifier': str(pair.get('id') or '–'),
                'resolved_backbone_label': f"{_display_label(backbone_type)} -> {backbone_value}",
            })

        resolved_backbones = [
            {
                'backbone': str(value),
                'backbone_label': f"{_display_label(backbone_type)} -> {value}",
                'producers': sorted(producers_by_backbone.get(str(value), set())),
            }
            for value in resolved_ids
        ]

        rows.append({
            'entity_pk': row.get('entity_pk'),
            'entity_type': entity_type,
            'entity_type_label': _display_label(entity_type),
            'taxonomy_id': row.get('taxonomy_id'),
            'backbone_type': backbone_type,
            'backbone_type_label': _display_label(backbone_type),
            'conflict_class': conflict_class,
            'near_conflict_subtype': near_conflict_subtype,
            'comparison_backbone': comparison_backbone,
            'evidence_type_labels': evidence_type_labels,
            'evidence_rows': evidence_rows,
            'resolved_backbones': resolved_backbones,
            'resolved_backbone_labels': [item['backbone_label'] for item in resolved_backbones],
        })
    return rows


_EMPTY_SOURCES = pl.lit([], dtype=pl.List(pl.Utf8))
_IDENTIFIER_STRUCT = pl.Struct({
    'identifier': pl.Utf8,
    'identifier_type': pl.Utf8,
})
_EMPTY_IDENTIFIERS = pl.lit([], dtype=pl.List(_IDENTIFIER_STRUCT))


def _empty_entity() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_pk': pl.Series([], dtype=pl.Int64),
        'canonical_identifier': pl.Series([], dtype=pl.Utf8),
        'canonical_identifier_type': pl.Series([], dtype=pl.Utf8),
        'identifiers': pl.Series([], dtype=pl.List(_IDENTIFIER_STRUCT)),
        'entity_type': pl.Series([], dtype=pl.Utf8),
        'taxonomy_id': pl.Series([], dtype=pl.Utf8),
        'entity_attributes': pl.Series([], dtype=pl.List(pl.Struct({
            'term': pl.Utf8,
            'value': pl.Utf8,
            'unit': pl.Utf8,
        }))),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _dedup_entities(entities: pl.DataFrame) -> pl.DataFrame:
    if entities.is_empty():
        return _empty_entity()
    return (
        entities
        .with_columns([
            pl.col('entity_id').cast(pl.Utf8),
            pl.col('entity_id_type').cast(pl.Utf8),
            pl.when(pl.col('sources').is_null()).then(_EMPTY_SOURCES).otherwise(pl.col('sources')).alias('sources'),
        ])
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.col('entity_type').drop_nulls().first().alias('entity_type'),
            pl.col('entity_attributes').explode().drop_nulls().unique(maintain_order=True).alias('entity_attributes'),
            pl.col('taxonomy_id').drop_nulls().first().alias('taxonomy_id'),
            pl.col('sources').explode().drop_nulls().unique().sort().alias('sources'),
        ])
        .sort(['entity_id_type', 'entity_id'])
    )


def _reduce_entities(entities: pl.DataFrame, identifiers: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    entities_dedup = _dedup_entities(entities)

    canonical = (
        identifiers
        .filter(pl.col('is_canonical'))
        .sort(['entity_id_type', 'entity_id', 'identifier_type', 'identifier'])
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.col('identifier').first().alias('canonical_identifier'),
            pl.col('identifier_type').first().alias('canonical_identifier_type'),
        ])
        if not identifiers.is_empty() else
        pl.DataFrame({
            'entity_id': pl.Series([], dtype=pl.Utf8),
            'entity_id_type': pl.Series([], dtype=pl.Utf8),
            'canonical_identifier': pl.Series([], dtype=pl.Utf8),
            'canonical_identifier_type': pl.Series([], dtype=pl.Utf8),
        })
    )

    folded = (
        identifiers
        .filter(~pl.col('is_canonical'))
        .sort(['entity_id_type', 'entity_id', 'identifier_type', 'identifier'])
        .group_by(['entity_id', 'entity_id_type'])
        .agg([
            pl.struct(['identifier', 'identifier_type']).alias('identifiers'),
        ])
        if not identifiers.is_empty() else
        pl.DataFrame({
            'entity_id': pl.Series([], dtype=pl.Utf8),
            'entity_id_type': pl.Series([], dtype=pl.Utf8),
            'identifiers': pl.Series([], dtype=pl.List(_IDENTIFIER_STRUCT)),
        })
    )

    reduced = (
        entities_dedup
        .join(canonical, on=['entity_id', 'entity_id_type'], how='left')
        .join(folded, on=['entity_id', 'entity_id_type'], how='left')
        .with_row_index('entity_pk', offset=1)
        .with_columns([
            pl.col('entity_pk').cast(pl.Int64),
            pl.coalesce([pl.col('canonical_identifier'), pl.col('entity_id')]).cast(pl.Utf8).alias('canonical_identifier'),
            pl.coalesce([pl.col('canonical_identifier_type'), pl.col('entity_id_type')]).cast(pl.Utf8).alias('canonical_identifier_type'),
            pl.when(pl.col('identifiers').is_null()).then(_EMPTY_IDENTIFIERS).otherwise(pl.col('identifiers')).alias('identifiers'),
        ])
        .select([
            'entity_pk',
            'canonical_identifier',
            'canonical_identifier_type',
            'identifiers',
            'entity_type',
            'taxonomy_id',
            'entity_attributes',
            'sources',
        ])
    )

    entity_key_map = (
        entities_dedup
        .sort(['entity_id_type', 'entity_id'])
        .with_row_index('entity_pk', offset=1)
        .select([
            pl.col('entity_id').cast(pl.Utf8),
            pl.col('entity_id_type').cast(pl.Utf8),
            pl.col('entity_pk').cast(pl.Int64),
        ])
    )
    return reduced, entity_key_map
