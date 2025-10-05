"""New DuckDB-only source-by-source loaders."""

from pathlib import Path
from typing import Optional

from .gold_parquet_builder_v3 import GoldParquetBuilderV3
from .source_processor import SourceProcessor

__all__ = [
    'SourceProcessor',
    'build_gold_from_silver_dir',
]


def build_gold_from_silver_dir(
    silver_dir: Path,
    output_dir: Path,
    source_filter: Optional[str] = None,
):
    """Load all silver parquet files (optionally filtered by source) into gold.

    Args:
        silver_dir: Directory containing silver parquet artefacts.
        output_dir: Directory where gold parquet files should be written.
        source_filter: Optional source name; when provided only silver files whose
            leading token matches the filter are processed.

    Returns:
        Mapping of gold table names to the exported parquet paths.
    """

    with GoldParquetBuilderV3(output_dir) as builder:
        # Load silver files
        silver_files = {}
        for parquet_file in silver_dir.glob('*.parquet'):
            if source_filter and not parquet_file.stem.startswith(source_filter):
                continue
            # Extract table name from filename (assumes format: source_function_table.parquet)
            parts = parquet_file.stem.split('_')
            if len(parts) >= 3:
                table_name = parts[-1]
                silver_files[table_name] = parquet_file

        return builder.run_full_pipeline(silver_files)
