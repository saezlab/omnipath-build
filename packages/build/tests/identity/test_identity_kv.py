"""Hub and decision kv writers: key order, value codec, sharding, parity with the Parquet rows."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "resolver" / "tests"))
from identity_snapshot import write_hub_index  # noqa: E402

from omnipath_build.identity import build_hub_kv_dir, build_identity_kv  # noqa: E402
from omnipath_resolver import identity_kv  # noqa: E402
from omnipath_resolver.identity_kv import HubKv, KvMissing  # noqa: E402

TRICKY = [
    "a", "A", "a b", "a-b", "a:b", "a_b", "ab", "é", "ü:1", "日本", "Z9", "z9", "\x01x", "~", "CHEBI:1",
    "CHEBI:10", "CHEBI:2", "x" * 300,
]  # fmt: skip


def test_value_codec_prefix_and_compression():
    small, large = ["a", "b"], [["chebi", "x" * 40]] * 50
    raw, packed = identity_kv.pack(small), identity_kv.pack(large)
    assert raw[:1] == identity_kv.RAW and packed[:1] == identity_kv.ZSTD
    assert identity_kv.unpack(raw) == small and identity_kv.unpack(packed) == large
    with pytest.raises(ValueError):
        identity_kv.unpack(b"\x07abc")


def test_duckdb_varchar_order_is_lmdb_byte_order():
    c = duckdb.connect()
    c.execute("CREATE TABLE t(ns VARCHAR, identifier VARCHAR)")
    c.executemany("INSERT INTO t VALUES (?, ?)", [(ns, i) for ns in ("a", "a-b", "ab", "é") for i in TRICKY])
    rows = c.execute("SELECT ns, identifier FROM t ORDER BY ns, identifier").fetchall()
    keys = [ns.encode() + b"\0" + i.encode() for ns, i in rows]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    values = [r[0] for r in c.execute("SELECT x FROM (SELECT unnest(?) x) ORDER BY x", [TRICKY]).fetchall()]
    assert [v.encode() for v in values] == sorted(v.encode() for v in TRICKY)


def hub(directory: Path, records):
    write_hub_index(directory, "chebi", records)
    return directory


def tricky_records():
    records = {}
    for n, local in enumerate(TRICKY):
        rows = [("chebi", local), ("name", f"name {n}"), ("synonym", "x" * 400 if n % 3 == 0 else "s")]
        rows += [("cas", f"{n}-0-0"), ("hmdb", TRICKY[(n + 1) % len(TRICKY)])]
        if n == 2:
            rows.append(("kegg", "y" * 600))  # too long for an LMDB key: counted, not stored
        records[local] = (None if n % 2 else "9606", bool(n % 2), rows)
    records["no-names"] = (None, False, [])  # a record without any by_record row
    return records


@pytest.mark.parametrize("shards,workers", [(1, 1), (16, 2)])
def test_hub_kv_holds_every_parquet_row(tmp_path, shards, workers):
    directory = hub(tmp_path / "chebi" / "0123456789ab", tricky_records())
    manifest = build_hub_kv_dir(
        directory, id_shards=shards, rec_shards=shards, memory="256MB", threads=1, workers=workers, min_free_gib=0.01
    )
    assert manifest["totals"]["rec"]["keys"] == len(tricky_records())
    assert manifest["totals"]["id"]["skipped_long_keys"] > 0  # the 300-character identifier
    assert build_hub_kv_dir(directory, min_free_gib=0.01) == manifest  # idempotent
    assert not (directory / "kv.building").exists()
    kv = HubKv("chebi", directory)
    try:
        by_id = pq.read_table(list(directory.glob("by_id/part=*/data.parquet"))[0].parent.parent).to_pylist()
        expected = {}
        for row in by_id:
            expected.setdefault((row["ns"], row["identifier"]), []).append((row["local_id"], row["tag"]))
        pairs = [p for p in expected if identity_kv.id_key(*p) is not None]
        found = kv.ids(pairs + [("chebi", "nope")])
        assert set(found) == set(pairs)
        assert all(sorted(found[p]) == sorted(expected[p]) for p in pairs)
        records = {r["local_id"]: r for r in pq.read_table(directory / "records.parquet").to_pylist()}
        by_record = {}
        for path in directory.glob("by_record/part=*/data.parquet"):
            for r in pq.read_table(path).to_pylist():
                by_record.setdefault(r["local_id"], []).append((r["source_type"], r["value"]))
        usable = [i for i in records if identity_kv.rec_key("chebi", i)]
        heads, rows = kv.heads(usable), kv.rows(usable)
        for local in usable:
            r = records[local]
            assert heads[local] == (r["taxon"], r["anchor"], r["anchor_count"], r["reviewed"])
            assert sorted(rows[local]) == sorted(by_record.get(local, []))
        assert kv.heads(["missing"]) == {} and kv.ids([("chebi", "x" * 600)]) == {}
        if shards == 16:  # a key reads only its own shard
            kv.close()
            kv = HubKv("chebi", directory)
            kv.ids([("chebi", "CHEBI:1")])
            assert {s for _, s in kv.opened()} == {identity_kv.shard_of("CHEBI:1", 16)}
    finally:
        kv.close()


def test_hub_kv_refuses_a_changed_index(tmp_path):
    directory = hub(tmp_path / "chebi" / "0123456789ab", tricky_records())
    build_hub_kv_dir(directory, memory="256MB", threads=1, min_free_gib=0.01)
    HubKv("chebi", directory).close()
    (directory / "manifest.json").write_text('{"hub": "chebi", "changed": true}')
    with pytest.raises(KvMissing, match="different index"):
        HubKv("chebi", directory)
    with pytest.raises(KvMissing, match="build-hub-kv"):
        HubKv("chebi", tmp_path)


def test_writer_rejects_keys_out_of_order(tmp_path):
    from omnipath_build.identity.hubkv import Writer

    import pyarrow as pa

    writer = Writer(tmp_path / "w", "id", 0)
    writer.add(pa.array(["b"]), pa.array(["x"]))
    with pytest.raises(AssertionError, match="not ascending"):
        writer.add(pa.array(["a"]), pa.array(["x"]))
    with pytest.raises(AssertionError, match="order"):  # out of order inside one batch
        writer.add(pa.array(["d", "c"]), pa.array(["x", "y"]))
    writer.env.close()


def test_values_round_trip_separators():
    from omnipath_resolver.identity_kv import decode_head, decode_ids, decode_rows

    assert decode_ids("a\x1fnative\x1eb\x1fclaim".encode()) == [("a", "native"), ("b", "claim")]
    assert decode_head("\x1f\x1f0\x1f0".encode()) == (None, None, 0, False)
    value = "9606\x1funiprot:P1\x1f1\x1f1\x1ename\x1fp53\x1egenesymbol\x1fTP53".encode()
    assert decode_head(value) == ("9606", "uniprot:P1", 1, True)
    assert decode_rows(value) == [("name", "p53"), ("genesymbol", "TP53")]
    assert decode_rows("\x1f\x1f0\x1f0".encode()) == []


def test_identity_kv_is_complete_and_optional_candidates(tmp_path):
    from identity_snapshot import build_snapshot

    identity = build_snapshot(tmp_path)
    manifest = build_identity_kv(identity, min_free_gib=0.001)
    assert build_identity_kv(identity, min_free_gib=0.001) == manifest  # idempotent
    assert set(manifest["counts"]) == set(identity_kv.DECISION_DBS)
    assert manifest["counts"]["exc"] == pq.read_table(identity / "exceptions.parquet").num_rows
    assert manifest["counts"]["cand"] > 0
    (identity / "record_candidates.parquet").unlink()  # an older snapshot: empty db
    assert build_identity_kv(identity, force=True, min_free_gib=0.001)["counts"]["cand"] == 0
    kv = identity_kv.DecisionsKv(identity, manifest["fingerprint"])
    try:
        assert kv.get("exc", ["chebi:CHEBI:99999", "nope"]) == {
            "chebi:CHEBI:99999": ["chebi:CHEBI:99999", "quarantined", True]
        }
        assert dict(kv.items("lipid"))
        with pytest.raises(KvMissing, match="another fingerprint"):
            identity_kv.DecisionsKv(identity, "other")
    finally:
        kv.close()
