"""Low-level DuckDB/PostgreSQL load helpers for the COPY pipeline."""

from __future__ import annotations

from pathlib import Path
import tempfile
from itertools import islice
from collections.abc import Iterable

import duckdb
import pyarrow as pa
import psycopg2
from psycopg2 import sql

from omnipath_build.cv_terms import (
    CV_TERM_ID_TYPE,
    GENE_ENTITY_TYPE,
    CV_TERM_ENTITY_TYPE,
    CHEMICAL_ENTITY_TYPE,
)
from pypath.internals.cv_terms import (
    BiologicalRoleCv,
    EntityTypeCv,
    IdentifierNamespaceCv,
    cv_term_label_accession,
)
from omnipath_build.ingest.common import unwrap_record
from pypath.internals.silver_schema import Entity
from omnipath_build.evidence_projector import (
    ProjectionStats,
    EvidenceProjectorBase,
    _MutableProjectionStats,
)
from omnipath_build.resolver.identifier_types import (
    UNRESOLVED_ID_TYPE,
    IDENTIFIER_TYPE_NAMES,
    COMPLEX_MEMBER_HASH_ID_TYPE,
    REACTION_MEMBER_HASH_ID_TYPE,
    identifier_type_id,
)

PROTEIN_ENTITY_TYPE = 'Protein:MI:0326'
COMPLEX_ENTITY_TYPE = 'Complex:MI:0314'
REACTION_ENTITY_TYPE = 'Reaction:OM:0015'
MIRNA_ENTITY_TYPE = cv_term_label_accession(EntityTypeCv.MIRNA)
PATHWAY_ENTITY_TYPE = cv_term_label_accession(EntityTypeCv.PATHWAY)
REACTANT_ROLE_TERMS = (
    cv_term_label_accession(BiologicalRoleCv.REACTANT),
    str(BiologicalRoleCv.REACTANT),
    cv_term_label_accession(BiologicalRoleCv.SUBSTRATE),
    str(BiologicalRoleCv.SUBSTRATE),
)
PRODUCT_ROLE_TERMS = (
    cv_term_label_accession(BiologicalRoleCv.PRODUCT),
    str(BiologicalRoleCv.PRODUCT),
)
PROTEIN_TAXONOMY_OPTIONAL_IDENTIFIER_TYPES = (
    cv_term_label_accession(IdentifierNamespaceCv.UNIPROT),
    cv_term_label_accession(IdentifierNamespaceCv.ENSEMBL),
    cv_term_label_accession(IdentifierNamespaceCv.ENTREZ),
    cv_term_label_accession(IdentifierNamespaceCv.HGNC),
    cv_term_label_accession(IdentifierNamespaceCv.UNIPROT_ENTRY_NAME),
)
RESOLVER_ALIAS_EXPANSION_EXCLUDED_IDENTIFIER_TYPES = (
    cv_term_label_accession(IdentifierNamespaceCv.ENSEMBL),
)
STANDARD_INCHI_KEY_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.STANDARD_INCHI_KEY
)
REACTOME_STABLE_ID_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.REACTOME_STABLE_ID
)
WIKIPATHWAYS_ID_TYPE = cv_term_label_accession(IdentifierNamespaceCv.WIKIPATHWAYS)


def _sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _duckdb_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _duckdb_pg_table(schema: str, table: str) -> str:
    return f'pg.{_duckdb_identifier(schema)}.{_duckdb_identifier(table)}'


def _resolver_component_dir(
    resolver_dir: Path,
    *,
    component: str,
    lookup_filename: str,
) -> Path:
    """Resolve the directory that contains one resolver component's parquet outputs."""

    candidates = (
        resolver_dir / component,
        resolver_dir,
        resolver_dir.parent / component,
    )
    for candidate in candidates:
        if (candidate / lookup_filename).exists():
            return candidate
    raise FileNotFoundError(
        f'Could not find {lookup_filename!r} for resolver component {component!r} '
        f'under {resolver_dir}.'
    )


def _chemical_resolver_component_dir(resolver_dir: Path) -> Path:
    """Resolve the directory that contains partitioned chemical lookup files."""

    candidates = (
        resolver_dir / 'chemicals',
        resolver_dir,
        resolver_dir.parent / 'chemicals',
    )
    for candidate in candidates:
        if (
            (candidate / 'lookup').is_dir()
            and _has_parquet_files(candidate / 'lookup')
            and (candidate / 'identifier_type.parquet').exists()
        ):
            return candidate
    raise FileNotFoundError(
        'Could not find partitioned chemical resolver outputs under '
        f'{resolver_dir}. Expected chemicals/lookup/*.parquet and '
        'chemicals/identifier_type.parquet.'
    )


def _parquet_glob(path: Path) -> str:
    return str(path).replace("'", "''")


def _has_parquet_files(path: Path) -> bool:
    return path.is_dir() and any(path.glob('*.parquet'))


def _create_duckdb_content_uuid_macro(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE MACRO content_uuid(payload) AS (
          (
            substr(md5(payload), 1, 8) || '-' ||
            substr(md5(payload), 9, 4) || '-' ||
            substr(md5(payload), 13, 4) || '-' ||
            substr(md5(payload), 17, 4) || '-' ||
            substr(md5(payload), 21, 12)
          )::UUID
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE MACRO canonical_entity_key(
          entity_type,
          taxonomy_id,
          canonical_identifier_type,
          canonical_identifier
        ) AS (
          to_json(
            list_value(
              entity_type::VARCHAR,
              coalesce(taxonomy_id::VARCHAR, ''),
              canonical_identifier_type::VARCHAR,
              canonical_identifier::VARCHAR
            )
          )
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE MACRO canonical_entity_uuid(
          entity_type,
          taxonomy_id,
          canonical_identifier_type,
          canonical_identifier
        ) AS (
          content_uuid(
            canonical_entity_key(
              entity_type,
              taxonomy_id,
              canonical_identifier_type,
              canonical_identifier
            )
          )
        )
        """
    )


def _create_duckdb_resolver_views(
    con: duckdb.DuckDBPyConnection,
    *,
    resolver_dir: Path,
) -> None:
    """Expose resolver parquet inputs in the DuckDB shape used by canonicalize."""

    protein_dir = _resolver_component_dir(
        resolver_dir,
        component='proteins',
        lookup_filename='protein_identifier_lookup.parquet',
    )
    chemical_dir = _chemical_resolver_component_dir(resolver_dir)
    protein_lookup_path = protein_dir / 'protein_identifier_lookup.parquet'
    protein_type_path = protein_dir / 'identifier_type.parquet'
    chemical_lookup_dir = chemical_dir / 'lookup'
    chemical_lookup_glob = chemical_lookup_dir / '*.parquet'
    chemical_type_path = chemical_dir / 'identifier_type.parquet'

    # miRNA (Milestone L): organism-agnostic name/accession -> MI#/MIMAT#. The
    # lookup already carries the identity rows (MI#->MI#, MIMAT#->MIMAT#), so it
    # both resolves precursor/mature names and collapses the maturation-stub
    # matures onto their MIMAT#. Guarded so resolver snapshots predating the
    # mirbase source still load.
    try:
        mirna_dir = _resolver_component_dir(
            resolver_dir,
            component='mirna',
            lookup_filename='mirna_identifier_lookup.parquet',
        )
    except FileNotFoundError:
        mirna_dir = None
    if mirna_dir is not None:
        mirna_lookup_path = mirna_dir / 'mirna_identifier_lookup.parquet'
        mirna_type_path = mirna_dir / 'identifier_type.parquet'
        mirna_identifier_type_sql = f"""
        UNION
        SELECT *
        FROM read_parquet({_sql_literal(mirna_type_path)})
        """
        mirna_lookup_sql = f"""
        UNION ALL
        SELECT
          {_sql_literal(MIRNA_ENTITY_TYPE)} AS entity_type,
          key_identifier_type_id,
          key_value,
          NULL::VARCHAR AS taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier
        FROM read_parquet({_sql_literal(mirna_lookup_path)})
        WHERE key_value IS NOT NULL
          AND canonical_identifier IS NOT NULL
        """
        mirna_canonical_sql = f"""
        UNION ALL
        SELECT
          row_number() OVER (
            ORDER BY canonical_identifier_type_id, canonical_identifier
          )::BIGINT AS resolver_entity_id,
          {_sql_literal(MIRNA_ENTITY_TYPE)} AS entity_type,
          NULL::VARCHAR AS taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier,
          list_distinct(list(key_identifier_type_id)) AS key_identifier_type_ids,
          count(*)::BIGINT AS lookup_rows
        FROM read_parquet({_sql_literal(mirna_lookup_path)})
        WHERE canonical_identifier IS NOT NULL
        GROUP BY
          canonical_identifier_type_id,
          canonical_identifier
        """
    else:
        mirna_identifier_type_sql = ''
        mirna_lookup_sql = ''
        mirna_canonical_sql = ''
    chemical_identifier_type_sql = f"""
        UNION
        SELECT *
        FROM read_parquet({_sql_literal(chemical_type_path)})
        """
    chemical_lookup_sql = f"""
        UNION ALL
        SELECT
          {_sql_literal(CHEMICAL_ENTITY_TYPE)} AS entity_type,
          key_identifier_type_id,
          key_value,
          NULL::VARCHAR AS taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier
        FROM read_parquet('{_parquet_glob(chemical_lookup_glob)}')
        WHERE key_value IS NOT NULL
          AND canonical_identifier IS NOT NULL
        UNION ALL
        SELECT DISTINCT
          {_sql_literal(CHEMICAL_ENTITY_TYPE)} AS entity_type,
          {identifier_type_id(STANDARD_INCHI_KEY_TYPE)} AS key_identifier_type_id,
          canonical_identifier AS key_value,
          NULL::VARCHAR AS taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier
        FROM read_parquet('{_parquet_glob(chemical_lookup_glob)}')
        WHERE canonical_identifier_type_id =
              {identifier_type_id(STANDARD_INCHI_KEY_TYPE)}
          AND canonical_identifier IS NOT NULL
          AND canonical_identifier <> ''
        """
    con.execute(
        f"""
        CREATE VIEW identifier_type AS
        SELECT *
        FROM read_parquet({_sql_literal(protein_type_path)})
        {chemical_identifier_type_sql}
        {mirna_identifier_type_sql}
        """
    )
    _create_duckdb_identifier_type_all_view(con)
    con.execute(
        f"""
        CREATE VIEW resolver_lookup AS
        SELECT
          {_sql_literal(PROTEIN_ENTITY_TYPE)} AS entity_type,
          key_identifier_type_id,
          key_value,
          taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier
        FROM read_parquet({_sql_literal(protein_lookup_path)})
        WHERE key_value IS NOT NULL
          AND canonical_identifier IS NOT NULL
        {chemical_lookup_sql}
        {mirna_lookup_sql}
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
        FROM read_parquet({_sql_literal(protein_lookup_path)})
        WHERE canonical_identifier IS NOT NULL
        GROUP BY
          taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier
        UNION ALL
        SELECT
          row_number() OVER (
            ORDER BY canonical_identifier_type_id, canonical_identifier
          )::BIGINT AS resolver_entity_id,
          {_sql_literal(CHEMICAL_ENTITY_TYPE)} AS entity_type,
          NULL::VARCHAR AS taxonomy_id,
          canonical_identifier_type_id,
          canonical_identifier,
          list_distinct(list(key_identifier_type_id)) AS key_identifier_type_ids,
          count(*)::BIGINT AS lookup_rows
        FROM read_parquet('{_parquet_glob(chemical_lookup_glob)}')
        WHERE canonical_identifier IS NOT NULL
        GROUP BY
          canonical_identifier_type_id,
          canonical_identifier
        {mirna_canonical_sql}
        """
    )


def _create_duckdb_identifier_type_all_view(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Expose resolver identifier types plus local synthetic namespaces."""

    static_rows_sql = ',\n'.join(
        f'({identifier_type_id(name)}, {_sql_literal(name)})'
        for name in IDENTIFIER_TYPE_NAMES
    )
    has_entity_identifier_raw = bool(
        con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = 'entity_identifier_raw'
            """
        ).fetchone()[0]
    )
    has_annotation_relation_evidence_raw = bool(
        con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = 'annotation_relation_evidence_raw'
            """
        ).fetchone()[0]
    )
    has_ontology_relation_raw = bool(
        con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = 'ontology_relation_raw'
            """
        ).fetchone()[0]
    )
    evidence_identifier_type_sql = (
        """
          UNION
          SELECT DISTINCT identifier_type AS name
          FROM entity_identifier_raw
          WHERE identifier_type IS NOT NULL
            AND identifier_type <> ''
        """
        if has_entity_identifier_raw
        else ''
    )
    annotation_relation_identifier_type_sql = (
        """
          UNION
          SELECT DISTINCT object_id_type AS name
          FROM annotation_relation_evidence_raw
          WHERE object_id_type IS NOT NULL
            AND object_id_type <> ''
        """
        if has_annotation_relation_evidence_raw
        else ''
    )
    ontology_relation_identifier_type_sql = (
        """
          UNION
          SELECT DISTINCT subject_identifier_type AS name
          FROM ontology_relation_raw
          WHERE subject_identifier_type IS NOT NULL
            AND subject_identifier_type <> ''
          UNION
          SELECT DISTINCT object_identifier_type AS name
          FROM ontology_relation_raw
          WHERE object_identifier_type IS NOT NULL
            AND object_identifier_type <> ''
        """
        if has_ontology_relation_raw
        else ''
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW identifier_type_all AS
        WITH static_identifier_type(identifier_type_id, name) AS (
          VALUES
          {static_rows_sql}
        ),
        base_identifier_type AS (
          SELECT * FROM identifier_type
          UNION
          SELECT * FROM static_identifier_type
        ),
        base AS (
          SELECT coalesce(max(identifier_type_id), 0) AS max_id
          FROM base_identifier_type
        ),
        required_name AS (
          SELECT {_sql_literal(CV_TERM_ID_TYPE)} AS name
          UNION ALL
          SELECT {_sql_literal(UNRESOLVED_ID_TYPE)} AS name
          {evidence_identifier_type_sql}
          {annotation_relation_identifier_type_sql}
          {ontology_relation_identifier_type_sql}
        ),
        missing AS (
          SELECT DISTINCT required_name.name
          FROM required_name
          LEFT JOIN base_identifier_type base_type
            ON base_type.name = required_name.name
          WHERE base_type.identifier_type_id IS NULL
        )
        SELECT * FROM base_identifier_type
        UNION ALL
        SELECT
          base.max_id + row_number() OVER (ORDER BY missing.name)
            AS identifier_type_id,
          missing.name
        FROM missing
        CROSS JOIN base
        """
    )


class DuckDBEvidenceProjector(EvidenceProjectorBase):
    """Flatten silver entity streams into DuckDB evidence tables."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        chunk_size: int = 100_000,
    ) -> None:
        super().__init__(chunk_size=chunk_size)
        self.con = con

    def project_records(
        self,
        records: Iterable[object],
        *,
        source: str,
        dataset: str,
        max_records: int | None = None,
        row_offset: int = 0,
    ) -> ProjectionStats:
        """Project source records into loaded DuckDB evidence tables."""

        if max_records is not None:
            records = islice(records, max_records)

        writers = _DuckDBEvidenceWriters(self.con, chunk_size=self.chunk_size)
        seen_annotations: set[tuple[str, str, str | None, str | None]] = set()
        stats = _MutableProjectionStats()
        try:
            for index, item in enumerate(records, start=row_offset + 1):
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
            _RELATION_ANNOTATION_REF_SCHEMA,
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
        self.ontology_relation = _DuckDBRowWriter(
            con,
            'ontology_relation_raw',
            _ONTOLOGY_RELATION_SCHEMA,
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
        self.ontology_relation.close()


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


_RELATION_ANNOTATION_REF_SCHEMA = pa.schema(
    [
        ('source', pa.string()),
        ('evidence_id', pa.string()),
        ('annotation_key', pa.string()),
        ('annotation_scope', pa.string()),
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


_ONTOLOGY_RELATION_SCHEMA = pa.schema(
    [
        ('source', pa.string()),
        ('dataset', pa.string()),
        ('subject_entity_evidence_id', pa.string()),
        ('ontology_id', pa.string()),
        ('subject_entity_type', pa.string()),
        ('subject_identifier_type', pa.string()),
        ('subject_identifier', pa.string()),
        ('predicate', pa.string()),
        ('object_entity_type', pa.string()),
        ('object_identifier_type', pa.string()),
        ('object_identifier', pa.string()),
    ]
)


def _create_duckdb_evidence_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE entity_evidence_raw (
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
        CREATE OR REPLACE TABLE ontology_terms_raw (
          source VARCHAR,
          dataset VARCHAR,
          term_id VARCHAR,
          term_entity_type VARCHAR,
          term_identifier_type VARCHAR,
          term_identifier VARCHAR,
          ontology_prefix VARCHAR,
          label VARCHAR,
          definition VARCHAR,
          ontology_id VARCHAR,
          synonyms VARCHAR[],
          synonyms_text VARCHAR,
          sources VARCHAR[]
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE ontology_relation_raw (
          source VARCHAR,
          dataset VARCHAR,
          subject_entity_evidence_id VARCHAR,
          ontology_id VARCHAR,
          subject_entity_type VARCHAR,
          subject_identifier_type VARCHAR,
          subject_identifier VARCHAR,
          predicate VARCHAR,
          object_entity_type VARCHAR,
          object_identifier_type VARCHAR,
          object_identifier VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE entity_identifier_raw (
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
        CREATE OR REPLACE TABLE entity_annotation_raw (
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
        CREATE OR REPLACE TABLE relation_annotation_raw (
          source VARCHAR,
          evidence_id VARCHAR,
          annotation_key VARCHAR,
          annotation_scope VARCHAR,
          term VARCHAR,
          value VARCHAR,
          unit VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE annotation_value (
          annotation_key VARCHAR,
          term VARCHAR,
          value VARCHAR,
          unit VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE relation_evidence_raw (
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
        CREATE OR REPLACE TABLE annotation_relation_evidence_raw (
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


def _ensure_duckdb_canonical_caches(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_key_cache (
          entity_id UUID,
          entity_key VARCHAR PRIMARY KEY,
          entity_type VARCHAR,
          taxonomy_id VARCHAR,
          canonical_identifier_type VARCHAR,
          canonical_identifier_type_id BIGINT,
          canonical_identifier VARCHAR,
          sources VARCHAR,
          first_seen_at TIMESTAMP,
          last_seen_at TIMESTAMP
        )
        """
    )
    con.execute(
        'ALTER TABLE entity_key_cache DROP COLUMN IF EXISTS identifiers_json'
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS relation_key_cache (
          relation_id UUID,
          relation_key VARCHAR PRIMARY KEY,
          subject_entity_id UUID,
          predicate VARCHAR,
          object_entity_id UUID,
          sources VARCHAR,
          first_seen_at TIMESTAMP,
          last_seen_at TIMESTAMP
        )
        """
    )


def _drop_duckdb_batch_tables(con: duckdb.DuckDBPyConnection) -> None:
    for table in (
        'evidence_identifier_key',
        'resolver_entity_type_match',
        'taxonomy_optional_resolver_key_type',
        'taxonomy_optional_unambiguous_key',
        'needed_resolver_lookup',
        'entity_identifier_group',
        'cv_term_evidence_resolution',
        'pathway_identifier_evidence_resolution',
        'pathway_stable_id_evidence_resolution',
        'standard_inchi_key_evidence_resolution',
        'entity_resolution_base',
        'complex_member_signature_base',
        'complex_member_signature',
        'reaction_member_signature',
        'entity_resolution',
        'ontology_term_resolution',
        'batch_entity_candidate',
        'new_entity',
        'canonical_entity',
        'canonical_entity_identifier',
        'entity_evidence_resolution',
        'entity_ontology_relation',
        'relation_candidate_evidence',
        'batch_relation_candidate',
        'new_relation',
        'relation',
        'relation_evidence_relation',
    ):
        con.execute(f'DROP TABLE IF EXISTS {table}')


def _canonicalize_loaded_duckdb(
    con: duckdb.DuckDBPyConnection,
) -> tuple[int, int, int]:
    """Canonicalize already-loaded DuckDB evidence and resolver tables."""

    _create_duckdb_identifier_type_all_view(con)
    _ensure_duckdb_canonical_caches(con)
    _drop_duckdb_batch_tables(con)
    resolver_alias_expansion_excluded_type_ids = ', '.join(
        str(identifier_type_id(name))
        for name in RESOLVER_ALIAS_EXPANSION_EXCLUDED_IDENTIFIER_TYPES
    )
    con.execute(
        """
        CREATE TABLE evidence_identifier_key AS
        SELECT DISTINCT
          ee.entity_type,
          ee.taxonomy_id,
          kit.identifier_type_id AS key_identifier_type_id,
          ei.identifier AS key_value
        FROM entity_evidence_raw ee
        JOIN entity_identifier_raw ei
          ON ei.source = ee.source
         AND ei.entity_evidence_id = ee.entity_evidence_id
        JOIN identifier_type_all kit
          ON kit.name = ei.identifier_type
        WHERE ei.identifier IS NOT NULL
          AND ei.identifier <> ''
        """
    )
    con.execute(
        """
        CREATE TABLE resolver_entity_type_match AS
        SELECT ? AS evidence_entity_type, ? AS resolver_entity_type
        UNION ALL
        SELECT ?, ?
        UNION ALL
        SELECT ?, ?
        """,
        [
            PROTEIN_ENTITY_TYPE,
            PROTEIN_ENTITY_TYPE,
            GENE_ENTITY_TYPE,
            PROTEIN_ENTITY_TYPE,
            CHEMICAL_ENTITY_TYPE,
            CHEMICAL_ENTITY_TYPE,
        ],
    )
    con.execute(
        """
        CREATE TABLE taxonomy_optional_resolver_key_type AS
        SELECT identifier_type_id
        FROM identifier_type_all
        WHERE name IN ({})
        """.format(
            ', '.join(
                '?' for _ in PROTEIN_TAXONOMY_OPTIONAL_IDENTIFIER_TYPES
            )
        ),
        list(PROTEIN_TAXONOMY_OPTIONAL_IDENTIFIER_TYPES),
    )
    con.execute(
        """
        CREATE TABLE taxonomy_optional_unambiguous_key AS
        SELECT
          rl.key_identifier_type_id,
          rl.key_value,
          rl.canonical_identifier_type_id
        FROM resolver_lookup rl
        JOIN taxonomy_optional_resolver_key_type opt
          ON opt.identifier_type_id = rl.key_identifier_type_id
        WHERE rl.entity_type = ?
        GROUP BY
          rl.key_identifier_type_id,
          rl.key_value,
          rl.canonical_identifier_type_id
        HAVING count(DISTINCT rl.canonical_identifier) = 1
        """,
        [PROTEIN_ENTITY_TYPE],
    )
    con.execute(
        """
        CREATE TABLE needed_resolver_lookup AS
        SELECT DISTINCT
          etm.evidence_entity_type,
          rl.*,
          opt.key_identifier_type_id IS NOT NULL AS taxonomy_optional_match
        FROM resolver_lookup rl
        JOIN resolver_entity_type_match etm
          ON etm.resolver_entity_type = rl.entity_type
        LEFT JOIN taxonomy_optional_unambiguous_key opt
          ON opt.key_identifier_type_id = rl.key_identifier_type_id
         AND opt.key_value = rl.key_value
         AND opt.canonical_identifier_type_id =
             rl.canonical_identifier_type_id
        JOIN evidence_identifier_key k
          ON k.entity_type = etm.evidence_entity_type
         AND k.key_identifier_type_id = rl.key_identifier_type_id
         AND k.key_value = rl.key_value
         AND (
           rl.taxonomy_id = k.taxonomy_id
           OR rl.taxonomy_id IS NULL
           OR opt.key_identifier_type_id IS NOT NULL
         )
        """
    )
    con.execute(
        """
        CREATE TABLE entity_identifier_group AS
        SELECT
          ee.source,
          ee.entity_evidence_id,
          coalesce(
            string_agg(
              kit.identifier_type_id::VARCHAR || '=' || ei.identifier,
              '|'
              ORDER BY kit.identifier_type_id, ei.identifier
            ),
            ee.source || ':' || ee.entity_evidence_id
          ) AS unresolved_identifier_key
        FROM entity_evidence_raw ee
        LEFT JOIN entity_identifier_raw ei
          ON ei.source = ee.source
         AND ei.entity_evidence_id = ee.entity_evidence_id
         AND ei.identifier IS NOT NULL
         AND ei.identifier <> ''
        LEFT JOIN identifier_type_all kit
          ON kit.name = ei.identifier_type
        GROUP BY ee.source, ee.entity_evidence_id
        """
    )
    con.execute(
        """
        CREATE TABLE cv_term_evidence_resolution AS
        SELECT
          ee.source,
          ee.entity_evidence_id,
          cv_type.identifier_type_id AS canonical_identifier_type_id,
          min(ei.identifier) AS canonical_identifier
        FROM entity_evidence_raw ee
        JOIN entity_identifier_raw ei
          ON ei.source = ee.source
         AND ei.entity_evidence_id = ee.entity_evidence_id
        CROSS JOIN (
          SELECT identifier_type_id
          FROM identifier_type_all
          WHERE name = ?
        ) cv_type
        WHERE ee.entity_type = ?
          AND ei.identifier_type = ?
          AND ei.identifier IS NOT NULL
          AND ei.identifier <> ''
        GROUP BY
          ee.source,
          ee.entity_evidence_id,
          cv_type.identifier_type_id
        """,
        [CV_TERM_ID_TYPE, CV_TERM_ENTITY_TYPE, CV_TERM_ID_TYPE],
    )
    con.execute(
        """
        CREATE TABLE pathway_identifier_evidence_resolution AS
        WITH candidate AS (
          SELECT
            ee.source,
            ee.entity_evidence_id,
            kit.identifier_type_id AS canonical_identifier_type_id,
            min(ei.identifier) AS canonical_identifier,
            CASE
              WHEN ei.identifier_type = ? THEN 1
              WHEN ei.identifier_type = ? THEN 2
              ELSE 100
            END AS priority
          FROM entity_evidence_raw ee
          JOIN entity_identifier_raw ei
            ON ei.source = ee.source
           AND ei.entity_evidence_id = ee.entity_evidence_id
          JOIN identifier_type_all kit
            ON kit.name = ei.identifier_type
          WHERE ee.entity_type = ?
            AND ei.identifier_type IN (?, ?)
            AND ei.identifier IS NOT NULL
            AND ei.identifier <> ''
          GROUP BY
            ee.source,
            ee.entity_evidence_id,
            kit.identifier_type_id,
            ei.identifier_type
          HAVING count(DISTINCT ei.identifier) = 1
        )
        SELECT
          source,
          entity_evidence_id,
          canonical_identifier_type_id,
          canonical_identifier
        FROM candidate
        QUALIFY row_number() OVER (
          PARTITION BY source, entity_evidence_id
          ORDER BY priority, canonical_identifier
        ) = 1
        """,
        [
            REACTOME_STABLE_ID_TYPE,
            WIKIPATHWAYS_ID_TYPE,
            PATHWAY_ENTITY_TYPE,
            REACTOME_STABLE_ID_TYPE,
            WIKIPATHWAYS_ID_TYPE,
        ],
    )
    con.execute(
        """
        CREATE TABLE standard_inchi_key_evidence_resolution AS
        SELECT
          ee.source,
          ee.entity_evidence_id,
          std_type.identifier_type_id AS canonical_identifier_type_id,
          min(ei.identifier) AS canonical_identifier
        FROM entity_evidence_raw ee
        JOIN entity_identifier_raw ei
          ON ei.source = ee.source
         AND ei.entity_evidence_id = ee.entity_evidence_id
        CROSS JOIN (
          SELECT identifier_type_id
          FROM identifier_type_all
          WHERE name = ?
        ) std_type
        WHERE ee.entity_type = ?
          AND ei.identifier_type = ?
          AND regexp_matches(
            ei.identifier,
            '^[A-Z]{14}-[A-Z]{10}-[A-Z]$'
          )
        GROUP BY
          ee.source,
          ee.entity_evidence_id,
          std_type.identifier_type_id
        HAVING count(DISTINCT ei.identifier) = 1
        """,
        [
            STANDARD_INCHI_KEY_TYPE,
            CHEMICAL_ENTITY_TYPE,
            STANDARD_INCHI_KEY_TYPE,
        ],
    )
    con.execute(
        """
        CREATE TABLE entity_resolution_base AS
        WITH direct_resolution AS (
          SELECT
            ee.source,
            ee.dataset,
            ee.row_id,
            ee.entity_evidence_id,
            ee.entity_type,
            CASE
              WHEN cv_term.canonical_identifier IS NOT NULL THEN NULL
              WHEN pathway_identifier.canonical_identifier IS NOT NULL THEN NULL
              ELSE ee.taxonomy_id
            END AS taxonomy_id,
            coalesce(
              cv_term.canonical_identifier_type_id,
              pathway_identifier.canonical_identifier_type_id,
              std_inchi_key.canonical_identifier_type_id
            ) AS canonical_identifier_type_id,
            coalesce(
              cv_term.canonical_identifier,
              pathway_identifier.canonical_identifier,
              std_inchi_key.canonical_identifier
            ) AS canonical_identifier,
            'resolved' AS status
          FROM entity_evidence_raw ee
          LEFT JOIN cv_term_evidence_resolution cv_term
            ON cv_term.source = ee.source
           AND cv_term.entity_evidence_id = ee.entity_evidence_id
          LEFT JOIN pathway_identifier_evidence_resolution pathway_identifier
            ON pathway_identifier.source = ee.source
           AND pathway_identifier.entity_evidence_id = ee.entity_evidence_id
          LEFT JOIN standard_inchi_key_evidence_resolution std_inchi_key
            ON std_inchi_key.source = ee.source
           AND std_inchi_key.entity_evidence_id = ee.entity_evidence_id
          WHERE coalesce(
            cv_term.canonical_identifier,
            pathway_identifier.canonical_identifier,
            std_inchi_key.canonical_identifier
          ) IS NOT NULL
        ),
        remaining_entity AS (
          SELECT ee.*
          FROM entity_evidence_raw ee
          LEFT JOIN direct_resolution direct
            ON direct.source = ee.source
           AND direct.entity_evidence_id = ee.entity_evidence_id
          WHERE direct.entity_evidence_id IS NULL
        ),
        resolver_candidate AS (
          SELECT DISTINCT
            ee.source,
            ee.entity_evidence_id,
            coalesce(rl.taxonomy_id, ee.taxonomy_id) AS taxonomy_id,
            rl.canonical_identifier_type_id,
            rl.canonical_identifier
          FROM remaining_entity ee
          JOIN entity_identifier_raw ei
            ON ei.source = ee.source
           AND ei.entity_evidence_id = ee.entity_evidence_id
          JOIN identifier_type_all kit
            ON kit.name = ei.identifier_type
          JOIN needed_resolver_lookup rl
            ON rl.key_identifier_type_id = kit.identifier_type_id
           AND rl.key_value = ei.identifier
           AND rl.evidence_entity_type = ee.entity_type
           AND (
             rl.taxonomy_id = ee.taxonomy_id
             OR rl.taxonomy_id IS NULL
             OR rl.taxonomy_optional_match
           )
        ),
        resolver_candidate_summary AS (
          SELECT
            source,
            entity_evidence_id,
            count(
              DISTINCT coalesce(taxonomy_id, '') || chr(31) ||
              canonical_identifier_type_id::VARCHAR || chr(31) ||
              canonical_identifier
            ) AS candidate_count,
            min(taxonomy_id) AS taxonomy_id,
            min(canonical_identifier_type_id) AS canonical_identifier_type_id,
            min(canonical_identifier) AS canonical_identifier
          FROM resolver_candidate
          GROUP BY source, entity_evidence_id
        )
        SELECT * FROM direct_resolution
        UNION ALL
        SELECT
          ee.source,
          ee.dataset,
          ee.row_id,
          ee.entity_evidence_id,
          ee.entity_type,
          CASE
            WHEN rcs.candidate_count = 1 THEN rcs.taxonomy_id
            ELSE ee.taxonomy_id
          END AS taxonomy_id,
          CASE
            WHEN rcs.candidate_count = 1 THEN rcs.canonical_identifier_type_id
            ELSE unresolved_type.identifier_type_id
          END AS canonical_identifier_type_id,
          CASE
            WHEN rcs.candidate_count = 1 THEN rcs.canonical_identifier
            ELSE md5(eig.unresolved_identifier_key)
          END AS canonical_identifier,
          CASE
            WHEN rcs.candidate_count = 1 THEN 'resolved'
            ELSE 'unresolved'
          END AS status
        FROM remaining_entity ee
        JOIN entity_identifier_group eig
          ON eig.source = ee.source
         AND eig.entity_evidence_id = ee.entity_evidence_id
        CROSS JOIN (
          SELECT identifier_type_id
          FROM identifier_type_all
          WHERE name = ?
        ) unresolved_type
        LEFT JOIN resolver_candidate_summary rcs
          ON rcs.source = ee.source
         AND rcs.entity_evidence_id = ee.entity_evidence_id
        """,
        [UNRESOLVED_ID_TYPE],
    )
    con.execute(
        """
        CREATE TABLE complex_member_signature_base AS
        WITH complex_member AS (
          SELECT DISTINCT
            parent.source,
            parent.entity_evidence_id,
            child_resolution.entity_type,
            child_resolution.taxonomy_id,
            child_resolution.canonical_identifier_type_id,
            child_resolution.canonical_identifier,
            child_resolution.status
          FROM entity_evidence_raw parent
          JOIN entity_evidence_raw child
            ON child.source = parent.source
           AND child.parent_entity_evidence_id = parent.entity_evidence_id
          JOIN entity_resolution_base child_resolution
            ON child_resolution.source = child.source
           AND child_resolution.entity_evidence_id = child.entity_evidence_id
          WHERE parent.entity_type = ?
        )
        SELECT
          complex_member.source,
          complex_member.entity_evidence_id,
          complex_hash_type.identifier_type_id AS canonical_identifier_type_id,
          sha256(
            to_json(
              list(
                struct_pack(
                  entity_type := complex_member.entity_type,
                  taxonomy_id := complex_member.taxonomy_id,
                  canonical_identifier_type_id := complex_member.canonical_identifier_type_id,
                  canonical_identifier := complex_member.canonical_identifier
                )
                ORDER BY
                  complex_member.entity_type,
                  complex_member.taxonomy_id,
                  complex_member.canonical_identifier_type_id,
                  complex_member.canonical_identifier
              )
            )
          ) AS canonical_identifier,
          CASE
            WHEN bool_and(complex_member.status = 'resolved') THEN 'resolved'
            ELSE 'unresolved'
          END AS status
        FROM complex_member
        CROSS JOIN (
          SELECT identifier_type_id
          FROM identifier_type_all
          WHERE name = ?
        ) complex_hash_type
        GROUP BY
          complex_member.source,
          complex_member.entity_evidence_id,
          complex_hash_type.identifier_type_id
        """,
        [COMPLEX_ENTITY_TYPE, COMPLEX_MEMBER_HASH_ID_TYPE],
    )
    con.execute(
        """
        CREATE TABLE complex_member_signature AS
        WITH complex_member AS (
          SELECT DISTINCT
            parent.source,
            parent.entity_evidence_id,
            child_resolution.entity_type,
            child_resolution.taxonomy_id,
            coalesce(
              child_complex.canonical_identifier_type_id,
              child_resolution.canonical_identifier_type_id
            ) AS canonical_identifier_type_id,
            coalesce(
              child_complex.canonical_identifier,
              child_resolution.canonical_identifier
            ) AS canonical_identifier,
            coalesce(child_complex.status, child_resolution.status) AS status
          FROM entity_evidence_raw parent
          JOIN entity_evidence_raw child
            ON child.source = parent.source
           AND child.parent_entity_evidence_id = parent.entity_evidence_id
          JOIN entity_resolution_base child_resolution
            ON child_resolution.source = child.source
           AND child_resolution.entity_evidence_id = child.entity_evidence_id
          LEFT JOIN complex_member_signature_base child_complex
            ON child_complex.source = child.source
           AND child_complex.entity_evidence_id = child.entity_evidence_id
          WHERE parent.entity_type = ?
        )
        SELECT
          complex_member.source,
          complex_member.entity_evidence_id,
          complex_hash_type.identifier_type_id AS canonical_identifier_type_id,
          sha256(
            to_json(
              list(
                struct_pack(
                  entity_type := complex_member.entity_type,
                  taxonomy_id := complex_member.taxonomy_id,
                  canonical_identifier_type_id := complex_member.canonical_identifier_type_id,
                  canonical_identifier := complex_member.canonical_identifier
                )
                ORDER BY
                  complex_member.entity_type,
                  complex_member.taxonomy_id,
                  complex_member.canonical_identifier_type_id,
                  complex_member.canonical_identifier
              )
            )
          ) AS canonical_identifier,
          CASE
            WHEN bool_and(complex_member.status = 'resolved') THEN 'resolved'
            ELSE 'unresolved'
          END AS status
        FROM complex_member
        CROSS JOIN (
          SELECT identifier_type_id
          FROM identifier_type_all
          WHERE name = ?
        ) complex_hash_type
        GROUP BY
          complex_member.source,
          complex_member.entity_evidence_id,
          complex_hash_type.identifier_type_id
        """,
        [COMPLEX_ENTITY_TYPE, COMPLEX_MEMBER_HASH_ID_TYPE],
    )
    con.execute(
        """
        CREATE TABLE reaction_member_signature AS
        WITH reaction_member AS (
          SELECT DISTINCT
            parent.source,
            parent.entity_evidence_id,
            CASE
              WHEN role_annotation.term IN (?, ?, ?, ?) THEN 'reactant'
              WHEN role_annotation.term IN (?, ?) THEN 'product'
            END AS participant_role,
            child_resolution.entity_type,
            child_resolution.taxonomy_id,
            coalesce(
              child_complex.canonical_identifier_type_id,
              child_resolution.canonical_identifier_type_id
            ) AS canonical_identifier_type_id,
            coalesce(
              child_complex.canonical_identifier,
              child_resolution.canonical_identifier
            ) AS canonical_identifier,
            coalesce(child_complex.status, child_resolution.status) AS status
          FROM entity_evidence_raw parent
          JOIN relation_evidence_raw relation
            ON relation.source = parent.source
           AND relation.subject_entity_evidence_id = parent.entity_evidence_id
           AND relation.predicate = 'has_participant'
          JOIN relation_annotation_raw role_annotation
            ON role_annotation.source = relation.source
           AND role_annotation.evidence_id = relation.relation_evidence_id
          JOIN entity_resolution_base child_resolution
            ON child_resolution.source = relation.source
           AND child_resolution.entity_evidence_id =
               relation.object_entity_evidence_id
          LEFT JOIN complex_member_signature child_complex
            ON child_complex.source = child_resolution.source
           AND child_complex.entity_evidence_id =
               child_resolution.entity_evidence_id
          WHERE parent.entity_type = ?
            AND role_annotation.term IN (?, ?, ?, ?, ?, ?)
        )
        SELECT
          reaction_member.source,
          reaction_member.entity_evidence_id,
          reaction_hash_type.identifier_type_id AS canonical_identifier_type_id,
          sha256(
            to_json(
              list(
                struct_pack(
                  participant_role := reaction_member.participant_role,
                  entity_type := reaction_member.entity_type,
                  taxonomy_id := reaction_member.taxonomy_id,
                  canonical_identifier_type_id :=
                    reaction_member.canonical_identifier_type_id,
                  canonical_identifier := reaction_member.canonical_identifier
                )
                ORDER BY
                  reaction_member.participant_role,
                  reaction_member.entity_type,
                  reaction_member.taxonomy_id,
                  reaction_member.canonical_identifier_type_id,
                  reaction_member.canonical_identifier
              )
            )
          ) AS canonical_identifier,
          CASE
            WHEN bool_and(reaction_member.status = 'resolved') THEN 'resolved'
            ELSE 'unresolved'
          END AS status
        FROM reaction_member
        CROSS JOIN (
          SELECT identifier_type_id
          FROM identifier_type_all
          WHERE name = ?
        ) reaction_hash_type
        GROUP BY
          reaction_member.source,
          reaction_member.entity_evidence_id,
          reaction_hash_type.identifier_type_id
        HAVING bool_or(reaction_member.participant_role = 'reactant')
           AND bool_or(reaction_member.participant_role = 'product')
        """,
        [
            *REACTANT_ROLE_TERMS,
            *PRODUCT_ROLE_TERMS,
            REACTION_ENTITY_TYPE,
            *REACTANT_ROLE_TERMS,
            *PRODUCT_ROLE_TERMS,
            REACTION_MEMBER_HASH_ID_TYPE,
        ],
    )
    con.execute(
        """
        CREATE TABLE entity_resolution AS
        SELECT
          base.source,
          base.dataset,
          base.row_id,
          base.entity_evidence_id,
          base.entity_type,
          base.taxonomy_id,
          coalesce(
            reaction_member.canonical_identifier_type_id,
            complex_member.canonical_identifier_type_id,
            base.canonical_identifier_type_id
          ) AS canonical_identifier_type_id,
          coalesce(
            reaction_member.canonical_identifier,
            complex_member.canonical_identifier,
            base.canonical_identifier
          ) AS canonical_identifier,
          coalesce(
            reaction_member.status,
            complex_member.status,
            base.status
          ) AS status
        FROM entity_resolution_base base
        LEFT JOIN complex_member_signature complex_member
          ON complex_member.source = base.source
         AND complex_member.entity_evidence_id = base.entity_evidence_id
        LEFT JOIN reaction_member_signature reaction_member
          ON reaction_member.source = base.source
         AND reaction_member.entity_evidence_id = base.entity_evidence_id
        """
    )
    con.execute(
        """
        CREATE TABLE ontology_term_resolution AS
        WITH term_key AS (
          SELECT DISTINCT
            ot.source,
            ot.ontology_id,
            ot.term_id,
            ot.term_entity_type AS entity_type,
            NULL::VARCHAR AS taxonomy_id,
            kit.identifier_type_id AS term_identifier_type_id,
            ot.term_identifier
          FROM ontology_terms_raw ot
          JOIN identifier_type_all kit
            ON kit.name = ot.term_identifier_type
          WHERE ot.term_identifier IS NOT NULL
            AND ot.term_identifier <> ''
            AND ot.term_entity_type IS NOT NULL
        )
        SELECT
          tk.source,
          tk.ontology_id,
          tk.term_id,
          tk.entity_type,
          coalesce(rl.taxonomy_id, tk.taxonomy_id) AS taxonomy_id,
          tk.term_identifier_type_id,
          tk.term_identifier,
          coalesce(
            rl.canonical_identifier_type_id,
            tk.term_identifier_type_id
          ) AS canonical_identifier_type_id,
          coalesce(
            rl.canonical_identifier,
            tk.term_identifier
          ) AS canonical_identifier
        FROM term_key tk
        LEFT JOIN resolver_entity_type_match etm
          ON etm.evidence_entity_type = tk.entity_type
        LEFT JOIN resolver_lookup rl
          ON rl.entity_type = etm.resolver_entity_type
         AND rl.key_identifier_type_id = tk.term_identifier_type_id
         AND rl.key_value = tk.term_identifier
         AND (rl.taxonomy_id = tk.taxonomy_id OR rl.taxonomy_id IS NULL)
        QUALIFY row_number() OVER (
          PARTITION BY
            tk.source,
            tk.ontology_id,
            tk.entity_type,
            tk.term_identifier_type_id,
            tk.term_identifier
          ORDER BY
            rl.canonical_identifier IS NULL,
            rl.canonical_identifier_type_id,
            rl.canonical_identifier
        ) = 1
        """
    )
    con.execute(
        """
        CREATE TABLE batch_entity_candidate AS
        WITH complex_hash_type AS (
          SELECT identifier_type_id
          FROM identifier_type_all
          WHERE name IN (?, ?)
        ),
        needed_resolved_key AS (
          SELECT DISTINCT
            er.entity_type,
            er.taxonomy_id,
            er.canonical_identifier_type_id,
            er.canonical_identifier
          FROM entity_resolution er
          WHERE er.status = 'resolved'
            AND er.canonical_identifier_type_id NOT IN (
              SELECT identifier_type_id
              FROM complex_hash_type
            )
        ),
        needed_resolved_entity AS (
          SELECT DISTINCT
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier
          FROM needed_resolved_key
        ),
        complex_member_entity AS (
          SELECT
            er.entity_type,
            er.taxonomy_id,
            er.canonical_identifier_type_id,
            er.canonical_identifier,
            er.status AS resolution_status
          FROM entity_resolution er
          WHERE er.canonical_identifier_type_id IN (
            SELECT identifier_type_id
            FROM complex_hash_type
          )
          GROUP BY
            er.entity_type,
            er.taxonomy_id,
            er.canonical_identifier_type_id,
            er.canonical_identifier,
            er.status
        ),
        cv_term_entity AS (
          SELECT DISTINCT
            ? AS entity_type,
            NULL::VARCHAR AS taxonomy_id,
            cv_type.identifier_type_id AS canonical_identifier_type_id,
            object_id AS canonical_identifier
          FROM annotation_relation_evidence_raw
          CROSS JOIN (
            SELECT identifier_type_id
            FROM identifier_type_all
            WHERE name = ?
          ) cv_type
          WHERE object_entity_type = ?
            AND object_id_type = ?
            AND object_id IS NOT NULL
            AND NOT EXISTS (
              SELECT 1
              FROM needed_resolved_entity existing_term
              WHERE existing_term.entity_type = ?
                AND existing_term.taxonomy_id IS NULL
                AND existing_term.canonical_identifier_type_id =
                    cv_type.identifier_type_id
                AND existing_term.canonical_identifier = object_id
            )
        ),
        annotation_object_entity AS (
          SELECT DISTINCT
            ar.object_entity_type AS entity_type,
            NULL::VARCHAR AS taxonomy_id,
            object_type.identifier_type_id AS canonical_identifier_type_id,
            ar.object_id AS canonical_identifier
          FROM annotation_relation_evidence_raw ar
          JOIN identifier_type_all object_type
            ON object_type.name = ar.object_id_type
          WHERE ar.object_entity_type <> ?
            AND ar.object_id IS NOT NULL
            AND ar.object_id <> ''
            AND NOT EXISTS (
              SELECT 1
              FROM needed_resolved_entity existing_entity
              WHERE existing_entity.entity_type = ar.object_entity_type
                AND existing_entity.taxonomy_id IS NULL
                AND existing_entity.canonical_identifier_type_id =
                    object_type.identifier_type_id
                AND existing_entity.canonical_identifier = ar.object_id
            )
        ),
        ontology_term_identifier_row AS (
          SELECT DISTINCT
            otr.entity_type,
            otr.taxonomy_id,
            otr.canonical_identifier_type_id,
            otr.canonical_identifier,
            otr.term_identifier_type_id AS identifier_type_id,
            otr.term_identifier AS identifier
          FROM ontology_term_resolution otr
          WHERE otr.canonical_identifier IS NOT NULL
            AND otr.canonical_identifier <> ''
          UNION
          SELECT DISTINCT
            otr.entity_type,
            otr.taxonomy_id,
            otr.canonical_identifier_type_id,
            otr.canonical_identifier,
            otr.canonical_identifier_type_id AS identifier_type_id,
            otr.canonical_identifier AS identifier
          FROM ontology_term_resolution otr
          WHERE otr.canonical_identifier IS NOT NULL
            AND otr.canonical_identifier <> ''
        ),
        ontology_term_entity AS (
          SELECT DISTINCT
            otir.entity_type,
            otir.taxonomy_id,
            otir.canonical_identifier_type_id,
            otir.canonical_identifier
          FROM ontology_term_identifier_row otir
          WHERE NOT EXISTS (
            SELECT 1
            FROM needed_resolved_entity existing_entity
            WHERE existing_entity.entity_type = otir.entity_type
              AND existing_entity.taxonomy_id IS NOT DISTINCT FROM otir.taxonomy_id
              AND existing_entity.canonical_identifier_type_id =
                  otir.canonical_identifier_type_id
              AND existing_entity.canonical_identifier =
                  otir.canonical_identifier
          )
        ),
        ontology_relation_endpoint_key AS (
          SELECT DISTINCT
            endpoint.source,
            endpoint.entity_type,
            NULL::VARCHAR AS taxonomy_id,
            kit.identifier_type_id AS endpoint_identifier_type_id,
            endpoint.identifier AS endpoint_identifier,
            coalesce(rl.taxonomy_id, NULL::VARCHAR) AS resolved_taxonomy_id,
            coalesce(
              rl.canonical_identifier_type_id,
              kit.identifier_type_id
            ) AS canonical_identifier_type_id,
            coalesce(
              rl.canonical_identifier,
              endpoint.identifier
            ) AS canonical_identifier
          FROM (
            SELECT
              source,
              subject_entity_type AS entity_type,
              subject_identifier_type AS identifier_type,
              subject_identifier AS identifier
            FROM ontology_relation_raw
            WHERE subject_entity_evidence_id IS NULL
              AND subject_identifier IS NOT NULL
              AND subject_identifier <> ''
            UNION
            SELECT
              source,
              object_entity_type AS entity_type,
              object_identifier_type AS identifier_type,
              object_identifier AS identifier
            FROM ontology_relation_raw
            WHERE object_identifier IS NOT NULL
              AND object_identifier <> ''
          ) endpoint
          JOIN identifier_type_all kit
            ON kit.name = endpoint.identifier_type
          LEFT JOIN resolver_entity_type_match etm
            ON etm.evidence_entity_type = endpoint.entity_type
          LEFT JOIN resolver_lookup rl
            ON rl.entity_type = etm.resolver_entity_type
           AND rl.key_identifier_type_id = kit.identifier_type_id
           AND rl.key_value = endpoint.identifier
           AND rl.taxonomy_id IS NULL
          WHERE endpoint.entity_type IS NOT NULL
          QUALIFY row_number() OVER (
            PARTITION BY
              endpoint.entity_type,
              kit.identifier_type_id,
              endpoint.identifier
            ORDER BY
              rl.canonical_identifier IS NULL,
              rl.canonical_identifier_type_id,
              rl.canonical_identifier
          ) = 1
        ),
        ontology_relation_identifier_row AS (
          SELECT DISTINCT
            entity_type,
            resolved_taxonomy_id AS taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            endpoint_identifier_type_id AS identifier_type_id,
            endpoint_identifier AS identifier
          FROM ontology_relation_endpoint_key
          WHERE canonical_identifier IS NOT NULL
            AND canonical_identifier <> ''
          UNION
          SELECT DISTINCT
            entity_type,
            resolved_taxonomy_id AS taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            canonical_identifier_type_id AS identifier_type_id,
            canonical_identifier AS identifier
          FROM ontology_relation_endpoint_key
          WHERE canonical_identifier IS NOT NULL
            AND canonical_identifier <> ''
        ),
        ontology_relation_endpoint_entity AS (
          SELECT DISTINCT
            orir.entity_type,
            orir.taxonomy_id,
            orir.canonical_identifier_type_id,
            orir.canonical_identifier
          FROM ontology_relation_identifier_row orir
          WHERE NOT EXISTS (
            SELECT 1
            FROM needed_resolved_entity existing_entity
            WHERE existing_entity.entity_type = orir.entity_type
              AND existing_entity.taxonomy_id IS NOT DISTINCT FROM orir.taxonomy_id
              AND existing_entity.canonical_identifier_type_id =
                  orir.canonical_identifier_type_id
              AND existing_entity.canonical_identifier =
                  orir.canonical_identifier
          )
        ),
        all_entity AS (
          SELECT
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            'resolved' AS resolution_status
          FROM needed_resolved_entity
          UNION ALL
          SELECT
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            'resolved' AS resolution_status
          FROM cv_term_entity
          UNION ALL
          SELECT
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            'resolved' AS resolution_status
          FROM annotation_object_entity
          UNION ALL
          SELECT
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            'resolved' AS resolution_status
          FROM ontology_term_entity
          UNION ALL
          SELECT
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier,
            'resolved' AS resolution_status
          FROM ontology_relation_endpoint_entity
          UNION ALL
          SELECT * FROM complex_member_entity
          UNION ALL
          SELECT
            er.entity_type,
            er.taxonomy_id,
            er.canonical_identifier_type_id,
            er.canonical_identifier,
            er.status AS resolution_status
          FROM entity_resolution er
          WHERE er.status = 'unresolved'
            AND er.canonical_identifier_type_id NOT IN (
              SELECT identifier_type_id
              FROM complex_hash_type
            )
          GROUP BY
            er.entity_type,
            er.taxonomy_id,
            er.canonical_identifier_type_id,
            er.canonical_identifier,
            er.status
        )
        SELECT
          canonical_entity_uuid(
            all_entity.entity_type,
            all_entity.taxonomy_id,
            it.name,
            all_entity.canonical_identifier
          ) AS entity_id,
          canonical_entity_key(
            all_entity.entity_type,
            all_entity.taxonomy_id,
            it.name,
            all_entity.canonical_identifier
                  ) AS entity_key,
                  all_entity.entity_type,
                  all_entity.taxonomy_id,
                  all_entity.canonical_identifier_type_id,
                  it.name AS canonical_identifier_type,
                  all_entity.canonical_identifier,
                  all_entity.resolution_status,
          string_agg(DISTINCT source_rows.source, ',' ORDER BY source_rows.source)
            AS sources
        FROM all_entity
        JOIN identifier_type_all it
          ON it.identifier_type_id = all_entity.canonical_identifier_type_id
        LEFT JOIN (
          SELECT DISTINCT
            source,
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier
          FROM entity_resolution
          UNION
          SELECT DISTINCT
            source,
            object_entity_type AS entity_type,
            NULL::VARCHAR AS taxonomy_id,
            object_type.identifier_type_id AS canonical_identifier_type_id,
            object_id AS canonical_identifier
          FROM annotation_relation_evidence_raw ar
          JOIN identifier_type_all object_type
            ON object_type.name = ar.object_id_type
          WHERE object_id IS NOT NULL
          UNION
          SELECT DISTINCT
            source,
            entity_type,
            taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier
          FROM ontology_term_resolution
          UNION
          SELECT DISTINCT
            source,
            entity_type,
            resolved_taxonomy_id AS taxonomy_id,
            canonical_identifier_type_id,
            canonical_identifier
          FROM ontology_relation_endpoint_key
        ) source_rows
          ON source_rows.entity_type = all_entity.entity_type
         AND source_rows.taxonomy_id IS NOT DISTINCT FROM all_entity.taxonomy_id
         AND source_rows.canonical_identifier_type_id =
             all_entity.canonical_identifier_type_id
         AND source_rows.canonical_identifier = all_entity.canonical_identifier
        GROUP BY
          entity_id,
                  entity_key,
                  all_entity.entity_type,
                  all_entity.taxonomy_id,
                  all_entity.canonical_identifier_type_id,
                  it.name,
                  all_entity.canonical_identifier,
                  all_entity.resolution_status
        """,
        [
            COMPLEX_MEMBER_HASH_ID_TYPE,
            REACTION_MEMBER_HASH_ID_TYPE,
            CV_TERM_ENTITY_TYPE,
            CV_TERM_ID_TYPE,
            CV_TERM_ENTITY_TYPE,
            CV_TERM_ID_TYPE,
            CV_TERM_ENTITY_TYPE,
            CV_TERM_ENTITY_TYPE,
        ],
    )
    con.execute(
        """
        CREATE TABLE new_entity AS
        SELECT c.*
        FROM batch_entity_candidate c
        LEFT JOIN entity_key_cache cache
          ON cache.entity_key = c.entity_key
        WHERE cache.entity_key IS NULL
        """
    )
    con.execute(
        """
        CREATE TABLE canonical_entity AS
        SELECT * FROM batch_entity_candidate
        """
    )
    con.execute(
        f"""
        CREATE TABLE canonical_entity_identifier AS
        WITH source_entity AS (
          SELECT DISTINCT
            source_name AS source,
            ce.entity_id,
            ce.entity_type,
            ce.taxonomy_id,
            ce.canonical_identifier_type_id,
            ce.canonical_identifier
          FROM canonical_entity ce,
            unnest(string_split(ce.sources, ',')) AS source_names(source_name)
          WHERE ce.sources IS NOT NULL
            AND ce.sources <> ''
            AND source_name IS NOT NULL
            AND source_name <> ''
        ),
        identifier_rows AS (
          SELECT
            se.source,
            se.entity_id,
            se.canonical_identifier_type_id AS identifier_type_id,
            se.canonical_identifier AS identifier
          FROM source_entity se
          WHERE se.canonical_identifier IS NOT NULL
            AND se.canonical_identifier <> ''
          UNION
          SELECT
            se.source,
            se.entity_id,
            rl.key_identifier_type_id AS identifier_type_id,
            rl.key_value AS identifier
          FROM source_entity se
          JOIN resolver_entity_type_match etm
            ON etm.evidence_entity_type = se.entity_type
          JOIN resolver_lookup rl
            ON rl.entity_type = etm.resolver_entity_type
           AND rl.canonical_identifier_type_id =
               se.canonical_identifier_type_id
           AND rl.canonical_identifier = se.canonical_identifier
           AND (
             rl.taxonomy_id = se.taxonomy_id
             OR rl.taxonomy_id IS NULL
           )
          WHERE rl.key_value IS NOT NULL
            AND rl.key_value <> ''
            AND rl.key_identifier_type_id NOT IN (
              {resolver_alias_expansion_excluded_type_ids}
            )
          UNION
          SELECT
            er.source,
            ce.entity_id,
            kit.identifier_type_id,
            ei.identifier
          FROM entity_resolution er
          JOIN canonical_entity ce
            ON ce.entity_type = er.entity_type
           AND ce.taxonomy_id IS NOT DISTINCT FROM er.taxonomy_id
           AND ce.canonical_identifier_type_id =
               er.canonical_identifier_type_id
           AND ce.canonical_identifier = er.canonical_identifier
          JOIN entity_identifier_raw ei
            ON ei.source = er.source
           AND ei.entity_evidence_id = er.entity_evidence_id
          JOIN identifier_type_all kit
            ON kit.name = ei.identifier_type
          WHERE ei.identifier IS NOT NULL
            AND ei.identifier <> ''
          UNION
          SELECT
            otr.source,
            ce.entity_id,
            otr.term_identifier_type_id AS identifier_type_id,
            otr.term_identifier AS identifier
          FROM ontology_term_resolution otr
          JOIN canonical_entity ce
            ON ce.entity_type = otr.entity_type
           AND ce.taxonomy_id IS NOT DISTINCT FROM otr.taxonomy_id
           AND ce.canonical_identifier_type_id =
               otr.canonical_identifier_type_id
           AND ce.canonical_identifier = otr.canonical_identifier
          WHERE otr.term_identifier IS NOT NULL
            AND otr.term_identifier <> ''
        )
        SELECT DISTINCT
          ir.source,
          ir.entity_id,
          it.name AS identifier_type,
          ir.identifier_type_id,
          ir.identifier
        FROM identifier_rows ir
        JOIN identifier_type_all it
          ON it.identifier_type_id = ir.identifier_type_id
        WHERE ir.source IS NOT NULL
          AND ir.identifier IS NOT NULL
          AND ir.identifier <> ''
        """
    )
    con.execute(
        """
        CREATE TABLE entity_ontology_relation AS
        WITH raw_edge AS (
          SELECT
            *,
            source || chr(31) || coalesce(ontology_id, '') || chr(31) ||
              coalesce(
                subject_entity_evidence_id,
                subject_entity_type || chr(30) ||
                  subject_identifier_type || chr(30) ||
                  subject_identifier,
                ''
              ) || chr(31) || predicate || chr(31) ||
              object_entity_type || chr(30) ||
                object_identifier_type || chr(30) ||
                object_identifier AS edge_key
          FROM ontology_relation_raw
          WHERE predicate IS NOT NULL
            AND predicate <> ''
            AND object_identifier IS NOT NULL
            AND object_identifier <> ''
        ),
        endpoint_resolution AS (
          SELECT
            raw.source,
            raw.dataset,
            raw.ontology_id,
            raw.edge_key,
            'subject' AS endpoint_role,
            er.entity_type,
            er.taxonomy_id,
            er.canonical_identifier_type_id,
            er.canonical_identifier
          FROM raw_edge raw
          JOIN entity_resolution er
            ON er.source = raw.source
           AND er.entity_evidence_id = raw.subject_entity_evidence_id
          WHERE raw.subject_entity_evidence_id IS NOT NULL
          UNION ALL
          SELECT
            endpoint.source,
            endpoint.dataset,
            endpoint.ontology_id,
            endpoint.edge_key,
            endpoint.endpoint_role,
            endpoint.entity_type,
            coalesce(rl.taxonomy_id, NULL::VARCHAR) AS taxonomy_id,
            coalesce(
              rl.canonical_identifier_type_id,
              kit.identifier_type_id
            ) AS canonical_identifier_type_id,
            coalesce(
              rl.canonical_identifier,
              endpoint.identifier
            ) AS canonical_identifier
          FROM (
            SELECT
              source,
              dataset,
              ontology_id,
              edge_key,
              subject_entity_type AS entity_type,
              subject_identifier_type AS identifier_type,
              subject_identifier AS identifier,
              'subject' AS endpoint_role
            FROM raw_edge
            WHERE subject_entity_evidence_id IS NULL
              AND subject_identifier IS NOT NULL
              AND subject_identifier <> ''
            UNION ALL
            SELECT
              source,
              dataset,
              ontology_id,
              edge_key,
              object_entity_type AS entity_type,
              object_identifier_type AS identifier_type,
              object_identifier AS identifier,
              'object' AS endpoint_role
            FROM raw_edge
          ) endpoint
          JOIN identifier_type_all kit
            ON kit.name = endpoint.identifier_type
          LEFT JOIN resolver_entity_type_match etm
            ON etm.evidence_entity_type = endpoint.entity_type
          LEFT JOIN resolver_lookup rl
            ON rl.entity_type = etm.resolver_entity_type
           AND rl.key_identifier_type_id = kit.identifier_type_id
           AND rl.key_value = endpoint.identifier
           AND rl.taxonomy_id IS NULL
          QUALIFY row_number() OVER (
            PARTITION BY
              endpoint.edge_key,
              endpoint.endpoint_role
            ORDER BY
              rl.canonical_identifier IS NULL,
              rl.canonical_identifier_type_id,
              rl.canonical_identifier
          ) = 1
        ),
        edge_endpoint AS (
          SELECT
            er.source,
            er.dataset,
            er.ontology_id,
            er.edge_key,
            er.endpoint_role,
            ce.entity_id
          FROM endpoint_resolution er
          JOIN canonical_entity ce
            ON ce.entity_type = er.entity_type
           AND ce.taxonomy_id IS NOT DISTINCT FROM er.taxonomy_id
           AND ce.canonical_identifier_type_id =
               er.canonical_identifier_type_id
           AND ce.canonical_identifier = er.canonical_identifier
        )
        SELECT DISTINCT
          raw.source,
          raw.dataset,
          raw.ontology_id,
          subject.entity_id AS subject_entity_id,
          raw.predicate,
          object.entity_id AS object_entity_id
        FROM raw_edge raw
        JOIN edge_endpoint subject
          ON subject.source = raw.source
         AND subject.ontology_id IS NOT DISTINCT FROM raw.ontology_id
         AND subject.endpoint_role = 'subject'
         AND subject.edge_key = raw.edge_key
        JOIN edge_endpoint object
          ON object.source = raw.source
         AND object.ontology_id IS NOT DISTINCT FROM raw.ontology_id
         AND object.endpoint_role = 'object'
         AND object.edge_key = subject.edge_key
        """
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
         AND ce.taxonomy_id IS NOT DISTINCT FROM er.taxonomy_id
         AND ce.canonical_identifier_type_id = er.canonical_identifier_type_id
         AND ce.canonical_identifier = er.canonical_identifier
        """
    )
    con.execute(
        """
        CREATE TABLE relation_candidate_evidence AS
        WITH member_projected AS (
          SELECT
            rr.source,
            rr.relation_evidence_id,
            subject.entity_id AS subject_entity_id,
            rr.predicate,
            object.entity_id AS object_entity_id,
            rr.relation_category
          FROM relation_evidence_raw rr
          JOIN entity_evidence_resolution subject
            ON subject.source = rr.source
           AND subject.entity_evidence_id = rr.subject_entity_evidence_id
          JOIN entity_evidence_resolution object
            ON object.source = rr.source
           AND object.entity_evidence_id = rr.object_entity_evidence_id
          WHERE subject.entity_id IS NOT NULL
            AND object.entity_id IS NOT NULL
        ),
        annotation_projected AS (
          SELECT
            ar.source,
            ar.relation_evidence_id,
            object.entity_id AS subject_entity_id,
            ar.predicate,
            subject.entity_id AS object_entity_id,
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
        SELECT * FROM member_projected
        UNION ALL
        SELECT * FROM annotation_projected
        """
    )
    con.execute(
        """
        CREATE TABLE batch_relation_candidate AS
        SELECT
          content_uuid(
            subject_entity_id::VARCHAR || '|' ||
            predicate || '|' ||
            object_entity_id::VARCHAR
          ) AS relation_id,
          subject_entity_id::VARCHAR || '|' ||
            predicate || '|' ||
            object_entity_id::VARCHAR AS relation_key,
          subject_entity_id,
          predicate,
          object_entity_id,
          min(relation_category) AS relation_category,
          string_agg(DISTINCT source, ',' ORDER BY source) AS sources
        FROM relation_candidate_evidence
        GROUP BY subject_entity_id, predicate, object_entity_id
        """
    )
    con.execute(
        """
        CREATE TABLE new_relation AS
        SELECT c.*
        FROM batch_relation_candidate c
        LEFT JOIN relation_key_cache cache
          ON cache.relation_key = c.relation_key
        WHERE cache.relation_key IS NULL
        """
    )
    con.execute(
        """
        CREATE TABLE relation AS
        SELECT * FROM batch_relation_candidate
        """
    )
    con.execute(
        """
        CREATE TABLE relation_evidence_relation AS
        SELECT
          evidence.source,
          evidence.relation_evidence_id,
          r.relation_id
        FROM relation_candidate_evidence evidence
        JOIN relation r
          ON r.subject_entity_id = evidence.subject_entity_id
         AND r.predicate = evidence.predicate
         AND r.object_entity_id = evidence.object_entity_id
        """
    )
    _refresh_duckdb_canonical_caches(con)

    entities = int(
        con.sql('SELECT COUNT(*) FROM canonical_entity').fetchone()[0]
    )
    relations = int(con.sql('SELECT COUNT(*) FROM relation').fetchone()[0])
    links = int(
        con.sql('SELECT COUNT(*) FROM relation_evidence_relation').fetchone()[0]
    )
    return entities, relations, links


def _refresh_duckdb_canonical_caches(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE entity_cache_source AS
        SELECT
          entity_key,
          unnest(string_split(sources, ',')) AS source
        FROM entity_key_cache
        WHERE sources IS NOT NULL
          AND sources <> ''
        UNION
        SELECT
          entity_key,
          unnest(string_split(sources, ',')) AS source
        FROM batch_entity_candidate
        WHERE sources IS NOT NULL
          AND sources <> ''
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE entity_key_cache AS
        SELECT
          any_value(entity_id) AS entity_id,
          entity_key,
          any_value(entity_type) AS entity_type,
          any_value(taxonomy_id) AS taxonomy_id,
              any_value(canonical_identifier_type) AS canonical_identifier_type,
              any_value(canonical_identifier_type_id) AS canonical_identifier_type_id,
              any_value(canonical_identifier) AS canonical_identifier,
              coalesce(
            (
              SELECT string_agg(DISTINCT source, ',' ORDER BY source)
              FROM entity_cache_source src
              WHERE src.entity_key = merged.entity_key
            ),
            ''
          ) AS sources,
          min(first_seen_at) AS first_seen_at,
          now() AS last_seen_at
        FROM (
          SELECT
            entity_id,
            entity_key,
            entity_type,
            taxonomy_id,
                canonical_identifier_type,
                canonical_identifier_type_id,
                canonical_identifier,
                sources,
            first_seen_at,
            last_seen_at
          FROM entity_key_cache
          UNION ALL
          SELECT
            entity_id,
            entity_key,
            entity_type,
            taxonomy_id,
                canonical_identifier_type,
                canonical_identifier_type_id,
                canonical_identifier,
                sources,
            now(),
            now()
          FROM batch_entity_candidate
        ) merged
        GROUP BY entity_key
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE relation_cache_source AS
        SELECT
          relation_key,
          unnest(string_split(sources, ',')) AS source
        FROM relation_key_cache
        WHERE sources IS NOT NULL
          AND sources <> ''
        UNION
        SELECT
          relation_key,
          unnest(string_split(sources, ',')) AS source
        FROM batch_relation_candidate
        WHERE sources IS NOT NULL
          AND sources <> ''
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE relation_key_cache AS
        SELECT
          any_value(relation_id) AS relation_id,
          relation_key,
          any_value(subject_entity_id) AS subject_entity_id,
          any_value(predicate) AS predicate,
          any_value(object_entity_id) AS object_entity_id,
          coalesce(
            (
              SELECT string_agg(DISTINCT source, ',' ORDER BY source)
              FROM relation_cache_source src
              WHERE src.relation_key = merged.relation_key
            ),
            ''
          ) AS sources,
          min(first_seen_at) AS first_seen_at,
          now() AS last_seen_at
        FROM (
          SELECT
            relation_id,
            relation_key,
            subject_entity_id,
            predicate,
            object_entity_id,
            sources,
            first_seen_at,
            last_seen_at
          FROM relation_key_cache
          UNION ALL
          SELECT
            relation_id,
            relation_key,
            subject_entity_id,
            predicate,
            object_entity_id,
            sources,
            now(),
            now()
          FROM batch_relation_candidate
        ) merged
        GROUP BY relation_key
        """
    )


_BULK_COPY_CONTENT_TABLES = (
    'identifier_evidence',
    'annotation',
    'entity_evidence',
    'entity_evidence_identifier',
    'entity_evidence_annotation',
    'relation_evidence',
    'relation_evidence_annotation',
    'entity',
    'entity_identifier',
    'ontology_terms',
    'entity_ontology_relation',
    'entity_evidence_resolution',
    'relation',
    'relation_evidence_relation',
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
        'pq_entity_identifier_resolved': 'canonical_entity_identifier',
        'pq_entity_evidence_resolution': 'entity_evidence_resolution',
        'pq_relation': 'relation',
        'pq_relation_evidence_relation': 'relation_evidence_relation',
        'pq_ontology_terms': 'ontology_terms_raw',
        'pq_entity_ontology_relation': 'entity_ontology_relation',
    }
    for view, table in views.items():
        con.execute(f'CREATE VIEW {view} AS SELECT * FROM {table}')
    con.execute(
        """
        CREATE VIEW pq_annotation_relation_evidence_resolved AS
        SELECT
          ar.*,
          object.entity_id AS object_entity_id
        FROM annotation_relation_evidence_raw ar
        JOIN canonical_entity object
          ON object.entity_type = ar.object_entity_type
         AND object.canonical_identifier_type = ar.object_id_type
         AND object.canonical_identifier = ar.object_id
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
        'entity_identifier',
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
        'entity_ontology_relation',
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
          SELECT DISTINCT term_entity_type AS name
          FROM pq_ontology_terms
          WHERE term_id IS NOT NULL
          UNION
          SELECT DISTINCT subject_entity_type AS name
          FROM ontology_relation_raw
          WHERE subject_entity_type IS NOT NULL
          UNION
          SELECT DISTINCT object_entity_type AS name
          FROM ontology_relation_raw
          WHERE object_entity_type IS NOT NULL
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
          UNION
          SELECT DISTINCT predicate AS name
          FROM pq_entity_ontology_relation
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
          SELECT DISTINCT term_identifier_type AS name
          FROM pq_ontology_terms
          WHERE term_id IS NOT NULL
          UNION
          SELECT DISTINCT subject_identifier_type AS name
          FROM ontology_relation_raw
          WHERE subject_identifier_type IS NOT NULL
          UNION
          SELECT DISTINCT object_identifier_type AS name
          FROM ontology_relation_raw
          WHERE object_identifier_type IS NOT NULL
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
        SELECT candidate.source
        FROM (
          SELECT source FROM pq_entity_evidence
          UNION
          SELECT source FROM pq_entity_evidence_resolution
          UNION
          SELECT source FROM pq_relation_evidence
          UNION
          SELECT source FROM pq_annotation_relation_evidence
          UNION
          SELECT source FROM pq_ontology_terms
          UNION
          SELECT source FROM pq_entity_ontology_relation
        ) candidate
        LEFT JOIN pg.{schema}.data_source existing
          ON existing.name = candidate.source
        WHERE candidate.source IS NOT NULL
          AND existing.source_id IS NULL
        """
    )
    con.execute(
        f"""
        INSERT INTO pg.{schema}.dataset (source_id, name)
        SELECT DISTINCT ds.source_id, candidate.dataset
        FROM (
          SELECT source, dataset FROM pq_entity_evidence
          UNION
          SELECT source, dataset FROM pq_relation_evidence
          UNION
          SELECT source, dataset FROM pq_annotation_relation_evidence
          UNION
          SELECT source, dataset FROM pq_ontology_terms
          UNION
          SELECT source, dataset FROM pq_entity_ontology_relation
        ) candidate
        JOIN pg.{schema}.data_source ds
          ON ds.name = candidate.source
        LEFT JOIN pg.{schema}.dataset existing
          ON existing.source_id = ds.source_id
         AND existing.name = candidate.dataset
        WHERE candidate.dataset IS NOT NULL
          AND existing.dataset_id IS NULL
        """
    )


def _bulk_load_materialize_dimensions(
    con: duckdb.DuckDBPyConnection,
    schema: str,
) -> None:
    dimension_tables = (
        (
            'load_data_source',
            f"""
            SELECT source_id, name
            FROM pg.{schema}.data_source
            """,
        ),
        (
            'load_dataset',
            f"""
            SELECT source_id, dataset_id, name
            FROM pg.{schema}.dataset
            """,
        ),
        (
            'load_vocab_identifier_type',
            f"""
            SELECT identifier_type_id, name
            FROM pg.{schema}.vocab_identifier_type
            """,
        ),
        (
            'load_vocab_entity_type',
            f"""
            SELECT entity_type_id, name
            FROM pg.{schema}.vocab_entity_type
            """,
        ),
        (
            'load_vocab_entity_role',
            f"""
            SELECT entity_role_id, name
            FROM pg.{schema}.vocab_entity_role
            """,
        ),
        (
            'load_vocab_relation_predicate',
            f"""
            SELECT relation_predicate_id, name
            FROM pg.{schema}.vocab_relation_predicate
            """,
        ),
        (
            'load_vocab_relation_category',
            f"""
            SELECT relation_category_id, name
            FROM pg.{schema}.vocab_relation_category
            """,
        ),
        (
            'load_vocab_resolution_status',
            f"""
            SELECT resolution_status_id, name
            FROM pg.{schema}.vocab_resolution_status
            """,
        ),
        (
            'load_vocab_annotation_scope',
            f"""
            SELECT annotation_scope_id, name
            FROM pg.{schema}.vocab_annotation_scope
            """,
        ),
    )
    for table, query in dimension_tables:
        con.execute(f'CREATE OR REPLACE TEMP TABLE {table} AS {query}')


def _drop_bulk_load_constraints_and_indexes(
    *,
    database_url: str,
    schema: str,
) -> None:
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.relname, c.conname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = %s
                  AND c.conislocal
                  AND c.contype <> 'n'
                  AND (
                    t.relname = ANY(%s)
                    OR t.relname = ANY(
                      SELECT child.relname
                      FROM pg_inherits i
                      JOIN pg_class parent ON parent.oid = i.inhparent
                      JOIN pg_namespace parent_ns
                        ON parent_ns.oid = parent.relnamespace
                      JOIN pg_class child ON child.oid = i.inhrelid
                      JOIN pg_namespace child_ns
                        ON child_ns.oid = child.relnamespace
                      WHERE parent_ns.nspname = %s
                        AND child_ns.nspname = %s
                        AND parent.relname = ANY(%s)
                    )
                  )
                ORDER BY c.contype = 'f' DESC, t.relname, c.conname
                """,
                [
                    schema,
                    list(_BULK_COPY_CONTENT_TABLES),
                    schema,
                    schema,
                    list(_BULK_COPY_CONTENT_TABLES),
                ],
            )
            constraints = cur.fetchall()
            for table, constraint in constraints:
                cur.execute(
                    sql.SQL(
                        'ALTER TABLE {}.{} DROP CONSTRAINT IF EXISTS {} CASCADE'
                    ).format(
                        sql.Identifier(schema),
                        sql.Identifier(table),
                        sql.Identifier(constraint),
                    )
                )
            cur.execute(
                """
                WITH target_tables AS (
                  SELECT c.oid
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE n.nspname = %s
                    AND c.relname = ANY(%s)
                  UNION
                  SELECT child.oid
                  FROM pg_inherits i
                  JOIN pg_class parent ON parent.oid = i.inhparent
                  JOIN pg_namespace parent_ns
                    ON parent_ns.oid = parent.relnamespace
                  JOIN pg_class child ON child.oid = i.inhrelid
                  JOIN pg_namespace child_ns
                    ON child_ns.oid = child.relnamespace
                  WHERE parent_ns.nspname = %s
                    AND child_ns.nspname = %s
                    AND parent.relname = ANY(%s)
                )
                SELECT index_class.relname
                FROM pg_index idx
                JOIN pg_class index_class ON index_class.oid = idx.indexrelid
                JOIN pg_namespace index_ns ON index_ns.oid = index_class.relnamespace
                JOIN target_tables target ON target.oid = idx.indrelid
                WHERE index_ns.nspname = %s
                  AND NOT EXISTS (
                    SELECT 1
                    FROM pg_inherits index_inherits
                    WHERE index_inherits.inhrelid = idx.indexrelid
                  )
                """,
                [
                    schema,
                    list(_BULK_COPY_CONTENT_TABLES),
                    schema,
                    schema,
                    list(_BULK_COPY_CONTENT_TABLES),
                    schema,
                ],
            )
            indexes = [row[0] for row in cur.fetchall()]
            for index in indexes:
                cur.execute(
                    sql.SQL('DROP INDEX IF EXISTS {}.{} CASCADE').format(
                        sql.Identifier(schema),
                        sql.Identifier(index),
                    )
                )


def _copy_duckdb_query_to_postgres(
    con: duckdb.DuckDBPyConnection,
    *,
    database_url: str,
    schema: str,
    table: str,
    columns: tuple[str, ...],
    query: str,
    attach_source_partition: bool = False,
    source_id: int | None = None,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / f'{table}.csv'
        con.execute(
            f"""
            COPY (
              {query}
            )
            TO {_sql_literal(csv_path)}
            (
              FORMAT CSV,
              HEADER false,
              DELIMITER ',',
              QUOTE '"',
              ESCAPE '"',
              NULL '\\N'
            )
            """
        )
        column_sql = sql.SQL(', ').join(
            sql.Identifier(column) for column in columns
        )
        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                target_table = sql.Identifier(table)
                if attach_source_partition:
                    if source_id is None:
                        raise ValueError(
                            'source_id is required for source partition copy'
                        )
                    staging_table = f'{table}_source_{source_id}_staging'
                    cur.execute(
                        """
                        SELECT EXISTS (
                          SELECT 1
                          FROM pg_inherits i
                          JOIN pg_class parent ON parent.oid = i.inhparent
                          JOIN pg_namespace parent_ns
                            ON parent_ns.oid = parent.relnamespace
                          JOIN pg_class child ON child.oid = i.inhrelid
                          JOIN pg_namespace child_ns
                            ON child_ns.oid = child.relnamespace
                          WHERE parent_ns.nspname = %s
                            AND parent.relname = %s
                            AND child_ns.nspname = %s
                            AND child.relname = %s
                        )
                        """,
                        [schema, table, schema, staging_table],
                    )
                    partition_attached = bool(cur.fetchone()[0])
                    should_attach = not partition_attached
                    if partition_attached:
                        target_table = sql.Identifier(staging_table)
                    else:
                        cur.execute(
                            sql.SQL(
                                'DROP TABLE IF EXISTS {}.{} CASCADE'
                            ).format(
                                sql.Identifier(schema),
                                sql.Identifier(staging_table),
                            )
                        )
                        cur.execute(
                            sql.SQL(
                                """
                                CREATE TABLE {}.{} (
                                  LIKE {}.{}
                                    INCLUDING DEFAULTS
                                    INCLUDING GENERATED
                                    INCLUDING CONSTRAINTS
                                )
                                """
                            ).format(
                                sql.Identifier(schema),
                                sql.Identifier(staging_table),
                                sql.Identifier(schema),
                                sql.Identifier(table),
                            )
                        )
                        target_table = sql.Identifier(staging_table)
                else:
                    should_attach = False
                with csv_path.open('r', encoding='utf-8') as csv_file:
                    cur.copy_expert(
                        sql.SQL(
                            """
                            COPY {}.{} ({})
                            FROM STDIN WITH (
                              FORMAT CSV,
                              NULL '\\N'
                            )
                            """
                        ).format(
                            sql.Identifier(schema),
                            target_table,
                            column_sql,
                        ),
                        csv_file,
                    )
                if attach_source_partition and should_attach:
                    cur.execute(
                        sql.SQL(
                            """
                            ALTER TABLE {}.{}
                            ATTACH PARTITION {}.{}
                            FOR VALUES IN ({})
                            """
                        ).format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                            sql.Identifier(schema),
                            sql.Identifier(staging_table),
                            sql.Literal(source_id),
                        )
                    )


def _copy_source_partition(
    con: duckdb.DuckDBPyConnection,
    *,
    database_url: str,
    schema: str,
    table: str,
    columns: tuple[str, ...],
    query: str,
    source_id: int,
) -> None:
    _copy_duckdb_query_to_postgres(
        con,
        database_url=database_url,
        schema=schema,
        table=table,
        columns=columns,
        query=query,
        attach_source_partition=True,
        source_id=source_id,
    )


def _duckdb_source_id(
    con: duckdb.DuckDBPyConnection,
    source: str | None = None,
) -> int:
    if source is None:
        rows = con.execute(
            """
            WITH current_source AS (
              SELECT source FROM pq_entity_evidence WHERE source IS NOT NULL
              UNION
              SELECT source FROM pq_entity_evidence_resolution WHERE source IS NOT NULL
              UNION
              SELECT source FROM pq_entity_identifier_resolved WHERE source IS NOT NULL
              UNION
              SELECT source FROM pq_relation_evidence WHERE source IS NOT NULL
              UNION
              SELECT source FROM pq_annotation_relation_evidence WHERE source IS NOT NULL
              UNION
              SELECT source FROM pq_ontology_terms WHERE source IS NOT NULL
              UNION
              SELECT source FROM pq_entity_ontology_relation WHERE source IS NOT NULL
            )
            SELECT ds.source_id, ds.name
            FROM current_source s
            JOIN load_data_source ds
              ON ds.name = s.source
            ORDER BY ds.source_id
            """
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(
                'Expected exactly one source for source-partition COPY load; '
                f'found {len(rows)}: {rows!r}'
            )
        return int(rows[0][0])
    row = con.execute(
        'SELECT source_id FROM load_data_source WHERE name = ?',
        [source],
    ).fetchone()
    if row is None:
        raise ValueError(f'No source_id found for {source!r}')
    return int(row[0])


def _bulk_copy_evidence(
    con: duckdb.DuckDBPyConnection,
    *,
    schema: str,
    database_url: str,
) -> None:
    source_id = _duckdb_source_id(con)
    existing_identifier_evidence = _duckdb_pg_table(
        schema,
        'identifier_evidence',
    )
    _copy_duckdb_query_to_postgres(
        con,
        database_url=database_url,
        schema=schema,
        table='identifier_evidence',
        columns=('identifier_id', 'identifier_type_id', 'value'),
        query="""
          SELECT DISTINCT
            i.identifier_id::UUID,
            it.identifier_type_id,
            i.identifier
          FROM pq_entity_identifier i
          JOIN load_vocab_identifier_type it
            ON it.name = i.identifier_type
          LEFT JOIN {existing_identifier_evidence} existing
            ON existing.identifier_type_id = it.identifier_type_id
           AND existing.value = i.identifier
          WHERE i.identifier_id IS NOT NULL
            AND i.identifier IS NOT NULL
            AND existing.identifier_id IS NULL
        """.format(
            existing_identifier_evidence=existing_identifier_evidence,
        ),
    )
    existing_annotation = _duckdb_pg_table(schema, 'annotation')
    _copy_duckdb_query_to_postgres(
        con,
        database_url=database_url,
        schema=schema,
        table='annotation',
        columns=('annotation_key', 'term', 'value', 'unit'),
        query="""
          SELECT DISTINCT
            pq_annotation.annotation_key::UUID,
            pq_annotation.term,
            pq_annotation.value,
            pq_annotation.unit
          FROM pq_annotation
          LEFT JOIN {existing_annotation} existing
            ON existing.annotation_key = pq_annotation.annotation_key::UUID
          WHERE pq_annotation.term IS NOT NULL
            AND existing.annotation_key IS NULL
        """.format(existing_annotation=existing_annotation),
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='entity_evidence',
        columns=(
            'source_id',
            'entity_evidence_id',
            'dataset_id',
            'row_id',
            'parent_entity_evidence_id',
            'entity_role_id',
            'entity_type_id',
            'taxonomy_id',
        ),
        query="""
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
          JOIN load_data_source ds
            ON ds.name = e.source
          JOIN load_dataset d
            ON d.source_id = ds.source_id
           AND d.name = e.dataset
          JOIN load_vocab_entity_role er
            ON er.name = e.entity_role
          LEFT JOIN load_vocab_entity_type et
            ON et.name = e.entity_type
        """,
        source_id=source_id,
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='entity_evidence_identifier',
        columns=('source_id', 'entity_evidence_id', 'identifier_id'),
        query=f"""
          SELECT DISTINCT
            ds.source_id,
            i.entity_evidence_id::UUID,
            coalesce(existing.identifier_id, i.identifier_id::UUID) AS identifier_id
          FROM pq_entity_identifier i
          JOIN load_data_source ds
            ON ds.name = i.source
          JOIN load_vocab_identifier_type it
            ON it.name = i.identifier_type
          LEFT JOIN {existing_identifier_evidence} existing
            ON existing.identifier_type_id = it.identifier_type_id
           AND existing.value = i.identifier
          WHERE i.identifier_id IS NOT NULL
        """,
        source_id=source_id,
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='entity_evidence_annotation',
        columns=('source_id', 'entity_evidence_id', 'annotation_key'),
        query="""
          SELECT DISTINCT
            ds.source_id,
            a.evidence_id::UUID,
            a.annotation_key::UUID
          FROM pq_entity_annotation a
          JOIN load_data_source ds
            ON ds.name = a.source
        """,
        source_id=source_id,
    )


def _bulk_copy_canonical(
    con: duckdb.DuckDBPyConnection,
    *,
    schema: str,
    database_url: str,
) -> None:
    source_id = _duckdb_source_id(con)
    _copy_duckdb_query_to_postgres(
        con,
        database_url=database_url,
        schema=schema,
        table='entity',
        columns=(
            'entity_id',
            'entity_type_id',
            'taxonomy_id',
            'canonical_identifier_type_id',
            'canonical_identifier',
            'resolution_status_id',
        ),
        query="""
          SELECT
            e.entity_id,
            et.entity_type_id,
            NULLIF(e.taxonomy_id, '')::BIGINT,
            it.identifier_type_id,
            e.canonical_identifier,
            rs.resolution_status_id
          FROM pq_entity e
          JOIN load_vocab_entity_type et
            ON et.name = e.entity_type
          JOIN load_vocab_identifier_type it
            ON it.name = e.canonical_identifier_type
          JOIN load_vocab_resolution_status rs
            ON rs.name = e.resolution_status
          LEFT JOIN {existing_entity} existing
            ON existing.entity_id = e.entity_id
          WHERE existing.entity_id IS NULL
        """.format(existing_entity=_duckdb_pg_table(schema, 'entity')),
    )
    existing_identifier_evidence = _duckdb_pg_table(
        schema,
        'identifier_evidence',
    )
    _copy_duckdb_query_to_postgres(
        con,
        database_url=database_url,
        schema=schema,
        table='identifier_evidence',
        columns=('identifier_id', 'identifier_type_id', 'value'),
        query=f"""
          SELECT DISTINCT
            content_uuid(
              'identifier' || chr(31) ||
              i.identifier_type || chr(31) ||
              i.identifier
            ) AS identifier_id,
            it.identifier_type_id,
            i.identifier
          FROM pq_entity_identifier_resolved i
          JOIN load_vocab_identifier_type it
            ON it.name = i.identifier_type
          LEFT JOIN {existing_identifier_evidence} existing
            ON existing.identifier_type_id = it.identifier_type_id
           AND existing.value = i.identifier
          WHERE i.identifier IS NOT NULL
            AND i.identifier <> ''
            AND existing.identifier_id IS NULL
        """,
    )
    existing_entity_identifier = _duckdb_pg_table(
        schema,
        'entity_identifier',
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='entity_identifier',
        columns=('source_id', 'entity_id', 'identifier_id'),
        query=f"""
          SELECT candidate.*
          FROM (
            SELECT DISTINCT
              ds.source_id,
              i.entity_id::UUID AS entity_id,
              coalesce(
                ie.identifier_id,
                content_uuid(
                  'identifier' || chr(31) ||
                  i.identifier_type || chr(31) ||
                  i.identifier
                )
              ) AS identifier_id
            FROM pq_entity_identifier_resolved i
            JOIN load_data_source ds
              ON ds.name = i.source
            JOIN load_vocab_identifier_type it
              ON it.name = i.identifier_type
            LEFT JOIN {existing_identifier_evidence} ie
              ON ie.identifier_type_id = it.identifier_type_id
             AND ie.value = i.identifier
            WHERE i.identifier IS NOT NULL
              AND i.identifier <> ''
          ) candidate
          LEFT JOIN {existing_entity_identifier} existing
            ON existing.source_id = candidate.source_id
           AND existing.entity_id = candidate.entity_id
           AND existing.identifier_id = candidate.identifier_id
          WHERE existing.source_id IS NULL
        """,
        source_id=source_id,
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='ontology_terms',
        columns=(
            'source_id',
            'term_entity_id',
            'term_id',
            'ontology_prefix',
            'label',
            'definition',
            'ontology_id',
            'synonyms',
            'synonyms_text',
            'sources',
        ),
        query="""
          SELECT
            ds.source_id,
            ce.entity_id,
            ot.term_id,
            ot.ontology_prefix,
            ot.label,
            ot.definition,
            ot.ontology_id,
            COALESCE(
              '{{' || array_to_string(
                list_transform(
                  ot.synonyms,
                  x -> chr(34)
                    || replace(
                         replace(x, chr(92), chr(92) || chr(92)),
                         chr(34),
                         chr(92) || chr(34)
                       )
                    || chr(34)
                ),
                ','
              ) || '}}',
              '{{}}'
            ),
            COALESCE(ot.synonyms_text, ''),
            COALESCE(
              '{{' || array_to_string(
                list_transform(
                  ot.sources,
                  x -> chr(34)
                    || replace(
                         replace(x, chr(92), chr(92) || chr(92)),
                         chr(34),
                         chr(92) || chr(34)
                       )
                    || chr(34)
                ),
                ','
              ) || '}}',
              '{{}}'
            )
          FROM pq_ontology_terms ot
          JOIN load_data_source ds
            ON ds.name = ot.source
          JOIN ontology_term_resolution otr
            ON otr.source = ot.source
           AND otr.ontology_id = ot.ontology_id
           AND otr.term_id = ot.term_id
          JOIN canonical_entity ce
            ON ce.entity_type = otr.entity_type
           AND ce.taxonomy_id IS NOT DISTINCT FROM otr.taxonomy_id
           AND ce.canonical_identifier_type_id =
               otr.canonical_identifier_type_id
           AND ce.canonical_identifier = otr.canonical_identifier
          WHERE ot.term_id IS NOT NULL
        """,
        source_id=source_id,
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='entity_ontology_relation',
        columns=(
            'source_id',
            'subject_entity_id',
            'predicate_id',
            'object_entity_id',
            'ontology_id',
        ),
        query="""
          SELECT candidate.*
          FROM (
            SELECT DISTINCT
              ds.source_id,
              eor.subject_entity_id,
              rp.relation_predicate_id,
              eor.object_entity_id,
              eor.ontology_id
            FROM pq_entity_ontology_relation eor
            JOIN load_data_source ds
              ON ds.name = eor.source
            JOIN load_vocab_relation_predicate rp
              ON rp.name = eor.predicate
          ) candidate
          LEFT JOIN {existing_entity_ontology_relation} existing
            ON existing.source_id = candidate.source_id
           AND existing.subject_entity_id = candidate.subject_entity_id
           AND existing.predicate_id = candidate.relation_predicate_id
           AND existing.object_entity_id = candidate.object_entity_id
           AND existing.ontology_id = candidate.ontology_id
          WHERE existing.source_id IS NULL
        """.format(
            existing_entity_ontology_relation=_duckdb_pg_table(
                schema,
                'entity_ontology_relation',
            ),
        ),
        source_id=source_id,
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='relation_evidence',
        columns=(
            'source_id',
            'relation_evidence_id',
            'dataset_id',
            'row_id',
            'subject_entity_evidence_id',
            'subject_entity_id',
            'predicate_id',
            'object_entity_evidence_id',
            'object_entity_id',
            'relation_category_id',
        ),
        query="""
          SELECT DISTINCT *
          FROM (
            SELECT
              ds.source_id,
              r.relation_evidence_id::UUID,
              d.dataset_id,
              r.row_id,
              r.subject_entity_evidence_id::UUID,
              NULL::UUID AS subject_entity_id,
              rp.relation_predicate_id,
              r.object_entity_evidence_id::UUID,
              NULL::UUID AS object_entity_id,
              rc.relation_category_id
            FROM pq_relation_evidence r
            JOIN load_data_source ds
              ON ds.name = r.source
            JOIN load_dataset d
              ON d.source_id = ds.source_id
             AND d.name = r.dataset
            JOIN load_vocab_relation_predicate rp
              ON rp.name = r.predicate
            JOIN load_vocab_relation_category rc
              ON rc.name = r.relation_category
            UNION ALL
            SELECT
              ds.source_id,
              ar.relation_evidence_id::UUID,
              d.dataset_id,
              ar.row_id,
              NULL::UUID AS subject_entity_evidence_id,
              ar.object_entity_id AS subject_entity_id,
              rp.relation_predicate_id,
              ar.subject_entity_evidence_id::UUID AS object_entity_evidence_id,
              NULL::UUID AS object_entity_id,
              rc.relation_category_id
            FROM pq_annotation_relation_evidence_resolved ar
            JOIN load_data_source ds
              ON ds.name = ar.source
            JOIN load_dataset d
              ON d.source_id = ds.source_id
             AND d.name = ar.dataset
            JOIN load_vocab_relation_predicate rp
              ON rp.name = ar.predicate
            JOIN load_vocab_relation_category rc
              ON rc.name = ar.relation_category
          )
        """,
        source_id=source_id,
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='relation_evidence_annotation',
        columns=(
            'source_id',
            'relation_evidence_id',
            'annotation_key',
            'annotation_scope_id',
        ),
        query="""
          SELECT DISTINCT
            ds.source_id,
            a.evidence_id::UUID,
            a.annotation_key::UUID,
            sc.annotation_scope_id
          FROM pq_relation_annotation a
          JOIN load_data_source ds
            ON ds.name = a.source
          JOIN load_vocab_annotation_scope sc
            ON sc.name = coalesce(a.annotation_scope, 'relation')
        """,
        source_id=source_id,
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='entity_evidence_resolution',
        columns=(
            'source_id',
            'entity_evidence_id',
            'status_id',
            'entity_id',
            'reason_id',
            'resolved_at',
        ),
        query="""
          SELECT
            ds.source_id,
            er.entity_evidence_id::UUID,
            rs.resolution_status_id,
            er.entity_id,
            NULL::SMALLINT,
            now()
          FROM pq_entity_evidence_resolution er
          JOIN load_data_source ds
            ON ds.name = er.source
          JOIN load_vocab_resolution_status rs
            ON rs.name = er.status
          WHERE er.entity_id IS NOT NULL
        """,
        source_id=source_id,
    )
    _copy_duckdb_query_to_postgres(
        con,
        database_url=database_url,
        schema=schema,
        table='relation',
        columns=(
            'relation_id',
            'subject_entity_id',
            'predicate_id',
            'object_entity_id',
            'relation_category_id',
        ),
        query="""
          SELECT
            r.relation_id,
            r.subject_entity_id,
            rp.relation_predicate_id,
            r.object_entity_id,
            rc.relation_category_id
          FROM pq_relation r
          JOIN load_vocab_relation_predicate rp
            ON rp.name = r.predicate
          LEFT JOIN load_vocab_relation_category rc
            ON rc.name = r.relation_category
          LEFT JOIN {existing_relation} existing
            ON existing.relation_id = r.relation_id
          WHERE existing.relation_id IS NULL
        """.format(existing_relation=_duckdb_pg_table(schema, 'relation')),
    )
    _copy_source_partition(
        con,
        database_url=database_url,
        schema=schema,
        table='relation_evidence_relation',
        columns=('source_id', 'relation_id', 'relation_evidence_id'),
        query="""
          SELECT
            ds.source_id,
            rer.relation_id,
            rer.relation_evidence_id::UUID
          FROM pq_relation_evidence_relation rer
          JOIN load_data_source ds
            ON ds.name = rer.source
        """,
        source_id=source_id,
    )


def _reset_postgres_sequences(
    *,
    database_url: str,
    schema: str,
) -> None:
    sequence_tables = (
        ('data_source', 'source_id'),
        ('dataset', 'dataset_id'),
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


__all__ = [
    '_sql_literal',
    '_create_duckdb_content_uuid_macro',
    '_create_duckdb_resolver_views',
    '_create_duckdb_identifier_type_all_view',
    'DuckDBEvidenceProjector',
    '_ENTITY_EVIDENCE_SCHEMA',
    '_ENTITY_IDENTIFIER_SCHEMA',
    '_ANNOTATION_REF_SCHEMA',
    '_ANNOTATION_VALUE_SCHEMA',
    '_RELATION_EVIDENCE_SCHEMA',
    '_ANNOTATION_RELATION_EVIDENCE_SCHEMA',
    '_create_duckdb_evidence_tables',
    '_ensure_duckdb_canonical_caches',
    '_drop_duckdb_batch_tables',
    '_canonicalize_loaded_duckdb',
    '_bulk_load_create_views_from_loaded_tables',
    '_bulk_load_assert_empty',
    '_bulk_load_small_dimensions',
    '_bulk_load_materialize_dimensions',
    '_drop_bulk_load_constraints_and_indexes',
    '_copy_duckdb_query_to_postgres',
    '_copy_source_partition',
    '_duckdb_source_id',
    '_bulk_copy_evidence',
    '_bulk_copy_canonical',
    '_reset_postgres_sequences',
]
