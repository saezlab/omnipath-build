#!/usr/bin/env python3
"""Checkpointed reference construction from the existing normalized hub Parquet contract.

DuckDB does bounded, spillable joins/grouping in isolated stage processes. Rust
computes only the reduced anchorless graph. No resource observations are inputs.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = "gene-product-reference-v6"
CHEMICAL = (
    "chebi",
    "pubchem",
    "chembl",
    "hmdb",
    "lipidmaps",
    "swisslipids",
    "bigg",
    "metanetx",
    "refmet",
    "ramp",
)
PROTEIN = ("uniprot", "entrez", "ramp_gene")
EMPTY_KEYS = ("MOSFIJXAXDLOML-UHFFFAOYSA-N", "MOSFIJXAXDLOML-UHFFFAOYNA-N")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def scan(path):
    return f"read_parquet({quote(path)})"


def log(event, **fields):
    print(
        json.dumps(dict(time=time.strftime("%FT%TZ", time.gmtime()), event=event, **fields)),
        flush=True,
    )


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def rows(path):
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).metadata.num_rows


def norm(ns, ident):
    from omnipath_resolver.canonical.identifiers import normalize_id_sql

    return normalize_id_sql(ns, ident)


def gene_products_query(graph):
    # Accept either hub's explicit gene/product cross-reference, retaining the
    # primary UniProt record's reviewed status. Never join proteins via the gene.
    return f"""WITH products AS (
        SELECT e.target_id entrez_id,e.source_anchor protein_entity_id,s.taxon,s.reviewed
        FROM {scan(graph / "assessed_edges.parquet")} e
        JOIN {scan(graph / "endpoints.parquet")} s ON s.record_id=e.source_record
        LEFT JOIN {scan(graph / "endpoints.parquet")} t ON t.record_id=e.target_record
        WHERE e.semantics='gene_product' AND e.source_anchor_count=1
        AND (t.taxon IS NULL OR t.taxon IN ('','0') OR s.taxon IN ('','0') OR s.taxon=t.taxon)
        UNION
        SELECT s.local_id entrez_id,e.target_anchor protein_entity_id,t.taxon,t.reviewed
        FROM {scan(graph / "assessed_edges.parquet")} e
        JOIN {scan(graph / "endpoints.parquet")} s ON s.record_id=e.source_record
        JOIN {scan(graph / "endpoints.parquet")} t ON t.record_id=e.target_record
        WHERE s.hub='entrez' AND t.hub='uniprot' AND e.target_anchor_count=1
        AND (s.taxon IN ('','0') OR t.taxon IN ('','0') OR s.taxon=t.taxon))
        SELECT *, reviewed OR NOT bool_or(reviewed) OVER(PARTITION BY entrez_id) projection_eligible
        FROM products"""


def gene_resolution_query(entities, products):
    """Gene identifiers resolve to gene identity, independently of products."""
    return f"""SELECT DISTINCT 'entrez' namespace,i.identifier,
        'entrez:' || i.identifier entity_id,'gene_identity' admission,
        'entrez:' || i.identifier record_id
        FROM {scan(entities / "identity_identifiers.parquet")} i WHERE i.namespace='entrez'
        UNION
        SELECT DISTINCT 'entrez',p.entrez_id,'entrez:' || p.entrez_id,
        'explicit_gene_product','entrez:' || p.entrez_id
        FROM {scan(products / "gene_products.parquet")} p"""


def worker(job_path):
    import duckdb

    job = json.loads(Path(job_path).read_text())
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute(f"SET memory_limit={quote(job['memory'])}")
    c.execute(f"SET threads={int(job['threads'])}")
    c.execute("SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory={quote(out / 'spill')}")
    if job.get("goslin_hubs"):
        c.close()
        from omnipath_build.reference.goslin_identifiers import build

        build(job["goslin_hubs"], out, job["threads"], job["memory"], job["goslin_cache"])
        counts = {str(p.relative_to(out)): rows(p) for p in out.rglob("*.parquet")}
        (out / "counts.json").write_text(json.dumps(counts, indent=2))
        return
    for i, sql in enumerate(job["sql"]):
        start = time.monotonic()
        log("sql_start", stage=job["name"], statement=i + 1)
        c.execute(sql)
        log(
            "sql_done",
            stage=job["name"],
            statement=i + 1,
            seconds=round(time.monotonic() - start, 2),
        )
    c.close()
    shutil.rmtree(out / "spill", ignore_errors=True)
    counts = {str(p.relative_to(out)): rows(p) for p in out.rglob("*.parquet")}
    (out / "counts.json").write_text(json.dumps(counts, indent=2))
    log("worker_done", stage=job["name"], counts=counts)


class Build:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.output).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.hubs = Path(args.hubs).resolve()
        self.binary = Path(args.components).resolve()
        from omnipath_build.provenance import file_fingerprint, runtime_provenance
        from omnipath_resolver.canonical import identifiers
        from omnipath_resolver.goslin_cache import cache_fingerprint
        from omnipath_resolver import goslin, goslin_cache
        from omnipath_resolver.goslin import normalize

        self.fingerprints = {}
        self.completed = []
        self.input_paths = {}
        self.sources = (
            CHEMICAL
            if args.domain == "chemical"
            else PROTEIN
            if args.domain == "gene_protein"
            else CHEMICAL + PROTEIN
        )
        for name in self.sources:
            p = (self.hubs / (name + ".parquet")).resolve()
            if not p.is_file():
                raise RuntimeError(
                    f"Missing required hub {p}; partial coverage must not publish silently"
                )
            st = p.stat()
            self.fingerprints[name] = {
                **file_fingerprint(p),
                "path": str(p),
                "bytes": st.st_size,
                "mtime_ns": st.st_mtime_ns,
            }
        identity = {
            "version": VERSION,
            "runtime": runtime_provenance(),
            "normalizer_sha256": file_fingerprint(identifiers.__file__)["sha256"],
            "goslin_grammar_fingerprint": cache_fingerprint(normalize),
            "sources": self.fingerprints,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "goslin_code_sha256": hashlib.sha256(Path(goslin.__file__).read_bytes()).hexdigest(),
            "goslin_cache_code_sha256": hashlib.sha256(
                Path(goslin_cache.__file__).read_bytes()
            ).hexdigest(),
            "binary_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
        }
        self.fingerprint = digest(identity)
        existing = self.out / "inputs.json"
        if existing.exists() and json.loads(existing.read_text()) != identity:
            raise RuntimeError("Input/code changed: choose a fresh output snapshot")
        existing.write_text(json.dumps(identity, indent=2))
        # Own the source snapshot: provenance row locators remain valid even if
        # the hub cache is replaced by a later download.
        frozen = self.out / "inputs"
        frozen.mkdir(exist_ok=True)
        for name, info in self.fingerprints.items():
            target = frozen / (name + ".parquet")
            if not target.exists():
                log("input_snapshot_start", hub=name, bytes=info["bytes"])
                temporary = target.with_suffix(".tmp")
                # Reuse an immutable reference's frozen inputs without duplicating
                # the full PubChem/UniProt snapshot on the same filesystem.
                source_path = Path(info["path"])
                parent_manifest = source_path.parent.parent / "manifest.json"
                immutable_input = source_path.parent.name == "inputs" and parent_manifest.is_file()
                if immutable_input:
                    previous = json.loads(parent_manifest.read_text())
                    immutable_input = (
                        previous.get("status") == "complete"
                        and str(source_path) in previous.get("frozen_inputs", {}).values()
                    )
                if immutable_input:
                    try:
                        os.link(source_path, temporary)
                    except OSError:
                        shutil.copyfile(source_path, temporary)
                else:
                    shutil.copyfile(source_path, temporary)
                os.replace(temporary, target)
                log("input_snapshot_done", hub=name)
            if file_fingerprint(target)["sha256"] != info["sha256"]:
                raise RuntimeError(f"Input snapshot content changed or incomplete: {name}")
            self.input_paths[name] = str(target)

    def stage(self, name, make_sql):
        out = self.out / name
        done = out / "_SUCCESS.json"
        if done.exists():
            marker = json.loads(done.read_text())
            if marker["fingerprint"] != self.fingerprint:
                raise RuntimeError(f"Stale checkpoint: {name}")
            if any(rows(out / file) != count for file, count in marker["counts"].items()):
                raise RuntimeError(f"Damaged checkpoint: {name}")
            log("stage_reused", stage=name, counts=marker["counts"])
            self.completed.append(name)
            return out
        out.mkdir(parents=True, exist_ok=True)
        sql = make_sql(out)
        job = dict(
            name=name,
            out=str(out),
            memory=self.args.memory,
            threads=self.args.threads,
            sql=sql,
        )
        if name == "goslin-identifiers":
            from omnipath_build.reference.goslin_identifiers import SOURCES

            job["goslin_hubs"] = {s: self.input_paths[s] for s in SOURCES}
            job["goslin_cache"] = str(self.out.parent / ".goslin-cache")
        job_path = out / "job.json"
        job_path.write_text(json.dumps(job, indent=2))
        reuse = getattr(self.args, "reuse_stages_from", None)
        prefix, _, hub = name.partition("-")
        if reuse and prefix in {"partition", "group", "records"} and hub in self.sources:
            previous = Path(reuse).resolve()
            prior_stage = previous / name
            prior_job = prior_stage / "job.json"
            prior_done = prior_stage / "_SUCCESS.json"
            prior_input = previous / "inputs" / (hub + ".parquet")
            # Only independently normalized hub stages may be reused. Graphs,
            # memberships and claims must be rebuilt when the hub set changes.
            if (
                prior_job.is_file()
                and prior_done.is_file()
                and prior_input.is_file()
                and os.path.samefile(prior_input, self.input_paths[hub])
            ):
                old_job = json.loads(prior_job.read_text())
                old_sql = json.dumps(old_job["sql"]).replace(str(previous), "@REFERENCE")
                new_sql = json.dumps(sql).replace(str(self.out), "@REFERENCE")
                marker = json.loads(prior_done.read_text())
                if old_sql == new_sql and all(
                    rows(prior_stage / f) == n for f, n in marker["counts"].items()
                ):
                    for child in prior_stage.iterdir():
                        if child.name not in {"job.json", "_SUCCESS.json", "worker.log", "spill"}:
                            destination = out / child.name
                            if (
                                destination.is_symlink()
                                and destination.resolve() == child.resolve()
                            ):
                                continue  # Interrupted copy of this same immutable stage.
                            if destination.is_dir() and not destination.is_symlink():
                                destination.rmdir()  # make_sql may create empty partition directories.
                            destination.symlink_to(child, target_is_directory=child.is_dir())
                    done.write_text(
                        json.dumps(
                            dict(
                                fingerprint=self.fingerprint,
                                counts=marker["counts"],
                                reused_from=str(prior_stage),
                            )
                        )
                    )
                    self.completed.append(name)
                    log("stage_reused_immutable_hub", stage=name, previous=str(prior_stage))
                    return out
        start = time.monotonic()
        log("stage_start", stage=name)
        with (out / "worker.log").open("w") as output:
            p = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    str(job_path),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            while p.poll() is None:
                time.sleep(1)
                if int(time.monotonic() - start) % 10 == 0:
                    size = sum(f.stat().st_size for f in out.rglob("*.parquet"))
                    log(
                        "stage_progress",
                        stage=name,
                        seconds=round(time.monotonic() - start),
                        output_bytes=size,
                    )
            if p.returncode:
                log(
                    "stage_failed",
                    stage=name,
                    code=p.returncode,
                    tail=(out / "worker.log").read_text()[-4000:],
                )
                raise RuntimeError(f"{name} failed; completed stages are reusable")
        counts = json.loads((out / "counts.json").read_text())
        done.write_text(
            json.dumps(
                {
                    "fingerprint": self.fingerprint,
                    "seconds": time.monotonic() - start,
                    "counts": counts,
                }
            )
        )
        self.completed.append(name)
        log(
            "stage_done",
            stage=name,
            seconds=round(time.monotonic() - start, 2),
            counts=counts,
        )
        return out

    def copy(self, query, path):
        return f"COPY ({query}) TO {quote(path)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 131072)"

    def records(self, name):
        p = self.input_paths[name]
        ident = norm(quote(name), "hub_id")
        value = norm("source_type", "source_id")
        valid = f"source_type='inchikey' AND regexp_matches(normalized_id,'^[A-Z]{{14}}-[A-Z]{{10}}-[A-Z]$') AND normalized_id NOT IN ({','.join(map(quote, EMPTY_KEYS))})"
        anchor = (
            "CASE WHEN source_type='uniprot' AND normalized_id=local_id THEN 'uniprot:' || local_id END"
            if name == "uniprot"
            else "NULL::VARCHAR"
            if name in PROTEIN
            else f"CASE WHEN {valid} THEN 'inchikey:' || normalized_id END"
        )
        native_targets = PROTEIN if name in PROTEIN else CHEMICAL
        grouped = None
        if name in ("pubchem", "uniprot", "entrez"):
            projected = f"""WITH raw AS (SELECT {ident} local_id,source_type,{value} AS normalized_id,taxonomy_id FROM {scan(p)} WHERE hub_id IS NOT NULL AND trim(hub_id)<>'')
                SELECT local_id,{anchor} anchor,taxonomy_id,
                source_type='uniprot_entry' AND split_part(normalized_id,'_',1)<>local_id reviewed,
                hash(local_id)%128 bucket FROM raw"""
            parts = self.stage(
                "partition-" + name,
                lambda out: [
                    "SET partitioned_write_max_open_files=1024",
                    "SET partitioned_write_flush_threshold=65536",
                    f"COPY ({projected}) TO {quote(out / 'parts')} (FORMAT PARQUET,COMPRESSION ZSTD,PARTITION_BY(bucket),OVERWRITE_OR_IGNORE true)",
                ],
            )

            def aggregate(out):
                (out / "parts").mkdir(exist_ok=True)
                queries = []
                for index, part in enumerate(sorted((parts / "parts").glob("bucket=*"))):
                    queries.append(
                        self.copy(
                            f"""SELECT {quote(name)} hub,local_id,{quote(name + ":")} || local_id record_id,
                        count(DISTINCT anchor)::UBIGINT anchor_count,min(anchor) anchor,
                        coalesce(min(taxonomy_id) FILTER(WHERE taxonomy_id IS NOT NULL AND taxonomy_id NOT IN ('','0')),'0') taxon,
                        bool_or(reviewed) reviewed FROM {scan(part / "*.parquet")} GROUP BY local_id""",
                            out / "parts" / f"{index}.parquet",
                        )
                    )
                if not queries:
                    queries.append(
                        self.copy(
                            f"SELECT {quote(name)}::VARCHAR hub,NULL::VARCHAR local_id,NULL::VARCHAR record_id,0::UBIGINT anchor_count,NULL::VARCHAR anchor,NULL::VARCHAR taxon,false reviewed WHERE false",
                            out / "parts" / "0.parquet",
                        )
                    )
                return queries

            grouped = self.stage("group-" + name, aggregate)

        def sql(out):
            return [
                self.copy(
                    f"""WITH raw AS (SELECT {ident} local_id,source_type,{value} AS normalized_id,taxonomy_id FROM {scan(p)} WHERE hub_id IS NOT NULL AND trim(hub_id)<>'')
                SELECT {quote(name)} hub,local_id,{quote(name + ":")} || local_id record_id,
                count(DISTINCT {anchor})::UBIGINT anchor_count,min({anchor}) anchor,
                coalesce(min(taxonomy_id) FILTER(WHERE taxonomy_id IS NOT NULL AND taxonomy_id NOT IN ('','0')),'0') taxon,
                bool_or(source_type='uniprot_entry' AND split_part(normalized_id,'_',1)<>local_id) reviewed
                FROM raw GROUP BY local_id""",
                    out / "records.parquet",
                )
                if grouped is None
                else self.copy(
                    f"SELECT * FROM {scan(grouped / 'parts' / '*.parquet')}",
                    out / "records.parquet",
                ),
                self.copy(
                    f"""SELECT {quote(name + ":")} || {ident} source_record,
                    source_type target_hub,{value} target_id, source_type || ':' || {value} target_record,
                    {quote(name + ":")} || file_row_number::VARCHAR assertion_id,
                    CASE WHEN ({quote(name)}='uniprot' AND source_type='entrez') OR ({quote(name)}='entrez' AND source_type='uniprot') THEN 'gene_product' ELSE 'identity_xref' END semantics
                    FROM read_parquet({quote(p)},file_row_number=true)
                    WHERE source_type IN ({",".join(map(quote, native_targets))}) AND source_id IS NOT NULL AND trim(source_id)<>'' AND hub_id IS NOT NULL AND trim(hub_id)<>''
                    AND NOT(source_type={quote(name)} AND {value}={ident})""",
                    out / "edges.parquet",
                ),
                self.copy(
                    f"""SELECT DISTINCT r.record_id,'inchikey:' || {value} anchor FROM {scan(p)} h JOIN {scan(out / "records.parquet")} r ON r.local_id={ident} WHERE r.anchor_count>1 AND source_type='inchikey' AND regexp_matches({value},'^[A-Z]{{14}}-[A-Z]{{10}}-[A-Z]$') AND {value} NOT IN ({",".join(map(quote, EMPTY_KEYS))})""",
                    out / "multiple_anchor_claims.parquet",
                ),
            ]

        return self.stage("records-" + name, sql)

    def domain(self, domain, names):
        goslin = self.stage("goslin-identifiers", lambda out: []) if domain == "chemical" else None
        base = {name: self.records(name) for name in names}
        record_union = " UNION ALL ".join(
            f"SELECT * FROM {scan(p / 'records.parquet')}" for p in base.values()
        )
        edge_union = " UNION ALL ".join(
            f"SELECT * FROM {scan(p / 'edges.parquet')}" for p in base.values()
        )
        if goslin is not None:
            # A virtual descriptor vertex sees EVERY full anchor for that ID.
            # A star through one real record would hide conflicting anchors.
            record_union += f" UNION ALL SELECT DISTINCT 'goslin' hub,goslin local_id,'goslin:' || goslin record_id,0::UBIGINT anchor_count,NULL::VARCHAR anchor,'0' taxon,false reviewed FROM {scan(goslin / 'claims.parquet')}"
            edge_union += f" UNION ALL SELECT DISTINCT record_id source_record,'goslin' target_hub,goslin target_id,'goslin:' || goslin target_record,'goslin:' || sha256(record_id || ':' || name) assertion_id,'goslin_identity' semantics FROM {scan(goslin / 'claims.parquet')}"
        graph = self.stage(
            domain + "-graph",
            lambda out: [
                self.copy(edge_union, out / "edges.parquet"),
                self.copy(
                    f"SELECT source_record record_id FROM {scan(out / 'edges.parquet')} UNION SELECT target_record FROM {scan(out / 'edges.parquet')}",
                    out / "endpoint_ids.parquet",
                ),
                self.copy(
                    f"SELECT r.* FROM ({record_union}) r SEMI JOIN {scan(out / 'endpoint_ids.parquet')} e USING(record_id)",
                    out / "endpoints.parquet",
                ),
                self.copy(
                    f"SELECT row_number() OVER(ORDER BY CASE hub WHEN 'entrez' THEN 0 WHEN 'chebi' THEN 1 ELSE 2 END,record_id)-1 AS vertex, * FROM ({record_union}) WHERE anchor_count=0",
                    out / "vertices.parquet",
                ),
                self.copy(
                    f"SELECT e.*,s.anchor source_anchor,s.anchor_count source_anchor_count,t.anchor target_anchor,t.anchor_count target_anchor_count,t.record_id IS NOT NULL target_exists FROM {scan(out / 'edges.parquet')} e JOIN {scan(out / 'endpoints.parquet')} s ON s.record_id=e.source_record LEFT JOIN {scan(out / 'endpoints.parquet')} t ON t.record_id=e.target_record",
                    out / "assessed_edges.parquet",
                ),
                self.copy(
                    f"SELECT a.vertex::UBIGINT a,b.vertex::UBIGINT b,e.assertion_id FROM {scan(out / 'edges.parquet')} e JOIN {scan(out / 'vertices.parquet')} a ON e.source_record=a.record_id JOIN {scan(out / 'vertices.parquet')} b ON e.target_record=b.record_id WHERE e.semantics<>'gene_product'",
                    out / "keyless_edges.parquet",
                ),
            ],
        )
        components = self.out / (domain + "-components")
        components.mkdir(exist_ok=True)
        if not (components / "_SUCCESS.json").exists():
            n = rows(graph / "vertices.parquet")
            log("components_start", domain=domain, vertices=n)
            subprocess.run(
                [
                    str(self.binary),
                    str(n),
                    str(graph / "keyless_edges.parquet"),
                    str(components),
                ],
                check=True,
            )
            (components / "_SUCCESS.json").write_text(
                json.dumps({"fingerprint": self.fingerprint, "vertices": n})
            )
        self.completed.append(domain + "-components")
        decisions = self.stage(
            domain + "-decisions",
            lambda out: [
                self.copy(
                    f"SELECT v.*,c.component FROM {scan(graph / 'vertices.parquet')} v JOIN {scan(components / 'components.parquet')} c USING(vertex)",
                    out / "vertices.parquet",
                ),
                self.copy(
                    f"""SELECT v.component,e.assertion_id,e.source_record,e.target_record,e.semantics,e.target_anchor boundary_anchor,e.target_anchor_count boundary_claims,e.target_record boundary_record
                FROM {scan(out / "vertices.parquet")} v JOIN {scan(graph / "assessed_edges.parquet")} e ON v.record_id=e.source_record WHERE e.target_anchor_count>0 AND e.semantics<>'gene_product'
                UNION ALL SELECT v.component,e.assertion_id,e.source_record,e.target_record,e.semantics,e.source_anchor,e.source_anchor_count,e.source_record
                FROM {scan(out / "vertices.parquet")} v JOIN {scan(graph / "assessed_edges.parquet")} e ON v.record_id=e.target_record WHERE e.source_anchor_count>0 AND e.semantics<>'gene_product'""",
                    out / "boundaries.parquet",
                ),
                self.copy(
                    f"SELECT component,count(DISTINCT boundary_anchor) FILTER(WHERE boundary_claims=1)::UBIGINT anchor_count,min(boundary_anchor) FILTER(WHERE boundary_claims=1) anchor,bool_or(boundary_claims>1) blocked FROM {scan(out / 'boundaries.parquet')} GROUP BY component",
                    out / "boundary_summary.parquet",
                ),
                self.copy(
                    f"""SELECT v.record_id,v.component,
                CASE WHEN coalesce(s.anchor_count,0)=1 AND NOT coalesce(s.blocked,false) THEN s.anchor
                     WHEN coalesce(s.anchor_count,0)=0 AND NOT coalesce(s.blocked,false) THEN root.record_id ELSE v.record_id END entity_id,
                CASE WHEN coalesce(s.anchor_count,0)=1 AND NOT coalesce(s.blocked,false) THEN 'attached'
                     WHEN coalesce(s.anchor_count,0)=0 AND NOT coalesce(s.blocked,false) THEN 'anchorless_component' ELSE 'ambiguous_native' END decision
                FROM {scan(out / "vertices.parquet")} v JOIN {scan(graph / "vertices.parquet")} root ON root.vertex=v.component LEFT JOIN {scan(out / "boundary_summary.parquet")} s USING(component)""",
                    out / "assignments.parquet",
                ),
                self.copy(
                    f"""SELECT *,CASE WHEN NOT target_exists THEN 'missing_target' WHEN source_anchor_count>1 OR target_anchor_count>1 THEN 'multiple_anchor_claims'
                WHEN source_anchor_count=1 AND target_anchor_count=1 AND source_anchor=target_anchor THEN 'agreement'
                WHEN source_anchor_count=1 AND target_anchor_count=1 THEN 'different_anchors' ELSE 'requires_component_decision' END category,
                CASE WHEN starts_with(source_anchor,'inchikey:') AND starts_with(target_anchor,'inchikey:') THEN substr(source_anchor,10,14)=substr(target_anchor,10,14) ELSE NULL END same_connectivity
                FROM {scan(graph / "assessed_edges.parquet")}""",
                    out / "edge_assessments.parquet",
                ),
            ],
        )
        members = {}
        for name, p in base.items():
            members[name] = self.stage(
                "members-" + name,
                lambda out, p=p: [
                    self.copy(
                        f"""SELECT r.*,CASE WHEN r.hub='entrez' THEN r.record_id ELSE coalesce(a.entity_id,CASE WHEN r.anchor_count=1 THEN r.anchor ELSE r.record_id END) END entity_id,
                CASE WHEN r.hub='entrez' THEN 'gene_identity' ELSE coalesce(a.decision,CASE WHEN r.anchor_count=1 THEN 'anchored' ELSE 'multiple_anchor_claims' END) END decision,a.component
                FROM {scan(p / "records.parquet")} r LEFT JOIN {scan(decisions / "assignments.parquet")} a USING(record_id)""",
                        out / "members.parquet",
                    )
                ],
            )
        multi_union = " UNION ALL ".join(
            f"SELECT * FROM {scan(p / 'multiple_anchor_claims.parquet')}" for p in base.values()
        )
        member_union = " UNION ALL ".join(
            f"SELECT * FROM {scan(p / 'members.parquet')}" for p in members.values()
        )
        entity_parts = self.stage(
            domain + "-partition-entities",
            lambda out: [
                "SET partitioned_write_max_open_files=1024",
                "SET partitioned_write_flush_threshold=65536",
                f"COPY (SELECT entity_id,taxon,decision,CASE WHEN anchor_count=1 THEN anchor END primary_anchor,hash(entity_id)%128 bucket FROM ({member_union})) TO {quote(out / 'parts')} (FORMAT PARQUET,COMPRESSION ZSTD,PARTITION_BY(bucket),OVERWRITE_OR_IGNORE true)",
            ],
        )

        def group_entities(out):
            (out / "parts").mkdir(exist_ok=True)
            queries = []
            for index, part in enumerate(sorted((entity_parts / "parts").glob("bucket=*"))):
                queries.append(
                    self.copy(
                        f"""SELECT entity_id,CASE WHEN starts_with(entity_id,'inchikey:') THEN 'chemical' WHEN starts_with(entity_id,'uniprot:') THEN 'protein' ELSE {quote("chemical" if domain == "chemical" else "gene")} END kind,
                    min(primary_anchor) primary_anchor,
                    min(taxon) FILTER(WHERE taxon<>'0') taxon,bool_or(decision='multiple_anchor_claims') quarantined
                    FROM {scan(part / "*.parquet")} GROUP BY entity_id""",
                        out / "parts" / f"{index}.parquet",
                    )
                )
            if not queries:
                queries.append(
                    self.copy(
                        "SELECT NULL::VARCHAR entity_id,NULL::VARCHAR kind,NULL::VARCHAR primary_anchor,NULL::VARCHAR taxon,false quarantined WHERE false",
                        out / "parts" / "0.parquet",
                    )
                )
            return queries

        entity_groups = self.stage(domain + "-group-entities", group_entities)
        goslin_aliases = (
            f" UNION ALL SELECT DISTINCT 'goslin',g.goslin,m.entity_id,g.record_id FROM {scan(goslin / 'claims.parquet')} g JOIN ({member_union}) m USING(record_id)"
            if goslin is not None
            else ""
        )
        final = self.stage(
            domain + "-entities",
            lambda out: [
                self.copy(
                    f"""SELECT * FROM {scan(entity_groups / "parts" / "*.parquet")}
                {f"UNION ALL SELECT 'entrez:' || p.entrez_id entity_id,'gene' kind,NULL::VARCHAR primary_anchor,min(p.taxon) taxon,false quarantined FROM ({gene_products_query(graph)}) p ANTI JOIN {scan(entity_groups / 'parts' / '*.parquet')} e ON e.entity_id='entrez:' || p.entrez_id GROUP BY p.entrez_id" if domain == "gene_protein" else ""}
                UNION ALL SELECT DISTINCT anchor entity_id,'chemical' kind,anchor primary_anchor,NULL taxon,false quarantined FROM ({multi_union}) x ANTI JOIN ({member_union}) m ON m.entity_id=x.anchor""",
                    out / "entities.parquet",
                ),
                self.copy(
                    f"SELECT hub namespace,local_id identifier,entity_id,record_id FROM ({member_union}) UNION ALL SELECT 'inchikey',substr(entity_id,10),entity_id,NULL FROM {scan(out / 'entities.parquet')} WHERE starts_with(entity_id,'inchikey:') UNION ALL SELECT 'entrez',substr(entity_id,8),entity_id,entity_id FROM {scan(out / 'entities.parquet')} WHERE starts_with(entity_id,'entrez:'){goslin_aliases}",
                    out / "identity_identifiers.parquet",
                ),
                self.copy(
                    f"SELECT category,count(*) assertions FROM {scan(decisions / 'edge_assessments.parquet')} GROUP BY category",
                    out / "diagnostic_counts.parquet",
                ),
                self.copy(
                    f"SELECT * FROM {scan(decisions / 'edge_assessments.parquet')} WHERE category IN ('missing_target','multiple_anchor_claims','different_anchors')",
                    out / "cross_reference_exceptions.parquet",
                ),
                self.copy(
                    f"SELECT * FROM ({member_union}) WHERE decision IN ('ambiguous_native','multiple_anchor_claims')",
                    out / "ambiguous_records.parquet",
                ),
            ],
        )
        if goslin is not None:
            self.stage(
                "goslin-qc",
                lambda out: [
                    self.copy(
                        f"SELECT g.goslin,count(DISTINCT m.record_id) records,count(DISTINCT m.anchor) FILTER(WHERE m.anchor_count=1) full_anchors,bool_or(m.anchor_count>1) multiple_anchor_claims FROM {scan(goslin / 'claims.parquet')} g JOIN ({member_union}) m USING(record_id) GROUP BY g.goslin",
                        out / "anchor_counts.parquet",
                    )
                ],
            )
        if domain == "gene_protein":
            products = self.stage(
                "gene-products",
                lambda out: [
                    self.copy(
                        gene_products_query(graph),
                        out / "gene_products.parquet",
                    )
                ],
            )
        if domain == "gene_protein":
            self.stage(
                "gene-resolution",
                lambda out: [
                    self.copy(
                        gene_resolution_query(final, products),
                        out / "entrez_identifiers.parquet",
                    )
                ],
            )
        checks = self.stage(
            domain + "-validation",
            lambda out: [
                self.copy(
                    f"""SELECT count(*) members,
            count(*) FILTER(WHERE anchor_count=1 AND entity_id<>anchor) changed_anchors,
            count(*) FILTER(WHERE entity_id IS NULL) missing_identity
            FROM ({member_union})""",
                    out / "membership_checks.parquet",
                )
            ],
        )
        import pyarrow.parquet as pq

        check = pq.read_table(checks / "membership_checks.parquet").to_pylist()[0]
        expected = sum(rows(p / "records.parquet") for p in base.values())
        if check["members"] != expected or check["changed_anchors"] or check["missing_identity"]:
            raise RuntimeError(f"Membership validation failed: {check}, expected {expected}")
        # Attribute claims retain the source locator. No nested all-node enrichment.
        for name, p in members.items():
            source = self.input_paths[name]
            ident = norm(quote(name), "hub_id")
            value = norm("source_type", "source_id")
            self.stage(
                "claims-" + name,
                lambda out, p=p, source=source, ident=ident, value=value, name=name: [
                    self.copy(
                        f"""SELECT m.record_id,m.entity_id,r.source_type namespace,{value} identifier,{quote(name + ":")} || r.file_row_number::VARCHAR assertion_id,
                CASE WHEN source_type IN ('name','synonym','lipid_shorthand','systematic_name','formula','smiles','inchi') THEN 'attribute'
                     WHEN source_type={quote(name)} AND {value}=m.local_id THEN 'native_identity'
                     WHEN source_type='inchikey' AND m.entity_id='inchikey:' || {value} THEN 'anchor'
                     WHEN source_type IN ({",".join(map(quote, names))}) THEN 'cross_reference' ELSE 'unverified_alias' END AS "role"
                FROM read_parquet({quote(source)},file_row_number=true) r JOIN {scan(p / "members.parquet")} m ON m.local_id={ident}
                WHERE source_id IS NOT NULL AND trim(source_id)<>'' AND source_type NOT IN ('smiles','inchi','formula','name','synonym','lipid_shorthand','systematic_name')""",
                        out / "identifier_claims.parquet",
                    )
                ],
            )
        if domain == "gene_protein":
            # Native source-gene IDs project to supported primary proteins using
            # authoritative assertions; they never merge different protein anchors.
            source_keys = " UNION ALL ".join(
                f"SELECT '{name}' namespace,hub_id identifier,source_type ns,{norm('source_type', 'source_id')} AS source_value,taxonomy_id taxon FROM {scan(self.input_paths[name])} WHERE source_type IN ('uniprot','entrez','ensg','ensp','enst','hgnc','refseq_protein','genbank')"
                for name in ("ramp_gene",)
            )
            self.stage(
                "source-gene-resolution",
                lambda out: [
                    "CREATE TABLE source_keys AS " + source_keys,
                    f"CREATE TABLE candidates AS SELECT DISTINCT k.namespace,k.identifier,i.entity_id,k.taxon FROM source_keys k JOIN {scan(self.out / 'claims-uniprot' / 'identifier_claims.parquet')} i ON k.source_value=i.identifier AND (k.ns=i.namespace OR (k.ns='uniprot' AND i.namespace='uniprot-sec')) WHERE k.ns<>'entrez' UNION SELECT DISTINCT k.namespace,k.identifier,i.entity_id,k.taxon FROM source_keys k JOIN {scan(self.out / 'gene-resolution' / 'entrez_identifiers.parquet')} i ON k.source_value=i.identifier WHERE k.ns='entrez'",
                    self.copy(
                        f"SELECT DISTINCT c.namespace,c.identifier,CASE WHEN starts_with(c.entity_id,'entrez:') THEN c.entity_id ELSE 'entrez:' || p.entrez_id END entity_id,'explicit_source_gene' admission,c.namespace || ':' || c.identifier record_id FROM candidates c LEFT JOIN {scan(self.out / 'gene-products' / 'gene_products.parquet')} p ON c.entity_id=p.protein_entity_id WHERE starts_with(c.entity_id,'entrez:') OR (p.entrez_id IS NOT NULL AND (c.taxon IN ('','0') OR c.taxon=p.taxon))",
                        out / "protein_identifiers.parquet",
                    ),
                    self.copy(
                        f"SELECT * FROM {scan(out / 'protein_identifiers.parquet')} UNION ALL SELECT i.namespace,i.identifier,i.entity_id,'source_gene_only' admission,i.record_id FROM {scan(final / 'identity_identifiers.parquet')} i ANTI JOIN {scan(out / 'protein_identifiers.parquet')} p USING(namespace,identifier) WHERE i.namespace='ramp_gene' AND NOT starts_with(i.entity_id,'uniprot:')",
                        out / "identifiers.parquet",
                    ),
                ],
            )
        return {
            "entities": str(final / "entities.parquet"),
            "identity_identifiers": str(final / "identity_identifiers.parquet"),
            "counts": json.loads((final / "counts.json").read_text()),
        }

    def run(self):
        outputs = {}
        if self.args.domain in ("all", "chemical"):
            outputs["chemical"] = self.domain("chemical", CHEMICAL)
            outputs["goslin"] = str(self.out / "goslin-identifiers" / "claims.parquet")
        if self.args.domain in ("all", "gene_protein"):
            outputs["gene_protein"] = self.domain("gene_protein", PROTEIN)
            outputs["gene_resolution"] = str(
                self.out / "gene-resolution" / "entrez_identifiers.parquet"
            )
        for source, v in self.fingerprints.items():
            st = Path(v["path"]).stat()
            if st.st_size != v["bytes"] or st.st_mtime_ns != v["mtime_ns"]:
                raise RuntimeError(f"Input changed during build: {source}")
        manifest = {
            "version": VERSION,
            "status": "complete",
            "format": "omnipath-offline-reference-parquet-v1",
            "full_scope": True,
            "fingerprint": self.fingerprint,
            "sources": self.fingerprints,
            "frozen_inputs": self.input_paths,
            "outputs": outputs,
            "stages": self.completed,
            "attributes": "Retained in immutable input hub assertions; locate by hub and assertion row number.",
            "gene_resolution_policy": "NCBI Gene identity is independent of primary UniProt product identity; explicit gene-product links never expand gene observations onto proteins.",
            "identity_policy": "Full anchors equal; anchorless components see all boundary anchors; differing anchors never merge. Ambiguous components retain native record identities.",
        }
        temp = self.out / "manifest.json.tmp"
        temp.write_text(json.dumps(manifest, indent=2))
        os.replace(temp, self.out / "manifest.json")
        log("build_complete", outputs=outputs)


def derive_gene_resolution(args):
    """Add the same build stage to an immutable catalogue without redoing hubs."""
    source = Path(args.derive_gene_resolution_from).resolve()
    out = Path(args.output).resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("status") != "complete":
        raise RuntimeError("Parent reference is not complete")
    if manifest.get("version") != VERSION:
        raise RuntimeError(
            "Gene-product identity requires a fresh reference build; legacy identity components cannot be derived safely"
        )
    out.mkdir(parents=True, exist_ok=False)
    for child in source.iterdir():
        if child.is_dir() and child.name not in ("gene-resolution", "gene-products"):
            (out / child.name).symlink_to(child, target_is_directory=True)
    stage = out / "gene-resolution"
    stage.mkdir()
    products = out / "gene-products"
    products.mkdir()
    query = gene_resolution_query(source / "gene_protein-entities", products)
    identity = dict(
        parent_fingerprint=manifest["fingerprint"],
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        policy="gene-product-identity-v2",
    )
    job = dict(
        name="gene-resolution",
        out=str(stage),
        memory=args.memory,
        threads=args.threads,
        sql=[
            f"COPY ({gene_products_query(source / 'gene_protein-graph')}) TO {quote(products / 'gene_products.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)",
            f"COPY ({query}) TO {quote(stage / 'entrez_identifiers.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)",
        ],
    )
    job_path = stage / "job.json"
    job_path.write_text(json.dumps(job, indent=2))
    subprocess.run([sys.executable, __file__, "--worker", str(job_path)], check=True)
    manifest.update(
        version=VERSION,
        fingerprint=digest(identity),
        parent_reference=str(source),
        derivation=identity,
        gene_resolution_policy="Gene identity independent of products; no gene-to-protein observation projection.",
    )
    manifest["stages"] = [s for s in manifest["stages"] if s != "gene-resolution"] + [
        "gene-resolution"
    ]
    manifest["outputs"]["gene_resolution"] = str(stage / "entrez_identifiers.parquet")
    temporary = out / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2))
    os.replace(temporary, out / "manifest.json")
    log(
        "derived_reference_complete",
        output=str(out),
        fingerprint=manifest["fingerprint"],
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--worker")
    p.add_argument("--derive-gene-resolution-from")
    p.add_argument("--hubs")
    p.add_argument(
        "--reuse-stages-from",
        help="Reuse identical normalized hub stages from an immutable reference with hard-linked inputs",
    )
    p.add_argument("--output")
    p.add_argument("--components")
    p.add_argument("--domain", choices=["all", "chemical", "gene_protein"], default="all")
    p.add_argument("--memory", default="4GB")
    p.add_argument("--threads", type=int, default=2)
    args = p.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.derive_gene_resolution_from:
        if not args.output:
            p.error("--output is required")
        derive_gene_resolution(args)
    else:
        if not all((args.hubs, args.output, args.components)):
            p.error("--hubs, --output and --components are required")
        Build(args).run()


if __name__ == "__main__":
    main()
