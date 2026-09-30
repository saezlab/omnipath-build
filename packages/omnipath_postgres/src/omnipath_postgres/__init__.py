"""PostgreSQL loading for already resolved, immutable Parquet releases."""

from .loader import LoadResult, load_release

__all__ = ["LoadResult", "load_release"]
