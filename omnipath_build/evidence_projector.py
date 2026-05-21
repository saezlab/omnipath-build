"""Shared evidence projection rules for the DuckDB/PostgreSQL load pipeline."""

from __future__ import annotations

from typing import Protocol
from dataclasses import dataclass
from collections.abc import Iterable

from omnipath_build.ingest.common import (
    entity_to_row,
    annotation_key,
    identifier_key,
    annotation_to_row,
    include_identifier,
    entity_evidence_key,
    extract_taxonomy_id,
    is_interaction_like,
    relation_evidence_key,
    membership_relation_spec,
    interaction_relation_spec,
    ontology_annotation_relation,
    interaction_relation_annotations,
)
from omnipath_build.relation_rules import ASSOCIATION_CATEGORY, string_or_none

@dataclass(frozen=True)
class ProjectionStats:
    """Counts produced while projecting source records into evidence rows."""

    source_rows: int
    entity_evidence: int
    relation_evidence: int
    identifiers: int
    annotations: int


class _RowWriter(Protocol):
    def write(self, row: dict[str, object]) -> None: ...


class _EvidenceWriters(Protocol):
    entity: _RowWriter
    identifier: _RowWriter
    entity_annotation: _RowWriter
    relation_annotation: _RowWriter
    annotation: _RowWriter
    relation: _RowWriter
    annotation_relation: _RowWriter


class EvidenceProjectorBase:
    """Flatten silver entity trees into source-shaped evidence rows."""

    def __init__(
        self,
        *,
        chunk_size: int = 100_000,
    ) -> None:
        self.chunk_size = chunk_size

    def _flatten_entity_tree(
        self,
        entity: object,
        *,
        source: str,
        dataset: str,
        row_id: int,
        occurrence_id: str,
        parent_entity_evidence_id: str | None,
        entity_role: str,
        writers: _EvidenceWriters,
        seen_annotations: set[tuple[str, str, str | None, str | None]],
        stats: _MutableProjectionStats,
    ) -> None:
        row = entity_to_row(entity)
        entity_type = string_or_none(row.get('type'))
        memberships = list(getattr(entity, 'membership', None) or [])
        relation_only_interaction = (
            is_interaction_like(entity_type)
            and sum(
                1
                for membership in memberships
                if getattr(membership, 'member', None) is not None
            )
            == 2
        )

        entity_evidence_id = None
        if not relation_only_interaction:
            entity_evidence_id = entity_evidence_key(
                source,
                dataset,
                row_id,
                occurrence_id,
            )
            writers.entity.write(
                {
                    'source': source,
                    'dataset': dataset,
                    'row_id': row_id,
                    'entity_evidence_id': entity_evidence_id,
                    'parent_entity_evidence_id': parent_entity_evidence_id,
                    'entity_role': entity_role,
                    'entity_type': entity_type,
                    'taxonomy_id': extract_taxonomy_id(row),
                }
            )
            stats.entity_evidence += 1

            for identifier in row.get('identifiers') or []:
                ident_type = string_or_none(identifier.get('type'))
                ident_value = string_or_none(identifier.get('value'))
                if not include_identifier(ident_type, ident_value):
                    continue
                writers.identifier.write(
                    {
                        'source': source,
                        'entity_evidence_id': entity_evidence_id,
                        'identifier_id': identifier_key(ident_type, ident_value),
                        'identifier_type': ident_type,
                        'identifier': ident_value,
                    }
                )
                stats.identifiers += 1

            for annotation in row.get('annotations') or []:
                relation_spec = ontology_annotation_relation(
                    annotation,
                    subject_occurrence_id=occurrence_id,
                )
                if relation_spec is not None:
                    writers.annotation_relation.write(
                        {
                            'relation_evidence_id': relation_evidence_key(
                                source,
                                dataset,
                                row_id,
                                relation_spec.relation_occurrence_id,
                            ),
                            'source': source,
                            'dataset': dataset,
                            'row_id': row_id,
                            'subject_entity_evidence_id': entity_evidence_id,
                            'predicate': relation_spec.predicate_rule.predicate,
                            'object_entity_type': relation_spec.object_entity_type,
                            'object_id_type': relation_spec.object_id_type,
                            'object_id': relation_spec.object_id,
                            'relation_category': (
                                relation_spec.predicate_rule.relation_category
                                or ASSOCIATION_CATEGORY
                            ),
                        }
                    )
                    stats.relation_evidence += 1
                    continue
                if _write_annotation(
                    writers.entity_annotation,
                    writers.annotation,
                    seen_annotations,
                    source=source,
                    evidence_id=entity_evidence_id,
                    annotation=annotation,
                ):
                    stats.annotations += 1

        member_refs: list[tuple[str, object]] = []
        for member_index, membership in enumerate(memberships):
            member = getattr(membership, 'member', None)
            if member is None:
                continue
            member_occurrence_id = f'{occurrence_id}:member:{member_index}'
            self._flatten_entity_tree(
                member,
                source=source,
                dataset=dataset,
                row_id=row_id,
                occurrence_id=member_occurrence_id,
                parent_entity_evidence_id=(
                    None if relation_only_interaction else entity_evidence_id
                ),
                entity_role='member',
                writers=writers,
                seen_annotations=seen_annotations,
                stats=stats,
            )
            member_refs.append((member_occurrence_id, membership))

        if relation_only_interaction and len(member_refs) == 2:
            spec = interaction_relation_spec(
                row,
                member_refs,
                occurrence_id=occurrence_id,
            )
            if spec is None:
                return
            relation_evidence_id = relation_evidence_key(
                source,
                dataset,
                row_id,
                spec.relation_occurrence_id,
            )
            self._write_relation(
                writers,
                source=source,
                dataset=dataset,
                row_id=row_id,
                relation_evidence_id=relation_evidence_id,
                subject_occurrence_id=str(spec.subject_ref),
                predicate=spec.predicate_rule.predicate,
                object_occurrence_id=str(spec.object_ref),
                relation_category=(
                    spec.predicate_rule.relation_category or ASSOCIATION_CATEGORY
                ),
            )
            stats.relation_evidence += 1
            stats.annotations += self._write_relation_annotations(
                writers,
                seen_annotations,
                source=source,
                relation_evidence_id=relation_evidence_id,
                annotations=interaction_relation_annotations(row),
            )
        elif member_refs:
            for member_index, (member_occurrence_id, membership) in enumerate(
                member_refs
            ):
                relation_occurrence_id = f'{occurrence_id}:membership:{member_index}'
                spec = membership_relation_spec(
                    parent_ref=occurrence_id,
                    member_ref=member_occurrence_id,
                    membership=membership,
                    parent_type=entity_type,
                    relation_occurrence_id=relation_occurrence_id,
                )
                relation_evidence_id = relation_evidence_key(
                    source,
                    dataset,
                    row_id,
                    spec.relation_occurrence_id,
                )
                self._write_relation(
                    writers,
                    source=source,
                    dataset=dataset,
                    row_id=row_id,
                    relation_evidence_id=relation_evidence_id,
                    subject_occurrence_id=str(spec.subject_ref),
                    predicate=spec.predicate_rule.predicate,
                    object_occurrence_id=str(spec.object_ref),
                    relation_category=(
                        spec.predicate_rule.relation_category
                        or ASSOCIATION_CATEGORY
                    ),
                )
                stats.relation_evidence += 1
                stats.annotations += self._write_relation_annotations(
                    writers,
                    seen_annotations,
                    source=source,
                    relation_evidence_id=relation_evidence_id,
                    annotations=getattr(membership, 'annotations', None) or [],
                )

    @staticmethod
    def _write_relation(
        writers: _EvidenceWriters,
        *,
        source: str,
        dataset: str,
        row_id: int,
        relation_evidence_id: str,
        subject_occurrence_id: str,
        predicate: str,
        object_occurrence_id: str,
        relation_category: str,
    ) -> None:
        writers.relation.write(
            {
                'source': source,
                'dataset': dataset,
                'row_id': row_id,
                'relation_evidence_id': relation_evidence_id,
                'subject_entity_evidence_id': entity_evidence_key(
                    source,
                    dataset,
                    row_id,
                    subject_occurrence_id,
                ),
                'predicate': predicate,
                'object_entity_evidence_id': entity_evidence_key(
                    source,
                    dataset,
                    row_id,
                    object_occurrence_id,
                ),
                'relation_category': relation_category,
            }
        )

    @staticmethod
    def _write_relation_annotations(
        writers: _EvidenceWriters,
        seen_annotations: set[tuple[str, str, str | None, str | None]],
        *,
        source: str,
        relation_evidence_id: str,
        annotations: Iterable[object],
    ) -> int:
        count = 0
        for annotation in annotations:
            if _write_annotation(
                writers.relation_annotation,
                writers.annotation,
                seen_annotations,
                source=source,
                evidence_id=relation_evidence_id,
                annotation=annotation,
            ):
                count += 1
        return count


def _write_annotation(
    target_writer: _RowWriter,
    value_writer: _RowWriter,
    seen_annotations: set[tuple[str, str, str | None, str | None]],
    *,
    source: str,
    evidence_id: str,
    annotation: object,
) -> bool:
    row = annotation_to_row(annotation)
    term = string_or_none(row.get('term'))
    if term is None:
        return False
    value = string_or_none(row.get('value'))
    unit = string_or_none(row.get('unit', row.get('units')))
    key = annotation_key(term, value, unit)
    target_writer.write(
        {
            'source': source,
            'evidence_id': evidence_id,
            'annotation_key': key,
            'term': term,
            'value': value,
            'unit': unit,
        }
    )
    value_tuple = (key, term, value, unit)
    if value_tuple not in seen_annotations:
        seen_annotations.add(value_tuple)
        value_writer.write(
            {
                'annotation_key': key,
                'term': term,
                'value': value,
                'unit': unit,
            }
        )
    return True


@dataclass
class _MutableProjectionStats:
    source_rows: int = 0
    entity_evidence: int = 0
    relation_evidence: int = 0
    identifiers: int = 0
    annotations: int = 0

    def freeze(self) -> ProjectionStats:
        return ProjectionStats(
            source_rows=self.source_rows,
            entity_evidence=self.entity_evidence,
            relation_evidence=self.relation_evidence,
            identifiers=self.identifiers,
            annotations=self.annotations,
        )
