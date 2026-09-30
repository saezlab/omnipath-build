"""Invalid destinations and unbounded batches fail before any database connection."""

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
