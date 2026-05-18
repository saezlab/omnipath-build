"""Project pypath silver entities into source evidence Parquet files."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.ingest.common import (
    annotation_key,
    annotation_to_row,
    entity_evidence_key,
    entity_to_row,
    extract_taxonomy_id,
    identifier_key,
    include_identifier,
    interaction_relation_annotations,
    interaction_relation_spec,
    is_interaction_like,
    membership_relation_spec,
    ontology_annotation_relation,
    relation_evidence_key,
    unwrap_record,
)
from omnipath_build.relation_rules import ASSOCIATION_CATEGORY, string_or_none
from pypath.internals.silver_schema import Entity


@dataclass(frozen=True)
class ParquetProjectionStats:
    source_rows: int
    entity_evidence: int
    relation_evidence: int
    identifiers: int
    annotations: int


class ParquetEvidenceProjector:
    """Flatten silver entity streams into source-shaped Parquet evidence."""

    def __init__(
        self,
        output_dir: str | Path,
        *,
        chunk_size: int = 100_000,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.chunk_size = chunk_size

    def project_records(
        self,
        records: Iterable[object],
        *,
        source: str,
        dataset: str,
        max_records: int | None = None,
    ) -> ParquetProjectionStats:
        if max_records is not None:
            records = islice(records, max_records)

        writers = _EvidenceWriters(self.output_dir, chunk_size=self.chunk_size)
        seen_annotations: set[tuple[str, str, str | None, str | None]] = set()
        stats = _MutableProjectionStats()
        try:
            for index, item in enumerate(records, start=1):
                entity, _ = unwrap_record(item)
                if not isinstance(entity, Entity):
                    continue
                self._flatten_entity_tree(
                    entity,
                    source=source,
                    dataset=dataset,
                    row_id=index,
                    occurrence_id=f'{dataset}:{index}:parent',
                    parent_entity_evidence_id=None,
                    entity_role='parent',
                    writers=writers,
                    seen_annotations=seen_annotations,
                    stats=stats,
                )
                stats.source_rows += 1
        finally:
            writers.close()
        return stats.freeze()

    def _flatten_entity_tree(
        self,
        entity: Entity,
        *,
        source: str,
        dataset: str,
        row_id: int,
        occurrence_id: str,
        parent_entity_evidence_id: str | None,
        entity_role: str,
        writers: '_EvidenceWriters',
        seen_annotations: set[tuple[str, str, str | None, str | None]],
        stats: '_MutableProjectionStats',
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
        writers: '_EvidenceWriters',
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
        writers: '_EvidenceWriters',
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
    target_writer: '_ParquetRowWriter',
    value_writer: '_ParquetRowWriter',
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


class _EvidenceWriters:
    def __init__(self, output_dir: Path, *, chunk_size: int) -> None:
        self.entity = _ParquetRowWriter(
            output_dir / 'entity_evidence.parquet',
            pa.schema(
                [
                    ('source', pa.string()),
                    ('dataset', pa.string()),
                    ('row_id', pa.int64()),
                    ('entity_evidence_id', pa.string()),
                    ('parent_entity_evidence_id', pa.string()),
                    ('entity_role', pa.string()),
                    ('entity_type', pa.string()),
                    ('taxonomy_id', pa.string()),
                ]
            ),
            chunk_size=chunk_size,
        )
        self.identifier = _ParquetRowWriter(
            output_dir / 'entity_identifier.parquet',
            pa.schema(
                [
                    ('source', pa.string()),
                    ('entity_evidence_id', pa.string()),
                    ('identifier_id', pa.string()),
                    ('identifier_type', pa.string()),
                    ('identifier', pa.string()),
                ]
            ),
            chunk_size=chunk_size,
        )
        annotation_ref_schema = pa.schema(
            [
                ('source', pa.string()),
                ('evidence_id', pa.string()),
                ('annotation_key', pa.string()),
                ('term', pa.string()),
                ('value', pa.string()),
                ('unit', pa.string()),
            ]
        )
        self.entity_annotation = _ParquetRowWriter(
            output_dir / 'entity_annotation.parquet',
            annotation_ref_schema,
            chunk_size=chunk_size,
        )
        self.relation_annotation = _ParquetRowWriter(
            output_dir / 'relation_annotation.parquet',
            annotation_ref_schema,
            chunk_size=chunk_size,
        )
        self.annotation = _ParquetRowWriter(
            output_dir / 'annotation.parquet',
            pa.schema(
                [
                    ('annotation_key', pa.string()),
                    ('term', pa.string()),
                    ('value', pa.string()),
                    ('unit', pa.string()),
                ]
            ),
            chunk_size=chunk_size,
        )
        self.relation = _ParquetRowWriter(
            output_dir / 'relation_evidence.parquet',
            pa.schema(
                [
                    ('source', pa.string()),
                    ('dataset', pa.string()),
                    ('row_id', pa.int64()),
                    ('relation_evidence_id', pa.string()),
                    ('subject_entity_evidence_id', pa.string()),
                    ('predicate', pa.string()),
                    ('object_entity_evidence_id', pa.string()),
                    ('relation_category', pa.string()),
                ]
            ),
            chunk_size=chunk_size,
        )
        self.annotation_relation = _ParquetRowWriter(
            output_dir / 'annotation_relation_evidence.parquet',
            pa.schema(
                [
                    ('relation_evidence_id', pa.string()),
                    ('source', pa.string()),
                    ('dataset', pa.string()),
                    ('row_id', pa.int64()),
                    ('subject_entity_evidence_id', pa.string()),
                    ('predicate', pa.string()),
                    ('object_entity_type', pa.string()),
                    ('object_id_type', pa.string()),
                    ('object_id', pa.string()),
                    ('relation_category', pa.string()),
                ]
            ),
            chunk_size=chunk_size,
        )

    def close(self) -> None:
        self.entity.close()
        self.identifier.close()
        self.entity_annotation.close()
        self.relation_annotation.close()
        self.annotation.close()
        self.relation.close()
        self.annotation_relation.close()


class _ParquetRowWriter:
    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        *,
        chunk_size: int,
    ) -> None:
        self.path = path
        self.schema = schema
        self.chunk_size = chunk_size
        self.writer: pq.ParquetWriter | None = None
        self.rows: list[dict[str, object]] = []

    def write(self, row: dict[str, object]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.chunk_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(self.path, self.schema)
        self.writer.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is not None:
            self.writer.close()
            return
        empty = {field.name: pa.array([], type=field.type) for field in self.schema}
        pq.write_table(pa.Table.from_pydict(empty, schema=self.schema), self.path)


@dataclass
class _MutableProjectionStats:
    source_rows: int = 0
    entity_evidence: int = 0
    relation_evidence: int = 0
    identifiers: int = 0
    annotations: int = 0

    def freeze(self) -> ParquetProjectionStats:
        return ParquetProjectionStats(
            source_rows=self.source_rows,
            entity_evidence=self.entity_evidence,
            relation_evidence=self.relation_evidence,
            identifiers=self.identifiers,
            annotations=self.annotations,
        )
