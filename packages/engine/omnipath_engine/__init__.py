"""OmniPath Parquet serving engine.

Analytical queries, autocomplete, faceted filtering and binary exports directly from
the resource Parquet tables via DuckDB. The API serves it over HTTP; the client runs
it on downloaded data.
"""

from .engine import ParquetServingEngine

__all__ = ["ParquetServingEngine"]
