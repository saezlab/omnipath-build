"""Source attributes support PostgreSQL-only reaction rebuilds with explicit gaps."""

from copy import deepcopy
import hashlib
import json
import uuid

import psycopg
from psycopg import sql
import pytest

from omnipath_core.source_attributes import (
    CELLULAR_LOCATION,
    CONVERSION_DIRECTION,
    SOURCE_RECORD_REFERENCE,
    SOURCE_RECORD_SHA256_PREFIX,
    SOURCE_RECORD_TYPE,
)
from omnipath_postgres import loader
from omnipath_postgres.reactions import direction_context, participant_context, rebuild_reactions
from test_postgres import resource, release, exists
from test_projection import annotation, entity, QUANTITY, fixture_rows


def source_attributes(raw, *, direction=None, location=None):
    body = json.dumps(raw)
    attrs = [
        annotation(
            SOURCE_RECORD_REFERENCE,
            value=SOURCE_RECORD_SHA256_PREFIX + hashlib.sha256(body.encode()).hexdigest(),
            scope="relation",
        ),
        annotation(
            SOURCE_RECORD_TYPE,
            value="object"
            if isinstance(raw, dict)
            else "array"
            if isinstance(raw, list)
            else "scalar",
            scope="relation",
        ),
    ]
    if direction is not None:
        attrs.append(annotation(CONVERSION_DIRECTION, value=direction, scope="relation"))
    if location is not None:
        attrs.append(annotation(CELLULAR_LOCATION, value=location, scope="object"))
    return body, attrs


def reaction_fixture(*, annotated=True, raw=None):
    raw = (
        {"unnecessary_original_body": "sensitive source detail", "values": [1, 2, 3]}
        if raw is None
        else raw
    )
    activity = entity(
        "published:activity", "RX1", namespace="rhea", entity_type="molecular_activity"
    )
    molecule = entity(
        "published:chemical", "15377", namespace="chebi", entity_type="chemical_entity"
    )
    edges, payloads = [], []
    for ordinal, predicate, location, coefficient in (
        (0, "has_input", "c", "n"),
        (1, "has_output", "e", "2n"),
    ):
        body, attrs = source_attributes(raw, direction="REVERSIBLE", location=location)
        attrs.append(
            annotation(
                "stoichiometry",
                value=coefficient,
                scope="relation",
                quantity=deepcopy(QUANTITY) if ordinal == 0 else None,
            )
        )
        item = deepcopy(fixture_rows()[1][0])
        item.update(
            relation_key=f"published:{predicate}",
            subject_entity_key=activity["entity_key"],
            subject_type="molecular_activity",
            object_entity_key=molecule["entity_key"],
            object_type="chemical_entity",
            predicate=predicate,
            sources=["rhea"],
            evidence_count=1,
            annotations=[],
            evidence=[
                dict(
                    source="reported-source",
                    dataset="reactions",
                    row_id="reactions:1",
                    upstream_id=f"reactions:1:member:{ordinal}",
                    annotations=attrs if annotated else [],
                )
            ],
        )
        edges.append(item)
        payloads.append(
            dict(
                relation_key=item["relation_key"],
                entity_key=None,
                source="reported-source",
                row_id="reactions:1",
                payload_json=body,
            )
        )
    return [activity, molecule], edges, payloads


@pytest.mark.parametrize(
    "assertions,resource,expected,diagnostic",
    [
        ([], "rhea", None, None),
        (["REVERSIBLE", "reversible"], "rhea", "reversible", None),
        (["LEFT-TO-RIGHT"], "recon3d", "left_to_right", None),
        (["RIGHT-TO-LEFT"], "kegg", "left_to_right", "kegg_inputs_already_oriented_right_to_left"),
        (["RIGHT-TO-LEFT"], "rhea", None, "unsupported_right_to_left_orientation"),
        (["REVERSIBLE", "LEFT-TO-RIGHT"], "rhea", None, "contradictory_direction_assertions"),
        (["UNKNOWN"], "rhea", None, "unsupported_direction_assertion"),
    ],
)
def test_published_direction_assertions_do_not_invent_defaults(
    assertions, resource, expected, diagnostic
):
    direction, original, diagnostics = direction_context(assertions, resource)
    assert direction == expected
    if diagnostic:
        assert diagnostic in diagnostics
    else:
        assert diagnostics == []
    if not assertions:
        assert original is None


def test_symbolic_coefficients_exact_quantities_and_conflicting_compartments():
    first = annotation("stoichiometry", value="n", quantity=deepcopy(QUANTITY), scope="relation")
    row = dict(
        row_id="reactions:1",
        upstream_id="reactions:1:member:0",
        annotations=[
            first,
            annotation(CELLULAR_LOCATION, value="c", scope="object"),
            annotation(CELLULAR_LOCATION, value="e", scope="object"),
        ],
    )
    actual = participant_context(row)
    assert actual["compartment"] is None and actual["context_status"] == "compartment_conflict"
    assert actual["raw_stoichiometry"] == "n" and actual["stoichiometry"] == {
        "annotations": [first]
    }
    assert actual["member_ordinal"] == 0
    row["annotations"].append(annotation("stoichiometry", value="2n"))
    actual = participant_context(row)
    assert actual["raw_stoichiometry"] is None
    assert "contradictory_stoichiometry_assertions" in actual["diagnostics"]


@pytest.mark.integration
def test_postgres_only_rebuild_preserves_attributes_after_parquets_are_unavailable(
    tmp_path, postgres_dsn
):
    original = reaction_fixture()
    directory = resource(tmp_path, "rhea", rows=original)
    schema = "reactions_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path, release(tmp_path, {"rhea": "1.0.0"}), postgres_dsn, schema=schema, batch_size=1
    )
    assert result.validated_payload_rows == {"rhea": 2} and "payloads" not in result.counts
    directory.rename(tmp_path / "raw_parquets_unavailable")
    namespace = sql.Identifier(schema)
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (schema + ".payloads",)).fetchone() == (None,)
        assert not conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND column_name='payload_json'",
            (schema,),
        ).fetchall()
        before = conn.execute(
            sql.SQL("SELECT * FROM {}.reaction_context").format(namespace)
        ).fetchall()
        stats = rebuild_reactions(conn, schema)
        assert stats == dict(contexts=1, participants=2, contexts_with_diagnostics=0)
        assert (
            conn.execute(sql.SQL("SELECT * FROM {}.reaction_context").format(namespace)).fetchall()
            == before
        )
        source_hash = conn.execute(
            sql.SQL(
                "SELECT source_record_sha256,source_record_type,direction,transport FROM {}.reaction_context"
            ).format(namespace)
        ).fetchone()
        assert source_hash == (
            hashlib.sha256(original[2][0]["payload_json"].encode()).hexdigest(),
            "object",
            "reversible",
            True,
        )
        parties = conn.execute(
            sql.SQL(
                "SELECT role,compartment,raw_stoichiometry,stoichiometry FROM {}.reaction_participant ORDER BY member_ordinal"
            ).format(namespace)
        ).fetchall()
        assert [row[:3] for row in parties] == [("reactant", "c", "n"), ("product", "e", "2n")]
        assert parties[0][3]["annotations"][0]["quantity"] == QUANTITY
        actual = conn.execute(
            sql.SQL("SELECT relation_key,record_json FROM {}.relations").format(namespace)
        ).fetchall()
        assert dict(actual) == {row["relation_key"]: row for row in original[1]}


@pytest.mark.integration
@pytest.mark.parametrize(
    "problem", ["legacy_payload_only", "invalid_hash", "two_hashes", "relation_only_hash"]
)
def test_unusable_reaction_provenance_rolls_back_import_with_rebuild_guidance(
    tmp_path, postgres_dsn, problem
):
    rows = reaction_fixture(annotated=problem != "legacy_payload_only")
    if problem == "invalid_hash":
        rows[1][0]["evidence"][0]["annotations"][0]["value"] = (
            SOURCE_RECORD_SHA256_PREFIX + "not-a-hash"
        )
    elif problem == "two_hashes":
        rows[1][0]["evidence"][0]["annotations"].append(
            annotation(SOURCE_RECORD_REFERENCE, value=SOURCE_RECORD_SHA256_PREFIX + "0" * 64)
        )
    elif problem == "relation_only_hash":
        for item in rows[1]:
            item["annotations"] = item["evidence"][0]["annotations"]
            item["evidence"][0]["annotations"] = []
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="source-record SHA reference"):
        loader.load_release(
            tmp_path, release(tmp_path, {"rhea": "1.0.0"}), postgres_dsn, schema=schema
        )
    assert not exists(postgres_dsn, schema)


@pytest.mark.integration
@pytest.mark.parametrize(
    "raw,expected_type", [(["not", "an", "object"], "array"), ("scalar source", "scalar")]
)
def test_source_shape_diagnostic_survives_without_raw_storage(
    tmp_path, postgres_dsn, raw, expected_type
):
    resource(tmp_path, "rhea", rows=reaction_fixture(raw=raw))
    schema = "reactions_" + uuid.uuid4().hex
    loader.load_release(tmp_path, release(tmp_path, {"rhea": "1.0.0"}), postgres_dsn, schema=schema)
    with psycopg.connect(postgres_dsn) as conn:
        shape, diagnostics = conn.execute(
            sql.SQL("SELECT source_record_type,diagnostics FROM {}.reaction_context").format(
                sql.Identifier(schema)
            )
        ).fetchone()
        assert shape == expected_type and "unsupported_payload_shape" in diagnostics


@pytest.mark.integration
@pytest.mark.parametrize(
    "problem", ["stale_hash", "changed_body", "whitespace", "conflicting_duplicate"]
)
def test_exact_source_text_must_match_published_reaction_hash_atomically(
    tmp_path, postgres_dsn, problem
):
    rows = reaction_fixture()
    if problem == "stale_hash":
        for item in rows[1]:
            item["evidence"][0]["annotations"][0]["value"] = SOURCE_RECORD_SHA256_PREFIX + "0" * 64
    elif problem == "changed_body":
        rows[2][0]["payload_json"] = '{"changed": true}'
    elif problem == "whitespace":
        original = rows[2][0]["payload_json"]
        changed = json.dumps(json.loads(original), separators=(",", ":"))
        assert changed != original and json.loads(changed) == json.loads(original)
        rows[2][0]["payload_json"] = changed
    else:
        different = deepcopy(rows[2][0])
        different["payload_json"] = '{"different": "source observation"}'
        rows[2].append(different)
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="source-record SHA reference does not match payload"):
        loader.load_release(
            tmp_path,
            release(tmp_path, {"rhea": "1.0.0"}),
            postgres_dsn,
            schema=schema,
            batch_size=1,
        )
    assert not exists(postgres_dsn, schema)


@pytest.mark.integration
def test_repeated_identical_raw_rows_and_evidence_references_are_valid(tmp_path, postgres_dsn):
    rows = reaction_fixture()
    rows[2].extend(deepcopy(rows[2]))
    occurrence = deepcopy(rows[1][0]["evidence"][0])
    occurrence["annotations"].append(deepcopy(occurrence["annotations"][0]))
    rows[1][0]["evidence"].append(occurrence)
    rows[1][0]["evidence_count"] += 1
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path,
        release(tmp_path, {"rhea": "1.0.0"}),
        postgres_dsn,
        schema=schema,
        batch_size=1,
    )
    assert result.validated_payload_rows == {"rhea": 4}
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute(
            sql.SQL("SELECT count(*) FROM {}.reaction_participant").format(sql.Identifier(schema))
        ).fetchone() == (3,)
        assert conn.execute("SELECT to_regclass(%s)", (schema + ".payloads",)).fetchone() == (None,)


@pytest.mark.integration
@pytest.mark.parametrize(
    "source,row_id",
    [
        ("another-source", "reactions:1"),
        ("reported-source", "reactions:2"),
        (None, "reactions:1"),
        ("reported-source", None),
        (None, None),
        ("", "reactions:1"),
        ("reported-source", ""),
    ],
)
def test_raw_hash_matching_keeps_distinct_source_and_row_scopes_separate(
    tmp_path, postgres_dsn, source, row_id
):
    rows = reaction_fixture()
    other = deepcopy(rows[1][0]["evidence"][0])
    body, attrs = source_attributes(
        {"separate": "source event"}, direction="LEFT-TO-RIGHT", location="c"
    )
    other.update(source=source, row_id=row_id, upstream_id=None, annotations=attrs)
    rows[1][0]["evidence"].append(other)
    rows[1][0]["evidence_count"] += 1
    rows[2].append(
        dict(
            relation_key=rows[1][0]["relation_key"],
            entity_key=None,
            source=source,
            row_id=row_id,
            payload_json=body,
        )
    )
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path,
        release(tmp_path, {"rhea": "1.0.0"}),
        postgres_dsn,
        schema=schema,
        batch_size=1,
    )
    assert result.validated_payload_rows == {"rhea": 3}


@pytest.mark.integration
@pytest.mark.parametrize(
    "source,row_id",
    [
        (None, "reactions:1"),
        ("reported-source", None),
        (None, None),
        ("", "reactions:1"),
        ("reported-source", ""),
    ],
)
def test_matching_null_or_empty_source_scope_still_rejects_stale_hash(
    tmp_path, postgres_dsn, source, row_id
):
    rows = reaction_fixture()
    rows[1][0]["evidence"][0].update(source=source, row_id=row_id)
    rows[2][0].update(source=source, row_id=row_id, payload_json='{"stale": true}')
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="source-record SHA reference does not match payload"):
        loader.load_release(
            tmp_path,
            release(tmp_path, {"rhea": "1.0.0"}),
            postgres_dsn,
            schema=schema,
        )
    assert not exists(postgres_dsn, schema)


@pytest.mark.integration
def test_every_matching_evidence_occurrence_is_checked(tmp_path, postgres_dsn):
    rows = reaction_fixture()
    stale = deepcopy(rows[1][0]["evidence"][0])
    stale["annotations"][0]["value"] = SOURCE_RECORD_SHA256_PREFIX + "0" * 64
    rows[1][0]["evidence"].append(stale)
    rows[1][0]["evidence_count"] += 1
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="source-record SHA reference does not match payload"):
        loader.load_release(
            tmp_path,
            release(tmp_path, {"rhea": "1.0.0"}),
            postgres_dsn,
            schema=schema,
        )
    assert not exists(postgres_dsn, schema)


@pytest.mark.integration
def test_declared_source_shape_must_match_existing_payload_atomically(tmp_path, postgres_dsn):
    rows = reaction_fixture()
    rows[1][0]["evidence"][0]["annotations"][1]["value"] = "array"
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="source-record type does not match payload"):
        loader.load_release(
            tmp_path,
            release(tmp_path, {"rhea": "1.0.0"}),
            postgres_dsn,
            schema=schema,
        )
    assert not exists(postgres_dsn, schema)


@pytest.mark.integration
def test_absent_source_type_remains_an_explicit_diagnostic(tmp_path, postgres_dsn):
    rows = reaction_fixture()
    for item in rows[1]:
        item["evidence"][0]["annotations"] = [
            a for a in item["evidence"][0]["annotations"] if a["term"] != SOURCE_RECORD_TYPE
        ]
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    loader.load_release(tmp_path, release(tmp_path, {"rhea": "1.0.0"}), postgres_dsn, schema=schema)
    with psycopg.connect(postgres_dsn) as conn:
        shape, diagnostics = conn.execute(
            sql.SQL("SELECT source_record_type,diagnostics FROM {}.reaction_context").format(
                sql.Identifier(schema)
            )
        ).fetchone()
        assert shape is None and "unrecorded_source_record_type" in diagnostics


@pytest.mark.integration
@pytest.mark.parametrize("raw_policy", ["absent", "null_body", "unmatched_source", "unmatched_row"])
def test_annotations_remain_authoritative_when_no_matching_nonnull_body_exists(
    tmp_path, postgres_dsn, raw_policy
):
    rows = reaction_fixture()
    if raw_policy == "absent":
        rows[2].clear()
    else:
        for payload in rows[2]:
            if raw_policy == "null_body":
                payload["payload_json"] = None
            elif raw_policy == "unmatched_source":
                payload.update(source="unmatched-source", payload_json='{"different": true}')
            else:
                payload.update(row_id="unmatched-row", payload_json='{"different": true}')
    resource(tmp_path, "rhea", rows=rows)
    schema = "reactions_" + uuid.uuid4().hex
    result = loader.load_release(
        tmp_path,
        release(tmp_path, {"rhea": "1.0.0"}),
        postgres_dsn,
        schema=schema,
    )
    assert result.validated_payload_rows == {"rhea": len(rows[2])}
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute(
            sql.SQL("SELECT count(*) FROM {}.reaction_context").format(sql.Identifier(schema))
        ).fetchone() == (1,)
