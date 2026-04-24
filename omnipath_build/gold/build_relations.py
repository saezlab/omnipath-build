from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import polars as pl
from omnipath_build.gold.utils.entity_extraction import (
    ENTITY_RELATION_EVIDENCE_SCHEMA,
    ENTITY_RELATION_SCHEMA,
    BufferedParquetWriter,
    collect_attributes,
    extract_ontology_entity_description,
)
from omnipath_build.gold.utils.schema import (
    AnnotationContext,
    CV_TERM_ENTITY_TYPE,
    INTERACTION_LIKE_TYPES,
    ONTOLOGY_IDENTIFIER_TERM,
    PredicateRule,
    classify_annotation,
    materialize_ontology_object,
    order_interaction_participants,
    predicate_for_interaction,
    predicate_for_membership,
    string_or_none,
)
from omnipath_build.silver.tables import has_silver_tables, silver_table_dir


class RelationWriterBase:
    def __init__(
        self,
        source: str,
        silver_dir: Path,
        output_dir: Path,
        entity_map: dict[str, int],
        batch_size: int = 10_000,
    ) -> None:
        self.source = source
        self.silver_dir = silver_dir
        self.output_dir = output_dir
        self.entity_map = entity_map
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
                'subject_entity_pk': relation_row['subject_entity_pk'],
                'predicate': relation_row['predicate'],
                'object_entity_pk': relation_row['object_entity_pk'],
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
                predicate=disposition.predicate or 'has_annotation',
                relation_category='annotation',
            )
            self._write_relation_evidence(
                subject_entity_pk=entity_pk,
                predicate_rule=rule,
                object_entity_pk=object_pk,
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
            relation_row = {
                'relation_pk': self.next_relation_pk,
                'subject_entity_pk': subject_entity_pk,
                'predicate': predicate_rule.predicate,
                'object_entity_pk': object_entity_pk,
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
        occurrence_map: dict[str, int],
        batch_size: int = 10_000,
    ) -> None:
        super().__init__(source, silver_dir, output_dir, entity_map, batch_size)
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
            self._emit_annotation_relations(parent_pk, row.get('annotations') or [], record_class)

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
            self._emit_annotation_relations(member_pk, member_row.get('annotations') or [], member_class)
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
            self._emit_annotation_relations(member_pk, member_row.get('annotations') or [], member_class)

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
        occurrence_map=occurrence_map,
    )
    try:
        builder.convert()
    finally:
        builder.close()

    return {
        'relation_count': len(builder.relation_index),
        'relation_evidence_count': builder.next_relation_evidence_pk - 1,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build entity relations from silver data using canonical entity PKs.')
    parser.add_argument('--silver-dir', type=Path, required=True, help='Directory containing silver parquet files.')
    parser.add_argument('--entity-map', type=Path, required=True, help='Path to entity_map.parquet from build_entities.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Output directory for relation parquet files.')
    parser.add_argument('--source-name', required=True, help='Source name for metadata.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_relations(
        silver_dir=args.silver_dir,
        entity_map_path=args.entity_map,
        output_dir=args.output_dir,
        source_name=args.source_name,
    )
    print(f'Relation build complete: {summary}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
