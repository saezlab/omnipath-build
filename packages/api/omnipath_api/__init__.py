"""OmniPath Full-Parquet Presentation and Serving Layer.

Provides high-performance analytical queries, autocomplete, faceted filtering,
and streaming binary exports directly from the resource Parquet tables via DuckDB.
"""

from .engine import ParquetServingEngine
from .server import create_app, run_server, serve

__all__ = [
    "ParquetServingEngine",
    "create_app",
    "run_server",
    "serve",
]
