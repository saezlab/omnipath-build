"""The resolver wheel owns its complete public facade without a builder."""

import os
import subprocess
import sys


def test_public_matching_does_not_import_builder():
    script = r"""
import importlib.abc
import sys

class BlockBuild(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "omnipath_build" or fullname.startswith("omnipath_build."):
            raise AssertionError("Runtime imported build: " + fullname)

sys.meta_path.insert(0, BlockBuild())
from omnipath_resolver import EntityResolver, RawEntityObservation
from omnipath_resolver.canonical import votes_for, get_policy
from omnipath_resolver.observations import observation_bundle

observation = RawEntityObservation(
    entity_key="protein", entity_type="protein", namespace="uniprot",
    identifier="P04637", taxon="9606",
)
resolver = EntityResolver()
try:
    results = resolver.resolve_entity_targets({"protein": observation})
    target, = results["protein"]
    assert target.canonical_namespace == "uniprot"
    assert target.canonical_identifier == "P04637"
    assert target.taxon == "9606"
    assert target.node_id is None
finally:
    resolver.close()
votes, observed = votes_for(observation, get_policy("protein"))
query, rows = observation_bundle("protein", observation, votes, observed, "gene_protein")
assert query["target"] == 2
assert rows and rows[0]["lookup_key"].startswith(bytes([1, 2, 1]))
# Populate a bounded published index directly from the runtime wire contract.
# This exercises disk lookup, native decisions and enrichment under the blocker.
import json
import tempfile
from pathlib import Path
import lmdb
from omnipath_resolver.observations import CODES
from omnipath_resolver.index_storage import FORMAT, partition, storage_pairs

entity_id = "uniprot:P04637"
record = dict(entity_id=entity_id, kind=2, anchor=entity_id, taxon="9606",
              label="TP53", identifiers=[["uniprot", "P04637"], ["hgnc", "11998"]])
posting = dict(gene=False, products=False,
               candidates=[[1, entity_id, 2, entity_id, False, True]])
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    files = {}
    for category in ("identifiers", "entities"):
        for number in range(256):
            shard = root / category / f"{number:02x}"
            shard.mkdir(parents=True)
            with lmdb.open(str(shard), map_size=1024**2) as env:
                value = lookup_key = None
                if category == "entities" and shard.name == partition(entity_id):
                    lookup_key = entity_id.encode()
                    value = dict(record=record)
                elif category == "identifiers" and shard.name == partition("P04637"):
                    lookup_key = rows[0]["lookup_key"]
                    value = posting
                if value is not None:
                    with env.begin(write=True) as txn:
                        for physical_key, payload in storage_pairs(lookup_key, json.dumps(value).encode()):
                            txn.put(physical_key, payload)
                env.sync(True)
            files[f"{category}/{shard.name}/data.mdb"] = (shard / "data.mdb").stat().st_size
    (root / "manifest.json").write_text(json.dumps(dict(
        format=FORMAT, complete=True, namespace_codes=CODES, files=files,
        reference_fingerprint="synthetic-runtime",
    )))
    resolver = EntityResolver(root)
    try:
        target, = resolver.resolve_entity_targets({"protein": observation})["protein"]
        assert target.node_id == entity_id
        assert target.label == "TP53"
        assert target.entity_type == "protein"
        assert target.aliases["hgnc"] == ["11998"]
    finally:
        resolver.close()
assert not any(name.startswith("omnipath_build") for name in sys.modules)
"""
    environment = dict(os.environ)
    environment.pop("OMNIPATH_LIBRARY_DIR", None)
    subprocess.run([sys.executable, "-c", script], check=True, env=environment)
