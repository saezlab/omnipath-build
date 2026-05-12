from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Iterable

import duckdb
import polars as pl

from omnipath_build.silver.tables import silver_table_dir, has_silver_tables
from omnipath_build.gold.utils.keys import compute_relation_key
from omnipath_build.gold.utils.schema import (
    CV_TERM_ENTITY_TYPE,
    ASSOCIATION_CATEGORY,
    ASSOCIATION_PREDICATE,
    INTERACTION_LIKE_TYPES,
    ONTOLOGY_IDENTIFIER_TERM,
    PredicateRule,
    AnnotationContext,
    string_or_none,
    classify_annotation,
    predicate_for_membership,
    predicate_for_interaction,
    materialize_ontology_object,
    order_interaction_participants,
)
from omnipath_build.gold.utils.entity_extraction import (
    ENTITY_RELATION_SCHEMA,
    ENTITY_RELATION_EVIDENCE_SCHEMA,
    BufferedParquetWriter,
    collect_attributes,
    extract_ontology_entity_description,
)
from omnipath_build.gold.build_entities import (
    GoldPartitionConfig,
    effective_partition_config_for_paths,
    _stable_bucket_py,
    _stable_part_py,
    _register_hash_functions,
    _configure_duckdb,
    _copy_part_query,
    _copy_query,
    _create_part_temp_table,
    _glob_or_none,
    _read_parquet_dataset_sql,
    _silver_table_path,
    _sql_path,
    _write_frame_partition_files,
)


class RelationWriterBase:
    def __init__(
        self,
        source: str,
        silver_dir: Path,
        output_dir: Path,
        entity_map: dict[str, int],
        entity_key_map: dict[int, str],
        batch_size: int = 10_000,
    ) -> None:
        self.source = source
        self.silver_dir = silver_dir
        self.output_dir = output_dir
        self.entity_map = entity_map
        self.entity_key_map = entity_key_map
        self.batch_size = batch_size

        self.next_relation_pk = 1
        self.next_relation_evidence_pk = 1

        self.entity_relation_evidence = BufferedParquetWriter(
            output_dir / 'entity_relation_evidence.parquet',
            ENTITY_RELATION_EVIDENCE_SCHEMA,
            batch_size,
        )
        self.entity_relations = BufferedParquetWriter(
            output_dir / 'entity_relation.parquet',
            ENTITY_RELATION_SCHEMA,
            batch_size,
        )

        self.relation_index: dict[tuple[int, str, int, str], dict[str, Any]] = {}

    def close(self) -> None:
        for relation_row in sorted(self.relation_index.values(), key=lambda row: row['relation_pk']):
            self.entity_relations.write({
                'relation_pk': relation_row['relation_pk'],
                'relation_key': relation_row['relation_key'],
                'subject_entity_pk': relation_row['subject_entity_pk'],
                'subject_entity_key': relation_row['subject_entity_key'],
                'predicate': relation_row['predicate'],
                'object_entity_pk': relation_row['object_entity_pk'],
                'object_entity_key': relation_row['object_entity_key'],
                'relation_category': relation_row['relation_category'],
                'evidence_count': relation_row['evidence_count'],
                'sources': sorted(relation_row['sources']),
            })

        self.entity_relation_evidence.close()
        self.entity_relations.close()

    def _emit_annotation_relations(
        self,
        entity_pk: int,
        annotations: list[dict[str, Any]],
        record_class: str,
        raw_record_id: str | None,
    ) -> None:
        context = AnnotationContext(record_class=record_class, parent_type=None)
        evidence = collect_attributes(annotations, context, {'evidence'})

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

            rule = PredicateRule(
                predicate=disposition.predicate or ASSOCIATION_PREDICATE,
                relation_category=ASSOCIATION_CATEGORY,
            )
            self._write_relation_evidence(
                subject_entity_pk=entity_pk,
                predicate_rule=rule,
                object_entity_pk=object_pk,
                raw_record_id=raw_record_id,
                record_attributes=None,
                subject_attributes=None,
                object_attributes=None,
                evidence=evidence,
            )

    def _write_relation_evidence(
        self,
        subject_entity_pk: int,
        predicate_rule: PredicateRule,
        object_entity_pk: int,
        raw_record_id: str | None,
        record_attributes: list[dict[str, str | None]] | None,
        subject_attributes: list[dict[str, str | None]] | None,
        object_attributes: list[dict[str, str | None]] | None,
        evidence: list[dict[str, str | None]] | None,
    ) -> None:
        key = (
            subject_entity_pk,
            predicate_rule.predicate,
            object_entity_pk,
            predicate_rule.relation_category,
        )
        relation_row = self.relation_index.get(key)
        if relation_row is None:
            subject_entity_key = self.entity_key_map.get(subject_entity_pk, '')
            object_entity_key = self.entity_key_map.get(object_entity_pk, '')
            relation_key = compute_relation_key(
                subject_entity_key,
                predicate_rule.predicate,
                object_entity_key,
                predicate_rule.relation_category,
            )
            relation_row = {
                'relation_pk': self.next_relation_pk,
                'relation_key': relation_key,
                'subject_entity_pk': subject_entity_pk,
                'subject_entity_key': subject_entity_key,
                'predicate': predicate_rule.predicate,
                'object_entity_pk': object_entity_pk,
                'object_entity_key': object_entity_key,
                'relation_category': predicate_rule.relation_category,
                'evidence_count': 0,
                'sources': set(),
            }
            self.relation_index[key] = relation_row
            self.next_relation_pk += 1

        relation_row['evidence_count'] += 1
        relation_row['sources'].add(self.source)

        self.entity_relation_evidence.write({
            'source': self.source,
            'relation_evidence_pk': self.next_relation_evidence_pk,
            'relation_pk': relation_row['relation_pk'],
            'relation_key': relation_row['relation_key'],
            'subject_entity_key': relation_row['subject_entity_key'],
            'predicate': relation_row['predicate'],
            'object_entity_key': relation_row['object_entity_key'],
            'relation_category': relation_row['relation_category'],
            'raw_record_id': raw_record_id or '',
            'record_attributes': record_attributes,
            'subject_attributes': subject_attributes,
            'object_attributes': object_attributes,
            'evidence': evidence,
        })
        self.next_relation_evidence_pk += 1


class RelationBuilder(RelationWriterBase):
    """Build relations from a bounded filtered silver part."""

    def __init__(
        self,
        source: str,
        silver_dir: Path,
        output_dir: Path,
        entity_map: dict[str, int],
        entity_key_map: dict[int, str],
        occurrence_map: dict[str, int],
        batch_size: int = 10_000,
    ) -> None:
        super().__init__(source, silver_dir, output_dir, entity_map, entity_key_map, batch_size)
        self.occurrence_map = occurrence_map

    def convert(self) -> None:
        base = silver_table_dir(self.silver_dir)

        occ = _read_or_empty(base / 'entity_occurrence.parquet')
        ids = _read_or_empty(base / 'entity_identifier.parquet')
        anns = _read_or_empty(base / 'entity_annotation.parquet')
        memberships = _read_or_empty(base / 'membership.parquet')
        membership_anns = _read_or_empty(base / 'membership_annotation.parquet')

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
            raw_record_id = row.get('raw_record_id')
            if record_class == 'interaction_relation':
                self._project_interaction_from_tables(row, occurrence_rows, membership_rows_by_parent.get(occurrence_id, []))
                continue

            parent_pk = self.occurrence_map.get(occurrence_id)
            if parent_pk is None:
                continue
            if record_class == 'membership_relation':
                self._project_memberships_from_tables(parent_pk, row, occurrence_rows, membership_rows_by_parent.get(occurrence_id, []))
            self._emit_annotation_relations(parent_pk, row.get('annotations') or [], record_class, raw_record_id)

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
        record_attributes = collect_attributes(row_annotations, record_context, {'record_attribute'})
        evidence = _merge_attribute_lists(
            collect_attributes(row_annotations, record_context, {'evidence'}),
            _merge_attribute_lists(
                collect_attributes(ordered_participants[0]['membership_annotations'], subject_context, {'evidence'}),
                collect_attributes(ordered_participants[1]['membership_annotations'], object_context, {'evidence'}),
            ),
        )
        subject_attributes = collect_attributes(ordered_participants[0]['membership_annotations'], subject_context, {'subject_attribute'})
        object_attributes = collect_attributes(ordered_participants[1]['membership_annotations'], object_context, {'object_attribute'})
        self._write_relation_evidence(
            subject_entity_pk=ordered_participants[0]['pk'],
            predicate_rule=rule,
            object_entity_pk=ordered_participants[1]['pk'],
            raw_record_id=raw_record_id,
            record_attributes=record_attributes,
            subject_attributes=subject_attributes,
            object_attributes=object_attributes,
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
            subject_pk = member_pk if member_is_subject else parent_pk
            object_pk = parent_pk if member_is_subject else member_pk
            membership_annotations = membership.get('annotations') or []
            subject_attributes = collect_attributes(
                membership_annotations,
                AnnotationContext(
                    record_class='membership_relation',
                    parent_type=parent_type,
                    is_membership=True,
                    participant_side='subject' if member_is_subject else 'record',
                ),
                {'subject_attribute'} if member_is_subject else set(),
            )
            object_attributes = collect_attributes(
                membership_annotations,
                AnnotationContext(
                    record_class='membership_relation',
                    parent_type=parent_type,
                    is_membership=True,
                    participant_side='object' if not member_is_subject else 'record',
                ),
                {'object_attribute'} if not member_is_subject else set(),
            )
            membership_evidence = collect_attributes(
                membership_annotations,
                AnnotationContext(record_class='membership_relation', parent_type=parent_type, is_membership=True),
                {'evidence'},
            )
            self._write_relation_evidence(
                subject_entity_pk=subject_pk,
                predicate_rule=rule,
                object_entity_pk=object_pk,
                raw_record_id=raw_record_id,
                record_attributes=None,
                subject_attributes=subject_attributes,
                object_attributes=object_attributes,
                evidence=_merge_attribute_lists(parent_evidence, membership_evidence),
            )


def build_relations(
    silver_dir: str | Path,
    entity_map_path: str | Path,
    output_dir: str | Path,
    source_name: str,
    *,
    partition_config: GoldPartitionConfig | None = None,
) -> dict[str, Any]:
    """Build source-level gold relations with bounded parent-part processing."""
    cfg = partition_config or GoldPartitionConfig()
    silver_dir = Path(silver_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not has_silver_tables(silver_dir):
        raise FileNotFoundError(f'silver tables not found under {silver_dir}')

    entity_map_path = Path(entity_map_path)
    entity_map_dataset = _resolve_sibling_dataset(entity_map_path, 'entity_map')
    occurrence_map_dataset = _resolve_sibling_dataset(entity_map_path, 'entity_occurrence_map')
    entity_dataset = _resolve_sibling_dataset(entity_map_path, 'entity')
    if entity_map_dataset is None or occurrence_map_dataset is None or entity_dataset is None:
        raise FileNotFoundError(
            'partitioned entity outputs not found. Expected sibling datasets '
            '`entity`, `entity_map`, and `entity_occurrence_map` next to entity_map_path.'
        )

    work_dir = output_dir / '_work_relations'
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    con = duckdb.connect()
    try:
        _configure_duckdb(con, output_dir, cfg)
        _register_hash_functions(con)
        silver_base = silver_table_dir(silver_dir)
        requested_part_count = cfg.part_count
        cfg, input_bytes = effective_partition_config_for_paths(
            cfg,
            [silver_base, entity_dataset, occurrence_map_dataset, entity_map_dataset],
        )
        started_at = time.perf_counter()
        _log_relations(
            source_name,
            f'start silver={silver_dir} output={output_dir} '
            f'parts={cfg.part_count}/{requested_part_count} '
            f'min_part_size={cfg.min_part_size_bytes} input_bytes={input_bytes}',
        )

        for parent_part in range(cfg.part_count):
            part_started_at = time.perf_counter()
            part_dir = work_dir / 'silver_parts' / f'parent_part={parent_part:05d}'
            _log_relations(
                source_name,
                f'parent_part {parent_part + 1}/{cfg.part_count} filter start',
            )
            parent_count = _write_filtered_silver_parent_part(
                con,
                silver_base=silver_base,
                part_state_dir=part_dir,
                parent_part=parent_part,
                cfg=cfg,
            )
            if parent_count == 0:
                _log_relations(
                    source_name,
                    f'parent_part {parent_part + 1}/{cfg.part_count} empty '
                    f'in {_elapsed(part_started_at)}',
                )
                continue

            _log_relations(
                source_name,
                f'parent_part {parent_part + 1}/{cfg.part_count} load maps '
                f'parents={parent_count}',
            )
            maps = _load_relation_part_maps(
                con,
                part_state_dir=part_dir,
                entity_dataset=entity_dataset,
                occurrence_map_dataset=occurrence_map_dataset,
                entity_map_dataset=entity_map_dataset,
                source_name=source_name,
            )
            if not maps['occurrence_map']:
                _log_relations(
                    source_name,
                    f'parent_part {parent_part + 1}/{cfg.part_count} no occurrence map '
                    f'in {_elapsed(part_started_at)}',
                )
                continue

            part_output_dir = work_dir / 'relation_part_outputs' / f'parent_part={parent_part:05d}'
            part_output_dir.mkdir(parents=True, exist_ok=True)
            _log_relations(
                source_name,
                f'parent_part {parent_part + 1}/{cfg.part_count} convert '
                f'occurrence_map={len(maps["occurrence_map"])} '
                f'entity_map={len(maps["entity_map"])}',
            )
            builder = RelationBuilder(
                source=source_name,
                silver_dir=part_dir,
                output_dir=part_output_dir,
                entity_map=maps['entity_map'],
                entity_key_map=maps['entity_key_map'],
                occurrence_map=maps['occurrence_map'],
            )
            try:
                builder.convert()
            finally:
                builder.close()

            evidence_path = part_output_dir / 'entity_relation_evidence.parquet'
            if evidence_path.exists():
                evidence = pl.read_parquet(evidence_path)
                if not evidence.is_empty():
                    evidence = _add_relation_part_columns(evidence, cfg)
                    (work_dir / 'relation_evidence').mkdir(parents=True, exist_ok=True)
                    _write_frame_partition_files(
                        evidence,
                        work_dir / 'relation_evidence',
                        part_column='relation_part',
                        part_count=cfg.part_count,
                        filename=f'parent_part={parent_part:05d}.parquet',
                    )
                    _log_relations(
                        source_name,
                        f'parent_part {parent_part + 1}/{cfg.part_count} '
                        f'evidence_rows={evidence.height} in {_elapsed(part_started_at)}',
                    )
                else:
                    _log_relations(
                        source_name,
                        f'parent_part {parent_part + 1}/{cfg.part_count} no evidence '
                        f'in {_elapsed(part_started_at)}',
                    )
            else:
                _log_relations(
                    source_name,
                    f'parent_part {parent_part + 1}/{cfg.part_count} no evidence file '
                    f'in {_elapsed(part_started_at)}',
                )

        _log_relations(source_name, 'finalize partitioned outputs start')
        finalize_started_at = time.perf_counter()
        row_counts = _finalize_relation_outputs(
            con,
            output_dir=output_dir,
            work_dir=work_dir,
            entity_dataset=entity_dataset,
            cfg=cfg,
        )
        _log_relations(
            source_name,
            f'finalize done rows={row_counts} in {_elapsed(finalize_started_at)}',
        )
        _write_gold_relation_manifest(output_dir, source_name=source_name, cfg=cfg, row_counts=row_counts)
        _log_relations(source_name, f'done rows={row_counts} in {_elapsed(started_at)}')
        return row_counts
    finally:
        con.close()
        shutil.rmtree(work_dir, ignore_errors=True)


def _write_filtered_silver_parent_part(
    con: duckdb.DuckDBPyConnection,
    *,
    silver_base: Path,
    part_state_dir: Path,
    parent_part: int,
    cfg: GoldPartitionConfig,
) -> int:
    if part_state_dir.exists():
        shutil.rmtree(part_state_dir)
    part_state_dir.mkdir(parents=True, exist_ok=True)

    occurrence_path = _silver_table_path(silver_base, 'entity_occurrence')
    membership_path = _silver_table_path(silver_base, 'membership')
    if not occurrence_path.exists():
        raise FileNotFoundError(f'missing silver entity_occurrence table: {occurrence_path}')

    con.execute('drop table if exists _parent_occurrence_ids')
    con.execute(f"""
        create temp table _parent_occurrence_ids as
        select distinct try_cast(occurrence_id as varchar) as occurrence_id
        from {_read_parquet_dataset_sql(occurrence_path)}
        where occurrence_id is not null
          and (parent_occurrence_id is null or try_cast(parent_occurrence_id as varchar) = '')
          and stable_part(try_cast(occurrence_id as varchar), {cfg.bucket_count}, {cfg.part_count}) = {parent_part}
    """)
    parent_count = int(con.execute('select count(*) from _parent_occurrence_ids').fetchone()[0])
    if parent_count == 0:
        return 0

    if membership_path.exists():
        _copy_query(con, f"""
            select *
            from {_read_parquet_dataset_sql(membership_path)}
            where try_cast(parent_occurrence_id as varchar) in (select occurrence_id from _parent_occurrence_ids)
        """, part_state_dir / 'membership.parquet')
    else:
        _copy_query(con, "select null::varchar as membership_id, null::varchar as parent_occurrence_id, null::varchar as member_occurrence_id where false", part_state_dir / 'membership.parquet')

    con.execute('drop table if exists _included_occurrence_ids')
    con.execute(f"""
        create temp table _included_occurrence_ids as
        select occurrence_id from _parent_occurrence_ids
        union
        select distinct try_cast(member_occurrence_id as varchar) as occurrence_id
        from read_parquet('{_sql_path(part_state_dir / 'membership.parquet')}')
        where member_occurrence_id is not null
    """)

    _copy_query(con, f"""
        select *
        from {_read_parquet_dataset_sql(occurrence_path)}
        where try_cast(occurrence_id as varchar) in (select occurrence_id from _included_occurrence_ids)
    """, part_state_dir / 'entity_occurrence.parquet')

    for table in ['entity_identifier', 'entity_annotation']:
        source_path = _silver_table_path(silver_base, table)
        if source_path.exists():
            _copy_query(con, f"""
                select *
                from {_read_parquet_dataset_sql(source_path)}
                where try_cast(occurrence_id as varchar) in (select occurrence_id from _included_occurrence_ids)
            """, part_state_dir / f'{table}.parquet')
        else:
            _copy_query(con, 'select null::varchar as occurrence_id where false', part_state_dir / f'{table}.parquet')

    con.execute('drop table if exists _included_membership_ids')
    con.execute(f"""
        create temp table _included_membership_ids as
        select distinct try_cast(membership_id as varchar) as membership_id
        from read_parquet('{_sql_path(part_state_dir / 'membership.parquet')}')
        where membership_id is not null
    """)
    membership_annotation_path = _silver_table_path(silver_base, 'membership_annotation')
    if membership_annotation_path.exists():
        _copy_query(con, f"""
            select *
            from {_read_parquet_dataset_sql(membership_annotation_path)}
            where try_cast(membership_id as varchar) in (select membership_id from _included_membership_ids)
        """, part_state_dir / 'membership_annotation.parquet')
    else:
        _copy_query(con, 'select null::varchar as membership_id, null::varchar as term, null::varchar as value, null::varchar as unit where false', part_state_dir / 'membership_annotation.parquet')

    return parent_count


def _load_relation_part_maps(
    con: duckdb.DuckDBPyConnection,
    *,
    part_state_dir: Path,
    entity_dataset: Path,
    occurrence_map_dataset: Path,
    entity_map_dataset: Path,
    source_name: str,
) -> dict[str, dict]:
    con.execute('drop table if exists _needed_occurrence_ids')
    con.execute(f"""
        create temp table _needed_occurrence_ids as
        select distinct try_cast(occurrence_id as varchar) as occurrence_id
        from read_parquet('{_sql_path(part_state_dir / 'entity_occurrence.parquet')}')
        where occurrence_id is not null
    """)

    occ_rows = con.execute(f"""
        select try_cast(om.occurrence_id as varchar), try_cast(om.entity_pk as bigint), try_cast(om.entity_key as varchar)
        from {_read_dataset_sql(occurrence_map_dataset)} om
        join _needed_occurrence_ids n on n.occurrence_id = try_cast(om.occurrence_id as varchar)
        where om.entity_pk is not null
    """).fetchall()
    occurrence_map = {row[0]: int(row[1]) for row in occ_rows if row[0] is not None and row[1] is not None}
    needed_entity_pks = {int(row[1]) for row in occ_rows if row[1] is not None}

    ontology_fingerprints = _ontology_fingerprints_in_part(part_state_dir, source_name)
    entity_map: dict[str, int] = {}
    if ontology_fingerprints:
        _create_value_table(con, '_needed_fingerprints', '_fingerprint', ontology_fingerprints)
        rows = con.execute(f"""
            select try_cast(em._fingerprint as varchar), try_cast(em.entity_pk as bigint)
            from {_read_dataset_sql(entity_map_dataset)} em
            join _needed_fingerprints f on f._fingerprint = try_cast(em._fingerprint as varchar)
            where em.entity_pk is not null
        """).fetchall()
        entity_map = {row[0]: int(row[1]) for row in rows if row[0] is not None and row[1] is not None}
        needed_entity_pks.update(entity_map.values())

    entity_key_map: dict[int, str] = {}
    if needed_entity_pks:
        _create_value_table(con, '_needed_entity_pks', 'entity_pk', {str(v) for v in needed_entity_pks})
        rows = con.execute(f"""
            select try_cast(e.entity_pk as bigint), try_cast(e.entity_key as varchar)
            from {_read_dataset_sql(entity_dataset)} e
            join _needed_entity_pks p on try_cast(p.entity_pk as bigint) = try_cast(e.entity_pk as bigint)
            where e.entity_key is not null
        """).fetchall()
        entity_key_map = {int(row[0]): row[1] for row in rows if row[0] is not None and row[1] is not None}

    return {
        'occurrence_map': occurrence_map,
        'entity_map': entity_map,
        'entity_key_map': entity_key_map,
    }


def _finalize_relation_outputs(
    con: duckdb.DuckDBPyConnection,
    *,
    output_dir: Path,
    work_dir: Path,
    entity_dataset: Path,
    cfg: GoldPartitionConfig,
) -> dict[str, int]:
    evidence_glob = _glob_or_none(work_dir / 'relation_evidence')
    for name in ['entity_relation', 'entity_relation_evidence']:
        path = output_dir / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    (output_dir / '_state' / 'relation_key_registry').mkdir(parents=True, exist_ok=True)

    if evidence_glob is None:
        for relation_part in range(cfg.part_count):
            _copy_part_query(
                con,
                _empty_relation_query(relation_part),
                output_dir / 'entity_relation',
                relation_part,
                cfg,
            )
            _copy_part_query(
                con,
                _empty_relation_evidence_query(relation_part),
                output_dir / 'entity_relation_evidence',
                relation_part,
                cfg,
            )
        return {'relation_count': 0, 'relation_evidence_count': 0}

    _load_or_create_relation_registry(con, output_dir)
    for relation_part in range(cfg.part_count):
        _create_part_temp_table(
            con,
            table_name='relation_evidence_part',
            root=work_dir / 'relation_evidence',
            fallback_glob=evidence_glob,
            part_column='relation_part',
            part=relation_part,
            extra_filter='relation_key is not null',
        )
        max_pk = int(con.execute('select coalesce(max(relation_pk), 0) from relation_key_registry').fetchone()[0])
        con.execute('drop table if exists _new_relation_keys')
        con.execute("""
            create temp table _new_relation_keys as
            select distinct relation_key, relation_bucket, relation_part
            from relation_evidence_part
            where relation_key not in (select relation_key from relation_key_registry)
        """)
        con.execute(f"""
            insert into relation_key_registry(relation_key, relation_pk, relation_bucket, relation_part)
            select
                relation_key,
                {max_pk} + row_number() over(order by relation_key) as relation_pk,
                relation_bucket,
                relation_part
            from _new_relation_keys
        """)

    for relation_part in range(cfg.part_count):
        _copy_part_query(
            con,
            f"select relation_key, relation_pk, relation_bucket, relation_part from relation_key_registry where relation_part = {relation_part}",
            output_dir / '_state' / 'relation_key_registry',
            relation_part,
            cfg,
        )

    relation_count = 0
    evidence_count = 0
    evidence_offset = 0
    for relation_part in range(cfg.part_count):
        _create_part_temp_table(
            con,
            table_name='relation_evidence_part',
            root=work_dir / 'relation_evidence',
            fallback_glob=evidence_glob,
            part_column='relation_part',
            part=relation_part,
            extra_filter='relation_key is not null',
        )
        relation_query = f"""
            with needed_entity_keys as (
                select subject_entity_key as entity_key from relation_evidence_part
                union
                select object_entity_key as entity_key from relation_evidence_part
            ),
            entity_lookup as (
                select
                    try_cast(e.entity_key as varchar) as entity_key,
                    try_cast(e.entity_pk as bigint) as entity_pk
                from {_read_dataset_sql(entity_dataset)} e
                join needed_entity_keys n on n.entity_key = try_cast(e.entity_key as varchar)
            ),
            grouped as (
                select
                    r.relation_pk,
                    e.relation_key,
                    first(e.subject_entity_key order by e.subject_entity_key nulls last) as subject_entity_key,
                    first(e.predicate order by e.predicate nulls last) as predicate,
                    first(e.object_entity_key order by e.object_entity_key nulls last) as object_entity_key,
                    first(e.relation_category order by e.relation_category nulls last) as relation_category,
                    count(*)::bigint as evidence_count,
                    list_sort(list_distinct(list(e.source) filter (where e.source is not null))) as sources,
                    r.relation_bucket,
                    r.relation_part
                from relation_evidence_part e
                join relation_key_registry r using(relation_key)
                group by r.relation_pk, e.relation_key, r.relation_bucket, r.relation_part
            )
            select
                g.relation_pk,
                g.relation_key,
                sm.entity_pk as subject_entity_pk,
                g.subject_entity_key,
                g.predicate,
                om.entity_pk as object_entity_pk,
                g.object_entity_key,
                g.relation_category,
                g.evidence_count,
                g.sources,
                g.relation_bucket,
                g.relation_part
            from grouped g
            left join entity_lookup sm on sm.entity_key = g.subject_entity_key
            left join entity_lookup om on om.entity_key = g.object_entity_key
            order by g.relation_key
        """
        relation_count += _copy_part_query(con, relation_query, output_dir / 'entity_relation', relation_part, cfg)

        evidence_query = f"""
            select
                {evidence_offset} + row_number() over(order by e.source, e.relation_key, e.raw_record_id) as relation_evidence_pk,
                r.relation_pk,
                e.relation_key,
                e.source,
                e.raw_record_id,
                e.record_attributes,
                e.subject_attributes,
                e.object_attributes,
                e.evidence,
                e.subject_entity_key,
                e.predicate,
                e.object_entity_key,
                e.relation_category,
                r.relation_bucket,
                r.relation_part
            from relation_evidence_part e
            join relation_key_registry r using(relation_key)
            order by e.source, e.relation_key, e.raw_record_id
        """
        part_count = _copy_part_query(con, evidence_query, output_dir / 'entity_relation_evidence', relation_part, cfg)
        evidence_count += part_count
        evidence_offset += part_count

    return {'relation_count': relation_count, 'relation_evidence_count': evidence_count}


def reduce_relations_from_evidence(
    relation_evidence: pl.DataFrame,
    *,
    entity_pk_map: pl.DataFrame,
    relation_pk_map: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Compatibility helper for small in-memory fixtures."""
    if relation_evidence.is_empty():
        return pl.DataFrame({
            'relation_pk': pl.Series([], dtype=pl.Int64),
            'relation_key': pl.Series([], dtype=pl.Utf8),
            'subject_entity_pk': pl.Series([], dtype=pl.Int64),
            'subject_entity_key': pl.Series([], dtype=pl.Utf8),
            'predicate': pl.Series([], dtype=pl.Utf8),
            'object_entity_pk': pl.Series([], dtype=pl.Int64),
            'object_entity_key': pl.Series([], dtype=pl.Utf8),
            'relation_category': pl.Series([], dtype=pl.Utf8),
            'evidence_count': pl.Series([], dtype=pl.Int64),
            'sources': pl.Series([], dtype=pl.List(pl.Utf8)),
        })

    subject_pk = entity_pk_map.select([
        pl.col('entity_key').alias('subject_entity_key'),
        pl.col('entity_pk').alias('subject_entity_pk'),
    ])
    object_pk = entity_pk_map.select([
        pl.col('entity_key').alias('object_entity_key'),
        pl.col('entity_pk').alias('object_entity_pk'),
    ])
    reduced = (
        relation_evidence
        .group_by([
            'relation_key',
            'subject_entity_key',
            'predicate',
            'object_entity_key',
            'relation_category',
        ])
        .agg([
            pl.len().cast(pl.Int64).alias('evidence_count'),
            pl.col('source').drop_nulls().unique().sort().alias('sources'),
        ])
        .join(subject_pk, on='subject_entity_key', how='left')
        .join(object_pk, on='object_entity_key', how='left')
        .sort('relation_key')
    )
    if relation_pk_map is not None and not relation_pk_map.is_empty():
        reduced = reduced.join(
            relation_pk_map.select(['relation_key', 'relation_pk']),
            on='relation_key',
            how='left',
        )
    if 'relation_pk' not in reduced.columns:
        reduced = reduced.with_row_index('relation_pk', offset=1)
    elif reduced['relation_pk'].null_count() > 0:
        max_pk = int(reduced['relation_pk'].max() or 0)
        reduced = (
            reduced
            .sort('relation_key')
            .with_row_index('_new_relation_pk', offset=max_pk + 1)
            .with_columns(
                pl.coalesce(['relation_pk', '_new_relation_pk']).cast(pl.Int64).alias('relation_pk')
            )
            .drop('_new_relation_pk')
        )

    return reduced.select([
        'relation_pk',
        'relation_key',
        'subject_entity_pk',
        'subject_entity_key',
        'predicate',
        'object_entity_pk',
        'object_entity_key',
        'relation_category',
        'evidence_count',
        'sources',
    ])


def _merge_attribute_lists(
    first: list[dict[str, str | None]] | None,
    second: list[dict[str, str | None]] | None,
) -> list[dict[str, str | None]] | None:
    rows = [*(first or []), *(second or [])]
    return rows or None


def _add_relation_part_columns(frame: pl.DataFrame, cfg: GoldPartitionConfig) -> pl.DataFrame:
    return frame.with_columns([
        pl.col('relation_key').map_elements(lambda v: _stable_bucket_py(v, cfg.bucket_count), return_dtype=pl.Int64).alias('relation_bucket'),
        pl.col('relation_key').map_elements(lambda v: _stable_part_py(v, cfg.bucket_count, cfg.part_count), return_dtype=pl.Int64).alias('relation_part'),
    ])


def _load_or_create_relation_registry(con: duckdb.DuckDBPyConnection, output_dir: Path) -> None:
    con.execute('drop table if exists relation_key_registry')
    registry_glob = _glob_or_none(output_dir / '_state' / 'relation_key_registry')
    if registry_glob is None:
        con.execute('''
            create temp table relation_key_registry(
                relation_key varchar,
                relation_pk bigint,
                relation_bucket bigint,
                relation_part bigint
            )
        ''')
    else:
        con.execute(f"""
            create temp table relation_key_registry as
            select
                try_cast(relation_key as varchar) as relation_key,
                try_cast(relation_pk as bigint) as relation_pk,
                try_cast(relation_bucket as bigint) as relation_bucket,
                try_cast(relation_part as bigint) as relation_part
            from read_parquet('{_sql_path(registry_glob)}', union_by_name=true)
        """)


def _resolve_sibling_dataset(reference: Path, name: str) -> Path | None:
    base = reference.parent
    candidates = [
        base / name,
        base / f'{name}.parquet',
        reference.with_suffix('') if reference.name.startswith(name) else base / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _read_dataset_sql(path: Path) -> str:
    if path.is_dir():
        return f"read_parquet('{_sql_path(path / '**' / '*.parquet')}', union_by_name=true, hive_partitioning=true)"
    return f"read_parquet('{_sql_path(path)}', union_by_name=true)"


def _read_or_empty(path: Path) -> pl.DataFrame:
    if path.exists():
        return pl.read_parquet(path)
    return pl.DataFrame()


def _ontology_fingerprints_in_part(part_state_dir: Path, source_name: str) -> set[str]:
    annotation_path = part_state_dir / 'entity_annotation.parquet'
    if not annotation_path.exists():
        return set()
    annotations = pl.read_parquet(annotation_path)
    out: set[str] = set()
    for row in annotations.iter_rows(named=True):
        desc = extract_ontology_entity_description(
            {
                'term': string_or_none(row.get('term')),
                'value': string_or_none(row.get('value')),
                'unit': string_or_none(row.get('unit')),
            },
            source_name,
        )
        if desc is not None:
            out.add(desc['_fingerprint'])
    return out


def _create_value_table(
    con: duckdb.DuckDBPyConnection,
    table: str,
    column: str,
    values: Iterable[str],
) -> None:
    con.execute(f'drop table if exists {table}')
    con.execute(f'create temp table {table}({column} varchar)')
    values = sorted({str(v) for v in values if v is not None})
    if values:
        con.executemany(f'insert into {table} values (?)', [(v,) for v in values])


def _empty_relation_query(relation_part: int = 0) -> str:
    return f"""
        select
            null::bigint as relation_pk,
            null::varchar as relation_key,
            null::bigint as subject_entity_pk,
            null::varchar as subject_entity_key,
            null::varchar as predicate,
            null::bigint as object_entity_pk,
            null::varchar as object_entity_key,
            null::varchar as relation_category,
            null::bigint as evidence_count,
            []::varchar[] as sources,
            null::bigint as relation_bucket,
            {relation_part}::bigint as relation_part
        where false
    """


def _empty_relation_evidence_query(relation_part: int = 0) -> str:
    return f"""
        select
            null::bigint as relation_evidence_pk,
            null::bigint as relation_pk,
            null::varchar as relation_key,
            null::varchar as source,
            null::varchar as raw_record_id,
            []::struct(term varchar, "value" varchar, unit varchar)[] as record_attributes,
            []::struct(term varchar, "value" varchar, unit varchar)[] as subject_attributes,
            []::struct(term varchar, "value" varchar, unit varchar)[] as object_attributes,
            []::struct(term varchar, "value" varchar, unit varchar)[] as evidence,
            null::varchar as subject_entity_key,
            null::varchar as predicate,
            null::varchar as object_entity_key,
            null::varchar as relation_category,
            null::bigint as relation_bucket,
            {relation_part}::bigint as relation_part
        where false
    """


def _write_gold_relation_manifest(
    output_dir: Path,
    *,
    source_name: str,
    cfg: GoldPartitionConfig,
    row_counts: dict[str, int],
) -> None:
    manifest = {
        'layer': 'gold',
        'kind': 'relations',
        'source': source_name,
        'bucket_algorithm': 'stable_u64_sha256_mod_v1',
        'relation_key_algorithm': 'sha256_v1',
        'bucket_count': cfg.bucket_count,
        'part_count': cfg.part_count,
        'min_part_size_bytes': cfg.min_part_size_bytes,
        'relation_bucket_count': cfg.bucket_count,
        'relation_part_count': cfg.part_count,
        'parent_bucket_count': cfg.bucket_count,
        'parent_part_count': cfg.part_count,
        'outputs': {
            'entity_relation': 'entity_relation/',
            'entity_relation_evidence': 'entity_relation_evidence/',
        },
        'row_counts': row_counts,
    }
    (output_dir / 'manifest.json').write_text(__import__('json').dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _elapsed(started_at: float) -> str:
    elapsed = time.perf_counter() - started_at
    if elapsed < 60:
        return f'{elapsed:.1f}s'
    minutes, seconds = divmod(elapsed, 60)
    return f'{int(minutes)}m {seconds:.0f}s'


def _log_relations(source_name: str, message: str) -> None:
    print(f'[gold:relations:{source_name}] {message}', flush=True)
