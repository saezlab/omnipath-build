"""New DuckDB-only source-by-source loaders."""

from pathlib import Path
from typing import Optional

from .gold_parquet_builder_v2 import GoldParquetBuilderV2
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

    with GoldParquetBuilderV2(source_filter or 'bulk', output_dir) as builder:
        builder.ingest_silver_directory(silver_dir, source_filter)
        return builder.export_all_tables()
