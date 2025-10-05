#!/usr/bin/env python3
"""Gold parquet builder using three-phase pipeline: extract → deduplicate → resolve FKs."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import duckdb

from new_loaders.gold_tables import gold_tables, silver_gold_map

logger = logging.getLogger(__name__)


class GoldParquetBuilderV3:
    """Builds gold parquet files using a three-phase pipeline.

    Phase 1: Extract from sources to individual parquet files (parallel)
    Phase 2: Combine and deduplicate pass1 files
    Phase 3: Resolve foreign keys and create final tables
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(':memory:')
        logger.info("GoldParquetBuilderV3 initialized with output_dir=%s", output_dir)

    def __enter__(self) -> 'GoldParquetBuilderV3':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.conn:
            self.conn.close()

    # ------------------------------------------------------------------
    # Phase 1: Source Extraction (Parallel)
    # ------------------------------------------------------------------
    def extract_pass1(
        self,
        table_name: str,
        source_name: str,
        select_sql: str,
        params: list[Any] | None = None,
    ) -> Path:
        """Extract data from a source to a pass1 parquet file.

        Args:
            table_name: Name of the gold table
            source_name: Name of the source (e.g., 'uniprot', 'pfam')
            select_sql: SQL query that returns data with main + temp columns
            params: Optional SQL parameters

        Returns:
            Path to the created pass1 parquet file
        """

        output_path = self.output_dir / f"{table_name}_pass1_{source_name}.parquet"

        # Execute the extraction query
        self.conn.execute(
            f"""
            COPY (
                {select_sql}
            ) TO '{output_path}' (FORMAT PARQUET)
            """,
            params or [],
        )

        logger.info("✓ Pass1: %s from %s → %s", table_name, source_name, output_path.name)
        return output_path

    # ------------------------------------------------------------------
    # Phase 2: Deduplication
    # ------------------------------------------------------------------
    def deduplicate_table(
        self,
        table_name: str,
        priority_columns: list[str] | None = None,
    ) -> Path:
        """Combine and deduplicate all pass1 files for a table.

        Args:
            table_name: Name of the gold table
            priority_columns: Optional columns for ordering during deduplication

        Returns:
            Path to the deduplicated parquet file
        """
        table_def = gold_tables[table_name]

        # Find all pass1 files for this table
        pattern = f"{table_name}_pass1_*.parquet"
        pass1_files = list(self.output_dir.glob(pattern))

        if not pass1_files:
            logger.warning("No pass1 files found for %s (pattern: %s)", table_name, pattern)
            return None

        # Parse constraints to extract deduplication keys
        pass1_constraints = table_def["constraints"]["pass1"]
        dedup_keys = self._extract_dedup_keys(pass1_constraints)

        if not dedup_keys:
            logger.warning("No deduplication keys found for %s, using all columns", table_name)
            all_columns = {**table_def["columns"]["main"], **table_def["columns"]["temp"]}
            dedup_keys = list(all_columns.keys())

        # Build ORDER BY clause
        order_clause = ', '.join(priority_columns) if priority_columns else ', '.join(dedup_keys)

        output_path = self.output_dir / f"{table_name}_deduped.parquet"

        # Deduplicate using DISTINCT ON and add auto-increment id
        self.conn.execute(
            f"""
            COPY (
                SELECT ROW_NUMBER() OVER ()::INTEGER AS id, deduped.*
                FROM (
                    SELECT DISTINCT ON ({', '.join(dedup_keys)}) *
                    FROM read_parquet('{self.output_dir}/{pattern}')
                    ORDER BY {order_clause}
                ) AS deduped
            ) TO '{output_path}' (FORMAT PARQUET)
            """
        )

        logger.info("✓ Pass2 (dedup): %s ← %d files → %s", table_name, len(pass1_files), output_path.name)
        return output_path

    def deduplicate_all_tables(self) -> dict[str, Path]:
        """Deduplicate all tables that have pass1 files."""
        results = {}
        for table_name in gold_tables.keys():
            output_path = self.deduplicate_table(table_name)
            if output_path:
                results[table_name] = output_path
        return results

    # ------------------------------------------------------------------
    # Phase 3: Foreign Key Resolution
    # ------------------------------------------------------------------
    def resolve_foreign_keys_table(self, table_name: str) -> Path:
        """Resolve foreign keys for a single table.

        Args:
            table_name: Name of the gold table

        Returns:
            Path to the final parquet file
        """
        table_def = gold_tables[table_name]

        deduped_path = self.output_dir / f"{table_name}_deduped.parquet"
        if not deduped_path.exists():
            raise FileNotFoundError(f"Deduped file not found: {deduped_path}")

        # Build FK join clauses
        joins = []
        fk_select_cols = []

        for fk_def in table_def["foreign_keys"]:
            fk_id = fk_def["id"]
            link_text = fk_def["link"]

            # Parse link text: "links to cv_namespace via cv_namespace.name = namespace_name"
            target_table, join_condition = self._parse_fk_link(link_text)

            # Skip FK if target table doesn't exist
            target_deduped = self.output_dir / f"{target_table}_deduped.parquet"
            if not target_deduped.exists():
                logger.warning(f"Skipping FK {fk_id} for {table_name} - target table {target_table} not found")
                fk_select_cols.append(f"NULL AS {fk_id}")
                continue

            # Use FK ID as unique alias to avoid ambiguous table names
            alias = f"{target_table}_{fk_id}"

            # Update join condition to use the alias
            join_condition_with_alias = join_condition.replace(f"{target_table}.", f"{alias}.")

            # Build join
            joins.append(
                f"""
                LEFT JOIN read_parquet('{target_deduped}') AS {alias}
                ON {join_condition_with_alias}
                """
            )

            fk_select_cols.append(f"{alias}.id AS {fk_id}")

        # Get main columns (excluding temp columns)
        main_cols = list(table_def["columns"]["main"].keys())

        # Build SELECT clause
        select_parts = []
        if main_cols:
            select_parts.append(', '.join(f'main.{col}' for col in main_cols))
        if fk_select_cols:
            select_parts.append(', '.join(fk_select_cols))

        all_select = ', '.join(select_parts)

        # Build final query
        output_path = self.output_dir / f"{table_name}.parquet"

        query = f"""
            COPY (
                SELECT {all_select}
                FROM read_parquet('{deduped_path}') AS main
                {' '.join(joins)}
            ) TO '{output_path}' (FORMAT PARQUET)
            """

        logger.debug(f"FK resolution query for {table_name}:\n{query}")

        self.conn.execute(query)

        logger.info("✓ Pass3 (FK resolve): %s → %s", table_name, output_path.name)
        return output_path

    def resolve_foreign_keys_all(self) -> dict[str, Path]:
        """Resolve foreign keys for all tables.

        Note: All deduped tables are available, so no topological ordering needed.
        """
        results = {}
        for table_name in gold_tables.keys():
            deduped_path = self.output_dir / f"{table_name}_deduped.parquet"
            if deduped_path.exists():
                results[table_name] = self.resolve_foreign_keys_table(table_name)

        return results

    # ------------------------------------------------------------------
    # Incremental Updates
    # ------------------------------------------------------------------
    def add_source_incremental(
        self,
        source_name: str,
        extraction_functions: dict[str, callable],
    ) -> dict[str, Path]:
        """Add a new source incrementally without reprocessing existing sources.

        Args:
            source_name: Name of the new source
            extraction_functions: Dict mapping table_name → extraction function
                Each function should accept (builder, source_name) and call extract_pass1

        Returns:
            Dict mapping table_name → final parquet path
        """
        # 1. Run pass1 for new source only
        logger.info("Running pass1 for new source: %s", source_name)
        for table_name, extract_fn in extraction_functions.items():
            extract_fn(self, source_name)

        # 2. Rerun deduplication (picks up new files automatically)
        logger.info("Rerunning deduplication phase")
        self.deduplicate_all_tables()

        # 3. Rerun pass2 (FK resolution)
        logger.info("Rerunning FK resolution phase")
        return self.resolve_foreign_keys_all()

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------
    def _extract_dedup_keys(self, constraints: list[str]) -> list[str]:
        """Extract column names from constraint definitions.

        Example: ["unique on (name)", "unique on (namespace_name, name)"]
        Returns: ["name"] or ["namespace_name", "name"]
        """
        for constraint in constraints:
            # Match "unique on (col1, col2, ...)"
            match = re.search(r'unique on \(([^)]+)\)', constraint, re.IGNORECASE)
            if match:
                cols = [c.strip() for c in match.group(1).split(',')]
                return cols
        return []

    def _parse_fk_link(self, link_text: str) -> tuple[str, str]:
        """Parse FK link text into table name and join condition.

        Examples:
            "links to cv_namespace via cv_namespace.name = namespace_name"
            → ("cv_namespace", "cv_namespace.name = main.namespace_name")

            "links to cv_term via (cv_namespace.name = type_namespace_name AND cv_term.name = type_name)"
            → ("cv_term", "cv_namespace.name = main.type_namespace_name AND cv_term.name = main.type_name")
        """
        # Extract table name: "links to TABLE via ..."
        table_match = re.search(r'links to (\w+) via (.+)', link_text, re.IGNORECASE)
        if not table_match:
            raise ValueError(f"Cannot parse FK link: {link_text}")

        target_table = table_match.group(1)
        condition_part = table_match.group(2).strip()

        # Remove outer parentheses if present
        if condition_part.startswith('(') and condition_part.endswith(')'):
            condition_part = condition_part[1:-1].strip()

        # Replace bare column references with main.column
        # Pattern: match "= columnname" or "columnname AND" where columnname doesn't have a dot
        def add_main_prefix(match):
            col = match.group(1)
            # Don't prefix if it already has a table prefix (contains a dot)
            if '.' in col:
                return match.group(0)
            return match.group(0).replace(col, f'main.{col}')

        # Match: "= column" or "column AND" or "column)"
        condition_part = re.sub(r'= (\w+)', lambda m: f'= main.{m.group(1)}', condition_part)

        return target_table, condition_part

    # ------------------------------------------------------------------
    # High-level pipeline
    # ------------------------------------------------------------------
    def extract_from_silver_parquet(self, silver_files: dict[str, Path]) -> None:
        """Extract all tables from silver parquet files using silver_gold_map.

        Args:
            silver_files: Dict mapping table names to silver parquet paths
        """
        # Create views for each silver parquet file
        for table_name, parquet_path in silver_files.items():
            self.conn.execute(f"CREATE OR REPLACE VIEW {table_name} AS SELECT * FROM read_parquet('{parquet_path}')")

        # Only extract from tables that reference silver tables we have
        available_silver_tables = set(silver_files.keys())

        for table_name, config in silver_gold_map.items():
            select_sql = config['select']
            source_name = config['source_table']

            # Skip if the source table doesn't exist
            if source_name not in available_silver_tables:
                logger.debug(f"Skipping {table_name} - source table {source_name} not available")
                continue

            self.extract_pass1(
                table_name=table_name,
                source_name=source_name,
                select_sql=select_sql
            )

    def run_full_pipeline(self, silver_files: dict[str, Path]) -> dict[str, Path]:
        """Run the full three-phase pipeline from silver parquet files.

        Args:
            silver_files: Dict mapping table names to silver parquet paths

        Returns:
            Dict mapping table_name → final parquet path
        """
        # Phase 1: Extract
        logger.info("=" * 70)
        logger.info("Phase 1: Source Extraction")
        logger.info("=" * 70)
        self.extract_from_silver_parquet(silver_files)

        # Phase 2: Deduplicate
        logger.info("=" * 70)
        logger.info("Phase 2: Deduplication")
        logger.info("=" * 70)
        self.deduplicate_all_tables()

        # Phase 3: Resolve FKs
        logger.info("=" * 70)
        logger.info("Phase 3: Foreign Key Resolution")
        logger.info("=" * 70)
        return self.resolve_foreign_keys_all()


__all__ = [
    'GoldParquetBuilderV3',
]
