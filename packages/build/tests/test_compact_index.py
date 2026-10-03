import base64
import json
import random

import lmdb
import pytest

from omnipath_build.reference.compact_index import (
    Codec,
    initialize,
    convert_partition,
    logical_values,
)
from omnipath_build.reference.full_index import FORMAT, MAX_RUNTIME_VALUE, Writer, read_value


def test_conversion_allows_json_larger_than_runtime_binary_budget(tmp_path):
    source, output, dictionaries = [tmp_path / name for name in ("source", "out", "dicts")]
    source.mkdir()
    dictionaries.mkdir()
    (source / "build-contract.json").write_text(
        json.dumps(dict(format=FORMAT, reference_fingerprint="large-json"))
    )
    for kind in ("entities", "identifiers"):
        (dictionaries / (kind + "-dictionary.zstd")).write_bytes(b"entity identifiers")
    initialize(source, output, dictionaries)
    entity = dict(
        record=dict(
            entity_id="large",
            kind=2,
            anchor="large",
            taxon=None,
            label="λ" * (MAX_RUNTIME_VALUE // 6 + 1),
            identifiers={},
        ),
        meta=dict(
            entity_id="large", kind=2, anchor="large", id=1, quarantined=False, reviewed=True
        ),
    )
    raw = json.dumps(entity).encode()
    assert len(raw) > MAX_RUNTIME_VALUE
    writer = Writer(source / "entities/00")
    writer.put([(b"large", raw)])
    report = writer.close()
    (source / "entity-checkpoints").mkdir()
    (source / "entity-checkpoints/00.json").write_text(json.dumps(report))
    with lmdb.open(str(source / "entities/00"), readonly=True, lock=False) as env:
        with env.begin() as txn:
            with pytest.raises(ValueError, match="runtime byte budget"):
                read_value(txn, b"large")
    result = convert_partition(source, output, "entities", "00")
    assert result["exact_round_trip_records"] == 1
    codec = Codec("entities", b"entity identifiers")
    with lmdb.open(str(output / "entities/00"), readonly=True, lock=False) as env:
        with env.begin() as txn:
            assert codec.decode(read_value(txn, b"large")) == entity


def test_compact_conversion_large_values_long_keys_and_resume(tmp_path):
    source, output, dictionaries = [
        tmp_path / name for name in ("source", "output", "dictionaries")
    ]
    source.mkdir()
    dictionaries.mkdir()
    (source / "build-contract.json").write_text(
        json.dumps(dict(format=FORMAT, reference_fingerprint="fixture"))
    )
    for kind in ("entities", "identifiers"):
        (dictionaries / (kind + "-dictionary.zstd")).write_bytes(
            b"entity identifiers protein uniprot anchor reviewed"
        )
    initialize(source, output, dictionaries)
    label = base64.b64encode(random.Random(17).randbytes(240000)).decode()
    eid = "entity-" + "x" * 900
    entity = dict(
        record=dict(
            entity_id=eid,
            kind=2,
            anchor=eid,
            taxon=None,
            label=label,
            identifiers={"uniprot": ["P00001", "P00001"], "name": ["λ\0"]},
        ),
        meta=dict(
            entity_id=eid, kind=2, anchor=eid, id=2**60 + 1, quarantined=False, reviewed=True
        ),
    )
    candidates = dict(
        gene=True,
        products=False,
        candidates=[[2**60 + 1, eid, 2, eid, False, True], [2, "second", 3, None, True, False]],
    )
    for kind, rows in [
        ("entities", [(eid.encode(), entity)]),
        (
            "identifiers",
            [
                (b"key" * 300, candidates),
                (b"empty", dict(gene=False, products=False, candidates=[])),
            ],
        ),
    ]:
        writer = Writer(source / kind / "00")
        writer.put([(key, json.dumps(obj).encode()) for key, obj in rows])
        report = writer.close()
        folder = source / (
            "entity-checkpoints" if kind == "entities" else "identifiers-checkpoints"
        )
        folder.mkdir()
        (folder / "00.json").write_text(json.dumps(report))
        compact = convert_partition(source, output, kind, "00")
        assert compact["records"] == compact["exact_round_trip_records"] == len(rows)
        assert compact["long_keys"] == 1
        assert convert_partition(source, output, kind, "00") == compact
        codec = Codec(kind, (dictionaries / (kind + "-dictionary.zstd")).read_bytes())
        with lmdb.open(str(output / kind / "00"), readonly=True, lock=False) as env:
            with env.begin() as txn:
                actual = {key: codec.decode(value) for key, value in logical_values(txn)}
                assert actual == dict(rows)
        # Simulate interruption after the atomic directory rename but before checkpoint publication.
        (output / "checkpoints" / kind / "00.json").unlink()
        assert convert_partition(source, output, kind, "00")["records"] == len(rows)
    with pytest.raises(ValueError, match="schema"):
        Codec("identifiers", b"").encode(
            dict(candidates=[], gene=False, products=False, surprise=True)
        )
    with pytest.raises(ValueError, match="codec"):
        Codec("identifiers", b"").decode(b"unknown")
