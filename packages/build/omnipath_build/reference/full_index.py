"""Compile the complete immutable reference into two partitioned runtime indexes.

The catalogue is processed in bounded partitions. Runtime reads only complete
stored values; LMDB and the OS supply page management, not a result cache.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shutil
import time

import duckdb
import lmdb
import pyarrow as pa

from omnipath_resolver.index_storage import (
    FORMAT,
    PARTITIONS,
    CHUNK_BYTES as CHUNK_BYTES,
    MAX_RUNTIME_VALUE as MAX_RUNTIME_VALUE,
    partition as partition,
    packed_key as packed_key,
    storage_pairs,
    read_value as read_value,
)


def log(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class Writer:
    def __init__(self, path):
        path.mkdir(parents=True, exist_ok=False)
        self.path = path
        self.env = lmdb.open(str(path), map_size=8 * 1024**3, sync=False, metasync=False)
        self.count = 0

    def put(self, rows):
        pairs = [pair for key, value in rows for pair in storage_pairs(key, value)]
        with self.env.begin(write=True) as txn:
            with txn.cursor() as cursor:
                consumed, added = cursor.putmulti(pairs, overwrite=False)
                if consumed != len(pairs) or added != len(pairs):
                    raise ValueError("Duplicate key while compiling immutable index")
        self.count += len(rows)

    def close(self):
        self.env.sync(True)
        stats = self.env.stat()
        self.env.close()
        return dict(records=self.count, bytes=(self.path / "data.mdb").stat().st_size, lmdb=stats)


class Compiler:
    def __init__(
        self,
        reference,
        output,
        memory="3GB",
        threads=4,
        min_free_gib=40,
        *,
        source_manifest=None,
        contract=None,
    ):
        self.reference = Path(reference).resolve()
        self.output = Path(output).absolute()
        self.output.mkdir(parents=True, exist_ok=True)
        self.source = (
            source_manifest
            if source_manifest is not None
            else json.loads((self.reference / "lookup/manifest.json").read_text())
        )
        self.fingerprint = json.loads((self.reference / "manifest.json").read_text())["fingerprint"]
        if self.source["reference_fingerprint"] != self.fingerprint:
            raise ValueError("Reference/index fingerprint mismatch")
        self.min_free = min_free_gib * 1024**3
        self.memory = memory
        self.threads = threads
        self.contract = contract or dict(
            format=FORMAT, reference_fingerprint=self.fingerprint, partitions=256
        )
        if self.contract["reference_fingerprint"] != self.fingerprint:
            raise ValueError("Compiler contract/reference fingerprint mismatch")
        contract_file = self.output / "build-contract.json"
        if contract_file.exists() and json.loads(contract_file.read_text()) != self.contract:
            raise ValueError("Cannot resume a different reference build")
        if not contract_file.exists():
            contract_file.write_text(json.dumps(self.contract, indent=2))

    def writer(self, path, category):
        return Writer(path)

    def entity_aliases(self, c, domain, part):
        forwards = [
            n
            for n in self.source["tables"]
            if n.startswith(domain + "-") and n.endswith(("-forward", "-names"))
        ]
        if domain == "gene_protein":
            forwards.append("gene-resolution-forward")
        return " UNION ALL ".join(
            f"SELECT entity_id,namespace,identifier FROM {self.relation(c, n, part)}"
            for n in forwards
        )

    def guard(self):
        if shutil.disk_usage(self.output).free < self.min_free:
            raise RuntimeError("Full-index build stopped at configured free-disk reserve")

    def connection(self, name):
        self.guard()
        c = duckdb.connect()
        c.execute(f"SET memory_limit={quote(self.memory)}")
        c.execute(f"SET threads={int(self.threads)}")
        c.execute("SET preserve_insertion_order=false")
        scratch = self.output / "scratch" / name
        scratch.mkdir(parents=True, exist_ok=True)
        c.execute(f"SET temp_directory={quote(scratch)}")
        c.execute("SET max_temp_directory_size='32GiB'")
        return c

    def relation(self, c, name, part=None):
        spec = self.source["tables"][name]
        parts = spec["parts"]
        if part is not None and spec["sharded"]:
            parts = {part: parts[part]} if part in parts else {}
        if not parts:
            schema = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(spec["schema"])))
            table_name = "empty_" + name.replace("-", "_")
            c.register(table_name, pa.Table.from_batches([], schema=schema))
            return table_name
        paths = [str(self.reference / "lookup" / (p["file"] + ".parquet")) for p in parts.values()]
        source = f"read_parquet([{','.join(quote(p) for p in paths)}])"
        if part is not None and not spec["sharded"]:
            return f"(SELECT * FROM {source} WHERE substr(md5(_key),1,2)={quote(part)})"
        return source

    def entity_partition(self, part):
        ready = self.output / "entity-checkpoints" / f"{part}.json"
        if ready.exists():
            return json.loads(ready.read_text())
        started = time.perf_counter()
        staging = self.output / "entities" / f".{part}.tmp"
        if staging.exists():
            shutil.rmtree(staging)
        writer = self.writer(staging, "entities")
        (self.output / "metadata").mkdir(exist_ok=True)
        domain_counts = {}
        try:
            for domain_index, domain in enumerate(("chemical", "gene_protein")):
                with self.connection("entity-" + part) as c:
                    entity_source = self.relation(c, domain + "-entities", part)
                    union = self.entity_aliases(c, domain, part)
                    c.execute(f"CREATE TEMP TABLE aliases AS SELECT DISTINCT * FROM ({union})")
                    reviewed = "false"
                    if domain == "gene_protein":
                        r = self.relation(c, "records-uniprot", part)
                        c.execute(f"CREATE TEMP TABLE reviewed AS SELECT anchor,reviewed FROM {r}")
                        reviewed = "COALESCE(r.reviewed,false)"
                    c.execute(f"""CREATE TEMP TABLE entities AS
                        SELECT {int(part, 16) * 2**40 + domain_index * 2**39}::UBIGINT + row_number() OVER(ORDER BY e.entity_id)::UBIGINT id,
                            e.entity_id,CASE e.kind WHEN 'chemical' THEN 1 WHEN 'protein' THEN 2 WHEN 'gene' THEN 3 END::UBIGINT kind,
                            e.primary_anchor anchor,e.taxon,e.quarantined,{reviewed} reviewed
                        FROM {entity_source} e
                        {"LEFT JOIN reviewed r ON r.anchor=e.entity_id" if domain == "gene_protein" else ""}""")
                    # Small intrinsic adjacency lists are compiler inputs for
                    # symbol-to-gene projection, never recomputed by the runtime.
                    if domain == "gene_protein":
                        products = self.relation(c, "gene-products-forward", part)
                        ensg = self.relation(c, "uniprot-ensg-forward", part)
                        c.execute(
                            f"CREATE TEMP TABLE genes AS SELECT protein_entity_id entity_id,list(DISTINCT entrez_id ORDER BY entrez_id) gene_ids FROM {products} GROUP BY protein_entity_id"
                        )
                        c.execute(
                            f"CREATE TEMP TABLE ensembl AS SELECT entity_id,list(DISTINCT identifier ORDER BY identifier) ensg_ids FROM {ensg} GROUP BY entity_id"
                        )
                    else:
                        c.execute("CREATE TEMP TABLE genes(entity_id VARCHAR,gene_ids VARCHAR[])")
                        c.execute("CREATE TEMP TABLE ensembl(entity_id VARCHAR,ensg_ids VARCHAR[])")
                    metadata = self.output / "metadata" / f"{domain}-{part}.parquet"
                    c.execute(
                        f"COPY (SELECT e.*,CASE WHEN starts_with(e.entity_id,'entrez:') THEN [substr(e.entity_id,8)] ELSE COALESCE(g.gene_ids,[]::VARCHAR[]) END gene_ids,COALESCE(s.ensg_ids,[]::VARCHAR[]) ensg_ids FROM entities e LEFT JOIN genes g USING(entity_id) LEFT JOIN ensembl s USING(entity_id)) TO {quote(metadata)} (FORMAT PARQUET,COMPRESSION ZSTD)"
                    )
                    priority = (
                        "CASE namespace WHEN 'name' THEN 0 WHEN 'chebi' THEN 1 WHEN 'pubchem' THEN 2 ELSE 9 END"
                        if domain == "chemical"
                        else "CASE namespace WHEN 'genesymbol' THEN 0 WHEN 'name' THEN 1 ELSE 9 END"
                    )
                    eligible = (
                        "namespace IN ('name','chebi','pubchem') AND NOT regexp_full_match(trim(identifier),'[A-Z]{14}-[A-Z]{10}-[A-Z0-9]')"
                        if domain == "chemical"
                        else "namespace IN ('genesymbol','name')"
                    )
                    length_rank = (
                        "CASE WHEN namespace='genesymbol' THEN length(trim(identifier))>15 WHEN namespace='name' THEN length(trim(identifier))>"
                        + ("80" if domain == "chemical" else "120")
                        + " ELSE false END"
                    )
                    c.execute(f"""CREATE TEMP TABLE grouped AS SELECT entity_id,
                        list([namespace,identifier] ORDER BY namespace,identifier) identifiers,
                        arg_min(struct_pack(ns:=namespace,val:=trim(identifier)),
                            struct_pack(priority:={priority},long_value:={length_rank},
                                n:=CASE WHEN namespace IN ('name','genesymbol') THEN length(trim(identifier)) ELSE 0 END,v:=trim(identifier)))
                            FILTER (WHERE {eligible} AND length(trim(identifier)) BETWEEN 1 AND 120) preferred
                        FROM aliases GROUP BY entity_id""")
                    query = """SELECT encode(e.entity_id) AS k,encode(to_json(struct_pack(
                        record:=struct_pack(entity_id:=e.entity_id,kind:=e.kind,anchor:=e.anchor,taxon:=e.taxon,
                            label:=COALESCE(CASE g.preferred.ns
                                WHEN 'chebi' THEN CASE WHEN starts_with(upper(g.preferred.val),'CHEBI') THEN g.preferred.val ELSE 'CHEBI:'||g.preferred.val END
                                WHEN 'pubchem' THEN 'CID:'||g.preferred.val ELSE g.preferred.val END,
                                substr(e.entity_id,strpos(e.entity_id,':')+1)),
                            identifiers:=COALESCE(g.identifiers,[]::VARCHAR[][]),gene_ids:=COALESCE(links.gene_ids,[]::VARCHAR[])),
                        meta:=struct_pack(id:=e.id,entity_id:=e.entity_id,kind:=e.kind,anchor:=e.anchor,
                            quarantined:=e.quarantined,reviewed:=e.reviewed)))) AS v
                        FROM entities e LEFT JOIN grouped g USING(entity_id) LEFT JOIN genes links USING(entity_id) ORDER BY e.entity_id"""
                    count = 0
                    reader = c.execute(query).to_arrow_reader(batch_size=16384)
                    for batch in reader:
                        self.guard()
                        writer.put(
                            list(zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()))
                        )
                        count += batch.num_rows
                    domain_counts[domain] = count
                    log(
                        "entity_domain",
                        part=part,
                        domain=domain,
                        records=count,
                        seconds=round(time.perf_counter() - started, 2),
                    )
            report = writer.close()
        except BaseException:
            writer.env.close()
            raise
        target = self.output / "entities" / part
        staging.rename(target)
        report.update(part=part, domains=domain_counts, seconds=time.perf_counter() - started)
        ready.parent.mkdir(exist_ok=True)
        ready.write_text(json.dumps(report, indent=2))
        shutil.rmtree(self.output / "scratch" / ("entity-" + part))
        log("entity_partition_complete", **report)
        return report


def validate_enriched(compiler):
    raw = json.loads((compiler.output / "assertions-checkpoint.json").read_text())
    count = 0
    for n in range(PARTITIONS):
        cp = compiler.output / "enriched-checkpoints" / f"{n:02x}.json"
        if not cp.exists():
            raise ValueError("Incomplete enriched assertion universe: " + cp.name)
        count += json.loads(cp.read_text())["rows"]
    if count != raw["rows"]:
        raise ValueError("Assertion count changed while joining entity metadata")
    return count


def publish(compiler):
    from omnipath_resolver.observations import CODES

    assertion_rows = validate_enriched(compiler)
    checkpoints = {}
    files = {}
    for category in ("entities", "identifiers"):
        counts = []
        checkpoint_dir = (
            "entity-checkpoints" if category == "entities" else "identifiers-checkpoints"
        )
        for n in range(PARTITIONS):
            part = f"{n:02x}"
            cp = compiler.output / checkpoint_dir / (part + ".json")
            if not cp.exists():
                raise ValueError(f"Missing {category} partition {part}")
            data = json.loads(cp.read_text())
            file = f"{category}/{part}/data.mdb"
            if (compiler.output / file).stat().st_size != data["bytes"]:
                raise ValueError("Index size does not match completed checkpoint: " + file)
            counts.append(data["records"])
            files[file] = data["bytes"]
        checkpoints[category] = sum(counts)
    expected = sum(
        compiler.source["tables"][d + "-entities"]["rows"] for d in ("chemical", "gene_protein")
    )
    if checkpoints["entities"] != expected:
        raise ValueError("Full entity count mismatch")
    manifest = dict(
        compiler.contract,
        complete=True,
        assertions=assertion_rows,
        counts=checkpoints,
        files=files,
        namespace_codes=CODES,
        application_result_cache=False,
    )
    temp = compiler.output / ".manifest.tmp"
    temp.write_text(json.dumps(manifest, indent=2))
    temp.replace(compiler.output / "manifest.json")
    log("full_index_published", counts=checkpoints, bytes=sum(files.values()))
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=["entities", "assertions", "enrich", "products", "identifiers", "publish"],
    )
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--memory", default="3GB")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--min-free-gib", type=float, default=40)
    p.add_argument("--parts", default=",".join(f"{n:02x}" for n in range(256)))
    args = p.parse_args()
    compiler = Compiler(args.reference, args.output, args.memory, args.threads, args.min_free_gib)
    if args.command == "assertions":
        from .full_index_assertions import stage_assertions

        stage_assertions(compiler)
        return
    if args.command == "publish":
        publish(compiler)
        return
    from .full_index_identifiers import (
        Products,
        product_partition,
        identifier_partition,
    )

    products = Products(compiler.output)
    for part in args.parts.split(","):
        if args.command == "entities":
            compiler.entity_partition(part)
        elif args.command == "products":
            product_partition(compiler, part)
        elif args.command == "identifiers":
            identifier_partition(compiler, part, products)
        else:
            from .full_index_assertions import enrich_assertion_partition

            enrich_assertion_partition(compiler, part)

    products.close()


if __name__ == "__main__":
    main()
