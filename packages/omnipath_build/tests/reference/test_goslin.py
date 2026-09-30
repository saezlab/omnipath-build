from omnipath_build.reference.goslin_identifiers import normalize


def test_goslin_identity_precision_and_chain_order():
    assert normalize("PC 18:1_16:0")["goslin"] == normalize("PC 16:0_18:1")["goslin"]
    assert normalize("PC(16:0/18:1)")["goslin"] == normalize("PC 16:0/18:1")["goslin"]
    assert normalize("PC 16:0/18:1")["goslin"] != normalize("PC 18:1/16:0")["goslin"]
    assert normalize("PC 34:1")["goslin"] != normalize("PC 16:0_18:1")["goslin"]
    assert normalize("PC 16:0_18:1")["goslin"] != normalize("PC 16:0/18:1")["goslin"]
    assert normalize("5-methyl-octadecanoic acid")["goslin"] == "full_structure:FA 18:0;5Me"
    assert normalize("water")["goslin"] is None
    assert normalize("PC")["goslin"] is None
    assert normalize("PC 16:0/18:1[M+H]1+")["status"] == "adduct_or_isotope"


def test_native_abbreviation_lists(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from omnipath_build.reference.goslin_identifiers import build

    hub = tmp_path / "swiss.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "hub_id": "SLM:1",
                    "source_type": "lipid_shorthand",
                    "source_id": "Cer(d18:0/24:0(2OH)) | Cer(d18:0/24:0-2OH)",
                },
            ]
        ),
        hub,
    )
    out = tmp_path / "result"
    build({"swisslipids": hub}, out, workers=1, memory="256MB")
    claims = pq.read_table(out / "claims.parquet").to_pylist()
    normalized = pq.read_table(out / "normalized.parquet").to_pylist()
    assert len(normalized) == 2
    assert all(r["status"] == "parsed" for r in normalized)
    # Goslin reports different precision for these native alternatives.
    # Retain the more informative descriptor rather than forcing equivalence.
    assert len(claims) == 1
    assert claims[0]["goslin"] == normalize("Cer(d18:0/24:0(2OH))")["goslin"]
    assert all(r["record_id"] == "swisslipids:SLM:1" for r in claims)


def test_cache_reused_across_builds_and_only_new_names_parsed(tmp_path, monkeypatch):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from omnipath_build.reference import goslin_identifiers as goslin

    hub = tmp_path / "hub.parquet"
    cache = tmp_path / "shared-cache"

    def write_names(names):
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"hub_id": str(i), "source_type": "name", "source_id": name}
                    for i, name in enumerate(names)
                ]
            ),
            hub,
        )

    def summary(out):
        return json.loads((out / "summary.json").read_text())

    write_names(["PC 16:0/18:1", "water"])
    first = tmp_path / "first"
    goslin.build({"hmdb": hub}, first, workers=1, memory="256MB", cache_dir=cache)
    assert summary(first)["parsed_names"] == 2
    assert summary(first)["cached_names"] == 0

    second = tmp_path / "second"
    with monkeypatch.context() as patch:

        def forbidden(*_):
            raise AssertionError("A cached name was sent to the parser")

        patch.setattr(goslin, "parse_batch", forbidden)
        goslin.build({"hmdb": hub}, second, workers=1, memory="256MB", cache_dir=cache)
    assert summary(second)["parsed_names"] == 0
    assert summary(second)["cached_names"] == 2  # failed parses are cached too
    assert (
        pq.read_table(first / "claims.parquet").to_pylist()
        == pq.read_table(second / "claims.parquet").to_pylist()
    )

    write_names(["PC 16:0/18:1", "water", "PE 36:2"])
    third = tmp_path / "third"
    goslin.build({"hmdb": hub}, third, workers=1, memory="256MB", cache_dir=cache)
    assert summary(third)["parsed_names"] == 1
    assert summary(third)["cached_names"] == 2


def test_cache_persists_batches_and_invalidates_changed_normalizer(tmp_path):
    from omnipath_build.reference.goslin_cache import NormalizationCache

    result = normalize("PC 34:1")
    with NormalizationCache(tmp_path, normalize) as cache:
        first_path = cache.path
        cache.store([result])
    with NormalizationCache(tmp_path, normalize) as cache:
        assert cache.lookup(["PC 34:1"]) == [result]

    def changed_normalizer(name):
        return normalize(name)

    with NormalizationCache(tmp_path, changed_normalizer) as cache:
        assert cache.path != first_path
        assert cache.lookup(["PC 34:1"]) == []
