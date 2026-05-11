from __future__ import annotations

from typing import Any
from pathlib import Path

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
    """Build relations from silver tables."""

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
        print(f'[{self.source}] building relations from silver tables in {base}')

        occ = pl.read_parquet(base / 'entity_occurrence.parquet')
        ids = pl.read_parquet(base / 'entity_identifier.parquet')
        anns = pl.read_parquet(base / 'entity_annotation.parquet')
        memberships = pl.read_parquet(base / 'membership.parquet')
        membership_anns = pl.read_parquet(base / 'membership_annotation.parquet')

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
        if frame.is_empty():
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
        if frame.is_empty():
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


def _merge_attribute_lists(
    first: list[dict[str, str | None]] | None,
    second: list[dict[str, str | None]] | None,
) -> list[dict[str, str | None]] | None:
    rows = [*(first or []), *(second or [])]
    return rows or None


def reduce_relations_from_evidence(
    relation_evidence: pl.DataFrame,
    *,
    entity_pk_map: pl.DataFrame,
    relation_pk_map: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Project final source relation rows from source relation evidence facts."""
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


def build_relations(
    silver_dir: str | Path,
    entity_map_path: str | Path,
    output_dir: str | Path,
    source_name: str,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    entity_map_df = pl.read_parquet(entity_map_path)
    entity_map = dict(zip(
        entity_map_df['_fingerprint'].to_list(),
        entity_map_df['entity_pk'].to_list(),
        strict=True,
    ))

    # Load entity keys from entity.parquet
    entity_parquet_path = Path(entity_map_path).with_name('entity.parquet')
    entity_key_map: dict[int, str] = {}
    if entity_parquet_path.exists():
        entity_df = pl.read_parquet(entity_parquet_path)
        if 'entity_key' in entity_df.columns:
            entity_key_map = dict(zip(
                entity_df['entity_pk'].to_list(),
                entity_df['entity_key'].to_list(),
                strict=True,
            ))

    silver_dir = Path(silver_dir)
    if not has_silver_tables(silver_dir):
        raise FileNotFoundError(f'silver tables not found under {silver_dir}')

    occurrence_map_path = Path(entity_map_path).with_name('entity_occurrence_map.parquet')
    if not occurrence_map_path.exists():
        raise FileNotFoundError(
            f'entity occurrence map not found: {occurrence_map_path}. '
            'Run the entity build before building relations.'
        )

    occurrence_map_df = pl.read_parquet(occurrence_map_path)
    occurrence_map = dict(zip(
        occurrence_map_df['occurrence_id'].to_list(),
        occurrence_map_df['entity_pk'].to_list(),
        strict=True,
    ))
    builder = RelationBuilder(
        source=source_name,
        silver_dir=silver_dir,
        output_dir=output_dir,
        entity_map=entity_map,
        entity_key_map=entity_key_map,
        occurrence_map=occurrence_map,
    )
    try:
        builder.convert()
    finally:
        builder.close()

    relation_evidence = pl.read_parquet(output_dir / 'entity_relation_evidence.parquet')
    existing_relations = pl.read_parquet(output_dir / 'entity_relation.parquet')
    reduced_relations = reduce_relations_from_evidence(
        relation_evidence,
        entity_pk_map=entity_df.select(['entity_key', 'entity_pk']),
        relation_pk_map=existing_relations.select(['relation_key', 'relation_pk']),
    )
    reduced_relations.write_parquet(output_dir / 'entity_relation.parquet')

    return {
        'relation_count': len(builder.relation_index),
        'relation_evidence_count': builder.next_relation_evidence_pk - 1,
    }
