"""Regressions for atomic publication, reference pinning, evidence and crash recovery."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from omnipath_build.canonical.library import build_library, pin_library
from omnipath_build.hubs.export import export_hubs
from omnipath_build.hubs.writer import HubParquetWriter
from omnipath_build.locking import BuildLock
from omnipath_build.resolver import EntityResolver
from omnipath_build.writer import ParquetWriter
from test_partition_contract import entity, run_rows


def test_failed_hub_refresh_preserves_published_bytes(tmp_path, monkeypatch):
    path = tmp_path / "chebi.parquet"
    writer = HubParquetWriter(path)
    writer.add_identity("chebi", "original", "0", "chebi")
    writer.close()
    published = path.read_bytes()

    def fail(writer):
        writer.batch_size = 1  # Force a real partial Parquet before the source fails.
        writer.add_identity("chebi", "partial", "0", "chebi")
        assert path.read_bytes() == published
        raise RuntimeError("upstream interrupted")

    monkeypatch.setitem(
        __import__("omnipath_build.hubs.export", fromlist=["HUB_EMITTERS"]).HUB_EMITTERS,
        "chebi",
        fail,
    )
    with pytest.raises(RuntimeError, match="upstream"):
        export_hubs(tmp_path, hubs=["chebi"], include_dictionaries=False, build_library=False)
    assert path.read_bytes() == published
    assert not list(tmp_path.glob(".*.tmp"))


def test_lock_recovers_after_killed_owner(tmp_path):
    path = tmp_path / ".version.lock"
    code = 'from omnipath_build.locking import BuildLock; import sys,time; lock=BuildLock(sys.argv[1]).__enter__(); print("ready",flush=True); time.sleep(60)'
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(FileExistsError):
            with BuildLock(path):
                pass
    finally:
        process.kill()
        process.wait(timeout=5)
    with BuildLock(path):
        pass
    assert path.exists()  # Keep one shared inode for all future contenders.


def test_relation_taxon_keeps_assertions_and_projects_consensus(tmp_path):
    def relation(taxon):
        return {
            "subject": entity("P1"),
            "predicate": "affects",
            "object": entity("P2"),
            "annotations": [{"term": "in_taxon", "value": f"NCBITaxon:{taxon}"}],
        }

    _, relations, _ = run_rows(
        tmp_path / "mixed", [("human", relation("9606")), ("mouse", relation("10090"))], 1
    )
    (row,) = relations
    assert row["taxon"] == ""
    assert row["evidence_count"] == 2
    assert {
        a["value"] for e in row["evidence"] for a in e["annotations"] if a["term"] == "in_taxon"
    } == {"NCBITaxon:9606", "NCBITaxon:10090"}
    _, relations, _ = run_rows(tmp_path / "human", [("human", relation("9606"))], 1)
    assert relations[0]["taxon"] == "9606"


def test_reference_refresh_pins_matcher_and_finalizer(tmp_path, monkeypatch):
    from library_fixture import build_fixture_library
    from omnipath_build.reference import build_reference, finalize_source_reference
    from omnipath_build.reference import direct_compact_index
    import shutil

    fixture = build_fixture_library(tmp_path / "fixture")
    # Exercise real publication/locking/failure paths with an already-built
    # graph. The fixture itself goes through the complete production builder.
    monkeypatch.setattr(
        build_reference.Build, "__init__", lambda self, args: setattr(self, "args", args)
    )
    monkeypatch.setattr(
        build_reference.Build, "run", lambda self: self.args.output.mkdir(parents=True)
    )
    monkeypatch.setattr(
        finalize_source_reference, "finalize", lambda src, dst, *_: dst.mkdir(parents=True)
    )
    monkeypatch.setattr(
        direct_compact_index,
        "build_direct_compact",
        lambda assigned, output, **kwargs: shutil.copytree(fixture, output),
    )
    library = tmp_path / "library"
    first = build_library(tmp_path / "hubs", library)
    resolver = EntityResolver(library)
    writer = ParquetWriter(tmp_path / "resource", library_dir=library)
    original = (first.library_dir / "manifest.json").read_bytes()
    try:
        second = build_library(tmp_path / "hubs", library)
        assert first.library_dir != second.library_dir
        assert pin_library(library) == second.library_dir.resolve()
        assert resolver.library_dir == writer.library_dir == first.library_dir.resolve()
        assert (first.library_dir / "manifest.json").read_bytes() == original

        def fail(*args, **kwargs):
            raise RuntimeError("graph failed")

        monkeypatch.setattr(build_reference.Build, "run", fail)
        with pytest.raises(RuntimeError, match="graph failed"):
            build_library(tmp_path / "hubs", library)
        assert pin_library(library) == second.library_dir.resolve()
        assert not list((library / ".generations").glob(".*.tmp"))
        assert not list(library.rglob("*.sqlite"))
    finally:
        resolver.close()
        writer.abort()


def test_resolution_keys_are_on_disk_and_shard_stats_deduplicate(tmp_path):
    from omnipath_build.silver import RawEntityObservation

    resolver = EntityResolver()
    try:
        observed = {"key": RawEntityObservation("key", "protein", "uniprot", "MISSING")}
        resolver.resolve_entities(observed)
        resolver.resolve_entities(observed)
        path = resolver.export_resolution_keys(tmp_path / "keys.parquet")
        assert pq.read_metadata(path).num_rows == 1
        assert resolver.resolution_stats()["input_entities"] == 1
        writer = ParquetWriter(tmp_path / "final")
        try:
            summary = writer.resolution_summary([path, path])
            assert summary["input_entities"] == 1
            assert summary["unresolved_entities"] == 1
        finally:
            writer.abort()
    finally:
        resolver.close()


def test_resume_invalidates_imported_normalizer_and_grammar(tmp_path, monkeypatch):
    from omnipath_build.reference.build_reference import Build, CHEMICAL
    from omnipath_build.reference import goslin_cache
    from omnipath_build.canonical import identifiers
    import pyarrow as pa

    hubs = tmp_path / "hubs"
    hubs.mkdir()
    for name in CHEMICAL:
        pq.write_table(pa.table({"source_id": ["x"]}), hubs / f"{name}.parquet")
    binary = tmp_path / "components"
    binary.write_bytes(b"binary")
    args = SimpleNamespace(
        output=str(tmp_path / "out"), hubs=str(hubs), components=str(binary), domain="chemical"
    )
    monkeypatch.setattr(goslin_cache, "cache_fingerprint", lambda _: "grammar-a")
    Build(args)
    frozen = Path(args.output) / "inputs" / f"{CHEMICAL[0]}.parquet"
    original = frozen.read_bytes()
    frozen.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(RuntimeError, match="Input snapshot content"):
        Build(args)
    frozen.write_bytes(original)
    monkeypatch.setattr(goslin_cache, "cache_fingerprint", lambda _: "grammar-b")
    with pytest.raises(RuntimeError, match="Input/code changed"):
        Build(args)
    monkeypatch.setattr(goslin_cache, "cache_fingerprint", lambda _: "grammar-a")
    normalizer = tmp_path / "changed_normalizer.py"
    normalizer.write_text("changed normalizer semantics")
    monkeypatch.setattr(identifiers, "__file__", str(normalizer))
    with pytest.raises(RuntimeError, match="Input/code changed"):
        Build(args)


def test_discovery_reports_broken_imports(tmp_path, monkeypatch):
    from omnipath_build import discovery

    original = discovery.importlib.import_module

    def import_module(name):
        if name == "fixture_inputs":
            return SimpleNamespace(__path__=[str(tmp_path)])
        if name == "fixture_inputs.broken":
            raise RuntimeError("broken module initialization")
        return original(name)

    monkeypatch.setattr(discovery, "setup_pypath_cache", lambda _: tmp_path)
    monkeypatch.setattr(discovery.importlib, "import_module", import_module)
    monkeypatch.setattr(
        discovery.pkgutil,
        "walk_packages",
        lambda *_: [SimpleNamespace(name="fixture_inputs.broken")],
    )
    with pytest.raises(discovery.DiscoveryError, match="broken module initialization"):
        discovery._collect_sources(
            inputs_package="fixture_inputs", cache_dir=tmp_path, source_filter="broken"
        )
    from unittest.mock import patch

    with patch.object(discovery.logger, "warning") as warning:
        discovery._collect_sources(inputs_package="fixture_inputs", cache_dir=tmp_path)
    assert warning.call_args.args[1] == "fixture_inputs.broken"
    assert warning.call_args.kwargs["exc_info"] is True
