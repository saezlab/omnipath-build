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

        # Create pass1 subdirectory
        pass1_dir = self.output_dir / "pass1"
        pass1_dir.mkdir(exist_ok=True)
        output_path = pass1_dir / f"{table_name}_pass1_{source_name}.parquet"

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
        pass1_dir = self.output_dir / "pass1"
        pattern = f"{table_name}_pass1_*.parquet"
        pass1_files = list(pass1_dir.glob(pattern)) if pass1_dir.exists() else []

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

        # Create deduped subdirectory
        deduped_dir = self.output_dir / "deduped"
        deduped_dir.mkdir(exist_ok=True)
        output_path = deduped_dir / f"{table_name}_deduped.parquet"

        # Deduplicate using DISTINCT ON and add auto-increment id
        self.conn.execute(
            f"""
            COPY (
                SELECT ROW_NUMBER() OVER ()::INTEGER AS id, deduped.*
                FROM (
                    SELECT DISTINCT ON ({', '.join(dedup_keys)}) *
                    FROM read_parquet('{pass1_dir}/{pattern}')
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

        # Clean up pass1 files after successful deduplication
        pass1_dir = self.output_dir / "pass1"
        if pass1_dir.exists():
            import shutil
            shutil.rmtree(pass1_dir)
            logger.info("✓ Cleaned up pass1 files")

        return results

    # ------------------------------------------------------------------
    # Phase 2.5: Enrich CV Terms
    # ------------------------------------------------------------------
    def enrich_cv_terms(self) -> None:
        """Scan deduped files and auto-generate missing cv_terms.

        This runs after deduplication but before FK resolution.
        It scans all deduped files for cv_term references and ensures
        those terms exist in cv_term_deduped.
        """
        deduped_dir = self.output_dir / "deduped"
        cv_namespace_deduped = deduped_dir / "cv_namespace_deduped.parquet"
        cv_term_deduped = deduped_dir / "cv_term_deduped.parquet"

        if not cv_namespace_deduped.exists() or not cv_term_deduped.exists():
            logger.info("Skipping cv_term enrichment - cv_namespace or cv_term not found")
            return

        logger.info("Enriching cv_terms from deduped files...")

        # Collect all namespace/term references from deduped files
        missing_namespaces = set()
        missing_terms = []  # [(namespace, name, description)]

        # Check entity table for entity types
        entity_deduped = deduped_dir / "entity_deduped.parquet"
        if entity_deduped.exists():
            result = self.conn.execute(f"""
                SELECT DISTINCT
                    entity_type_namespace_name as namespace,
                    entity_type_name as name
                FROM read_parquet('{entity_deduped}')
                WHERE entity_type_namespace_name IS NOT NULL
                  AND entity_type_name IS NOT NULL
            """).fetchall()

            for namespace, name in result:
                missing_namespaces.add(namespace)
                missing_terms.append((namespace, name, 'Auto-generated entity type'))

        # Check entity_identifier table for identifier types
        entity_identifier_deduped = deduped_dir / "entity_identifier_deduped.parquet"
        if entity_identifier_deduped.exists():
            result = self.conn.execute(f"""
                SELECT DISTINCT
                    identifier_type_namespace_name as namespace,
                    identifier_type_name as name
                FROM read_parquet('{entity_identifier_deduped}')
                WHERE identifier_type_namespace_name IS NOT NULL
                  AND identifier_type_name IS NOT NULL
            """).fetchall()

            for namespace, name in result:
                missing_namespaces.add(namespace)
                missing_terms.append((namespace, name, 'Auto-generated identifier type'))

        # Check reference table for reference types
        reference_deduped = deduped_dir / "reference_deduped.parquet"
        if reference_deduped.exists():
            result = self.conn.execute(f"""
                SELECT DISTINCT
                    type_namespace_name as namespace,
                    type_name as name
                FROM read_parquet('{reference_deduped}')
                WHERE type_namespace_name IS NOT NULL
                  AND type_name IS NOT NULL
            """).fetchall()

            for namespace, name in result:
                missing_namespaces.add(namespace)
                missing_terms.append((namespace, name, 'Auto-generated reference type'))

        # Filter out namespaces that already exist
        existing_namespaces = set(
            row[0] for row in self.conn.execute(f"""
                SELECT DISTINCT name FROM read_parquet('{cv_namespace_deduped}')
            """).fetchall()
        )

        new_namespaces = missing_namespaces - existing_namespaces

        # Filter out terms that already exist
        existing_terms = set(
            (row[0], row[1]) for row in self.conn.execute(f"""
                SELECT DISTINCT namespace_name, name
                FROM read_parquet('{cv_term_deduped}')
            """).fetchall()
        )

        new_terms = [(ns, name, desc) for ns, name, desc in missing_terms
                     if (ns, name) not in existing_terms]

        if not new_namespaces and not new_terms:
            logger.info("✓ No missing cv_terms to enrich")
            return

        # Insert missing namespaces
        if new_namespaces:
            logger.info(f"Adding {len(new_namespaces)} missing namespaces: {new_namespaces}")

            # Get current max ID
            max_id = self.conn.execute(f"""
                SELECT COALESCE(MAX(id), 0) FROM read_parquet('{cv_namespace_deduped}')
            """).fetchone()[0]

            # Create temp table with new namespaces
            namespace_values = ', '.join(f"({max_id + i + 1}, '{ns}')"
                                        for i, ns in enumerate(sorted(new_namespaces)))

            # Append to existing file
            self.conn.execute(f"""
                COPY (
                    SELECT * FROM read_parquet('{cv_namespace_deduped}')
                    UNION ALL
                    SELECT * FROM (VALUES {namespace_values}) AS t(id, name)
                ) TO '{cv_namespace_deduped}' (FORMAT PARQUET)
            """)

        # Insert missing terms
        if new_terms:
            logger.info(f"Adding {len(new_terms)} missing cv_terms")

            # Get current max ID
            max_id = self.conn.execute(f"""
                SELECT COALESCE(MAX(id), 0) FROM read_parquet('{cv_term_deduped}')
            """).fetchone()[0]

            # Create temp table with new terms
            term_values = ', '.join(
                f"({max_id + i + 1}, '{name}', NULL, '{desc}', FALSE, '{ns}', NULL, NULL)"
                for i, (ns, name, desc) in enumerate(new_terms)
            )

            # Append to existing file
            self.conn.execute(f"""
                COPY (
                    SELECT * FROM read_parquet('{cv_term_deduped}')
                    UNION ALL
                    SELECT * FROM (VALUES {term_values}) AS t(id, name, accession, description, is_obsolete, namespace_name, replaces_accession, replaced_by_accession)
                ) TO '{cv_term_deduped}' (FORMAT PARQUET)
            """)

        logger.info(f"✓ Enriched cv_terms: +{len(new_namespaces)} namespaces, +{len(new_terms)} terms")

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

        deduped_dir = self.output_dir / "deduped"
        deduped_path = deduped_dir / f"{table_name}_deduped.parquet"
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
            target_deduped = deduped_dir / f"{target_table}_deduped.parquet"
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

        # Get main columns only (temp columns are dropped in final tables)
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
        deduped_dir = self.output_dir / "deduped"
        for table_name in gold_tables.keys():
            deduped_path = deduped_dir / f"{table_name}_deduped.parquet"
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
        # and use IS NOT DISTINCT FROM for NULL-safe equality
        condition_part = re.sub(r'= (\w+)', lambda m: f'IS NOT DISTINCT FROM main.{m.group(1)}', condition_part)

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

        for extraction_name, config in silver_gold_map.items():
            select_sql = config['select']
            source_table = config['source_table']
            target_gold_table = config.get('target_gold_table', extraction_name)

            # Skip if the source table doesn't exist
            if source_table not in available_silver_tables:
                logger.debug(f"Skipping {extraction_name} - source table {source_table} not available")
                continue

            self.extract_pass1(
                table_name=target_gold_table,
                source_name=extraction_name,
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

        # Phase 2.5: Enrich CV Terms
        logger.info("=" * 70)
        logger.info("Phase 2.5: CV Term Enrichment")
        logger.info("=" * 70)
        self.enrich_cv_terms()

        # Phase 3: Resolve FKs
        logger.info("=" * 70)
        logger.info("Phase 3: Foreign Key Resolution")
        logger.info("=" * 70)
        return self.resolve_foreign_keys_all()


__all__ = [
    'GoldParquetBuilderV3',
]
