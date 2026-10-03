"""Capture source configuration and recorded download dates for a build."""

import importlib
import sqlite3
from dataclasses import fields, is_dataclass
from pathlib import Path

from omnipath_core.resource_metadata import resource_metadata


def _download_urls(download, seen=()):
    """Inspect static download descriptors without invoking URL resolvers."""
    if download is None or id(download) in seen:
        return set(), False
    url = getattr(download, "url", None)
    if isinstance(url, str):
        return {url}, True
    if hasattr(download, "url"):
        return set(), False
    if is_dataclass(download) and not isinstance(download, type):
        parts = [
            _download_urls(getattr(download, field.name), (*seen, id(download)))
            for field in fields(download)
        ]
        return set().union(*(urls for urls, _ in parts)), bool(parts) and all(ok for _, ok in parts)
    return set(), False


def snapshot_source_metadata(source, datasets, cache_dir):
    metadata = resource_metadata(source, {})
    modules = sorted({dataset.qualified_module for dataset in datasets})
    if modules:
        module = importlib.import_module(modules[0])
        config = getattr(module, "config", None)
        if config is not None:
            license = getattr(config, "license", None)
            metadata.update(
                website=getattr(config, "url", None),
                license=getattr(license, "definition", None),
                license_url=getattr(license, "source", None),
            )
        metadata["input_module_url"] = (
            "https://github.com/saezlab/pypath/blob/silver_schema_improvement/"
            + modules[0].replace(".", "/")
            + ("/__init__.py" if hasattr(module, "__path__") else ".py")
        )
    metadata.pop("metadata_source", None)
    # Only recorded completed downloads count. File mtimes are not download
    # provenance, and URL resolvers must not trigger more network requests here.
    downloads = [getattr(ds.raw_dataset, "download", None) for ds in datasets]
    parts = [_download_urls(d) for d in downloads]
    urls = set().union(*(urls for urls, _ in parts))
    complete = bool(parts) and all(ok for _, ok in parts)
    dates = []
    db = Path(cache_dir) / "cache.sqlite"
    if urls and db.is_file():
        try:
            with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                for url in sorted(urls):
                    row = conn.execute(
                        """
                        SELECT max(f.value) FROM attr_varchar u
                        JOIN main m ON m.id = u.id AND m.status = 3
                        JOIN (SELECT id, name, value FROM attr_varchar
                              UNION ALL SELECT id, name, value FROM attr_datetime) f ON f.id = u.id
                        WHERE u.name = 'url' AND u.value = ?
                        AND f.name = 'download_finished'
                    """,
                        (url,),
                    ).fetchone()
                    if row and row[0]:
                        dates.append(row[0])
        except sqlite3.Error:
            pass
    metadata["downloaded_at"] = (
        max(dates) if complete and dates and len(dates) == len(urls) else None
    )
    return metadata
