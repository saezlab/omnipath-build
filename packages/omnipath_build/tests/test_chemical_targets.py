from library_fixture import build_fixture_library, WATER, ASPIRIN
import json
from collections import defaultdict

import lmdb

from omnipath_build.reference.compact_index import load_codecs, logical_values
from omnipath_build.reference.full_index import partition, storage_pairs
from omnipath_build.reference.replay_resources import key
from omnipath_build.resolver import EntityResolver
from omnipath_build.silver import RawEntityObservation, SilverExtractor
from writer_fixture import write_observations

VARIANT = WATER[:-1] + "O"
MISSING = "ZZZZZZZZZZZZZZ-UHFFFAOYSA-N"


def setup(tmp_path):
    # Modify only this test's private copy of the published compact reference.
    library = build_fixture_library(tmp_path)
    manifest_path = library / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    codecs = load_codecs(library, manifest["dictionaries"])
    mappings = [("inchikey", k, k) for k in (WATER, VARIANT, ASPIRIN)] + [
        ("pubchem", "1", WATER),
        ("chebi", "CHEBI:1", VARIANT),
        ("chembl", "CHEMBL1", ASPIRIN),
    ]
    rows = defaultdict(list)
    for n, structure in enumerate((WATER, VARIANT, ASPIRIN), start=2**60):
        entity_id = "inchikey:" + structure
        identifiers = [[ns, identifier] for ns, identifier, k in mappings if k == structure]
        record = dict(
            entity_id=entity_id,
            kind=1,
            anchor=entity_id,
            taxon=None,
            label=structure,
            identifiers=identifiers,
        )
        meta = dict(
            id=n, entity_id=entity_id, kind=1, anchor=entity_id, quarantined=False, reviewed=False
        )
        rows[("entities", partition(entity_id))].append(
            (entity_id.encode(), dict(record=record, meta=meta))
        )
        for ns, identifier in identifiers:
            posting = dict(
                gene=False, products=False, candidates=[[n, entity_id, 1, entity_id, False, False]]
            )
            rows[("identifiers", partition(identifier))].append(
                (key(1, 1, ns, "", identifier), posting)
            )
    for (kind, part), updates in rows.items():
        shard = library / kind / part
        with lmdb.open(str(shard), map_size=8 * 1024**2) as env:
            with env.begin(write=True) as txn:
                before = sum(1 for _ in logical_values(txn))
                for lookup_key, value in updates:
                    for physical_key, payload in storage_pairs(
                        lookup_key, codecs[kind].encode(value)
                    ):
                        txn.put(physical_key, payload)
                after = sum(1 for _ in logical_values(txn))
            env.sync(True)
        manifest["counts"][kind] += after - before
        manifest["files"][f"{kind}/{part}/data.mdb"] = (shard / "data.mdb").stat().st_size
    manifest_path.write_text(json.dumps(manifest))
    return EntityResolver(library_dir=library)


def obs(extra):
    return RawEntityObservation(
        entity_key="x",
        entity_type="chemical_entity",
        namespace="pubchem",
        identifier="1",
        taxon=None,
        identifiers=[dict(ns=ns, id=ident) for ns, ident in extra],
    )


def test_supplied_key_overrides_conflicting_ids_and_does_not_expand(tmp_path):
    r = setup(tmp_path)
    try:
        targets = r.resolve_entity_targets(
            {"x": obs([("inchikey", ASPIRIN), ("chebi", "CHEBI:1")])}
        )["x"]
        assert [t.canonical_identifier for t in targets] == [ASPIRIN]
        assert targets[0].matched
    finally:
        r.close()


def test_absent_supplied_key_keeps_structure_identity(tmp_path):
    r = setup(tmp_path)
    try:
        targets = r.resolve_entity_targets({"x": obs([("inchikey", MISSING)])})["x"]
        assert [t.canonical_identifier for t in targets] == [MISSING]
        assert not targets[0].matched
    finally:
        r.close()


def test_conflicting_structures_do_not_project_by_connectivity(tmp_path):
    r = setup(tmp_path)
    try:
        targets = r.resolve_entity_targets({"x": obs([("chebi", "CHEBI:1")])})["x"]
        assert len(targets) == 1 and not targets[0].matched
        assert r.resolution_stats()["input_entities"] == 1
        assert len(r.resolve_entity_targets({"y": obs([("chembl", "CHEMBL1")])})["y"]) == 1
        targets = r.resolve_entity_targets(
            {"z": obs([("inchikey", WATER), ("inchikey", VARIANT)])}
        )["z"]
        assert len(targets) == 1 and not targets[0].matched
        targets = r.resolve_entity_targets(
            {"bad": obs([("inchikey", WATER), ("inchikey", MISSING)])}
        )["bad"]
        assert len(targets) == 1 and not targets[0].matched
    finally:
        r.close()


def test_projected_relations_keep_payloads(tmp_path):
    r = setup(tmp_path)
    x = SilverExtractor("fixture", "test")
    x.process_record(
        {
            "subject": {
                "type": "chemical_entity",
                "identifiers": [
                    {"type": "pubchem", "value": "1"},
                    {"type": "chebi", "value": "CHEBI:1"},
                ],
            },
            "predicate": "interacts_with",
            "object": {"type": "protein", "identifiers": [{"type": "uniprot", "value": "P04637"}]},
        },
        {"measurement": "same evidence"},
        "row1",
        0,
    )
    try:
        entities, relations, payloads = write_observations(r, x.entities, x.relations, x.payloads)
        assert len(entities) == 2
        assert len(relations) == 1
        assert payloads
    finally:
        r.close()
