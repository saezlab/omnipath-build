#!/usr/bin/env python3
"""Build and inspect a bounded, entirely offline SIGNOR migration fixture.

This uses the real inputs_v2 SIGNOR parser and RelationBuilder, native entity
resolver, Parquet writer and immutable resource publication. The small reference
and source rows are synthetic fixtures; no biological assertions are implied.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch


FIXTURE = Path(__file__).parent / "fixtures" / "signor.json"
SOURCE_ROWS = 24  # More than the permitted cap, so every run exercises truncation.
RECORD_CAP = 20


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def record_limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max-records must be an integer from 1 to 20") from exc
    if not 1 <= number <= RECORD_CAP:
        raise argparse.ArgumentTypeError("max-records must be between 1 and 20")
    return number


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def source_fixture(path: Path, fixture: dict) -> list[dict]:
    rows = []
    for index in range(SOURCE_ROWS):
        row = dict(fixture["rows"][index % len(fixture["rows"])])
        row["Interaction identifier(s)"] = f"signor:SIGNOR-SMOKE-{index + 1}"
        row["Publication Identifier(s)"] = f"pubmed:{123456 + index}"
        row["Interaction annotation(s)"] += f" (fixture record {index + 1})"
        rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def compact_reference(path: Path, fixture: dict) -> Path:
    """Write two synthetic entities in the production compact index format.

    This intentionally avoids source hub downloads and full reference builds.
    CompactWriter validates codec round trips; FullRuntime and the native
    decision policy consume the resulting index without a mocked resolver.
    """
    from omnipath_build.reference.compact_index import FORMAT, CompactWriter, atomic_json
    from omnipath_build.reference.full_index import partition
    from omnipath_resolver.observations import CODES, key

    if path.exists():
        require(path.is_dir() and not path.is_symlink(), f"Unsafe fixture reference path: {path}")
        manifest = path / "manifest.json"
        if manifest.exists():
            previous = json.loads(manifest.read_text())
            require(
                previous.get("fixture") is True,
                f"Refusing to replace a non-fixture reference: {path}",
            )
            if previous.get("reference_fingerprint") == digest(FIXTURE):
                from omnipath_resolver.index import FullRuntime

                try:
                    runtime = FullRuntime(path)
                except (OSError, ValueError):
                    pass  # Interrupted fixture; rebuild these private, synthetic assets.
                else:
                    runtime.close()
                    return path
        shutil.rmtree(path)
    path.mkdir(parents=True)
    dictionary = b"entity identifier candidates protein uniprot taxon reviewed"
    dictionaries = {}
    for kind in ("entities", "identifiers"):
        filename = f"dictionaries/{kind}.zstd"
        target = path / filename
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(dictionary)
        dictionaries[kind] = {
            "file": filename,
            "bytes": len(dictionary),
            "sha256": digest(target),
        }
    entities, identifiers = [], []
    for number, item in enumerate(fixture["reference"], start=1):
        eid = "uniprot:" + item["identifier"]
        candidate = [number, eid, 2, eid, False, True]
        entities.append(
            (
                eid.encode(),
                {
                    "record": {
                        "entity_id": eid,
                        "kind": 2,
                        "anchor": eid,
                        "taxon": "9606",
                        "label": item["label"],
                        "identifiers": item["identifiers"],
                    },
                    "meta": {
                        "id": number,
                        "entity_id": eid,
                        "kind": 2,
                        "anchor": eid,
                        "quarantined": False,
                        "reviewed": True,
                    },
                },
            )
        )
        for namespace, identifier in item["identifiers"]:
            for taxon in ("", "9606"):
                identifiers.append(
                    (
                        key(2, 1, namespace, taxon, identifier),
                        {
                            "gene": namespace == "genesymbol",
                            "products": False,
                            "candidates": [candidate],
                        },
                        identifier,
                    )
                )
    require(len(entities) <= RECORD_CAP, "Reference entity fixture exceeds the cap")
    require(len(identifiers) <= RECORD_CAP, "Reference lookup fixture exceeds the cap")
    files, counts = {}, {}
    for kind, objects in (("entities", entities), ("identifiers", identifiers)):
        counts[kind] = 0
        for number in range(256):
            shard = f"{number:02x}"
            writer = CompactWriter(path / kind / shard, kind, dictionary)
            rows = [
                (row[0], row[1])
                for row in objects
                if partition(row[0].decode() if kind == "entities" else row[2]) == shard
            ]
            writer.put_objects(rows)
            report = writer.close()
            counts[kind] += report["records"]
            files[f"{kind}/{shard}/data.mdb"] = report["bytes"]
    atomic_json(
        path / "manifest.json",
        {
            "format": FORMAT,
            "complete": True,
            "reference_fingerprint": digest(FIXTURE),
            "namespace_codes": CODES,
            "dictionaries": dictionaries,
            "files": files,
            "counts": counts,
            "fixture": True,
            "description": fixture["description"],
        },
    )
    return path


class LocalDownload:
    """Only a fixture opener is substituted; parsing and mapping stay real."""

    def __init__(self, path: Path):
        self.path = path
        self.url = path.as_uri()

    def open(self, **_kwargs):
        return SimpleNamespace(result=StringIO(self.path.read_text(encoding="utf-8")))


def blocked_download(*_args, **_kwargs):
    raise RuntimeError("Network downloads are disabled for the migration smoke build")


def inspect_resource(directory: Path) -> dict:
    """Check every manifest table's schema and checksum, then query a small DuckDB sample."""
    import duckdb
    import pyarrow.parquet as pq
    from omnipath_core.schema import PUBLISHED_TABLES, SERVING_TABLES

    manifest = json.loads((directory / "build_manifest.json").read_text())
    tables = {**PUBLISHED_TABLES, **SERVING_TABLES}
    require(
        set(manifest["files"]) == {f"{table}.parquet" for table in tables},
        "The manifest does not list exactly the published and serving tables",
    )
    files = {}
    for table, schema in tables.items():
        name = f"{table}.parquet"
        path = directory / name
        parquet = pq.ParquetFile(path)
        expected = manifest["files"][name]
        require(parquet.schema_arrow.equals(schema), f"Unexpected schema in {name}")
        require(parquet.metadata.num_rows == expected["rows"], f"Row count differs in {name}")
        require(path.stat().st_size == expected["size_bytes"], f"Size differs in {name}")
        require(digest(path) == expected["sha256"], f"Checksum differs in {name}")
        files[name] = dict(expected, schema_verified=True, checksum_verified=True)
    with duckdb.connect(config={"threads": "1", "memory_limit": "256MB"}) as connection:
        relations = connection.execute(
            "SELECT s.identifier AS subject,r.subject_label,r.predicate,"
            "o.identifier AS object,r.object_label,r.sign,r.evidence_count "
            "FROM read_parquet(?) r "
            "JOIN read_parquet(?) s ON s.entity_key=r.subject_entity_key "
            "JOIN read_parquet(?) o ON o.entity_key=r.object_entity_key "
            "ORDER BY r.subject_label,r.object_label LIMIT 20",
            [str(directory / "relation.parquet"), *[str(directory / "entity.parquet")] * 2],
        )
        sample = [
            dict(zip((column[0] for column in relations.description), row))
            for row in relations.fetchall()
        ]
    return {
        "resource": manifest["resource"],
        "version": manifest["version"],
        "directory": str(directory),
        "max_records": manifest["max_records"],
        "files": files,
        "relations": sample,
    }


def verify_fixture(result: dict, rows: list[dict]) -> dict:
    import pyarrow.parquet as pq

    def table(name: str) -> list[dict]:
        return pq.read_table(result["files"][name]).to_pylist()

    entities = {entity["entity_id"]: entity for entity in table("entity")}
    identifiers = table("entity_identifier")
    relations = {relation["relation_id"]: relation for relation in table("relation")}
    evidence = table("relation_evidence")
    relation_annotations = table("relation_annotation")
    payloads = table("evidence_payloads")
    canonical = {entity["identifier"]: entity for entity in entities.values()}
    require(
        canonical.keys() == {"P04637", "P0DP23"}
        and all(entity["namespace"] == "uniprot" for entity in entities.values()),
        "The native resolver did not produce both canonical protein identifiers",
    )
    require(all(entity["taxon"] == "9606" for entity in entities.values()), "Entity taxa were lost")
    require(
        all(
            any(
                identifier["entity_id"] == entity_id
                and identifier["is_canonical"]
                and (identifier["ns"], identifier["id"]) == ("uniprot", entity["identifier"])
                for identifier in identifiers
            )
            for entity_id, entity in entities.items()
        ),
        "An entity lacks its canonical identifier row",
    )
    require(result["resolution_stats"]["unresolved_entities"] == 0, "An entity was unresolved")
    require(result["resolution_stats"]["resolved_entities"] >= 2, "Resolution was not exercised")
    require(
        any(
            identifier["id"] == "Q15086"
            and not identifier["is_canonical"]
            and identifier["entity_id"] == canonical["P04637"]["entity_id"]
            for identifier in identifiers
        ),
        "The observed secondary accession was lost",
    )
    entity_keys = {entity["entity_key"] for entity in entities.values()}
    require(
        all(
            {relation["subject_entity_key"], relation["object_entity_key"]} <= entity_keys
            for relation in relations.values()
        ),
        "A relation endpoint is missing from the entity table",
    )
    require(len(payloads) == len(rows), "Not every consumed source record retained a payload")
    require(
        Counter(
            json.dumps(json.loads(payload["payload_json"]), sort_keys=True) for payload in payloads
        )
        == Counter(json.dumps(row, sort_keys=True) for row in rows),
        "Raw source payloads changed",
    )
    relation_keys = {relation["relation_key"] for relation in relations.values()}
    require(
        all(payload["relation_key"] in relation_keys for payload in payloads),
        "A raw payload is not linked to a published relation",
    )
    require(len(evidence) == len(rows), "Source evidence was dropped during consolidation")
    require(
        {item["row_id"] for item in evidence} == {payload["row_id"] for payload in payloads},
        "Relation evidence and raw payloads disagree on the consumed source rows",
    )
    per_relation = Counter(item["relation_id"] for item in evidence)
    require(
        per_relation.keys() == relations.keys()
        and all(
            relation["evidence_count"] == per_relation[relation_id]
            for relation_id, relation in relations.items()
        ),
        "Consolidated evidence counts do not agree",
    )
    require(
        all(relation["predicate"] == "affects" for relation in relations.values()),
        "The SIGNOR Biolink predicate was lost",
    )
    publications = {
        annotation["value"]
        for item in evidence
        for annotation in item["annotations"]
        if annotation["term"] == "publications"
    }
    require(
        publications == {"PMID:" + row["Publication Identifier(s)"].split(":")[1] for row in rows},
        "Publication evidence was lost",
    )
    require(
        all(relation["object_direction_qualifier"] for relation in relations.values()),
        "Biolink relation qualifiers were lost",
    )
    activity = {
        annotation["relation_id"]
        for annotation in relation_annotations
        if annotation["term"] == "object_aspect_qualifier" and annotation["value"] == "activity"
    }
    require(
        all(
            relation["object_aspect_qualifier"] == ["activity"] and relation_id in activity
            for relation_id, relation in relations.items()
        ),
        "The Biolink activity qualifier was lost",
    )
    return {
        "canonical_identifiers": sorted(canonical),
        "secondary_accession_resolved": "Q15086 -> P04637",
        "payloads_and_evidence_preserved": len(rows),
        "resolution": result["resolution_stats"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/migration-smoke"))
    parser.add_argument("--version", default="0.1.0", help="New immutable numeric resource version")
    parser.add_argument("--max-records", type=record_limit, default=RECORD_CAP)
    parser.add_argument(
        "--inspect",
        type=Path,
        metavar="RESOURCE_VERSION_DIR",
        help="Verify and inspect an existing version without building",
    )
    args = parser.parse_args(argv)
    if args.inspect:
        print(json.dumps(inspect_resource(args.inspect.resolve()), indent=2))
        return 0

    from omnipath_build.pipeline import build_resource
    from omnipath_build.versioning import validate_version
    from pypath.inputs_v2 import signor

    validate_version(args.version)
    root = args.output_dir.resolve()
    target = root / "resources" / "signor" / args.version
    if target.exists():
        raise FileExistsError(
            f"Resource signor/{args.version} already exists; choose a new version"
        )
    fixture = json.loads(FIXTURE.read_text())
    fixture_dir = root / "fixtures" / args.version
    fixture_dir.mkdir(parents=True, exist_ok=True)
    rows = source_fixture(fixture_dir / "signor.tsv", fixture)
    reference = compact_reference(fixture_dir / "reference", fixture)
    dataset = signor.resource.datasets()["interactions"]
    raw_parser = dataset._raw_parser
    consumed = 0

    def bounded_parser(opener, **kwargs):
        nonlocal consumed
        for row in raw_parser(opener, **kwargs):
            consumed += 1
            require(consumed <= args.max_records, "The parser consumed a record beyond the cap")
            yield row

    # The isolated worker imports the real mapper from pypath.inputs_v2.signor.
    # Only this parent process opens the synthetic input file.
    os.environ["OMNIPATH_BUILD_DUCKDB_THREADS"] = "1"
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(dataset, "download", LocalDownload(fixture_dir / "signor.tsv"))
        )
        stack.enter_context(patch.object(dataset, "_raw_parser", bounded_parser))
        stack.enter_context(patch("pypath.inputs_v2.base.download_and_open", blocked_download))
        result = build_resource(
            "signor",
            version=args.version,
            output_dir=root,
            datasets=["interactions"],
            max_records=args.max_records,
            batch_workers=1,
            batch_size=20,
            max_batch_records=20,
            max_batch_relations=20,
            duckdb_memory_limit="256MB",
            min_free_disk_bytes=0,
            library_dir=reference,
            cache_dir=root / "pypath-data",
            progress=False,
        )
    require(consumed == args.max_records, "The source cap was not exercised")
    checks = verify_fixture(result, rows[:consumed])
    report = inspect_resource(target)
    before = digest(target / "build_manifest.json")
    try:
        build_resource(
            "signor", version=args.version, output_dir=root, max_records=args.max_records
        )
    except FileExistsError:
        pass
    else:
        raise RuntimeError("A duplicate resource version was accepted")
    require(
        digest(target / "build_manifest.json") == before, "Duplicate build changed the manifest"
    )
    inspect_resource(target)  # Published files must also remain unchanged after rejection.
    report.update(
        fixture=True,
        source_rows_available=len(rows),
        source_rows_consumed=consumed,
        duplicate_version_rejected=True,
        checks=checks,
    )
    report_path = root / f"inspection-{args.version}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
