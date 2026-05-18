"""Experimental Parquet + DuckDB build path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from io import StringIO
import argparse
import csv
import os
import time

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import psycopg2
import psycopg2.extensions
from psycopg2 import sql

from omnipath_build.cv_terms import CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE
from omnipath_build.ingest.bulk import (
    BulkIngestor,
    _create_staging_tables,
    _index_staging_tables,
)
from omnipath_build.ingest.common import unwrap_record
from omnipath_build.parquet_projector import (
    ParquetEvidenceProjector,
    _MutableProjectionStats,
)
from omnipath_build.relation_rules import ASSOCIATION_CATEGORY
from pypath.internals.silver_schema import Entity
from pypath.internals.ontology_schema import OntologyTerm
from pypath.inputs_v2.uniprot import resource as uniprot_resource


SOURCE = 'uniprot'
DATASET = 'proteins'
ONTOLOGY_DATASET = 'ontology'
ONTOLOGY_ID = 'uniprot_keywords'
PROTEIN_ENTITY_TYPE = 'Protein:MI:0326'
UNIPROT_ID_TYPE = 'Uniprot:MI:1097'


@dataclass(frozen=True)
class ParquetDuckDBStats:
    source_rows: int
    identifiers: int
    annotations: int
    resolver_seconds: float
    projection_seconds: float
    canonicalize_seconds: float
    entities: int
    relations: int
    annotation_relation_links: int
    ontology_terms: int = 0
    copied_entities: int = 0
    copied_entity_resolutions: int = 0
    copied_relations: int = 0
    copied_annotation_relation_links: int = 0
    copied_evidence_entities: int = 0
    copied_evidence_identifiers: int = 0
    copied_evidence_relations: int = 0
    copied_evidence_annotations: int = 0
    bulk_loaded: bool = False


def run_uniprot_parquet_build(
    *,
    output_dir: str | Path = 'data/experiments/uniprot_duckdb',
    resolver_dir: str | Path = 'data/proteins',
    max_records: int | None = None,
    force_refresh: bool = False,
    database_url: str | None = None,
    schema: str = 'public',
    copy_evidence: bool = False,
    copy_final: bool = False,
    bulk_load: bool = False,
    include_ontology: bool = True,
) -> ParquetDuckDBStats:
    """Project UniProt to Parquet and canonicalize it in DuckDB."""

    output_dir = Path(output_dir)
    evidence_dir = output_dir / 'evidence'
    ontology_dir = output_dir / 'ontology'
    resolver_projection_dir = output_dir / 'resolver'
    final_dir = output_dir / 'final'
    evidence_dir.mkdir(parents=True, exist_ok=True)
    ontology_dir.mkdir(parents=True, exist_ok=True)
    resolver_projection_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    resolver_started = time.perf_counter()
    _materialize_resolver_tables(
        resolver_dir=Path(resolver_dir),
        output_dir=resolver_projection_dir,
        force_refresh=force_refresh,
    )
    resolver_seconds = time.perf_counter() - resolver_started

    projection_started = time.perf_counter()
    source_rows, identifiers, annotations = _project_uniprot(
        evidence_dir=evidence_dir,
        max_records=max_records,
        force_refresh=force_refresh,
    )
    ontology_terms = 0
    if include_ontology:
        ontology_terms = _project_uniprot_ontology(
            ontology_dir=ontology_dir,
            force_refresh=force_refresh,
        )
    projection_seconds = time.perf_counter() - projection_started

    canonicalize_started = time.perf_counter()
    entities, relations, links = _canonicalize_duckdb(
        evidence_dir=evidence_dir,
        final_dir=final_dir,
        resolver_dir=resolver_projection_dir,
        state_path=output_dir / 'state.duckdb',
    )
    canonicalize_seconds = time.perf_counter() - canonicalize_started
    copied = (0, 0, 0, 0)
    copied_evidence = (0, 0, 0, 0)
    if database_url and bulk_load:
        bulk_load_parquet_to_postgres(
            database_url=database_url,
            evidence_dir=evidence_dir,
            ontology_dir=ontology_dir if include_ontology else None,
            final_dir=final_dir,
            schema=schema,
            state_path=output_dir / 'bulk_load.duckdb',
        )
    elif database_url:
        with psycopg2.connect(database_url) as conn:
            if copy_evidence:
                copied_evidence = copy_evidence_parquet_to_postgres(
                    conn,
                    evidence_dir=evidence_dir,
                    schema=schema,
                )
            if copy_final:
                copied = copy_final_parquet_to_postgres(
                    conn,
                    final_dir=final_dir,
                    schema=schema,
                )
            conn.commit()
    return ParquetDuckDBStats(
        source_rows=source_rows,
        identifiers=identifiers,
        annotations=annotations,
        resolver_seconds=resolver_seconds,
        projection_seconds=projection_seconds,
        canonicalize_seconds=canonicalize_seconds,
        entities=entities,
        relations=relations,
        annotation_relation_links=links,
        ontology_terms=ontology_terms,
        copied_entities=copied[0],
        copied_entity_resolutions=copied[1],
        copied_relations=copied[2],
        copied_annotation_relation_links=copied[3],
        copied_evidence_entities=copied_evidence[0],
        copied_evidence_identifiers=copied_evidence[1],
        copied_evidence_relations=copied_evidence[2],
        copied_evidence_annotations=copied_evidence[3],
        bulk_loaded=bulk_load,
    )


def run_uniprot_duckdb_direct_build(
    *,
    database_url: str,
    resolver_dir: str | Path = 'data/proteins',
    max_records: int = 50_000,
    force_refresh: bool = False,
    schema: str = 'public',
    state_path: str | Path | None = None,
) -> ParquetDuckDBStats:
    """Project and canonicalize UniProt in DuckDB, then load Postgres directly."""

    resolver_dir = Path(resolver_dir)
    con = duckdb.connect(str(state_path) if state_path is not None else ':memory:')
    con.execute("SET threads TO 4")

    resolver_started = time.perf_counter()
    _create_duckdb_resolver_views(con, resolver_dir=resolver_dir)
    resolver_seconds = time.perf_counter() - resolver_started

    projection_started = time.perf_counter()
    source_rows, identifiers, annotations = _project_uniprot_to_duckdb(
        con,
        max_records=max_records,
        force_refresh=force_refresh,
    )
    projection_seconds = time.perf_counter() - projection_started

    canonicalize_started = time.perf_counter()
    entities, relations, links = _canonicalize_loaded_duckdb(con)
    canonicalize_seconds = time.perf_counter() - canonicalize_started

    con.execute('LOAD postgres')
    con.execute(f"ATTACH {_sql_literal(database_url)} AS pg (TYPE postgres)")
    _bulk_load_create_views_from_loaded_tables(con)
    _bulk_load_assert_empty(con, schema)
    _bulk_load_small_dimensions(con, schema)
    _bulk_load_evidence(con, schema)
    _bulk_load_canonical(con, schema)
    con.close()
    _reset_postgres_sequences(database_url=database_url, schema=schema)

    return ParquetDuckDBStats(
        source_rows=source_rows,
        identifiers=identifiers,
        annotations=annotations,
        resolver_seconds=resolver_seconds,
        projection_seconds=projection_seconds,
        canonicalize_seconds=canonicalize_seconds,
        entities=entities,
        relations=relations,
        annotation_relation_links=links,
        bulk_loaded=True,
    )


def _project_uniprot(
    *,
    evidence_dir: Path,
    max_records: int | None,
    force_refresh: bool,
) -> tuple[int, int, int]:
    records = uniprot_resource.proteins(
        force_refresh=force_refresh,
        source=SOURCE,
        dataset=DATASET,
    )
    stats = ParquetEvidenceProjector(evidence_dir).project_records(
        records,
        source=SOURCE,
        dataset=DATASET,
        max_records=max_records,
    )
    return stats.source_rows, stats.identifiers, stats.annotations


def _project_uniprot_to_duckdb(
    con: duckdb.DuckDBPyConnection,
    *,
    max_records: int,
    force_refresh: bool,
) -> tuple[int, int, int]:
    _create_duckdb_evidence_tables(con)
    records = uniprot_resource.proteins(
        force_refresh=force_refresh,
        source=SOURCE,
        dataset=DATASET,
    )
    stats = DuckDBEvidenceProjector(con).project_records(
        records,
        source=SOURCE,
        dataset=DATASET,
        max_records=max_records,
    )
    return stats.source_rows, stats.identifiers, stats.annotations


def _project_uniprot_ontology(
    *,
    ontology_dir: Path,
    force_refresh: bool,
) -> int:
    path = ontology_dir / 'ontology_terms.parquet'
    if path.exists() and not force_refresh:
        return int(pq.ParquetFile(path).metadata.num_rows)
    records = uniprot_resource.ontology(force_refresh=force_refresh)
    return _project_ontology_terms(
        records,
        path=path,
        source=SOURCE,
        dataset=ONTOLOGY_DATASET,
        ontology_id=ONTOLOGY_ID,
    )


def _project_ontology_terms(
    records: Iterable[OntologyTerm],
    *,
    path: Path,
    source: str,
    dataset: str,
    ontology_id: str,
) -> int:
    rows = []
    for term in records:
        if not isinstance(term, OntologyTerm) or not term.id:
            continue
        synonyms = _ontology_term_synonyms(term)
        rows.append(
            {
                'source': source,
                'dataset': dataset,
                'term_id': term.id,
                'ontology_prefix': _ontology_prefix(term.id),
                'label': term.name or term.id,
                'definition': term.definition,
                'ontology_id': ontology_id,
                'synonyms': synonyms,
                'synonyms_text': ' '.join(synonyms),
                'sources': [ontology_id],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema(
        [
            ('source', pa.string()),
            ('dataset', pa.string()),
            ('term_id', pa.string()),
            ('ontology_prefix', pa.string()),
            ('label', pa.string()),
            ('definition', pa.string()),
            ('ontology_id', pa.string()),
            ('synonyms', pa.list_(pa.string())),
            ('synonyms_text', pa.string()),
            ('sources', pa.list_(pa.string())),
        ]
    )
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path, compression='zstd')
    return len(rows)


def _ontology_prefix(term_id: str) -> str | None:
    if term_id.upper().startswith('KW-'):
        return 'kw'
    if ':' in term_id:
        return term_id.split(':', 1)[0].lower()
    return None


def _ontology_term_synonyms(term: OntologyTerm) -> list[str]:
    values = [
        *(term.synonyms or []),
        *(
            alt_id
            for alt_id in term.alt_ids or []
            if alt_id and alt_id != term.id
        ),
    ]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if not value or value == term.name or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _materialize_resolver_tables(
    *,
    resolver_dir: Path,
    output_dir: Path,
    force_refresh: bool,
) -> None:
    """Write the DuckDB resolver shape: vertical lookup plus canonical entity."""

    output_dir.mkdir(parents=True, exist_ok=True)
    identifier_type_path = output_dir / 'identifier_type.parquet'
    lookup_path = output_dir / 'vertical_lookup.parquet'
    entity_path = output_dir / 'canonical_entity.parquet'
    if (
        not force_refresh
        and identifier_type_path.exists()
        and lookup_path.exists()
        and entity_path.exists()
    ):
        return
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT *
          FROM read_parquet({_sql_literal(resolver_dir / 'identifier_type.parquet')})
        )
        TO {_sql_literal(identifier_type_path)} (FORMAT PARQUET)
        """
    )
    con.execute(
        f"""
        COPY (
          SELECT
            key_identifier_type_id,
            key_value,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            mapping_type
          FROM read_parquet({_sql_literal(resolver_dir / 'protein_identifier_lookup.parquet')})
          WHERE key_value IS NOT NULL
            AND canonical_identifier IS NOT NULL
        )
        TO {_sql_literal(lookup_path)} (FORMAT PARQUET)
        """
    )
    con.execute(
        f"""
        COPY (
          SELECT
            row_number() OVER (
              ORDER BY
                taxonomy_id NULLS FIRST,
                canonical_identifier_type_id,
                canonical_identifier
            )::BIGINT AS resolver_entity_id,
            {_sql_literal(PROTEIN_ENTITY_TYPE)} AS entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            list_distinct(list(key_identifier_type_id)) AS key_identifier_type_ids,
            count(*)::BIGINT AS lookup_rows
          FROM read_parquet({_sql_literal(resolver_dir / 'protein_identifier_lookup.parquet')})
          WHERE canonical_identifier IS NOT NULL
          GROUP BY
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier
        )
        TO {_sql_literal(entity_path)} (FORMAT PARQUET)
        """
    )
    con.close()


def _create_duckdb_resolver_views(
    con: duckdb.DuckDBPyConnection,
    *,
    resolver_dir: Path,
) -> None:
    """Expose resolver parquet inputs in the DuckDB shape used by canonicalize."""

    con.execute(
        f"""
        CREATE VIEW identifier_type AS
        SELECT *
        FROM read_parquet({_sql_literal(resolver_dir / 'identifier_type.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW identifier_type_all AS
        SELECT * FROM identifier_type
        UNION ALL
        SELECT
          (SELECT coalesce(max(identifier_type_id), 0) + 1 FROM identifier_type)
            AS identifier_type_id,
          {_sql_literal(CV_TERM_ID_TYPE)} AS name
        WHERE NOT EXISTS (
          SELECT 1
          FROM identifier_type
          WHERE name = {_sql_literal(CV_TERM_ID_TYPE)}
        )
        """
    )
    con.execute(
        f"""
        CREATE VIEW resolver_lookup AS
        SELECT
          key_identifier_type_id,
          key_value,
          taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier,
          mapping_type
        FROM read_parquet({_sql_literal(resolver_dir / 'protein_identifier_lookup.parquet')})
        WHERE key_value IS NOT NULL
          AND canonical_identifier IS NOT NULL
        """
    )
    con.execute(
        f"""
        CREATE VIEW resolver_canonical_entity AS
        SELECT
          row_number() OVER (
            ORDER BY
              taxonomy_id NULLS FIRST,
              canonical_identifier_type_id,
              canonical_identifier
          )::BIGINT AS resolver_entity_id,
          {_sql_literal(PROTEIN_ENTITY_TYPE)} AS entity_type,
          taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier,
          list_distinct(list(key_identifier_type_id)) AS key_identifier_type_ids,
          count(*)::BIGINT AS lookup_rows
        FROM read_parquet({_sql_literal(resolver_dir / 'protein_identifier_lookup.parquet')})
        WHERE canonical_identifier IS NOT NULL
        GROUP BY
          taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier
        """
    )


class DuckDBEvidenceProjector(ParquetEvidenceProjector):
    """Flatten silver entity streams into DuckDB evidence tables."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        chunk_size: int = 100_000,
    ) -> None:
        super().__init__(':duckdb:', chunk_size=chunk_size)
        self.con = con

    def project_records(
        self,
        records: Iterable[object],
        *,
        source: str,
        dataset: str,
        max_records: int | None = None,
    ):
        if max_records is not None:
            records = islice(records, max_records)

        writers = _DuckDBEvidenceWriters(self.con, chunk_size=self.chunk_size)
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


class _DuckDBEvidenceWriters:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        chunk_size: int,
    ) -> None:
        self.entity = _DuckDBRowWriter(
            con,
            'entity_evidence_raw',
            _ENTITY_EVIDENCE_SCHEMA,
            chunk_size=chunk_size,
        )
        self.identifier = _DuckDBRowWriter(
            con,
            'entity_identifier_raw',
            _ENTITY_IDENTIFIER_SCHEMA,
            chunk_size=chunk_size,
        )
        self.entity_annotation = _DuckDBRowWriter(
            con,
            'entity_annotation_raw',
            _ANNOTATION_REF_SCHEMA,
            chunk_size=chunk_size,
        )
        self.relation_annotation = _DuckDBRowWriter(
            con,
            'relation_annotation_raw',
            _ANNOTATION_REF_SCHEMA,
            chunk_size=chunk_size,
        )
        self.annotation = _DuckDBRowWriter(
            con,
            'annotation_value',
            _ANNOTATION_VALUE_SCHEMA,
            chunk_size=chunk_size,
        )
        self.relation = _DuckDBRowWriter(
            con,
            'relation_evidence_raw',
            _RELATION_EVIDENCE_SCHEMA,
            chunk_size=chunk_size,
        )
        self.annotation_relation = _DuckDBRowWriter(
            con,
            'annotation_relation_evidence_raw',
            _ANNOTATION_RELATION_EVIDENCE_SCHEMA,
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


class _DuckDBRowWriter:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        table: str,
        schema: pa.Schema,
        *,
        chunk_size: int,
    ) -> None:
        self.con = con
        self.table = table
        self.schema = schema
        self.chunk_size = chunk_size
        self.rows: list[dict[str, object]] = []

    def write(self, row: dict[str, object]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.chunk_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        batch = f'_batch_{self.table}'
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        self.con.register(batch, table)
        try:
            self.con.execute(f'INSERT INTO {self.table} SELECT * FROM {batch}')
        finally:
            self.con.unregister(batch)
        self.rows.clear()

    def close(self) -> None:
        self.flush()


_ENTITY_EVIDENCE_SCHEMA = pa.schema(
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
)
_ENTITY_IDENTIFIER_SCHEMA = pa.schema(
    [
        ('source', pa.string()),
        ('entity_evidence_id', pa.string()),
        ('identifier_id', pa.string()),
        ('identifier_type', pa.string()),
        ('identifier', pa.string()),
    ]
)
_ANNOTATION_REF_SCHEMA = pa.schema(
    [
        ('source', pa.string()),
        ('evidence_id', pa.string()),
        ('annotation_key', pa.string()),
        ('term', pa.string()),
        ('value', pa.string()),
        ('unit', pa.string()),
    ]
)
_ANNOTATION_VALUE_SCHEMA = pa.schema(
    [
        ('annotation_key', pa.string()),
        ('term', pa.string()),
        ('value', pa.string()),
        ('unit', pa.string()),
    ]
)
_RELATION_EVIDENCE_SCHEMA = pa.schema(
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
)
_ANNOTATION_RELATION_EVIDENCE_SCHEMA = pa.schema(
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
)


def _create_duckdb_evidence_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE entity_evidence_raw (
          source VARCHAR,
          dataset VARCHAR,
          row_id BIGINT,
          entity_evidence_id VARCHAR,
          parent_entity_evidence_id VARCHAR,
          entity_role VARCHAR,
          entity_type VARCHAR,
          taxonomy_id VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE entity_identifier_raw (
          source VARCHAR,
          entity_evidence_id VARCHAR,
          identifier_id VARCHAR,
          identifier_type VARCHAR,
          identifier VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE entity_annotation_raw (
          source VARCHAR,
          evidence_id VARCHAR,
          annotation_key VARCHAR,
          term VARCHAR,
          value VARCHAR,
          unit VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE relation_annotation_raw (
          source VARCHAR,
          evidence_id VARCHAR,
          annotation_key VARCHAR,
          term VARCHAR,
          value VARCHAR,
          unit VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE annotation_value (
          annotation_key VARCHAR,
          term VARCHAR,
          value VARCHAR,
          unit VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE relation_evidence_raw (
          source VARCHAR,
          dataset VARCHAR,
          row_id BIGINT,
          relation_evidence_id VARCHAR,
          subject_entity_evidence_id VARCHAR,
          predicate VARCHAR,
          object_entity_evidence_id VARCHAR,
          relation_category VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE annotation_relation_evidence_raw (
          relation_evidence_id VARCHAR,
          source VARCHAR,
          dataset VARCHAR,
          row_id BIGINT,
          subject_entity_evidence_id VARCHAR,
          predicate VARCHAR,
          object_entity_type VARCHAR,
          object_id_type VARCHAR,
          object_id VARCHAR,
          relation_category VARCHAR
        )
        """
    )


def _canonicalize_duckdb(
    *,
    evidence_dir: Path,
    final_dir: Path,
    resolver_dir: Path,
    state_path: Path,
) -> tuple[int, int, int]:
    if state_path.exists():
        state_path.unlink()
    con = duckdb.connect(str(state_path))
    con.execute("SET threads TO 4")
    con.execute(
        f"""
        CREATE VIEW identifier_type AS
        SELECT *
        FROM read_parquet({_sql_literal(resolver_dir / 'identifier_type.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW identifier_type_all AS
        SELECT * FROM identifier_type
        UNION ALL
        SELECT
          (SELECT coalesce(max(identifier_type_id), 0) + 1 FROM identifier_type)
            AS identifier_type_id,
          {_sql_literal(CV_TERM_ID_TYPE)} AS name
        WHERE NOT EXISTS (
          SELECT 1
          FROM identifier_type
          WHERE name = {_sql_literal(CV_TERM_ID_TYPE)}
        )
        """
    )
    con.execute(
        f"""
        CREATE VIEW resolver_lookup AS
        SELECT *
        FROM read_parquet({_sql_literal(resolver_dir / 'vertical_lookup.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW resolver_canonical_entity AS
        SELECT *
        FROM read_parquet({_sql_literal(resolver_dir / 'canonical_entity.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW entity_evidence_raw AS
        SELECT *
        FROM read_parquet({_sql_literal(evidence_dir / 'entity_evidence.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW entity_identifier_raw AS
        SELECT *
        FROM read_parquet({_sql_literal(evidence_dir / 'entity_identifier.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW entity_annotation_raw AS
        SELECT
          source,
          evidence_id AS entity_evidence_id,
          annotation_key,
          term,
          value,
          unit
        FROM read_parquet({_sql_literal(evidence_dir / 'entity_annotation.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW annotation_value AS
        SELECT *
        FROM read_parquet({_sql_literal(evidence_dir / 'annotation.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW annotation_relation_evidence_raw AS
        SELECT *
        FROM read_parquet({_sql_literal(evidence_dir / 'annotation_relation_evidence.parquet')})
        """
    )
    con.execute(
        """
        CREATE TABLE entity_resolution AS
        SELECT
          ee.source,
          ee.dataset,
          ee.row_id,
          ee.entity_evidence_id,
          ee.entity_type,
          ee.taxonomy_id,
          rl.canonical_identifier_type_id,
          rl.canonical_identifier,
          CASE
            WHEN rl.canonical_identifier IS NULL THEN 'unresolved'
            ELSE 'resolved'
          END AS status
        FROM entity_evidence_raw ee
        LEFT JOIN entity_identifier_raw ei
          ON ei.source = ee.source
         AND ei.entity_evidence_id = ee.entity_evidence_id
        LEFT JOIN identifier_type_all kit
          ON kit.name = ei.identifier_type
        LEFT JOIN resolver_lookup rl
          ON rl.key_identifier_type_id = kit.identifier_type_id
         AND rl.key_value = ei.identifier
         AND rl.taxonomy_id = ee.taxonomy_id
        QUALIFY row_number() OVER (
          PARTITION BY ee.source, ee.entity_evidence_id
          ORDER BY rl.canonical_identifier IS NULL, rl.canonical_identifier
        ) = 1
        """
    )
    con.execute(
        """
        CREATE TABLE canonical_entity AS
        WITH needed_protein_key AS (
          SELECT DISTINCT
            rce.entity_type,
            rce.taxonomy_id,
            rce.canonical_identifier_type_id,
            rce.canonical_identifier
          FROM entity_resolution er
          JOIN resolver_canonical_entity rce
            ON rce.entity_type = er.entity_type
           AND rce.taxonomy_id = er.taxonomy_id
           AND rce.canonical_identifier_type_id = er.canonical_identifier_type_id
           AND rce.canonical_identifier = er.canonical_identifier
          WHERE er.status = 'resolved'
        ),
        protein_identifier_rows AS (
          SELECT
            np.entity_type,
            np.taxonomy_id,
            np.canonical_identifier_type_id,
            np.canonical_identifier,
            np.canonical_identifier_type_id AS identifier_type_id,
            np.canonical_identifier AS identifier
          FROM needed_protein_key np
          UNION
          SELECT
            np.entity_type,
            np.taxonomy_id,
            np.canonical_identifier_type_id,
            np.canonical_identifier,
            rl.key_identifier_type_id AS identifier_type_id,
            rl.key_value AS identifier
          FROM needed_protein_key np
          JOIN resolver_lookup rl
            ON rl.canonical_identifier_type_id = np.canonical_identifier_type_id
           AND rl.canonical_identifier = np.canonical_identifier
           AND rl.taxonomy_id = np.taxonomy_id
          WHERE rl.key_value IS NOT NULL
            AND rl.key_value <> ''
        ),
        needed_protein_entity AS (
          SELECT
            pir.entity_type,
            pir.taxonomy_id,
            pir.canonical_identifier_type_id,
            pir.canonical_identifier,
            to_json(
              list(
                struct_pack(
                  identifier_type := it.name,
                  identifier_type_id := pir.identifier_type_id,
                  identifier := pir.identifier
                )
                ORDER BY it.name, pir.identifier
              )
            ) AS identifiers_json
          FROM (
            SELECT DISTINCT *
            FROM protein_identifier_rows
          ) pir
          JOIN identifier_type_all it
            ON it.identifier_type_id = pir.identifier_type_id
          GROUP BY
            pir.entity_type,
            pir.taxonomy_id,
            pir.canonical_identifier_type_id,
            pir.canonical_identifier
        ),
        cv_term_entity AS (
          SELECT DISTINCT
            ? AS entity_type,
            NULL::VARCHAR AS taxonomy_id,
            cv_type.identifier_type_id AS canonical_identifier_type_id,
            object_id AS canonical_identifier,
            to_json(
              list(
                struct_pack(
                  identifier_type := ?,
                  identifier_type_id := cv_type.identifier_type_id,
                  identifier := object_id
                )
              )
            ) AS identifiers_json
          FROM annotation_relation_evidence_raw
          CROSS JOIN (
            SELECT identifier_type_id
            FROM identifier_type_all
            WHERE name = ?
          ) cv_type
          WHERE object_id_type = ?
            AND object_id IS NOT NULL
          GROUP BY cv_type.identifier_type_id, object_id
        ),
        all_entity AS (
          SELECT * FROM needed_protein_entity
          UNION ALL
          SELECT * FROM cv_term_entity
        )
        SELECT
          row_number() OVER (
            ORDER BY
              entity_type,
              taxonomy_id NULLS FIRST,
              canonical_identifier_type_id,
              canonical_identifier
          )::BIGINT AS entity_id,
          entity_type,
          taxonomy_id,
          canonical_identifier_type_id,
          it.name AS canonical_identifier_type,
          canonical_identifier,
          identifiers_json,
          'resolved' AS resolution_status
        FROM all_entity
        JOIN identifier_type_all it
          ON it.identifier_type_id = all_entity.canonical_identifier_type_id
        """,
        [CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE, CV_TERM_ID_TYPE, CV_TERM_ID_TYPE],
    )
    con.execute(
        """
        CREATE TABLE entity_evidence_resolution AS
        SELECT
          er.source,
          er.entity_evidence_id,
          er.status,
          ce.entity_id
        FROM entity_resolution er
        LEFT JOIN canonical_entity ce
          ON ce.entity_type = er.entity_type
         AND ce.taxonomy_id = er.taxonomy_id
         AND ce.canonical_identifier_type_id = er.canonical_identifier_type_id
         AND ce.canonical_identifier = er.canonical_identifier
        """
    )
    con.execute(
        """
        CREATE TABLE relation AS
        WITH projected AS (
          SELECT DISTINCT
            subject.entity_id AS subject_entity_id,
            ar.predicate,
            object.entity_id AS object_entity_id,
            ar.relation_category
          FROM annotation_relation_evidence_raw ar
          JOIN entity_evidence_resolution subject
            ON subject.source = ar.source
           AND subject.entity_evidence_id = ar.subject_entity_evidence_id
          JOIN canonical_entity object
            ON object.entity_type = ar.object_entity_type
           AND object.canonical_identifier_type = ar.object_id_type
           AND object.canonical_identifier = ar.object_id
          WHERE subject.entity_id IS NOT NULL
        )
        SELECT
          row_number() OVER (
            ORDER BY
              subject_entity_id,
              predicate,
              object_entity_id,
              relation_category
          )::BIGINT AS relation_id,
          *
        FROM projected
        """
    )
    con.execute(
        """
        CREATE TABLE relation_evidence_relation AS
        SELECT
          ar.source,
          ar.relation_evidence_id,
          r.relation_id
        FROM annotation_relation_evidence_raw ar
        JOIN entity_evidence_resolution subject
          ON subject.source = ar.source
         AND subject.entity_evidence_id = ar.subject_entity_evidence_id
        JOIN canonical_entity object
          ON object.entity_type = ar.object_entity_type
         AND object.canonical_identifier_type = ar.object_id_type
         AND object.canonical_identifier = ar.object_id
        JOIN relation r
          ON r.subject_entity_id = subject.entity_id
         AND r.predicate = ar.predicate
         AND r.object_entity_id = object.entity_id
         AND r.relation_category = ar.relation_category
        WHERE subject.entity_id IS NOT NULL
        """
    )
    _copy_to_parquet(con, 'canonical_entity', final_dir / 'entity.parquet')
    _copy_to_parquet(
        con,
        'entity_evidence_resolution',
        final_dir / 'entity_evidence_resolution.parquet',
    )
    _copy_to_parquet(con, 'relation', final_dir / 'relation.parquet')
    _copy_to_parquet(
        con,
        'relation_evidence_relation',
        final_dir / 'relation_evidence_relation.parquet',
    )
    entities = int(con.sql('SELECT COUNT(*) FROM canonical_entity').fetchone()[0])
    relations = int(con.sql('SELECT COUNT(*) FROM relation').fetchone()[0])
    links = int(con.sql('SELECT COUNT(*) FROM relation_evidence_relation').fetchone()[0])
    con.close()
    return entities, relations, links


def _copy_to_parquet(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY {table} TO {_sql_literal(path)} (FORMAT PARQUET)")


def _canonicalize_loaded_duckdb(
    con: duckdb.DuckDBPyConnection,
) -> tuple[int, int, int]:
    """Canonicalize already-loaded DuckDB evidence and resolver tables."""

    con.execute(
        """
        CREATE TABLE entity_resolution AS
        SELECT
          ee.source,
          ee.dataset,
          ee.row_id,
          ee.entity_evidence_id,
          ee.entity_type,
          ee.taxonomy_id,
          rl.canonical_identifier_type_id,
          rl.canonical_identifier,
          CASE
            WHEN rl.canonical_identifier IS NULL THEN 'unresolved'
            ELSE 'resolved'
          END AS status
        FROM entity_evidence_raw ee
        LEFT JOIN entity_identifier_raw ei
          ON ei.source = ee.source
         AND ei.entity_evidence_id = ee.entity_evidence_id
        LEFT JOIN identifier_type_all kit
          ON kit.name = ei.identifier_type
        LEFT JOIN resolver_lookup rl
          ON rl.key_identifier_type_id = kit.identifier_type_id
         AND rl.key_value = ei.identifier
         AND rl.taxonomy_id = ee.taxonomy_id
        QUALIFY row_number() OVER (
          PARTITION BY ee.source, ee.entity_evidence_id
          ORDER BY rl.canonical_identifier IS NULL, rl.canonical_identifier
        ) = 1
        """
    )
    con.execute(
        """
        CREATE TABLE canonical_entity AS
        WITH needed_protein_key AS (
          SELECT DISTINCT
            rce.entity_type,
            rce.taxonomy_id,
            rce.canonical_identifier_type_id,
            rce.canonical_identifier
          FROM entity_resolution er
          JOIN resolver_canonical_entity rce
            ON rce.entity_type = er.entity_type
           AND rce.taxonomy_id = er.taxonomy_id
           AND rce.canonical_identifier_type_id = er.canonical_identifier_type_id
           AND rce.canonical_identifier = er.canonical_identifier
          WHERE er.status = 'resolved'
        ),
        protein_identifier_rows AS (
          SELECT
            np.entity_type,
            np.taxonomy_id,
            np.canonical_identifier_type_id,
            np.canonical_identifier,
            np.canonical_identifier_type_id AS identifier_type_id,
            np.canonical_identifier AS identifier
          FROM needed_protein_key np
          UNION
          SELECT
            np.entity_type,
            np.taxonomy_id,
            np.canonical_identifier_type_id,
            np.canonical_identifier,
            rl.key_identifier_type_id AS identifier_type_id,
            rl.key_value AS identifier
          FROM needed_protein_key np
          JOIN resolver_lookup rl
            ON rl.canonical_identifier_type_id = np.canonical_identifier_type_id
           AND rl.canonical_identifier = np.canonical_identifier
           AND rl.taxonomy_id = np.taxonomy_id
          WHERE rl.key_value IS NOT NULL
            AND rl.key_value <> ''
        ),
        needed_protein_entity AS (
          SELECT
            pir.entity_type,
            pir.taxonomy_id,
            pir.canonical_identifier_type_id,
            pir.canonical_identifier,
            to_json(
              list(
                struct_pack(
                  identifier_type := it.name,
                  identifier_type_id := pir.identifier_type_id,
                  identifier := pir.identifier
                )
                ORDER BY it.name, pir.identifier
              )
            ) AS identifiers_json
          FROM (
            SELECT DISTINCT *
            FROM protein_identifier_rows
          ) pir
          JOIN identifier_type_all it
            ON it.identifier_type_id = pir.identifier_type_id
          GROUP BY
            pir.entity_type,
            pir.taxonomy_id,
            pir.canonical_identifier_type_id,
            pir.canonical_identifier
        ),
        cv_term_entity AS (
          SELECT DISTINCT
            ? AS entity_type,
            NULL::VARCHAR AS taxonomy_id,
            cv_type.identifier_type_id AS canonical_identifier_type_id,
            object_id AS canonical_identifier,
            to_json(
              list(
                struct_pack(
                  identifier_type := ?,
                  identifier_type_id := cv_type.identifier_type_id,
                  identifier := object_id
                )
              )
            ) AS identifiers_json
          FROM annotation_relation_evidence_raw
          CROSS JOIN (
            SELECT identifier_type_id
            FROM identifier_type_all
            WHERE name = ?
          ) cv_type
          WHERE object_id_type = ?
            AND object_id IS NOT NULL
          GROUP BY cv_type.identifier_type_id, object_id
        ),
        all_entity AS (
          SELECT * FROM needed_protein_entity
          UNION ALL
          SELECT * FROM cv_term_entity
        )
        SELECT
          row_number() OVER (
            ORDER BY
              entity_type,
              taxonomy_id NULLS FIRST,
              canonical_identifier_type_id,
              canonical_identifier
          )::BIGINT AS entity_id,
          entity_type,
          taxonomy_id,
          canonical_identifier_type_id,
          it.name AS canonical_identifier_type,
          canonical_identifier,
          identifiers_json,
          'resolved' AS resolution_status
        FROM all_entity
        JOIN identifier_type_all it
          ON it.identifier_type_id = all_entity.canonical_identifier_type_id
        """,
        [CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE, CV_TERM_ID_TYPE, CV_TERM_ID_TYPE],
    )
    con.execute(
        """
        CREATE TABLE entity_evidence_resolution AS
        SELECT
          er.source,
          er.entity_evidence_id,
          er.status,
          ce.entity_id
        FROM entity_resolution er
        LEFT JOIN canonical_entity ce
          ON ce.entity_type = er.entity_type
         AND ce.taxonomy_id = er.taxonomy_id
         AND ce.canonical_identifier_type_id = er.canonical_identifier_type_id
         AND ce.canonical_identifier = er.canonical_identifier
        """
    )
    con.execute(
        """
        CREATE TABLE relation AS
        WITH projected AS (
          SELECT DISTINCT
            subject.entity_id AS subject_entity_id,
            ar.predicate,
            object.entity_id AS object_entity_id,
            ar.relation_category
          FROM annotation_relation_evidence_raw ar
          JOIN entity_evidence_resolution subject
            ON subject.source = ar.source
           AND subject.entity_evidence_id = ar.subject_entity_evidence_id
          JOIN canonical_entity object
            ON object.entity_type = ar.object_entity_type
           AND object.canonical_identifier_type = ar.object_id_type
           AND object.canonical_identifier = ar.object_id
          WHERE subject.entity_id IS NOT NULL
        )
        SELECT
          row_number() OVER (
            ORDER BY
              subject_entity_id,
              predicate,
              object_entity_id,
              relation_category
          )::BIGINT AS relation_id,
          *
        FROM projected
        """
    )
    con.execute(
        """
        CREATE TABLE relation_evidence_relation AS
        SELECT
          ar.source,
          ar.relation_evidence_id,
          r.relation_id
        FROM annotation_relation_evidence_raw ar
        JOIN entity_evidence_resolution subject
          ON subject.source = ar.source
         AND subject.entity_evidence_id = ar.subject_entity_evidence_id
        JOIN canonical_entity object
          ON object.entity_type = ar.object_entity_type
         AND object.canonical_identifier_type = ar.object_id_type
         AND object.canonical_identifier = ar.object_id
        JOIN relation r
          ON r.subject_entity_id = subject.entity_id
         AND r.predicate = ar.predicate
         AND r.object_entity_id = object.entity_id
         AND r.relation_category = ar.relation_category
        WHERE subject.entity_id IS NOT NULL
        """
    )

    entities = int(con.sql('SELECT COUNT(*) FROM canonical_entity').fetchone()[0])
    relations = int(con.sql('SELECT COUNT(*) FROM relation').fetchone()[0])
    links = int(con.sql('SELECT COUNT(*) FROM relation_evidence_relation').fetchone()[0])
    return entities, relations, links


def _sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def copy_final_parquet_to_postgres(
    conn: psycopg2.extensions.connection,
    *,
    final_dir: str | Path,
    schema: str = 'public',
    batch_size: int = 100_000,
) -> tuple[int, int, int, int]:
    """Copy DuckDB final Parquet outputs into PostgreSQL canonical tables."""

    final_dir = Path(final_dir)
    with conn.cursor() as cur:
        _create_postgres_staging(cur)
        _copy_parquet_to_table(
            cur,
            table='_pq_entity',
            columns=(
                'entity_id',
                'entity_type',
                'taxonomy_id',
                'canonical_identifier_type',
                'canonical_identifier',
                'identifiers_json',
                'resolution_status',
            ),
            path=final_dir / 'entity.parquet',
            batch_size=batch_size,
        )
        _copy_parquet_to_table(
            cur,
            table='_pq_entity_evidence_resolution',
            columns=('source', 'entity_evidence_id', 'status', 'entity_id'),
            path=final_dir / 'entity_evidence_resolution.parquet',
            batch_size=batch_size,
        )
        _copy_parquet_to_table(
            cur,
            table='_pq_relation',
            columns=(
                'relation_id',
                'subject_entity_id',
                'predicate',
                'object_entity_id',
                'relation_category',
            ),
            path=final_dir / 'relation.parquet',
            batch_size=batch_size,
        )
        _copy_parquet_to_table(
            cur,
            table='_pq_relation_evidence_relation',
            columns=(
                'source',
                'relation_evidence_id',
                'relation_id',
            ),
            path=final_dir / 'relation_evidence_relation.parquet',
            batch_size=batch_size,
        )
        return _load_postgres_staging(cur, schema)


def copy_evidence_parquet_to_postgres(
    conn: psycopg2.extensions.connection,
    *,
    evidence_dir: str | Path,
    schema: str = 'public',
    batch_size: int = 100_000,
) -> tuple[int, int, int, int]:
    """Copy projected evidence Parquet through the existing ingest staging SQL."""

    evidence_dir = Path(evidence_dir)
    with conn.cursor() as cur:
        _create_staging_tables(cur)
        entity_rows = _copy_parquet_transform(
            cur,
            table='stg_entity',
            columns=(
                'entity_evidence_id',
                'source',
                'dataset',
                'row_id',
                'parent_entity_evidence_id',
                'vocab_entity_role',
                'vocab_entity_type',
                'taxonomy_id',
            ),
            path=evidence_dir / 'entity_evidence.parquet',
            parquet_columns=(
                'entity_evidence_id',
                'source',
                'dataset',
                'row_id',
                'parent_entity_evidence_id',
                'entity_role',
                'entity_type',
                'taxonomy_id',
            ),
            batch_size=batch_size,
        )
        identifier_rows = _copy_parquet_transform(
            cur,
            table='stg_identifier_ref',
            columns=(
                'source',
                'entity_evidence_id',
                'identifier_id',
                'type',
                'value',
            ),
            path=evidence_dir / 'entity_identifier.parquet',
            parquet_columns=(
                'source',
                'entity_evidence_id',
                'identifier_id',
                'identifier_type',
                'identifier',
            ),
            batch_size=batch_size,
        )
        relation_rows = _copy_parquet_transform(
            cur,
            table='stg_relation',
            columns=(
                'relation_evidence_id',
                'source',
                'dataset',
                'row_id',
                'subject_entity_evidence_id',
                'predicate',
                'object_entity_evidence_id',
                'vocab_relation_category',
            ),
            path=evidence_dir / 'relation_evidence.parquet',
            parquet_columns=(
                'relation_evidence_id',
                'source',
                'dataset',
                'row_id',
                'subject_entity_evidence_id',
                'predicate',
                'object_entity_evidence_id',
                'relation_category',
            ),
            batch_size=batch_size,
        )
        annotation_relation_rows = _copy_parquet_transform(
            cur,
            table='stg_annotation_relation',
            columns=(
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
            ),
            path=evidence_dir / 'annotation_relation_evidence.parquet',
            parquet_columns=(
                'relation_evidence_id',
                'source',
                'dataset',
                'row_id',
                'subject_entity_evidence_id',
                'predicate',
                'object_entity_type',
                'object_id_type',
                'object_id',
                'relation_category',
            ),
            batch_size=batch_size,
        )
        annotation_value_rows = _copy_parquet_transform(
            cur,
            table='stg_annotation_value',
            columns=('annotation_key', 'term', 'value', 'unit'),
            path=evidence_dir / 'annotation.parquet',
            parquet_columns=('annotation_key', 'term', 'value', 'unit'),
            batch_size=batch_size,
        )
        entity_annotation_rows = _copy_annotation_refs(
            cur,
            path=evidence_dir / 'entity_annotation.parquet',
            target_kind='entity',
            scope='entity',
            batch_size=batch_size,
        )
        relation_annotation_rows = _copy_annotation_refs(
            cur,
            path=evidence_dir / 'relation_annotation.parquet',
            target_kind='relation',
            scope='relation',
            batch_size=batch_size,
        )
        _index_staging_tables(cur)
        BulkIngestor(conn, schema=schema)._insert_from_staging(cur)
    return (
        entity_rows,
        identifier_rows,
        relation_rows + annotation_relation_rows,
        annotation_value_rows + entity_annotation_rows + relation_annotation_rows,
    )


def bulk_load_parquet_to_postgres(
    *,
    database_url: str,
    evidence_dir: str | Path,
    final_dir: str | Path,
    ontology_dir: str | Path | None = None,
    schema: str = 'public',
    state_path: str | Path = 'data/experiments/uniprot_duckdb/bulk_load.duckdb',
) -> None:
    """Directly load empty PostgreSQL content tables from Parquet via DuckDB."""

    evidence_dir = Path(evidence_dir)
    ontology_dir = Path(ontology_dir) if ontology_dir is not None else None
    final_dir = Path(final_dir)
    state_path = Path(state_path)
    if state_path.exists():
        state_path.unlink()
    con = duckdb.connect(str(state_path))
    con.execute('LOAD postgres')
    con.execute(
        f"ATTACH {_sql_literal(database_url)} AS pg (TYPE postgres)"
    )
    _bulk_load_create_views(
        con,
        evidence_dir=evidence_dir,
        ontology_dir=ontology_dir,
        final_dir=final_dir,
    )
    _bulk_load_assert_empty(con, schema)
    _bulk_load_small_dimensions(con, schema)
    _bulk_load_evidence(con, schema)
    _bulk_load_canonical(con, schema)
    con.close()
    _reset_postgres_sequences(database_url=database_url, schema=schema)


def _bulk_load_create_views(
    con: duckdb.DuckDBPyConnection,
    *,
    evidence_dir: Path,
    ontology_dir: Path | None,
    final_dir: Path,
) -> None:
    views = {
        'pq_entity_evidence': evidence_dir / 'entity_evidence.parquet',
        'pq_entity_identifier': evidence_dir / 'entity_identifier.parquet',
        'pq_entity_annotation': evidence_dir / 'entity_annotation.parquet',
        'pq_relation_evidence': evidence_dir / 'relation_evidence.parquet',
        'pq_annotation_relation_evidence': (
            evidence_dir / 'annotation_relation_evidence.parquet'
        ),
        'pq_relation_annotation': evidence_dir / 'relation_annotation.parquet',
        'pq_annotation': evidence_dir / 'annotation.parquet',
        'pq_entity': final_dir / 'entity.parquet',
        'pq_entity_evidence_resolution': (
            final_dir / 'entity_evidence_resolution.parquet'
        ),
        'pq_relation': final_dir / 'relation.parquet',
        'pq_relation_evidence_relation': (
            final_dir / 'relation_evidence_relation.parquet'
        ),
    }
    for view, path in views.items():
        con.execute(
            f"""
            CREATE VIEW {view} AS
            SELECT *
            FROM read_parquet({_sql_literal(path)})
            """
        )
    ontology_path = (
        ontology_dir / 'ontology_terms.parquet'
        if ontology_dir is not None
        else None
    )
    if ontology_path is not None and ontology_path.exists():
        con.execute(
            f"""
            CREATE VIEW pq_ontology_terms AS
            SELECT *
            FROM read_parquet({_sql_literal(ontology_path)})
            """
        )
    else:
        con.execute(
            """
            CREATE VIEW pq_ontology_terms AS
            SELECT
              NULL::VARCHAR AS source,
              NULL::VARCHAR AS dataset,
              NULL::VARCHAR AS term_id,
              NULL::VARCHAR AS ontology_prefix,
              NULL::VARCHAR AS label,
              NULL::VARCHAR AS definition,
              NULL::VARCHAR AS ontology_id,
              []::VARCHAR[] AS synonyms,
              NULL::VARCHAR AS synonyms_text,
              []::VARCHAR[] AS sources
            WHERE false
            """
        )


def _bulk_load_create_views_from_loaded_tables(
    con: duckdb.DuckDBPyConnection,
) -> None:
    views = {
        'pq_entity_evidence': 'entity_evidence_raw',
        'pq_entity_identifier': 'entity_identifier_raw',
        'pq_entity_annotation': 'entity_annotation_raw',
        'pq_relation_evidence': 'relation_evidence_raw',
        'pq_annotation_relation_evidence': 'annotation_relation_evidence_raw',
        'pq_relation_annotation': 'relation_annotation_raw',
        'pq_annotation': 'annotation_value',
        'pq_entity': 'canonical_entity',
        'pq_entity_evidence_resolution': 'entity_evidence_resolution',
        'pq_relation': 'relation',
        'pq_relation_evidence_relation': 'relation_evidence_relation',
    }
    for view, table in views.items():
        con.execute(f'CREATE VIEW {view} AS SELECT * FROM {table}')
    con.execute(
        """
        CREATE VIEW pq_ontology_terms AS
        SELECT
          NULL::VARCHAR AS source,
          NULL::VARCHAR AS dataset,
          NULL::VARCHAR AS term_id,
          NULL::VARCHAR AS ontology_prefix,
          NULL::VARCHAR AS label,
          NULL::VARCHAR AS definition,
          NULL::VARCHAR AS ontology_id,
          []::VARCHAR[] AS synonyms,
          NULL::VARCHAR AS synonyms_text,
          []::VARCHAR[] AS sources
        WHERE false
        """
    )


def _bulk_load_assert_empty(
    con: duckdb.DuckDBPyConnection,
    schema: str,
) -> None:
    content_tables = (
        'data_source',
        'dataset',
        'identifier_evidence',
        'annotation',
        'entity',
        'entity_evidence',
        'entity_evidence_identifier',
        'entity_evidence_annotation',
        'relation',
        'relation_evidence',
        'relation_evidence_annotation',
        'entity_evidence_resolution',
        'relation_evidence_relation',
        'entity_annotation_relation',
        'ontology_terms',
    )
    non_empty = []
    for table in content_tables:
        count = int(
            con.sql(f'SELECT COUNT(*) FROM pg.{schema}.{table}').fetchone()[0]
        )
        if count:
            non_empty.append(f'{table}={count}')
    if non_empty:
        raise RuntimeError(
            'bulk_load_parquet_to_postgres requires empty content tables: '
            + ', '.join(non_empty)
        )


def _bulk_load_small_dimensions(
    con: duckdb.DuckDBPyConnection,
    schema: str,
) -> None:
    con.execute(
        f"""
        INSERT INTO pg.{schema}.vocab_entity_type (name)
        SELECT candidate.name
        FROM (
          SELECT DISTINCT entity_type AS name FROM pq_entity_evidence WHERE entity_type IS NOT NULL
          UNION
          SELECT DISTINCT entity_type AS name FROM pq_entity WHERE entity_type IS NOT NULL
          UNION
          SELECT DISTINCT object_entity_type AS name
          FROM pq_annotation_relation_evidence
          WHERE object_entity_type IS NOT NULL
          UNION
          SELECT {_sql_literal(CV_TERM_ENTITY_TYPE)} AS name
          FROM pq_ontology_terms
          WHERE term_id IS NOT NULL
        ) candidate
        LEFT JOIN pg.{schema}.vocab_entity_type existing
          ON existing.name = candidate.name
        WHERE existing.entity_type_id IS NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.vocab_entity_role (name)
        SELECT candidate.name
        FROM (
          SELECT DISTINCT entity_role AS name
          FROM pq_entity_evidence
          WHERE entity_role IS NOT NULL
        ) candidate
        LEFT JOIN pg.{schema}.vocab_entity_role existing
          ON existing.name = candidate.name
        WHERE existing.entity_role_id IS NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.vocab_relation_predicate (name)
        SELECT candidate.name
        FROM (
          SELECT DISTINCT predicate AS name FROM pq_relation WHERE predicate IS NOT NULL
          UNION
          SELECT DISTINCT predicate AS name FROM pq_relation_evidence WHERE predicate IS NOT NULL
          UNION
          SELECT DISTINCT predicate AS name
          FROM pq_annotation_relation_evidence
          WHERE predicate IS NOT NULL
        ) candidate
        LEFT JOIN pg.{schema}.vocab_relation_predicate existing
          ON existing.name = candidate.name
        WHERE existing.relation_predicate_id IS NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.vocab_relation_category (name)
        SELECT candidate.name
        FROM (
          SELECT DISTINCT relation_category AS name FROM pq_relation
          UNION
          SELECT DISTINCT relation_category AS name FROM pq_relation_evidence
          UNION
          SELECT DISTINCT relation_category AS name
          FROM pq_annotation_relation_evidence
        ) candidate
        LEFT JOIN pg.{schema}.vocab_relation_category existing
          ON existing.name = candidate.name
        WHERE candidate.name IS NOT NULL
          AND existing.relation_category_id IS NULL
        """
    )
    con.execute(
        f"""
        WITH missing AS (
          SELECT DISTINCT identifier_type AS name
          FROM pq_entity_identifier
          WHERE identifier_type IS NOT NULL
          UNION
          SELECT DISTINCT canonical_identifier_type AS name
          FROM pq_entity
          WHERE canonical_identifier_type IS NOT NULL
          UNION
          SELECT DISTINCT object_id_type AS name
          FROM pq_annotation_relation_evidence
          WHERE object_id_type IS NOT NULL
          UNION
          SELECT {_sql_literal(CV_TERM_ID_TYPE)} AS name
          FROM pq_ontology_terms
          WHERE term_id IS NOT NULL
        ),
        missing_new AS (
          SELECT missing.name
          FROM missing
          LEFT JOIN pg.{schema}.vocab_identifier_type it
            ON it.name = missing.name
          WHERE it.identifier_type_id IS NULL
        ),
        base AS (
          SELECT coalesce(max(identifier_type_id), 0) AS max_id
          FROM pg.{schema}.vocab_identifier_type
        )
        INSERT INTO pg.{schema}.vocab_identifier_type (identifier_type_id, name)
        SELECT base.max_id + row_number() OVER (ORDER BY missing_new.name),
               missing_new.name
        FROM missing_new
        CROSS JOIN base
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.data_source (name)
        SELECT DISTINCT source
        FROM (
          SELECT source FROM pq_entity_evidence
          UNION
          SELECT source FROM pq_entity_evidence_resolution
          UNION
          SELECT source FROM pq_ontology_terms
        ) s
        WHERE source IS NOT NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.dataset (source_id, name)
        SELECT DISTINCT ds.source_id, candidate.dataset
        FROM (
          SELECT source, dataset FROM pq_entity_evidence
          UNION
          SELECT source, dataset FROM pq_ontology_terms
        ) candidate
        JOIN pg.{schema}.data_source ds
          ON ds.name = candidate.source
        WHERE candidate.dataset IS NOT NULL
        """
    )


def _bulk_load_evidence(
    con: duckdb.DuckDBPyConnection,
    schema: str,
) -> None:
    con.execute(
        f"""
        INSERT INTO pg.{schema}.identifier_evidence (
          identifier_id,
          identifier_type_id,
          value
        )
        SELECT DISTINCT
          identifier_id::UUID,
          it.identifier_type_id,
          identifier
        FROM pq_entity_identifier i
        JOIN pg.{schema}.vocab_identifier_type it
          ON it.name = i.identifier_type
        WHERE i.identifier_id IS NOT NULL
          AND i.identifier IS NOT NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.annotation (
          annotation_key,
          term,
          value,
          unit
        )
        SELECT DISTINCT
          annotation_key::UUID,
          term,
          value,
          unit
        FROM pq_annotation
        WHERE term IS NOT NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.entity_evidence (
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
          e.entity_evidence_id::UUID,
          d.dataset_id,
          e.row_id,
          e.parent_entity_evidence_id::UUID,
          er.entity_role_id,
          et.entity_type_id,
          NULLIF(e.taxonomy_id, '')::BIGINT
        FROM pq_entity_evidence e
        JOIN pg.{schema}.data_source ds
          ON ds.name = e.source
        JOIN pg.{schema}.dataset d
          ON d.source_id = ds.source_id
         AND d.name = e.dataset
        JOIN pg.{schema}.vocab_entity_role er
          ON er.name = e.entity_role
        LEFT JOIN pg.{schema}.vocab_entity_type et
          ON et.name = e.entity_type
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.entity_evidence_identifier (
          source_id,
          entity_evidence_id,
          identifier_id
        )
        SELECT DISTINCT
          ds.source_id,
          i.entity_evidence_id::UUID,
          i.identifier_id::UUID
        FROM pq_entity_identifier i
        JOIN pg.{schema}.data_source ds
          ON ds.name = i.source
        WHERE i.identifier_id IS NOT NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.entity_evidence_annotation (
          source_id,
          entity_evidence_id,
          annotation_key
        )
        SELECT DISTINCT
          ds.source_id,
          a.evidence_id::UUID,
          a.annotation_key::UUID
        FROM pq_entity_annotation a
        JOIN pg.{schema}.data_source ds
          ON ds.name = a.source
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.relation_evidence (
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
          r.relation_evidence_id::UUID,
          d.dataset_id,
          r.row_id,
          r.subject_entity_evidence_id::UUID,
          NULL::BIGINT,
          rp.relation_predicate_id,
          r.object_entity_evidence_id::UUID,
          NULL::BIGINT,
          rc.relation_category_id
        FROM pq_relation_evidence r
        JOIN pg.{schema}.data_source ds
          ON ds.name = r.source
        JOIN pg.{schema}.dataset d
          ON d.source_id = ds.source_id
         AND d.name = r.dataset
        JOIN pg.{schema}.vocab_relation_predicate rp
          ON rp.name = r.predicate
        JOIN pg.{schema}.vocab_relation_category rc
          ON rc.name = r.relation_category
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.relation_evidence_annotation (
          source_id,
          relation_evidence_id,
          annotation_key,
          annotation_scope_id
        )
        SELECT DISTINCT
          ds.source_id,
          a.evidence_id::UUID,
          a.annotation_key::UUID,
          sc.annotation_scope_id
        FROM pq_relation_annotation a
        JOIN pg.{schema}.data_source ds
          ON ds.name = a.source
        JOIN pg.{schema}.vocab_annotation_scope sc
          ON sc.name = 'relation'
        """
    )


def _bulk_load_canonical(
    con: duckdb.DuckDBPyConnection,
    schema: str,
) -> None:
    con.execute(
        f"""
        INSERT INTO pg.{schema}.entity (
          entity_id,
          entity_type_id,
          taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier,
          identifiers,
          resolution_status_id
        )
        SELECT
          e.entity_id,
          et.entity_type_id,
          NULLIF(e.taxonomy_id, '')::BIGINT,
          it.identifier_type_id,
          e.canonical_identifier,
          e.identifiers_json::JSON,
          rs.resolution_status_id
        FROM pq_entity e
        JOIN pg.{schema}.vocab_entity_type et
          ON et.name = e.entity_type
        JOIN pg.{schema}.vocab_identifier_type it
          ON it.name = e.canonical_identifier_type
        JOIN pg.{schema}.vocab_resolution_status rs
          ON rs.name = e.resolution_status
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.entity (
          entity_id,
          entity_type_id,
          taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier,
          identifiers,
          resolution_status_id
        )
        WITH missing AS (
          SELECT
            ot.term_id,
            et.entity_type_id,
            it.identifier_type_id,
            it.name AS identifier_type,
            rs.resolution_status_id
          FROM pq_ontology_terms ot
          JOIN pg.{schema}.vocab_entity_type et
            ON et.name = {_sql_literal(CV_TERM_ENTITY_TYPE)}
          JOIN pg.{schema}.vocab_identifier_type it
            ON it.name = {_sql_literal(CV_TERM_ID_TYPE)}
          JOIN pg.{schema}.vocab_resolution_status rs
            ON rs.name = 'resolved'
          LEFT JOIN pg.{schema}.entity existing
            ON existing.entity_type_id = et.entity_type_id
           AND existing.taxonomy_id IS NULL
           AND existing.canonical_identifier_type_id = it.identifier_type_id
           AND existing.canonical_identifier = ot.term_id
          WHERE ot.term_id IS NOT NULL
            AND existing.entity_id IS NULL
        ),
        base AS (
          SELECT coalesce(max(entity_id), 0) AS max_entity_id
          FROM pg.{schema}.entity
        )
        SELECT
          base.max_entity_id + row_number() OVER (ORDER BY missing.term_id),
          missing.entity_type_id,
          NULL::BIGINT,
          missing.identifier_type_id,
          missing.term_id,
          to_json(
            list_value(
              struct_pack(
                identifier_type := missing.identifier_type,
                identifier_type_id := missing.identifier_type_id,
                identifier := missing.term_id
              )
            )
          )::JSON,
          missing.resolution_status_id
        FROM missing
        CROSS JOIN base
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.ontology_terms (
          source_id,
          term_entity_id,
          term_id,
          ontology_prefix,
          label,
          definition,
          ontology_id,
          synonyms,
          synonyms_text,
          sources
        )
        SELECT
          ds.source_id,
          e.entity_id,
          ot.term_id,
          ot.ontology_prefix,
          ot.label,
          ot.definition,
          ot.ontology_id,
          ot.synonyms,
          COALESCE(ot.synonyms_text, ''),
          ot.sources
        FROM pq_ontology_terms ot
        JOIN pg.{schema}.data_source ds
          ON ds.name = ot.source
        JOIN pg.{schema}.vocab_entity_type et
          ON et.name = {_sql_literal(CV_TERM_ENTITY_TYPE)}
        JOIN pg.{schema}.vocab_identifier_type it
          ON it.name = {_sql_literal(CV_TERM_ID_TYPE)}
        JOIN pg.{schema}.entity e
          ON e.entity_type_id = et.entity_type_id
         AND e.taxonomy_id IS NULL
         AND e.canonical_identifier_type_id = it.identifier_type_id
         AND e.canonical_identifier = ot.term_id
        WHERE ot.term_id IS NOT NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.relation_evidence (
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
          ar.relation_evidence_id::UUID,
          d.dataset_id,
          ar.row_id,
          ar.subject_entity_evidence_id::UUID,
          NULL::BIGINT,
          rp.relation_predicate_id,
          NULL::UUID,
          object.entity_id,
          rc.relation_category_id
        FROM pq_annotation_relation_evidence ar
        JOIN pg.{schema}.data_source ds
          ON ds.name = ar.source
        JOIN pg.{schema}.dataset d
          ON d.source_id = ds.source_id
         AND d.name = ar.dataset
        JOIN pg.{schema}.vocab_entity_type et
          ON et.name = ar.object_entity_type
        JOIN pg.{schema}.vocab_identifier_type it
          ON it.name = ar.object_id_type
        JOIN pg.{schema}.entity object
          ON object.entity_type_id = et.entity_type_id
         AND object.taxonomy_id IS NULL
         AND object.canonical_identifier_type_id = it.identifier_type_id
         AND object.canonical_identifier = ar.object_id
        JOIN pg.{schema}.vocab_relation_predicate rp
          ON rp.name = ar.predicate
        JOIN pg.{schema}.vocab_relation_category rc
          ON rc.name = ar.relation_category
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.entity_evidence_resolution (
          source_id,
          entity_evidence_id,
          status_id,
          entity_id,
          reason_id,
          resolved_at
        )
        SELECT
          ds.source_id,
          er.entity_evidence_id::UUID,
          rs.resolution_status_id,
          er.entity_id,
          NULL::SMALLINT,
          now()
        FROM pq_entity_evidence_resolution er
        JOIN pg.{schema}.data_source ds
          ON ds.name = er.source
        JOIN pg.{schema}.vocab_resolution_status rs
          ON rs.name = er.status
        WHERE er.entity_id IS NOT NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.relation (
          relation_id,
          subject_entity_id,
          predicate_id,
          object_entity_id,
          relation_category_id
        )
        SELECT
          r.relation_id,
          r.subject_entity_id,
          rp.relation_predicate_id,
          r.object_entity_id,
          rc.relation_category_id
        FROM pq_relation r
        JOIN pg.{schema}.vocab_relation_predicate rp
          ON rp.name = r.predicate
        LEFT JOIN pg.{schema}.vocab_relation_category rc
          ON rc.name = r.relation_category
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.relation_evidence_relation (
          source_id,
          relation_id,
          relation_evidence_id
        )
        SELECT
          ds.source_id,
          rer.relation_id,
          rer.relation_evidence_id::UUID
        FROM pq_relation_evidence_relation rer
        JOIN pg.{schema}.data_source ds
          ON ds.name = rer.source
        """
    )

def _reset_postgres_sequences(
    *,
    database_url: str,
    schema: str,
) -> None:
    sequence_tables = (
        ('data_source', 'source_id'),
        ('dataset', 'dataset_id'),
        ('entity', 'entity_id'),
        ('relation', 'relation_id'),
    )
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            for table, column in sequence_tables:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT setval(
                          pg_get_serial_sequence(%s, %s),
                          COALESCE((SELECT MAX({column}) FROM {table}), 1),
                          COALESCE((SELECT MAX({column}) FROM {table}), 0) > 0
                        )
                        """
                    ).format(
                        column=sql.Identifier(column),
                        table=sql.SQL('{}.{}').format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        ),
                    ),
                    [f'{schema}.{table}', column],
                )
        conn.commit()


def _create_postgres_staging(cur: psycopg2.extensions.cursor) -> None:
    cur.execute('DROP TABLE IF EXISTS _pq_entity')
    cur.execute('DROP TABLE IF EXISTS _pq_entity_evidence_resolution')
    cur.execute('DROP TABLE IF EXISTS _pq_relation')
    cur.execute('DROP TABLE IF EXISTS _pq_relation_evidence_relation')
    cur.execute(
        """
        CREATE TEMP TABLE _pq_entity (
          entity_id bigint PRIMARY KEY,
          entity_type text NOT NULL,
          taxonomy_id text,
          canonical_identifier_type text NOT NULL,
          canonical_identifier text NOT NULL,
          identifiers_json text NOT NULL,
          resolution_status text NOT NULL
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE _pq_entity_evidence_resolution (
          source text NOT NULL,
          entity_evidence_id uuid NOT NULL,
          status text NOT NULL,
          entity_id bigint
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE _pq_relation (
          relation_id bigint PRIMARY KEY,
          subject_entity_id bigint NOT NULL,
          predicate text NOT NULL,
          object_entity_id bigint NOT NULL,
          relation_category text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE _pq_relation_evidence_relation (
          source text NOT NULL,
          relation_evidence_id uuid NOT NULL,
          relation_id bigint NOT NULL
        ) ON COMMIT DROP
        """
    )


def _load_postgres_staging(
    cur: psycopg2.extensions.cursor,
    schema: str,
) -> tuple[int, int, int, int]:
    schema_id = sql.Identifier(schema)
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.data_source (name)
            SELECT DISTINCT source
            FROM _pq_entity_evidence_resolution
            ON CONFLICT (name) DO NOTHING
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.vocab_entity_type (name)
            SELECT DISTINCT entity_type
            FROM _pq_entity
            ON CONFLICT (name) DO NOTHING
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            WITH missing AS (
              SELECT DISTINCT canonical_identifier_type AS name
              FROM _pq_entity pe
              LEFT JOIN {}.vocab_identifier_type it
                ON it.name = pe.canonical_identifier_type
              WHERE it.identifier_type_id IS NULL
            ),
            base AS (
              SELECT coalesce(max(identifier_type_id), 0) AS max_id
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
        ).format(schema_id, schema_id, schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.vocab_relation_predicate (name)
            SELECT DISTINCT predicate
            FROM _pq_relation
            ON CONFLICT (name) DO NOTHING
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.vocab_relation_category (name)
            SELECT DISTINCT relation_category
            FROM _pq_relation
            WHERE relation_category IS NOT NULL
            ON CONFLICT (name) DO NOTHING
            """
        ).format(schema_id)
    )
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity (
              entity_type_id,
              taxonomy_id,
              canonical_identifier_type_id,
              canonical_identifier,
              identifiers,
              resolution_status_id
            )
            SELECT
              et.entity_type_id,
              NULLIF(pe.taxonomy_id, '')::bigint,
              it.identifier_type_id,
              pe.canonical_identifier,
              pe.identifiers_json::jsonb,
              rs.resolution_status_id
            FROM _pq_entity pe
            JOIN {}.vocab_entity_type et
              ON et.name = pe.entity_type
            JOIN {}.vocab_identifier_type it
              ON it.name = pe.canonical_identifier_type
            JOIN {}.vocab_resolution_status rs
              ON rs.name = pe.resolution_status
            ON CONFLICT (
              entity_type_id,
              taxonomy_id,
              canonical_identifier_type_id,
              canonical_identifier
            )
            DO UPDATE SET
              identifiers = EXCLUDED.identifiers,
              resolution_status_id = LEAST(
                {}.entity.resolution_status_id,
                EXCLUDED.resolution_status_id
              )
            """
        ).format(schema_id, schema_id, schema_id, schema_id, schema_id)
    )
    copied_entities = cur.rowcount
    cur.execute('DROP TABLE IF EXISTS _pq_entity_map')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _pq_entity_map ON COMMIT DROP AS
            SELECT
              pe.entity_id AS local_entity_id,
              e.entity_id AS db_entity_id
            FROM _pq_entity pe
            JOIN {}.vocab_entity_type et
              ON et.name = pe.entity_type
            JOIN {}.vocab_identifier_type it
              ON it.name = pe.canonical_identifier_type
            JOIN {}.entity e
              ON e.entity_type_id = et.entity_type_id
             AND e.taxonomy_id IS NOT DISTINCT FROM NULLIF(pe.taxonomy_id, '')::bigint
             AND e.canonical_identifier_type_id = it.identifier_type_id
             AND e.canonical_identifier = pe.canonical_identifier
            """
        ).format(schema_id, schema_id, schema_id)
    )
    cur.execute('CREATE UNIQUE INDEX ON _pq_entity_map (local_entity_id)')
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.entity_evidence_resolution (
              source_id,
              entity_evidence_id,
              status_id,
              entity_id,
              reason_id,
              resolved_at
            )
            SELECT
              ds.source_id,
              er.entity_evidence_id,
              rs.resolution_status_id,
              em.db_entity_id,
              NULL::smallint,
              now()
            FROM _pq_entity_evidence_resolution er
            JOIN {}.data_source ds
              ON ds.name = er.source
            JOIN {}.vocab_resolution_status rs
              ON rs.name = er.status
            JOIN _pq_entity_map em
              ON em.local_entity_id = er.entity_id
            WHERE er.entity_id IS NOT NULL
            ON CONFLICT (source_id, entity_evidence_id)
            DO UPDATE SET
              status_id = EXCLUDED.status_id,
              entity_id = EXCLUDED.entity_id,
              reason_id = EXCLUDED.reason_id,
              resolved_at = now()
            """
        ).format(schema_id, schema_id, schema_id)
    )
    copied_resolutions = cur.rowcount
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.relation (
              subject_entity_id,
              predicate_id,
              object_entity_id,
              relation_category_id
            )
            SELECT
              subject.db_entity_id,
              rp.relation_predicate_id,
              object.db_entity_id,
              rc.relation_category_id
            FROM _pq_relation r
            JOIN _pq_entity_map subject
              ON subject.local_entity_id = r.subject_entity_id
            JOIN _pq_entity_map object
              ON object.local_entity_id = r.object_entity_id
            JOIN {}.vocab_relation_predicate rp
              ON rp.name = r.predicate
            LEFT JOIN {}.vocab_relation_category rc
              ON rc.name = r.relation_category
            ON CONFLICT (
              subject_entity_id,
              predicate_id,
              object_entity_id,
              relation_category_id
            )
            DO NOTHING
            """
        ).format(schema_id, schema_id, schema_id)
    )
    copied_relations = cur.rowcount
    cur.execute('DROP TABLE IF EXISTS _pq_relation_map')
    cur.execute(
        sql.SQL(
            """
            CREATE TEMP TABLE _pq_relation_map ON COMMIT DROP AS
            SELECT
              r.relation_id AS local_relation_id,
              rel.relation_id AS db_relation_id
            FROM _pq_relation r
            JOIN _pq_entity_map subject
              ON subject.local_entity_id = r.subject_entity_id
            JOIN _pq_entity_map object
              ON object.local_entity_id = r.object_entity_id
            JOIN {}.vocab_relation_predicate rp
              ON rp.name = r.predicate
            LEFT JOIN {}.vocab_relation_category rc
              ON rc.name = r.relation_category
            JOIN {}.relation rel
              ON rel.subject_entity_id = subject.db_entity_id
             AND rel.predicate_id = rp.relation_predicate_id
             AND rel.object_entity_id = object.db_entity_id
             AND rel.relation_category_id IS NOT DISTINCT FROM rc.relation_category_id
            """
        ).format(schema_id, schema_id, schema_id)
    )
    cur.execute('CREATE UNIQUE INDEX ON _pq_relation_map (local_relation_id)')
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {}.relation_evidence_relation (
              source_id,
              relation_id,
              relation_evidence_id
            )
            SELECT
              ds.source_id,
              rm.db_relation_id,
              rer.relation_evidence_id
            FROM _pq_relation_evidence_relation rer
            JOIN {}.data_source ds
              ON ds.name = rer.source
            JOIN _pq_relation_map rm
              ON rm.local_relation_id = rer.relation_id
            ON CONFLICT (
              source_id,
              relation_evidence_id
            )
            DO NOTHING
            """
        ).format(schema_id, schema_id)
    )
    copied_links = cur.rowcount
    return copied_entities, copied_resolutions, copied_relations, copied_links


def _copy_parquet_to_table(
    cur: psycopg2.extensions.cursor,
    *,
    table: str,
    columns: tuple[str, ...],
    path: Path,
    batch_size: int,
) -> int:
    parquet = pq.ParquetFile(path)
    missing_columns = [column for column in columns if column not in parquet.schema.names]
    if missing_columns:
        raise ValueError(
            f'{path} is missing required column(s): {", ".join(missing_columns)}'
        )
    total = 0
    for batch in parquet.iter_batches(batch_size=batch_size, columns=list(columns)):
        buffer = StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        table_batch = pa.Table.from_batches([batch])
        arrays = [table_batch.column(column).to_pylist() for column in columns]
        for row in zip(*arrays, strict=True):
            writer.writerow([_copy_value(value) for value in row])
        buffer.seek(0)
        cur.copy_expert(
            _copy_sql(None, table, columns).as_string(cur.connection),
            buffer,
        )
        total += batch.num_rows
    return total


def _copy_parquet_transform(
    cur: psycopg2.extensions.cursor,
    *,
    table: str,
    columns: tuple[str, ...],
    path: Path,
    parquet_columns: tuple[str, ...],
    batch_size: int,
) -> int:
    return _copy_parquet_rows(
        cur,
        table=table,
        columns=columns,
        path=path,
        parquet_columns=parquet_columns,
        batch_size=batch_size,
        row_factory=lambda row: row,
    )


def _copy_annotation_refs(
    cur: psycopg2.extensions.cursor,
    *,
    path: Path,
    target_kind: str,
    scope: str,
    batch_size: int,
) -> int:
    return _copy_parquet_rows(
        cur,
        table='stg_annotation',
        columns=(
            'target_kind',
            'source',
            'target_evidence_id',
            'scope',
            'annotation_key',
        ),
        path=path,
        parquet_columns=('source', 'evidence_id', 'annotation_key'),
        batch_size=batch_size,
        row_factory=lambda row: (
            target_kind,
            row[0],
            row[1],
            scope,
            row[2],
        ),
    )


def _copy_parquet_rows(
    cur: psycopg2.extensions.cursor,
    *,
    table: str,
    columns: tuple[str, ...],
    path: Path,
    parquet_columns: tuple[str, ...],
    batch_size: int,
    row_factory,
) -> int:
    parquet = pq.ParquetFile(path)
    missing_columns = [
        column for column in parquet_columns if column not in parquet.schema.names
    ]
    if missing_columns:
        raise ValueError(
            f'{path} is missing required column(s): {", ".join(missing_columns)}'
        )
    total = 0
    for batch in parquet.iter_batches(
        batch_size=batch_size,
        columns=list(parquet_columns),
    ):
        table_batch = pa.Table.from_batches([batch])
        arrays = [
            table_batch.column(column).to_pylist() for column in parquet_columns
        ]
        buffer = StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        for row in zip(*arrays, strict=True):
            writer.writerow([_copy_value(value) for value in row_factory(row)])
        buffer.seek(0)
        cur.copy_expert(
            _copy_sql(None, table, columns).as_string(cur.connection),
            buffer,
        )
        total += batch.num_rows
    return total


def _copy_sql(
    schema: str | None,
    table: str,
    columns: tuple[str, ...],
) -> sql.Composed:
    table_sql = (
        sql.Identifier(table)
        if schema is None
        else sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(table))
    )
    return sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')").format(
        table_sql,
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )


def _copy_value(value: object) -> object:
    if value is None:
        return r'\N'
    if isinstance(value, float) and value != value:
        return r'\N'
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Experimental UniProt Parquet/DuckDB canonicalization.'
    )
    parser.add_argument('--output-dir', default='data/experiments/uniprot_duckdb')
    parser.add_argument('--resolver-dir', default='data/proteins')
    parser.add_argument('--max-records', type=int, default=None)
    parser.add_argument('--force-refresh', action='store_true')
    parser.add_argument(
        '--skip-ontology',
        action='store_true',
        help='Do not project and load UniProt ontology terms.',
    )
    parser.add_argument(
        '--database-url',
        default=os.environ.get('DATABASE_URL'),
        help='PostgreSQL URL for copying final Parquet outputs. Defaults to DATABASE_URL.',
    )
    parser.add_argument('--schema', default='public')
    parser.add_argument(
        '--copy-to-db',
        action='store_true',
        help='Copy final canonical Parquet outputs into PostgreSQL.',
    )
    parser.add_argument(
        '--copy-evidence-to-db',
        action='store_true',
        help='Copy projected evidence Parquet into PostgreSQL before final outputs.',
    )
    parser.add_argument(
        '--bulk-load-to-db',
        action='store_true',
        help='Directly load empty PostgreSQL content tables from Parquet via DuckDB.',
    )
    parser.add_argument(
        '--direct-to-db',
        action='store_true',
        help=(
            'Project and canonicalize in DuckDB memory, then load empty '
            'PostgreSQL content tables without writing intermediate Parquet.'
        ),
    )
    parser.add_argument(
        '--state-path',
        default=None,
        help='Optional DuckDB state path for --direct-to-db. Defaults to memory.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.direct_to_db:
        if not args.database_url:
            raise SystemExit('--direct-to-db requires --database-url or DATABASE_URL')
        stats = run_uniprot_duckdb_direct_build(
            database_url=args.database_url,
            resolver_dir=args.resolver_dir,
            max_records=args.max_records or 50_000,
            force_refresh=args.force_refresh,
            schema=args.schema,
            state_path=args.state_path,
        )
        print(
            '[duckdb-direct] '
            f'source_rows={stats.source_rows} '
            f'identifiers={stats.identifiers} '
            f'annotations={stats.annotations} '
            f'resolver={stats.resolver_seconds:.3f}s '
            f'projection={stats.projection_seconds:.3f}s '
            f'canonicalize={stats.canonicalize_seconds:.3f}s '
            f'entities={stats.entities} '
            f'relations={stats.relations} '
            f'annotation_relation_links={stats.annotation_relation_links} '
            f'bulk_loaded={stats.bulk_loaded}',
            flush=True,
        )
        return 0
    stats = run_uniprot_parquet_build(
        output_dir=args.output_dir,
        resolver_dir=args.resolver_dir,
        max_records=args.max_records,
        force_refresh=args.force_refresh,
        database_url=(
            args.database_url
            if args.copy_to_db or args.copy_evidence_to_db or args.bulk_load_to_db
            else None
        ),
        schema=args.schema,
        copy_evidence=args.copy_evidence_to_db,
        copy_final=args.copy_to_db,
        bulk_load=args.bulk_load_to_db,
        include_ontology=not args.skip_ontology,
    )
    print(
        '[parquet-duckdb] '
        f'source_rows={stats.source_rows} '
        f'identifiers={stats.identifiers} '
        f'annotations={stats.annotations} '
        f'resolver={stats.resolver_seconds:.3f}s '
        f'projection={stats.projection_seconds:.3f}s '
        f'canonicalize={stats.canonicalize_seconds:.3f}s '
        f'entities={stats.entities} '
        f'relations={stats.relations} '
        f'annotation_relation_links={stats.annotation_relation_links} '
        f'ontology_terms={stats.ontology_terms} '
        f'copied_entities={stats.copied_entities} '
        f'copied_entity_resolutions={stats.copied_entity_resolutions} '
        f'copied_relations={stats.copied_relations} '
        f'copied_annotation_relation_links={stats.copied_annotation_relation_links} '
        f'copied_evidence_entities={stats.copied_evidence_entities} '
        f'copied_evidence_identifiers={stats.copied_evidence_identifiers} '
        f'copied_evidence_relations={stats.copied_evidence_relations} '
        f'copied_evidence_annotations={stats.copied_evidence_annotations} '
        f'bulk_loaded={stats.bulk_loaded}',
        flush=True,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
