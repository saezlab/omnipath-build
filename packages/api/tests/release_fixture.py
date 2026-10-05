"""Tiny resolved releases for product validation, without source builds."""

import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_core import SERVING_SCHEMA_VERSION
from omnipath_core.keys import entity_key, relation_key
from omnipath_core.schema import ENTITY_SCHEMA, RELATION_SCHEMA, PAYLOAD_SCHEMA
from omnipath_core.source_attributes import (
    CELLULAR_LOCATION,
    CONVERSION_DIRECTION,
    SOURCE_RECORD_REFERENCE,
    SOURCE_RECORD_SHA256_PREFIX,
    SOURCE_RECORD_TYPE,
)


def annotation(
    term, value=None, *, source="fixture", dataset="fixture", scope="relation", quantity=None
):
    return dict(
        term=term, value=value, quantity=quantity, source=source, dataset=dataset, scope=scope
    )


def source_context_annotations(raw, *, source="fixture", dataset="fixture", compartment=None):
    """Publish mapper/build context without needing raw records after the load."""
    result = [
        annotation(
            SOURCE_RECORD_REFERENCE,
            SOURCE_RECORD_SHA256_PREFIX + hashlib.sha256(json.dumps(raw).encode()).hexdigest(),
            source=source,
            dataset=dataset,
        ),
        annotation(
            SOURCE_RECORD_TYPE,
            "object" if isinstance(raw, dict) else "array" if isinstance(raw, list) else "scalar",
            source=source,
            dataset=dataset,
        ),
    ]
    if isinstance(raw, dict):
        for field in ("direction", "conversion_direction"):
            if raw.get(field) is not None:
                result.append(
                    annotation(CONVERSION_DIRECTION, raw[field], source=source, dataset=dataset)
                )
    if compartment is not None:
        result.append(
            annotation(
                CELLULAR_LOCATION, compartment, source=source, dataset=dataset, scope="object"
            )
        )
    return result


def entity(
    identifier,
    entity_type="small_molecule",
    namespace="chebi",
    *,
    taxon="",
    label=None,
    aliases=(),
    annotations=(),
):
    return dict(
        entity_key=entity_key(entity_type, namespace, identifier),
        entity_type=entity_type,
        namespace=namespace,
        identifier=identifier,
        taxon=taxon,
        label=label or identifier,
        has_hierarchy=False,
        parent_count=0,
        child_count=0,
        identifiers=[
            dict(ns=namespace, id=identifier, is_canonical=True, source="canonical"),
            *[dict(ns=ns, id=value, is_canonical=False, source="raw") for ns, value in aliases],
        ],
        annotations=[
            {key: value for key, value in item.items() if key != "scope"} for item in annotations
        ],
    )


def relation(
    subject,
    predicate,
    obj,
    *,
    source="fixture",
    dataset="fixture",
    row_id="1",
    annotations=(),
    statement_kind="relation",
    upstream_id="",
    sign=0,
):
    key = relation_key(
        subject["entity_key"],
        predicate,
        obj["entity_key"],
        annotations,
        statement_kind=statement_kind,
    )
    return dict(
        relation_key=key,
        statement_kind=statement_kind,
        subject_entity_key=subject["entity_key"],
        subject_label=subject["label"],
        subject_type=subject["entity_type"],
        predicate=predicate,
        object_entity_key=obj["entity_key"],
        object_label=obj["label"],
        object_type=obj["entity_type"],
        taxon="",
        is_directed=predicate != "interacts_with",
        sign=sign,
        category="association",
        interaction_class="directed",
        sources=[source],
        evidence_count=1,
        evidence=[
            dict(
                source=source,
                dataset=dataset,
                row_id=row_id,
                upstream_id=upstream_id,
                annotations=list(annotations),
            )
        ],
        annotations=list(annotations),
    )


def payload(owner, raw, *, source="fixture", row_id="1"):
    return dict(
        relation_key=owner.get("relation_key"),
        entity_key=owner.get("entity_key"),
        source=source,
        row_id=row_id,
        payload_json=json.dumps(raw),
    )


def write_resource(root, source, entities, relations=(), payloads=(), version="1.0.0"):
    # Record caps count original source rows; one source record may yield several spokes.
    assert len({row["row_id"] for rel in relations for row in rel["evidence"]}) <= 20
    directory = root / "resources" / source / version
    directory.mkdir(parents=True)
    files = {}
    for name, schema, rows in (
        ("entities.parquet", ENTITY_SCHEMA, entities),
        ("relations.parquet", RELATION_SCHEMA, relations),
        ("evidence_payloads.parquet", PAYLOAD_SCHEMA, payloads),
    ):
        path = directory / name
        pq.write_table(pa.Table.from_pylist(list(rows), schema=schema), path)
        files[name] = dict(
            rows=len(rows),
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    (directory / "build_manifest.json").write_text(
        json.dumps(
            dict(
                schema_version=1,
                serving_schema_version=SERVING_SCHEMA_VERSION,
                resource=source,
                version=version,
                max_records=None,
                files=files,
            )
        )
    )
    return directory


def write_release(root, sources, version="2026.09"):
    path = root / "release.json"
    selections = (
        dict(sources) if isinstance(sources, dict) else {source: "1.0.0" for source in sources}
    )
    path.write_text(json.dumps(dict(schema_version=1, version=version, resources=selections)))
    return path
