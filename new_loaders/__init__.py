"""New DuckDB-only source-by-source loaders.

This package implements a streamlined data pipeline that processes sources
one-by-one using DuckDB for all transformations.
"""

from .source_processor import SourceProcessor

__all__ = [
    'SourceProcessor',
]
