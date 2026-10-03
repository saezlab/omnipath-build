"""Current-main presets are explicit; historical record folding is not reused."""

import pytest

from omnipath_subsets import network_views as main_presets
from omnipath_subsets.compatibility.record_layout import network_views
from omnipath_subsets.compatibility.record_layout.network_views import _query


class MainCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, statement, parameters):
        assert isinstance(statement, str)
        assert "information_schema.tables" in statement and "parquet_release" in statement
        assert parameters == ("aligned",)
        self.connection.statements.append(statement)

    def fetchone(self):
        return (True,)


class MainConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return MainCursor(self)


@pytest.mark.parametrize("name", ["liana", "metalinksdb", "reactions"])
@pytest.mark.parametrize("entrypoint", ["iter_records", "query"])
def test_main_layout_rejects_historical_fold_before_raw_records_or_plural_tables(
    monkeypatch, name, entrypoint
):
    def forbid(*_, **__):
        pytest.fail("Main preset queries must never enter historical release or row readers")

    for helper in ("_require_published_release", "_binary_rows", "_reaction_rows"):
        monkeypatch.setattr(_query, helper, forbid)
    connection = MainConnection()
    with pytest.raises(NotImplementedError, match="separate serving consumer"):
        if entrypoint == "iter_records":
            list(network_views.iter_records(connection, "aligned", name))
        else:
            network_views.query(connection, "aligned", name)
    assert len(connection.statements) == 1
    assert "record_json" not in connection.statements[0]


def test_explicit_main_preset_exports_reuse_exact_main_definitions_and_builders():
    assert network_views.main is main_presets
    assert network_views.main.NETWORKS is main_presets.NETWORKS
    assert network_views.main.METALINKSDB is main_presets.METALINKSDB
    assert network_views.main.LIANA is main_presets.LIANA
    assert network_views.main.REACTIONS is main_presets.REACTIONS
    assert network_views.main.apply_all is main_presets.apply_all
    assert network_views.main.register_network is main_presets.register_network
    assert network_views.main.REACTIONS.grain == "participant"
    assert network_views.main.REACTIONS.collapse_mode == "none"
