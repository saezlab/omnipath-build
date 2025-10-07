#!/usr/bin/env python3
"""Gold Loader: Silver → Gold transformations using three-phase pipeline.

This loader handles the transformation of silver parquet files to gold parquet files
through a three-phase pipeline: extract → deduplicate → resolve foreign keys.

Phase 1: Extract from sources to individual parquet files (parallel)
Phase 2: Combine and deduplicate pass1 files
Phase 3: Resolve foreign keys and create final tables
"""

from __future__ import annotations

import glob
import logging
import re
from pathlib import Path
from typing import Any

import duckdb
import importlib

import sys
from pathlib import Path as PathType

# Import gold_tables from configuration directory
sys.path.insert(0, str(PathType(__file__).parent.parent / 'databases' / 'omnipath' / 'configuration'))
from gold_tables import gold_tables, silver_gold_map

from omnipath_build.utils import PathManager

# Delayed import to avoid circular dependency
def _get_augment_loader_class():
    """Lazy import AugmentLoader to avoid circular dependency."""
    augment_module = importlib.import_module('.4_augment_loader', package='omnipath_build')
    return augment_module.AugmentLoader

logger = logging.getLogger(__name__)


class GoldLoader:
    """Builds gold parquet files using a three-phase pipeline.

    Phase 1: Extract from sources to individual parquet files (parallel)
    Phase 2: Combine and deduplicate pass1 files
    Phase 3: Resolve foreign keys and create final tables
    """

    PASS1_DIR_NAME = 'pass1'
    DEDUPED_DIR_NAME = 'deduped'

    def __init__(
        self,
        path_or_manager: Path | PathManager,
        path_manager: PathManager | None = None,
        *,
        compound_limit: int | None = 1000,
    ) -> None:
        """Initialize the gold loader.

        Args:
            path_or_manager: Either a PathManager (preferred) or a legacy output directory.
            path_manager: Optional explicit PathManager when the first argument is a Path.
            compound_limit: Maximum number of compounds to augment (default: 1000)
        """

        if isinstance(path_or_manager, PathManager) or hasattr(path_or_manager, 'gold_final_path'):
            if path_manager is not None:
                raise ValueError('Provide either a PathManager or output path + path_manager, not both')
            self.path_manager = path_or_manager  # type: ignore[assignment]
            self.output_dir = None
        else:
            self.output_dir = Path(path_or_manager)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.path_manager = path_manager

        if self.path_manager is not None:
            self.gold_final_dir = self.path_manager.gold_final_path()
        elif self.output_dir is not None:
            self.gold_final_dir = self.output_dir
        else:
            raise ValueError('GoldLoader requires either a PathManager or an output directory')

        self.gold_final_dir.mkdir(parents=True, exist_ok=True)
        self.deduped_dir = self.gold_final_dir / self.DEDUPED_DIR_NAME
        self.deduped_dir.mkdir(parents=True, exist_ok=True)

        # Legacy pass1 directory support when path manager is not supplied
        self._legacy_pass1_dir = None
        if self.path_manager is None and self.output_dir is not None:
            self._legacy_pass1_dir = self.output_dir / self.PASS1_DIR_NAME

        self.conn = duckdb.connect(':memory:')
        logger.info(
            "GoldLoader initialized (path_manager=%s, output_dir=%s)",
            bool(self.path_manager),
            self.output_dir,
        )
        self._augmentor = None  # Will be AugmentLoader when created
        self.compound_limit = compound_limit

    def __enter__(self) -> 'GoldLoader':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.conn:
            self.conn.close()
        self._augmentor = None

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

        # Use PathManager if available, otherwise use legacy paths
        if self.path_manager:
            source_module, function_name = self._split_source_key(source_name, table_name)
            output_path = self.path_manager.gold_file(
                source_module,
                function_name,
                table_name,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            if self._legacy_pass1_dir is None:
                raise ValueError('Legacy pass1 directory is unavailable without an output directory')
            self._legacy_pass1_dir.mkdir(exist_ok=True)
            output_path = self._legacy_pass1_dir / f"{table_name}_pass1_{source_name}.parquet"

        # Execute the extraction query
        output_path_literal = self._duckdb_path_literal(output_path)
        self.conn.execute(
            f"""
            COPY (
                {select_sql}
            ) TO '{output_path_literal}' (FORMAT PARQUET)
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
        pass1_read_expr, pass1_files = self._get_pass1_read_source(table_name)

        if not pass1_files:
            logger.warning("No pass1 files found for %s", table_name)
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

        # Prepare deduped output path
        output_path = self._deduped_file_path(table_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path_literal = self._duckdb_path_literal(output_path)

        # Deduplicate using DISTINCT ON and add auto-increment id
        self.conn.execute(
            f"""
            COPY (
                SELECT ROW_NUMBER() OVER ()::INTEGER AS id, deduped.*
                FROM (
                    SELECT DISTINCT ON ({', '.join(dedup_keys)}) *
                    FROM {pass1_read_expr}
                    ORDER BY {order_clause}
                ) AS deduped
            ) TO '{output_path_literal}' (FORMAT PARQUET)
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

    def deduplicate_table_incremental(
        self,
        table_name: str,
        priority_columns: list[str] | None = None,
    ) -> Path:
        """Append new pass1 files to existing deduped file and re-deduplicate.

        This method is used for incremental source processing. It:
        1. Loads existing deduped file (if exists)
        2. Appends new pass1 data
        3. Re-deduplicates the combined dataset

        Args:
            table_name: Name of the gold table
            priority_columns: Optional columns for ordering during deduplication

        Returns:
            Path to the deduplicated parquet file
        """
        table_def = gold_tables[table_name]

        # Find all pass1 files for this table
        pass1_read_expr, pass1_files = self._get_pass1_read_source(table_name)

        if not pass1_files:
            logger.warning("No pass1 files found for %s", table_name)
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

        # Check if deduped file already exists
        output_path = self._deduped_file_path(table_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path_literal = self._duckdb_path_literal(output_path)

        # Build union query: existing deduped + new pass1 files
        if output_path.exists():
            # Drop the auto-increment id column from existing deduped file before union
            union_query = f"""
                SELECT * EXCLUDE (id) FROM read_parquet('{output_path_literal}')
                UNION ALL
                SELECT * FROM {pass1_read_expr}
            """
            logger.info("Appending to existing deduped file: %s", table_name)
        else:
            # No existing deduped file, just use pass1 files
            union_query = f"SELECT * FROM {pass1_read_expr}"
            logger.info("Creating new deduped file: %s", table_name)

        # Deduplicate the combined dataset and add auto-increment id
        self.conn.execute(
            f"""
            COPY (
                SELECT ROW_NUMBER() OVER ()::INTEGER AS id, deduped.*
                FROM (
                    SELECT DISTINCT ON ({', '.join(dedup_keys)}) *
                    FROM ({union_query})
                    ORDER BY {order_clause}
                ) AS deduped
            ) TO '{output_path_literal}' (FORMAT PARQUET)
            """
        )

        logger.info("✓ Pass2 (incremental dedup): %s ← %d new files → %s",
                   table_name, len(pass1_files), output_path.name)
        return output_path

    def deduplicate_all_tables_incremental(self) -> dict[str, Path]:
        """Incrementally deduplicate all tables that have pass1 files.

        Appends new pass1 data to existing deduped files and re-deduplicates.
        """
        results = {}
        for table_name in gold_tables.keys():
            output_path = self.deduplicate_table_incremental(table_name)
            if output_path:
                results[table_name] = output_path

        return results

    # ------------------------------------------------------------------
    # Phase 2.5: Enrich CV Terms
    # ------------------------------------------------------------------
    def run_data_augmentation(self, cv_terms_only: bool = False) -> None:
        """Augment deduplicated data with derived metadata before FK resolution."""

        augmentor = self._get_data_augmentor()
        if cv_terms_only:
            augmentor.ensure_cv_terms()
        else:
            augmentor.run_all()

    def enrich_cv_terms(self) -> None:
        """Backward-compatible wrapper for legacy callers."""

        logger.debug("enrich_cv_terms() is deprecated – using DataAugmentor.ensure_cv_terms")
        self.run_data_augmentation(cv_terms_only=True)

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

        deduped_dir = self.deduped_dir
        deduped_path = deduped_dir / f"{table_name}_deduped.parquet"
        deduped_path_literal = self._duckdb_path_literal(deduped_path)
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

            target_literal = self._duckdb_path_literal(target_deduped)

            # Check if this is an entity_identifier fallback FK
            if join_condition.startswith("__ENTITY_IDENTIFIER_FALLBACK__:"):
                # Parse: "__ENTITY_IDENTIFIER_FALLBACK__:col1,col2:direct_condition"
                parts = join_condition.split(":", 2)
                column_list = parts[1]
                direct_condition = parts[2]

                identifier_col, type_col = [c.strip() for c in column_list.split(',')]

                # Check if entity_identifier table exists
                entity_identifier_deduped = deduped_dir / "entity_identifier_deduped.parquet"
                if not entity_identifier_deduped.exists():
                    logger.warning(f"entity_identifier table not found - falling back to direct match only for {fk_id}")
                    # Fall back to direct match only
                    alias = f"{target_table}_{fk_id}"
                    direct_condition_with_main = re.sub(r'= (\w+)', lambda m: f'= main.{m.group(1)}', direct_condition)
                    direct_condition_with_alias = direct_condition_with_main.replace(f"{target_table}.", f"{alias}.")
                    joins.append(f"""
                        LEFT JOIN read_parquet('{target_literal}') AS {alias}
                        ON {direct_condition_with_alias}
                    """)
                    fk_select_cols.append(f"{alias}.id AS {fk_id}")
                else:
                    # Two-stage join: direct match OR entity_identifier lookup
                    entity_identifier_literal = self._duckdb_path_literal(entity_identifier_deduped)

                    alias_direct = f"{target_table}_direct_{fk_id}"
                    alias_ei = f"entity_identifier_{fk_id}"
                    alias_via_ei = f"{target_table}_via_ei_{fk_id}"

                    # Prepare direct condition with proper table references
                    direct_condition_with_main = re.sub(r'= (\w+)', lambda m: f'= main.{m.group(1)}', direct_condition)
                    direct_condition_with_alias = direct_condition_with_main.replace(f"{target_table}.", f"{alias_direct}.")

                    # Join 1: Direct match on entity table
                    joins.append(f"""
                        LEFT JOIN read_parquet('{target_literal}') AS {alias_direct}
                        ON {direct_condition_with_alias}
                    """)

                    # Join 2: Lookup via entity_identifier
                    joins.append(f"""
                        LEFT JOIN read_parquet('{entity_identifier_literal}') AS {alias_ei}
                        ON {alias_ei}.identifier IS NOT DISTINCT FROM main.{identifier_col}
                       AND {alias_ei}.type_name IS NOT DISTINCT FROM main.{type_col}
                    """)

                    # Join 3: Get entity via entity_identifier.entity_id
                    joins.append(f"""
                        LEFT JOIN read_parquet('{target_literal}') AS {alias_via_ei}
                        ON {alias_via_ei}.id = {alias_ei}.entity_id
                    """)

                    # COALESCE: prefer direct match, fallback to entity_identifier lookup
                    fk_select_cols.append(f"""
                        COALESCE({alias_direct}.id, {alias_via_ei}.id) AS {fk_id}
                    """)

                continue

            # Standard FK resolution (non-entity_identifier fallback)
            # Use FK ID as unique alias to avoid ambiguous table names
            alias = f"{target_table}_{fk_id}"

            # Update join condition to use the alias
            join_condition_with_alias = join_condition.replace(f"{target_table}.", f"{alias}.")

            # Use "IS NOT DISTINCT FROM" for FK comparisons so NULLs match when appropriate
            join_condition_with_alias = join_condition_with_alias.replace(
                ' = main.',
                ' IS NOT DISTINCT FROM main.',
            )

            # Track which main table columns participate in the join
            main_columns = {
                match.group(1)
                for match in re.finditer(r'main\.([A-Za-z_][A-Za-z0-9_]*)', join_condition_with_alias)
            }

            # Build join
            joins.append(
                f"""
                LEFT JOIN read_parquet('{target_literal}') AS {alias}
                ON {join_condition_with_alias}
                """
            )

            if main_columns:
                null_guard = ' AND '.join(f"main.{col} IS NULL" for col in sorted(main_columns))
                fk_select_cols.append(
                    f"CASE WHEN {null_guard} THEN NULL ELSE {alias}.id END AS {fk_id}"
                )
            else:
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
        output_path = self.gold_final_dir / f"{table_name}.parquet"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_literal = self._duckdb_path_literal(output_path)

        query = f"""
            COPY (
                SELECT {all_select}
                FROM read_parquet('{deduped_path_literal}') AS main
                {' '.join(joins)}
            ) TO '{output_literal}' (FORMAT PARQUET)
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
        deduped_dir = self.deduped_dir
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

        This method appends new source data to existing deduped files and re-deduplicates,
        avoiding the need to regenerate everything from scratch.

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

        # 2. Incrementally append to existing deduped files and re-deduplicate
        logger.info("Incrementally deduplicating (appending to existing deduped files)")
        self.deduplicate_all_tables_incremental()

        # 3. Augment deduped data before FK resolution
        logger.info("Running data augmentation")
        self.run_data_augmentation()

        # 4. Rerun FK resolution
        logger.info("Rerunning FK resolution phase")
        return self.resolve_foreign_keys_all()

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------
    def _get_data_augmentor(self):
        """Get or create AugmentLoader instance (lazy import to avoid circular dependency)."""
        if self._augmentor is None:
            AugmentLoaderClass = _get_augment_loader_class()
            self._augmentor = AugmentLoaderClass(
                self.conn,
                self.deduped_dir,
                compound_limit=self.compound_limit,
            )
        return self._augmentor

    def _split_source_key(self, source_name: str, fallback_func: str) -> tuple[str, str]:
        """Split a pass1 source key into (source_module, function_name)."""
        if '__' in source_name:
            return source_name.split('__', 1)
        return source_name, fallback_func

    def _collect_pass1_paths(self, table_name: str) -> list[Path]:
        """Collect all pass1 parquet files contributing to a gold table."""
        if self.path_manager:
            data_root = self.path_manager.data_path()
            pattern = data_root / '*' / '*' / PathManager.GOLD / f'{table_name}.parquet'
            return [Path(p) for p in sorted(glob.glob(str(pattern)))]

        if self._legacy_pass1_dir and self._legacy_pass1_dir.exists():
            pattern = f'{table_name}_pass1_*.parquet'
            return sorted(self._legacy_pass1_dir.glob(pattern))

        return []

    def _get_pass1_read_source(self, table_name: str) -> tuple[str, list[Path]]:
        """Return DuckDB read expression and matching files for a table."""
        pass1_files = self._collect_pass1_paths(table_name)
        if not pass1_files:
            return '', []

        if self.path_manager:
            data_root = self.path_manager.data_path()
            pattern = self._duckdb_path_literal(
                data_root / '*' / '*' / PathManager.GOLD / f'{table_name}.parquet'
            )
            return f"read_parquet('{pattern}')", pass1_files

        if self._legacy_pass1_dir is None:
            raise ValueError('Legacy pass1 directory unavailable without output dir')

        pattern = self._duckdb_path_literal(
            self._legacy_pass1_dir / f'{table_name}_pass1_*.parquet'
        )
        return f"read_parquet('{pattern}')", pass1_files

    def _deduped_file_path(self, table_name: str) -> Path:
        """Return the deduped parquet path for a gold table."""
        return self.deduped_dir / f'{table_name}_deduped.parquet'

    @staticmethod
    def _duckdb_path_literal(path: Path | str) -> str:
        """Escape a filesystem path for embedding in DuckDB SQL."""
        return str(path).replace("'", "''")

    def _list_pass1_files_by_table(self) -> dict[str, list[Path]]:
        """Return table → pass1 files mapping based on storage backend."""
        pass1_map: dict[str, list[Path]] = {}

        if self.path_manager:
            data_root = self.path_manager.data_path()
            if not data_root.exists():
                return {}

            for source_dir in data_root.iterdir():
                if not source_dir.is_dir():
                    continue
                for function_dir in source_dir.iterdir():
                    if not function_dir.is_dir():
                        continue
                    gold_dir = function_dir / PathManager.GOLD
                    if not gold_dir.exists():
                        continue
                    for parquet_file in gold_dir.glob('*.parquet'):
                        pass1_map.setdefault(parquet_file.stem, []).append(parquet_file)

            return pass1_map

        if self._legacy_pass1_dir and self._legacy_pass1_dir.exists():
            for parquet_file in self._legacy_pass1_dir.glob('*_pass1_*.parquet'):
                table_name = parquet_file.name.split('_pass1_')[0]
                pass1_map.setdefault(table_name, []).append(parquet_file)

        return pass1_map

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

            "links to entity via (...) OR via entity_identifier (col1, col2)"
            → ("entity", special handling - returns marker for two-stage join)
        """
        # Check for entity_identifier fallback pattern
        if "OR via entity_identifier" in link_text:
            return self._parse_fk_with_entity_identifier_fallback(link_text)

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
        condition_part = re.sub(r'= (\w+)', lambda m: f'= main.{m.group(1)}', condition_part)

        return target_table, condition_part

    def _parse_fk_with_entity_identifier_fallback(self, link_text: str) -> tuple[str, str]:
        """Parse FK link with entity_identifier fallback.

        Example link:
            "links to entity via (entity.deduplication_identifier = entity_deduplication_identifier
             AND entity.deduplication_identifier_type = entity_deduplication_identifier_type)
             OR via entity_identifier (entity_deduplication_identifier, entity_deduplication_identifier_type)"

        Returns:
            ("entity", "__ENTITY_IDENTIFIER_FALLBACK__:identifier_col,type_col")

        This marker signals to resolve_foreign_keys_table() to use the two-stage lookup.
        """
        # Extract the columns for entity_identifier lookup
        match = re.search(r'OR via entity_identifier \(([^)]+)\)', link_text, re.IGNORECASE)
        if not match:
            raise ValueError(f"Cannot parse entity_identifier fallback in: {link_text}")

        column_list = match.group(1).strip()

        # Parse the direct match condition (before "OR via entity_identifier")
        direct_match = re.search(r'links to (\w+) via (.+?) OR via entity_identifier', link_text, re.IGNORECASE)
        if not direct_match:
            raise ValueError(f"Cannot parse entity FK direct match in: {link_text}")

        target_table = direct_match.group(1)
        direct_condition = direct_match.group(2).strip()

        # Remove outer parentheses
        if direct_condition.startswith('(') and direct_condition.endswith(')'):
            direct_condition = direct_condition[1:-1].strip()

        # Return special marker that includes both the direct condition and the fallback columns
        return target_table, f"__ENTITY_IDENTIFIER_FALLBACK__:{column_list}:{direct_condition}"

    # ------------------------------------------------------------------
    # High-level pipeline
    # ------------------------------------------------------------------
    def extract_from_silver_parquet(
        self,
        silver_files: dict[str, Path],
        source_label: str | None = None,
        table_function_map: dict[str, str] | None = None,
    ) -> None:
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

            pass1_source_name = extraction_name
            if source_label:
                function_name: str | None = None
                if table_function_map:
                    function_name = table_function_map.get(source_table)

                if function_name is None:
                    parquet_path = silver_files.get(source_table)
                    if parquet_path:
                        silver_dir = parquet_path.parent
                        function_dir = silver_dir.parent if silver_dir.name == PathManager.SILVER else silver_dir
                        # Avoid using the root path name ("/")
                        if function_dir != function_dir.parent:
                            function_name = function_dir.name

                if function_name:
                    pass1_source_name = f"{source_label}__{function_name}"
                else:
                    pass1_source_name = f"{source_label}__{extraction_name}"

            self.extract_pass1(
                table_name=target_gold_table,
                source_name=pass1_source_name,
                select_sql=select_sql
            )

    def run_pass1_only(
        self,
        silver_files: dict[str, Path],
        source_label: str | None = None,
        table_function_map: dict[str, str] | None = None,
    ) -> dict[str, list[Path]]:
        """Run only Phase 1: Extract pass1 files from silver parquet.

        This is used for parallel processing where each source creates its pass1 files
        independently, and deduplication happens later across all sources.

        Args:
            silver_files: Dict mapping table names to silver parquet paths

        Returns:
            Dict mapping table_name → list of pass1 parquet paths created
        """
        logger.info("=" * 70)
        logger.info("Phase 1: Source Extraction (Pass1 only)")
        logger.info("=" * 70)
        self.extract_from_silver_parquet(
            silver_files,
            source_label=source_label,
            table_function_map=table_function_map,
        )

        return self._list_pass1_files_by_table()

    def run_dedup_and_fk_resolution(self) -> dict[str, Path]:
        """Run Phase 2 & 3: Deduplicate all pass1 files and resolve foreign keys.

        This is the cross-source phase that runs after all sources have completed
        their pass1 extraction. It reads all pass1 files from the pass1 directory
        and creates the final deduplicated gold parquet files.

        Returns:
            Dict mapping table_name → final parquet path
        """
        # Phase 2: Deduplicate
        logger.info("=" * 70)
        logger.info("Phase 2: Cross-Source Deduplication")
        logger.info("=" * 70)
        self.deduplicate_all_tables()

        # Phase 2.5: Data augmentation
        logger.info("=" * 70)
        logger.info("Phase 2.5: Data Augmentation")
        logger.info("=" * 70)
        self.run_data_augmentation()

        # Phase 3: Resolve FKs
        logger.info("=" * 70)
        logger.info("Phase 3: Foreign Key Resolution")
        logger.info("=" * 70)
        return self.resolve_foreign_keys_all()

    def run_full_pipeline(
        self,
        silver_files: dict[str, Path],
        source_label: str | None = None,
        table_function_map: dict[str, str] | None = None,
    ) -> dict[str, Path]:
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
        self.extract_from_silver_parquet(
            silver_files,
            source_label=source_label,
            table_function_map=table_function_map,
        )

        # Phase 2: Deduplicate
        logger.info("=" * 70)
        logger.info("Phase 2: Deduplication")
        logger.info("=" * 70)
        self.deduplicate_all_tables()

        # Phase 2.5: Data augmentation
        logger.info("=" * 70)
        logger.info("Phase 2.5: Data Augmentation")
        logger.info("=" * 70)
        self.run_data_augmentation()

        # Phase 3: Resolve FKs
        logger.info("=" * 70)
        logger.info("Phase 3: Foreign Key Resolution")
        logger.info("=" * 70)
        return self.resolve_foreign_keys_all()


__all__ = [
    'GoldLoader',
]
