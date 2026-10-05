"""Build compact indexes from assigned hub records, without the old lookup indexes.

Canonical membership/admission semantics are reused. Eight unordered, temporary
projections replace the old runtime access tables, partitioned by entity ID or
Entrez identifier for the Ensembl fallback. Identifier
assertions scan canonical tables directly; final values go straight to compact
LMDB instead of a JSON database followed by conversion.
"""

from __future__ import annotations

import base64
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import time

import pyarrow as pa
import pyarrow.parquet as pq

from .compact_index import FORMAT, CompactWriter, atomic_json
from .full_index import Compiler, Writer, log, quote, publish as publish_base
from .projections import specification

BULK_METADATA = (
    "chemical-entities",
    "gene_protein-entities",
    "records-uniprot",
    "gene-products-forward",
    "uniprot-ensg-forward",
    "claims-entrez",
)


def compiler_hash():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in [
        "direct_compact_index.py",
        "full_index.py",
        "full_index_assertions.py",
        "full_index_identifiers.py",
        "compact_index.py",
        "candidate_limit.py",
        "index_rewrite.py",
        "identifier_admission.py",
        "projections.py",
        "gene_role_index.py",
    ]:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


class DirectCompiler(Compiler):
    def __init__(
        self, reference, output, memory="3GB", threads=4, min_free_gib=40, *, dictionaries=None
    ):
        reference, output = Path(reference).resolve(), Path(output).absolute()
        fingerprint = json.loads((reference / "manifest.json").read_text())["fingerprint"]
        self.queries = {name: query for name, _, _, query in specification(reference)}
        source = dict(reference_fingerprint=fingerprint, tables={name: {} for name in self.queries})
        for domain in ["chemical", "gene_protein"]:
            source["tables"][domain + "-entities"]["rows"] = pq.ParquetFile(
                reference / (domain + "-entities/entities.parquet")
            ).metadata.num_rows
        self.dictionary_bytes = {}
        dictionary_specs = {}
        dictionary_root = Path(dictionaries) if dictionaries else Path(__file__).parent / "data"
        for kind in ["entities", "identifiers"]:
            path = dictionary_root / (kind + ".zstd")
            if not path.exists():
                path = dictionary_root / (kind + "-dictionary.zstd")
            raw = path.read_bytes()
            self.dictionary_bytes[kind] = raw
            dictionary_specs[kind] = dict(
                file="dictionaries/" + kind + ".zstd",
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        contract = dict(
            format=FORMAT,
            reference_fingerprint=fingerprint,
            partitions=256,
            dictionaries=dictionary_specs,
            checkpoint_layout="compiler",
            builder="direct-compact-v1",
            gene_role_component_required=True,
            compiler_sha256=compiler_hash(),
        )
        super().__init__(
            reference,
            output,
            memory,
            threads,
            min_free_gib,
            source_manifest=source,
            contract=contract,
        )
        for kind, raw in self.dictionary_bytes.items():
            path = output / dictionary_specs[kind]["file"]
            path.parent.mkdir(exist_ok=True)
            if path.exists():
                if path.read_bytes() != raw:
                    raise ValueError("Build dictionary changed")
            else:
                with path.open("xb") as f:
                    f.write(raw)
        self.bulk_queries = {name: self.queries[name] for name in BULK_METADATA}
        for domain in ["chemical", "gene_protein"]:
            names = [
                n
                for n in self.queries
                if n.startswith(domain + "-") and n.endswith(("-forward", "-names"))
            ]
            if domain == "gene_protein":
                names.append("gene-resolution-forward")
            self.bulk_queries[domain + "-aliases"] = " UNION ALL ".join(
                "SELECT entity_id _key,entity_id,namespace,identifier FROM ("
                + self.queries[name]
                + ")"
                for name in names
            )
        self.bulk = {}

    def prepare_bulk(self):
        for name, query in self.bulk_queries.items():
            ready = self.output / "bulk-checkpoints" / (name + ".json")
            target = self.output / "bulk-inputs" / name
            if ready.exists():
                record = json.loads(ready.read_text())
                actual = {
                    str(p.relative_to(self.output)): p.stat().st_size
                    for p in target.rglob("*.parquet")
                }
                if actual != record["files"]:
                    raise ValueError("Bulk source partition changed: " + name)
                self.bulk[name] = record
                continue
            started = time.monotonic()
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            with self.connection("bulk-" + name) as c:
                columns = f"SELECT * EXCLUDE(_key) FROM ({query})"
                schema = c.execute(columns + " LIMIT 0").to_arrow_table().schema
                c.execute("SET partitioned_write_max_open_files=256")
                count = c.execute(
                    f"COPY (SELECT * EXCLUDE(_key),substr(md5(_key),1,2) shard FROM ({query})) TO {quote(target)} (FORMAT PARQUET,COMPRESSION ZSTD,PARTITION_BY(shard))"
                ).fetchone()[0]
            record = dict(
                name=name,
                rows=count,
                seconds=time.monotonic() - started,
                schema=base64.b64encode(schema.serialize().to_pybytes()).decode(),
                files={
                    str(p.relative_to(self.output)): p.stat().st_size
                    for p in target.rglob("*.parquet")
                },
            )
            atomic_json(ready, record)
            self.bulk[name] = record
            shutil.rmtree(self.output / "scratch" / ("bulk-" + name))
            log(
                "direct_bulk_complete",
                name=name,
                rows=count,
                seconds=record["seconds"],
                bytes=sum(record["files"].values()),
            )

    def relation(self, c, name, part=None):
        if part is None:
            return "(" + self.queries[name] + ")"
        if name not in self.bulk:
            path = self.output / "bulk-checkpoints" / (name + ".json")
            self.bulk[name] = json.loads(path.read_text())
        record = self.bulk[name]
        prefix = f"bulk-inputs/{name}/shard={part}/"
        paths = [self.output / file for file in record["files"] if file.startswith(prefix)]
        if not paths:
            schema = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(record["schema"])))
            table = "empty_" + name.replace("-", "_")
            c.register(table, pa.Table.from_batches([], schema=schema))
            return table
        for path in paths:
            if path.stat().st_size != record["files"][str(path.relative_to(self.output))]:
                raise ValueError("Bulk partition is missing or changed")
        return "read_parquet([" + ",".join(quote(p) for p in paths) + "],hive_partitioning=false)"

    def entity_aliases(self, c, domain, part):
        return "SELECT entity_id,namespace,identifier FROM " + self.relation(
            c, domain + "-aliases", part
        )

    def writer(self, path, category):
        if category in self.dictionary_bytes:
            return CompactWriter(
                path,
                category,
                self.dictionary_bytes[category],
                audit_limit=10 if category == "identifiers" else None,
            )
        return Writer(path)  # Small, temporary gene-product adjacency for the compiler.

    def publish(self):
        # This is a direct build, not a conversion: validate every final writer's count.
        for kind, folder in [
            ("entities", "entity-checkpoints"),
            ("identifiers", "identifiers-checkpoints"),
        ]:
            for n in range(256):
                cp = json.loads((self.output / folder / f"{n:02x}.json").read_text())
                if cp["records"] != cp["exact_round_trip_records"]:
                    raise ValueError("Direct compact partition lacks exact validation")
        return publish_base(self)

    def cleanup_scratch(self):
        manifest = json.loads((self.output / "manifest.json").read_text())
        if not manifest.get("complete") or manifest.get("builder") != "direct-compact-v1":
            raise ValueError("Cannot retire compiler inputs before publication")
        removed = 0
        for name in [
            "bulk-inputs",
            "raw-assertions",
            "enriched-assertions",
            "metadata",
            "products",
            "scratch",
            "candidate-policy/archive",
            "candidate-policy/affected-entities",
            "candidate-policy/affected-source-rows",
            "candidate-policy/spill",
        ]:
            path = self.output / name
            if path.exists():
                removed += sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                shutil.rmtree(path)
        atomic_json(
            self.output / "compiler-inputs-retired.json", dict(logical_bytes_removed=removed)
        )
        return removed


@lru_cache(maxsize=None)
def _compiler(reference, output, memory, threads, reserve, dictionaries):
    """One compiler per process and build: partitions need not rebuild it (and its
    manifest/dictionary/checkpoint-validation state) 256 times per stage.
    ``build_direct_compact`` clears this so a reused process never sees a stale build."""
    return DirectCompiler(reference, output, memory, threads, reserve, dictionaries=dictionaries)


def _partition_worker(arguments):
    reference, output, memory, threads, reserve, dictionaries, stage, part = arguments
    compiler = _compiler(reference, output, memory, threads, reserve, dictionaries)
    if stage == "entities":
        return compiler.entity_partition(part)
    if stage == "enrich":
        from .full_index_assertions import enrich_assertion_partition

        return enrich_assertion_partition(compiler, part)
    if stage == "products":
        from .full_index_identifiers import product_partition

        return product_partition(compiler, part)
    from .full_index_identifiers import Products, identifier_partition

    products = Products(compiler.output)
    try:
        return identifier_partition(compiler, part, products)
    finally:
        products.close()


def build_direct_compact(
    reference,
    output,
    *,
    memory="3GB",
    threads=2,
    workers=2,
    min_free_gib=0,
    dictionaries=None,
):
    """Compile an assigned catalogue into the published compact runtime format."""
    from .candidate_limit import apply_limit
    from .full_index_assertions import stage_assertions

    _compiler.cache_clear()
    output = Path(output).absolute()
    compiler = DirectCompiler(
        reference, output, memory, threads, min_free_gib, dictionaries=dictionaries
    )
    compiler.prepare_bulk()
    stage_assertions(compiler)
    jobs = {
        stage: [
            (reference, output, memory, threads, min_free_gib, dictionaries, stage, f"{n:02x}")
            for n in range(256)
        ]
        for stage in ("entities", "enrich", "products", "identifiers")
    }
    if workers == 1:
        for stage_jobs in jobs.values():
            for job in stage_jobs:
                _partition_worker(job)
    else:
        # Stages stay strictly sequential; the worker processes are shared so each
        # does not pay interpreter and DuckDB/PyArrow start-up again per stage.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for stage_jobs in jobs.values():
                list(pool.map(_partition_worker, stage_jobs))
    compiler.publish()
    apply_limit(output, reference)
    atomic_json(
        output / "assignment-manifest.json",
        json.loads((Path(reference) / "manifest.json").read_text()),
    )
    from .gene_role_index import build_gene_role_index

    build_gene_role_index(
        reference, output, memory=memory, threads=threads, min_free_gib=min_free_gib
    )
    compiler.cleanup_scratch()
    return json.loads((output / "manifest.json").read_text())
