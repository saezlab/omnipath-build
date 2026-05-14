from __future__ import annotations

import time
from datetime import UTC, datetime
from collections.abc import Iterable

from psycopg2 import sql
import psycopg2.extensions

from minimal.ingest.common import (
    CV_TERM_ID_TYPE,
    CV_TERM_ENTITY_TYPE,
    IngestStats,
    MutableStats as _MutableStats,
    entity_to_row as _entity_to_row,
    unwrap_record as _unwrap,
    annotation_to_row as _annotation_to_row,
    annotations_to_rows as _annotations_to_rows,
    is_interaction_like,
    membership_relation_spec,
    interaction_relation_spec,
    ontology_annotation_relation,
    extract_taxonomy_id,
)
from minimal.ingest.preparse import ProvenancedRecord
from pypath.internals.silver_schema import Entity
from omnipath_build.gold.utils.schema import (
    ASSOCIATION_CATEGORY,
    PredicateRule,
    string_or_none,
)

class MinimalIngestor:
    """Stream silver entities into minimal evidence tables."""

    def __init__(
        self,
        conn: psycopg2.extensions.connection,
        *,
        schema: str = 'minimal',
    ) -> None:
        self.conn = conn
        self.schema = schema
        self._identifier_cache: dict[tuple[str, str], int] = {}

    def ingest_records(
        self,
        records: Iterable[Entity | ProvenancedRecord],
        *,
        source: str,
        dataset: str,
        commit_every: int = 1000,
        progress_every: int = 1000,
    ) -> IngestStats:
        """Ingest records with per-row inserts and periodic commits."""

        stats = _MutableStats()
        started_at = time.monotonic()
        for index, item in enumerate(records, start=1):
            entity, provenance = _unwrap(item)
            if not isinstance(entity, Entity):
                continue
            row_id = int(provenance.raw_record_id) if provenance else index
            snapshot_id = provenance.snapshot_id if provenance else None
            self._insert_source_row(source, dataset, row_id, snapshot_id)
            self._ingest_entity_tree(
                entity,
                source=source,
                dataset=dataset,
                row_id=row_id,
                snapshot_id=snapshot_id,
                occurrence_id=f'{dataset}:{row_id}:parent',
                parent_entity_evidence_id=None,
                entity_role='parent',
                stats=stats,
            )
            self._mark_source_row_processed(source, dataset, row_id)
            stats.source_rows += 1
            if commit_every > 0 and index % commit_every == 0:
                self.conn.commit()
            if progress_every > 0 and stats.source_rows % progress_every == 0:
                elapsed = time.monotonic() - started_at
                rate = stats.source_rows / elapsed if elapsed else 0.0
                print(
                    f'[{source}.{dataset}] ingest progress '
                    f'rows={stats.source_rows:,} '
                    f'entities={stats.entity_evidence:,} '
                    f'relations={stats.relation_evidence:,} '
                    f'identifiers={stats.identifiers:,} '
                    f'annotations={stats.annotations:,} '
                    f'rate={rate:,.1f}/s',
                    flush=True,
                )
        self.conn.commit()
        return stats.freeze()

    def _ingest_entity_tree(
        self,
        entity: Entity,
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        occurrence_id: str,
        parent_entity_evidence_id: int | None,
        entity_role: str,
        stats: _MutableStats,
    ) -> int:
        row = _entity_to_row(entity)
        entity_evidence_id = self._insert_entity_evidence(
            source=source,
            dataset=dataset,
            row_id=row_id,
            snapshot_id=snapshot_id,
            occurrence_id=occurrence_id,
            parent_entity_evidence_id=parent_entity_evidence_id,
            entity_role=entity_role,
            entity_type=row.get('type'),
            taxonomy_id=extract_taxonomy_id(row),
        )
        stats.entity_evidence += 1

        for identifier in row.get('identifiers') or []:
            ident_type = string_or_none(identifier.get('type'))
            ident_value = string_or_none(identifier.get('value'))
            if ident_type is None or ident_value is None:
                continue
            identifier_id = self._identifier_id(ident_type, ident_value)
            self._link_entity_identifier(entity_evidence_id, identifier_id)
            stats.identifiers += 1

        for annotation in row.get('annotations') or []:
            relation_id = self._insert_annotation_relation_evidence(
                source=source,
                dataset=dataset,
                row_id=row_id,
                snapshot_id=snapshot_id,
                subject_entity_evidence_id=entity_evidence_id,
                subject_occurrence_id=occurrence_id,
                annotation=annotation,
            )
            if relation_id is not None:
                stats.relation_evidence += 1
                continue
            if self._insert_annotation(
                scope='entity',
                annotation=annotation,
                entity_evidence_id=entity_evidence_id,
                relation_evidence_id=None,
            ):
                stats.annotations += 1

        memberships = list(getattr(entity, 'membership', None) or [])
        member_ids: list[tuple[int, object]] = []
        for member_index, membership in enumerate(memberships):
            member = getattr(membership, 'member', None)
            if member is None:
                continue
            member_id = self._ingest_entity_tree(
                member,
                source=source,
                dataset=dataset,
                row_id=row_id,
                snapshot_id=snapshot_id,
                occurrence_id=f'{occurrence_id}:member:{member_index}',
                parent_entity_evidence_id=entity_evidence_id,
                entity_role='member',
                stats=stats,
            )
            member_ids.append((member_id, membership))

        entity_type = string_or_none(row.get('type'))
        if is_interaction_like(entity_type) and len(member_ids) == 2:
            relation_id = self._insert_interaction_relation(
                row,
                member_ids,
                source=source,
                dataset=dataset,
                row_id=row_id,
                snapshot_id=snapshot_id,
                occurrence_id=occurrence_id,
            )
            if relation_id is not None:
                stats.relation_evidence += 1
                stats.annotations += self._insert_relation_annotations(
                    relation_id,
                    row.get('annotations') or [],
                    scope='relation',
                )
        elif member_ids:
            for member_index, (member_id, membership) in enumerate(member_ids):
                relation_id = self._insert_membership_relation(
                    parent_id=entity_evidence_id,
                    member_id=member_id,
                    membership=membership,
                    parent_type=entity_type,
                    source=source,
                    dataset=dataset,
                    row_id=row_id,
                    snapshot_id=snapshot_id,
                    relation_occurrence_id=(
                        f'{occurrence_id}:membership:{member_index}'
                    ),
                )
                if relation_id is not None:
                    stats.relation_evidence += 1
                    stats.annotations += self._insert_relation_annotations(
                        relation_id,
                        getattr(membership, 'annotations', None) or [],
                        scope='membership',
                    )

        return entity_evidence_id

    def _insert_interaction_relation(
        self,
        row: dict[str, object],
        member_ids: list[tuple[int, object]],
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        occurrence_id: str,
    ) -> int | None:
        spec = interaction_relation_spec(
            row,
            member_ids,
            occurrence_id=occurrence_id,
        )
        if spec is None:
            return None
        return self._insert_relation_evidence(
            source=source,
            dataset=dataset,
            row_id=row_id,
            snapshot_id=snapshot_id,
            relation_occurrence_id=spec.relation_occurrence_id,
            subject_entity_evidence_id=int(spec.subject_ref),
            predicate_rule=spec.predicate_rule,
            object_entity_evidence_id=int(spec.object_ref),
        )

    def _insert_membership_relation(
        self,
        *,
        parent_id: int,
        member_id: int,
        membership: object,
        parent_type: str | None,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        relation_occurrence_id: str,
    ) -> int | None:
        spec = membership_relation_spec(
            parent_ref=parent_id,
            member_ref=member_id,
            membership=membership,
            parent_type=parent_type,
            relation_occurrence_id=relation_occurrence_id,
        )
        return self._insert_relation_evidence(
            source=source,
            dataset=dataset,
            row_id=row_id,
            snapshot_id=snapshot_id,
            relation_occurrence_id=spec.relation_occurrence_id,
            subject_entity_evidence_id=int(spec.subject_ref),
            predicate_rule=spec.predicate_rule,
            object_entity_evidence_id=int(spec.object_ref),
        )

    def _insert_source_row(
        self,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.source_row
                      (source, dataset, row_id, snapshot_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (source, dataset, row_id) DO NOTHING
                    """
                ).format(sql.Identifier(self.schema)),
                [source, dataset, row_id, snapshot_id],
            )

    def _mark_source_row_processed(
        self,
        source: str,
        dataset: str,
        row_id: int,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    UPDATE {}.source_row
                    SET processed_at = %s
                    WHERE source = %s AND dataset = %s AND row_id = %s
                    """
                ).format(sql.Identifier(self.schema)),
                [datetime.now(UTC), source, dataset, row_id],
            )

    def _insert_entity_evidence(
        self,
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        occurrence_id: str,
        parent_entity_evidence_id: int | None,
        entity_role: str,
        entity_type: str | None,
        taxonomy_id: str | None,
    ) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.entity_evidence (
                      source, dataset, row_id, snapshot_id, occurrence_id,
                      parent_entity_evidence_id, entity_role, entity_type,
                      taxonomy_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source, dataset, row_id, occurrence_id)
                    DO NOTHING
                    RETURNING entity_evidence_id
                    """
                ).format(sql.Identifier(self.schema)),
                [
                    source,
                    dataset,
                    row_id,
                    snapshot_id,
                    occurrence_id,
                    parent_entity_evidence_id,
                    entity_role,
                    entity_type,
                    taxonomy_id,
                ],
            )
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute(
                sql.SQL(
                    """
                    SELECT entity_evidence_id
                    FROM {}.entity_evidence
                    WHERE source = %s
                      AND dataset = %s
                      AND row_id = %s
                      AND occurrence_id = %s
                    """
                ).format(sql.Identifier(self.schema)),
                [source, dataset, row_id, occurrence_id],
            )
            return int(cur.fetchone()[0])

    def _identifier_id(self, ident_type: str, ident_value: str) -> int:
        key = (ident_type, ident_value)
        cached = self._identifier_cache.get(key)
        if cached is not None:
            return cached
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.identifier (type, value)
                    VALUES (%s, %s)
                    ON CONFLICT (type, value_hash) DO NOTHING
                    RETURNING identifier_id
                    """
                ).format(sql.Identifier(self.schema)),
                [ident_type, ident_value],
            )
            row = cur.fetchone()
            if row:
                identifier_id = int(row[0])
            else:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT identifier_id
                        FROM {}.identifier
                        WHERE type = %s
                          AND value_hash = md5(%s)
                          AND value = %s
                        """
                    ).format(sql.Identifier(self.schema)),
                    [ident_type, ident_value, ident_value],
                )
                identifier_id = int(cur.fetchone()[0])
        self._identifier_cache[key] = identifier_id
        return identifier_id

    def _link_entity_identifier(
        self,
        entity_evidence_id: int,
        identifier_id: int,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.entity_evidence_identifier
                      (entity_evidence_id, identifier_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """
                ).format(sql.Identifier(self.schema)),
                [entity_evidence_id, identifier_id],
            )

    def _insert_relation_evidence(
        self,
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        relation_occurrence_id: str,
        subject_entity_evidence_id: int | None,
        predicate_rule: PredicateRule,
        object_entity_evidence_id: int | None = None,
        object_entity_id: int | None = None,
    ) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.relation_evidence (
                      source, dataset, row_id, snapshot_id,
                      relation_occurrence_id, subject_entity_evidence_id,
                      subject_entity_id, predicate, object_entity_evidence_id,
                      object_entity_id, relation_category
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s)
                    ON CONFLICT (source, dataset, row_id, relation_occurrence_id)
                    DO NOTHING
                    RETURNING relation_evidence_id
                    """
                ).format(sql.Identifier(self.schema)),
                [
                    source,
                    dataset,
                    row_id,
                    snapshot_id,
                    relation_occurrence_id,
                    subject_entity_evidence_id,
                    predicate_rule.predicate,
                    object_entity_evidence_id,
                    object_entity_id,
                    predicate_rule.relation_category or ASSOCIATION_CATEGORY,
                ],
            )
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute(
                sql.SQL(
                    """
                    SELECT relation_evidence_id
                    FROM {}.relation_evidence
                    WHERE source = %s
                      AND dataset = %s
                      AND row_id = %s
                      AND relation_occurrence_id = %s
                    """
                ).format(sql.Identifier(self.schema)),
                [source, dataset, row_id, relation_occurrence_id],
            )
            return int(cur.fetchone()[0])

    def _insert_annotation_relation_evidence(
        self,
        *,
        source: str,
        dataset: str,
        row_id: int,
        snapshot_id: str | None,
        subject_entity_evidence_id: int,
        subject_occurrence_id: str,
        annotation: object,
    ) -> int | None:
        spec = ontology_annotation_relation(
            _annotation_to_row(annotation),
            subject_occurrence_id=subject_occurrence_id,
        )
        if spec is None:
            return None
        object_entity_id = self._upsert_cv_term_entity(spec.object_id)
        return self._insert_relation_evidence(
            source=source,
            dataset=dataset,
            row_id=row_id,
            snapshot_id=snapshot_id,
            relation_occurrence_id=spec.relation_occurrence_id,
            subject_entity_evidence_id=subject_entity_evidence_id,
            predicate_rule=spec.predicate_rule,
            object_entity_id=object_entity_id,
        )

    def _upsert_cv_term_entity(self, term_id: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.entity (
                      entity_type,
                      id_type,
                      id,
                      taxonomy_id,
                      resolution_status
                    )
                    VALUES (%s, %s, %s, NULL, 'resolved')
                    ON CONFLICT (entity_type, id_type, id_hash)
                    DO UPDATE SET resolution_status = 'resolved'
                    RETURNING entity_id
                    """
                ).format(sql.Identifier(self.schema)),
                [CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE, term_id],
            )
            return int(cur.fetchone()[0])

    def _insert_relation_annotations(
        self,
        relation_evidence_id: int,
        annotations: Iterable[object],
        *,
        scope: str,
    ) -> int:
        count = 0
        for annotation in _annotations_to_rows(annotations):
            if self._insert_annotation(
                scope=scope,
                annotation=annotation,
                entity_evidence_id=None,
                relation_evidence_id=relation_evidence_id,
            ):
                count += 1
        return count

    def _insert_annotation(
        self,
        *,
        scope: str,
        annotation: object,
        entity_evidence_id: int | None,
        relation_evidence_id: int | None,
    ) -> bool:
        row = _annotation_to_row(annotation)
        term = string_or_none(row.get('term'))
        if term is None:
            return False
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.annotation (
                      term, value, unit, scope,
                      entity_evidence_id, relation_evidence_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """
                ).format(sql.Identifier(self.schema)),
                [
                    term,
                    string_or_none(row.get('value')),
                    string_or_none(row.get('unit')),
                    scope,
                    entity_evidence_id,
                    relation_evidence_id,
                ],
            )
            return bool(cur.rowcount)
