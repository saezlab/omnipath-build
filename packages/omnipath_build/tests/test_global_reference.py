"""Global reference defaults and authoritative PubChem identity regression tests."""

import json

from library_fixture import ASPIRIN, _hub, write_hubs
from omnipath_build.canonical import build_library, LibraryMatcher
from omnipath_build.silver import RawEntityObservation


def test_conflicting_authoritative_identities_remain_unresolved(tmp_path):
    hubs, library = tmp_path / "hubs", tmp_path / "library"
    write_hubs(hubs)
    # An external resource claims CID 962 for WATER in the fixture. Deliberately
    # conflicting PubChem evidence verifies ownership, not real-world chemistry.
    _hub(hubs, "pubchem", [("inchikey", ASPIRIN, "962", "0")])
    build_library(hubs, library)
    assert json.loads((library / "current" / "manifest.json").read_text())["complete"] is True
    matcher = LibraryMatcher(library)
    observation = RawEntityObservation(
        entity_key="bindingdb-example",
        entity_type="chemical_entity",
        namespace="pubchem",
        identifier="962",
        taxon=None,
        identifiers=[{"ns": "chebi", "id": "CHEBI:15377"}],
    )
    try:
        mouse = RawEntityObservation(
            entity_key="mouse",
            entity_type="protein",
            namespace="uniprot",
            identifier="P02340",
            taxon="10090",
            identifiers=[],
        )
        matches = matcher.match({observation.entity_key: observation, "mouse": mouse})
        result = matches[observation.entity_key]
        assert matches["mouse"].matched
        assert matches["mouse"].taxon == "10090"
    finally:
        matcher.close()
    assert result.canonical_identifier == "962"
    assert not result.matched
    assert "CHEBI:15377" in result.aliases["chebi"]
