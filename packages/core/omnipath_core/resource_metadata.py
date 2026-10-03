"""Source metadata usable by the API without importing pypath input modules."""

from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _catalog():
    return json.loads((Path(__file__).parent / "vocab/resource_metadata.yaml").read_text())


@lru_cache(maxsize=1)
def _browsing_catalog():
    return json.loads((Path(__file__).parent / "vocab/resource_catalog.yaml").read_text())


@lru_cache(maxsize=1)
def _tag_vocabulary():
    return json.loads((Path(__file__).parent / "vocab/resource_tags.yaml").read_text())


def resource_metadata(source: str, manifest: dict) -> dict:
    # Legacy builds have no source snapshot. Never substitute build time for
    # download time: cached inputs can predate a build by months.
    snapshot = manifest.get("resource_metadata")
    metadata = dict(snapshot if isinstance(snapshot, dict) else _catalog().get(source, {}))
    curated = _browsing_catalog().get(source, {})
    vocabulary = _tag_vocabulary()
    # A browsing edit applies to existing builds, but must never overwrite their
    # acquisition provenance or infer permissions for a different source license.
    license_use = {"academic": "unknown", "commercial": "unknown"}
    if curated.get("license_reference") and curated["license_reference"] == metadata.get("license"):
        license_use.update(curated.get("license_use", {}))
    return {
        **metadata,
        "description": curated.get("description", ""),
        "tags": [{"id": tag, **vocabulary[tag]} for tag in curated.get("tags", [])],
        "license_use": license_use,
        "metadata_source": "build snapshot"
        if isinstance(snapshot, dict)
        else "pypath input configuration",
        "downloaded_at": metadata.get("downloaded_at"),
    }
