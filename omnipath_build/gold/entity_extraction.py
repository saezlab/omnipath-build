from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.gold.projection_schema import (
    AnnotationContext,
    CV_TERM_ENTITY_TYPE,
    ONTOLOGY_IDENTIFIER_TERM,
    classify_annotation,
    classify_silver_record,
    extract_taxonomy_id,
    infer_ontology_term_row,
    is_cv_term_accession,
    is_pure_ontology_term_annotation,
    materialize_ontology_object,
    projected_attribute,
    string_or_none,
)
from omnipath_build.gold.cv_terms import format_cv_term


ATTRIBUTES_STRUCT = pa.list_(
    pa.struct([
        pa.field('term', pa.string()),
        pa.field('value', pa.string()),
        pa.field('unit', pa.string()),
    ])
)

ENTITY_SCHEMA = pa.schema([
    pa.field('entity_pk', pa.int64()),
    pa.field('entity_type', pa.string()),
    pa.field('taxonomy_id', pa.string()),
    pa.field('entity_attributes', ATTRIBUTES_STRUCT),
    pa.field('sources', pa.list_(pa.string())),
])

RAW_ENTITY_IDENTIFIERS_SCHEMA = pa.schema([
    pa.field('entity_pk', pa.int64()),
    pa.field('identifier', pa.string()),
    pa.field('identifier_type', pa.string()),
    pa.field('source', pa.string()),
])

ENTITY_RELATION_EVIDENCE_SCHEMA = pa.schema([
    pa.field('source', pa.string()),
    pa.field('relation_evidence_pk', pa.int64()),
    pa.field('relation_pk', pa.int64()),
    pa.field('record_attributes', ATTRIBUTES_STRUCT),
    pa.field('subject_attributes', ATTRIBUTES_STRUCT),
    pa.field('object_attributes', ATTRIBUTES_STRUCT),
    pa.field('evidence', ATTRIBUTES_STRUCT),
])

ENTITY_RELATION_SCHEMA = pa.schema([
    pa.field('relation_pk', pa.int64()),
    pa.field('subject_entity_pk', pa.int64()),
    pa.field('predicate', pa.string()),
    pa.field('object_entity_pk', pa.int64()),
    pa.field('relation_category', pa.string()),
    pa.field('evidence_count', pa.int64()),
    pa.field('sources', pa.list_(pa.string())),
])

ONTOLOGY_TERM_SCHEMA = pa.schema([
    pa.field('term_id', pa.string()),
    pa.field('ontology_prefix', pa.string()),
    pa.field('label', pa.string()),
    pa.field('definition', pa.string()),
    pa.field('synonyms', pa.list_(pa.string())),
    pa.field('source', pa.string()),
])


class BufferedParquetWriter:
    def __init__(self, path: Path, schema: pa.Schema, batch_size: int = 10_000) -> None:
        self.path = path
        self.schema = schema
        self.batch_size = batch_size
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.writer: pq.ParquetWriter | None = None
        self.rows: list[dict[str, Any]] = []

    def write(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.path, self.schema)
        self.writer.write_table(table)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is None:
            if self.path.exists():
                self.path.unlink()
            return
        self.writer.close()


def compute_entity_fingerprint(entity_type: str | None, identifiers: list[dict]) -> str:
    if entity_type is None:
        entity_type = ''
    ident_tuples = sorted(
        (str(i.get('type') or ''), str(i.get('value') or ''))
        for i in identifiers
        if i.get('type') and i.get('value')
    )
    key = json.dumps({'type': entity_type, 'ids': ident_tuples}, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def collect_attributes(
    annotations: list[dict[str, Any]],
    context: AnnotationContext,
    buckets: set[str],
) -> list[dict[str, str | None]] | None:
    rows: list[dict[str, str | None]] = []
    for annotation in annotations:
        disposition = classify_annotation(annotation, context)
        if disposition.bucket not in buckets:
            continue
        attribute = projected_attribute(annotation)
        if attribute is not None:
            rows.append({'term': attribute.term, 'value': attribute.value, 'unit': attribute.unit})
    return rows or None


def extract_entity_description(
    row: dict[str, Any],
    source: str,
    record_class: str,
) -> dict[str, Any] | None:
    row_type = string_or_none(row.get('type'))
    identifiers = row.get('identifiers') or []

    if row_type is None and not identifiers:
        return None

    annotations = row.get('annotations') or []
    context = AnnotationContext(record_class=record_class, parent_type=row_type)
    entity_attributes = collect_attributes(annotations, context, buckets={'record_attribute'})

    formatted_identifiers = [
        {'type': format_cv_term(string_or_none(i.get('type'))),
         'value': string_or_none(i.get('value'))}
        for i in identifiers
        if string_or_none(i.get('value')) is not None
    ]

    entity_type = format_cv_term(row_type)
    fingerprint = compute_entity_fingerprint(entity_type, formatted_identifiers)

    return {
        '_fingerprint': fingerprint,
        'entity_type': entity_type,
        'taxonomy_id': extract_taxonomy_id(row),
        'entity_attributes': entity_attributes,
        'sources': [source],
        'identifiers': formatted_identifiers,
    }


def extract_ontology_entity_description(
    annotation: dict[str, Any],
    source: str,
) -> dict[str, Any] | None:
    term_id = string_or_none(annotation.get('value'))
    if term_id is None or not is_cv_term_accession(term_id):
        return None

    entity_type = format_cv_term(CV_TERM_ENTITY_TYPE)
    identifiers = [{'type': format_cv_term(ONTOLOGY_IDENTIFIER_TERM), 'value': term_id}]
    fingerprint = compute_entity_fingerprint(entity_type, identifiers)

    return {
        '_fingerprint': fingerprint,
        'entity_type': entity_type,
        'taxonomy_id': None,
        'entity_attributes': None,
        'sources': [source],
        'identifiers': identifiers,
    }


def extract_entities_and_ontologies_from_row(
    row: dict[str, Any],
    source: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract entity descriptions and ontology term rows from a single silver row.

    Recursively processes members so nested complexes are handled correctly.
    """
    entities: list[dict[str, Any]] = []
    ontology_terms: list[dict[str, Any]] = []

    record_class = classify_silver_record(row)

    if record_class == 'ignored':
        return entities, ontology_terms

    if record_class == 'ontology_term_only':
        term_row = infer_ontology_term_row(row, source)
        if term_row is not None:
            ontology_terms.append(term_row)
        return entities, ontology_terms

    # Interaction rows do not materialize a parent entity in the old projector;
    # they only create relations between members.
    if record_class != 'interaction_relation':
        parent = extract_entity_description(row, source, record_class)
        if parent is not None:
            entities.append(parent)

        if record_class == 'entity_with_ontology_backing':
            term_row = infer_ontology_term_row(row, source)
            if term_row is not None:
                ontology_terms.append(term_row)

        # Ontology entities from parent annotations
        for annotation in row.get('annotations') or []:
            if is_pure_ontology_term_annotation(annotation):
                ont = extract_ontology_entity_description(annotation, source)
                if ont is not None:
                    entities.append(ont)

    # Members (recursive)
    for membership in row.get('membership') or []:
        member_row = membership.get('member') or {}
        member_entities, member_ontologies = extract_entities_and_ontologies_from_row(
            member_row, source
        )
        entities.extend(member_entities)
        ontology_terms.extend(member_ontologies)

    return entities, ontology_terms


def extract_all_from_silver(
    silver_dir: str | Path,
    source: str,
    batch_size: int = 10_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract all entity descriptions and ontology terms from silver files.

    Returns:
        (entity_descriptions, ontology_term_rows)
    """
    entity_descriptions: list[dict[str, Any]] = []
    ontology_term_rows: list[dict[str, Any]] = []

    silver_path = Path(silver_dir)
    parquet_files = sorted(
        path for path in silver_path.glob('*.parquet') if path.name != 'resource.parquet'
    )

    for parquet_path in parquet_files:
        pf = pq.ParquetFile(parquet_path)
        for batch in pf.iter_batches(batch_size=batch_size):
            for row in batch.to_pylist():
                entities, terms = extract_entities_and_ontologies_from_row(row, source)
                entity_descriptions.extend(entities)
                ontology_term_rows.extend(terms)

    return entity_descriptions, ontology_term_rows
