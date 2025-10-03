#!/usr/bin/env python3
"""Gold parquet builder that ingests natural keys then resolves FKs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import duckdb

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColumnDef:
    """Declarative representation of a table column."""

    name: str
    sql_type: str
    constraints: tuple[str, ...] = ()
    default: str | None = None
    temporary: bool = False

    def create_sql(self) -> str:
        parts = [self.name, self.sql_type]
        parts.extend(self.constraints)
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")
        return ' '.join(parts)

    def add_column_sql(self) -> str:
        parts = [self.name, self.sql_type]
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")
        return ' '.join(parts)


@dataclass(frozen=True)
class TableDef:
    """Declarative table definition including natural-key metadata."""

    name: str
    columns: tuple[ColumnDef, ...]
    primary_key: str | None
    natural_keys: tuple[tuple[str, ...], ...]
    export_columns: tuple[str, ...]
    order_by: tuple[str, ...] | None = None

    @property
    def temporary_columns(self) -> tuple[str, ...]:
        return tuple(col.name for col in self.columns if col.temporary)


@dataclass(frozen=True)
class ForeignKeyRule:
    """Rule describing how to resolve a single foreign key."""

    table: str
    target_column: str
    natural_column: str
    lookup_table: str
    lookup_column: str
    extra_condition: str | None = None


GOLD_TABLE_DEFINITIONS: dict[str, TableDef] = {
    'cv_namespace': TableDef(
        name='cv_namespace',
        columns=(
            ColumnDef('id', 'INTEGER', ('PRIMARY KEY',)),
            ColumnDef('name', 'VARCHAR(255)', ('NOT NULL', 'UNIQUE')),
        ),
        primary_key='id',
        natural_keys=(('name',),),
        export_columns=('id', 'name'),
        order_by=('id',),
    ),
    'cv_term': TableDef(
        name='cv_term',
        columns=(
            ColumnDef('id', 'INTEGER', ('PRIMARY KEY',)),
            ColumnDef('namespace_id', 'INTEGER'),
            ColumnDef('namespace_name', 'VARCHAR(255)', temporary=True),
            ColumnDef('accession', 'VARCHAR(100)'),
            ColumnDef('name', 'VARCHAR(255)', ('NOT NULL',)),
            ColumnDef('description', 'TEXT'),
            ColumnDef('is_obsolete', 'BOOLEAN', default='FALSE'),
            ColumnDef('replaces', 'INTEGER'),
            ColumnDef('replaced_by', 'INTEGER'),
        ),
        primary_key='id',
        natural_keys=(('namespace_name', 'name'),),
        export_columns=(
            'id',
            'namespace_id',
            'accession',
            'name',
            'description',
            'is_obsolete',
            'replaces',
            'replaced_by',
        ),
        order_by=('id',),
    ),
    'source': TableDef(
        name='source',
        columns=(
            ColumnDef('id', 'INTEGER', ('PRIMARY KEY',)),
            ColumnDef('name', 'VARCHAR(255)', ('NOT NULL', 'UNIQUE')),
            ColumnDef('url', 'VARCHAR(500)'),
            ColumnDef('description', 'TEXT'),
            ColumnDef('created_at', 'TIMESTAMP', default='NOW()'),
        ),
        primary_key='id',
        natural_keys=(('name',),),
        export_columns=('id', 'name', 'url', 'description', 'created_at'),
        order_by=('id',),
    ),
    'reference': TableDef(
        name='reference',
        columns=(
            ColumnDef('id', 'BIGINT', ('PRIMARY KEY',)),
            ColumnDef('type_id', 'INTEGER'),
            ColumnDef('value', 'TEXT', ('NOT NULL', 'UNIQUE')),
            ColumnDef('citation', 'TEXT'),
            ColumnDef('year', 'INTEGER'),
            ColumnDef('journal', 'TEXT'),
            ColumnDef('title', 'TEXT'),
        ),
        primary_key='id',
        natural_keys=(('value',),),
        export_columns=('id', 'type_id', 'value', 'citation', 'year', 'journal', 'title'),
        order_by=('id',),
    ),
    'provenance': TableDef(
        name='provenance',
        columns=(
            ColumnDef('id', 'BIGINT', ('PRIMARY KEY',)),
            ColumnDef('source_id', 'INTEGER'),
            ColumnDef('source_name', 'VARCHAR(255)', temporary=True),
            ColumnDef('primary_source_id', 'INTEGER'),
            ColumnDef('reference_id', 'BIGINT'),
            ColumnDef('created_at', 'TIMESTAMP', default='NOW()'),
        ),
        primary_key='id',
        natural_keys=(('source_name',),),
        export_columns=('id', 'source_id', 'primary_source_id', 'reference_id', 'created_at'),
        order_by=('id',),
    ),
    'entity': TableDef(
        name='entity',
        columns=(
            ColumnDef('id', 'BIGINT', ('PRIMARY KEY',)),
            ColumnDef('cv_term_id', 'INTEGER'),
            ColumnDef('entity_type_name', 'VARCHAR(255)', temporary=True),
            ColumnDef('canonical_id', 'VARCHAR'),
            ColumnDef('canonical_identifier_type_name', 'VARCHAR(255)', temporary=True),
            ColumnDef('source_name', 'VARCHAR(255)', temporary=True),
            ColumnDef('created_at', 'TIMESTAMP', default='NOW()'),
        ),
        primary_key='id',
        natural_keys=(('canonical_id',),),
        export_columns=('id', 'cv_term_id', 'created_at'),
        order_by=('id',),
    ),
    'entity_identifier': TableDef(
        name='entity_identifier',
        columns=(
            ColumnDef('id', 'BIGINT', ('PRIMARY KEY',)),
            ColumnDef('entity_id', 'BIGINT'),
            ColumnDef('entity_canonical_id', 'VARCHAR', temporary=True),
            ColumnDef('cv_term_id', 'INTEGER'),
            ColumnDef('identifier_type_name', 'VARCHAR(255)', temporary=True),
            ColumnDef('identifier', 'TEXT', ('NOT NULL',)),
            ColumnDef('provenance_id', 'BIGINT'),
            ColumnDef('source_name', 'VARCHAR(255)', temporary=True),
            ColumnDef('is_canonical', 'BOOLEAN'),
            ColumnDef('created_at', 'TIMESTAMP', default='NOW()'),
        ),
        primary_key='id',
        natural_keys=(
            ('entity_canonical_id', 'identifier', 'identifier_type_name', 'is_canonical'),
        ),
        export_columns=('id', 'entity_id', 'cv_term_id', 'identifier', 'provenance_id', 'created_at'),
        order_by=('id',),
    ),
    'compound': TableDef(
        name='compound',
        columns=(
            ColumnDef('entity_id', 'BIGINT'),
            ColumnDef('entity_canonical_id', 'VARCHAR', temporary=True),
            ColumnDef('formula', 'VARCHAR(255)'),
            ColumnDef('molecular_weight', 'DOUBLE'),
            ColumnDef('exact_mass', 'DOUBLE'),
            ColumnDef('tpsa', 'DOUBLE'),
            ColumnDef('logp', 'DOUBLE'),
            ColumnDef('hbd', 'INTEGER'),
            ColumnDef('hba', 'INTEGER'),
            ColumnDef('rotatable_bonds', 'INTEGER'),
            ColumnDef('aromatic_rings', 'INTEGER'),
            ColumnDef('heavy_atoms', 'INTEGER'),
        ),
        primary_key=None,
        natural_keys=(('entity_canonical_id',),),
        export_columns=(
            'entity_id',
            'formula',
            'molecular_weight',
            'exact_mass',
            'tpsa',
            'logp',
            'hbd',
            'hba',
            'rotatable_bonds',
            'aromatic_rings',
            'heavy_atoms',
        ),
        order_by=('entity_id',),
    ),
}


GOLD_ID_COLUMN_MAP: dict[str, tuple[str, str]] = {
    name: (table.name, table.primary_key)
    for name, table in GOLD_TABLE_DEFINITIONS.items()
    if table.primary_key is not None
}


class GoldParquetBuilderV2:
    """Builds relational gold parquet files using a declarative schema."""

    TABLE_DEFINITIONS: dict[str, TableDef] = GOLD_TABLE_DEFINITIONS
    TABLES: tuple[str, ...] = tuple(GOLD_TABLE_DEFINITIONS)
    ID_COLUMN_MAP: dict[str, tuple[str, str]] = GOLD_ID_COLUMN_MAP

    FK_RULES: tuple[ForeignKeyRule, ...] = (
        ForeignKeyRule(
            table='cv_term',
            target_column='namespace_id',
            natural_column='namespace_name',
            lookup_table='cv_namespace',
            lookup_column='name',
        ),
        ForeignKeyRule(
            table='provenance',
            target_column='source_id',
            natural_column='source_name',
            lookup_table='source',
            lookup_column='name',
        ),
        ForeignKeyRule(
            table='entity',
            target_column='cv_term_id',
            natural_column='entity_type_name',
            lookup_table='cv_term',
            lookup_column='name',
            extra_condition="lookup.namespace_id = (SELECT id FROM cv_namespace WHERE name = 'entity_type')",
        ),
        ForeignKeyRule(
            table='entity_identifier',
            target_column='entity_id',
            natural_column='entity_canonical_id',
            lookup_table='entity',
            lookup_column='canonical_id',
        ),
        ForeignKeyRule(
            table='entity_identifier',
            target_column='cv_term_id',
            natural_column='identifier_type_name',
            lookup_table='cv_term',
            lookup_column='name',
            extra_condition="lookup.namespace_id = (SELECT id FROM cv_namespace WHERE name = 'identifier_type')",
        ),
        ForeignKeyRule(
            table='entity_identifier',
            target_column='provenance_id',
            natural_column='source_name',
            lookup_table='provenance',
            lookup_column='source_name',
        ),
        ForeignKeyRule(
            table='compound',
            target_column='entity_id',
            natural_column='entity_canonical_id',
            lookup_table='entity',
            lookup_column='canonical_id',
        ),
    )

    def __init__(self, source_name: str, output_dir: Path):
        self.source_name = source_name
        self.output_dir = output_dir
        self.conn = duckdb.connect(':memory:')

        self._create_gold_schema()
        self._init_id_counters()

        logger.info("GoldParquetBuilderV2 initialised for %s", source_name)

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------
    def __enter__(self) -> 'GoldParquetBuilderV2':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.conn:
            self.conn.close()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------
    def _create_gold_schema(self) -> None:
        """Create gold tables from declarative definitions and backfill columns."""

        for table in self.TABLE_DEFINITIONS.values():
            columns_sql = ',\n                '.join(col.create_sql() for col in table.columns)
            self.conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table.name} (
                    {columns_sql}
                )
                """
            )

            existing = {
                row[1]
                for row in self.conn.execute(f"PRAGMA table_info('{table.name}')").fetchall()
            }

            for column in table.columns:
                if column.name in existing:
                    continue
                self.conn.execute(
                    f"ALTER TABLE {table.name} ADD COLUMN {column.add_column_sql()}"
                )


    # ------------------------------------------------------------------
    # ID counters
    # ------------------------------------------------------------------
    def _init_id_counters(self) -> None:
        self._id_counters: dict[str, int] = {}
        for key, (table, column) in self.ID_COLUMN_MAP.items():
            max_id = self.conn.execute(
                f"SELECT COALESCE(MAX({column}), 0) FROM {table}"
            ).fetchone()[0]
            self._id_counters[key] = max_id + 1

    def _reserve_ids(self, key: str, count: int) -> int:
        start = self._id_counters.get(key, 1)
        self._id_counters[key] = start + count
        return start

    def _resolve_source(self, override: str | None) -> str:
        return override or self.source_name

    def _insert_new_rows(
        self,
        table_name: str,
        select_sql: str,
        insert_columns: Sequence[str],
        natural_key: Sequence[str] | None = None,
        order_by: Sequence[str] | None = None,
        params: Sequence[Any] | None = None,
    ) -> int:
        """Insert new rows into ``table_name`` from a parametrised SELECT."""

        table_def = self.TABLE_DEFINITIONS[table_name]
        if natural_key is None:
            natural_key = table_def.natural_keys[0] if table_def.natural_keys else tuple(insert_columns)
        if order_by is None:
            order_by = natural_key

        params = list(params or [])
        temp_table = f"pending_{table_name}"
        dedup_table = f"{temp_table}_dedup"

        self.conn.execute(
            f"CREATE OR REPLACE TEMP TABLE {temp_table} AS {select_sql}",
            params,
        )
        self.conn.execute(
            f"CREATE OR REPLACE TEMP TABLE {dedup_table} AS\n"
            f"SELECT DISTINCT {', '.join(insert_columns)} FROM {temp_table}"
        )

        condition = ' AND '.join(
            f"existing.{col} IS NOT DISTINCT FROM pending.{col}"
            for col in natural_key
        )
        check_column = table_def.primary_key or natural_key[0]

        try:
            new_count = self.conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {dedup_table} AS pending
                LEFT JOIN {table_name} AS existing
                    ON {condition}
                WHERE existing.{check_column} IS NULL
                """
            ).fetchone()[0]

            if not new_count:
                return 0

            pending_projection = ', '.join(f'pending.{col}' for col in insert_columns)
            if table_def.primary_key:
                start_id = self._reserve_ids(table_name, new_count)
                order_clause = ', '.join(f'pending.{col}' for col in order_by)
                id_expr = f"{start_id} + ROW_NUMBER() OVER (ORDER BY {order_clause}) - 1"
                target_columns = [table_def.primary_key, *insert_columns]
                self.conn.execute(
                    f"""
                    INSERT INTO {table_name} ({', '.join(target_columns)})
                    SELECT
                        {id_expr},
                        {pending_projection}
                    FROM {dedup_table} AS pending
                    LEFT JOIN {table_name} AS existing
                        ON {condition}
                    WHERE existing.{check_column} IS NULL
                    """
                )
            else:
                self.conn.execute(
                    f"""
                    INSERT INTO {table_name} ({', '.join(insert_columns)})
                    SELECT {pending_projection}
                    FROM {dedup_table} AS pending
                    LEFT JOIN {table_name} AS existing
                        ON {condition}
                    WHERE existing.{check_column} IS NULL
                    """
                )

            return new_count
        finally:
            self.conn.execute(f"DROP TABLE IF EXISTS {dedup_table}")
            self.conn.execute(f"DROP TABLE IF EXISTS {temp_table}")

    # ------------------------------------------------------------------
    # Stage population
    # ------------------------------------------------------------------
    def build_cv_terms(
        self,
        silver_parquet_path: Path,
        source_override: str | None = None,
    ) -> dict[str, int]:
        logger.info("Ingesting CV terms from %s", silver_parquet_path.name)

        namespaces = self._insert_new_rows(
            'cv_namespace',
            """
            SELECT DISTINCT namespace AS name
            FROM read_parquet(?)
            WHERE namespace IS NOT NULL
            """,
            insert_columns=('name',),
            order_by=('name',),
            params=[str(silver_parquet_path)],
        )

        terms = self._insert_new_rows(
            'cv_term',
            """
            SELECT DISTINCT
                namespace AS namespace_name,
                term_accession AS accession,
                term_name AS name,
                term_definition AS description,
                FALSE AS is_obsolete,
                NULL AS replaces,
                NULL AS replaced_by
            FROM read_parquet(?)
            WHERE namespace IS NOT NULL AND term_name IS NOT NULL
            """,
            insert_columns=(
                'namespace_name',
                'accession',
                'name',
                'description',
                'is_obsolete',
                'replaces',
                'replaced_by',
            ),
            order_by=('namespace_name', 'name'),
            params=[str(silver_parquet_path)],
        )

        return {'inserted_namespaces': namespaces, 'inserted_terms': terms}

    def build_entities(
        self,
        silver_parquet_path: Path,
        source_override: str | None = None,
    ) -> dict[str, int]:
        logger.info("Ingesting entities from %s", silver_parquet_path.name)

        source_name = self._resolve_source(source_override)

        available_columns = {
            col[0]
            for col in self.conn.execute(
                "SELECT * FROM read_parquet(?) LIMIT 0",
                [str(silver_parquet_path)],
            ).description
        }

        def column_or_null(column: str) -> str:
            return column if column in available_columns else f"NULL AS {column}"

        canonical_expr = (
            "CASE\n                WHEN compound_inchi IS NOT NULL AND TRIM(compound_inchi) <> '' THEN compound_inchi\n"
            "                ELSE identifier\n             END AS canonical_id"
            if 'compound_inchi' in available_columns
            else "identifier AS canonical_id"
        )

        canonical_type_expr = (
            "CASE\n                WHEN compound_inchi IS NOT NULL AND TRIM(compound_inchi) <> '' THEN 'inchi'\n"
            "                ELSE COALESCE(identifier_type, 'unknown')\n             END AS canonical_identifier_type"
        )

        where_clause = "WHERE is_valid = TRUE" if 'is_valid' in available_columns else ""

        base_select = f"""
            SELECT DISTINCT
                {column_or_null('entity_type')},
                {column_or_null('identifier')},
                {column_or_null('identifier_type')},
                {column_or_null('compound_formula')},
                {column_or_null('molecular_weight')},
                {column_or_null('exact_mass')},
                {column_or_null('tpsa')},
                {column_or_null('logp')},
                {column_or_null('hbd')},
                {column_or_null('hba')},
                {column_or_null('rotatable_bonds')},
                {column_or_null('aromatic_rings')},
                {column_or_null('heavy_atoms')},
                {column_or_null('compound_inchi')},
                {canonical_expr},
                {canonical_type_expr}
            FROM read_parquet(?)
            {where_clause}
        """

        stats: dict[str, int] = {}

        stats['inserted_namespaces'] = self._insert_new_rows(
            'cv_namespace',
            "SELECT name FROM (VALUES ('entity_type'), ('identifier_type')) AS t(name)",
            insert_columns=('name',),
            order_by=('name',),
        )

        entity_type_terms = self._insert_new_rows(
            'cv_term',
            f"""
            WITH entity_data AS ({base_select})
            SELECT DISTINCT
                'entity_type' AS namespace_name,
                NULL AS accession,
                entity_type AS name,
                NULL AS description,
                FALSE AS is_obsolete,
                NULL AS replaces,
                NULL AS replaced_by
            FROM entity_data
            WHERE entity_type IS NOT NULL
            """,
            insert_columns=(
                'namespace_name',
                'accession',
                'name',
                'description',
                'is_obsolete',
                'replaces',
                'replaced_by',
            ),
            order_by=('namespace_name', 'name'),
            params=[str(silver_parquet_path)],
        )

        identifier_terms = self._insert_new_rows(
            'cv_term',
            f"""
            WITH entity_data AS ({base_select})
            SELECT DISTINCT
                'identifier_type' AS namespace_name,
                NULL AS accession,
                identifier_type AS name,
                NULL AS description,
                FALSE AS is_obsolete,
                NULL AS replaces,
                NULL AS replaced_by
            FROM entity_data
            WHERE identifier_type IS NOT NULL
            UNION ALL
            SELECT DISTINCT
                'identifier_type',
                NULL,
                canonical_identifier_type,
                NULL,
                FALSE,
                NULL,
                NULL
            FROM entity_data
            WHERE canonical_identifier_type IS NOT NULL
            """,
            insert_columns=(
                'namespace_name',
                'accession',
                'name',
                'description',
                'is_obsolete',
                'replaces',
                'replaced_by',
            ),
            order_by=('namespace_name', 'name'),
            params=[str(silver_parquet_path)],
        )

        stats['inserted_cv_terms'] = entity_type_terms + identifier_terms

        stats['inserted_sources'] = self._insert_new_rows(
            'source',
            "SELECT ? AS name, NULL AS url, NULL AS description",
            insert_columns=('name', 'url', 'description'),
            params=[source_name],
        )

        stats['inserted_provenance'] = self._insert_new_rows(
            'provenance',
            "SELECT ? AS source_name, NULL AS primary_source_id, NULL AS reference_id",
            insert_columns=('source_name', 'primary_source_id', 'reference_id'),
            params=[source_name],
        )

        stats['inserted_entities'] = self._insert_new_rows(
            'entity',
            f"""
            WITH entity_data AS ({base_select})
            SELECT DISTINCT
                NULL AS cv_term_id,
                entity_type AS entity_type_name,
                canonical_id,
                canonical_identifier_type AS canonical_identifier_type_name,
                ? AS source_name
            FROM entity_data
            WHERE canonical_id IS NOT NULL
            """,
            insert_columns=(
                'cv_term_id',
                'entity_type_name',
                'canonical_id',
                'canonical_identifier_type_name',
                'source_name',
            ),
            order_by=('canonical_id',),
            params=[str(silver_parquet_path), source_name],
        )

        stats['inserted_identifiers'] = self._insert_new_rows(
            'entity_identifier',
            f"""
            WITH entity_data AS ({base_select})
            SELECT DISTINCT
                NULL AS entity_id,
                canonical_id AS entity_canonical_id,
                NULL AS cv_term_id,
                canonical_identifier_type AS identifier_type_name,
                canonical_id AS identifier,
                NULL AS provenance_id,
                ? AS source_name,
                TRUE AS is_canonical
            FROM entity_data
            WHERE canonical_id IS NOT NULL AND canonical_identifier_type IS NOT NULL
            UNION ALL
            SELECT DISTINCT
                NULL,
                canonical_id,
                NULL,
                identifier_type,
                identifier,
                NULL,
                ?,
                FALSE
            FROM entity_data
            WHERE canonical_id IS NOT NULL
              AND identifier IS NOT NULL
              AND identifier <> canonical_id
            """,
            insert_columns=(
                'entity_id',
                'entity_canonical_id',
                'cv_term_id',
                'identifier_type_name',
                'identifier',
                'provenance_id',
                'source_name',
                'is_canonical',
            ),
            order_by=('entity_canonical_id', 'identifier'),
            params=[str(silver_parquet_path), source_name, source_name],
        )

        stats['inserted_compounds'] = self._insert_new_rows(
            'compound',
            f"""
            WITH entity_data AS ({base_select})
            SELECT DISTINCT
                NULL AS entity_id,
                canonical_id AS entity_canonical_id,
                compound_formula AS formula,
                molecular_weight,
                exact_mass,
                tpsa,
                logp,
                hbd,
                hba,
                rotatable_bonds,
                aromatic_rings,
                heavy_atoms
            FROM entity_data
            WHERE canonical_id IS NOT NULL
            """,
            insert_columns=(
                'entity_id',
                'entity_canonical_id',
                'formula',
                'molecular_weight',
                'exact_mass',
                'tpsa',
                'logp',
                'hbd',
                'hba',
                'rotatable_bonds',
                'aromatic_rings',
                'heavy_atoms',
            ),
            order_by=('entity_canonical_id',),
            params=[str(silver_parquet_path)],
        )

        return stats

    def ingest_silver_directory(
        self,
        silver_dir: Path,
        source_filter: str | None = None,
    ) -> dict[str, dict[str, int]]:
        """Bulk ingest all recognised silver parquet files in a directory.

        Args:
            silver_dir: Directory containing silver parquet files.
            source_filter: Optional source name; when provided only files whose
                leading token matches this source are processed.

        Returns:
            Mapping of ``{file_path: stats_dict}`` with per-file staging counts.
        """

        silver_dir = Path(silver_dir)
        if not silver_dir.exists():
            raise FileNotFoundError(f"Silver directory not found: {silver_dir}")

        results: dict[str, dict[str, int]] = {}

        for parquet_path in sorted(silver_dir.glob('*.parquet')):
            stem = parquet_path.stem
            if stem.endswith('silver_entities'):
                suffix = '_silver_entities'
                target = 'entities'
            elif stem.endswith('silver_cv_terms'):
                suffix = '_silver_cv_terms'
                target = 'cv_terms'
            else:
                continue

            core = stem[: -len(suffix)]
            source_token = core.split('_')[0] if core else None
            if not source_token:
                continue

            if source_filter and source_token != source_filter:
                continue

            if target == 'entities':
                stats = self.build_entities(parquet_path, source_override=source_token)
            else:
                stats = self.build_cv_terms(parquet_path, source_override=source_token)

            results[parquet_path.name] = stats

        if source_filter and not results:
            logger.warning(
                "No silver files ingested for source %s in %s",
                source_filter,
                silver_dir,
            )

        return results

    # ------------------------------------------------------------------
    # Foreign key resolution
    # ------------------------------------------------------------------
    def resolve_foreign_keys(self) -> None:
        for rule in self.FK_RULES:
            condition = (
                f"lookup.{rule.lookup_column} = target.{rule.natural_column}"
            )
            if rule.extra_condition:
                condition += f" AND {rule.extra_condition}"

            self.conn.execute(
                f"""
                UPDATE {rule.table} AS target
                SET {rule.target_column} = lookup.id
                FROM {rule.lookup_table} AS lookup
                WHERE target.{rule.target_column} IS NULL
                  AND target.{rule.natural_column} IS NOT NULL
                  AND {condition}
                """
            )

    def _drop_temporary_columns(self) -> None:
        for table in self.TABLE_DEFINITIONS.values():
            for column in table.temporary_columns:
                self.conn.execute(
                    f"ALTER TABLE {table.name} DROP COLUMN IF EXISTS {column}"
                )

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------
    def export_all_tables(self) -> dict[str, Path]:
        logger.info("Resolving foreign keys via dictionary rules")
        self.resolve_foreign_keys()

        logger.info("Dropping temporary natural key columns before export")
        self._drop_temporary_columns()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        exported: dict[str, Path] = {}

        for table in self.TABLES:
            select_sql = self._export_select_sql(table)
            if select_sql is None:
                continue

            count = self.conn.execute(
                f"SELECT COUNT(*) FROM ({select_sql})"
            ).fetchone()[0]
            if count == 0:
                continue

            parquet_path = self.output_dir / f"{table}.parquet"
            self.conn.execute(
                f"COPY ({select_sql}) TO '{parquet_path}' (FORMAT PARQUET)"
            )
            exported[table] = parquet_path
            logger.info("✓ Exported %s (%d rows)", table, count)

        return exported

    def _export_select_sql(self, table: str) -> str | None:
        table_def = self.TABLE_DEFINITIONS.get(table)
        if not table_def:
            return None

        columns = ', '.join(table_def.export_columns)
        order_clause = ''
        if table_def.order_by:
            order_clause = ' ORDER BY ' + ', '.join(table_def.order_by)
        return f"SELECT {columns} FROM {table_def.name}{order_clause}"


__all__ = [
    'ColumnDef',
    'ForeignKeyRule',
    'GOLD_ID_COLUMN_MAP',
    'GOLD_TABLE_DEFINITIONS',
    'GoldParquetBuilderV2',
    'TableDef',
]
