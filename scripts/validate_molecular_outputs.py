#!/usr/bin/env python3
"""Validate real molecular Parquets and their API without opening a network port.

Run with the workspace Python environment. Resource selectors are names (latest
available version) or NAME/VERSION. All SQL baselines read original Parquets;
TestClient uses temporary symlinks, with any existing disposable serving indexes.
Python client checks use a temporary hard-linked offline snapshot on the data
root's filesystem and streaming checksums. Staging directories are removed on
exit; immutable resource files are never written. Unsupported hardlinks report
unavailable: escaping symlinks and full dataset copies are not used. Missing
observed cases are reported, not replaced with synthetic biological examples.
Exit status is 1 on failed checks.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import errno
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any

import duckdb
from fastapi.testclient import TestClient
import pyarrow.parquet as pq

from omnipath_api.engine import ParquetServingEngine
from omnipath_api.server import create_app
from omnipath_api.serving_index import projected_paths
from omnipath_api.settings import Settings
from omnipath_client import Client
from omnipath_core.molecular_forms import normalize_molecular_form
from omnipath_core.schema import ENTITY_SCHEMA, PAYLOAD_SCHEMA, RELATION_SCHEMA

_UNIPROT_ACCESSION = "([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})"
_UNIPROT_REPORTED_PRODUCT = _UNIPROT_ACCESSION + "(-[0-9]+)?(-PRO_[0-9]+)?"

CLIENT_SAMPLE_LIMIT = 5
CLIENT_PRODUCT_LIMIT = 100
_VALIDATOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_CLIENT_SHA256 = hashlib.sha256(Path(inspect.getfile(Client)).read_bytes()).hexdigest()


def _literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _version_key(path: Path) -> tuple:
    return tuple((0, int(s)) if s.isdigit() else (1, s) for s in re.split(r"(\d+)", path.name))


def select_resource(root: Path, selector: str) -> Path:
    parts = selector.split("/")
    if len(parts) == 2:
        path = root / "resources" / parts[0] / parts[1]
        if (path / "entities.parquet").exists():
            return path
    elif len(parts) == 1:
        candidates = [p.parent for p in (root / "resources" / selector).glob("*/entities.parquet")]
        if candidates:
            return max(candidates, key=_version_key)
    raise ValueError(f"No built Parquet resource for {selector!r} in {root}")


def _form(value: Any) -> str:
    return json.dumps(normalize_molecular_form(value), sort_keys=True, separators=(",", ":"))


def _identity(ev: dict, *, api: bool = False) -> tuple:
    return (
        ev.get("source") or "unknown",
        ev.get("dataset"),
        ev.get("rowId" if api else "row_id"),
        ev.get("upstreamId" if api else "upstream_id"),
        _form(ev.get("subjectMolecularForm" if api else "subject_molecular_form")),
        _form(ev.get("objectMolecularForm" if api else "object_molecular_form")),
    )


def _standalone_identity(ev: dict) -> tuple:
    return (
        ev.get("source") or "unknown",
        ev.get("dataset"),
        ev.get("row_id"),
        ev.get("upstream_id"),
        _form(ev.get("molecular_form")),
    )


def _product_predicate(filters: dict) -> tuple[str, list]:
    kind = "protein" if "protein_entity_keys" in filters else "transcript"
    mode = filters["molecular_endpoint_mode"]
    sides = (
        ["subject"]
        if mode == "source"
        else ["object"]
        if mode == "target"
        else ["subject", "object"]
    )
    isoform = (filters.get("isoform_identifiers") or [None])[0]
    clauses, parameters = [], []
    for side in sides:
        clause = f"ev.{side}_molecular_form.{kind}_entity_key=?"
        parameters.append(filters[kind + "_entity_keys"][0])
        if isoform:
            clause += f" AND ev.{side}_molecular_form.isoform_identifier.ns || ':' || ev.{side}_molecular_form.isoform_identifier.id=?"
            parameters.append(isoform)
        clauses.append(f"({clause})")
    return "(" + (" AND " if mode == "both" else " OR ").join(clauses) + ")", parameters


class Validator:
    def __init__(
        self,
        folder: Path,
        *,
        examples: int,
        max_export_relations: int,
        projection_mode: str = "auto",
    ):
        self.folder = folder.resolve()
        self.examples = examples
        self.max_export_relations = max_export_relations
        self.projection_mode = projection_mode
        self.report: dict[str, Any] = {
            "resource": f"{folder.parent.name}/{folder.name}",
            "path": str(self.folder),
            "tables": {},
            "checks": [],
            "timings": [],
            "examples": [],
            "coverage": {},
        }
        self.db = duckdb.connect()
        self.db.execute("SET threads=2")
        self.db.execute("SET memory_limit='2GB'")
        self.db.execute("SET enable_progress_bar=false")

    def timing(self, item):
        self.report["timings"].append(item)
        if os.environ.get("OMNIPATH_VALIDATION_PROGRESS") == "1":
            operation = re.sub(r"[0-9a-f]{64}", "<key>", item["operation"])
            print(
                json.dumps(
                    {
                        "resource": self.report["resource"],
                        "operation": operation,
                        "kind": item["kind"],
                        "elapsed_ms": item["elapsed_ms"],
                    }
                ),
                file=sys.stderr,
                flush=True,
            )

    def check(self, name: str, passed: bool, **details):
        self.report["checks"].append(
            {"name": name, "status": "passed" if passed else "failed", **details}
        )

    def unavailable(self, name: str, reason: str):
        self.report["checks"].append({"name": name, "status": "unavailable", "reason": reason})

    def sql(self, name: str, query: str, params=None):
        started = time.perf_counter()
        result = self.db.execute(query, params or []).fetchall()
        self.timing(
            {
                "operation": name,
                "kind": "sql",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return result

    def zero(self, name: str, query: str):
        count = self.sql(name, query)[0][0]
        self.check(name, count == 0, invalid_count=count)

    def request(self, client: TestClient, method: str, path: str, **kwargs):
        started = time.perf_counter()
        response = client.request(method, path, **kwargs)
        self.timing(
            {
                "operation": f"{method} {path}",
                "kind": "api",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "status_code": response.status_code,
                "parameters": kwargs.get("json") or kwargs.get("params") or {},
            }
        )
        if response.status_code != 200:
            raise ValueError(f"{method} {path}: HTTP {response.status_code}: {response.text[:500]}")
        return response

    def schemas(self) -> bool:
        valid = True
        for name, expected in (
            ("entities", ENTITY_SCHEMA),
            ("relations", RELATION_SCHEMA),
            ("evidence_payloads", PAYLOAD_SCHEMA),
        ):
            path = self.folder / f"{name}.parquet"
            if not path.exists():
                self.check(f"schema.{name}", False, reason="Missing contract table")
                valid = False
                continue
            schema = pq.read_schema(path)
            errors = []
            for field in expected:
                if field.name not in schema.names:
                    errors.append(f"Missing {field.name}")
                elif not schema.field(field.name).type.equals(field.type):
                    errors.append(
                        f"{field.name}: actual {schema.field(field.name).type}; expected {field.type}"
                    )
            extras = sorted(set(schema.names) - set(expected.names))
            self.check(f"schema.{name}", not errors, errors=errors, additional_columns=extras)
            valid &= not errors
            self.report["tables"][name] = {
                "rows": pq.ParquetFile(path).metadata.num_rows,
                "bytes": path.stat().st_size,
                "schema": str(schema),
            }
            self.db.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({_literal(path)})")
        return valid

    def structural(self):
        # Projection UNNEST avoids correlated delimiter joins that retain large
        # parent evidence arrays when these views are expanded through unions.
        self.db.execute(
            "CREATE VIEW occurrences AS SELECT relation_key, subject_reference_entity_key, object_reference_entity_key, unnest(evidence) AS ev FROM relations"
        )
        self.db.execute("""CREATE VIEW forms AS
            SELECT 'relation' AS owner_type, relation_key AS owner_key, 'source' AS side,
                subject_reference_entity_key AS reference_key, ev.subject_molecular_form AS form, ev.annotations FROM occurrences
            UNION ALL SELECT 'relation', relation_key, 'target', object_reference_entity_key,
                ev.object_molecular_form, ev.annotations FROM occurrences
            UNION ALL SELECT 'entity', entity_key, 'standalone', reference_entity_key,
                ev.molecular_form, ev.annotations FROM
                (SELECT entity_key, reference_entity_key, unnest(evidence) AS ev FROM entities)""")
        self.sql(
            "molecular.product_references",
            """CREATE TEMP TABLE product_refs AS
            SELECT DISTINCT form.protein_entity_key AS entity_key, 'protein' AS kind FROM forms WHERE form.protein_entity_key IS NOT NULL
            UNION SELECT DISTINCT form.transcript_entity_key, 'transcript' FROM forms WHERE form.transcript_entity_key IS NOT NULL""",
        )
        for table, key in (("entities", "entity_key"), ("relations", "relation_key")):
            self.zero(
                f"{table}.unique_nonempty_keys",
                f"SELECT count(*) - count(DISTINCT NULLIF({key}, '')) FROM {table}",
            )
        self.zero(
            "relations.endpoint_closure_and_source_type",
            """SELECT count(*) FROM relations r
            LEFT JOIN entities s ON s.entity_key=r.subject_entity_key LEFT JOIN entities o ON o.entity_key=r.object_entity_key
            WHERE s.entity_key IS NULL OR o.entity_key IS NULL OR s.entity_type IS DISTINCT FROM r.subject_type OR o.entity_type IS DISTINCT FROM r.object_type""",
        )
        self.zero(
            "molecular.product_closure_and_type",
            """SELECT count(*) FROM product_refs p LEFT JOIN entities e USING(entity_key)
            WHERE e.entity_key IS NULL OR (p.kind='protein' AND e.entity_type NOT IN ('protein','polypeptide'))
            OR (p.kind='transcript' AND e.entity_type NOT IN ('rna_product','transcript','rna','RNA'))""",
        )
        self.zero(
            "entities.supported_gene_references",
            """SELECT count(*) FROM entities WHERE
            (starts_with(reference_entity_key,'entrez:') AND (NOT regexp_full_match(reference_entity_key,'entrez:[0-9]+')
                OR NOT coalesce(list_contains(gene_reference_keys,reference_entity_key),FALSE)))
            OR EXISTS (SELECT 1 FROM UNNEST(gene_reference_keys) AS keys(k) WHERE NOT coalesce(regexp_full_match(k,'entrez:[0-9]+'),FALSE))""",
        )
        self.zero(
            "entities.native_fallback_or_supported_gene",
            """SELECT count(*) FROM entities WHERE reference_entity_key IS NULL OR
            (NOT starts_with(reference_entity_key,'entrez:') AND reference_entity_key <> namespace || ':' || identifier)""",
        )
        self.zero(
            "relations.reference_consistency",
            """SELECT count(*) FROM relations r JOIN entities s ON s.entity_key=r.subject_entity_key JOIN entities o ON o.entity_key=r.object_entity_key
            WHERE r.subject_reference_entity_key IS DISTINCT FROM s.reference_entity_key OR r.object_reference_entity_key IS DISTINCT FROM o.reference_entity_key""",
        )
        self.zero(
            "relations.evidence_count",
            "SELECT count(*) FROM relations WHERE evidence_count IS DISTINCT FROM coalesce(len(evidence),0)",
        )
        # These namespaces have unscoped reusable record identities. Name-only
        # source fallbacks may intentionally include context in their hashes.
        self.zero(
            "molecular.no_form_specific_product_keys",
            """SELECT count(*) FROM entities e WHERE namespace IN ('entrez','uniprot','ensp','enst','refseq')
            AND entity_key <> sha256(lower(trim(entity_type)) || chr(0) || lower(trim(namespace)) || chr(0) || trim(identifier) || chr(0))""",
        )
        self.zero(
            "molecular.reusable_uniprot_products",
            f"""WITH pointed_forms AS (
                SELECT form, annotations, side, form.protein_entity_key AS product_key, 'protein' AS product_kind
                FROM forms WHERE form.protein_entity_key IS NOT NULL
                UNION ALL
                SELECT form, annotations, side, form.transcript_entity_key, 'transcript'
                FROM forms WHERE form.transcript_entity_key IS NOT NULL
            )
            SELECT count(*) FROM pointed_forms f JOIN entities e ON e.entity_key=f.product_key
            WHERE e.namespace='uniprot'
            AND NOT coalesce(regexp_full_match(e.identifier,'{_UNIPROT_ACCESSION}'),FALSE)
            AND NOT coalesce(
                regexp_full_match(e.identifier,'{_UNIPROT_REPORTED_PRODUCT}')
                AND f.product_kind='protein'
                AND EXISTS (SELECT 1 FROM UNNEST(f.annotations) AS annotations(a)
                    WHERE a.term='omnipath:protein_mapping_status' AND a.value='reported'
                    AND a.source='resolver' AND a.dataset='molecular_reference'
                    AND a.scope IS NOT DISTINCT FROM CASE f.side
                        WHEN 'source' THEN 'subject' WHEN 'target' THEN 'object' ELSE NULL END)
                AND ((f.form.isoform_identifier.ns='uniprot' AND f.form.isoform_identifier.id=e.identifier)
                    OR EXISTS (SELECT 1 FROM UNNEST(f.form.sequence_identifiers) AS identifiers(i)
                        WHERE i.ns='uniprot' AND i.id=e.identifier)),FALSE)""",
        )
        self.check(
            "molecular.no_entity_level_form_enumeration",
            "molecular_form" not in pq.read_schema(self.folder / "entities.parquet").names,
            scope="Stable reusable product keys; exact reported native identifiers require occurrence provenance. Forms remain on occurrences. Does not compare to unavailable upstream input.",
        )
        self.zero(
            "payloads.owner_closure",
            """SELECT count(*) FROM evidence_payloads p
            WHERE (p.relation_key IS NOT NULL AND NOT EXISTS(SELECT 1 FROM relations r WHERE r.relation_key=p.relation_key))
            OR (p.entity_key IS NOT NULL AND NOT EXISTS(SELECT 1 FROM entities e WHERE e.entity_key=p.entity_key))""",
        )
        counts = self.sql(
            "molecular.coverage",
            """SELECT
            count(*) FILTER(WHERE owner_type='relation' AND form IS NOT NULL),
            count(*) FILTER(WHERE owner_type='entity' AND form IS NOT NULL),
            count(*) FILTER(WHERE form.isoform_identifier.id IS NOT NULL),
            count(*) FILTER(WHERE len(form.modifications)>0), count(*) FILTER(WHERE len(form.variants)>0),
            count(*) FILTER(WHERE len(form.sequence_identifiers)>0) FROM forms""",
        )[0]
        coverage = dict(
            zip(
                (
                    "relation_side_forms",
                    "standalone_forms",
                    "isoforms",
                    "modifications",
                    "variants",
                    "sequence_identifiers",
                ),
                counts,
            )
        )
        coverage["paired_occurrences"] = self.sql(
            "molecular.paired_occurrences",
            "SELECT count(*) FROM occurrences WHERE ev.subject_molecular_form IS NOT NULL AND ev.object_molecular_form IS NOT NULL",
        )[0][0]
        coverage["distinct_referenced_products"] = self.sql(
            "molecular.distinct_products", "SELECT count(DISTINCT entity_key) FROM product_refs"
        )[0][0]
        self.report["coverage"] = coverage
        for name, count in coverage.items():
            if count == 0:
                self.unavailable(
                    f"observed.{name}", "No such molecular case is present in the selected dataset"
                )

    def relation_page(self, name, keys, predicate, params, total, limit=5):
        # Valid sort ties can select different rows. Check bounded page size
        # and independent membership, without prescribing a tied first page.
        matched = {
            key
            for (key,) in self.sql(
                f"baseline.{name}.page_keys",
                f"SELECT DISTINCT relation_key FROM occurrences WHERE relation_key IN (SELECT UNNEST(?::VARCHAR[])) AND ({predicate})",
                [keys, *params],
            )
        }
        self.check(
            f"api.{name}.page",
            len(keys) == min(limit, total) and len(keys) == len(set(keys)) and set(keys) == matched,
            expected_page_relations=min(limit, total),
            actual_page_relations=len(keys),
            unmatched_relation_keys=sorted(set(keys) - matched),
        )
        return matched

    def evidence(self, client, filters, example_name):
        side = filters["molecular_endpoint_mode"]
        field = (
            "protein_entity_key" if "protein_entity_keys" in filters else "transcript_entity_key"
        )
        product = filters[field.replace("_key", "_keys")][0]
        predicate, params = (
            f"ev.{'subject' if side == 'source' else 'object'}_molecular_form.{field}=?",
            [product],
        )
        isoform = (filters.get("isoform_identifiers") or [None])[0]
        if isoform:
            predicate += f" AND ev.{'subject' if side == 'source' else 'object'}_molecular_form.isoform_identifier.ns || ':' || ev.{'subject' if side == 'source' else 'object'}_molecular_form.isoform_identifier.id=?"
            params.append(isoform)
        total, occurrences = self.sql(
            f"baseline.{example_name}",
            f"SELECT count(DISTINCT relation_key), count(*) FROM occurrences WHERE {predicate}",
            params,
        )[0]
        result = self.request(
            client, "POST", "/relations/search", json={"filters": filters, "limit": 5}
        ).json()
        self.check(
            f"api.{example_name}.count",
            result["total"] == total,
            expected_relations=total,
            actual_relations=result["total"],
            expected_occurrences=occurrences,
        )
        matched_keys = self.relation_page(
            example_name,
            [row["relationPk"] for row in result["relations"]],
            predicate,
            params,
            total,
        )
        example = {
            "name": example_name,
            "filters": filters,
            "expected_relations": total,
            "expected_occurrences": occurrences,
            "sample_relation_keys": [],
        }
        for row in result["relations"]:
            key = row["relationPk"]
            example["sample_relation_keys"].append(key)
            if key not in matched_keys:
                continue
            expected = self.sql(
                f"baseline.{example_name}.evidence",
                f"SELECT ev FROM occurrences WHERE relation_key=? AND {predicate}",
                [key, *params],
            )
            actual = self.request(
                client, "GET", f"/relations/{key}/evidence", params={"filters": json.dumps(filters)}
            ).json()["evidence"]
            self.check(
                f"api.{example_name}.paired_evidence.{key}",
                Counter(_identity(ev) for (ev,) in expected)
                == Counter(_identity(ev, api=True) for ev in actual),
                expected_occurrences=len(expected),
                actual_occurrences=len(actual),
            )
            ids = [ev["relationEvidencePk"] for ev in actual]
            self.check(f"api.{example_name}.occurrence_identity.{key}", len(ids) == len(set(ids)))
        context = self.request(
            client,
            "GET",
            f"/entities/{product}/molecular-context",
            params={
                "view": "product",
                "limit": 5,
                **({"isoform_identifier": isoform} if isoform else {}),
            },
        ).json()
        # Product context uses either endpoint; independently count that wider scope.
        context_predicates, context_params = [], []
        for endpoint in ("subject", "object"):
            clause = f"ev.{endpoint}_molecular_form.{field}=?"
            context_params.append(product)
            if isoform:
                clause += f" AND ev.{endpoint}_molecular_form.isoform_identifier.ns || ':' || ev.{endpoint}_molecular_form.isoform_identifier.id=?"
                context_params.append(isoform)
            context_predicates.append(f"({clause})")
        expected_context = self.sql(
            f"baseline.{example_name}.product_context",
            f"SELECT count(DISTINCT relation_key) FROM occurrences WHERE {' OR '.join(context_predicates)}",
            context_params,
        )[0][0]
        self.check(
            f"api.{example_name}.product_context",
            context["relationsTotal"] == expected_context,
            expected=expected_context,
            actual=context["relationsTotal"],
        )
        context_predicate = " OR ".join(context_predicates)
        matched_context_keys = self.relation_page(
            f"{example_name}.product_context",
            [row["relation"]["relationPk"] for row in context["relations"]],
            context_predicate,
            context_params,
            expected_context,
        )
        for row in context["relations"]:
            key = row["relation"]["relationPk"]
            if key not in matched_context_keys:
                continue
            expected = self.sql(
                f"baseline.{example_name}.product_context.evidence",
                f"SELECT ev FROM occurrences WHERE relation_key=? AND ({context_predicate})",
                [key, *context_params],
            )
            self.check(
                f"api.{example_name}.product_context.paired_evidence.{key}",
                Counter(_identity(ev) for (ev,) in expected)
                == Counter(_identity(ev) for ev in row["evidence"]),
                expected_occurrences=len(expected),
                actual_occurrences=len(row["evidence"]),
            )
        standalone_predicate = f"ev.molecular_form.{field}=?"
        standalone_params = [product]
        if isoform:
            standalone_predicate += " AND ev.molecular_form.isoform_identifier.ns || ':' || ev.molecular_form.isoform_identifier.id=?"
            standalone_params.append(isoform)
        standalone_count = self.sql(
            f"baseline.{example_name}.standalone_count",
            f"SELECT count(*) FROM (SELECT unnest(evidence) AS ev FROM entities) WHERE {standalone_predicate}",
            standalone_params,
        )[0][0]
        standalone_expected = self.sql(
            f"baseline.{example_name}.standalone",
            f"""SELECT entity_key, ev FROM (
                SELECT entity_key, file_row_number, unnest(evidence) AS ev,
                    unnest(range(0,len(evidence))) AS occurrence_index
                FROM read_parquet({_literal(self.folder / "entities.parquet")}, file_row_number=TRUE)
            ) WHERE {standalone_predicate} ORDER BY file_row_number, occurrence_index LIMIT 5""",
            standalone_params,
        )
        expected_standalone = Counter(
            (key, _standalone_identity(ev)) for key, ev in standalone_expected
        )
        observed_standalone = Counter(
            (item["entityPk"], _standalone_identity(item["occurrence"]))
            for item in context["standaloneEvidence"]
        )
        self.check(
            f"api.{example_name}.standalone_forms",
            observed_standalone == expected_standalone
            and sum(observed_standalone.values()) == min(5, standalone_count),
            expected_occurrences=standalone_count,
            actual_page_occurrences=sum(observed_standalone.values()),
        )
        self.export(client, filters, predicate, params, total, example_name)
        self.report["examples"].append(example)

    def export(self, client, filters, predicate, params, total, name):
        if total > self.max_export_relations:
            self.unavailable(
                f"export.{name}",
                f"{total} rows exceed --max-export-relations={self.max_export_relations}",
            )
            return
        response = self.request(
            client, "POST", "/export", json={"filters": filters, "format": "parquet"}
        )
        rows = pq.read_table(io.BytesIO(response.content)).to_pylist()
        self.check(f"export.{name}.count", len(rows) == total, expected=total, actual=len(rows))
        valid_pairs, missing, record_mismatches = True, set(), set()
        expected_rows = self.sql(
            f"baseline.export.{name}.all_evidence",
            f"SELECT relation_key, ev FROM occurrences WHERE {predicate}",
            params,
        )
        expected_by_key = {}
        for key, ev in expected_rows:
            expected_by_key.setdefault(key, Counter()).update([_identity(ev)])
        actual_keys = Counter(row["relation_key"] for row in rows)
        expected_keys = Counter({key: 1 for key in expected_by_key})
        self.check(
            f"export.{name}.relation_keys",
            actual_keys == expected_keys,
            missing_relation_keys=sorted((expected_keys - actual_keys).elements()),
            unexpected_relation_keys=sorted((actual_keys - expected_keys).elements()),
        )
        export_products = {
            p["entity_key"]: p for row in rows for p in row.get("referenced_product_records") or []
        }
        stored_products = dict(
            (key, (type_, ns, identifier, ref))
            for key, type_, ns, identifier, ref in self.sql(
                f"baseline.export.{name}.products",
                "SELECT entity_key, entity_type, namespace, identifier, reference_entity_key FROM entities WHERE entity_key IN (SELECT UNNEST(?::VARCHAR[]))",
                [list(export_products)],
            )
        )
        for row in rows:
            valid_pairs &= row["relation_key"] in expected_by_key and expected_by_key[
                row["relation_key"]
            ] == Counter(_identity(ev) for ev in row["evidence"])
            refs = {
                form[field]
                for ev in row["evidence"]
                for side in ("subject", "object")
                if (form := ev.get(f"{side}_molecular_form"))
                for field in ("protein_entity_key", "transcript_entity_key")
                if form.get(field)
            }
            products = {p["entity_key"]: p for p in row.get("referenced_product_records") or []}
            missing.update(refs - products.keys())
            for key, product in products.items():
                stored = stored_products.get(key)
                if stored != tuple(
                    product.get(k)
                    for k in ("entity_type", "namespace", "identifier", "reference_entity_key")
                ):
                    record_mismatches.add(key)
        self.check(f"export.{name}.paired_occurrences", valid_pairs)
        self.check(
            f"export.{name}.product_closure",
            not missing and not record_mismatches,
            missing_product_keys=sorted(missing),
            mismatched_product_keys=sorted(record_mismatches),
        )

    def gene(self, client):
        candidates = self.sql(
            "baseline.gene_example",
            "SELECT reference_entity_key FROM entities WHERE starts_with(reference_entity_key,'entrez:') GROUP BY reference_entity_key ORDER BY count(*) DESC, reference_entity_key LIMIT 1",
        )
        if not candidates:
            self.unavailable(
                "api.gene_navigation", "No supported scalar NCBI gene reference in selected dataset"
            )
            return
        ref = candidates[0][0]
        expected = self.sql(
            "baseline.gene_members",
            "SELECT entity_key, entity_type FROM entities WHERE reference_entity_key=?",
            [ref],
        )
        groups = self.request(
            client,
            "POST",
            "/entities/groups",
            json={"strategy": "gene_reference", "group_key": "gene:" + ref, "member_limit": 100},
        ).json()["groups"]
        observed = (
            {(m["entityPk"], m["entityType"]) for m in groups[0]["members"]} if groups else set()
        )
        expected_pairs = set(expected)
        self.check(
            "api.gene_members_and_source_types",
            bool(groups)
            and groups[0]["member_count"] == len(expected_pairs)
            and observed <= expected_pairs
            and len(observed) == min(100, len(expected_pairs)),
            reference_entity_key=ref,
            expected_members=len(expected_pairs),
            observed_members=len(observed),
        )
        context = self.request(
            client, "GET", f"/entities/gene:{ref}/molecular-context", params={"limit": 5}
        ).json()
        expected_count = self.sql(
            "baseline.gene_relations",
            "SELECT count(*) FROM relations WHERE subject_reference_entity_key=? OR object_reference_entity_key=?",
            [ref, ref],
        )[0][0]
        self.check(
            "api.gene_relation_count",
            context["relationsTotal"] == expected_count,
            reference_entity_key=ref,
            expected=expected_count,
            actual=context["relationsTotal"],
        )
        self.report["examples"].append(
            {
                "name": "gene_navigation",
                "reference_entity_key": ref,
                "members": len(expected_pairs),
                "relations": expected_count,
            }
        )
        for type_ in sorted({t for _, t in expected_pairs}):
            typed = self.request(
                client,
                "POST",
                "/entities/groups",
                json={
                    "strategy": "gene_reference",
                    "group_key": "gene:" + ref,
                    "filters": {"entity_types": [type_]},
                    "member_limit": 100,
                },
            ).json()["groups"]
            self.check(
                f"api.gene_type_filter.{type_}",
                bool(typed)
                and typed[0]["member_count"] == sum(t == type_ for _, t in expected_pairs)
                and all(m["entityType"] == type_ for m in typed[0]["members"]),
            )

    def api(self, root: Path):
        with tempfile.TemporaryDirectory(prefix="omnipath-molecular-validation-") as temporary:
            isolated_root = Path(temporary)
            folder = isolated_root / "resources" / self.folder.parent.name / self.folder.name
            folder.mkdir(parents=True)
            for path in self.folder.glob("*.parquet"):
                (folder / path.name).symlink_to(path)
            if self.projection_mode != "raw" and (root / ".serving").exists():
                (isolated_root / ".serving").symlink_to((root / ".serving").resolve())
            engine = ParquetServingEngine(isolated_root)
            self.report["serving_projections"] = {
                kind: {
                    "available": paths != raw_paths,
                    "paths": [str(Path(path).resolve()) for path in paths],
                }
                for kind in ("entities", "relations")
                for raw_paths in [[str(folder / f"{kind}.parquet")]]
                for paths in [projected_paths(isolated_root, kind, raw_paths)]
            }
            if self.projection_mode != "auto":
                available = [
                    item["available"] for item in self.report["serving_projections"].values()
                ]
                valid = (
                    all(available) if self.projection_mode == "projected" else not any(available)
                )
                self.check("api.projection_mode", valid, requested_mode=self.projection_mode)
                if not valid:
                    return
            with TestClient(
                create_app(engine=engine, settings=Settings(read_only=True)),
                headers={"x-omnipath-release": "latest"},
            ) as client:
                products = self.sql(
                    "baseline.product_examples",
                    """SELECT DISTINCT side, form.protein_entity_key, form.transcript_entity_key FROM forms
                    WHERE owner_type='relation' AND (form.protein_entity_key IS NOT NULL OR form.transcript_entity_key IS NOT NULL)
                    ORDER BY side, form.protein_entity_key, form.transcript_entity_key LIMIT ?""",
                    [self.examples],
                )
                if not products:
                    self.unavailable(
                        "api.exact_product_filter",
                        "No relation occurrence references a reusable product",
                    )
                for index, (side, protein, transcript) in enumerate(products):
                    filters = {
                        "protein_entity_keys" if protein else "transcript_entity_keys": [
                            protein or transcript
                        ],
                        "molecular_endpoint_mode": side,
                    }
                    self.evidence(client, filters, f"product_{index + 1}")
                isoforms = self.sql(
                    "baseline.isoform_examples",
                    """SELECT DISTINCT side, form.protein_entity_key, form.transcript_entity_key,
                    form.isoform_identifier.ns || ':' || form.isoform_identifier.id FROM forms
                    WHERE owner_type='relation' AND (form.protein_entity_key IS NOT NULL OR form.transcript_entity_key IS NOT NULL)
                        AND form.isoform_identifier.ns IS NOT NULL AND form.isoform_identifier.id IS NOT NULL
                    ORDER BY side, form.protein_entity_key, form.transcript_entity_key, 4 LIMIT ?""",
                    [self.examples],
                )
                if not isoforms:
                    self.unavailable(
                        "api.exact_isoform_filter",
                        "No relation occurrence includes both a resolved product and an isoform identifier",
                    )
                for index, (side, protein, transcript, isoform) in enumerate(isoforms):
                    filters = {
                        "protein_entity_keys" if protein else "transcript_entity_keys": [
                            protein or transcript
                        ],
                        "isoform_identifiers": [isoform],
                        "molecular_endpoint_mode": side,
                    }
                    self.evidence(client, filters, f"isoform_{index + 1}")
                self.paired(client)
                self.gene(client)

    def paired(self, client):
        pairs = self.sql(
            "baseline.paired_examples",
            """SELECT relation_key, ev FROM occurrences
            WHERE ev.subject_molecular_form IS NOT NULL AND ev.object_molecular_form IS NOT NULL
            ORDER BY relation_key LIMIT ?""",
            [self.examples],
        )
        if not pairs:
            self.unavailable(
                "api.paired_occurrences", "No occurrence reports forms on both endpoints"
            )
            return
        for index, (key, ev) in enumerate(pairs):
            expected = self.sql(
                "baseline.paired_relation_evidence",
                "SELECT ev FROM occurrences WHERE relation_key=?",
                [key],
            )
            actual = self.request(client, "GET", f"/relations/{key}/evidence").json()["evidence"]
            self.check(
                f"api.paired_occurrences.{index + 1}",
                Counter(_identity(item) for (item,) in expected)
                == Counter(_identity(item, api=True) for item in actual),
                relation_key=key,
            )
            subject, obj = ev["subject_molecular_form"], ev["object_molecular_form"]
            product = subject.get("protein_entity_key") or subject.get("transcript_entity_key")
            isoform = obj.get("isoform_identifier")
            if product and isoform:
                # Deliberately combine reported opposite-side constraints. The
                # independent baseline decides whether any real occurrence can
                # satisfy both constraints on its subject; no zero is assumed.
                filters = {
                    "protein_entity_keys"
                    if subject.get("protein_entity_key")
                    else "transcript_entity_keys": [product],
                    "isoform_identifiers": [isoform["ns"] + ":" + isoform["id"]],
                    "molecular_endpoint_mode": "source",
                }
                self.evidence(client, filters, f"opposite_endpoint_constraints_{index + 1}")
            else:
                self.unavailable(
                    f"api.opposite_endpoint_constraints_{index + 1}",
                    "Paired occurrence has no subject product or no object isoform",
                )

    def client_timed(self, operation, evaluate, **parameters):
        started = time.perf_counter()
        result = evaluate()
        self.timing(
            {
                "operation": operation,
                "kind": "python_client",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "parameters": parameters,
            }
        )
        return result

    def client_product(self, client, example):
        filters, name = example["filters"], example["name"]
        kind = "protein" if "protein_entity_keys" in filters else "transcript"
        product = filters[kind + "_entity_keys"][0]
        side = filters["molecular_endpoint_mode"]
        isoform = (filters.get("isoform_identifiers") or [None])[0]
        predicate, parameters = _product_predicate(filters)
        selected = client.related_product(
            product,
            resources=self.folder.parent.name,
            product_type=kind,
            endpoint=side,
            isoform_identifier=isoform,
        )
        actual_count, actual_occurrences = self.client_timed(
            f"client.{name}.count",
            lambda: selected.aggregate(
                "count(*) AS relations, coalesce(sum(evidence_count),0) AS occurrences"
            ).fetchone(),
            **filters,
        )
        self.check(
            f"client.{name}.count",
            actual_count == example["expected_relations"]
            and actual_occurrences == example["expected_occurrences"],
            expected_relations=example["expected_relations"],
            actual_relations=actual_count,
            expected_occurrences=example["expected_occurrences"],
            actual_occurrences=actual_occurrences,
        )
        sample = selected.order("relation_key").limit(CLIENT_SAMPLE_LIMIT)
        columns = "relation_key, subject_entity_key, object_entity_key, subject_type, object_type, evidence_count, evidence, _resource, _resource_version"
        rows = self.client_timed(
            f"client.{name}.sample",
            lambda: sample.project(columns).to_arrow_table().to_pylist(),
            **filters,
        )
        expected_keys = [
            key
            for (key,) in self.sql(
                f"baseline.client.{name}.sample_keys",
                f"SELECT DISTINCT relation_key FROM occurrences WHERE {predicate} ORDER BY relation_key LIMIT ?",
                [*parameters, CLIENT_SAMPLE_LIMIT],
            )
        ]
        self.check(
            f"client.{name}.sample_keys",
            [row["relation_key"] for row in rows] == expected_keys,
            sample_limit=CLIENT_SAMPLE_LIMIT,
        )
        for row in rows:
            key = row["relation_key"]
            expected = self.sql(
                f"baseline.client.{name}.paired_evidence",
                f"SELECT ev FROM occurrences WHERE relation_key=? AND {predicate}",
                [key, *parameters],
            )
            endpoints = self.sql(
                f"baseline.client.{name}.typed_endpoints",
                "SELECT subject_entity_key, object_entity_key, subject_type, object_type FROM relations WHERE relation_key=?",
                [key],
            )[0]
            self.check(
                f"client.{name}.paired_evidence.{key}",
                Counter(_identity(ev) for ev in row["evidence"])
                == Counter(_identity(ev) for (ev,) in expected)
                and row["evidence_count"] == len(expected),
                expected_occurrences=len(expected),
                actual_occurrences=len(row["evidence"]),
            )
            self.check(
                f"client.{name}.typed_endpoints.{key}",
                tuple(
                    row[column]
                    for column in (
                        "subject_entity_key",
                        "object_entity_key",
                        "subject_type",
                        "object_type",
                    )
                )
                == endpoints
                and row["_resource"] == self.folder.parent.name
                and row["_resource_version"] == self.folder.name,
            )
        self.client_products(client, sample, expected_keys, predicate, parameters, name)

    def client_products(self, client, sample, keys, predicate, parameters, name):
        products = client.referenced_products(sample, resources=self.folder.parent.name)
        closure = f"""FROM entities e WHERE EXISTS (SELECT 1 FROM occurrences
            WHERE relation_key IN (SELECT UNNEST(?::VARCHAR[])) AND ({predicate}) AND
            (ev.subject_molecular_form.protein_entity_key=e.entity_key OR ev.subject_molecular_form.transcript_entity_key=e.entity_key
             OR ev.object_molecular_form.protein_entity_key=e.entity_key OR ev.object_molecular_form.transcript_entity_key=e.entity_key))"""
        expected_count = self.sql(
            f"baseline.client.{name}.product_count",
            f"SELECT count(*) {closure}",
            [keys, *parameters],
        )[0][0]
        actual_count = self.client_timed(
            f"client.{name}.product_count", lambda: products.aggregate("count(*)").fetchone()[0]
        )
        self.check(
            f"client.{name}.product_closure_count",
            expected_count == actual_count,
            expected_products=expected_count,
            actual_products=actual_count,
            scope=f"First {CLIENT_SAMPLE_LIMIT} selected relation records",
        )
        columns = "entity_key, entity_type, namespace, identifier, reference_entity_key, gene_reference_keys"
        expected = self.sql(
            f"baseline.client.{name}.product_records",
            f"SELECT {columns} {closure} ORDER BY entity_key LIMIT ?",
            [keys, *parameters, CLIENT_PRODUCT_LIMIT],
        )
        actual = self.client_timed(
            f"client.{name}.product_records",
            lambda: products.project(columns)
            .order("entity_key")
            .limit(CLIENT_PRODUCT_LIMIT)
            .fetchall(),
        )
        self.check(
            f"client.{name}.product_records", expected == actual, record_limit=CLIENT_PRODUCT_LIMIT
        )
        if expected_count > CLIENT_PRODUCT_LIMIT:
            self.unavailable(
                f"client.{name}.all_product_record_values",
                f"Closure has {expected_count} records; counts are exact, value comparison is limited to {CLIENT_PRODUCT_LIMIT}",
            )

    def client_reference(self, client, reference):
        for endpoint in ("any", "source", "target", "both"):
            sides = (
                ["subject"]
                if endpoint == "source"
                else ["object"]
                if endpoint == "target"
                else ["subject", "object"]
            )
            predicate = (" AND " if endpoint == "both" else " OR ").join(
                f"{side}_reference_entity_key=?" for side in sides
            )
            expected_count = self.sql(
                f"baseline.client.reference.{endpoint}.count",
                f"SELECT count(*) FROM relations WHERE {predicate}",
                [reference] * len(sides),
            )[0][0]
            selected = client.related_reference(
                reference, resources=self.folder.parent.name, endpoint=endpoint
            )
            actual_count = self.client_timed(
                f"client.reference.{endpoint}.count",
                lambda: selected.aggregate("count(*)").fetchone()[0],
                reference_entity_key=reference,
            )
            columns = "relation_key, subject_entity_key, object_entity_key, subject_type, object_type, subject_reference_entity_key, object_reference_entity_key"
            expected = self.sql(
                f"baseline.client.reference.{endpoint}.sample",
                f"SELECT {columns} FROM relations WHERE {predicate} ORDER BY relation_key LIMIT ?",
                [*([reference] * len(sides)), CLIENT_SAMPLE_LIMIT],
            )
            actual = self.client_timed(
                f"client.reference.{endpoint}.sample",
                lambda: selected.project(columns)
                .order("relation_key")
                .limit(CLIENT_SAMPLE_LIMIT)
                .fetchall(),
                reference_entity_key=reference,
            )
            self.check(
                f"client.reference.{endpoint}",
                actual_count == expected_count and actual == expected,
                reference_entity_key=reference,
                expected_relations=expected_count,
                actual_relations=actual_count,
                sample_limit=CLIENT_SAMPLE_LIMIT,
            )

    def python_client(self):
        # Normal /tmp may be a separate tmpfs: hardlinks require the source
        # filesystem. Keep staging outside immutable resource version folders.
        with tempfile.TemporaryDirectory(
            prefix="omnipath-client-validation-", dir=self.folder.parents[2]
        ) as temporary:
            snapshot = Path(temporary)
            folder = snapshot / "resources" / self.folder.parent.name / self.folder.name
            folder.mkdir(parents=True)
            files = []
            for name in ("entities.parquet", "relations.parquet"):
                source, destination = self.folder / name, folder / name
                try:
                    os.link(source, destination)
                except OSError as exc:
                    if exc.errno in {errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOTSUP}:
                        self.unavailable(
                            "client.offline_snapshot",
                            f"Temporary hardlink unavailable ({exc.strerror}); escaping symlinks are unsupported and full Parquets are not copied",
                        )
                        return
                    raise
                started = time.perf_counter()
                with source.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                self.report["timings"].append(
                    {
                        "operation": f"client.snapshot.checksum.{name}",
                        "kind": "snapshot",
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                        "bytes": source.stat().st_size,
                    }
                )
                files.append({"name": name, "size_bytes": source.stat().st_size, "sha256": digest})
            (snapshot / "snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_version": 1,
                        "api_url": "https://validation.invalid/api",
                        "release": self.folder.name,
                        "resources": [
                            {
                                "resource_id": self.folder.parent.name,
                                "version": self.folder.name,
                                "files": files,
                            }
                        ],
                    }
                )
            )
            self.report["client_snapshot"] = {
                "staging": "temporary_hardlinks",
                "memory_limit": "2GB",
                "sample_relations": CLIENT_SAMPLE_LIMIT,
                "sample_product_records": CLIENT_PRODUCT_LIMIT,
            }
            with Client.from_snapshot(snapshot, memory_limit="2GB") as client:
                product_examples = [
                    example for example in self.report["examples"] if "filters" in example
                ]
                if not product_examples:
                    self.unavailable(
                        "client.related_product",
                        "No resolved product example available in relation occurrences",
                    )
                    self.unavailable(
                        "client.referenced_products",
                        "No product selection available to validate closure",
                    )
                for example in product_examples:
                    self.client_product(client, example)
                if product_examples:
                    representative = next(
                        (
                            example
                            for example in product_examples
                            if example["name"].startswith("isoform_")
                        ),
                        product_examples[0],
                    )
                    for mode in ("any", "both"):
                        filters = {**representative["filters"], "molecular_endpoint_mode": mode}
                        predicate, parameters = _product_predicate(filters)
                        count, occurrences = self.sql(
                            f"baseline.client.product_{mode}.count",
                            f"SELECT count(DISTINCT relation_key),count(*) FROM occurrences WHERE {predicate}",
                            parameters,
                        )[0]
                        self.client_product(
                            client,
                            {
                                "name": f"product_{mode}",
                                "filters": filters,
                                "expected_relations": count,
                                "expected_occurrences": occurrences,
                            },
                        )
                gene = next(
                    (
                        example
                        for example in self.report["examples"]
                        if example["name"] == "gene_navigation"
                    ),
                    None,
                )
                if gene:
                    self.client_reference(client, gene["reference_entity_key"])
                else:
                    self.unavailable(
                        "client.related_reference",
                        "No supported scalar NCBI gene reference observed",
                    )

    def run(self, root: Path) -> dict:
        started = time.perf_counter()
        try:
            if self.schemas():
                self.structural()
                self.api(root)
                self.python_client()
            else:
                self.unavailable(
                    "data_and_api_checks", "Contract schema errors prevent safe structured queries"
                )
        except Exception as exc:
            self.check("validator.execution", False, error=f"{type(exc).__name__}: {exc}")
        finally:
            self.db.close()
        self.report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        self.report["status"] = (
            "failed" if any(c["status"] == "failed" for c in self.report["checks"]) else "passed"
        )
        return self.report


def validate_outputs(
    data_root: Path,
    resources: list[str],
    *,
    examples: int = 2,
    max_export_relations: int = 10000,
    projection_mode: str = "auto",
) -> dict:
    if projection_mode not in {"auto", "raw", "projected"}:
        raise ValueError("projection_mode must be auto, raw or projected")
    root = data_root.resolve()
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_root": str(root),
        "validator_sha256": _VALIDATOR_SHA256,
        "client_sha256": _CLIENT_SHA256,
        "projection_mode": projection_mode,
        "resources": [],
        "limits": {"examples_per_kind": examples, "max_export_relations": max_export_relations},
    }
    for selector in resources:
        try:
            folder = select_resource(root, selector)
        except ValueError as exc:
            report["resources"].append(
                {
                    "resource": selector,
                    "status": "failed",
                    "checks": [
                        {"name": "resource.available", "status": "failed", "reason": str(exc)}
                    ],
                }
            )
            continue
        report["resources"].append(
            Validator(
                folder,
                examples=examples,
                max_export_relations=max_export_relations,
                projection_mode=projection_mode,
            ).run(root)
        )
    report["status"] = (
        "failed" if any(r["status"] == "failed" for r in report["resources"]) else "passed"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--resources", nargs="+", default=["signor", "intact"])
    parser.add_argument("--output", type=Path, required=True, help="JSON report destination")
    parser.add_argument("--examples", type=int, default=2)
    parser.add_argument("--max-export-relations", type=int, default=10000)
    parser.add_argument("--projection-mode", choices=("auto", "raw", "projected"), default="auto")
    args = parser.parse_args()
    if args.examples < 1 or args.max_export_relations < 1:
        parser.error("--examples and --max-export-relations must be positive")
    report = validate_outputs(
        args.data_root,
        args.resources,
        examples=args.examples,
        max_export_relations=args.max_export_relations,
        projection_mode=args.projection_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for resource in report["resources"]:
        checks = Counter(c["status"] for c in resource["checks"])
        sizes = resource.get("tables", {})
        print(
            f"{resource['resource']}: {resource['status']}; {sizes.get('entities', {}).get('rows', '?')} entities, {sizes.get('relations', {}).get('rows', '?')} relations; {checks['passed']} passed, {checks['failed']} failed, {checks['unavailable']} unavailable; {resource.get('elapsed_seconds', 0):.2f}s"
        )
        for check in resource["checks"]:
            if check["status"] == "failed":
                print(
                    f"  FAIL {check['name']}: {json.dumps({k: v for k, v in check.items() if k not in {'name', 'status'}})}"
                )
    print(f"JSON report: {args.output.resolve()}")
    return int(report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
