"""Query versioned OmniPath Parquet resources locally with DuckDB."""

from .client import Client, ClientError

__all__ = ["Client", "ClientError"]
__version__ = "0.1.0"
