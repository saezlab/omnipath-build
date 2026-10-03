"""The release auditor detects corruption and leaves snapshots unchanged."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_postgres.compatibility.record_layout.loader import load_release
from omnipath_core.source_attributes import (
    CELLULAR_LOCATION,
    CONVERSION_DIRECTION,
    SOURCE_RECORD_TYPE,
)
from omnipath_subsets.compatibility.record_layout.build import build_subsets
from test_postgres import resource, release
from test_projection import entity, fixture_rows
from test_reactions import reaction_fixture


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/verify_postgres_release.py"
spec = importlib.util.spec_from_file_location("verify_postgres_release", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def loaded(tmp_path, postgres_dsn):
    original = reaction_fixture()
    resource(tmp_path, "rhea", rows=original)
    resource(tmp_path, "signor", rows=fixture_rows())
    nodes = [
        entity(
            f"term-{index}", f"CHEMONT:{index}", namespace="chemont", entity_type="ontology_term"
        )
        for index in range(4)
    ]

    def axiom(key, subject, predicate, obj):
        row = deepcopy(fixture_rows()[1][0])
        row.update(
            relation_key=key,
            statement_kind="ontology",
            subject_entity_key=f"term-{subject}",
            object_entity_key=f"term-{obj}",
            predicate=predicate,
            evidence_count=0,
            evidence=[],
            annotations=[],
        )
        return row

    resource(
        tmp_path,
        "chemont",
        rows=(
            nodes,
            [
                axiom("a-b", 0, "subclass_of", 1),
                axiom("b-c", 1, "subclass_of", 2),
                axiom("c-a", 2, "subclass_of", 0),
                axiom("a-has-d", 0, "has_part", 3),
            ],
            [],
        ),
    )
    resource(tmp_path, "ontology-extra", rows=(nodes, [axiom("c-d", 2, "subclass_of", 3)], []))
    manifest = release(
        tmp_path, {name: "1.0.0" for name in ("rhea", "signor", "chemont", "ontology-extra")}
    )
    schema = "audit_" + uuid.uuid4().hex
    load_release(tmp_path, manifest, postgres_dsn, schema=schema, batch_size=1)
    build_subsets(postgres_dsn, schema)
    return dict(
        root=tmp_path, manifest=manifest, schema=schema, dsn=postgres_dsn, original=original
    )


def inspect(fixture, **kwargs):
    return audit.verify_postgres_release(
        fixture["dsn"],
        fixture["root"],
        fixture["manifest"],
        schema=fixture["schema"],
        batch_size=1,
        **kwargs,
    )


@pytest.mark.parametrize("batch_size", [0, 1025, True, 1.5])
def test_invalid_batch_cannot_read_artifacts_or_connect(monkeypatch, batch_size):
    monkeypatch.setattr(audit, "load_pinned_release", lambda *_: pytest.fail("No files permitted"))
    with pytest.raises(audit.VerificationError, match="batch_size"):
        audit.verify_postgres_release(
            "unused", "unused", "unused", schema="test_release", batch_size=batch_size
        )


@pytest.mark.integration
def test_all_records_nested_values_quantities_catalog_and_products_are_verified(loaded):
    result = inspect(loaded)
    assert result["status"] == "success" and result["mode"] == "read_only"
    assert result["resources"]["rhea"]["stored_counts"]["relations"] == 2
    assert result["resources"]["rhea"]["source_payload_rows"] == 2
    assert result["catalog"]["raw_storage_absent"] is True
    assert result["derived"]["measurement_profile_groups"] > 0
    assert result["products"]["missing_resources"]["networks"]["liana"] == ["connectomedb2025"]
    assert result["products"]["cosmos_edges"] > 0
    assert result["derived"]["ontology"]["scoped_closure_rows"] == 8
    assert result["derived"]["ontology"]["release_closure_rows"] == 10
    assert result["derived"]["reaction_provenance"] == {"contexts": 1, "participant_occurrences": 2}


@pytest.mark.integration
@pytest.mark.parametrize(
    "damage",
    [
        "record",
        "scalar",
        "identifier",
        "evidence",
        "quantity",
        "compartment",
        "index",
        "constraint",
        "metadata",
    ],
)
def test_real_corruption_is_detected(loaded, damage):
    namespace = sql.Identifier(loaded["schema"])
    statements = {
        "record": "UPDATE {s}.entities SET record_json=jsonb_set(record_json,'{{label}}','\"altered\"') WHERE resource='signor'",
        "scalar": "UPDATE {s}.entities SET label='altered' WHERE resource='signor'",
        "identifier": "UPDATE {s}.identifiers SET id='wrong' WHERE resource='signor'",
        "evidence": "UPDATE {s}.evidence SET row_id='wrong' WHERE resource='signor'",
        "quantity": "UPDATE {s}.annotations SET quantity=jsonb_set(quantity,'{{has_unit}}','\"wrong\"') WHERE quantity IS NOT NULL",
        "compartment": "UPDATE {s}.reaction_participant SET compartment='wrong'",
        "index": "DROP INDEX {s}.relations_subject_idx",
        "constraint": "ALTER TABLE {s}.entities DROP CONSTRAINT entities_resource_version_fkey",
        "metadata": "UPDATE {s}.subset_build_metadata SET stats=jsonb_set(stats,'{{edges}}','100') WHERE product='cosmos'",
    }
    with psycopg.connect(loaded["dsn"]) as conn:
        conn.execute(sql.SQL(statements[damage]).format(s=namespace))
    with pytest.raises(audit.VerificationError):
        inspect(loaded)


@pytest.mark.integration
@pytest.mark.parametrize(
    "damage",
    [
        "index_column",
        "index_order",
        "index_opclass",
        "index_method",
        "index_partial",
        "index_include",
        "index_sort",
        "occurrence_table",
        "occurrence_expression",
        "foreign_namespace",
        "foreign_action",
        "check_true",
        "check_no_inherit",
        "primary_deferred",
        "orphan_context",
        "transport",
        "context_identity",
        "ontology_edge",
        "ontology_edge_missing",
        "scoped_missing",
        "scoped_depth",
        "scoped_spurious",
        "global_missing",
        "derived_index_missing",
    ],
)
def test_same_name_catalog_replacements_and_derived_corruption_are_rejected(loaded, damage):
    statements = {
        "index_column": [
            "DROP INDEX {s}.relations_subject_idx",
            "CREATE INDEX relations_subject_idx ON {s}.relations(predicate)",
        ],
        "index_order": [
            "DROP INDEX {s}.relations_predicate_category_idx",
            "CREATE INDEX relations_predicate_category_idx ON {s}.relations(category,predicate)",
        ],
        "index_opclass": [
            "DROP INDEX {s}.entities_identifier_lower_idx",
            "CREATE INDEX entities_identifier_lower_idx ON {s}.entities(lower(identifier))",
        ],
        "index_method": [
            "DROP INDEX {s}.relations_subject_idx",
            "CREATE INDEX relations_subject_idx ON {s}.relations USING hash(subject_entity_key)",
        ],
        "index_partial": [
            "DROP INDEX {s}.relations_subject_idx",
            "CREATE INDEX relations_subject_idx ON {s}.relations(subject_entity_key) WHERE predicate='affects'",
        ],
        "index_include": [
            "DROP INDEX {s}.relations_subject_idx",
            "CREATE INDEX relations_subject_idx ON {s}.relations(subject_entity_key) INCLUDE(predicate)",
        ],
        "index_sort": [
            "DROP INDEX {s}.relations_subject_idx",
            "CREATE INDEX relations_subject_idx ON {s}.relations(subject_entity_key DESC)",
        ],
        "occurrence_table": [
            "DROP INDEX {s}.annotations_owner_ordinal_idx",
            "CREATE UNIQUE INDEX annotations_owner_ordinal_idx ON {s}.entities(resource,version,entity_key)",
        ],
        "occurrence_expression": [
            "DROP INDEX {s}.annotations_owner_ordinal_idx",
            "CREATE UNIQUE INDEX annotations_owner_ordinal_idx ON {s}.annotations(resource,version,owner_kind,owner_key,COALESCE(evidence_ordinal,0),ordinal)",
        ],
        "foreign_namespace": [
            "CREATE SCHEMA {b}",
            "CREATE TABLE {b}.resource_versions AS TABLE {s}.resource_versions",
            "ALTER TABLE {b}.resource_versions ADD PRIMARY KEY(resource,version)",
            "ALTER TABLE {s}.entities DROP CONSTRAINT entities_resource_version_fkey",
            "ALTER TABLE {s}.entities ADD FOREIGN KEY(resource,version) REFERENCES {b}.resource_versions(resource,version) DEFERRABLE INITIALLY DEFERRED",
        ],
        "foreign_action": [
            "ALTER TABLE {s}.entities DROP CONSTRAINT entities_resource_version_fkey",
            "ALTER TABLE {s}.entities ADD FOREIGN KEY(resource,version) REFERENCES {s}.resource_versions(resource,version) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED",
        ],
        "check_true": [
            "ALTER TABLE {s}.identifiers DROP CONSTRAINT identifiers_ordinal_check",
            "ALTER TABLE {s}.identifiers ADD CONSTRAINT identifiers_ordinal_check CHECK(true)",
        ],
        "check_no_inherit": [
            "ALTER TABLE {s}.identifiers DROP CONSTRAINT identifiers_ordinal_check",
            "ALTER TABLE {s}.identifiers ADD CONSTRAINT identifiers_ordinal_check CHECK(ordinal>=0) NO INHERIT",
        ],
        "primary_deferred": [
            "ALTER TABLE {s}.annotations DROP CONSTRAINT annotations_pkey",
            "ALTER TABLE {s}.annotations ADD PRIMARY KEY(annotation_id) DEFERRABLE INITIALLY DEFERRED",
        ],
        "orphan_context": [
            "INSERT INTO {s}.reaction_context SELECT 'orphan',resource,version,reaction_entity_id,source,dataset,row_id,namespace,identifier,taxon,NULL,NULL,false,repeat('0',64),NULL,ARRAY[]::text[],'[]'::jsonb FROM {s}.reaction_context LIMIT 1"
        ],
        "transport": ["UPDATE {s}.reaction_context SET transport=NOT transport"],
        "context_identity": [
            "INSERT INTO {s}.reaction_context SELECT 'wrong-identity',resource,version,reaction_entity_id,source,dataset,row_id,namespace,identifier,taxon,direction,raw_direction,transport,source_record_sha256,source_record_type,sources,diagnostics FROM {s}.reaction_context LIMIT 1",
            "INSERT INTO {s}.reaction_participant SELECT 'wrong-identity',ordinal,relation_key,evidence_ordinal,participant_entity_id,role,compartment,stoichiometry,member_ordinal,raw_stoichiometry,context_status FROM {s}.reaction_participant",
            "DELETE FROM {s}.reaction_participant WHERE context_id<>'wrong-identity'",
            "DELETE FROM {s}.reaction_context WHERE context_id<>'wrong-identity'",
        ],
        "ontology_edge": [
            "UPDATE {s}.entity_ontology_relation SET child_entity_id=parent_entity_id,parent_entity_id=child_entity_id WHERE relation_id='a-has-d'"
        ],
        "ontology_edge_missing": [
            "DELETE FROM {s}.entity_ontology_relation WHERE relation_id='a-b'"
        ],
        "scoped_missing": [
            "DELETE FROM {s}.ontology_closure WHERE resource='chemont' AND descendant_entity_id='term-0' AND ancestor_entity_id='term-2'"
        ],
        "scoped_depth": [
            "UPDATE {s}.ontology_closure SET depth=10 WHERE resource='chemont' AND descendant_entity_id='term-0' AND ancestor_entity_id='term-2'"
        ],
        "scoped_spurious": [
            "INSERT INTO {s}.ontology_closure VALUES('chemont','1.0.0','subclass','term-0','term-3',3)"
        ],
        "global_missing": [
            "DELETE FROM {s}.ontology_ancestor WHERE hierarchy_kind='subclass' AND descendant_entity_id='term-0' AND ancestor_entity_id='term-3'"
        ],
        "derived_index_missing": ["DROP INDEX {s}.ontology_closure_ancestor_idx"],
    }
    with psycopg.connect(loaded["dsn"]) as conn:
        for statement in statements[damage]:
            conn.execute(
                sql.SQL(statement).format(
                    s=sql.Identifier(loaded["schema"]),
                    b=sql.Identifier("foreign_" + loaded["schema"]),
                )
            )
    with pytest.raises(audit.VerificationError):
        inspect(loaded)


@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    [
        "resource",
        "version",
        "owner_kind",
        "owner_key",
        "evidence_ordinal",
        "ordinal",
        "term",
        "value",
        "source",
        "dataset",
        "scope",
    ],
)
def test_measurement_view_provenance_must_match_original_annotation(loaded, field):
    with psycopg.connect(loaded["dsn"]) as conn:
        definition = conn.execute(
            "SELECT pg_get_viewdef(%s::regclass,true)",
            (loaded["schema"] + ".annotation_quantities",),
        ).fetchone()[0]
        if field in ("ordinal", "evidence_ordinal"):
            replacement = f"COALESCE(annotations.{field},0)+1 AS {field},"
        else:
            replacement = f"'forged'::text AS {field},"
        needle = f"    {field},"
        assert needle in definition
        conn.execute(
            sql.SQL("CREATE OR REPLACE VIEW {}.annotation_quantities AS ").format(
                sql.Identifier(loaded["schema"])
            )
            + sql.SQL(definition.replace(needle, replacement, 1))
        )
    with pytest.raises(audit.VerificationError, match="Measurement view"):
        inspect(loaded)


@pytest.mark.integration
@pytest.mark.parametrize(
    "field", ["sources", "raw_direction", "diagnostics", "member_ordinal", "context_status"]
)
def test_reaction_provenance_fields_cannot_be_changed_without_detection(loaded, field):
    statements = {
        "sources": "UPDATE {s}.reaction_context SET sources=ARRAY['forged']",
        "raw_direction": "UPDATE {s}.reaction_context SET raw_direction='forged'",
        "diagnostics": "UPDATE {s}.reaction_context SET diagnostics='[\"forged\"]'::jsonb",
        "member_ordinal": "UPDATE {s}.reaction_participant SET member_ordinal=999",
        "context_status": "UPDATE {s}.reaction_participant SET context_status='forged'",
    }
    with psycopg.connect(loaded["dsn"]) as conn:
        conn.execute(sql.SQL(statements[field]).format(s=sql.Identifier(loaded["schema"])))
    with pytest.raises(audit.VerificationError, match="Reaction provenance " + field):
        inspect(loaded)


@pytest.mark.integration
@pytest.mark.parametrize("table", ["reaction_context", "reaction_participant"])
@pytest.mark.parametrize("damage", ["missing", "namespace", "action", "deferred"])
def test_immediate_reaction_foreign_keys_require_exact_contract(loaded, table, damage):
    keys, parent, parent_keys = (
        (
            ["resource", "version", "reaction_entity_id"],
            "entities",
            ["resource", "version", "entity_key"],
        )
        if table == "reaction_context"
        else (["context_id"], "reaction_context", ["context_id"])
    )
    namespace = sql.Identifier(loaded["schema"])
    other = sql.Identifier("other_" + loaded["schema"])
    columns = sql.SQL(",").join(map(sql.Identifier, keys))
    parent_columns = sql.SQL(",").join(map(sql.Identifier, parent_keys))
    with psycopg.connect(loaded["dsn"]) as conn:
        constraint = conn.execute(
            "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname=%s AND c.contype='f'",
            (loaded["schema"], table),
        ).fetchone()[0]
        conn.execute(
            sql.SQL("ALTER TABLE {}.{} DROP CONSTRAINT {}").format(
                namespace, sql.Identifier(table), sql.Identifier(constraint)
            )
        )
        if damage != "missing":
            target = namespace
            if damage == "namespace":
                conn.execute(sql.SQL("CREATE SCHEMA {}").format(other))
                conn.execute(
                    sql.SQL("CREATE TABLE {}.{} AS TABLE {}.{}").format(
                        other, sql.Identifier(parent), namespace, sql.Identifier(parent)
                    )
                )
                conn.execute(
                    sql.SQL("ALTER TABLE {}.{} ADD PRIMARY KEY({})").format(
                        other, sql.Identifier(parent), parent_columns
                    )
                )
                target = other
            suffix = (
                " ON DELETE CASCADE"
                if damage == "action"
                else " DEFERRABLE INITIALLY DEFERRED"
                if damage == "deferred"
                else ""
            )
            conn.execute(
                sql.SQL(
                    "ALTER TABLE {}.{} ADD CONSTRAINT {} FOREIGN KEY({}) REFERENCES {}.{}({})"
                    + suffix
                ).format(
                    namespace,
                    sql.Identifier(table),
                    sql.Identifier(constraint),
                    columns,
                    target,
                    sql.Identifier(parent),
                    parent_columns,
                )
            )
    with pytest.raises(audit.VerificationError, match="Immediate foreign key contract"):
        inspect(loaded)


@pytest.mark.integration
@pytest.mark.parametrize("case", ["conflicting_attributes", "unscoped", "array_shape"])
def test_pg_only_provenance_checks_handle_exact_diagnostics_and_missing_member_positions(
    tmp_path, postgres_dsn, monkeypatch, case
):
    entities, relations, _ = deepcopy(reaction_fixture())
    for relation in relations:
        evidence = relation["evidence"][0]
        evidence["annotations"] = [
            a for a in evidence["annotations"] if a["term"] != SOURCE_RECORD_TYPE
        ]
        if case == "array_shape":
            evidence["annotations"].append(
                dict(
                    term=SOURCE_RECORD_TYPE,
                    value="array",
                    quantity=None,
                    source="rhea",
                    dataset=None,
                    scope="relation",
                )
            )
        if case == "unscoped":
            evidence["row_id"] = ""
    relations[0]["sources"] = ["rhea", None, "", "α"]
    relations[1]["sources"] = ["raw-source", "z"]
    if case == "conflicting_attributes":
        evidence = relations[0]["evidence"][0]
        for term, value in (
            (CONVERSION_DIRECTION, "left_to_right"),
            (CELLULAR_LOCATION, "another-location"),
            ("stoichiometry", "conflicting-coefficient"),
        ):
            evidence["annotations"].append(
                dict(
                    term=term,
                    value=value,
                    quantity=None,
                    source="rhea",
                    dataset=None,
                    scope="object",
                )
            )
        evidence["upstream_id"] = "unrelated"
    resource(tmp_path, "rhea", rows=(entities, relations, []))
    manifest = release(tmp_path, {"rhea": "1.0.0"})
    schema = "provenance_" + uuid.uuid4().hex
    load_release(tmp_path, manifest, postgres_dsn, schema=schema, batch_size=1)
    (tmp_path / "resources").rename(tmp_path / "unavailable")
    monkeypatch.setattr(
        audit, "load_pinned_release", lambda *_args, **_kwargs: pytest.fail("No artifact reads")
    )
    monkeypatch.setattr(
        audit, "iter_rows", lambda *_args, **_kwargs: pytest.fail("No artifact reads")
    )
    with psycopg.connect(postgres_dsn) as conn:
        result = audit._reaction_provenance_checks(conn, schema)
        assert result == {"contexts": 2 if case == "unscoped" else 1, "participant_occurrences": 2}
        contexts = conn.execute(
            sql.SQL("SELECT sources,diagnostics FROM {}.reaction_context").format(
                sql.Identifier(schema)
            )
        ).fetchall()
        assert all(diagnostics for _, diagnostics in contexts)
        assert any("α" in sources and "" in sources for sources, _ in contexts)


@pytest.mark.integration
def test_pg_only_rebuild_needs_no_parquets_and_rolls_back_every_product(loaded, monkeypatch):
    (loaded["root"] / "resources").rename(loaded["root"] / "unavailable_resources")
    monkeypatch.setattr(
        audit, "load_pinned_release", lambda *_: pytest.fail("No artifact validation")
    )
    monkeypatch.setattr(
        audit, "iter_rows", lambda *_args, **_kwargs: pytest.fail("No artifact reads")
    )
    namespace = sql.Identifier(loaded["schema"])
    tables = (
        "reaction_context",
        "reaction_participant",
        "cosmos_edge",
        "cosmos_label",
        "network_registry",
        "metsigdb_membership",
        "subset_build_metadata",
    )

    def snapshot():
        with psycopg.connect(loaded["dsn"]) as conn:
            return {
                table: conn.execute(
                    sql.SQL("SELECT to_jsonb(t) FROM {s}.{t} t ORDER BY to_jsonb(t)::text").format(
                        s=namespace, t=sql.Identifier(table)
                    )
                ).fetchall()
                for table in tables
            }

    before = snapshot()
    result = audit.pg_only_rebuild(loaded["dsn"], schema=loaded["schema"])
    assert result["rolled_back"] is True and result["products"]["reactions"]["participants"] == 2
    assert result["network_query_source_rows"]["reactions"] == 1
    assert snapshot() == before


@pytest.mark.integration
def test_optional_baseline_compares_only_identical_unique_source_events(loaded):
    baseline = "old_" + uuid.uuid4().hex
    with psycopg.connect(loaded["dsn"]) as conn:
        current, old = sql.Identifier(loaded["schema"]), sql.Identifier(baseline)
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(old))
        conn.execute(
            sql.SQL(
                "CREATE TABLE {b}.reaction_context AS SELECT * FROM {s}.reaction_context"
            ).format(b=old, s=current)
        )
        conn.execute(
            sql.SQL("ALTER TABLE {b}.reaction_context ADD COLUMN payload_json text").format(b=old)
        )
        conn.execute(
            sql.SQL("UPDATE {b}.reaction_context SET payload_json=%s").format(b=old),
            (loaded["original"][2][0]["payload_json"],),
        )
        conn.execute(
            sql.SQL(
                "CREATE TABLE {b}.reaction_participant AS SELECT * FROM {s}.reaction_participant"
            ).format(b=old, s=current)
        )
    result = inspect(loaded, baseline_schema=baseline)["baseline"]
    assert result["status"] == "compared" and result["counts"]["identical_source_text_rows"] == 1
    with psycopg.connect(loaded["dsn"]) as conn:
        conn.execute(
            sql.SQL("UPDATE {}.reaction_participant SET compartment='changed'").format(
                sql.Identifier(baseline)
            )
        )
    result = inspect(loaded, baseline_schema=baseline)["baseline"]
    assert result["status"] == "differences"
    assert result["counts"]["member_positions_roles_compartments_coefficients"] == 1
    assert "canonical entity IDs" in result["scope"]
    with psycopg.connect(loaded["dsn"]) as conn:
        conn.execute(
            sql.SQL("UPDATE {}.reaction_context SET payload_json='{{}}'").format(
                sql.Identifier(baseline)
            )
        )
    result = inspect(loaded, baseline_schema=baseline)["baseline"]
    assert result["status"] == "no_identical_comparable_rows"
    assert result["counts"]["excluded_missing_or_changed_original_text"] == 1


@pytest.mark.integration
def test_read_only_validation_enforces_a_read_only_transaction(loaded, monkeypatch):
    def attempt_write(conn, schema):
        conn.execute(
            sql.SQL("UPDATE {}.entities SET label='changed'").format(sql.Identifier(schema))
        )

    monkeypatch.setattr(audit, "_derived_checks", attempt_write)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        inspect(loaded)


@pytest.mark.integration
def test_failed_pg_only_rebuild_rolls_back_existing_reaction_rows(loaded, monkeypatch):
    from omnipath_subsets.compatibility.record_layout import cosmos

    namespace = sql.Identifier(loaded["schema"])
    with psycopg.connect(loaded["dsn"]) as conn:
        before = conn.execute(
            sql.SQL("SELECT * FROM {}.reaction_participant ORDER BY context_id,ordinal").format(
                namespace
            )
        ).fetchall()

    def fail(*_args):
        raise ValueError("product failed")

    monkeypatch.setattr(cosmos, "rebuild", fail)
    with pytest.raises(ValueError, match="product failed"):
        audit.pg_only_rebuild(loaded["dsn"], schema=loaded["schema"])
    with psycopg.connect(loaded["dsn"]) as conn:
        assert (
            conn.execute(
                sql.SQL("SELECT * FROM {}.reaction_participant ORDER BY context_id,ordinal").format(
                    namespace
                )
            ).fetchall()
            == before
        )


def test_base_comparison_batch_is_bounded_and_counts_include_duplicate_nested_entries(monkeypatch):
    records = [deepcopy(fixture_rows()[0][0]) for _ in range(5)]
    for index, item in enumerate(records):
        item["entity_key"] = str(index)

    class Resource:
        source, version, entities_path, relations_path = "fixture", "1", "entities", "relations"
        files = {
            "entities.parquet": type("File", (), {"rows": 5})(),
            "relations.parquet": type("File", (), {"rows": 0})(),
        }

    def rows(path, **_):
        yield from records if path == "entities" else []

    monkeypatch.setattr(audit, "iter_rows", rows)
    batches = []
    monkeypatch.setattr(
        audit, "_compare_batch", lambda _c, _s, _t, _r, _v, rows: batches.append(len(rows))
    )
    counts = audit._compare_resource(None, "unused", Resource(), 2)
    assert batches == [2, 2, 1]
    assert counts["identifiers"] == 10 and counts["annotations"] == 15


def test_cli_pg_only_mode_takes_no_artifact_paths_and_writes_json(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIPATH_DATABASE_URL", "unused")
    monkeypatch.setattr(
        audit,
        "pg_only_rebuild",
        lambda *_args, **_kwargs: {"status": "success", "rolled_back": True},
    )
    output = tmp_path / "report.json"
    assert (
        audit.main(["--schema", "test_release", "--pg-only-rebuild", "--output", str(output)]) == 0
    )
    assert json.loads(output.read_text())["rolled_back"] is True
