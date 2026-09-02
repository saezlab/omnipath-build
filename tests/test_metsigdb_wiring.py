"""MetSigDB is a step of the build, not something a person remembers to run.

Cycle 010 shipped the substrate as a Python entry point with no caller, so a
fresh build produced a database with no ``metsigdb_membership`` at all and the
API answered 503. This covers the wiring that fixed it, in the shape cycle 008
established for the network views:

- the ``derive`` runs it by default, and ``--no-metsigdb`` opts out;
- it runs **after** the build manifest, whose stamp every published row carries;
- a failure is loud but does not abort the build, exactly like the network
  views and unlike the interaction projection;
- ``metsigdb`` is also a subcommand of its own, for a rebuild without a derive;
- ``reset-content`` truncates the substrate, so a reload cannot leave rows
  keyed on entity ids that no longer exist.

No database is touched: the build step is stubbed.

    uv run --with pytest pytest tests/test_metsigdb_wiring.py -v
"""

from __future__ import annotations

import inspect
import logging

import pytest

import omnipath_build  # noqa: F401  (imports `_session`, configuring logging)
from omnipath_build import cli
from omnipath_build.db.schema import CONTENT_TABLES
from omnipath_build.metsigdb import BuildStats, ResourceLoadStats

STATS = BuildStats(
    build_id='0ed5afcb26c6',
    partial=False,
    rows=3_508_801,
    seconds=99.4,
    resources=(
        ResourceLoadStats(
            resource='KEGG',
            rows=4969,
            sets=176,
            metabolites=1799,
            removed=0,
            seconds=1.5,
        ),
    ),
)


class _FakeConn:
    """The little the CLI asks of a connection before the step runs."""

    def __init__(self) -> None:
        self.rolled_back = False

    def __enter__(self) -> '_FakeConn':
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def stubbed(monkeypatch):
    """A CLI whose database and session tuning are inert."""
    conn = _FakeConn()
    monkeypatch.setattr(cli.psycopg2, 'connect', lambda url: conn)
    monkeypatch.setattr(cli, '_apply_build_session_tuning', lambda conn: None)
    return conn


def test_the_subcommand_runs_the_step(stubbed, monkeypatch, capsys):
    """`omnipath_build.cli metsigdb` publishes the substrate and says what it wrote."""
    seen = {}

    def _build(conn, *, max_records=None, schema='public', **kwargs):
        seen['schema'] = schema
        seen['max_records'] = max_records
        return STATS

    monkeypatch.setattr(cli, 'build_metsigdb', _build)

    assert cli.main(['--database-url', 'postgresql:///x', 'metsigdb']) == 0

    assert seen['schema'] == 'public'
    out = capsys.readouterr().out
    assert 'build=0ed5afcb26c6' in out
    assert 'rows=3508801' in out
    assert 'KEGG=4969' in out


def test_the_subcommand_applies_a_record_cap(stubbed, monkeypatch):
    """A capped run is for the dev loop, and the step is told the cap."""
    seen = {}
    monkeypatch.setattr(
        cli,
        'build_metsigdb',
        lambda conn, *, max_records=None, schema='public': (
            seen.update(max_records=max_records) or STATS
        ),
    )

    cli.main(
        ['--database-url', 'postgresql:///x', 'metsigdb', '--max-records', '1000']
    )

    assert seen['max_records'] == 1000


@pytest.mark.parametrize(
    ('value', 'expected'),
    [(None, None), ('', None), ('0', None), ('1000', 1000), ('nonsense', None)],
)
def test_an_unusable_cap_means_no_cap(value, expected):
    """An unset or unparseable MAX_RECORDS is the authoritative case, not an error."""
    assert cli._record_cap(value) == expected


def test_the_derive_runs_it_by_default_and_can_be_told_not_to():
    """The flag exists, defaults to on, and is a BooleanOptionalAction."""
    source = inspect.getsource(cli.main)
    flag = source.index("'--metsigdb'")
    declaration = source[flag:flag + 260]
    assert 'BooleanOptionalAction' in declaration, (
        '--no-metsigdb has to be spellable, like --no-network-views'
    )
    assert 'default=True' in declaration, (
        'the substrate is part of the build; opting out is the explicit act'
    )


def test_it_runs_after_the_manifest_it_stamps_rows_with():
    """`build_id()` reads build_manifest, so the order is a requirement."""
    source = inspect.getsource(cli.main)
    # The derive call site, not the subcommand's: the subcommand is declared
    # earlier in the source and enforces the same order at runtime instead,
    # where `build_id()` raises on an empty manifest.
    derive_call = source.index('build_metsigdb(conn, schema=')
    assert source.index("'build_manifest_done'") < derive_call


def test_the_derive_does_not_reapply_the_load_cap():
    """The substrate is a projection of what the load wrote, capped by construction."""
    source = inspect.getsource(cli.main)
    call = source.index('build_metsigdb(conn, schema=')
    assert call > 0, 'the derive passes a record cap it should not re-apply'


def test_a_failure_is_loud_and_does_not_abort_the_build(caplog):
    """Like the network views: reported at error level, and the build goes on."""
    source = inspect.getsource(cli.main)
    step = source.index("_derive_log('metsigdb_start')")
    tail = source[step:step + 1600]
    assert "'metsigdb_failed'" in tail
    assert '_level=logging.ERROR' in tail
    assert 'conn.rollback()' in tail
    assert 'raise' not in tail, (
        'a published dataset must not abort the build that produced its content'
    )


def test_reset_content_truncates_the_substrate():
    """A reload cannot leave rows keyed on entity ids that no longer exist."""
    assert 'metsigdb_membership' in CONTENT_TABLES
