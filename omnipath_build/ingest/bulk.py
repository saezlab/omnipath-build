from __future__ import annotations

from io import StringIO
import csv
import time
from dataclasses import field, dataclass
from collections.abc import Iterable

from psycopg2 import sql
import psycopg2.extensions

from omnipath_build.ingest.common import (
    IngestStats,
    MutableStats as _MutableStats,
    copy_value,
    entity_to_row as _entity_to_row,
    unwrap_record as _unwrap,
    annotation_key as _annotation_key,
    annotation_to_row as _annotation_to_row,
    entity_evidence_key as _entity_evidence_key,
    identifier_key as _identifier_key,
    interaction_relation_annotations,
    is_interaction_like,
    membership_relation_spec,
    relation_evidence_key as _relation_evidence_key,
    interaction_relation_spec,
    ontology_annotation_relation,
    extract_taxonomy_id,
    include_identifier,
)
from pypath.internals.silver_schema import Entity
from omnipath_build.relation_rules import (
    ASSOCIATION_CATEGORY,
    string_or_none,
)


class BulkIngestor:
    """Bulk ingest via bounded COPY staging tables.

    Dedupe remains global because final writes target the real tables and their
    unique constraints. Staging tables are only a fast transport and ID
    resolution layer.
    """

    def __init__(
        self,
        conn: psycopg2.extensions.connection,
        *,
        schema: str = 'public',
        profile: bool = False,
    ) -> None:
        self.conn = conn
        self.schema = schema
        self.profile = profile

    def ingest_records(
        self,
        records: Iterable[Entity],
        *,
        source: str,
        dataset: str,
        batch_size: int = 25_000,
        progress_every: int = 5000,
    ) -> IngestStats:
        """Ingest records through bounded COPY staging batches."""

        stats = _MutableStats()
        buffers = _BulkBuffers()
        started_at = time.monotonic()
        rows_since_flush = 0
        read_seconds = 0.0
        flatten_seconds = 0.0

        record_iter = iter(records)
        index = 0
        while True:
            read_started = time.perf_counter()
            try:
                item = next(record_iter)
            except StopIteration:
                read_seconds += time.perf_counter() - read_started
                break
            read_seconds += time.perf_counter() - read_started
            index += 1
            entity, _ = _unwrap(item)
            if not isinstance(entity, Entity):
                continue
            row_id = index
            flatten_started = time.perf_counter()
            self._flatten_entity_tree(
                entity,
                source=source,
                dataset=dataset,
                row_id=row_id,
                occurrence_id=f'{dataset}:{row_id}:parent',
                parent_entity_evidence_id=None,
                vocab_entity_role='parent',
                buffers=buffers,
                stats=stats,
            )
            flatten_seconds += time.perf_counter() - flatten_started
            stats.source_rows += 1
            rows_since_flush += 1

            if batch_size > 0 and rows_since_flush >= batch_size:
                self._flush(
                    buffers,
                    source=source,
                    dataset=dataset,
                    source_rows=stats.source_rows,
                    read_seconds=read_seconds,
                    flatten_seconds=flatten_seconds,
                )
                read_seconds = 0.0
                flatten_seconds = 0.0
                rows_since_flush = 0
            if progress_every > 0 and stats.source_rows % progress_every == 0:
                self._print_progress(source, dataset, stats, started_at)

        self._flush(
            buffers,
            source=source,
            dataset=dataset,
            source_rows=stats.source_rows,
            read_seconds=read_seconds,
            flatten_seconds=flatten_seconds,
        )
        self.conn.commit()
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
        vocab_entity_role: str,
        buffers: _BulkBuffers,
        stats: _MutableStats,
    ) -> None:
        row = _entity_to_row(entity)
        vocab_entity_type = string_or_none(row.get('type'))
        memberships = list(getattr(entity, 'membership', None) or [])
        relation_only_interaction = (
            is_interaction_like(vocab_entity_type)
            and sum(
                1
                for membership in memberships
                if getattr(membership, 'member', None) is not None
            )
            == 2
        )

        entity_evidence_id = None
        if not relation_only_interaction:
            entity_evidence_id = _entity_evidence_key(
                source,
                dataset,
                row_id,
                occurrence_id,
            )
            buffers.entities.append(
                (
                    entity_evidence_id,
                    source,
                    dataset,
                    row_id,
                    parent_entity_evidence_id,
                    vocab_entity_role,
                    row.get('type'),
                    extract_taxonomy_id(row),
                )
            )
            stats.entity_evidence += 1

            for identifier in row.get('identifiers') or []:
                ident_type = string_or_none(identifier.get('type'))
                ident_value = string_or_none(identifier.get('value'))
                if not include_identifier(ident_type, ident_value):
                    continue
                identifier_cache_key = (ident_type, ident_value)
                identifier_id = buffers.identifier_keys.get(
                    identifier_cache_key
                )
                if identifier_id is None:
                    identifier_id = _identifier_key(ident_type, ident_value)
                    buffers.identifier_keys[identifier_cache_key] = (
                        identifier_id
                    )
                buffers.identifiers.append(
                    (
                        source,
                        entity_evidence_id,
                        identifier_id,
                        ident_type,
                        ident_value,
                    )
                )
                stats.identifiers += 1

            for annotation in row.get('annotations') or []:
                relation_count = len(buffers.annotation_relations)
                if self._append_annotation(
                    buffers,
                    source=source,
                    dataset=dataset,
                    row_id=row_id,
                    target_kind='entity',
                    target_occurrence_id=occurrence_id,
                    target_evidence_id=entity_evidence_id,
                    scope='entity',
                    annotation=annotation,
                ):
                    stats.annotations += 1
                elif len(buffers.annotation_relations) > relation_count:
                    stats.relation_evidence += 1

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
                    None
                    if relation_only_interaction
                    else entity_evidence_id
                ),
                vocab_entity_role='member',
                buffers=buffers,
                stats=stats,
            )
            member_refs.append((member_occurrence_id, membership))

        if relation_only_interaction and len(member_refs) == 2:
            spec = interaction_relation_spec(
                row,
                member_refs,
                occurrence_id=occurrence_id,
            )
            if spec is not None:
                relation_evidence_id = _relation_evidence_key(
                    source,
                    dataset,
                    row_id,
                    spec.relation_occurrence_id,
                )
                buffers.relations.append(
                    (
                        relation_evidence_id,
                        source,
                        dataset,
                        row_id,
                        _entity_evidence_key(
                            source,
                            dataset,
                            row_id,
                            str(spec.subject_ref),
                        ),
                        spec.predicate_rule.predicate,
                        _entity_evidence_key(
                            source,
                            dataset,
                            row_id,
                            str(spec.object_ref),
                        ),
                        spec.predicate_rule.relation_category
                        or ASSOCIATION_CATEGORY,
                    )
                )
                stats.relation_evidence += 1
                stats.annotations += self._append_relation_annotations(
                    buffers,
                    source=source,
                    dataset=dataset,
                    row_id=row_id,
                    relation_occurrence_id=spec.relation_occurrence_id,
                    relation_evidence_id=relation_evidence_id,
                    annotations=interaction_relation_annotations(row),
                    scope='relation',
                )
        elif member_refs:
            for member_index, (member_occurrence_id, membership) in enumerate(
                member_refs
            ):
                relation_occurrence_id = (
                    f'{occurrence_id}:membership:{member_index}'
                )
                spec = membership_relation_spec(
                    parent_ref=occurrence_id,
                    member_ref=member_occurrence_id,
                    membership=membership,
                    parent_type=vocab_entity_type,
                    relation_occurrence_id=relation_occurrence_id,
                )
                relation_evidence_id = _relation_evidence_key(
                    source,
                    dataset,
                    row_id,
                    spec.relation_occurrence_id,
                )
                buffers.relations.append(
                    (
                        relation_evidence_id,
                        source,
                        dataset,
                        row_id,
                        _entity_evidence_key(
                            source,
                            dataset,
                            row_id,
                            str(spec.subject_ref),
                        ),
                        spec.predicate_rule.predicate,
                        _entity_evidence_key(
                            source,
                            dataset,
                            row_id,
                            str(spec.object_ref),
                        ),
                        spec.predicate_rule.relation_category
                        or ASSOCIATION_CATEGORY,
                    )
                )
                stats.relation_evidence += 1
                stats.annotations += self._append_relation_annotations(
                    buffers,
                    source=source,
                    dataset=dataset,
                    row_id=row_id,
                    relation_occurrence_id=relation_occurrence_id,
                    relation_evidence_id=relation_evidence_id,
                    annotations=getattr(membership, 'annotations', None) or [],
                    scope='relation',
                )

    def _append_relation_annotations(
        self,
        buffers: _BulkBuffers,
        *,
        source: str,
        dataset: str,
        row_id: int,
        relation_occurrence_id: str,
        relation_evidence_id: object,
        annotations: Iterable[object],
        scope: str,
    ) -> int:
        count = 0
        for annotation in annotations:
            if self._append_annotation(
                buffers,
                source=source,
                dataset=dataset,
                row_id=row_id,
                target_kind='relation',
                target_occurrence_id=relation_occurrence_id,
                target_evidence_id=relation_evidence_id,
                scope=scope,
                annotation=annotation,
            ):
                count += 1
        return count

    @staticmethod
    def _append_annotation(
        buffers: _BulkBuffers,
        *,
        source: str,
        dataset: str,
        row_id: int,
        target_kind: str,
        target_occurrence_id: str,
        target_evidence_id: object,
        scope: str,
        annotation: object,
    ) -> bool:
        row = _annotation_to_row(annotation)
        if (
            target_kind == 'entity'
            and BulkIngestor._append_annotation_relation(
                buffers,
                source=source,
                dataset=dataset,
                row_id=row_id,
                subject_occurrence_id=target_occurrence_id,
                annotation=row,
            )
        ):
            return False
        term = string_or_none(row.get('term'))
        if term is None:
            return False
        value = string_or_none(row.get('value'))
        unit = string_or_none(row.get('unit', row.get('units')))
        annotation_cache_key = (term, value, unit)
        annotation_key = buffers.annotation_keys.get(annotation_cache_key)
        if annotation_key is None:
            annotation_key = _annotation_key(term, value, unit)
            buffers.annotation_keys[annotation_cache_key] = annotation_key
        buffers.annotations.append(
            (
                target_kind,
                source,
                target_evidence_id,
                scope,
                annotation_key,
            )
        )
        annotation_value = (annotation_key, term, value, unit)
        if annotation_value not in buffers.annotation_values_seen:
            buffers.annotation_values_seen.add(annotation_value)
            buffers.annotation_values.append(annotation_value)
        return True

    @staticmethod
    def _append_annotation_relation(
        buffers: _BulkBuffers,
        *,
        source: str,
        dataset: str,
        row_id: int,
        subject_occurrence_id: str,
        annotation: dict[str, str | None],
    ) -> bool:
        spec = ontology_annotation_relation(
            annotation,
            subject_occurrence_id=subject_occurrence_id,
        )
        if spec is None:
            return False
        buffers.annotation_relations.append(
            (
                _relation_evidence_key(
                    source,
                    dataset,
                    row_id,
                    spec.relation_occurrence_id,
                ),
                source,
                dataset,
                row_id,
                _entity_evidence_key(
                    source,
                    dataset,
                    row_id,
                    spec.subject_occurrence_id,
                ),
                spec.predicate_rule.predicate,
                spec.object_entity_type,
                spec.object_id_type,
                spec.object_id,
                spec.predicate_rule.relation_category or ASSOCIATION_CATEGORY,
            )
        )
        return True

    def _flush(
        self,
        buffers: _BulkBuffers,
        *,
        source: str,
        dataset: str,
        source_rows: int,
        read_seconds: float,
        flatten_seconds: float,
    ) -> None:
        if not buffers:
            return
        profile = _FlushProfile(
            source=source,
            dataset=dataset,
            source_rows=source_rows,
            read_seconds=read_seconds,
            flatten_seconds=flatten_seconds,
            entity_rows=len(buffers.entities),
            identifier_rows=len(buffers.identifiers),
            relation_rows=len(buffers.relations),
            annotation_relation_rows=len(buffers.annotation_relations),
            annotation_value_rows=len(buffers.annotation_values),
            annotation_rows=len(buffers.annotations),
        )
        flush_started = time.perf_counter()
        with self.conn.cursor() as cur:
            started = time.perf_counter()
            _create_staging_tables(cur)
            profile.add('create_staging_tables', started)
            _copy_profiled(
                cur, 'stg_entity', _ENTITY_COLUMNS, buffers.entities, profile
            )
            _copy_profiled(
                cur,
                'stg_identifier_ref',
                _IDENTIFIER_REF_COLUMNS,
                buffers.identifiers,
                profile,
            )
            _copy_profiled(
                cur,
                'stg_relation',
                _RELATION_COLUMNS,
                buffers.relations,
                profile,
            )
            _copy_profiled(
                cur,
                'stg_annotation_relation',
                _ANNOTATION_RELATION_COLUMNS,
                buffers.annotation_relations,
                profile,
            )
            _copy_profiled(
                cur,
                'stg_annotation_value',
                _ANNOTATION_VALUE_COLUMNS,
                buffers.annotation_values,
                profile,
            )
            _copy_profiled(
                cur,
                'stg_annotation',
                _ANNOTATION_COLUMNS,
                buffers.annotations,
                profile,
            )
            started = time.perf_counter()
            _index_staging_tables(cur)
            profile.add('index_staging_tables', started)
            self._insert_from_staging(cur, profile=profile)
        self.conn.commit()
        profile.total_seconds = time.perf_counter() - flush_started
        if self.profile:
            profile.print()
        buffers.clear()

    def _insert_from_staging(
        self,
        cur: psycopg2.extensions.cursor,
        *,
        profile: _FlushProfile | None = None,
    ) -> None:
        schema = sql.Identifier(self.schema)
        self._execute_step(
            cur,
            'sql_identifier_type',
            sql.SQL(
                """
                WITH missing AS (
                  SELECT DISTINCT s.type AS name
                  FROM stg_identifier_ref s
                  LEFT JOIN {}.vocab_identifier_type it
                    ON it.name = s.type
                  WHERE s.type IS NOT NULL
                    AND it.identifier_type_id IS NULL
                ),
                base AS (
                  SELECT COALESCE(MAX(identifier_type_id), 0) AS max_id
                  FROM {}.vocab_identifier_type
                )
                INSERT INTO {}.vocab_identifier_type (identifier_type_id, name)
                SELECT
                  base.max_id + row_number() OVER (ORDER BY missing.name),
                  missing.name
                FROM missing
                CROSS JOIN base
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema, schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_identifier_evidence',
            sql.SQL(
                """
                INSERT INTO {}.identifier_evidence (
                  identifier_id,
                  identifier_type_id,
                  value
                )
                SELECT DISTINCT s.identifier_id, it.identifier_type_id, s.value
                FROM stg_identifier_ref s
                JOIN {}.vocab_identifier_type it
                  ON it.name = s.type
                WHERE s.type IS NOT NULL AND s.value IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            ).format(schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_sources',
            sql.SQL(
                """
                INSERT INTO {}.data_source (name)
                SELECT DISTINCT source
                FROM (
                  SELECT source FROM stg_entity
                  UNION
                  SELECT source FROM stg_relation
                  UNION
                  SELECT source FROM stg_annotation_relation
                  UNION
                  SELECT source FROM stg_annotation
                ) s
                WHERE source IS NOT NULL
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_datasets',
            sql.SQL(
                """
                INSERT INTO {}.dataset (source_id, name)
                SELECT DISTINCT ds.source_id, d.dataset
                FROM (
                  SELECT source, dataset FROM stg_entity
                  UNION
                  SELECT source, dataset FROM stg_relation
                  UNION
                  SELECT source, dataset FROM stg_annotation_relation
                ) d
                JOIN {}.data_source ds
                  ON ds.name = d.source
                WHERE d.dataset IS NOT NULL
                ON CONFLICT (source_id, name) DO NOTHING
                """
            ).format(schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_entity_types',
            sql.SQL(
                """
                INSERT INTO {}.vocab_entity_type (name)
                SELECT DISTINCT vocab_entity_type
                FROM stg_entity
                WHERE vocab_entity_type IS NOT NULL
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_entity_roles',
            sql.SQL(
                """
                INSERT INTO {}.vocab_entity_role (name)
                SELECT DISTINCT vocab_entity_role
                FROM stg_entity
                WHERE vocab_entity_role IS NOT NULL
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_relation_predicates',
            sql.SQL(
                """
                INSERT INTO {}.vocab_relation_predicate (name)
                SELECT DISTINCT predicate
                FROM (
                  SELECT predicate FROM stg_relation
                  UNION
                  SELECT predicate FROM stg_annotation_relation
                ) p
                WHERE predicate IS NOT NULL
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_relation_categories',
            sql.SQL(
                """
                INSERT INTO {}.vocab_relation_category (name)
                SELECT DISTINCT vocab_relation_category
                FROM (
                  SELECT vocab_relation_category FROM stg_relation
                  UNION
                  SELECT vocab_relation_category FROM stg_annotation_relation
                ) c
                WHERE vocab_relation_category IS NOT NULL
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_entity_evidence',
            sql.SQL(
                """
                INSERT INTO {}.entity_evidence (
                  source_id,
                  entity_evidence_id,
                  dataset_id,
                  row_id,
                  parent_entity_evidence_id,
                  entity_role_id,
                  entity_type_id,
                  taxonomy_id
                )
                SELECT DISTINCT
                  ds.source_id,
                  s.entity_evidence_id,
                  d.dataset_id,
                  s.row_id,
                  s.parent_entity_evidence_id,
                  er.entity_role_id,
                  et.entity_type_id,
                  NULLIF(s.taxonomy_id, '')::bigint
                FROM stg_entity s
                JOIN {}.data_source ds
                  ON ds.name = s.source
                JOIN {}.dataset d
                  ON d.source_id = ds.source_id
                 AND d.name = s.dataset
                JOIN {}.vocab_entity_role er
                  ON er.name = s.vocab_entity_role
                LEFT JOIN {}.vocab_entity_type et
                  ON et.name = s.vocab_entity_type
                ON CONFLICT DO NOTHING
                """
            ).format(schema, schema, schema, schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_entity_identifier_links',
            sql.SQL(
                """
                INSERT INTO {}.entity_evidence_identifier
                  (source_id, entity_evidence_id, identifier_id)
                SELECT DISTINCT ds.source_id, s.entity_evidence_id, s.identifier_id
                FROM stg_identifier_ref s
                JOIN {}.data_source ds
                  ON ds.name = s.source
                WHERE s.identifier_id IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            ).format(schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_cv_term_entities',
            sql.SQL(
                """
                WITH entity_type_rows AS (
                  INSERT INTO {}.vocab_entity_type (name)
                  SELECT DISTINCT object_entity_type
                  FROM stg_annotation_relation
                  WHERE object_entity_type IS NOT NULL
                  ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                  RETURNING entity_type_id, name
                )
                INSERT INTO {}.entity (
                  entity_type_id,
                  taxonomy_id,
                  canonical_identifier_type_id,
                  canonical_identifier,
                  identifiers,
                  resolution_status_id
                )
                SELECT DISTINCT
                  et.entity_type_id,
                  NULL::bigint,
                  it.identifier_type_id,
                  s.object_id,
                  jsonb_build_array(
                    jsonb_build_object(
                      'identifier_type', it.name,
                      'identifier_type_id', it.identifier_type_id,
                      'identifier', s.object_id
                    )
                  ),
                  1
                FROM stg_annotation_relation s
                JOIN {}.vocab_entity_type et
                  ON et.name = s.object_entity_type
                JOIN {}.vocab_identifier_type it
                  ON it.name = s.object_id_type
                WHERE s.object_id IS NOT NULL
                ON CONFLICT (
                  entity_type_id,
                  taxonomy_id,
                  canonical_identifier_type_id,
                  canonical_identifier
                )
                DO UPDATE SET
                  identifiers = EXCLUDED.identifiers,
                  resolution_status_id = 1
                """
            ).format(schema, schema, schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_relation_evidence',
            sql.SQL(
                """
                INSERT INTO {}.relation_evidence (
                  source_id,
                  relation_evidence_id,
                  dataset_id,
                  row_id,
                  subject_entity_evidence_id,
                  subject_entity_id,
                  predicate_id,
                  object_entity_evidence_id,
                  object_entity_id,
                  relation_category_id
                )
                SELECT DISTINCT
                  ds.source_id,
                  s.relation_evidence_id,
                  d.dataset_id,
                  s.row_id,
                  s.subject_entity_evidence_id,
                  NULL::bigint,
                  rp.relation_predicate_id,
                  s.object_entity_evidence_id,
                  NULL::bigint,
                  rc.relation_category_id
                FROM stg_relation s
                JOIN {}.data_source ds
                  ON ds.name = s.source
                JOIN {}.dataset d
                  ON d.source_id = ds.source_id
                 AND d.name = s.dataset
                JOIN {}.vocab_relation_predicate rp
                  ON rp.name = s.predicate
                JOIN {}.vocab_relation_category rc
                  ON rc.name = s.vocab_relation_category
                ON CONFLICT DO NOTHING
                """
            ).format(schema, schema, schema, schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_annotation_relation_evidence',
            sql.SQL(
                """
                INSERT INTO {}.relation_evidence (
                  source_id,
                  relation_evidence_id,
                  dataset_id,
                  row_id,
                  subject_entity_evidence_id,
                  subject_entity_id,
                  predicate_id,
                  object_entity_evidence_id,
                  object_entity_id,
                  relation_category_id
                )
                SELECT DISTINCT
                  ds.source_id,
                  s.relation_evidence_id,
                  d.dataset_id,
                  s.row_id,
                  s.subject_entity_evidence_id,
                  NULL::bigint,
                  rp.relation_predicate_id,
                  NULL::uuid,
                  object.entity_id,
                  rc.relation_category_id
                FROM stg_annotation_relation s
                JOIN {}.data_source ds
                  ON ds.name = s.source
                JOIN {}.dataset d
                  ON d.source_id = ds.source_id
                 AND d.name = s.dataset
                JOIN {}.vocab_entity_type et
                  ON et.name = s.object_entity_type
                JOIN {}.entity object
                  ON object.entity_type_id = et.entity_type_id
                JOIN {}.vocab_identifier_type it
                  ON it.name = s.object_id_type
                 AND object.canonical_identifier_type_id =
                     it.identifier_type_id
                 AND object.canonical_identifier = s.object_id
                JOIN {}.vocab_relation_predicate rp
                  ON rp.name = s.predicate
                JOIN {}.vocab_relation_category rc
                  ON rc.name = s.vocab_relation_category
                ON CONFLICT DO NOTHING
                """
            ).format(
                schema,
                schema,
                schema,
                schema,
                schema,
                schema,
                schema,
                schema,
            ),
            profile,
        )
        self._execute_step(
            cur,
            'sql_annotation_values',
            sql.SQL(
                """
                INSERT INTO {}.annotation (
                  annotation_key,
                  term,
                  value,
                  unit
                )
                SELECT DISTINCT
                  s.annotation_key,
                  s.term,
                  s.value,
                  s.unit
                FROM stg_annotation_value s
                WHERE s.term IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            ).format(schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_entity_annotation_links',
            sql.SQL(
                """
                INSERT INTO {}.entity_evidence_annotation (
                  source_id,
                  entity_evidence_id,
                  annotation_key
                )
                SELECT DISTINCT
                  ds.source_id,
                  s.target_evidence_id,
                  s.annotation_key
                FROM stg_annotation s
                JOIN {}.data_source ds
                  ON ds.name = s.source
                WHERE s.target_kind = 'entity'
                ON CONFLICT DO NOTHING
                """
            ).format(schema, schema),
            profile,
        )
        self._execute_step(
            cur,
            'sql_relation_annotation_links',
            sql.SQL(
                """
                INSERT INTO {}.relation_evidence_annotation (
                  source_id,
                  relation_evidence_id,
                  annotation_key,
                  annotation_scope_id
                )
                SELECT DISTINCT
                  ds.source_id,
                  s.target_evidence_id,
                  s.annotation_key,
                  sc.annotation_scope_id
                FROM stg_annotation s
                JOIN {}.data_source ds
                  ON ds.name = s.source
                JOIN {}.vocab_annotation_scope sc
                  ON sc.name = s.scope
                WHERE s.target_kind = 'relation'
                  AND s.scope IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            ).format(schema, schema, schema),
            profile,
        )

    @staticmethod
    def _execute_step(
        cur: psycopg2.extensions.cursor,
        name: str,
        statement: sql.Composed,
        profile: _FlushProfile | None,
    ) -> None:
        started = time.perf_counter()
        cur.execute(statement)
        if profile is not None:
            profile.timings.append((name, time.perf_counter() - started))

    @staticmethod
    def _print_progress(
        source: str,
        dataset: str,
        stats: _MutableStats,
        started_at: float,
    ) -> None:
        elapsed = time.monotonic() - started_at
        rate = stats.source_rows / elapsed if elapsed else 0.0
        print(
            f'[{source}.{dataset}] bulk ingest progress '
            f'rows={stats.source_rows:,} '
            f'entities={stats.entity_evidence:,} '
            f'relations={stats.relation_evidence:,} '
            f'identifiers={stats.identifiers:,} '
            f'annotations={stats.annotations:,} '
            f'rate={rate:,.1f}/s',
            flush=True,
        )


@dataclass
class _FlushProfile:
    source: str
    dataset: str
    source_rows: int
    read_seconds: float
    flatten_seconds: float
    entity_rows: int
    identifier_rows: int
    relation_rows: int
    annotation_relation_rows: int
    annotation_value_rows: int
    annotation_rows: int
    timings: list[tuple[str, float]] = field(default_factory=list)
    total_seconds: float = 0.0

    def add(self, name: str, started: float) -> None:
        self.timings.append((name, time.perf_counter() - started))

    def print(self) -> None:
        rows = (
            f'entities={self.entity_rows:,} '
            f'identifiers={self.identifier_rows:,} '
            f'relations={self.relation_rows:,} '
            f'annotation_relations={self.annotation_relation_rows:,} '
            f'annotation_values={self.annotation_value_rows:,} '
            f'annotations={self.annotation_rows:,}'
        )
        print(
            f'[{self.source}.{self.dataset}] profile flush '
            f'source_rows={self.source_rows:,} {rows}',
            flush=True,
        )
        print(
            f'[{self.source}.{self.dataset}] profile '
            f'read_records={self.read_seconds:.3f}s',
            flush=True,
        )
        print(
            f'[{self.source}.{self.dataset}] profile '
            f'flatten={self.flatten_seconds:.3f}s',
            flush=True,
        )
        for name, seconds in self.timings:
            print(
                f'[{self.source}.{self.dataset}] profile '
                f'{name}={seconds:.3f}s',
                flush=True,
            )
        print(
            f'[{self.source}.{self.dataset}] profile '
            f'flush_total={self.total_seconds:.3f}s',
            flush=True,
        )


@dataclass
class _BulkBuffers:
    entities: list[tuple[object, ...]] = field(default_factory=list)
    identifiers: list[tuple[object, ...]] = field(default_factory=list)
    relations: list[tuple[object, ...]] = field(default_factory=list)
    annotation_relations: list[tuple[object, ...]] = field(default_factory=list)
    identifier_keys: dict[tuple[object, ...], object] = field(
        default_factory=dict
    )
    annotation_values: list[tuple[object, ...]] = field(default_factory=list)
    annotation_keys: dict[tuple[object, ...], object] = field(
        default_factory=dict
    )
    annotation_values_seen: set[tuple[object, ...]] = field(
        default_factory=set
    )
    annotations: list[tuple[object, ...]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(
            self.entities
            or self.identifiers
            or self.relations
            or self.annotation_relations
            or self.annotation_values
            or self.annotations
        )

    def clear(self) -> None:
        self.entities.clear()
        self.identifiers.clear()
        self.relations.clear()
        self.annotation_relations.clear()
        self.identifier_keys.clear()
        self.annotation_values.clear()
        self.annotation_keys.clear()
        self.annotation_values_seen.clear()
        self.annotations.clear()


_ENTITY_COLUMNS = (
    'entity_evidence_id',
    'source',
    'dataset',
    'row_id',
    'parent_entity_evidence_id',
    'vocab_entity_role',
    'vocab_entity_type',
    'taxonomy_id',
)
_IDENTIFIER_REF_COLUMNS = (
    'source',
    'entity_evidence_id',
    'identifier_id',
    'type',
    'value',
)
_RELATION_COLUMNS = (
    'relation_evidence_id',
    'source',
    'dataset',
    'row_id',
    'subject_entity_evidence_id',
    'predicate',
    'object_entity_evidence_id',
    'vocab_relation_category',
)
_ANNOTATION_RELATION_COLUMNS = (
    'relation_evidence_id',
    'source',
    'dataset',
    'row_id',
    'subject_entity_evidence_id',
    'predicate',
    'object_entity_type',
    'object_id_type',
    'object_id',
    'vocab_relation_category',
)
_ANNOTATION_COLUMNS = (
    'target_kind',
    'source',
    'target_evidence_id',
    'scope',
    'annotation_key',
)
_ANNOTATION_VALUE_COLUMNS = (
    'annotation_key',
    'term',
    'value',
    'unit',
)


def _create_staging_tables(cur: psycopg2.extensions.cursor) -> None:
    cur.execute('DROP TABLE IF EXISTS stg_entity')
    cur.execute('DROP TABLE IF EXISTS stg_identifier_ref')
    cur.execute('DROP TABLE IF EXISTS stg_relation')
    cur.execute('DROP TABLE IF EXISTS stg_annotation_relation')
    cur.execute('DROP TABLE IF EXISTS stg_annotation_value')
    cur.execute('DROP TABLE IF EXISTS stg_annotation')
    cur.execute(
        """
        CREATE TEMP TABLE stg_entity (
          entity_evidence_id uuid,
          source text,
          dataset text,
          row_id bigint,
          parent_entity_evidence_id uuid,
          vocab_entity_role text,
          vocab_entity_type text,
          taxonomy_id text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_identifier_ref (
          source text,
          entity_evidence_id uuid,
          identifier_id uuid,
          type text,
          value text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_relation (
          relation_evidence_id uuid,
          source text,
          dataset text,
          row_id bigint,
          subject_entity_evidence_id uuid,
          predicate text,
          object_entity_evidence_id uuid,
          vocab_relation_category text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_annotation_relation (
          relation_evidence_id uuid,
          source text,
          dataset text,
          row_id bigint,
          subject_entity_evidence_id uuid,
          predicate text,
          object_entity_type text,
          object_id_type text,
          object_id text,
          vocab_relation_category text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_annotation_value (
          annotation_key uuid,
          term text,
          value text,
          unit text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_annotation (
          target_kind text,
          source text,
          target_evidence_id uuid,
          scope text,
          annotation_key uuid
        ) ON COMMIT DROP
        """
    )


def _index_staging_tables(cur: psycopg2.extensions.cursor) -> None:
    cur.execute(
        """
        CREATE INDEX stg_annotation_target_idx
        ON stg_annotation (
          target_kind,
          target_evidence_id
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX stg_annotation_value_idx
        ON stg_annotation_value (annotation_key)
        """
    )
    cur.execute(
        """
        CREATE INDEX stg_annotation_relation_key_idx
        ON stg_annotation_relation (
          relation_evidence_id
        )
        """
    )
    for table in (
        'stg_entity',
        'stg_identifier_ref',
        'stg_relation',
        'stg_annotation_relation',
        'stg_annotation_value',
        'stg_annotation',
    ):
        cur.execute(sql.SQL('ANALYZE {}').format(sql.Identifier(table)))


def _copy_rows(
    cur: psycopg2.extensions.cursor,
    table: str,
    columns: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> None:
    if not rows:
        return
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    for row in rows:
        writer.writerow([copy_value(value) for value in row])
    buffer.seek(0)
    column_sql = sql.SQL(', ').join(
        sql.Identifier(column) for column in columns
    )
    copy_sql = sql.SQL(
        "COPY {} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')"
    ).format(sql.Identifier(table), column_sql)
    cur.copy_expert(copy_sql.as_string(cur.connection), buffer)


def _copy_profiled(
    cur: psycopg2.extensions.cursor,
    table: str,
    columns: tuple[str, ...],
    rows: list[tuple[object, ...]],
    profile: _FlushProfile,
) -> None:
    started = time.perf_counter()
    _copy_rows(cur, table, columns, rows)
    profile.timings.append((f'copy_{table}', time.perf_counter() - started))
