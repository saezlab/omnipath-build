"""Exercise the real-output validator with valid and deliberately damaged Parquets."""

import copy
import errno
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_core.keys import entity_key
from omnipath_core.schema import ENTITY_SCHEMA, PAYLOAD_SCHEMA, RELATION_SCHEMA
from omnipath_client import Client
from omnipath_api.engine import ParquetServingEngine
from omnipath_api.serving_index import build_indexes, index_path
from scripts.validate_molecular_outputs import Validator, validate_outputs


@pytest.fixture
def outputs(tmp_path):
    folder = tmp_path / "resources" / "signor" / "1"
    folder.mkdir(parents=True)
    rows = []
    for type_, ns, identifier, ref in [
        ("gene", "entrez", "7157", "entrez:7157"),
        ("protein", "entrez", "7157", "entrez:7157"),
        ("protein", "uniprot", "P04637", "entrez:7157"),
        ("protein", "entrez", "1956", "entrez:1956"),
        ("protein", "uniprot", "P00533", "entrez:1956"),
        # Catalogue-only links must not turn an ambiguous fallback into a gene group.
        ("protein", "uniprot", "P00001", "uniprot:P00001"),
        ("chemical", "chebi", "1234", "chebi:1234"),
    ]:
        rows.append(
            dict(
                entity_key=entity_key(type_, ns, identifier),
                entity_type=type_,
                namespace=ns,
                identifier=identifier,
                label=identifier,
                taxon="9606",
                reference_entity_key=ref,
                gene_reference_keys=[ref]
                if ref.startswith("entrez:")
                else ["entrez:7157", "entrez:1956"]
                if ns == "uniprot"
                else [],
                identifiers=[],
                annotations=[],
                evidence=[],
            )
        )
    product, counterpart = rows[2]["entity_key"], rows[4]["entity_key"]
    form = dict(
        protein_entity_key=product,
        isoform_identifier=dict(ns="uniprot", id="P04637-2"),
        sequence_identifiers=[dict(ns="uniprot", id="P04637-2")],
        modifications=[
            dict(
                term="MOD:00696",
                residue="S",
                position=15,
                coordinate_reference=dict(
                    identifier=dict(ns="uniprot", id="P04637-2"),
                    coordinate_system="protein",
                    position_base=1,
                ),
            )
        ],
    )
    other = dict(
        protein_entity_key=counterpart, isoform_identifier=dict(ns="uniprot", id="P00533-3")
    )
    rows[1]["evidence"] = [
        dict(
            source="signor",
            dataset="annotations",
            row_id="standalone",
            annotations=[],
            molecular_form=form,
        )
    ]
    evidence = [
        dict(
            source="signor",
            dataset="interactions",
            row_id="row-1",
            upstream_id="source-1",
            annotations=[],
            subject_molecular_form=form,
            object_molecular_form=other,
        ),
        dict(
            source="signor",
            dataset="interactions",
            row_id="row-2",
            annotations=[],
            subject_molecular_form=dict(
                protein_entity_key=product, isoform_identifier=dict(ns="uniprot", id="P04637-4")
            ),
            object_molecular_form=None,
        ),
        dict(
            source="signor",
            dataset="interactions",
            row_id="row-3",
            annotations=[],
            subject_molecular_form=None,
            object_molecular_form=None,
        ),
    ]
    relation = dict(
        relation_key="relation",
        subject_entity_key=rows[1]["entity_key"],
        subject_type="protein",
        subject_label="TP53",
        object_entity_key=rows[3]["entity_key"],
        object_type="protein",
        object_label="EGFR",
        predicate="biolink:affects",
        subject_reference_entity_key="entrez:7157",
        object_reference_entity_key="entrez:1956",
        category="interaction",
        statement_kind="relation",
        sources=["signor"],
        taxon="9606",
        evidence=evidence,
        evidence_count=len(evidence),
        annotations=[],
    )
    for name, data, schema in (
        ("entities", rows, ENTITY_SCHEMA),
        ("relations", [relation], RELATION_SCHEMA),
        ("evidence_payloads", [], PAYLOAD_SCHEMA),
    ):
        pq.write_table(pa.Table.from_pylist(data, schema=schema), folder / f"{name}.parquet")
    return tmp_path, folder


def failures(report):
    return [c for r in report["resources"] for c in r["checks"] if c["status"] == "failed"]


def test_explicit_raw_and_projected_modes_are_distinct_with_existing_indexes(outputs):
    root, _ = outputs
    build_indexes(ParquetServingEngine(root), threads=1, min_free_disk=0)
    for mode in ("raw", "projected"):
        report = validate_outputs(root, ["signor"], examples=1, projection_mode=mode)
        assert not failures(report), failures(report)
        assert report["projection_mode"] == mode
        projections = report["resources"][0]["serving_projections"]
        assert {item["available"] for item in projections.values()} == {mode == "projected"}


@pytest.mark.parametrize("missing_kind", ["entities", "relations"])
def test_projected_mode_requires_both_matching_indexes(outputs, missing_kind):
    root, folder = outputs
    build_indexes(ParquetServingEngine(root), threads=1, min_free_disk=0)
    index_path(root, missing_kind, [str(folder / f"{missing_kind}.parquet")]).unlink()
    report = validate_outputs(root, ["signor"], examples=1, projection_mode="projected")
    assert any(c["name"] == "api.projection_mode" for c in failures(report))


def test_projected_mode_rejects_stale_input_signature(outputs):
    import os

    root, folder = outputs
    build_indexes(ParquetServingEngine(root), threads=1, min_free_disk=0)
    path = folder / "relations.parquet"
    before = path.stat()
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
    report = validate_outputs(root, ["signor"], examples=1, projection_mode="projected")
    assert any(c["name"] == "api.projection_mode" for c in failures(report))


def test_progress_logging_omits_query_results_and_parameters(outputs, monkeypatch, capsys):
    monkeypatch.setenv("OMNIPATH_VALIDATION_PROGRESS", "1")
    validator = Validator(outputs[1], examples=1, max_export_relations=100)
    try:
        assert validator.sql("fixture.operation", "SELECT ?", ["private-value"]) == [
            ("private-value",)
        ]
    finally:
        validator.db.close()
    output = capsys.readouterr().err
    assert "private-value" not in output
    assert set(json.loads(output)) == {"resource", "operation", "kind", "elapsed_ms"}


def test_validator_paired_forms_gene_navigation_export_and_unavailable_cases(outputs):
    root, _ = outputs
    report = validate_outputs(root, ["signor"], examples=2)
    assert not failures(report), json.dumps(failures(report), indent=2)
    resource = report["resources"][0]
    assert report["status"] == "passed"
    from scripts import validate_molecular_outputs as validator_module

    assert (
        report["validator_sha256"]
        == hashlib.sha256(Path(validator_module.__file__).read_bytes()).hexdigest()
    )
    assert resource["coverage"]["paired_occurrences"] == 1
    assert resource["coverage"]["modifications"] == 2
    assert any(
        c["name"] == "observed.variants" and c["status"] == "unavailable"
        for c in resource["checks"]
    )
    assert any(c["name"].endswith("product_closure") for c in resource["checks"])
    assert any(c["name"] == "api.paired_occurrences.1" for c in resource["checks"])
    mixed = next(
        example
        for example in resource["examples"]
        if example["name"] == "opposite_endpoint_constraints_1"
    )
    assert mixed["expected_relations"] == 0
    assert mixed["filters"]["isoform_identifiers"] == ["uniprot:P00533-3"]
    assert any(c["name"] == "api.gene_type_filter.protein" for c in resource["checks"])
    assert any(c["name"] == "api.gene_type_filter.gene" for c in resource["checks"])
    assert any(t["kind"] == "api" for t in resource["timings"])
    assert resource["tables"]["entities"]["bytes"] > 0
    assert resource["client_snapshot"]["staging"] == "temporary_hardlinks"
    assert any(t["kind"] == "python_client" for t in resource["timings"])
    for endpoint in ("any", "source", "target", "both"):
        assert any(
            c["name"] == f"client.reference.{endpoint}" and c["status"] == "passed"
            for c in resource["checks"]
        )
    closure = next(
        c for c in resource["checks"] if c["name"] == "client.isoform_1.product_closure_count"
    )
    assert closure["expected_products"] == closure["actual_products"] == 2
    assert any(
        c["name"] == "client.product_any.count" and c["actual_relations"] == 1
        for c in resource["checks"]
    )
    assert any(
        c["name"] == "client.product_both.count" and c["actual_relations"] == 0
        for c in resource["checks"]
    )
    assert any(
        c["name"] == "client.opposite_endpoint_constraints_1.count" and c["actual_relations"] == 0
        for c in resource["checks"]
    )


def test_validator_detects_dangling_product_reference(outputs):
    root, folder = outputs
    table = pq.read_table(folder / "relations.parquet")
    rows = table.to_pylist()
    rows[0]["evidence"][0]["subject_molecular_form"]["protein_entity_key"] = "missing-product"
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), folder / "relations.parquet")
    report = validate_outputs(root, ["signor/1"], examples=1)
    assert any(c["name"] == "molecular.product_closure_and_type" for c in failures(report))


def test_validator_reports_schema_error_without_unstructured_api_fallback(outputs):
    root, folder = outputs
    table = pq.read_table(folder / "entities.parquet").drop(["reference_entity_key"])
    pq.write_table(table, folder / "entities.parquet")
    report = validate_outputs(root, ["signor"], examples=1)
    assert failures(report)[0]["name"] == "schema.entities"
    assert any(
        c["name"] == "data_and_api_checks" and c["status"] == "unavailable"
        for c in report["resources"][0]["checks"]
    )


def test_validator_detects_form_specific_product_hash(outputs):
    root, folder = outputs
    table = pq.read_table(folder / "entities.parquet")
    rows = table.to_pylist()
    rows[2]["entity_key"] = "form-enumerated-key"
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), folder / "entities.parquet")
    report = validate_outputs(root, ["signor"], examples=1)
    assert any(c["name"] == "molecular.no_form_specific_product_keys" for c in failures(report))


def test_validator_checks_physical_arrow_type(outputs):
    root, folder = outputs
    path = folder / "entities.parquet"
    table = pq.read_table(path)
    table = table.set_column(
        table.schema.get_field_index("reference_entity_key"),
        "reference_entity_key",
        pa.array([7157] * table.num_rows, type=pa.int64()),
    )
    pq.write_table(table, path)
    report = validate_outputs(root, ["signor"], examples=1)
    error = next(c for c in failures(report) if c["name"] == "schema.entities")
    assert any(
        "reference_entity_key: actual int64; expected string" in message
        for message in error["errors"]
    )


def test_client_audit_detects_lost_exact_evidence_trimming(outputs, monkeypatch):
    root, _ = outputs
    original = Client.related_product

    def untrimmed(self, product, **kwargs):
        selected = original(self, product, **kwargs)
        return self.relations(kwargs["resources"]).filter(
            f"relation_key IN (SELECT relation_key FROM ({selected.sql_query()}))"
        )

    monkeypatch.setattr(Client, "related_product", untrimmed)
    report = validate_outputs(root, ["signor"], examples=1)
    assert any(c["name"].startswith("client.isoform_1.paired_evidence.") for c in failures(report))
    assert not any(c["name"].startswith("api.") for c in failures(report))


def add_matching_relation(outputs):
    _, folder = outputs
    path = folder / "relations.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    second = copy.deepcopy(rows[0])
    second["relation_key"] = "another-relation"
    rows.append(second)
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)


@pytest.mark.parametrize("damage", ["invented_empty", "duplicate_key"])
def test_export_audit_rejects_substituted_relation_key_multiset(outputs, monkeypatch, damage):
    add_matching_relation(outputs)
    original = Validator.request

    def corrupted(self, client, method, path, **kwargs):
        response = original(self, client, method, path, **kwargs)
        if path != "/export":
            return response
        table = pq.read_table(io.BytesIO(response.content))
        rows = table.to_pylist()
        if damage == "duplicate_key":
            rows = [rows[0]] * len(rows)
        else:
            for index, row in enumerate(rows):
                row.update(
                    relation_key=f"invented-{index}",
                    evidence=[],
                    evidence_count=0,
                    referenced_product_records=[],
                )
        buffer = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), buffer)
        return SimpleNamespace(content=buffer.getvalue())

    monkeypatch.setattr(Validator, "request", corrupted)
    report = validate_outputs(outputs[0], ["signor"], examples=1)
    assert any(c["name"].endswith(".relation_keys") for c in failures(report))
    assert not any(c["name"].endswith(".count") for c in failures(report))
    assert not any(c["name"].startswith("api.") for c in failures(report))
    if damage == "invented_empty":
        assert any(c["name"].endswith(".paired_occurrences") for c in failures(report))


@pytest.mark.parametrize("damage", ["empty_page", "duplicate_page"])
def test_api_audit_requires_populated_unique_search_pages(outputs, monkeypatch, damage):
    add_matching_relation(outputs)
    original = Validator.request

    def corrupted(self, client, method, path, **kwargs):
        response = original(self, client, method, path, **kwargs)
        if path != "/relations/search":
            return response
        data = response.json()
        data["relations"] = (
            [] if damage == "empty_page" else [data["relations"][0]] * len(data["relations"])
        )
        return SimpleNamespace(json=lambda: data)

    monkeypatch.setattr(Validator, "request", corrupted)
    report = validate_outputs(outputs[0], ["signor"], examples=1)
    assert any(
        c["name"].endswith(".page") and ".product_context." not in c["name"]
        for c in failures(report)
    )
    assert not any(c["name"].endswith(".count") for c in failures(report))
    assert not any(c["name"].startswith(("export.", "client.")) for c in failures(report))


@pytest.mark.parametrize("damage", ["empty_page", "duplicate_page", "untrimmed", "opposite_form"])
def test_product_context_audit_checks_page_and_paired_occurrences(outputs, monkeypatch, damage):
    add_matching_relation(outputs)
    raw_evidence = pq.read_table(outputs[1] / "relations.parquet").to_pylist()[0]["evidence"]
    original = Validator.request

    def corrupted(self, client, method, path, **kwargs):
        response = original(self, client, method, path, **kwargs)
        if (
            not path.endswith("/molecular-context")
            or kwargs.get("params", {}).get("view") != "product"
        ):
            return response
        data = response.json()
        if damage == "empty_page":
            data["relations"] = []
        elif damage == "duplicate_page":
            data["relations"] = [data["relations"][0]] * len(data["relations"])
        else:
            for row in data["relations"]:
                if damage == "untrimmed":
                    row["evidence"] = copy.deepcopy(raw_evidence)
                else:
                    for ev in row["evidence"]:
                        if ev.get("object_molecular_form"):
                            ev["object_molecular_form"]["isoform_identifier"] = {
                                "ns": "uniprot",
                                "id": "P00533-99",
                            }
        return SimpleNamespace(json=lambda: data)

    monkeypatch.setattr(Validator, "request", corrupted)
    report = validate_outputs(outputs[0], ["signor"], examples=1)
    assert any(".product_context." in c["name"] for c in failures(report))
    assert not any(c["name"].endswith(".product_context") for c in failures(report))
    assert not any(c["name"].startswith(("export.", "client.")) for c in failures(report))


def test_standalone_context_audit_rejects_repeated_observation_with_same_form(outputs, monkeypatch):
    path = outputs[1] / "entities.parquet"
    rows = pq.read_table(path).to_pylist()
    source = next(row for row in rows if row["evidence"])
    second = copy.deepcopy(source["evidence"][0])
    second["row_id"] = "another-standalone"
    source["evidence"].append(second)
    pq.write_table(pa.Table.from_pylist(rows, schema=ENTITY_SCHEMA), path)
    original = Validator.request

    def corrupted(self, client, method, path, **kwargs):
        response = original(self, client, method, path, **kwargs)
        if (
            not path.endswith("/molecular-context")
            or kwargs.get("params", {}).get("view") != "product"
        ):
            return response
        data = response.json()
        if len(data["standaloneEvidence"]) > 1:
            data["standaloneEvidence"][1] = copy.deepcopy(data["standaloneEvidence"][0])
        return SimpleNamespace(json=lambda: data)

    monkeypatch.setattr(Validator, "request", corrupted)
    report = validate_outputs(outputs[0], ["signor"], examples=1)
    assert any(c["name"].endswith(".standalone_forms") for c in failures(report))
    assert not any(c["name"].startswith(("export.", "client.")) for c in failures(report))


def test_api_page_proof_allows_valid_sort_ties(outputs, monkeypatch):
    add_matching_relation(outputs)
    original = Validator.request

    def reordered(self, client, method, path, **kwargs):
        response = original(self, client, method, path, **kwargs)
        if path == "/relations/search" or path.endswith("/molecular-context"):
            data = response.json()
            data["relations"].reverse()
            return SimpleNamespace(json=lambda: data)
        return response

    monkeypatch.setattr(Validator, "request", reordered)
    report = validate_outputs(outputs[0], ["signor"], examples=1)
    assert not failures(report), failures(report)


def test_client_audit_detects_missing_counterpart_product(outputs, monkeypatch):
    root, _ = outputs
    original = Client.referenced_products

    def missing_counterpart(self, selected, **kwargs):
        return original(self, selected, **kwargs).filter("identifier='P04637'")

    monkeypatch.setattr(Client, "referenced_products", missing_counterpart)
    report = validate_outputs(root, ["signor"], examples=1)
    closure = next(
        c for c in failures(report) if c["name"] == "client.isoform_1.product_closure_count"
    )
    assert closure["expected_products"] == 2
    assert closure["actual_products"] == 1


def test_client_snapshot_cross_filesystem_is_unavailable_without_source_writes(
    outputs, monkeypatch
):
    root, folder = outputs
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_nlink)
        for path in folder.glob("*.parquet")
    }

    def different_filesystem(*args, **kwargs):
        raise OSError(errno.EXDEV, "Cross-device link")

    monkeypatch.setattr("scripts.validate_molecular_outputs.os.link", different_filesystem)
    report = validate_outputs(root, ["signor"], examples=1)
    assert not failures(report)
    unavailable = next(
        c for c in report["resources"][0]["checks"] if c["name"] == "client.offline_snapshot"
    )
    assert unavailable["status"] == "unavailable"
    assert "full Parquets are not copied" in unavailable["reason"]
    assert before == {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_nlink)
        for path in folder.glob("*.parquet")
    }
    assert not (root / "snapshot.json").exists()


def test_client_snapshot_staging_uses_source_filesystem_and_is_cleaned(outputs, monkeypatch):
    from scripts import validate_molecular_outputs as validator_module

    root, folder = outputs
    original_temporary_directory = validator_module.tempfile.TemporaryDirectory
    original_link = validator_module.os.link
    staged = []
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_nlink)
        for path in folder.glob("*.parquet")
    }

    def temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        if kwargs.get("prefix") == "omnipath-client-validation-":
            assert Path(kwargs["dir"]) == root
            staged.append(Path(directory.name))
        return directory

    def same_filesystem_link(source, destination):
        # Simulate the production /tmp tmpfs boundary: a snapshot outside the
        # source data root cannot be hardlinked, even on this test machine.
        if not Path(destination).is_relative_to(root):
            raise OSError(errno.EXDEV, "Cross-device link")
        assert Path(source).stat().st_dev == Path(destination).parent.stat().st_dev
        original_link(source, destination)

    monkeypatch.setattr(validator_module.tempfile, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(validator_module.os, "link", same_filesystem_link)
    report = validate_outputs(root, ["signor"], examples=1)
    assert not failures(report)
    assert report["resources"][0]["client_snapshot"]["staging"] == "temporary_hardlinks"
    assert len(staged) == 1 and not staged[0].exists()
    assert before == {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_nlink)
        for path in folder.glob("*.parquet")
    }


def plan_nodes(plan):
    for node in plan:
        yield node
        yield from plan_nodes(node.get("children", []))


def test_validator_base_expansions_and_example_queries_have_no_delimiter_joins(
    outputs, monkeypatch
):
    original_sql = Validator.sql
    checked = set()
    base_queries = {
        "molecular.product_references",
        "molecular.coverage",
        "molecular.paired_occurrences",
        "baseline.product_examples",
        "baseline.isoform_examples",
        "baseline.paired_examples",
    }

    def checked_sql(self, name, query, params=None):
        if name in base_queries or name == "molecular.reusable_uniprot_products":
            plan = json.loads(
                self.db.execute("EXPLAIN (FORMAT JSON) " + query, params or []).fetchone()[1]
            )
            delimiters = [node for node in plan_nodes(plan) if "DELIM_JOIN" in node["name"]]
            if name in base_queries:
                assert not delimiters, (name, delimiters)
            else:
                # The two short-list provenance EXISTS checks remain MARK
                # joins; evidence-array expansion must never add INNER ones.
                assert len(delimiters) <= 2
                assert all(node["extra_info"]["Join Type"] == "MARK" for node in delimiters)
            checked.add(name)
        return original_sql(self, name, query, params)

    monkeypatch.setattr(Validator, "sql", checked_sql)
    report = validate_outputs(outputs[0], ["signor"], examples=1)
    assert not failures(report), failures(report)
    assert checked == base_queries | {"molecular.reusable_uniprot_products"}


def test_streaming_occurrences_and_forms_preserve_nullable_duplicate_multisets(outputs):
    _, folder = outputs
    for name, schema, key in (
        ("entities", ENTITY_SCHEMA, "entity_key"),
        ("relations", RELATION_SCHEMA, "relation_key"),
    ):
        path = folder / f"{name}.parquet"
        rows = pq.read_table(path).to_pylist()
        source = next(row for row in rows if row["evidence"])
        for index, evidence in enumerate(
            (None, [], [None], [source["evidence"][0], source["evidence"][0]])
        ):
            row = copy.deepcopy(source)
            if name == "entities":
                row.update(
                    namespace="fixture",
                    identifier=str(index),
                    entity_key=entity_key(row["entity_type"], "fixture", str(index)),
                    reference_entity_key=f"fixture:{index}",
                    gene_reference_keys=[],
                )
            else:
                row[key] = f"nullable-relation-{index}"
                row["evidence_count"] = len(evidence or [])
            row["evidence"] = copy.deepcopy(evidence)
            rows.append(row)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    validator = Validator(folder, examples=0, max_export_relations=0)
    try:
        assert validator.schemas()
        validator.structural()
        validator.db.execute("""CREATE VIEW previous_occurrences AS
            SELECT relation_key, subject_reference_entity_key, object_reference_entity_key, ev
            FROM relations, UNNEST(evidence) AS items(ev)""")
        validator.db.execute("""CREATE VIEW previous_forms AS
            SELECT 'relation' AS owner_type, relation_key AS owner_key, 'source' AS side,
                subject_reference_entity_key AS reference_key, ev.subject_molecular_form AS form, ev.annotations
                FROM previous_occurrences
            UNION ALL SELECT 'relation', relation_key, 'target', object_reference_entity_key,
                ev.object_molecular_form, ev.annotations FROM previous_occurrences
            UNION ALL SELECT 'entity', entity_key, 'standalone', reference_entity_key,
                ev.molecular_form, ev.annotations FROM entities, UNNEST(evidence) AS items(ev)""")
        for view in ("occurrences", "forms"):
            difference = validator.db.execute(f"""SELECT count(*) FROM (
                (SELECT * FROM {view} EXCEPT ALL SELECT * FROM previous_{view})
                UNION ALL (SELECT * FROM previous_{view} EXCEPT ALL SELECT * FROM {view})
            )""").fetchone()[0]
            assert difference == 0
        assert validator.db.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 6
        assert validator.db.execute("SELECT count(*) FROM forms").fetchone()[0] == 16
        assert all(check["status"] != "failed" for check in validator.report["checks"])
    finally:
        validator.db.close()


def test_validator_large_parent_evidence_arrays_stay_bounded(outputs, tmp_path):
    _, folder = outputs
    path = folder / "entities.parquet"
    rows = pq.read_table(path).to_pylist()
    source = next(row for row in rows if row["evidence"])
    parents, observations = 64, 256
    for parent in range(parents):
        row = copy.deepcopy(source)
        identifier = str(parent)
        row.update(
            namespace="fixture",
            identifier=identifier,
            entity_key=entity_key(row["entity_type"], "fixture", identifier),
            reference_entity_key=f"fixture:{identifier}",
            gene_reference_keys=[],
            evidence=[],
        )
        for observation in range(observations):
            evidence = copy.deepcopy(source["evidence"][0])
            evidence["row_id"] = f"{parent}:{observation}"
            evidence["annotations"].append(
                dict(term="fixture:payload", value=evidence["row_id"] + "x" * 512)
            )
            row["evidence"].append(evidence)
        rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows, schema=ENTITY_SCHEMA), path)
    validator = Validator(folder, examples=0, max_export_relations=0)
    try:
        spill = tmp_path / "bounded-duckdb-spill"
        validator.db.execute("SET memory_limit='256MB'")
        validator.db.execute("SET max_temp_directory_size='256MB'")
        validator.db.execute("SET temp_directory=?", [str(spill)])
        assert validator.schemas()
        validator.structural()
        assert validator.report["coverage"]["standalone_forms"] == parents * observations + 1
        assert validator.report["coverage"]["paired_occurrences"] == 1
        assert validator.report["coverage"]["distinct_referenced_products"] == 2
        assert all(check["status"] != "failed" for check in validator.report["checks"])
        assert any(
            timing["operation"] == "molecular.product_references"
            for timing in validator.report["timings"]
        )
    finally:
        validator.db.close()


def structural_checks(folder):
    validator = Validator(folder, examples=0, max_export_relations=0)
    try:
        assert validator.schemas()
        validator.structural()
        return {check["name"]: check for check in validator.report["checks"]}
    finally:
        validator.db.close()


def reported_native_product(outputs, identifier="Q9Y6K9-2-PRO_000001"):
    """Use the writer's precise provenance format on every pointed occurrence."""
    _, folder = outputs
    path = folder / "entities.parquet"
    rows = pq.read_table(path).to_pylist()
    product = rows[2]
    old_key = product["entity_key"]
    product["identifier"] = identifier
    product["entity_key"] = entity_key("protein", "uniprot", identifier)
    product["reference_entity_key"] = "uniprot:" + identifier
    product["gene_reference_keys"] = []

    def update(form, annotations, scope):
        if not form or form.get("protein_entity_key") != old_key:
            return
        form["protein_entity_key"] = product["entity_key"]
        form["isoform_identifier"] = {"ns": "uniprot", "id": identifier.split("-PRO_")[0]}
        form["sequence_identifiers"] = [{"ns": "uniprot", "id": identifier}]
        annotations.append(
            dict(
                term="omnipath:protein_mapping_status",
                value="reported",
                source="resolver",
                dataset="molecular_reference",
                scope=scope,
            )
        )

    for row in rows:
        for occurrence in row["evidence"] or []:
            update(occurrence["molecular_form"], occurrence["annotations"], None)
    pq.write_table(pa.Table.from_pylist(rows, schema=ENTITY_SCHEMA), path)
    path = folder / "relations.parquet"
    rows = pq.read_table(path).to_pylist()
    for row in rows:
        for occurrence in row["evidence"]:
            for side in ("subject", "object"):
                update(occurrence[side + "_molecular_form"], occurrence["annotations"], side)
    pq.write_table(pa.Table.from_pylist(rows, schema=RELATION_SCHEMA), path)
    return folder


@pytest.mark.parametrize("identifier", ["Q9Y6K9-2", "Q9Y6K9-PRO_000001", "Q9Y6K9-2-PRO_000001"])
def test_reported_native_uniprot_suffixes_require_every_occurrence_provenance(outputs, identifier):
    checks = structural_checks(reported_native_product(outputs, identifier))
    assert checks["molecular.reusable_uniprot_products"]["status"] == "passed"
    assert checks["molecular.no_form_specific_product_keys"]["status"] == "passed"
    assert all(check["status"] != "failed" for check in checks.values())


@pytest.mark.parametrize("owner", ["relation", "standalone"])
@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "wrong_scope",
        "wrong_source",
        "wrong_dataset",
        "resolved",
        "missing_assertion",
        "wrong_assertion",
    ],
)
def test_native_uniprot_suffix_cannot_borrow_other_occurrence_or_endpoint_provenance(
    outputs, owner, damage
):
    folder = reported_native_product(outputs)
    path = folder / ("relations.parquet" if owner == "relation" else "entities.parquet")
    table = pq.read_table(path)
    rows = table.to_pylist()
    occurrence = rows[0]["evidence"][0] if owner == "relation" else rows[1]["evidence"][0]
    annotations = occurrence["annotations"]
    status = next(
        annotation
        for annotation in annotations
        if annotation["term"] == "omnipath:protein_mapping_status"
    )
    form = occurrence["subject_molecular_form" if owner == "relation" else "molecular_form"]
    if damage == "missing":
        annotations.remove(status)
    elif damage == "wrong_scope":
        if owner == "relation":
            status["scope"] = "object"
        else:
            # Standalone annotations have no scope field in their schema.
            # Provenance on another entity occurrence cannot authorize this one.
            annotations.remove(status)
            rows[3]["evidence"] = [
                dict(source="signor", row_id="other", annotations=[status], molecular_form=None)
            ]
    elif damage == "wrong_source":
        status["source"] = "raw"
    elif damage == "wrong_dataset":
        status["dataset"] = "gene_reference"
    elif damage == "resolved":
        status["value"] = "resolved"
    elif damage == "missing_assertion":
        form["sequence_identifiers"] = []
        form["isoform_identifier"] = None
    elif damage == "wrong_assertion":
        # An enclosing isoform is insufficient proof of an exact chain assertion.
        form["sequence_identifiers"] = [{"ns": "uniprot", "id": "Q9Y6K9-2"}]
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    check = structural_checks(folder)["molecular.reusable_uniprot_products"]
    assert check["status"] == "failed"
    assert check["invalid_count"] == 1


@pytest.mark.parametrize(
    "identifier", ["NOT_A_VALID_ACCESSION-2", "Q9Y6K9-BAD", "Q9Y6K9-2-PRO_BAD"]
)
def test_reported_provenance_does_not_waive_invalid_uniprot_identifier(outputs, identifier):
    check = structural_checks(reported_native_product(outputs, identifier))[
        "molecular.reusable_uniprot_products"
    ]
    assert check["status"] == "failed"
    assert check["invalid_count"] == 3


def test_both_product_fields_are_checked_without_extending_protein_provenance_to_transcript(
    outputs,
):
    folder = reported_native_product(outputs)
    entity_path = folder / "entities.parquet"
    entities = pq.read_table(entity_path).to_pylist()
    transcript_key = entity_key("transcript", "uniprot", "Q9Y6K9-2")
    entities.append(
        dict(
            entity_key=transcript_key,
            entity_type="transcript",
            namespace="uniprot",
            identifier="Q9Y6K9-2",
            reference_entity_key="uniprot:Q9Y6K9-2",
            gene_reference_keys=[],
            evidence=[],
        )
    )
    pq.write_table(pa.Table.from_pylist(entities, schema=ENTITY_SCHEMA), entity_path)
    relation_path = folder / "relations.parquet"
    relations = pq.read_table(relation_path).to_pylist()
    relations[0]["evidence"][0]["subject_molecular_form"]["transcript_entity_key"] = transcript_key
    pq.write_table(pa.Table.from_pylist(relations, schema=RELATION_SCHEMA), relation_path)
    check = structural_checks(folder)["molecular.reusable_uniprot_products"]
    assert check["status"] == "failed"
    assert check["invalid_count"] == 1


@pytest.mark.parametrize("identifier", ["Q9Y6K9-2", "Q9Y6K9-PRO_000001", "Q9Y6K9-2-PRO_000001"])
def test_actual_writer_reported_native_uniprot_provenance_passes_validator(tmp_path, identifier):
    from omnipath_resolver.resolver import EntityResolver
    from omnipath_build.silver import SilverExtractor
    from omnipath_build.writer import ParquetWriter

    folder = tmp_path / "resources" / "fixture" / "1"
    item = {"type": "protein", "identifiers": [{"type": "uniprot", "value": identifier}]}
    extractor = SilverExtractor("fixture", "reported-native")
    extractor.process_record(item, item, "standalone", 0)
    relation = {
        "subject": item,
        "predicate": "affects",
        "object": {"type": "gene", "identifiers": [{"type": "entrez", "value": "55"}]},
    }
    extractor.process_record(relation, relation, "relation", 1)
    resolver = EntityResolver(library_dir=tmp_path / "absent")
    writer = ParquetWriter(folder)
    try:
        writer.append_observations(extractor, resolver)
        writer.close()
    finally:
        resolver.close()
    checks = structural_checks(folder)
    assert checks["molecular.reusable_uniprot_products"]["status"] == "passed"
    assert all(check["status"] != "failed" for check in checks.values())
