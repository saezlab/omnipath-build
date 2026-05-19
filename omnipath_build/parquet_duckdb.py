"""Experimental Parquet + DuckDB build path.
"""

from __future__ import annotations

from io import StringIO
import os
import csv
import time
from pathlib import Path
import argparse
import tempfile
from itertools import islice
from dataclasses import dataclass
from collections.abc import Iterable

import duckdb
import pyarrow as pa
import psycopg2
from psycopg2 import sql
import pyarrow.parquet as pq
import psycopg2.extensions

from omnipath_build.cv_terms import (
    CV_TERM_ID_TYPE,
    CV_TERM_ENTITY_TYPE,
    SMALL_MOLECULE_ENTITY_TYPE,
)
from pypath.inputs_v2.uniprot import resource as uniprot_resource
from omnipath_build.duckdb_load import (
    DuckDBEvidenceProjector,
    _bulk_copy_canonical,
    _bulk_copy_evidence,
    _bulk_load_assert_empty,
    _bulk_load_create_views_from_loaded_tables,
    _bulk_load_materialize_dimensions,
    _bulk_load_small_dimensions,
    _canonicalize_loaded_duckdb,
    _create_duckdb_content_uuid_macro,
    _create_duckdb_evidence_tables,
    _create_duckdb_identifier_type_all_view,
    _create_duckdb_resolver_views,
    _drop_bulk_load_constraints_and_indexes,
    _reset_postgres_sequences,
    _sql_literal,
)
from omnipath_build.ingest.bulk import (
    BulkIngestor,
    _index_staging_tables,
    _create_staging_tables,
)
from omnipath_build.ingest.common import unwrap_record
from pypath.internals.silver_schema import Entity
from omnipath_build.parquet_projector import (
    ParquetEvidenceProjector,
    _MutableProjectionStats,
)
from pypath.internals.ontology_schema import OntologyTerm

SOURCE = 'uniprot'
DATASET = 'proteins'
ONTOLOGY_DATASET = 'ontology'
ONTOLOGY_ID = 'uniprot_keywords'
PROTEIN_ENTITY_TYPE = 'Protein:MI:0326'
UNIPROT_ID_TYPE = 'Uniprot:MI:1097'
UNRESOLVED_ID_TYPE = 'omnipath:unresolved_entity_key'


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
    copy_load: bool = False,
    drop_load_constraints: bool = False,
) -> ParquetDuckDBStats:
    """Project and canonicalize UniProt in DuckDB, then load Postgres directly."""

    resolver_dir = Path(resolver_dir)
    if state_path is not None:
        state_path = Path(state_path)
        if state_path.exists():
            state_path.unlink()
    con = duckdb.connect(str(state_path) if state_path is not None else ':memory:')
    con.execute("SET threads TO 4")
    _create_duckdb_content_uuid_macro(con)

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
    _bulk_load_materialize_dimensions(con, schema)
    if copy_load:
        if drop_load_constraints:
            _drop_bulk_load_constraints_and_indexes(
                database_url=database_url,
                schema=schema,
            )
        _bulk_copy_evidence(con, schema=schema, database_url=database_url)
        _bulk_copy_canonical(con, schema=schema, database_url=database_url)
        con.execute('DETACH pg')
    else:
        _bulk_load_evidence(con, schema)
        _bulk_load_canonical(con, schema, database_url=database_url)
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
            {_sql_literal(PROTEIN_ENTITY_TYPE)} AS entity_type,
            key_identifier_type_id,
            key_value,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier
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
    _create_duckdb_content_uuid_macro(con)
    con.execute(
        f"""
        CREATE VIEW identifier_type AS
        SELECT *
        FROM read_parquet({_sql_literal(resolver_dir / 'identifier_type.parquet')})
        """
    )
    _create_duckdb_identifier_type_all_view(con)
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
        CREATE VIEW relation_evidence_raw AS
        SELECT *
        FROM read_parquet({_sql_literal(evidence_dir / 'relation_evidence.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW relation_annotation_raw AS
        SELECT
          source,
          evidence_id AS relation_evidence_id,
          annotation_key,
          term,
          value,
          unit
        FROM read_parquet({_sql_literal(evidence_dir / 'relation_annotation.parquet')})
        """
    )
    con.execute(
        f"""
        CREATE VIEW annotation_relation_evidence_raw AS
        SELECT *
        FROM read_parquet({_sql_literal(evidence_dir / 'annotation_relation_evidence.parquet')})
        """
    )
    _canonicalize_loaded_duckdb(con)
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
    _create_duckdb_content_uuid_macro(con)
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
    _bulk_load_materialize_dimensions(con, schema)
    _bulk_load_evidence(con, schema)
    _bulk_load_canonical(con, schema, database_url=database_url)
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
    con.execute(
        """
        CREATE VIEW pq_annotation_relation_evidence_resolved AS
        SELECT
          ar.*,
          object.entity_id AS object_entity_id
        FROM pq_annotation_relation_evidence ar
        JOIN pq_entity object
          ON object.entity_type = ar.object_entity_type
         AND object.canonical_identifier_type = ar.object_id_type
         AND object.canonical_identifier = ar.object_id
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
          NULL::UUID,
          rp.relation_predicate_id,
          r.object_entity_evidence_id::UUID,
          NULL::UUID,
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
    *,
    database_url: str,
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
        )
        SELECT
          canonical_entity_uuid(
            {_sql_literal(CV_TERM_ENTITY_TYPE)},
            NULL,
            missing.identifier_type,
            missing.term_id
          ),
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
          NULL::UUID,
          rp.relation_predicate_id,
          NULL::UUID,
          ar.object_entity_id,
          rc.relation_category_id
        FROM pq_annotation_relation_evidence_resolved ar
        JOIN pg.{schema}.data_source ds
          ON ds.name = ar.source
        JOIN pg.{schema}.dataset d
          ON d.source_id = ds.source_id
         AND d.name = ar.dataset
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
    _copy_relation_evidence_relation(
        con,
        schema=schema,
        database_url=database_url,
    )


def _copy_relation_evidence_relation(
    con: duckdb.DuckDBPyConnection,
    *,
    schema: str,
    database_url: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / 'relation_evidence_relation.csv'
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE load_relation_evidence_relation AS
            SELECT
              ds.source_id,
              rer.relation_id,
              rer.relation_evidence_id::UUID AS relation_evidence_id
            FROM pq_relation_evidence_relation rer
            JOIN load_data_source ds
              ON ds.name = rer.source
            """
        )
        con.execute(
            f"""
            COPY (
              SELECT
                source_id,
                relation_id,
                relation_evidence_id
              FROM load_relation_evidence_relation
            )
            TO {_sql_literal(csv_path)}
            (FORMAT CSV, HEADER false, DELIMITER ',', QUOTE '"', ESCAPE '"')
            """
        )
        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                with csv_path.open('r', encoding='utf-8') as csv_file:
                    cur.copy_expert(
                        sql.SQL(
                            """
                            COPY {}.relation_evidence_relation (
                              source_id,
                              relation_id,
                              relation_evidence_id
                            )
                            FROM STDIN WITH (FORMAT CSV)
                            """
                        ).format(sql.Identifier(schema)),
                        csv_file,
                    )



def _create_postgres_staging(cur: psycopg2.extensions.cursor) -> None:
    cur.execute('DROP TABLE IF EXISTS _pq_entity')
    cur.execute('DROP TABLE IF EXISTS _pq_entity_evidence_resolution')
    cur.execute('DROP TABLE IF EXISTS _pq_relation')
    cur.execute('DROP TABLE IF EXISTS _pq_relation_evidence_relation')
    cur.execute(
        """
        CREATE TEMP TABLE _pq_entity (
          entity_id uuid PRIMARY KEY,
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
          entity_id uuid
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE _pq_relation (
          relation_id uuid PRIMARY KEY,
          subject_entity_id uuid NOT NULL,
          predicate text NOT NULL,
          object_entity_id uuid NOT NULL,
          relation_category text
        ) ON COMMIT DROP
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE _pq_relation_evidence_relation (
          source text NOT NULL,
          relation_evidence_id uuid NOT NULL,
          relation_id uuid NOT NULL
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
    parser.add_argument(
        '--copy-load',
        action='store_true',
        help='Experimental: load high-volume tables by Postgres COPY.',
    )
    parser.add_argument(
        '--drop-load-constraints',
        action='store_true',
        help='Experimental: drop content table constraints/indexes before COPY load.',
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
            copy_load=args.copy_load,
            drop_load_constraints=args.drop_load_constraints,
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
