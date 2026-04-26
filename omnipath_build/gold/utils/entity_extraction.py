from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.gold.utils.schema import (
    AnnotationContext,
    CV_TERM_ENTITY_TYPE,
    ONTOLOGY_IDENTIFIER_TERM,
    classify_annotation,
    projected_attribute,
    string_or_none,
)
from omnipath_build.gold.utils.cv_terms import format_cv_term


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


def extract_ontology_entity_description(
    annotation: dict[str, Any],
    source: str,
) -> dict[str, Any] | None:
    term_id = string_or_none(annotation.get('value'))
    if term_id is None:
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
