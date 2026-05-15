"""Evidence ingest backends."""

from minimal.ingest.bulk import BulkMinimalIngestor
from minimal.ingest.simple import MinimalIngestor

__all__ = [
    'BulkMinimalIngestor',
    'MinimalIngestor',
]
