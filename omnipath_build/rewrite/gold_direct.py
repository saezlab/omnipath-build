from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import duckdb
import polars as pl

from omnipath_build.gold.build_entities import (
    GoldPartitionConfig,
    _add_entity_part_columns,
    _add_fingerprint_part_columns,
    _add_occurrence_part_columns,
    _canonicalize_entities,
)
from omnipath_build.gold.build_relations import _merge_attribute_lists
from omnipath_build.gold.utils.canonicalization import _reduce_entities
from omnipath_build.gold.utils.cv_terms import format_cv_term
from omnipath_build.gold.utils.entity_extraction import (
    collect_attributes,
    compute_entity_fingerprint,
    extract_ontology_entity_description,
)
from omnipath_build.gold.utils.keys import compute_entity_key, compute_relation_key
from omnipath_build.gold.utils.schema import (
    ASSOCIATION_CATEGORY,
    ASSOCIATION_PREDICATE,
    CV_TERM_ENTITY_TYPE,
    DEFINITION_HINT_TERMS,
    EVIDENCE_IDENTIFIER_TERMS,
    INTERACTION_LIKE_TYPES,
    NAME_IDENTIFIER_TERMS,
    ONTOLOGY_IDENTIFIER_TERM,
    PredicateRule,
    SYNONYM_IDENTIFIER_TERMS,
    TAXONOMY_IDENTIFIER_TERM,
    AnnotationContext,
    classify_annotation,
    is_cv_term_accession,
    materialize_ontology_object,
    order_interaction_participants,
    predicate_for_interaction,
    predicate_for_membership,
    string_or_none,
)
from omnipath_build.gold.utils.table_schema import ENTITY_EVIDENCE_SCHEMA
from omnipath_build.rewrite.silver import SILVER_TABLE_NAMES, SILVER_TABLE_PREFIX


_ATTR_DTYPE = pl.List(pl.Struct({
    'term': pl.Utf8,
    'value': pl.Utf8,
    'unit': pl.Utf8,
}))
_IDENTIFIER_DTYPE = pl.List(pl.Struct({
    'type': pl.Utf8,
    'value': pl.Utf8,
}))
_RAW_SCOPE_CHUNK_SIZE = 50_000


@dataclass(frozen=True)
class DirectGoldBuildResult:
    changed: bool
    rows_by_table: dict[str, int]


@dataclass(frozen=True)
class SourceGoldScope:
    raw_record_ids: set[int]
    occurrence_ids: set[str]

    @property
    def is_empty(self) -> bool:
        return not self.raw_record_ids and not self.occurrence_ids


@dataclass(frozen=True)
class ScopeApplyResult:
    frames: dict[str, pl.DataFrame]
    entity_keys: set[str]
    relation_keys: set[str]
    changed: bool


def build_gold_source_duckdb(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    mapping_dir: str | Path,
    cfg: GoldPartitionConfig,
) -> DirectGoldBuildResult:
    """Apply the current source scope from rewrite silver into source-gold state."""
    has_current_gold = _current_gold_state_exists(con)
    scope = _load_source_scope(con, source=source, bootstrap=not has_current_gold)
    if scope.is_empty:
        _clear_scope_tables(con)
        return DirectGoldBuildResult(
            changed=False,
            rows_by_table=_current_gold_row_counts(con),
        )

    scoped_frames = _build_scoped_gold_frames(
        con=con,
        scope=scope,
        source=source,
        mapping_dir=mapping_dir,
        cfg=cfg,
    )
    applied = (
        _apply_scope_to_current_gold(
            con,
            changed_frames=scoped_frames,
            scope=scope,
        )
        if has_current_gold
        else ScopeApplyResult(
            frames=scoped_frames,
            entity_keys=_string_set(scoped_frames['gold_entity'], 'entity_key'),
            relation_keys=_string_set(scoped_frames['gold_entity_relation'], 'relation_key'),
            changed=True,
        )
    )

    if applied.changed:
        _replace_gold_tables(con, applied.frames)
        _refresh_scope_tables(
            con,
            entity_keys=applied.entity_keys,
            relation_keys=applied.relation_keys,
        )
    else:
        _clear_scope_tables(con)
    return DirectGoldBuildResult(
        changed=applied.changed,
        rows_by_table=_frame_row_counts(applied.frames),
    )


def _build_scoped_gold_frames(
    *,
    con: duckdb.DuckDBPyConnection,
    scope: SourceGoldScope,
    source: str,
    mapping_dir: str | Path,
    cfg: GoldPartitionConfig,
) -> dict[str, pl.DataFrame]:
    temp_entities, temp_identifiers, occurrence_fingerprint_map = _build_scoped_entity_candidates(
        con=con,
        source=source,
        scope=scope,
    )
    if temp_entities.is_empty():
        return _empty_gold_frames()

    return _build_gold_frames_from_candidates(
        con=con,
        source=source,
        scope=scope,
        temp_entities=temp_entities,
        temp_identifiers=temp_identifiers,
        occurrence_fingerprint_map=occurrence_fingerprint_map,
        mapping_dir=mapping_dir,
        cfg=cfg,
    )


def _build_scoped_entity_candidates(
    *,
    con: duckdb.DuckDBPyConnection,
    source: str,
    scope: SourceGoldScope,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    entity_frames: list[pl.DataFrame] = []
    identifier_frames: list[pl.DataFrame] = []
    occurrence_map_frames: list[pl.DataFrame] = []
    entity_pk_offset = 0
    for raw_record_ids in _iter_raw_record_id_chunks(scope.raw_record_ids):
        silver = _load_silver_frames(con, source=source, raw_record_ids=raw_record_ids)
        temp_entities, temp_identifiers, occurrence_map, _entity_occurrences = (
            _extract_from_silver_frames(silver, source)
        )
        if temp_entities.is_empty():
            continue
        temp_entities, temp_identifiers = _offset_temp_entity_pks(
            temp_entities,
            temp_identifiers,
            offset=entity_pk_offset,
        )
        entity_pk_offset = int(temp_entities.get_column('entity_pk').max() or entity_pk_offset)
        entity_frames.append(temp_entities)
        if not temp_identifiers.is_empty():
            identifier_frames.append(temp_identifiers)
        if not occurrence_map.is_empty():
            occurrence_map_frames.append(occurrence_map)

    if not entity_frames:
        return _empty_temp_entities(), _empty_temp_identifiers(), _empty_occurrence_map()
    temp_entities = pl.concat(entity_frames, how='vertical_relaxed')
    temp_identifiers = _concat_or_empty(identifier_frames, _empty_temp_identifiers())
    temp_entities, temp_identifiers = _dedupe_temp_entities(temp_entities, temp_identifiers)
    return (
        temp_entities,
        temp_identifiers,
        _concat_or_empty(occurrence_map_frames, _empty_occurrence_map()).unique(),
    )


def _build_gold_frames_from_candidates(
    *,
    con: duckdb.DuckDBPyConnection,
    source: str,
    scope: SourceGoldScope,
    temp_entities: pl.DataFrame,
    temp_identifiers: pl.DataFrame,
    occurrence_fingerprint_map: pl.DataFrame,
    mapping_dir: str | Path,
    cfg: GoldPartitionConfig,
) -> dict[str, pl.DataFrame]:
    canonicalized_entities, canonical_identifiers, _summary, _ambiguous = _canonicalize_entities(
        temp_entities,
        temp_identifiers,
        Path(mapping_dir),
        source,
    )
    final_entities, entity_key_map = _reduce_entities(canonicalized_entities, canonical_identifiers)
    final_entities = _with_entity_keys(final_entities)

    fingerprint_map = (
        temp_entities.select(['entity_pk', '_fingerprint'])
        .join(
            canonicalized_entities.select(['entity_pk', 'entity_id', 'entity_id_type']),
            on='entity_pk',
            how='inner',
        )
        .join(
            entity_key_map.rename({'entity_pk': 'final_entity_pk'}),
            on=['entity_id', 'entity_id_type'],
            how='inner',
        )
        .select(['_fingerprint', 'final_entity_pk'])
        .rename({'final_entity_pk': 'entity_pk'})
        .unique()
    )
    occurrence_map = (
        occurrence_fingerprint_map
        .join(fingerprint_map, on='_fingerprint', how='inner')
        .select(['occurrence_id', '_fingerprint', 'entity_pk'])
        .unique()
    )
    entity_evidence_frames: list[pl.DataFrame] = []
    for raw_record_ids in _iter_raw_record_id_chunks(scope.raw_record_ids):
        silver = _load_silver_frames(con, source=source, raw_record_ids=raw_record_ids)
        chunk_evidence = _build_entity_evidence_from_frames(
            silver=silver,
            source=source,
            occurrence_map=occurrence_map,
            fingerprint_map=fingerprint_map,
            final_entities=final_entities,
        )
        if not chunk_evidence.is_empty():
            entity_evidence_frames.append(chunk_evidence)
    entity_evidence = _concat_or_empty(entity_evidence_frames, _empty_entity_evidence())
    entity_evidence = _add_entity_part_columns(entity_evidence, cfg)

    entity_registry = _entity_registry_from_evidence(entity_evidence)
    gold_entity = _gold_entity_from_evidence(entity_evidence, entity_registry)
    gold_entity_evidence = _gold_entity_evidence(entity_evidence, entity_registry)
    gold_occurrence_map = _gold_occurrence_map(occurrence_map, final_entities, entity_registry, cfg)
    gold_entity_map = _gold_entity_map(fingerprint_map, final_entities, entity_registry, cfg)

    relation_evidence_frames: list[pl.DataFrame] = []
    for raw_record_ids in _iter_raw_record_id_chunks(scope.raw_record_ids):
        silver = _load_silver_frames(con, source=source, raw_record_ids=raw_record_ids)
        relation_evidence = _build_relation_evidence_from_frames(
            silver=silver,
            source=source,
            entity_map=gold_entity_map,
            occurrence_map=gold_occurrence_map,
        )
        if not relation_evidence.is_empty():
            relation_evidence_frames.append(relation_evidence)
    relation_evidence_raw = _concat_or_empty(relation_evidence_frames, _empty_relation_evidence_raw())
    gold_relation, gold_relation_evidence, relation_registry = _finalize_relations(
        relation_evidence_raw,
        cfg=cfg,
    )

    return {
        'gold_entity': gold_entity,
        'gold_entity_evidence': gold_entity_evidence,
        'gold_entity_map': gold_entity_map,
        'gold_entity_occurrence_map': gold_occurrence_map,
        'gold_entity_relation': gold_relation,
        'gold_entity_relation_evidence': gold_relation_evidence,
        'gold_entity_key_registry': entity_registry,
        'gold_relation_key_registry': relation_registry,
    }


def _frame_row_counts(frames: dict[str, pl.DataFrame]) -> dict[str, int]:
    return {
        'entity': frames['gold_entity'].height,
        'entity_evidence': frames['gold_entity_evidence'].height,
        'entity_map': frames['gold_entity_map'].height,
        'entity_occurrence_map': frames['gold_entity_occurrence_map'].height,
        'entity_relation': frames['gold_entity_relation'].height,
        'entity_relation_evidence': frames['gold_entity_relation_evidence'].height,
    }


def _current_gold_row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        'entity': _table_count(con, 'gold_entity'),
        'entity_evidence': _table_count(con, 'gold_entity_evidence'),
        'entity_map': _table_count(con, 'gold_entity_map'),
        'entity_occurrence_map': _table_count(con, 'gold_entity_occurrence_map'),
        'entity_relation': _table_count(con, 'gold_entity_relation'),
        'entity_relation_evidence': _table_count(con, 'gold_entity_relation_evidence'),
    }


def _load_silver_frames(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    raw_record_ids: set[int] | None = None,
) -> dict[str, pl.DataFrame]:
    frames: dict[str, pl.DataFrame] = {}
    scope_relation = None
    if raw_record_ids is not None:
        scope_relation = '_rewrite_gold_scope_raw_ids'
        con.register(
            scope_relation,
            pl.DataFrame({'raw_record_id': sorted(raw_record_ids)}).to_arrow(),
        )
    try:
        for name in SILVER_TABLE_NAMES:
            table = _quote_identifier(SILVER_TABLE_PREFIX + name)
            if scope_relation is None:
                sql = f'select * exclude(source_run_id) from {table} where source = ?'
            else:
                sql = f'''
                    select * exclude(source_run_id)
                    from {table}
                    where source = ?
                      and _raw_record_id in (select raw_record_id from {scope_relation})
                '''
            arrow = con.execute(sql, [source]).fetch_arrow_table()
            frames[name] = pl.from_arrow(arrow)
    finally:
        if scope_relation is not None:
            con.unregister(scope_relation)
    return frames


def _iter_raw_record_id_chunks(raw_record_ids: set[int]) -> Iterable[set[int]]:
    sorted_ids = sorted(raw_record_ids)
    for start in range(0, len(sorted_ids), _RAW_SCOPE_CHUNK_SIZE):
        yield set(sorted_ids[start:start + _RAW_SCOPE_CHUNK_SIZE])


def _offset_temp_entity_pks(
    temp_entities: pl.DataFrame,
    temp_identifiers: pl.DataFrame,
    *,
    offset: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if offset <= 0:
        return temp_entities, temp_identifiers
    temp_entities = temp_entities.with_columns((pl.col('entity_pk') + offset).alias('entity_pk'))
    if not temp_identifiers.is_empty():
        temp_identifiers = temp_identifiers.with_columns((pl.col('entity_pk') + offset).alias('entity_pk'))
    return temp_entities, temp_identifiers


def _dedupe_temp_entities(
    temp_entities: pl.DataFrame,
    temp_identifiers: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if temp_entities.is_empty():
        return temp_entities, temp_identifiers
    temp_entities = temp_entities.unique(subset=['_fingerprint'], maintain_order=True)
    if temp_identifiers.is_empty():
        return temp_entities, temp_identifiers
    kept_entity_pks = temp_entities.select('entity_pk')
    temp_identifiers = temp_identifiers.join(kept_entity_pks, on='entity_pk', how='inner')
    return temp_entities, temp_identifiers


def _concat_or_empty(frames: list[pl.DataFrame], empty: pl.DataFrame) -> pl.DataFrame:
    return pl.concat(frames, how='vertical_relaxed') if frames else empty


def _load_source_scope(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    bootstrap: bool,
) -> SourceGoldScope:
    raw_record_ids: set[int] = set()
    occurrence_ids: set[str] = set()
    if _table_exists(con, 'source_run_scope_raw_record'):
        raw_rows = con.execute('''
            select distinct raw_record_id
            from source_run_scope_raw_record
            where raw_record_id is not null
        ''').fetchall()
        raw_record_ids.update(int(row[0]) for row in raw_rows)
    if _table_exists(con, 'source_run_scope_occurrence'):
        occurrence_rows = con.execute('''
            select distinct raw_record_id, occurrence_id
            from source_run_scope_occurrence
        ''').fetchall()
        raw_record_ids.update(int(row[0]) for row in occurrence_rows if row[0] is not None)
        occurrence_ids.update(str(row[1]) for row in occurrence_rows if row[1] is not None)
    if bootstrap and not raw_record_ids:
        rows = con.execute('''
            select distinct _raw_record_id, occurrence_id
            from silver_entity_occurrence
            where source = ?
              and _raw_record_id is not null
        ''', [source]).fetchall()
        raw_record_ids.update(int(row[0]) for row in rows)
        occurrence_ids.update(str(row[1]) for row in rows if row[1] is not None)
    return SourceGoldScope(raw_record_ids=raw_record_ids, occurrence_ids=occurrence_ids)


def _extract_from_silver_frames(
    silver: dict[str, pl.DataFrame],
    source: str,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, int]:
    occ = silver['entity_occurrence'].lazy()
    ids_raw = silver['entity_identifier'].lazy()
    anns_raw = silver['entity_annotation'].lazy()
    memberships = silver['membership'].lazy()

    occurrence_count = occ.select(pl.len()).collect().item()
    if occurrence_count == 0:
        return _empty_temp_entities(), _empty_temp_identifiers(), _empty_occurrence_map(), 0

    ids_fmt = (
        ids_raw
        .filter(pl.col('identifier').is_not_null() & (pl.col('identifier') != ''))
        .select([
            'occurrence_id',
            pl.col('identifier_type').map_elements(format_cv_term, return_dtype=pl.Utf8).alias('type'),
            pl.col('identifier').alias('value'),
        ])
        .collect()
    )
    anns = (
        anns_raw
        .select(['occurrence_id', 'term', 'value', pl.col('unit').alias('units')])
        .with_row_index('_annotation_index')
        .with_columns(pl.col('_annotation_index').cum_count().over('occurrence_id').alias('_annotation_pos'))
        .collect()
    )
    occ_df = (
        occ
        .select([
            'occurrence_id',
            'entity_type',
            pl.col('entity_type').map_elements(format_cv_term, return_dtype=pl.Utf8).alias('entity_type_formatted'),
        ])
        .with_row_index('_occurrence_index')
        .collect()
    )
    membership_df = memberships.select(['parent_occurrence_id', 'member_occurrence_id']).collect()

    has_membership = (
        membership_df.select(pl.col('parent_occurrence_id').alias('occurrence_id')).unique()
        if not membership_df.is_empty()
        else pl.DataFrame({'occurrence_id': pl.Series([], dtype=pl.Utf8)})
    )
    has_ontology_backing = (
        ids_raw
        .filter(
            (pl.col('identifier_type') == ONTOLOGY_IDENTIFIER_TERM)
            & pl.col('identifier').is_not_null()
            & (pl.col('identifier') != '')
        )
        .select('occurrence_id')
        .unique()
        .collect()
    )
    occ_class = (
        occ_df
        .join(has_membership.with_columns(pl.lit(True).alias('has_membership')), on='occurrence_id', how='left')
        .join(has_ontology_backing.with_columns(pl.lit(True).alias('has_ontology_backing')), on='occurrence_id', how='left')
        .with_columns([
            pl.col('has_membership').fill_null(False),
            pl.col('has_ontology_backing').fill_null(False),
        ])
        .with_columns(
            pl.when(pl.col('entity_type').is_in(list(INTERACTION_LIKE_TYPES)) & pl.col('has_membership'))
            .then(pl.lit('interaction_relation'))
            .when(pl.col('entity_type') == CV_TERM_ENTITY_TYPE)
            .then(pl.lit('ontology_term_only'))
            .when(pl.col('has_ontology_backing'))
            .then(pl.lit('entity_with_ontology_backing'))
            .when(pl.col('has_membership'))
            .then(pl.lit('membership_relation'))
            .when(pl.col('entity_type').is_not_null())
            .then(pl.lit('entity_only'))
            .otherwise(pl.lit('ignored'))
            .alias('record_class')
        )
    )
    identifiers_by_occ = ids_fmt.group_by('occurrence_id', maintain_order=True).agg(
        pl.struct(['type', 'value']).alias('identifiers')
    )
    direct_taxonomy = _direct_taxonomy(occ_df, ids_fmt, anns)
    member_taxonomy = _member_taxonomy(membership_df, direct_taxonomy)
    pure_ontology = _pure_ontology(anns, occ_df)
    attributes_by_occ = _attributes_by_occ(anns)

    materialized_occurrences = (
        occ_class
        .filter(~pl.col('record_class').is_in(['ignored', 'interaction_relation']))
        .join(identifiers_by_occ, on='occurrence_id', how='left')
        .join(direct_taxonomy, on='occurrence_id', how='left')
        .join(member_taxonomy, on='occurrence_id', how='left')
        .join(attributes_by_occ, on='occurrence_id', how='left')
        .with_columns([
            pl.coalesce(['direct_taxonomy_id', 'member_taxonomy_id']).alias('taxonomy_id'),
            pl.col('identifiers').cast(_IDENTIFIER_DTYPE),
            pl.col('entity_attributes').cast(_ATTR_DTYPE),
        ])
        .select([
            'occurrence_id',
            (pl.col('_occurrence_index') * 10_000).alias('_candidate_order'),
            pl.col('entity_type_formatted').alias('entity_type'),
            'taxonomy_id',
            'entity_attributes',
            'identifiers',
        ])
        .with_columns(
            pl.struct(['entity_type', 'identifiers'])
            .map_elements(_fingerprint_from_row, return_dtype=pl.Utf8)
            .alias('_fingerprint')
        )
    )
    ontology_annotation_entities = (
        pure_ontology
        .group_by('term_id', maintain_order=True)
        .agg(pl.col('_candidate_order').min().alias('_candidate_order'))
        .sort('_candidate_order')
        .with_columns([
            pl.lit(format_cv_term(CV_TERM_ENTITY_TYPE)).alias('entity_type'),
            pl.lit(None, dtype=pl.Utf8).alias('taxonomy_id'),
            pl.lit(None, dtype=_ATTR_DTYPE).alias('entity_attributes'),
            pl.struct([
                pl.lit(format_cv_term(ONTOLOGY_IDENTIFIER_TERM)).alias('type'),
                pl.col('term_id').alias('value'),
            ]).alias('_identifier'),
        ])
        .with_columns(pl.col('_identifier').implode().over('term_id').alias('identifiers'))
        .select(['_candidate_order', 'entity_type', 'taxonomy_id', 'entity_attributes', 'identifiers'])
        .with_columns(
            pl.struct(['entity_type', 'identifiers'])
            .map_elements(_fingerprint_from_row, return_dtype=pl.Utf8)
            .alias('_fingerprint')
        )
    )
    entity_candidates = pl.concat([
        materialized_occurrences.select(['_candidate_order', '_fingerprint', 'entity_type', 'taxonomy_id', 'entity_attributes', 'identifiers']),
        ontology_annotation_entities.select(['_candidate_order', '_fingerprint', 'entity_type', 'taxonomy_id', 'entity_attributes', 'identifiers']),
    ], how='vertical_relaxed')
    entity_occurrences = int(materialized_occurrences.height + pure_ontology.height)
    if entity_candidates.is_empty():
        return _empty_temp_entities(), _empty_temp_identifiers(), _empty_occurrence_map(), entity_occurrences

    deduped = (
        entity_candidates
        .sort('_candidate_order')
        .unique(subset=['_fingerprint'], maintain_order=True)
        .with_row_index('entity_pk', offset=1)
        .with_columns([pl.col('entity_pk').cast(pl.Int64), pl.lit([source]).alias('sources')])
    )
    temp_entities = deduped.select([
        'entity_pk',
        '_fingerprint',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
    ])
    temp_identifiers = (
        deduped
        .select(['entity_pk', 'identifiers'])
        .explode('identifiers')
        .unnest('identifiers')
        .filter(pl.col('value').is_not_null() & (pl.col('value') != '') & pl.col('type').is_not_null() & (pl.col('type') != ''))
        .select([
            'entity_pk',
            pl.col('value').alias('identifier'),
            pl.col('type').alias('identifier_type'),
            pl.lit(source).alias('source'),
        ])
    )
    if temp_identifiers.is_empty():
        temp_identifiers = _empty_temp_identifiers()
    occurrence_fingerprint_map = materialized_occurrences.select(['occurrence_id', '_fingerprint']).unique()
    return temp_entities, temp_identifiers, occurrence_fingerprint_map, entity_occurrences


def _build_entity_evidence_from_frames(
    *,
    silver: dict[str, pl.DataFrame],
    source: str,
    occurrence_map: pl.DataFrame,
    fingerprint_map: pl.DataFrame,
    final_entities: pl.DataFrame,
) -> pl.DataFrame:
    raw_records = (
        silver['entity_occurrence']
        .select(['occurrence_id', pl.col('record_id').cast(pl.Utf8).alias('raw_record_id')])
        .filter(pl.col('raw_record_id').is_not_null() & (pl.col('raw_record_id') != ''))
    )
    entity_level_evidence = _build_entity_level_evidence(silver['entity_annotation'])
    occurrence_evidence = (
        occurrence_map
        .join(raw_records, on='occurrence_id', how='inner')
        .select(['entity_pk', 'raw_record_id', 'occurrence_id', '_fingerprint'])
        .join(entity_level_evidence, on='occurrence_id', how='left')
    )
    ontology_evidence = _build_ontology_entity_evidence(
        annotations=silver['entity_annotation'],
        raw_records=raw_records,
        fingerprint_map=fingerprint_map,
        source=source,
    )
    evidence_index = pl.concat([occurrence_evidence, ontology_evidence], how='vertical_relaxed').unique()
    evidence = (
        final_entities
        .select([
            'entity_pk',
            'entity_key',
            'canonical_identifier',
            'canonical_identifier_type',
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
        ])
        .join(evidence_index, on='entity_pk', how='inner')
        .select([
            pl.lit(source).alias('source'),
            'entity_key',
            'canonical_identifier',
            'canonical_identifier_type',
            'raw_record_id',
            'occurrence_id',
            pl.col('_fingerprint').alias('fingerprint'),
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
            'evidence',
        ])
    )
    return evidence if not evidence.is_empty() else _empty_entity_evidence()


def _build_relation_evidence_from_frames(
    *,
    silver: dict[str, pl.DataFrame],
    source: str,
    entity_map: pl.DataFrame,
    occurrence_map: pl.DataFrame,
) -> pl.DataFrame:
    fingerprint_to_pk = {
        str(row['_fingerprint']): int(row['entity_pk'])
        for row in entity_map.select(['_fingerprint', 'entity_pk']).iter_rows(named=True)
    }
    entity_key_map = {
        int(row['entity_pk']): str(row['entity_key'])
        for row in occurrence_map.select(['entity_pk', 'entity_key']).unique().iter_rows(named=True)
    }
    occurrence_pk_map = {
        str(row['occurrence_id']): int(row['entity_pk'])
        for row in occurrence_map.select(['occurrence_id', 'entity_pk']).iter_rows(named=True)
    }
    builder = _DirectRelationBuilder(
        source=source,
        entity_map=fingerprint_to_pk,
        entity_key_map=entity_key_map,
        occurrence_map=occurrence_pk_map,
    )
    builder.convert(silver)
    return builder.relation_evidence_frame()


class _DirectRelationBuilder:
    def __init__(
        self,
        *,
        source: str,
        entity_map: dict[str, int],
        entity_key_map: dict[int, str],
        occurrence_map: dict[str, int],
    ) -> None:
        self.source = source
        self.entity_map = entity_map
        self.entity_key_map = entity_key_map
        self.occurrence_map = occurrence_map
        self.rows: list[dict[str, Any]] = []

    def convert(self, silver: dict[str, pl.DataFrame]) -> None:
        occ = silver['entity_occurrence']
        ids = silver['entity_identifier']
        anns = silver['entity_annotation']
        memberships = silver['membership']
        membership_anns = silver['membership_annotation']

        annotations_by_occ = self._annotations_by_key(anns, 'occurrence_id')
        identifiers_by_occ = self._identifiers_by_occ(ids)
        membership_annotations_by_id = self._annotations_by_key(membership_anns, 'membership_id')

        membership_rows_by_parent: dict[str, list[dict[str, Any]]] = {}
        for row in memberships.iter_rows(named=True):
            parent_id = string_or_none(row.get('parent_occurrence_id'))
            member_id = string_or_none(row.get('member_occurrence_id'))
            membership_id = string_or_none(row.get('membership_id'))
            if parent_id is None or member_id is None:
                continue
            membership_rows_by_parent.setdefault(parent_id, []).append({
                'membership_id': membership_id,
                'member_occurrence_id': member_id,
                'is_parent': row.get('is_parent'),
                'annotations': membership_annotations_by_id.get(membership_id or '', []),
            })

        ontology_backed = {
            string_or_none(row.get('occurrence_id'))
            for row in ids.iter_rows(named=True)
            if string_or_none(row.get('identifier_type')) == ONTOLOGY_IDENTIFIER_TERM
            and string_or_none(row.get('identifier')) is not None
        }
        ontology_backed.discard(None)

        occurrence_rows: dict[str, dict[str, Any]] = {}
        for row in occ.iter_rows(named=True):
            occurrence_id = string_or_none(row.get('occurrence_id'))
            if occurrence_id is None:
                continue
            row_type = string_or_none(row.get('entity_type'))
            has_membership = occurrence_id in membership_rows_by_parent
            if row_type is None and not identifiers_by_occ.get(occurrence_id) and not has_membership:
                record_class = 'ignored'
            elif row_type in INTERACTION_LIKE_TYPES and has_membership:
                record_class = 'interaction_relation'
            elif row_type == CV_TERM_ENTITY_TYPE:
                record_class = 'ontology_term_only'
            elif occurrence_id in ontology_backed:
                record_class = 'entity_with_ontology_backing'
            elif has_membership:
                record_class = 'membership_relation'
            else:
                record_class = 'entity_only'
            occurrence_rows[occurrence_id] = {
                'occurrence_id': occurrence_id,
                'type': row_type,
                'parent_occurrence_id': string_or_none(row.get('parent_occurrence_id')),
                'entity_role': string_or_none(row.get('entity_role')),
                'annotations': annotations_by_occ.get(occurrence_id, []),
                'identifiers': identifiers_by_occ.get(occurrence_id, []),
                'record_class': record_class,
                'raw_record_id': string_or_none(row.get('record_id')),
            }

        for occurrence_id, row in occurrence_rows.items():
            if row.get('parent_occurrence_id') is not None:
                continue
            record_class = row['record_class']
            if record_class in {'ignored', 'ontology_term_only'}:
                continue
            if record_class == 'interaction_relation':
                self._project_interaction_from_tables(row, occurrence_rows, membership_rows_by_parent.get(occurrence_id, []))
                continue
            parent_pk = self.occurrence_map.get(occurrence_id)
            if parent_pk is None:
                continue
            if record_class == 'membership_relation':
                self._project_memberships_from_tables(parent_pk, row, occurrence_rows, membership_rows_by_parent.get(occurrence_id, []))
            self._emit_annotation_relations(parent_pk, row.get('annotations') or [], record_class, row.get('raw_record_id'))

    def _emit_annotation_relations(
        self,
        entity_pk: int,
        annotations: list[dict[str, Any]],
        record_class: str,
        raw_record_id: str | None,
    ) -> None:
        context = AnnotationContext(record_class=record_class, parent_type=None)
        for annotation in annotations:
            disposition = classify_annotation(annotation, context)
            if disposition.bucket != 'annotation_relation':
                continue
            ontology_disposition = materialize_ontology_object(annotation, context)
            if not ontology_disposition.materialize_object_entity:
                continue
            object_desc = extract_ontology_entity_description(annotation, self.source)
            if object_desc is None:
                continue
            object_pk = self.entity_map.get(object_desc['_fingerprint'])
            if object_pk is None:
                continue
            self._write_relation_evidence(
                subject_entity_pk=entity_pk,
                predicate_rule=PredicateRule(
                    predicate=disposition.predicate or ASSOCIATION_PREDICATE,
                    relation_category=ASSOCIATION_CATEGORY,
                ),
                object_entity_pk=object_pk,
                raw_record_id=raw_record_id,
                record_attributes=None,
                subject_attributes=None,
                object_attributes=None,
                evidence=None,
            )

    def _write_relation_evidence(
        self,
        *,
        subject_entity_pk: int,
        predicate_rule: PredicateRule,
        object_entity_pk: int,
        raw_record_id: str | None,
        record_attributes: list[dict[str, str | None]] | None,
        subject_attributes: list[dict[str, str | None]] | None,
        object_attributes: list[dict[str, str | None]] | None,
        evidence: list[dict[str, str | None]] | None,
    ) -> None:
        subject_entity_key = self.entity_key_map.get(subject_entity_pk, '')
        object_entity_key = self.entity_key_map.get(object_entity_pk, '')
        relation_key = compute_relation_key(
            subject_entity_key,
            predicate_rule.predicate,
            object_entity_key,
            predicate_rule.relation_category,
        )
        self.rows.append({
            'source': self.source,
            'relation_key': relation_key,
            'subject_entity_key': subject_entity_key,
            'predicate': predicate_rule.predicate,
            'object_entity_key': object_entity_key,
            'relation_category': predicate_rule.relation_category,
            'raw_record_id': raw_record_id or '',
            'record_attributes': _normalize_attribute_list(record_attributes),
            'subject_attributes': _normalize_attribute_list(subject_attributes),
            'object_attributes': _normalize_attribute_list(object_attributes),
            'evidence': _normalize_attribute_list(evidence),
        })

    @staticmethod
    def _annotations_by_key(frame: pl.DataFrame, key_column: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        if frame.is_empty() or key_column not in frame.columns:
            return out
        for row in frame.iter_rows(named=True):
            key = string_or_none(row.get(key_column))
            if key is None:
                continue
            out.setdefault(key, []).append({
                'term': string_or_none(row.get('term')),
                'value': string_or_none(row.get('value')),
                'units': string_or_none(row.get('unit')),
            })
        return out

    @staticmethod
    def _identifiers_by_occ(frame: pl.DataFrame) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        if frame.is_empty() or 'occurrence_id' not in frame.columns:
            return out
        for row in frame.iter_rows(named=True):
            occurrence_id = string_or_none(row.get('occurrence_id'))
            if occurrence_id is None:
                continue
            out.setdefault(occurrence_id, []).append({
                'type': string_or_none(row.get('identifier_type')),
                'value': string_or_none(row.get('identifier')),
            })
        return out

    def _project_interaction_from_tables(
        self,
        row: dict[str, Any],
        occurrence_rows: dict[str, dict[str, Any]],
        memberships: list[dict[str, Any]],
    ) -> None:
        participants: list[dict[str, Any]] = []
        raw_record_id = row.get('raw_record_id')
        for membership in memberships:
            member_row = occurrence_rows.get(membership['member_occurrence_id'])
            if member_row is None:
                continue
            member_class = member_row.get('record_class') or 'entity_only'
            if member_class == 'ignored':
                member_class = 'entity_only'
            member_pk = self.occurrence_map.get(membership['member_occurrence_id'])
            if member_pk is None:
                continue
            self._emit_annotation_relations(member_pk, member_row.get('annotations') or [], member_class, raw_record_id)
            participants.append({'pk': member_pk, 'membership_annotations': membership.get('annotations') or []})
        ordered_participants = order_interaction_participants(participants)
        if len(ordered_participants) != 2:
            return
        row_type = string_or_none(row.get('type'))
        rule = predicate_for_interaction(row, ordered_participants)
        row_annotations = row.get('annotations') or []
        record_context = AnnotationContext(record_class='interaction_relation', parent_type=row_type, participant_side='record')
        subject_context = AnnotationContext(record_class='interaction_relation', parent_type=row_type, participant_side='subject')
        object_context = AnnotationContext(record_class='interaction_relation', parent_type=row_type, participant_side='object')
        evidence = _merge_attribute_lists(
            collect_attributes(row_annotations, record_context, {'evidence'}),
            _merge_attribute_lists(
                collect_attributes(ordered_participants[0]['membership_annotations'], subject_context, {'evidence'}),
                collect_attributes(ordered_participants[1]['membership_annotations'], object_context, {'evidence'}),
            ),
        )
        self._write_relation_evidence(
            subject_entity_pk=ordered_participants[0]['pk'],
            predicate_rule=rule,
            object_entity_pk=ordered_participants[1]['pk'],
            raw_record_id=raw_record_id,
            record_attributes=collect_attributes(row_annotations, record_context, {'record_attribute'}),
            subject_attributes=collect_attributes(ordered_participants[0]['membership_annotations'], subject_context, {'subject_attribute'}),
            object_attributes=collect_attributes(ordered_participants[1]['membership_annotations'], object_context, {'object_attribute'}),
            evidence=evidence,
        )

    def _project_memberships_from_tables(
        self,
        parent_pk: int,
        row: dict[str, Any],
        occurrence_rows: dict[str, dict[str, Any]],
        memberships: list[dict[str, Any]],
    ) -> None:
        parent_type = string_or_none(row.get('type'))
        raw_record_id = row.get('raw_record_id')
        parent_evidence = collect_attributes(
            row.get('annotations') or [],
            AnnotationContext(record_class='membership_relation', parent_type=parent_type),
            {'evidence'},
        )
        for membership in memberships:
            member_id = membership['member_occurrence_id']
            member_row = occurrence_rows.get(member_id)
            if member_row is None:
                continue
            member_class = member_row.get('record_class') or 'entity_only'
            if member_class == 'ignored':
                member_class = 'entity_only'
            member_pk = self.occurrence_map.get(member_id)
            if member_pk is None:
                continue
            self._emit_annotation_relations(member_pk, member_row.get('annotations') or [], member_class, raw_record_id)
            rule = predicate_for_membership(parent_type, membership)
            member_is_subject = bool(membership.get('is_parent', False))
            membership_annotations = membership.get('annotations') or []
            self._write_relation_evidence(
                subject_entity_pk=member_pk if member_is_subject else parent_pk,
                predicate_rule=rule,
                object_entity_pk=parent_pk if member_is_subject else member_pk,
                raw_record_id=raw_record_id,
                record_attributes=None,
                subject_attributes=collect_attributes(
                    membership_annotations,
                    AnnotationContext(
                        record_class='membership_relation',
                        parent_type=parent_type,
                        is_membership=True,
                        participant_side='subject' if member_is_subject else 'record',
                    ),
                    {'subject_attribute'} if member_is_subject else set(),
                ),
                object_attributes=collect_attributes(
                    membership_annotations,
                    AnnotationContext(
                        record_class='membership_relation',
                        parent_type=parent_type,
                        is_membership=True,
                        participant_side='object' if not member_is_subject else 'record',
                    ),
                    {'object_attribute'} if not member_is_subject else set(),
                ),
                evidence=_merge_attribute_lists(
                    parent_evidence,
                    collect_attributes(
                        membership_annotations,
                        AnnotationContext(record_class='membership_relation', parent_type=parent_type, is_membership=True),
                        {'evidence'},
                    ),
                ),
            )

    def relation_evidence_frame(self) -> pl.DataFrame:
        if not self.rows:
            return _empty_relation_evidence_raw()
        return pl.DataFrame(self.rows, schema_overrides={
            'record_attributes': _ATTR_DTYPE,
            'subject_attributes': _ATTR_DTYPE,
            'object_attributes': _ATTR_DTYPE,
            'evidence': _ATTR_DTYPE,
        })


def _finalize_relations(
    evidence: pl.DataFrame,
    *,
    cfg: GoldPartitionConfig,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    if evidence.is_empty():
        return _empty_relation(), _empty_relation_evidence(), _empty_relation_registry()
    relation = (
        evidence
        .group_by(['relation_key', 'subject_entity_key', 'predicate', 'object_entity_key', 'relation_category'])
        .agg([
            pl.len().alias('evidence_count'),
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .sort('relation_key')
        .with_row_index('relation_pk', offset=1)
    )
    relation = relation.with_columns([
        pl.col('relation_key').map_elements(lambda x: _stable_bucket(str(x), cfg.bucket_count), return_dtype=pl.Int64).alias('relation_bucket'),
        pl.col('relation_key').map_elements(lambda x: _stable_part(str(x), cfg.bucket_count, cfg.part_count), return_dtype=pl.Int64).alias('relation_part'),
    ])
    relation_registry = relation.select(['relation_key', 'relation_pk', 'relation_bucket', 'relation_part'])
    evidence_out = (
        evidence
        .join(relation_registry, on='relation_key', how='inner')
        .sort(['relation_key', 'source', 'raw_record_id'])
        .with_row_index('relation_evidence_pk', offset=1)
        .select([
            'relation_evidence_pk',
            'relation_pk',
            'relation_key',
            'source',
            'raw_record_id',
            'record_attributes',
            'subject_attributes',
            'object_attributes',
            'evidence',
            'subject_entity_key',
            'predicate',
            'object_entity_key',
            'relation_category',
            'relation_bucket',
            'relation_part',
        ])
    )
    relation_out = relation.select([
        'relation_pk',
        'relation_key',
        pl.lit(None, dtype=pl.Int64).alias('subject_entity_pk'),
        'subject_entity_key',
        'predicate',
        pl.lit(None, dtype=pl.Int64).alias('object_entity_pk'),
        'object_entity_key',
        'relation_category',
        'evidence_count',
        'sources',
        'relation_bucket',
        'relation_part',
    ])
    return relation_out, evidence_out, relation_registry


def _with_entity_keys(final_entities: pl.DataFrame) -> pl.DataFrame:
    return final_entities.with_columns([
        pl.col('entity_attributes').cast(_ATTR_DTYPE),
        pl.struct(['canonical_identifier', 'canonical_identifier_type', 'taxonomy_id'])
        .map_elements(
            lambda row: compute_entity_key(
                row['canonical_identifier'],
                row['canonical_identifier_type'],
                row['taxonomy_id'],
            ),
            return_dtype=pl.Utf8,
        )
        .alias('entity_key'),
    ]).select([
        'entity_pk',
        'entity_key',
        'canonical_identifier',
        'canonical_identifier_type',
        'identifiers',
        'entity_type',
        'taxonomy_id',
        'entity_attributes',
        'sources',
    ])


def _entity_registry_from_evidence(entity_evidence: pl.DataFrame) -> pl.DataFrame:
    return (
        entity_evidence
        .select(['entity_key', 'entity_bucket', 'entity_part'])
        .unique()
        .sort('entity_key')
        .with_row_index('entity_pk', offset=1)
        .select(['entity_key', 'entity_pk', 'entity_bucket', 'entity_part'])
    )


def _gold_entity_from_evidence(entity_evidence: pl.DataFrame, registry: pl.DataFrame) -> pl.DataFrame:
    return (
        entity_evidence
        .join(registry.select(['entity_key', 'entity_pk', 'entity_bucket', 'entity_part']), on='entity_key', how='inner')
        .group_by([
            'entity_pk',
            'entity_key',
            'entity_bucket',
            'entity_part',
        ])
        .agg([
            pl.col('canonical_identifier').drop_nulls().sort().first().alias('canonical_identifier'),
            pl.col('canonical_identifier_type').drop_nulls().sort().first().alias('canonical_identifier_type'),
            pl.col('identifiers').explode().drop_nulls().unique(maintain_order=True).alias('identifiers'),
            pl.col('entity_type').drop_nulls().sort().first().alias('entity_type'),
            pl.col('taxonomy_id').drop_nulls().sort().first().alias('taxonomy_id'),
            pl.col('entity_attributes').explode().drop_nulls().unique(maintain_order=True).alias('entity_attributes'),
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .sort('entity_key')
    )


def _gold_entity_evidence(entity_evidence: pl.DataFrame, registry: pl.DataFrame) -> pl.DataFrame:
    return (
        entity_evidence
        .join(registry.select(['entity_key', 'entity_pk']), on='entity_key', how='inner')
        .select([
            'entity_pk',
            'source',
            'entity_key',
            'canonical_identifier',
            'canonical_identifier_type',
            'raw_record_id',
            'occurrence_id',
            'fingerprint',
            'entity_type',
            'taxonomy_id',
            'identifiers',
            'entity_attributes',
            'evidence',
            'entity_bucket',
            'entity_part',
            'occ_bucket',
            'occ_part',
        ])
        .sort(['entity_key', 'source', 'raw_record_id', 'occurrence_id'])
    )


def _gold_occurrence_map(
    occurrence_map: pl.DataFrame,
    final_entities: pl.DataFrame,
    registry: pl.DataFrame,
    cfg: GoldPartitionConfig,
) -> pl.DataFrame:
    out = (
        occurrence_map
        .join(final_entities.select(['entity_pk', 'entity_key']), on='entity_pk', how='inner')
        .drop('entity_pk')
        .unique()
    )
    out = _add_occurrence_part_columns(out, cfg)
    return (
        out
        .join(registry.select(['entity_key', 'entity_pk']), on='entity_key', how='inner')
        .select(['occurrence_id', '_fingerprint', 'entity_pk', 'entity_key', 'occ_bucket', 'occ_part'])
        .sort('occurrence_id')
    )


def _gold_entity_map(
    fingerprint_map: pl.DataFrame,
    final_entities: pl.DataFrame,
    registry: pl.DataFrame,
    cfg: GoldPartitionConfig,
) -> pl.DataFrame:
    out = (
        fingerprint_map
        .join(final_entities.select(['entity_pk', 'entity_key']), on='entity_pk', how='inner')
        .drop('entity_pk')
        .unique()
    )
    out = _add_fingerprint_part_columns(out, cfg)
    return (
        out
        .join(registry.select(['entity_key', 'entity_pk']), on='entity_key', how='inner')
        .select(['_fingerprint', 'entity_pk', 'entity_key', 'fingerprint_bucket', 'fingerprint_part'])
        .sort('_fingerprint')
    )


def _replace_gold_tables(con: duckdb.DuckDBPyConnection, frames: dict[str, pl.DataFrame]) -> None:
    for table in [
        'gold_entity',
        'gold_entity_evidence',
        'gold_entity_map',
        'gold_entity_occurrence_map',
        'gold_entity_relation',
        'gold_entity_relation_evidence',
        'gold_entity_key_registry',
        'gold_relation_key_registry',
    ]:
        frame = frames.get(table)
        con.execute(f'drop table if exists {_quote_identifier(table)}')
        if frame is None:
            frame = pl.DataFrame()
        relation_name = f'_rewrite_{table}'
        con.register(relation_name, frame.to_arrow())
        try:
            con.execute(f'create table {_quote_identifier(table)} as select * from {relation_name}')
        finally:
            con.unregister(relation_name)


def _apply_scope_to_current_gold(
    con: duckdb.DuckDBPyConnection,
    *,
    changed_frames: dict[str, pl.DataFrame],
    scope: SourceGoldScope,
) -> ScopeApplyResult:
    current = {
        table: _read_table_frame(con, table)
        for table in [
            'gold_entity',
            'gold_entity_evidence',
            'gold_entity_map',
            'gold_entity_occurrence_map',
            'gold_entity_relation',
            'gold_entity_relation_evidence',
            'gold_entity_key_registry',
            'gold_relation_key_registry',
        ]
    }
    changed_frames = _stabilize_existing_fingerprints(changed_frames, current)
    entity_registry = _merge_entity_registry(
        current['gold_entity_key_registry'],
        changed_frames['gold_entity_evidence'],
    )
    changed_entity_evidence = _remap_entity_pk(
        changed_frames['gold_entity_evidence'],
        entity_registry,
    )
    changed_occurrence_map = _remap_entity_pk(
        changed_frames['gold_entity_occurrence_map'],
        entity_registry,
    )
    changed_entity_map = _remap_entity_pk(
        changed_frames['gold_entity_map'],
        entity_registry,
    )

    old_entity_evidence = current['gold_entity_evidence']
    changed_entity_evidence = _align_columns(changed_entity_evidence, old_entity_evidence)
    changed_occurrence_map = _align_columns(changed_occurrence_map, current['gold_entity_occurrence_map'])
    changed_entity_map = _align_columns(changed_entity_map, current['gold_entity_map'])
    affected_old_entity_evidence = _filter_entity_evidence_scope(
        old_entity_evidence,
        raw_record_ids=scope.raw_record_ids,
        occurrence_ids=scope.occurrence_ids,
    )
    affected_entity_keys = _string_set(affected_old_entity_evidence, 'entity_key') | _string_set(
        changed_entity_evidence,
        'entity_key',
    )
    affected_fingerprints = _string_set(affected_old_entity_evidence, 'fingerprint') | _string_set(
        changed_entity_map,
        '_fingerprint',
    )
    scoped_occurrence_ids = (
        scope.occurrence_ids
        | _string_set(affected_old_entity_evidence, 'occurrence_id')
        | _string_set(changed_occurrence_map, 'occurrence_id')
    )

    merged_entity_evidence = pl.concat([
        _anti_entity_evidence_scope(
            old_entity_evidence,
            raw_record_ids=scope.raw_record_ids,
            occurrence_ids=scoped_occurrence_ids,
        ),
        changed_entity_evidence,
    ], how='vertical_relaxed')
    merged_occurrence_map = pl.concat([
        _anti_string_values(current['gold_entity_occurrence_map'], 'occurrence_id', scoped_occurrence_ids),
        changed_occurrence_map,
    ], how='vertical_relaxed')
    merged_entity_map = pl.concat([
        _anti_string_values(current['gold_entity_map'], '_fingerprint', affected_fingerprints),
        changed_entity_map,
    ], how='vertical_relaxed')
    recomputed_entities = (
        _gold_entity_from_evidence(
            _filter_string_values(merged_entity_evidence, 'entity_key', affected_entity_keys),
            entity_registry,
        )
        if affected_entity_keys
        else current['gold_entity'].head(0)
    )
    merged_entity = pl.concat([
        _anti_string_values(current['gold_entity'], 'entity_key', affected_entity_keys),
        recomputed_entities,
    ], how='vertical_relaxed')

    relation_registry = _merge_relation_registry(
        current['gold_relation_key_registry'],
        changed_frames['gold_entity_relation_evidence'],
    )
    changed_relation_evidence = _remap_relation_pk(
        changed_frames['gold_entity_relation_evidence'],
        relation_registry,
    )
    old_relation_evidence = current['gold_entity_relation_evidence']
    changed_relation_evidence = _align_columns(changed_relation_evidence, old_relation_evidence)
    affected_old_relation_evidence = _filter_relation_evidence_scope(
        old_relation_evidence,
        raw_record_ids=scope.raw_record_ids,
        entity_keys=affected_entity_keys,
    )
    affected_relation_keys = _string_set(affected_old_relation_evidence, 'relation_key') | _string_set(
        changed_relation_evidence,
        'relation_key',
    )
    relation_registry = _merge_relation_registry(
        relation_registry,
        changed_relation_evidence,
    )
    merged_relation_evidence_base = pl.concat([
        _anti_relation_evidence_scope(
            old_relation_evidence,
            raw_record_ids=scope.raw_record_ids,
        ),
        changed_relation_evidence,
    ], how='vertical_relaxed')
    recomputed_relations, remapped_relation_evidence, relation_registry = _finalize_relations_from_evidence(
        merged_relation_evidence_base,
        relation_registry=relation_registry,
    )
    merged_relation = pl.concat([
        _anti_string_values(current['gold_entity_relation'], 'relation_key', affected_relation_keys),
        _filter_string_values(recomputed_relations, 'relation_key', affected_relation_keys),
    ], how='vertical_relaxed')
    scoped_changed = _scoped_gold_delta_changed(
        con,
        current=current,
        entity_registry=entity_registry,
        relation_registry=relation_registry,
        old_entity_evidence=affected_old_entity_evidence,
        new_entity_evidence=changed_entity_evidence,
        old_occurrence_map=_filter_string_values(
            current['gold_entity_occurrence_map'],
            'occurrence_id',
            scoped_occurrence_ids,
        ),
        new_occurrence_map=changed_occurrence_map,
        old_entity_map=_filter_string_values(
            current['gold_entity_map'],
            '_fingerprint',
            affected_fingerprints,
        ),
        new_entity_map=changed_entity_map,
        old_entity=_filter_string_values(
            current['gold_entity'],
            'entity_key',
            affected_entity_keys,
        ),
        new_entity=recomputed_entities,
        old_relation_evidence=_filter_relation_evidence_raw_scope(
            old_relation_evidence,
            raw_record_ids=scope.raw_record_ids,
        ),
        new_relation_evidence=changed_relation_evidence,
        old_relation=_filter_string_values(
            current['gold_entity_relation'],
            'relation_key',
            affected_relation_keys,
        ),
        new_relation=_filter_string_values(
            recomputed_relations,
            'relation_key',
            affected_relation_keys,
        ),
        affected_entity_keys=affected_entity_keys,
        affected_relation_keys=affected_relation_keys,
    )

    return ScopeApplyResult(
        frames={
            'gold_entity': _sort_if_present(merged_entity, 'entity_key'),
            'gold_entity_evidence': _sort_if_present(merged_entity_evidence, 'entity_key'),
            'gold_entity_map': _sort_if_present(merged_entity_map, '_fingerprint'),
            'gold_entity_occurrence_map': _sort_if_present(merged_occurrence_map, 'occurrence_id'),
            'gold_entity_relation': _sort_if_present(merged_relation, 'relation_key'),
            'gold_entity_relation_evidence': _sort_if_present(remapped_relation_evidence, 'relation_key'),
            'gold_entity_key_registry': _sort_if_present(entity_registry, 'entity_key'),
            'gold_relation_key_registry': _sort_if_present(relation_registry, 'relation_key'),
        },
        entity_keys=affected_entity_keys,
        relation_keys=affected_relation_keys,
        changed=scoped_changed,
    )


def _current_gold_state_exists(con: duckdb.DuckDBPyConnection) -> bool:
    return all(_table_exists(con, table) for table in [
        'gold_entity',
        'gold_entity_evidence',
        'gold_entity_map',
        'gold_entity_occurrence_map',
        'gold_entity_relation',
        'gold_entity_relation_evidence',
        'gold_entity_key_registry',
        'gold_relation_key_registry',
    ])


def _read_table_frame(con: duckdb.DuckDBPyConnection, table: str) -> pl.DataFrame:
    return pl.from_arrow(con.execute(f'select * from {_quote_identifier(table)}').fetch_arrow_table())


def _merge_entity_registry(registry: pl.DataFrame, evidence: pl.DataFrame) -> pl.DataFrame:
    if evidence.is_empty():
        return registry
    existing = set(registry.get_column('entity_key').to_list()) if not registry.is_empty() else set()
    new_keys = (
        evidence
        .select(['entity_key', 'entity_bucket', 'entity_part'])
        .unique()
        .filter(~pl.col('entity_key').is_in(existing))
        .sort('entity_key')
    )
    if new_keys.is_empty():
        return registry
    max_pk = int(registry.get_column('entity_pk').max() or 0) if not registry.is_empty() else 0
    new_rows = new_keys.with_row_index('entity_pk', offset=max_pk + 1).select([
        'entity_key',
        'entity_pk',
        'entity_bucket',
        'entity_part',
    ])
    return pl.concat([registry, new_rows], how='vertical_relaxed')


def _merge_relation_registry(registry: pl.DataFrame, evidence: pl.DataFrame) -> pl.DataFrame:
    if evidence.is_empty():
        return registry
    existing = set(registry.get_column('relation_key').to_list()) if not registry.is_empty() else set()
    new_keys = (
        evidence
        .select(['relation_key', 'relation_bucket', 'relation_part'])
        .unique()
        .filter(~pl.col('relation_key').is_in(existing))
        .sort('relation_key')
    )
    if new_keys.is_empty():
        return registry
    max_pk = int(registry.get_column('relation_pk').max() or 0) if not registry.is_empty() else 0
    new_rows = new_keys.with_row_index('relation_pk', offset=max_pk + 1).select([
        'relation_key',
        'relation_pk',
        'relation_bucket',
        'relation_part',
    ])
    return pl.concat([registry, new_rows], how='vertical_relaxed')


def _remap_entity_pk(frame: pl.DataFrame, registry: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return (
        frame.drop(['entity_pk', 'entity_bucket', 'entity_part'], strict=False)
        .join(
            registry.select(['entity_key', 'entity_pk', 'entity_bucket', 'entity_part']),
            on='entity_key',
            how='left',
        )
    )


def _stabilize_existing_fingerprints(
    changed: dict[str, pl.DataFrame],
    current: dict[str, pl.DataFrame],
) -> dict[str, pl.DataFrame]:
    current_map = current['gold_entity_map']
    if current_map.is_empty():
        return changed
    changed_map = changed['gold_entity_map']
    if changed_map.is_empty():
        return changed
    fingerprint_keys = current_map.select([
        '_fingerprint',
        pl.col('entity_key').alias('_current_entity_key'),
    ]).unique()
    changed_key_map = (
        changed_map
        .select([
            'entity_key',
            '_fingerprint',
        ])
        .join(fingerprint_keys, on='_fingerprint', how='inner')
        .filter(pl.col('_current_entity_key').is_not_null())
        .select([
            'entity_key',
            '_current_entity_key',
        ])
        .unique()
    )
    current_entities = current['gold_entity'].select([
        'entity_key',
        pl.col('canonical_identifier').alias('_current_canonical_identifier'),
        pl.col('canonical_identifier_type').alias('_current_canonical_identifier_type'),
        pl.col('identifiers').alias('_current_identifiers'),
        pl.col('entity_type').alias('_current_entity_type'),
        pl.col('taxonomy_id').alias('_current_taxonomy_id'),
        pl.col('entity_attributes').alias('_current_entity_attributes'),
    ])

    stabilized = dict(changed)
    for table in ('gold_entity_map', 'gold_entity_occurrence_map'):
        frame = stabilized[table]
        if frame.is_empty():
            continue
        stabilized[table] = (
            frame
            .join(fingerprint_keys, on='_fingerprint', how='left')
            .with_columns(
                pl.coalesce(['_current_entity_key', 'entity_key']).alias('entity_key')
            )
            .drop('_current_entity_key')
        )

    evidence = stabilized['gold_entity_evidence']
    if not evidence.is_empty():
        stabilized['gold_entity_evidence'] = (
            evidence
            .join(fingerprint_keys, left_on='fingerprint', right_on='_fingerprint', how='left')
            .with_columns(pl.coalesce(['_current_entity_key', 'entity_key']).alias('entity_key'))
            .join(current_entities, on='entity_key', how='left')
            .with_columns([
                pl.coalesce(['_current_canonical_identifier', 'canonical_identifier']).alias('canonical_identifier'),
                pl.coalesce(['_current_canonical_identifier_type', 'canonical_identifier_type']).alias('canonical_identifier_type'),
                pl.coalesce(['_current_identifiers', 'identifiers']).alias('identifiers'),
                pl.coalesce(['_current_entity_type', 'entity_type']).alias('entity_type'),
                pl.coalesce(['_current_taxonomy_id', 'taxonomy_id']).alias('taxonomy_id'),
                pl.coalesce(['_current_entity_attributes', 'entity_attributes']).alias('entity_attributes'),
            ])
            .drop([
                '_current_entity_key',
                '_current_canonical_identifier',
                '_current_canonical_identifier_type',
                '_current_identifiers',
                '_current_entity_type',
                '_current_taxonomy_id',
                '_current_entity_attributes',
            ])
        )
    relation_evidence = stabilized['gold_entity_relation_evidence']
    if not relation_evidence.is_empty() and not changed_key_map.is_empty():
        subject_key_map = changed_key_map.rename({
            'entity_key': 'subject_entity_key',
            '_current_entity_key': '_current_subject_entity_key',
        })
        object_key_map = changed_key_map.rename({
            'entity_key': 'object_entity_key',
            '_current_entity_key': '_current_object_entity_key',
        })
        stabilized['gold_entity_relation_evidence'] = (
            relation_evidence
            .join(subject_key_map, on='subject_entity_key', how='left')
            .join(object_key_map, on='object_entity_key', how='left')
            .with_columns([
                pl.coalesce(['_current_subject_entity_key', 'subject_entity_key']).alias('subject_entity_key'),
                pl.coalesce(['_current_object_entity_key', 'object_entity_key']).alias('object_entity_key'),
            ])
            .with_columns(
                pl.struct([
                    'subject_entity_key',
                    'predicate',
                    'object_entity_key',
                    'relation_category',
                ])
                .map_elements(
                    lambda row: compute_relation_key(
                        row['subject_entity_key'],
                        row['predicate'],
                        row['object_entity_key'],
                        row['relation_category'],
                    ),
                    return_dtype=pl.Utf8,
                )
                .alias('relation_key')
            )
            .drop(['_current_subject_entity_key', '_current_object_entity_key'])
        )
    return stabilized


def _remap_relation_pk(frame: pl.DataFrame, registry: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return (
        frame.drop(['relation_pk', 'relation_evidence_pk'], strict=False)
        .join(registry.select(['relation_key', 'relation_pk']), on='relation_key', how='left')
    )


def _finalize_relations_from_evidence(
    evidence: pl.DataFrame,
    *,
    relation_registry: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    if evidence.is_empty():
        return _empty_relation(), _empty_relation_evidence(), relation_registry
    relation = (
        evidence
        .group_by(['relation_key', 'subject_entity_key', 'predicate', 'object_entity_key', 'relation_category'])
        .agg([
            pl.len().alias('evidence_count'),
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .join(relation_registry, on='relation_key', how='inner')
        .select([
            'relation_pk',
            'relation_key',
            pl.lit(None, dtype=pl.Int64).alias('subject_entity_pk'),
            'subject_entity_key',
            'predicate',
            pl.lit(None, dtype=pl.Int64).alias('object_entity_pk'),
            'object_entity_key',
            'relation_category',
            'evidence_count',
            'sources',
            'relation_bucket',
            'relation_part',
        ])
    )
    evidence_out = (
        evidence
        .drop(['relation_pk', 'relation_evidence_pk'], strict=False)
        .join(relation_registry, on='relation_key', how='inner')
        .sort(['relation_key', 'source', 'raw_record_id'])
        .with_row_index('relation_evidence_pk', offset=1)
        .select([
            'relation_evidence_pk',
            'relation_pk',
            'relation_key',
            'source',
            'raw_record_id',
            'record_attributes',
            'subject_attributes',
            'object_attributes',
            'evidence',
            'subject_entity_key',
            'predicate',
            'object_entity_key',
            'relation_category',
            'relation_bucket',
            'relation_part',
        ])
    )
    return relation, evidence_out, relation_registry


def _filter_entity_evidence_scope(
    frame: pl.DataFrame,
    *,
    raw_record_ids: set[int],
    occurrence_ids: set[str],
) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(
        pl.col('raw_record_id').cast(pl.Int64, strict=False).is_in(raw_record_ids)
        | pl.col('occurrence_id').is_in(occurrence_ids)
    )


def _anti_entity_evidence_scope(
    frame: pl.DataFrame,
    *,
    raw_record_ids: set[int],
    occurrence_ids: set[str],
) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(
        ~(
            pl.col('raw_record_id').cast(pl.Int64, strict=False).is_in(raw_record_ids)
            | pl.col('occurrence_id').is_in(occurrence_ids)
        )
    )


def _filter_relation_evidence_scope(
    frame: pl.DataFrame,
    *,
    raw_record_ids: set[int],
    entity_keys: set[str],
) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(
        pl.col('raw_record_id').cast(pl.Int64, strict=False).is_in(raw_record_ids)
        | pl.col('subject_entity_key').is_in(entity_keys)
        | pl.col('object_entity_key').is_in(entity_keys)
    )


def _anti_relation_evidence_scope(
    frame: pl.DataFrame,
    *,
    raw_record_ids: set[int],
) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(
        ~pl.col('raw_record_id').cast(pl.Int64, strict=False).is_in(raw_record_ids)
    )


def _filter_relation_evidence_raw_scope(
    frame: pl.DataFrame,
    *,
    raw_record_ids: set[int],
) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(
        pl.col('raw_record_id').cast(pl.Int64, strict=False).is_in(raw_record_ids)
    )


def _filter_string_values(frame: pl.DataFrame, column: str, values: set[str]) -> pl.DataFrame:
    if frame.is_empty() or not values:
        return frame.head(0)
    return frame.filter(pl.col(column).is_in(values))


def _anti_string_values(frame: pl.DataFrame, column: str, values: set[str]) -> pl.DataFrame:
    if frame.is_empty() or not values:
        return frame
    return frame.filter(~pl.col(column).is_in(values))


def _string_set(frame: pl.DataFrame, column: str) -> set[str]:
    if frame.is_empty() or column not in frame.columns:
        return set()
    return {str(value) for value in frame.get_column(column).drop_nulls().to_list()}


def _sort_if_present(frame: pl.DataFrame, column: str) -> pl.DataFrame:
    return frame.sort(column) if not frame.is_empty() and column in frame.columns else frame


def _align_columns(frame: pl.DataFrame, reference: pl.DataFrame) -> pl.DataFrame:
    if reference.is_empty() and not reference.columns:
        return frame
    for column in reference.columns:
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias(column))
    return frame.select(reference.columns)


def _scoped_gold_delta_changed(
    con: duckdb.DuckDBPyConnection,
    *,
    current: dict[str, pl.DataFrame],
    entity_registry: pl.DataFrame,
    relation_registry: pl.DataFrame,
    old_entity_evidence: pl.DataFrame,
    new_entity_evidence: pl.DataFrame,
    old_occurrence_map: pl.DataFrame,
    new_occurrence_map: pl.DataFrame,
    old_entity_map: pl.DataFrame,
    new_entity_map: pl.DataFrame,
    old_entity: pl.DataFrame,
    new_entity: pl.DataFrame,
    old_relation_evidence: pl.DataFrame,
    new_relation_evidence: pl.DataFrame,
    old_relation: pl.DataFrame,
    new_relation: pl.DataFrame,
    affected_entity_keys: set[str],
    affected_relation_keys: set[str],
) -> bool:
    checks = [
        (old_entity_evidence, new_entity_evidence, ()),
        (old_occurrence_map, new_occurrence_map, ()),
        (old_entity_map, new_entity_map, ()),
        (old_entity, new_entity, ()),
        (
            old_relation_evidence,
            new_relation_evidence,
            ('relation_evidence_pk', 'relation_pk', 'relation_bucket', 'relation_part'),
        ),
        (old_relation, new_relation, ()),
        (
            _filter_string_values(current['gold_entity_key_registry'], 'entity_key', affected_entity_keys),
            _filter_string_values(entity_registry, 'entity_key', affected_entity_keys),
            (),
        ),
        (
            _filter_string_values(current['gold_relation_key_registry'], 'relation_key', affected_relation_keys),
            _filter_string_values(relation_registry, 'relation_key', affected_relation_keys),
            (),
        ),
    ]
    return any(
        not _frames_equal(con, old_frame, new_frame, ignore_columns=ignore_columns)
        for old_frame, new_frame, ignore_columns in checks
    )


def _frames_equal(
    con: duckdb.DuckDBPyConnection,
    left: pl.DataFrame,
    right: pl.DataFrame,
    *,
    ignore_columns: tuple[str, ...] = (),
) -> bool:
    if left.is_empty() and right.is_empty():
        return True
    if left.columns:
        right = _align_columns(right, left)
    elif right.columns:
        left = _align_columns(left, right)
    columns = [column for column in left.columns if column not in set(ignore_columns)]
    if not columns:
        return left.height == right.height
    left_name = '_rewrite_delta_left'
    right_name = '_rewrite_delta_right'
    con.register(left_name, left.to_arrow())
    con.register(right_name, right.to_arrow())
    try:
        column_sql = ', '.join(_quote_identifier(column) for column in columns)
        diff_count = int(con.execute(f'''
            select count(*) from (
                (select {column_sql} from {left_name}
                 except all
                 select {column_sql} from {right_name})
                union all
                (select {column_sql} from {right_name}
                 except all
                 select {column_sql} from {left_name})
            )
        ''').fetchone()[0])
    finally:
        con.unregister(left_name)
        con.unregister(right_name)
    return diff_count == 0


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(con.execute(
        """
        select count(*)
        from information_schema.tables
        where table_schema = 'main'
          and table_name = ?
        """,
        [table],
    ).fetchone()[0])


def _table_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    if not _table_exists(con, table):
        return 0
    return int(con.execute(f'select count(*) from {_quote_identifier(table)}').fetchone()[0] or 0)


def _refresh_scope_tables(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_keys: set[str],
    relation_keys: set[str],
) -> None:
    entity_key_frame = pl.DataFrame({'entity_key': sorted(entity_keys)})
    relation_key_frame = pl.DataFrame({'relation_key': sorted(relation_keys)})
    con.register('_rewrite_scope_entity_keys', entity_key_frame.to_arrow())
    con.register('_rewrite_scope_relation_keys', relation_key_frame.to_arrow())
    try:
        con.execute('drop table if exists source_run_scope_entity')
        con.execute('''
            create table source_run_scope_entity as
            with scope_run as (
                select max(source_run_id) as source_run_id, max(source) as source
                from (
                    select source_run_id, source from source_run_scope_raw_record
                    union all
                    select source_run_id, source from source_run_scope_occurrence
                )
            )
            select
                coalesce(nullif(scope_run.source_run_id, ''), 'rewrite') as source_run_id,
                scope_run.source,
                k.entity_key,
                max(e.entity_bucket) as entity_bucket,
                max(e.entity_part) as entity_part,
                'scoped_gold_update'::varchar as reason
            from _rewrite_scope_entity_keys k
            cross join scope_run
            left join gold_entity e
              on e.entity_key = k.entity_key
            group by scope_run.source_run_id, scope_run.source, k.entity_key
        ''')
        con.execute('drop table if exists source_run_scope_relation')
        con.execute('''
            create table source_run_scope_relation as
            with scope_run as (
                select max(source_run_id) as source_run_id, max(source) as source
                from (
                    select source_run_id, source from source_run_scope_raw_record
                    union all
                    select source_run_id, source from source_run_scope_occurrence
                )
            )
            select
                coalesce(nullif(scope_run.source_run_id, ''), 'rewrite') as source_run_id,
                scope_run.source,
                k.relation_key,
                max(r.relation_bucket) as relation_bucket,
                max(r.relation_part) as relation_part,
                'scoped_gold_update'::varchar as reason
            from _rewrite_scope_relation_keys k
            cross join scope_run
            left join gold_entity_relation r
              on r.relation_key = k.relation_key
            group by scope_run.source_run_id, scope_run.source, k.relation_key
        ''')
    finally:
        con.unregister('_rewrite_scope_entity_keys')
        con.unregister('_rewrite_scope_relation_keys')


def _clear_scope_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute('drop table if exists source_run_scope_entity')
    con.execute('''
        create table source_run_scope_entity(
            source_run_id varchar,
            source varchar,
            entity_key varchar,
            entity_bucket bigint,
            entity_part bigint,
            reason varchar
        )
    ''')
    con.execute('drop table if exists source_run_scope_relation')
    con.execute('''
        create table source_run_scope_relation(
            source_run_id varchar,
            source varchar,
            relation_key varchar,
            relation_bucket bigint,
            relation_part bigint,
            reason varchar
        )
    ''')


def _direct_taxonomy(occ_df: pl.DataFrame, ids_fmt: pl.DataFrame, anns: pl.DataFrame) -> pl.DataFrame:
    taxonomy_from_ids = (
        ids_fmt
        .filter(pl.col('type') == format_cv_term(TAXONOMY_IDENTIFIER_TERM))
        .group_by('occurrence_id')
        .agg(pl.col('value').first().alias('taxonomy_from_id'))
    )
    taxonomy_from_anns = (
        anns
        .filter((pl.col('term') == TAXONOMY_IDENTIFIER_TERM) & pl.col('value').is_not_null() & (pl.col('value') != ''))
        .group_by('occurrence_id')
        .agg(pl.col('value').first().alias('taxonomy_from_annotation'))
    )
    return (
        occ_df.select('occurrence_id')
        .join(taxonomy_from_ids, on='occurrence_id', how='left')
        .join(taxonomy_from_anns, on='occurrence_id', how='left')
        .with_columns(pl.coalesce(['taxonomy_from_id', 'taxonomy_from_annotation']).alias('direct_taxonomy_id'))
        .select(['occurrence_id', 'direct_taxonomy_id'])
    )


def _member_taxonomy(membership_df: pl.DataFrame, direct_taxonomy: pl.DataFrame) -> pl.DataFrame:
    if membership_df.is_empty():
        return pl.DataFrame({
            'occurrence_id': pl.Series([], dtype=pl.Utf8),
            'member_taxonomy_id': pl.Series([], dtype=pl.Utf8),
        })
    return (
        membership_df
        .join(direct_taxonomy.rename({'occurrence_id': 'member_occurrence_id'}), on='member_occurrence_id', how='left')
        .filter(pl.col('direct_taxonomy_id').is_not_null() & (pl.col('direct_taxonomy_id') != ''))
        .group_by('parent_occurrence_id')
        .agg([
            pl.col('direct_taxonomy_id').n_unique().alias('_member_taxonomy_n'),
            pl.col('direct_taxonomy_id').first().alias('member_taxonomy_id'),
        ])
        .filter(pl.col('_member_taxonomy_n') == 1)
        .select([pl.col('parent_occurrence_id').alias('occurrence_id'), 'member_taxonomy_id'])
    )


def _pure_ontology(anns: pl.DataFrame, occ_df: pl.DataFrame) -> pl.DataFrame:
    return (
        anns
        .filter((pl.col('term') == ONTOLOGY_IDENTIFIER_TERM) & pl.col('units').is_null())
        .filter(pl.col('value').is_not_null() & (pl.col('value') != ''))
        .join(occ_df.select(['occurrence_id', '_occurrence_index']), on='occurrence_id', how='left')
        .select([
            'occurrence_id',
            pl.col('value').alias('term_id'),
            ((pl.col('_occurrence_index') * 10_000) + pl.col('_annotation_pos')).alias('_candidate_order'),
        ])
    )


def _attributes_by_occ(anns: pl.DataFrame) -> pl.DataFrame:
    attr_rows = (
        anns
        .with_columns(pl.col('value').map_elements(_is_accession, return_dtype=pl.Boolean).alias('_value_is_accession'))
        .filter(
            pl.col('term').is_not_null()
            & (pl.col('term') != TAXONOMY_IDENTIFIER_TERM)
            & (~pl.col('term').is_in(list(EVIDENCE_IDENTIFIER_TERMS)))
            & ~((pl.col('term') == ONTOLOGY_IDENTIFIER_TERM) & pl.col('units').is_null() & pl.col('_value_is_accession'))
            & ~((pl.col('term') == ONTOLOGY_IDENTIFIER_TERM) & pl.col('units').is_null())
        )
        .select([
            'occurrence_id',
            pl.col('term').map_elements(_normalize_attr_term, return_dtype=pl.Utf8).alias('term'),
            'value',
            pl.col('units').map_elements(_normalize_attr_term, return_dtype=pl.Utf8).alias('unit'),
        ])
    )
    return attr_rows.group_by('occurrence_id', maintain_order=True).agg(
        pl.struct(['term', 'value', 'unit']).alias('entity_attributes')
    )


def _build_entity_level_evidence(annotations: pl.DataFrame) -> pl.DataFrame:
    if annotations.is_empty():
        return pl.DataFrame({
            'occurrence_id': pl.Series([], dtype=pl.Utf8),
            'evidence': pl.Series([], dtype=ENTITY_EVIDENCE_SCHEMA['evidence']),
        })
    rows = (
        annotations
        .filter(
            pl.col('term').is_in(list(EVIDENCE_IDENTIFIER_TERMS))
            & pl.col('value').is_not_null()
            & (pl.col('value') != '')
        )
        .select([
            'occurrence_id',
            pl.struct([
                pl.col('term').cast(pl.Utf8).alias('term'),
                pl.col('value').cast(pl.Utf8).alias('value'),
                pl.col('unit').cast(pl.Utf8).alias('unit'),
            ]).alias('evidence_item'),
        ])
    )
    if rows.is_empty():
        return pl.DataFrame({
            'occurrence_id': pl.Series([], dtype=pl.Utf8),
            'evidence': pl.Series([], dtype=ENTITY_EVIDENCE_SCHEMA['evidence']),
        })
    return rows.unique().group_by('occurrence_id').agg(pl.col('evidence_item').alias('evidence'))


def _build_ontology_entity_evidence(
    *,
    annotations: pl.DataFrame,
    raw_records: pl.DataFrame,
    fingerprint_map: pl.DataFrame,
    source: str,
) -> pl.DataFrame:
    empty = _empty_entity_evidence_index()
    if fingerprint_map.is_empty() or annotations.is_empty():
        return empty
    rows = (
        annotations
        .filter(
            (pl.col('term') == ONTOLOGY_IDENTIFIER_TERM)
            & pl.col('value').is_not_null()
            & (pl.col('value') != '')
            & pl.col('unit').is_null()
        )
        .select(['occurrence_id', 'value'])
    )
    if rows.is_empty():
        return empty
    fingerprint_rows = []
    for row in rows.unique().iter_rows(named=True):
        desc = extract_ontology_entity_description({'value': row['value']}, source)
        if desc is not None:
            fingerprint_rows.append({'occurrence_id': row['occurrence_id'], '_fingerprint': desc['_fingerprint']})
    if not fingerprint_rows:
        return empty
    return (
        pl.DataFrame(fingerprint_rows)
        .join(raw_records, on='occurrence_id', how='inner')
        .join(fingerprint_map, on='_fingerprint', how='inner')
        .select(['entity_pk', 'raw_record_id', 'occurrence_id', '_fingerprint'])
        .with_columns(pl.lit([], dtype=ENTITY_EVIDENCE_SCHEMA['evidence']).alias('evidence'))
        .unique()
    )


def _empty_temp_entities() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_pk': pl.Series([], dtype=pl.Int64),
        '_fingerprint': pl.Series([], dtype=pl.Utf8),
        'entity_type': pl.Series([], dtype=pl.Utf8),
        'taxonomy_id': pl.Series([], dtype=pl.Utf8),
        'entity_attributes': pl.Series([], dtype=_ATTR_DTYPE),
        'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
    })


def _empty_temp_identifiers() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_pk': pl.Series([], dtype=pl.Int64),
        'identifier': pl.Series([], dtype=pl.Utf8),
        'identifier_type': pl.Series([], dtype=pl.Utf8),
        'source': pl.Series([], dtype=pl.Utf8),
    })


def _empty_occurrence_map() -> pl.DataFrame:
    return pl.DataFrame({
        'occurrence_id': pl.Series([], dtype=pl.Utf8),
        '_fingerprint': pl.Series([], dtype=pl.Utf8),
    })


def _empty_entity_evidence_index() -> pl.DataFrame:
    return pl.DataFrame({
        'entity_pk': pl.Series([], dtype=pl.Int64),
        'raw_record_id': pl.Series([], dtype=pl.Utf8),
        'occurrence_id': pl.Series([], dtype=pl.Utf8),
        '_fingerprint': pl.Series([], dtype=pl.Utf8),
        'evidence': pl.Series([], dtype=ENTITY_EVIDENCE_SCHEMA['evidence']),
    })


def _empty_entity_evidence() -> pl.DataFrame:
    return pl.DataFrame({name: pl.Series([], dtype=dtype) for name, dtype in ENTITY_EVIDENCE_SCHEMA.items()})


def _empty_relation_evidence_raw() -> pl.DataFrame:
    return pl.DataFrame({
        'source': pl.Series([], dtype=pl.Utf8),
        'relation_key': pl.Series([], dtype=pl.Utf8),
        'subject_entity_key': pl.Series([], dtype=pl.Utf8),
        'predicate': pl.Series([], dtype=pl.Utf8),
        'object_entity_key': pl.Series([], dtype=pl.Utf8),
        'relation_category': pl.Series([], dtype=pl.Utf8),
        'raw_record_id': pl.Series([], dtype=pl.Utf8),
        'record_attributes': pl.Series([], dtype=_ATTR_DTYPE),
        'subject_attributes': pl.Series([], dtype=_ATTR_DTYPE),
        'object_attributes': pl.Series([], dtype=_ATTR_DTYPE),
        'evidence': pl.Series([], dtype=_ATTR_DTYPE),
    })


def _normalize_attribute_list(values: list[dict[str, Any]] | None) -> list[dict[str, str | None]]:
    if not values:
        return []
    normalized: list[dict[str, str | None]] = []
    for value in values:
        normalized.append({
            'term': string_or_none(value.get('term')),
            'value': string_or_none(value.get('value')),
            'unit': string_or_none(value.get('unit', value.get('units'))),
        })
    return normalized


def _empty_relation() -> pl.DataFrame:
    return pl.DataFrame()


def _empty_relation_evidence() -> pl.DataFrame:
    return pl.DataFrame()


def _empty_relation_registry() -> pl.DataFrame:
    return pl.DataFrame()


def _empty_gold_frames() -> dict[str, pl.DataFrame]:
    return {
        'gold_entity': pl.DataFrame(),
        'gold_entity_evidence': pl.DataFrame(),
        'gold_entity_map': pl.DataFrame(),
        'gold_entity_occurrence_map': pl.DataFrame(),
        'gold_entity_relation': pl.DataFrame(),
        'gold_entity_relation_evidence': pl.DataFrame(),
        'gold_entity_key_registry': pl.DataFrame(),
        'gold_relation_key_registry': pl.DataFrame(),
    }


def _is_accession(value: Any) -> bool:
    text = string_or_none(value)
    return bool(text and is_cv_term_accession(text))


def _normalize_attr_term(value: Any) -> str | None:
    text = string_or_none(value)
    if text is None:
        return None
    if is_cv_term_accession(text):
        return format_cv_term(text)
    return text


def _fingerprint_from_row(row: dict[str, Any]) -> str:
    return compute_entity_fingerprint(row.get('entity_type'), row.get('identifiers') or [])


def _stable_bucket(value: str, bucket_count: int) -> int:
    import hashlib

    digest = hashlib.sha256(value.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big', signed=False) % bucket_count


def _stable_part(value: str, bucket_count: int, part_count: int) -> int:
    return _stable_bucket(value, bucket_count) % part_count


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
