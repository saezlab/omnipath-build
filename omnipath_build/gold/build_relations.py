from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from omnipath_build.gold.utils.entity_extraction import (
    ENTITY_RELATION_EVIDENCE_SCHEMA,
    ENTITY_RELATION_SCHEMA,
    BufferedParquetWriter,
    collect_attributes,
    compute_entity_fingerprint,
    extract_entity_description,
    extract_ontology_entity_description,
)
from omnipath_build.gold.utils.schema import (
    AnnotationContext,
    PredicateRule,
    classify_annotation,
    classify_silver_record,
    is_pure_ontology_term_annotation,
    materialize_ontology_object,
    order_interaction_participants,
    predicate_for_interaction,
    predicate_for_membership,
    string_or_none,
)


class RelationBuilder:
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

    def convert(self) -> None:
        parquet_files = sorted(
            path for path in self.silver_dir.glob('*.parquet') if path.name != 'resource.parquet'
        )
        print(f'[{self.source}] building relations from {len(parquet_files)} silver parquet(s) from {self.silver_dir}')

        for parquet_path in parquet_files:
            print(f'[{self.source}] reading {parquet_path.name}')
            pf = pq.ParquetFile(parquet_path)
            for batch in pf.iter_batches(batch_size=self.batch_size):
                for row in batch.to_pylist():
                    self._process_row(row)

    def _lookup_entity_pk(self, row: dict[str, Any], record_class: str) -> int | None:
        desc = extract_entity_description(row, self.source, record_class)
        if desc is None:
            return None
        pk = self.entity_map.get(desc['_fingerprint'])
        if pk is None:
            print(f'[{self.source}] WARNING: entity not found in map: {desc["_fingerprint"][:16]}... ({desc.get("entity_type")})')
        return pk

    def _process_row(self, row: dict[str, Any]) -> None:
        record_class = classify_silver_record(row)
        if record_class == 'ignored':
            return
        if record_class == 'ontology_term_only':
            return
        if record_class == 'interaction_relation':
            self._project_interaction(row)
            return

        parent_pk = self._lookup_entity_pk(row, record_class)
        if parent_pk is None:
            return

        if record_class == 'membership_relation':
            self._project_memberships(parent_pk, row)

        # Annotation relations for all non-ignored, non-ontology-only entities
        self._emit_annotation_relations(parent_pk, row.get('annotations') or [], record_class)

    def _project_interaction(self, row: dict[str, Any]) -> None:
        participants: list[dict[str, Any]] = []
        for membership in row.get('membership') or []:
            member_row = membership.get('member') or {}
            member_class = classify_silver_record(member_row)
            if member_class == 'ignored':
                member_class = 'entity_only'
            member_pk = self._lookup_entity_pk(member_row, member_class)
            if member_pk is None:
                continue

            # Emit annotation relations for this member
            self._emit_annotation_relations(member_pk, member_row.get('annotations') or [], member_class)

            participants.append({
                'pk': member_pk,
                'membership_annotations': membership.get('annotations') or [],
            })

        ordered_participants = order_interaction_participants(participants)
        if len(ordered_participants) != 2:
            return

        row_type = string_or_none(row.get('type'))
        rule = predicate_for_interaction(row, ordered_participants)
        row_annotations = row.get('annotations') or []

        record_context = AnnotationContext(
            record_class='interaction_relation',
            parent_type=row_type,
            participant_side='record',
        )
        subject_context = AnnotationContext(
            record_class='interaction_relation',
            parent_type=row_type,
            participant_side='subject',
        )
        object_context = AnnotationContext(
            record_class='interaction_relation',
            parent_type=row_type,
            participant_side='object',
        )

        record_attributes = collect_attributes(row_annotations, record_context, {'record_attribute'})
        evidence = _merge_attribute_lists(
            collect_attributes(row_annotations, record_context, {'evidence'}),
            _merge_attribute_lists(
                collect_attributes(ordered_participants[0]['membership_annotations'], subject_context, {'evidence'}),
                collect_attributes(ordered_participants[1]['membership_annotations'], object_context, {'evidence'}),
            ),
        )
        subject_attributes = collect_attributes(
            ordered_participants[0]['membership_annotations'], subject_context, {'subject_attribute'}
        )
        object_attributes = collect_attributes(
            ordered_participants[1]['membership_annotations'], object_context, {'object_attribute'}
        )

        self._write_relation_evidence(
            subject_entity_pk=ordered_participants[0]['pk'],
            predicate_rule=rule,
            object_entity_pk=ordered_participants[1]['pk'],
            record_attributes=record_attributes,
            subject_attributes=subject_attributes,
            object_attributes=object_attributes,
            evidence=evidence,
        )

    def _project_memberships(self, parent_pk: int, row: dict[str, Any]) -> None:
        memberships = row.get('membership') or []
        parent_annotations = row.get('annotations') or []
        parent_evidence = collect_attributes(
            parent_annotations,
            AnnotationContext(record_class='membership_relation', parent_type=string_or_none(row.get('type'))),
            {'evidence'},
        )

        for membership in memberships:
            member_row = membership.get('member') or {}
            member_class = classify_silver_record(member_row)
            if member_class == 'ignored':
                member_class = 'entity_only'
            member_pk = self._lookup_entity_pk(member_row, member_class)
            if member_pk is None:
                continue

            # Emit annotation relations for this member
            self._emit_annotation_relations(member_pk, member_row.get('annotations') or [], member_class)

            rule = predicate_for_membership(string_or_none(row.get('type')), membership)
            member_is_subject = bool(membership.get('is_parent', False))
            subject_pk = member_pk if member_is_subject else parent_pk
            object_pk = parent_pk if member_is_subject else member_pk

            membership_annotations = membership.get('annotations') or []
            subject_attributes = collect_attributes(
                membership_annotations,
                AnnotationContext(
                    record_class='membership_relation',
                    parent_type=string_or_none(row.get('type')),
                    is_membership=True,
                    participant_side='subject' if member_is_subject else 'record',
                ),
                {'subject_attribute'} if member_is_subject else set(),
            )
            object_attributes = collect_attributes(
                membership_annotations,
                AnnotationContext(
                    record_class='membership_relation',
                    parent_type=string_or_none(row.get('type')),
                    is_membership=True,
                    participant_side='object' if not member_is_subject else 'record',
                ),
                {'object_attribute'} if not member_is_subject else set(),
            )
            membership_evidence = collect_attributes(
                membership_annotations,
                AnnotationContext(
                    record_class='membership_relation',
                    parent_type=string_or_none(row.get('type')),
                    is_membership=True,
                ),
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
    ))

    builder = RelationBuilder(
        source=source_name,
        silver_dir=Path(silver_dir),
        output_dir=output_dir,
        entity_map=entity_map,
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
