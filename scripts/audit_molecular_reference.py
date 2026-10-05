"""One-shot audit: wait for isolated reference success, then use indexed lookups only."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os
import sys
import time
import traceback

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--release-root", type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args()
BASE = args.release_root.resolve()
LIBRARY = BASE / "reference/library"
REPORT = BASE / "reports/compact-runtime-qc.json"
STATE = BASE / "reference-build-status.json"
DEADLINE = time.monotonic() + 24 * 3600
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
report = {
    "audit": "coordinated-molecular-compact-runtime-v2",
    "status": "waiting",
    "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "reference": str(LIBRARY),
    "pid": os.getpid(),
    "started_at_epoch": time.time(),
    "resource_limits": {"systemd_memory_max": "1GB", "cpu_quota": "100%", "threads": 1},
    "access_policy": "No Parquet/SQL scans; only published-manifest metadata and indexed participant lookups.",
    "cases": {},
    "defects": [],
    "coverage_limitations": [],
    "oracle_refinements": [
        {
            "case": "tp53_refseq_protein_version",
            "previous_expectation": "Selected primary UniProt P04637",
            "reason": "Exact RefSeq version has three distinct primary UniProt candidates; all support Gene7157. Preserve product ambiguity rather than rank by review status.",
            "catalogue_candidates": ["uniprot:Q53GA5", "uniprot:K7PPA8", "uniprot:P04637"],
        }
    ],
}


def save():
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(REPORT)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


save()
while True:
    state = read_json(STATE)
    manifest = read_json(LIBRARY / "manifest.json")
    if state and state.get("status") == "failed":
        report.update(status="build_failed", build_state=state)
        save()
        raise SystemExit(2)
    if (
        state
        and state.get("stage") == "reference"
        and state.get("status") == "complete"
        and manifest
        and manifest.get("complete") is True
    ):
        break
    if time.monotonic() > DEADLINE:
        report.update(status="gate_timeout", build_state=state)
        save()
        raise SystemExit(2)
    time.sleep(15)

report.update(
    status="running", build_state=state, manifest=manifest, gate_opened_at_epoch=time.time()
)
save()
try:
    assignment = read_json(LIBRARY / "assignment-manifest.json")
    if not isinstance(assignment, dict) or not assignment.get("fingerprint"):
        raise ValueError("Missing or invalid assignment manifest.")
    report["assignment_manifest"] = {
        key: assignment.get(key)
        for key in (
            "status",
            "version",
            "full_scope",
            "fingerprint",
            "parent_reference",
            "derivation",
        )
    }
    if assignment["fingerprint"] != manifest["reference_fingerprint"]:
        report["defects"].append(
            "Compact manifest fingerprint does not match the assignment manifest."
        )
    component = LIBRARY / "gene-role-index/manifest.json"
    if component.exists():
        report["gene_role_component_manifest_sha256"] = hashlib.sha256(
            component.read_bytes()
        ).hexdigest()
    from omnipath_resolver.canonical.match import LibraryMatcher, votes_for
    from omnipath_resolver.canonical.policy import get_policy
    from omnipath_build.extract.observations import RawEntityObservation
    from omnipath_build.reference.replay_resources import observation_bundle
    import omnipath_resolver.canonical.match as match_module
    import omnipath_resolver.canonical.policy as policy_module
    import omnipath_resolver.index as runtime_module
    import omnipath_build.reference.replay_resources as replay_module
    import omnipath_resolver._omnipath_resolver as native_module

    report["runtime_provenance"] = {
        module.__name__: {
            "path": module.__file__,
            "sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        }
        for module in (match_module, policy_module, runtime_module, replay_module, native_module)
    }
    report["python"] = {"executable": sys.executable, "version": sys.version}
    matcher = LibraryMatcher(LIBRARY)

    def check(
        name,
        entity_type,
        namespace,
        identifier,
        expected,
        *,
        identifiers=None,
        form=None,
        taxon="9606",
        coverage_optional=False,
    ):
        obs = RawEntityObservation(
            entity_key=name,
            entity_type=entity_type,
            namespace=namespace,
            identifier=identifier,
            taxon=taxon,
            identifiers=identifiers or [],
            molecular_form=form,
        )
        started = time.monotonic()
        result = matcher.match({name: obs})[name]
        normalized, observed = votes_for(obs, get_policy(entity_type))
        _, votes = observation_bundle(name, obs, normalized, observed, "gene_protein")
        postings = []
        for vote in votes:
            value = matcher.runtime.lookup(vote["lookup_key"])
            postings.append(
                {
                    "namespace": vote["ns"],
                    "identifier": vote["identifier"],
                    "scope": vote["scope"],
                    "route": vote["route"],
                    "gene_only": vote.get("gene_only", False),
                    "candidate_count": len(value["candidates"]),
                    "gene_role": value["gene"],
                    "candidate_facts": value["candidates"],
                }
            )
        exact = asdict(result)
        aliases = exact.pop("aliases")
        protein_aliases = exact.pop("protein_aliases")
        exact["matched"] = result.matched
        failures = {
            key: {"expected": value, "actual": exact.get(key)}
            for key, value in expected.items()
            if exact.get(key) != value
        }
        classification = "pass"
        if failures:
            if coverage_optional and not any(p["candidate_count"] for p in postings):
                classification = "coverage_missing"
                report["coverage_limitations"].append(
                    {
                        "case": name,
                        "mismatches": failures,
                        "reason": "No indexed posting for the asserted stable transcript accession.",
                    }
                )
            else:
                classification = "defect"
                report["defects"].append({"case": name, "mismatches": failures})
        # Sequence identity must survive independently of catalogue coverage.
        if coverage_optional and namespace in ("enst", "ensembl", "refseq"):
            expected_ns = "enst" if namespace in ("enst", "ensembl") else "refseq"
            if (
                result.transcript_namespace != expected_ns
                or result.transcript_identifier != identifier
            ):
                classification = "defect"
                report["defects"].append(
                    {
                        "case": name,
                        "reason": "Asserted transcript sequence/version was not retained.",
                    }
                )
        report["cases"][name] = {
            "input": asdict(obs),
            "output": exact,
            "expected": expected,
            "classification": classification,
            "lookup_postings": postings,
            "alias_counts": {ns: len(values) for ns, values in aliases.items()},
            "product_alias_counts": {ns: len(values) for ns, values in protein_aliases.items()},
            "seconds": round(time.monotonic() - started, 6),
        }
        save()
        print(
            json.dumps({"case": name, "classification": classification, "output": exact}),
            flush=True,
        )
        return result

    def gene_expected(identifier, product=None, entity_type="protein"):
        return {
            "canonical_namespace": "entrez",
            "canonical_identifier": identifier,
            "protein_identifier": product,
            "entity_type": entity_type,
            "gene_mapping_status": "resolved",
            "matched": True,
        }

    check("tp53_primary_protein", "protein", "uniprot", "P04637", gene_expected("7157", "P04637"))
    check("tp53_source_gene", "gene", "entrez", "7157", gene_expected("7157", entity_type="gene"))
    check("tp53_symbol_gene_evidence", "protein", "genesymbol", "TP53", gene_expected("7157"))
    check("brca1_primary_protein", "protein", "uniprot", "P38398", gene_expected("672", "P38398"))
    check(
        "brca1_symbol_more_than_ten_products",
        "gene",
        "genesymbol",
        "BRCA1",
        gene_expected("672", entity_type="gene"),
    )
    check(
        "brca1_ensembl_gene",
        "gene",
        "ensg",
        "ENSG00000012048.25",
        gene_expected("672", entity_type="gene"),
    )
    check(
        "calmodulin_ambiguous_gene",
        "protein",
        "uniprot",
        "P0DP23",
        {
            "canonical_namespace": "uniprot",
            "canonical_identifier": "P0DP23",
            "protein_identifier": "P0DP23",
            "gene_mapping_status": "ambiguous",
            "gene_candidates": ("entrez:801", "entrez:805", "entrez:808"),
            "matched": True,
        },
    )
    check(
        "calmodulin_explicit_gene_disambiguation",
        "protein",
        "uniprot",
        "P0DP23",
        gene_expected("805", "P0DP23"),
        identifiers=[{"ns": "entrez", "id": "805"}],
    )
    for gene in ("801", "805", "808"):
        check(
            "calmodulin_source_gene_" + gene,
            "gene",
            "entrez",
            gene,
            gene_expected(gene, entity_type="gene"),
        )
    check(
        "tp53_conflicting_brca1_gene",
        "protein",
        "uniprot",
        "P04637",
        {
            "canonical_namespace": "uniprot",
            "canonical_identifier": "P04637",
            "protein_identifier": "P04637",
            "gene_mapping_status": "conflict",
            "protein_gene_candidates": ("entrez:7157",),
            "matched": True,
        },
        identifiers=[{"ns": "entrez", "id": "672"}],
    )
    check(
        "tp53_conflicting_missing_gene",
        "protein",
        "uniprot",
        "P04637",
        {
            "canonical_namespace": "uniprot",
            "canonical_identifier": "P04637",
            "protein_identifier": "P04637",
            "gene_mapping_status": "conflict",
            "matched": True,
        },
        identifiers=[{"ns": "entrez", "id": "999999999999"}],
    )
    check(
        "rna_subtype_gene_preservation",
        "microrna",
        "entrez",
        "7157",
        gene_expected("7157", entity_type="microrna"),
    )
    check(
        "mouse_tp53_product_taxon",
        "protein",
        "uniprot",
        "P02340",
        {
            **gene_expected("22059", "P02340"),
            "taxon": "10090",
            "protein_taxon": "10090",
        },
        taxon="10090",
    )
    check(
        "uniprot_isoform_primary_product",
        "protein",
        "uniprot",
        "P04637-2",
        gene_expected("7157", "P04637"),
        form={"isoform_identifier": {"ns": "uniprot", "id": "P04637-2"}},
    )
    refseq = check(
        "tp53_refseq_protein_version",
        "protein",
        "refseq",
        "NP_000537.3",
        {
            **gene_expected("7157", "NP_000537.3"),
            "protein_namespace": "refseq",
            "protein_node_id": None,
        },
    )
    from omnipath_build.reference.replay_resources import key as lookup_key

    # Validate the global postings the matcher uses, plus taxon-specific keys.
    # Both the normalized accession and its exact version must preserve all
    # three distinct product assertions, rather than silently become gene-only.
    expected_products = {"uniprot:Q53GA5", "uniprot:K7PPA8", "uniprot:P04637"}
    observed_refseq = report["cases"]["tp53_refseq_protein_version"]["lookup_postings"]
    diagnostics = []
    diagnostic_failures = []
    if not any(
        p["namespace"] == "refseq_protein"
        and p["identifier"] == "NP_000537"
        and not p["gene_only"]
        and not p["gene_role"]
        for p in observed_refseq
    ):
        diagnostic_failures.append(
            {"reason": "Reported RefSeq protein did not use a product lookup."}
        )
    for scope in ("", "9606"):
        for identifier in ("NP_000537", "NP_000537.3"):
            posting = matcher.runtime.lookup(lookup_key(2, 1, "refseq_protein", scope, identifier))
            facts = posting["candidates"]
            diagnostic = {
                "scope": scope or "global",
                "identifier": identifier,
                "candidate_facts": facts,
                "gene_role": posting["gene"],
            }
            diagnostics.append(diagnostic)
            if (
                {fact[1] for fact in facts} != expected_products
                or any(fact[3] != fact[1] or fact[6] != ["7157"] for fact in facts)
                or posting["gene"]
            ):
                diagnostic_failures.append(
                    {
                        "reason": "The frozen snapshot no longer preserves the three distinct product assertions.",
                        **diagnostic,
                    }
                )
    if diagnostic_failures:
        report["cases"]["tp53_refseq_protein_version"]["classification"] = "defect"
        report["defects"].append(
            {"case": "tp53_refseq_protein_version", "product_lookup_failures": diagnostic_failures}
        )
    report["cases"]["tp53_refseq_protein_version"]["product_ambiguity"] = {
        "catalogue_candidates": sorted(expected_products),
        "postings": diagnostics,
        "gene_is_resolved": refseq.gene_mapping_status == "resolved",
        "selection_policy": "Additional product evidence required; review status alone does not select an asserted participant.",
    }
    for accession in ("P04637", "K7PPA8"):
        check(
            "tp53_refseq_explicit_product_" + accession,
            "protein",
            "refseq",
            "NP_000537.3",
            gene_expected("7157", accession),
            identifiers=[{"ns": "uniprot", "id": accession}],
        )
    check(
        "tp53_refseq_gene_does_not_select_product",
        "protein",
        "refseq",
        "NP_000537.3",
        {
            **gene_expected("7157", "NP_000537.3"),
            "protein_namespace": "refseq",
            "protein_node_id": None,
        },
        identifiers=[{"ns": "entrez", "id": "7157"}],
    )
    check(
        "serialized_nullable_molecular_form",
        "protein",
        "uniprot",
        "P04637",
        gene_expected("7157", "P04637"),
        form={
            "sequence_identifiers": None,
            "variants": None,
            "modifications": [{"residue": "S", "position": 15}],
        },
    )
    for namespace, identifier in (
        ("enst", "ENST00000269305.9"),
        ("ensembl", "ENST00000269305.987"),
        ("refseq", "NM_000546.999"),
    ):
        check(
            "tp53_transcript_version_" + namespace,
            "rna_product",
            namespace,
            identifier,
            {
                **gene_expected("7157", entity_type="rna_product"),
                "transcript_namespace": "refseq" if namespace == "refseq" else "enst",
                "transcript_identifier": identifier,
            },
            coverage_optional=True,
        )
    for namespace, identifier in (
        ("enst", "ENST999999999999.73"),
        ("refseq", "NR_999999999999.73"),
    ):
        check(
            "missing_transcript_" + namespace,
            "transcript",
            namespace,
            identifier,
            {
                "matched": False,
                "protein_identifier": None,
                "gene_mapping_status": "missing",
                "transcript_namespace": namespace,
                "transcript_identifier": identifier,
                "entity_type": "transcript",
            },
        )
    product = matcher.runtime.record("uniprot:P04637")
    available = [
        (ns, identifier)
        for ns, identifier in product["identifiers"]
        if ns == "enst" or (ns == "refseq" and identifier.startswith(("NM_", "NR_", "XM_", "XR_")))
    ]
    report["available_tp53_transcript_aliases"] = available
    for i, (namespace, identifier) in enumerate(available[:2]):
        raw = identifier.rsplit(".", 1)[0] + ".997"
        check(
            "catalogued_transcript_version_" + str(i),
            "rna_product",
            namespace,
            raw,
            {
                **gene_expected("7157", entity_type="rna_product"),
                "transcript_namespace": namespace,
                "transcript_identifier": raw,
            },
        )
    if not available:
        report["coverage_limitations"].append(
            "Primary TP53 product record has no explicit transcript aliases; fixed transcript assertions are reported individually above."
        )
    matcher.close()
    report["status"] = "failed" if report["defects"] else "passed"
    report["finished_at_epoch"] = time.time()
    report["summary"] = {
        "cases": len(report["cases"]),
        "passed": sum(case["classification"] == "pass" for case in report["cases"].values()),
        "coverage_missing": sum(
            case["classification"] == "coverage_missing" for case in report["cases"].values()
        ),
        "defects": len(report["defects"]),
    }
    save()
    print(
        json.dumps(
            {"status": report["status"], "summary": report["summary"], "report": str(REPORT)}
        ),
        flush=True,
    )
    raise SystemExit(1 if report["defects"] else 0)
except Exception as exc:
    report.update(status="error", error=str(exc), traceback=traceback.format_exc())
    save()
    raise
