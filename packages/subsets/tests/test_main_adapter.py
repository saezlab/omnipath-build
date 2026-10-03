"""Main product adapter publication boundaries without server credentials."""

from copy import deepcopy

import pytest

from omnipath_subsets import runner as main_adapter
from omnipath_subsets.build import BuildResult
from omnipath_subsets import scientific as pipeline


class Database:
    def __init__(self):
        self.state = {
            "identity": ("release", "digest"),
            "phases": {"base": {}, "derived": {}},
            "metadata": {},
            "contents": {},
            "status": "derived",
        }
        self.owner = None
        self.connections = []

    def connect(self, _):
        connection = Connection(self)
        self.connections.append(connection)
        return connection


class Connection:
    def __init__(self, database):
        self.database = database
        self._pending = None
        self.commits = self.rollbacks = 0
        self.closed = False
        self.statements = []

    @property
    def pending(self):
        if self._pending is None:
            self._pending = deepcopy(self.database.state)
        return self._pending

    def cursor(self):
        return Cursor(self)

    def commit(self):
        if self._pending is not None:
            self.database.state = deepcopy(self._pending)
        self._pending = None
        self.commits += 1

    def rollback(self):
        self._pending = None
        self.rollbacks += 1

    def close(self):
        self.closed = True
        self._pending = None
        if self.database.owner is self:
            self.database.owner = None


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, statement, parameters=None):
        text = str(statement)
        connection = self.connection
        connection.statements.append((text, parameters))
        if "pg_try_advisory_lock" in text:
            locked = connection.database.owner is None
            if locked:
                connection.database.owner = connection
            self.rows = [(locked,)]
        elif "SELECT version,manifest_sha256" in text:
            self.rows = [connection.pending["identity"]]
        elif "SELECT phase FROM" in text:
            self.rows = [(phase,) for phase in connection.pending["phases"]]
        elif "subset_build_metadata" in text and "INSERT INTO" in text:
            product, release, digest, stats = parameters
            connection.pending["metadata"][product] = (release, digest, deepcopy(stats.adapted))
        elif "parquet_phase" in text and "INSERT INTO" in text:
            product, seconds, outcome = parameters
            connection.pending["phases"][product] = (seconds, deepcopy(outcome.adapted))
        elif "UPDATE" in text and "parquet_release" in text:
            product, seconds, products, _ = parameters
            connection.pending["status"] = (
                "complete" if set(products).issubset(connection.pending["phases"]) else product
            )
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


@pytest.fixture
def environment(monkeypatch):
    database = Database()
    monkeypatch.setattr(main_adapter.psycopg2, "connect", database.connect)
    tuning = []
    monkeypatch.setattr(
        main_adapter,
        "_fresh_worker",
        lambda connection, products: tuning.append((connection, tuple(products))),
    )
    return database, tuning


def builder(*, failed_product=None, failure=RuntimeError, change_identity=False):
    def run(connection, product, **kwargs):
        # The imported scientific builder may commit internally. Its production
        # adapter suppresses those commits until tables+publication markers agree.
        wrapped = pipeline.CommitDeferredConnection(connection)
        current = connection.pending["contents"].get(product, 0)
        connection.pending["contents"][product] = current + 1
        wrapped.commit()
        assert connection.commits == 0
        if change_identity:
            connection.pending["identity"] = ("changed", "changed-digest")
        if failed_product == product:
            raise failure("deliberate product interruption")
        return {"phase_seconds": 0.5, "result": {"rows": current + 1}}

    return run


def test_checkpoint_callbacks_observe_durable_products_metadata_and_held_importer_lock(
    environment, monkeypatch
):
    database, tuning = environment
    monkeypatch.setattr(main_adapter, "run_product", builder())
    observed = []

    def committed(product, result):
        assert database.owner is not None and not database.owner.closed
        assert database.state["contents"][product] == 1
        assert database.state["metadata"][product][:2] == ("release", "digest")
        assert product in database.state["phases"]
        observed.append((product, tuple(result.products)))
        result.products[product]["rows"] = -100

    result = main_adapter.build_subsets(
        "unused",
        "target",
        products=("metsigdb", "cosmos"),
        checkpoint_products=True,
        on_product_committed=committed,
    )
    assert isinstance(result, BuildResult)
    assert result.release == "release" and result.manifest_sha256 == "digest"
    assert observed == [("metsigdb", ("metsigdb",)), ("cosmos", ("metsigdb", "cosmos"))]
    assert result.products["metsigdb"]["rows"] == 1
    assert len(database.connections) == 3
    assert database.connections[0].statements[0][1] == ("omnipath:main-parquet:target",)
    assert tuning == [
        (database.connections[1], ("metsigdb",)),
        (database.connections[2], ("cosmos",)),
    ]
    assert all(connection.closed for connection in database.connections)
    assert database.owner is None


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_checkpoint_failure_retains_previous_product_and_rolls_back_only_current(
    environment, monkeypatch, failure
):
    database, _ = environment
    monkeypatch.setattr(
        main_adapter, "run_product", builder(failed_product="cosmos", failure=failure)
    )
    with pytest.raises(failure, match="deliberate"):
        main_adapter.build_subsets(
            "unused", "target", products=("metsigdb", "cosmos"), checkpoint_products=True
        )
    assert database.state["contents"] == {"metsigdb": 1}
    assert set(database.state["metadata"]) == {"metsigdb"}
    assert "cosmos" not in database.state["phases"]
    assert database.connections[-1].rollbacks == 1
    assert database.owner is None


def test_default_mode_rolls_back_all_selected_products_on_later_failure(environment, monkeypatch):
    database, tuning = environment
    monkeypatch.setattr(main_adapter, "run_product", builder(failed_product="cosmos"))
    with pytest.raises(RuntimeError, match="deliberate"):
        main_adapter.build_subsets(
            "unused", "target", products=("metsigdb", "cosmos"), checkpoint_products=False
        )
    assert database.state["contents"] == {}
    assert database.state["metadata"] == {}
    assert set(database.state["phases"]) == {"base", "derived"}
    assert len(tuning) == 1 and tuning[0][1] == ("metsigdb", "cosmos")


def test_callback_failure_preserves_current_commit_and_does_not_start_next_product(
    environment, monkeypatch
):
    database, _ = environment
    monkeypatch.setattr(main_adapter, "run_product", builder())

    def failed(*_):
        raise OSError("observer persistence failed")

    with pytest.raises(OSError, match="observer"):
        main_adapter.build_subsets(
            "unused",
            "target",
            products=("metsigdb", "cosmos"),
            checkpoint_products=True,
            on_product_committed=failed,
        )
    assert database.state["contents"] == {"metsigdb": 1}
    assert set(database.state["metadata"]) == {"metsigdb"}
    assert len(database.connections) == 2


def test_selected_rebuild_updates_only_explicit_product_and_keeps_other_checkpoints(
    environment, monkeypatch
):
    database, _ = environment
    database.state["contents"] = {"metsigdb": 10, "network_views": 20, "cosmos": 30}
    database.state["phases"].update(
        {product: "old phase" for product in database.state["contents"]}
    )
    database.state["metadata"] = {product: "old metadata" for product in database.state["contents"]}
    monkeypatch.setattr(main_adapter, "run_product", builder())
    result = main_adapter.build_subsets(
        "unused", "target", products=("cosmos",), checkpoint_products=True
    )
    assert result.products == {"cosmos": {"rows": 31}}
    assert database.state["contents"] == {"metsigdb": 10, "network_views": 20, "cosmos": 31}
    assert database.state["metadata"]["metsigdb"] == "old metadata"
    assert database.state["phases"]["network_views"] == "old phase"
    assert database.state["status"] == "complete"


def test_release_change_between_transactions_stops_before_next_product(environment, monkeypatch):
    database, _ = environment
    monkeypatch.setattr(main_adapter, "run_product", builder())

    def mutate(*_):
        database.state["identity"] = ("changed", "changed-digest")

    with pytest.raises(ValueError, match="release changed"):
        main_adapter.build_subsets(
            "unused",
            "target",
            products=("metsigdb", "cosmos"),
            checkpoint_products=True,
            on_product_committed=mutate,
        )
    assert database.state["contents"] == {"metsigdb": 1}
    assert database.state["metadata"]["metsigdb"][:2] == ("release", "digest")


def test_product_mutating_release_identity_rolls_back_its_tables_and_metadata(
    environment, monkeypatch
):
    database, _ = environment
    monkeypatch.setattr(main_adapter, "run_product", builder(change_identity=True))
    with pytest.raises(ValueError, match="release changed"):
        main_adapter.build_subsets(
            "unused", "target", products=("cosmos",), checkpoint_products=True
        )
    assert database.state["identity"] == ("release", "digest")
    assert database.state["contents"] == {}
    assert database.state["metadata"] == {}


def test_missing_shared_checkpoint_rejects_product_before_build(environment, monkeypatch):
    database, _ = environment
    del database.state["phases"]["derived"]
    monkeypatch.setattr(
        main_adapter,
        "run_product",
        lambda *_args, **_kwargs: pytest.fail("Incomplete base must not build products"),
    )
    with pytest.raises(ValueError, match="shared derivations"):
        main_adapter.build_subsets(
            "unused", "target", products=("cosmos",), checkpoint_products=True
        )


def test_lock_collision_rejects_before_worker_is_created(environment):
    database, _ = environment
    previous_owner = object()
    database.owner = previous_owner
    with pytest.raises(ValueError, match="Another migration owns"):
        main_adapter.build_subsets(
            "unused", "target", products=("cosmos",), checkpoint_products=True
        )
    assert len(database.connections) == 1
    assert database.owner is previous_owner


def test_public_api_is_the_current_runner_without_layout_discovery():
    from omnipath_subsets import build

    assert build.build_subsets is main_adapter.build_subsets
    assert not hasattr(main_adapter, "is_main_layout")


def test_finish_resume_skips_existing_products_and_emits_events_after_commit(
    environment, monkeypatch
):
    database, tuning = environment
    database.state["phases"]["metsigdb"] = {"old": True}
    database.state["contents"]["metsigdb"] = 10
    monkeypatch.setattr(main_adapter, "run_product", builder())
    events = []
    owner = database.connect("unused")
    main_adapter.acquire_schema_lock(owner, "target")

    def observer(event, **fields):
        assert database.owner is owner
        if event == "phase_committed":
            assert database.state["contents"][fields["phase"]] == 1
        events.append((event, fields["phase"]))

    try:
        result = main_adapter.run_products(
            "unused",
            "target",
            owner=owner,
            identity=("release", "digest"),
            products=("metsigdb", "cosmos"),
            resume=True,
            observer=observer,
        )
        assert result.products == {"cosmos": {"rows": 1}}
        assert database.state["contents"] == {"metsigdb": 10, "cosmos": 1}
        assert events == [("phase_start", "cosmos"), ("phase_committed", "cosmos")]
        assert len(tuning) == 1 and tuning[0][1] == ("cosmos",)
    finally:
        owner.close()


def test_finish_observer_failure_preserves_commit_and_stops_next_product(environment, monkeypatch):
    database, _ = environment
    monkeypatch.setattr(main_adapter, "run_product", builder())
    owner = database.connect("unused")
    main_adapter.acquire_schema_lock(owner, "target")

    def observer(event, **fields):
        if event == "phase_committed":
            raise OSError("observer persistence failed")

    try:
        with pytest.raises(OSError, match="persistence"):
            main_adapter.run_products(
                "unused",
                "target",
                owner=owner,
                identity=("release", "digest"),
                products=("metsigdb", "cosmos"),
                resume=True,
                observer=observer,
            )
        assert database.state["contents"] == {"metsigdb": 1}
        assert "metsigdb" in database.state["phases"]
        assert database.owner is owner
    finally:
        owner.close()
