"""Evidence ingest backends and source-row synchronization."""

from minimal.ingest.bulk import BulkMinimalIngestor
from minimal.ingest.simple import MinimalIngestor
from minimal.ingest.source_rows import (
    SourceSnapshotSyncStats,
    sync_source_snapshot,
)

__all__ = [
    'BulkMinimalIngestor',
    'MinimalIngestor',
    'SourceSnapshotSyncStats',
    'sync_source_snapshot',
]
