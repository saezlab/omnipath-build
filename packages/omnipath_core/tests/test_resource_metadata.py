"""Browsing curation stays independent of immutable acquisition metadata."""

import json
from importlib.resources import files

from omnipath_core.resource_metadata import resource_metadata


VOCAB = files("omnipath_core").joinpath("vocab")


def test_catalog_covers_current_resources_with_valid_controlled_tags():
    sources = json.loads((VOCAB / "resource_metadata.yaml").read_text())
    catalog = json.loads((VOCAB / "resource_catalog.yaml").read_text())
    tags = json.loads((VOCAB / "resource_tags.yaml").read_text())
    assert set(catalog) == set(sources)
    for source, curated in catalog.items():
        assert curated["description"]
        assert curated["tags"] and len(curated["tags"]) == len(set(curated["tags"]))
        assert set(curated["tags"]) <= set(tags)
        assert curated["license_reference"] == sources[source]["license"]
        assert set(curated["license_use"]) == {"academic", "commercial"}
        assert set(curated["license_use"].values()) <= {
            "allowed",
            "requires_permission",
            "not_allowed",
            "unknown",
        }
    for tag in tags.values():
        assert tag["label"] and tag["description"]
        assert tag["dimension"] in {"topic", "content", "molecule", "knowledge_origin"}


def test_curation_merges_after_snapshot_without_rewriting_provenance():
    snapshot = {
        "website": "https://historical.example",
        "license": "Historical license",
        "downloaded_at": "2020-01-01",
        "description": "Old description",
        "tags": ["obsolete"],
        "license_use": {"commercial": "allowed"},
    }
    metadata = resource_metadata("reactome", {"resource_metadata": snapshot})
    assert metadata["website"] == snapshot["website"]
    assert metadata["license"] == snapshot["license"]
    assert metadata["downloaded_at"] == snapshot["downloaded_at"]
    assert metadata["metadata_source"] == "build snapshot"
    assert metadata["description"] != "Old description"
    assert "pathways" in {tag["id"] for tag in metadata["tags"]}
    assert metadata["license_use"] == {"academic": "unknown", "commercial": "unknown"}
    assert snapshot["tags"] == ["obsolete"]


def test_matching_license_and_legacy_fallback_use_catalog_classification():
    current = resource_metadata("corum", {})
    assert current["license_use"] == {"academic": "allowed", "commercial": "not_allowed"}
    snapshot = resource_metadata("corum", {"resource_metadata": {"license": current["license"]}})
    assert snapshot["license_use"] == current["license_use"]
    assert resource_metadata("kegg", {})["license_use"]["commercial"] == "requires_permission"
    assert resource_metadata("cellchat", {})["license_use"]["commercial"] == "unknown"


def test_unknown_resource_and_missing_snapshot_license_are_conservative():
    unknown = resource_metadata("future_resource", {})
    assert unknown["tags"] == []
    assert unknown["description"] == ""
    assert unknown["license_use"] == {"academic": "unknown", "commercial": "unknown"}
    missing = resource_metadata("reactome", {"resource_metadata": {}})
    assert missing["license_use"] == unknown["license_use"]
    assert missing["metadata_source"] == "build snapshot"
