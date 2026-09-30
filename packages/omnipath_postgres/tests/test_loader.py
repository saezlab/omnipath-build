"""Invalid destinations and resource limits fail before reading or connecting."""

import pytest

from omnipath_postgres import loader


@pytest.mark.parametrize(
    "schema", ["public", "information_schema", "pg_temp", "", "a-b", "x" * 64, None]
)
def test_rejects_invalid_destination_before_connecting(monkeypatch, schema):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid parameters must not connect or read a release")

    monkeypatch.setattr(loader, "read_release", unexpected)
    with pytest.raises(ValueError, match="schema"):
        loader.load_release("missing", "missing", "unused", schema=schema)


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_rejects_invalid_batch_before_connecting(monkeypatch, batch_size):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid parameters must not connect or read a release")

    monkeypatch.setattr(loader, "read_release", unexpected)
    with pytest.raises(ValueError, match="batch_size"):
        loader.load_release("missing", "missing", "unused", batch_size=batch_size)


@pytest.mark.parametrize("duckdb_threads", [0, -1, True, 1.5, None])
def test_rejects_invalid_threads_before_connecting(monkeypatch, duckdb_threads):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid parameters must not connect or read a release")

    monkeypatch.setattr(loader, "read_release", unexpected)
    with pytest.raises(ValueError, match="duckdb_threads"):
        loader.load_release("missing", "missing", "unused", duckdb_threads=duckdb_threads)


@pytest.mark.parametrize(
    "memory_limit", [None, "", 0, True, "0MB", "512", "unlimited", "-1MB", "512MB; SELECT 1"]
)
def test_rejects_invalid_memory_limit_before_connecting(monkeypatch, memory_limit):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid parameters must not connect or read a release")

    monkeypatch.setattr(loader, "read_release", unexpected)
    with pytest.raises(ValueError, match="memory_limit"):
        loader.load_release("missing", "missing", "unused", memory_limit=memory_limit)
