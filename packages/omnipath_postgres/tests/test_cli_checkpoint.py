"""Checkpoint and finish options cannot silently ignore load/audit arguments."""

import pytest

from omnipath_postgres import cli


@pytest.mark.parametrize(
    "extra",
    [
        ["manifest.json"],
        ["--data-root", "data"],
        ["--validate-source-records"],
        ["--batch-size", "1"],
        ["--duckdb-threads", "1"],
        ["--memory-limit", "1GB"],
        ["--temp-directory", "tmp"],
        ["--checkpoint-base"],
    ],
)
def test_finish_rejects_inapplicable_options_without_connecting(monkeypatch, extra):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid CLI selection must not connect")

    monkeypatch.setattr(cli, "finish_release", unexpected)
    monkeypatch.setattr(cli, "load_release", unexpected)
    with pytest.raises(SystemExit) as error:
        cli.main(["--finish", "--database-url", "unused", *extra])
    assert error.value.code == 2
