from __future__ import annotations

from io import StringIO
import csv
import time
from dataclasses import field, dataclass
from collections.abc import Iterable

from psycopg2 import sql
import psycopg2.extensions

from minimal.ingest.common import (
    IngestStats,
    MutableStats as _MutableStats,
    copy_value,
    entity_to_row as _entity_to_row,
    unwrap_record as _unwrap,
    annotation_key as _annotation_key,
    annotation_to_row as _annotation_to_row,
    interaction_relation_annotations,
    is_interaction_like,
    membership_relation_spec,
    interaction_relation_spec,
    ontology_annotation_relation,
    extract_taxonomy_id,
    include_identifier,
)
from pypath.internals.silver_schema import Entity
from omnipath_build.gold.utils.schema import (
    ASSOCIATION_CATEGORY,
    string_or_none,
)


class BulkMinimalIngestor:
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
    ) -> None:
        self.conn = conn
        self.schema = schema

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

        for index, item in enumerate(records, start=1):
            entity, _ = _unwrap(item)
            if not isinstance(entity, Entity):
                continue
            row_id = index
            snapshot_id = None
            self._flatten_entity_tree(
                entity,
                source=source,
                dataset=dataset,
                row_id=row_id,
                snapshot_id=snapshot_id,
                occurrence_id=f'{dataset}:{row_id}:parent',
                parent_occurrence_id=None,
                entity_role='parent',
                buffers=buffers,
                stats=stats,
            )
            stats.source_rows += 1
            rows_since_flush += 1

            if batch_size > 0 and rows_since_flush >= batch_size:
                self._flush(buffers)
                rows_since_flush = 0
            if progress_every > 0 and stats.source_rows % progress_every == 0:
                self._print_progress(source, dataset, stats, started_at)

        self._flush(buffers)
        self.conn.commit()
        return stats.freeze()

    def _flatten_entity_tree(
        self,
        entity: Entity,
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        occurrence_id: str,
        parent_occurrence_id: str | None,
        entity_role: str,
        buffers: _BulkBuffers,
        stats: _MutableStats,
    ) -> None:
        row = _entity_to_row(entity)
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

        if not relation_only_interaction:
            buffers.entities.append(
                (
                    source,
                    dataset,
                    row_id,
                    snapshot_id,
                    occurrence_id,
                    parent_occurrence_id,
                    entity_role,
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
                buffers.identifiers.append(
                    (
                        source,
                        dataset,
                        row_id,
                        occurrence_id,
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
                    snapshot_id=snapshot_id,
                    target_kind='entity',
                    target_occurrence_id=occurrence_id,
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
                snapshot_id=snapshot_id,
                occurrence_id=member_occurrence_id,
                parent_occurrence_id=occurrence_id,
                entity_role='member',
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
                buffers.relations.append(
                    (
                        source,
                        dataset,
                        row_id,
                        snapshot_id,
                        spec.relation_occurrence_id,
                        spec.subject_ref,
                        spec.predicate_rule.predicate,
                        spec.object_ref,
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
                    snapshot_id=snapshot_id,
                    relation_occurrence_id=spec.relation_occurrence_id,
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
                    parent_type=entity_type,
                    relation_occurrence_id=relation_occurrence_id,
                )
                buffers.relations.append(
                    (
                        source,
                        dataset,
                        row_id,
                        snapshot_id,
                        spec.relation_occurrence_id,
                        spec.subject_ref,
                        spec.predicate_rule.predicate,
                        spec.object_ref,
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
                    snapshot_id=snapshot_id,
                    relation_occurrence_id=relation_occurrence_id,
                    annotations=getattr(membership, 'annotations', None) or [],
                    scope='membership',
                )

    def _append_relation_annotations(
        self,
        buffers: _BulkBuffers,
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        relation_occurrence_id: str,
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
                snapshot_id=snapshot_id,
                target_kind='relation',
                target_occurrence_id=relation_occurrence_id,
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
        snapshot_id: str | None,
        target_kind: str,
        target_occurrence_id: str,
        scope: str,
        annotation: object,
    ) -> bool:
        row = _annotation_to_row(annotation)
        if (
            target_kind == 'entity'
            and BulkMinimalIngestor._append_annotation_relation(
                buffers,
                source=source,
                dataset=dataset,
                row_id=row_id,
                snapshot_id=snapshot_id,
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
        buffers.annotations.append(
            (
                source,
                dataset,
                row_id,
                target_kind,
                target_occurrence_id,
                scope,
                _annotation_key(term, value, unit),
                term,
                value,
                unit,
            )
        )
        return True

    @staticmethod
    def _append_annotation_relation(
        buffers: _BulkBuffers,
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
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
                source,
                dataset,
                row_id,
                snapshot_id,
                spec.relation_occurrence_id,
                spec.subject_occurrence_id,
                spec.predicate_rule.predicate,
                spec.object_entity_type,
                spec.object_id_type,
                spec.object_id,
                spec.predicate_rule.relation_category or ASSOCIATION_CATEGORY,
            )
        )
        return True

    def _flush(self, buffers: _BulkBuffers) -> None:
        if not buffers:
            return
        with self.conn.cursor() as cur:
            _create_staging_tables(cur)
            _copy_rows(cur, 'stg_entity', _ENTITY_COLUMNS, buffers.entities)
            _copy_rows(
                cur,
                'stg_identifier_ref',
                _IDENTIFIER_REF_COLUMNS,
                buffers.identifiers,
            )
            _copy_rows(
                cur, 'stg_relation', _RELATION_COLUMNS, buffers.relations
            )
            _copy_rows(
                cur,
                'stg_annotation_relation',
                _ANNOTATION_RELATION_COLUMNS,
                buffers.annotation_relations,
            )
            _copy_rows(
                cur,
                'stg_annotation',
                _ANNOTATION_COLUMNS,
                buffers.annotations,
            )
            _index_staging_tables(cur)
            self._insert_from_staging(cur)
        self.conn.commit()
        buffers.clear()

    def _insert_from_staging(self, cur: psycopg2.extensions.cursor) -> None:
        schema = sql.Identifier(self.schema)
        cur.execute(
            sql.SQL(
                """
                WITH missing AS (
                  SELECT DISTINCT s.type AS name
                  FROM stg_identifier_ref s
                  LEFT JOIN {}.identifier_type it
                    ON it.name = s.type
                  WHERE s.type IS NOT NULL
                    AND it.identifier_type_id IS NULL
                ),
                base AS (
                  SELECT COALESCE(MAX(identifier_type_id), 0) AS max_id
                  FROM {}.identifier_type
                )
                INSERT INTO {}.identifier_type (identifier_type_id, name)
                SELECT
                  base.max_id + row_number() OVER (ORDER BY missing.name),
                  missing.name
                FROM missing
                CROSS JOIN base
                ON CONFLICT (name) DO NOTHING
                """
            ).format(schema, schema, schema)
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.identifier_evidence (identifier_type_id, value)
                SELECT DISTINCT it.identifier_type_id, s.value
                FROM stg_identifier_ref s
                JOIN {}.identifier_type it
                  ON it.name = s.type
                WHERE s.type IS NOT NULL AND s.value IS NOT NULL
                ON CONFLICT (identifier_type_id, value) DO NOTHING
                """
            ).format(schema, schema)
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.entity_evidence (
                  source, dataset, row_id, snapshot_id, occurrence_id,
                  parent_entity_evidence_id, entity_role, entity_type,
                  taxonomy_id
                )
                SELECT DISTINCT
                  source, dataset, row_id, snapshot_id, occurrence_id,
                  NULL::bigint, entity_role, entity_type, taxonomy_id
                FROM stg_entity
                ON CONFLICT (source, dataset, row_id, occurrence_id)
                DO NOTHING
                """
            ).format(schema)
        )
        cur.execute(
            sql.SQL(
                """
                UPDATE {}.entity_evidence child
                SET parent_entity_evidence_id = parent.entity_evidence_id
                FROM stg_entity s
                JOIN {}.entity_evidence parent
                  ON parent.source = s.source
                 AND parent.dataset = s.dataset
                 AND parent.row_id = s.row_id
                 AND parent.occurrence_id = s.parent_occurrence_id
                WHERE child.source = s.source
                  AND child.dataset = s.dataset
                  AND child.row_id = s.row_id
                  AND child.occurrence_id = s.occurrence_id
                  AND s.parent_occurrence_id IS NOT NULL
                  AND child.parent_entity_evidence_id IS DISTINCT FROM
                      parent.entity_evidence_id
                """
            ).format(schema, schema)
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.entity_evidence_identifier
                  (entity_evidence_id, identifier_id)
                SELECT DISTINCT e.entity_evidence_id, i.identifier_id
                FROM stg_identifier_ref s
                JOIN {}.entity_evidence e
                  ON e.source = s.source
                 AND e.dataset = s.dataset
                 AND e.row_id = s.row_id
                 AND e.occurrence_id = s.occurrence_id
                JOIN {}.identifier_type it
                  ON it.name = s.type
                JOIN {}.identifier_evidence i
                  ON i.identifier_type_id = it.identifier_type_id
                 AND i.value = s.value
                ON CONFLICT DO NOTHING
                """
            ).format(schema, schema, schema, schema)
        )
        cur.execute(
            sql.SQL(
                """
                WITH entity_type_rows AS (
                  INSERT INTO {}.entity_type (name)
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
                  NULL::text,
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
                JOIN {}.entity_type et
                  ON et.name = s.object_entity_type
                JOIN {}.identifier_type it
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
            ).format(schema, schema, schema, schema)
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.relation_evidence (
                  source, dataset, row_id, snapshot_id,
                  relation_occurrence_id, subject_entity_evidence_id,
                  subject_entity_id, predicate, object_entity_evidence_id,
                  object_entity_id, relation_category
                )
                SELECT DISTINCT
                  s.source, s.dataset, s.row_id, s.snapshot_id,
                  s.relation_occurrence_id,
                  subject.entity_evidence_id,
                  NULL::bigint,
                  s.predicate,
                  object.entity_evidence_id,
                  NULL::bigint,
                  s.relation_category
                FROM stg_relation s
                JOIN {}.entity_evidence subject
                  ON subject.source = s.source
                 AND subject.dataset = s.dataset
                 AND subject.row_id = s.row_id
                 AND subject.occurrence_id = s.subject_occurrence_id
                JOIN {}.entity_evidence object
                  ON object.source = s.source
                 AND object.dataset = s.dataset
                 AND object.row_id = s.row_id
                 AND object.occurrence_id = s.object_occurrence_id
                ON CONFLICT (source, dataset, row_id, relation_occurrence_id)
                DO NOTHING
                """
            ).format(schema, schema, schema)
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.relation_evidence (
                  source, dataset, row_id, snapshot_id,
                  relation_occurrence_id, subject_entity_evidence_id,
                  subject_entity_id, predicate, object_entity_evidence_id,
                  object_entity_id, relation_category
                )
                SELECT DISTINCT
                  s.source, s.dataset, s.row_id, s.snapshot_id,
                  s.relation_occurrence_id,
                  subject.entity_evidence_id,
                  NULL::bigint,
                  s.predicate,
                  NULL::bigint,
                  object.entity_id,
                  s.relation_category
                FROM stg_annotation_relation s
                JOIN {}.entity_evidence subject
                  ON subject.source = s.source
                 AND subject.dataset = s.dataset
                 AND subject.row_id = s.row_id
                 AND subject.occurrence_id = s.subject_occurrence_id
                JOIN {}.entity_type et
                  ON et.name = s.object_entity_type
                JOIN {}.entity object
                  ON object.entity_type_id = et.entity_type_id
                JOIN {}.identifier_type it
                  ON it.name = s.object_id_type
                 AND object.canonical_identifier_type_id =
                     it.identifier_type_id
                 AND object.canonical_identifier = s.object_id
                ON CONFLICT (source, dataset, row_id, relation_occurrence_id)
                DO NOTHING
                """
            ).format(schema, schema, schema, schema, schema)
        )
        cur.execute(
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
                FROM stg_annotation s
                WHERE s.term IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            ).format(schema)
        )
        _create_evidence_id_maps(cur, self.schema)
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.entity_evidence_annotation (
                  entity_evidence_id,
                  annotation_key,
                  scope
                )
                SELECT DISTINCT
                  em.entity_evidence_id,
                  s.annotation_key,
                  s.scope
                FROM stg_annotation s
                JOIN stg_entity_id_map em
                  ON em.source = s.source
                 AND em.dataset = s.dataset
                 AND em.row_id = s.row_id
                 AND em.occurrence_id = s.target_occurrence_id
                WHERE s.target_kind = 'entity'
                  AND s.scope IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            ).format(schema)
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {}.relation_evidence_annotation (
                  relation_evidence_id,
                  annotation_key,
                  scope
                )
                SELECT DISTINCT
                  rm.relation_evidence_id,
                  s.annotation_key,
                  s.scope
                FROM stg_annotation s
                JOIN stg_relation_id_map rm
                  ON rm.source = s.source
                 AND rm.dataset = s.dataset
                 AND rm.row_id = s.row_id
                 AND rm.relation_occurrence_id = s.target_occurrence_id
                WHERE s.target_kind = 'relation'
                  AND s.scope IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            ).format(schema)
        )
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
class _BulkBuffers:
    entities: list[tuple[object, ...]] = field(default_factory=list)
    identifiers: list[tuple[object, ...]] = field(default_factory=list)
    relations: list[tuple[object, ...]] = field(default_factory=list)
    annotation_relations: list[tuple[object, ...]] = field(default_factory=list)
    annotations: list[tuple[object, ...]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(
            self.entities
            or self.identifiers
            or self.relations
            or self.annotation_relations
            or self.annotations
        )

    def clear(self) -> None:
        self.entities.clear()
        self.identifiers.clear()
        self.relations.clear()
        self.annotation_relations.clear()
        self.annotations.clear()


_ENTITY_COLUMNS = (
    'source',
    'dataset',
    'row_id',
    'snapshot_id',
    'occurrence_id',
    'parent_occurrence_id',
    'entity_role',
    'entity_type',
    'taxonomy_id',
)
_IDENTIFIER_REF_COLUMNS = (
    'source',
    'dataset',
    'row_id',
    'occurrence_id',
    'type',
    'value',
)
_RELATION_COLUMNS = (
    'source',
    'dataset',
    'row_id',
    'snapshot_id',
    'relation_occurrence_id',
    'subject_occurrence_id',
    'predicate',
    'object_occurrence_id',
    'relation_category',
)
_ANNOTATION_RELATION_COLUMNS = (
    'source',
    'dataset',
    'row_id',
    'snapshot_id',
    'relation_occurrence_id',
    'subject_occurrence_id',
    'predicate',
    'object_entity_type',
    'object_id_type',
    'object_id',
    'relation_category',
)
_ANNOTATION_COLUMNS = (
    'source',
    'dataset',
    'row_id',
    'target_kind',
    'target_occurrence_id',
    'scope',
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
    cur.execute('DROP TABLE IF EXISTS stg_annotation')
    cur.execute(
        """
        CREATE TEMP TABLE stg_entity (
          source text,
          dataset text,
          row_id bigint,
          snapshot_id text,
          occurrence_id text,
          parent_occurrence_id text,
          entity_role text,
          entity_type text,
          taxonomy_id text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_identifier_ref (
          source text,
          dataset text,
          row_id bigint,
          occurrence_id text,
          type text,
          value text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_relation (
          source text,
          dataset text,
          row_id bigint,
          snapshot_id text,
          relation_occurrence_id text,
          subject_occurrence_id text,
          predicate text,
          object_occurrence_id text,
          relation_category text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_annotation_relation (
          source text,
          dataset text,
          row_id bigint,
          snapshot_id text,
          relation_occurrence_id text,
          subject_occurrence_id text,
          predicate text,
          object_entity_type text,
          object_id_type text,
          object_id text,
          relation_category text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE stg_annotation (
          source text,
          dataset text,
          row_id bigint,
          target_kind text,
          target_occurrence_id text,
          scope text,
          annotation_key uuid,
          term text,
          value text,
          unit text
        ) ON COMMIT DROP
        """
    )


def _index_staging_tables(cur: psycopg2.extensions.cursor) -> None:
    cur.execute(
        """
        CREATE INDEX stg_annotation_target_idx
        ON stg_annotation (
          target_kind,
          source,
          dataset,
          row_id,
          target_occurrence_id
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX stg_annotation_value_idx
        ON stg_annotation (term, value, unit)
        """
    )
    cur.execute(
        """
        CREATE INDEX stg_annotation_relation_key_idx
        ON stg_annotation_relation (
          source,
          dataset,
          row_id,
          relation_occurrence_id
        )
        """
    )
    for table in (
        'stg_entity',
        'stg_identifier_ref',
        'stg_relation',
        'stg_annotation_relation',
        'stg_annotation',
    ):
        cur.execute(sql.SQL('ANALYZE {}').format(sql.Identifier(table)))


def _create_evidence_id_maps(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> None:
    schema_id = sql.Identifier(schema)
    cur.execute('DROP TABLE IF EXISTS stg_entity_id_map')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE stg_entity_id_map
            ON COMMIT DROP AS
            SELECT DISTINCT
              s.source,
              s.dataset,
              s.row_id,
              s.occurrence_id,
              e.entity_evidence_id
            FROM stg_entity s
            JOIN {}.entity_evidence e
              ON e.source = s.source
             AND e.dataset = s.dataset
             AND e.row_id = s.row_id
             AND e.occurrence_id = s.occurrence_id
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX stg_entity_id_map_key_idx
        ON stg_entity_id_map (
          source,
          dataset,
          row_id,
          occurrence_id
        )
        """
    )
    cur.execute('DROP TABLE IF EXISTS stg_relation_id_map')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE stg_relation_id_map
            ON COMMIT DROP AS
            SELECT DISTINCT
              s.source,
              s.dataset,
              s.row_id,
              s.relation_occurrence_id,
              r.relation_evidence_id
            FROM (
              SELECT
                source,
                dataset,
                row_id,
                relation_occurrence_id
              FROM stg_relation
              UNION
              SELECT
                source,
                dataset,
                row_id,
                relation_occurrence_id
              FROM stg_annotation_relation
            ) s
            JOIN {}.relation_evidence r
              ON r.source = s.source
             AND r.dataset = s.dataset
             AND r.row_id = s.row_id
             AND r.relation_occurrence_id = s.relation_occurrence_id
            """
        ).format(schema_id)
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX stg_relation_id_map_key_idx
        ON stg_relation_id_map (
          source,
          dataset,
          row_id,
          relation_occurrence_id
        )
        """
    )
    for table in ('stg_entity_id_map', 'stg_relation_id_map'):
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
