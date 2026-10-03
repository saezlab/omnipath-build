"""Offline candidate materialization for the complete identifier universe."""

from __future__ import annotations

import json
import shutil
import time
from collections import defaultdict

import lmdb

from .full_index import log, partition, quote, read_value, validate_enriched
from omnipath_resolver.observations import CODES, key

SYMBOLS = "('genesymbol','genesymbol-syn')"
FACT = "json_array(id::UBIGINT,entity_id,kind,anchor,quarantined,reviewed)"


def dump(value):
    return json.dumps(value, separators=(",", ":")).encode()


def source(compiler, c, part):
    paths = sorted((compiler.output / "enriched-assertions").glob(f"*/shard={part}/*.parquet"))
    if paths:
        return f"read_parquet([{','.join(quote(p) for p in paths)}],hive_partitioning=false)"
    c.execute("""CREATE TEMP TABLE empty_assertions(target UTINYINT,route UTINYINT,
        namespace VARCHAR,identifier VARCHAR,entity_id VARCHAR,tag VARCHAR,id UBIGINT,
        kind UBIGINT,anchor VARCHAR,taxon VARCHAR,quarantined BOOLEAN,reviewed BOOLEAN,
        gene_ids VARCHAR[],ensg_ids VARCHAR[])""")
    return "empty_assertions"


def finish(compiler, writer, staging, category, part, start, **extra):
    result = writer.close()
    staging.rename(compiler.output / category / part)
    result.update(part=part, seconds=time.perf_counter() - start, **extra)
    ready = compiler.output / (category + "-checkpoints") / (part + ".json")
    ready.parent.mkdir(exist_ok=True)
    ready.write_text(json.dumps(result, indent=2))
    shutil.rmtree(compiler.output / "scratch" / (category + "-" + part))
    log(category + "_complete", **result)
    return result


def start_writer(compiler, category, part):
    staging = compiler.output / category / ("." + part + ".tmp")
    orphan = compiler.output / category / part
    if orphan.exists():
        # A previous process may have synced/renamed the private shard but
        # exited before writing its completion checkpoint.
        shutil.rmtree(orphan)
    if staging.exists():
        shutil.rmtree(staging)
    return staging, compiler.writer(staging, category)


def product_partition(compiler, part):
    if not getattr(compiler, "_enriched_validated", False):
        validate_enriched(compiler)
        compiler._enriched_validated = True
    ready = compiler.output / "products-checkpoints" / (part + ".json")
    if ready.exists():
        return json.loads(ready.read_text())
    start = time.perf_counter()
    staging, writer = start_writer(compiler, "products", part)
    try:
        with compiler.connection("products-" + part) as c:
            src = source(compiler, c, part)
            q = f"""SELECT encode(namespace||':'||identifier),
                encode(to_json(list(json_object('candidate',{FACT},'taxon',taxon,'genes',gene_ids) ORDER BY id)))
                FROM (SELECT DISTINCT namespace,identifier,id,entity_id,kind,anchor,quarantined,reviewed,taxon,gene_ids
                    FROM {src} WHERE tag='product' AND id IS NOT NULL AND kind=2)
                GROUP BY namespace,identifier ORDER BY namespace,identifier"""
            for batch in c.execute(q).to_arrow_reader(batch_size=8192):
                compiler.guard()
                writer.put(list(zip(batch.column(0).to_pylist(), batch.column(1).to_pylist())))
        return finish(compiler, writer, staging, "products", part, start)
    except BaseException:
        writer.env.close()
        raise


class Products:
    """Compiler-only gene adjacency; no runtime dependency or result cache."""

    def __init__(self, root):
        self.root = root
        self.opened = {}
        self.projections = None
        self.entity_shards = None
        self.entity_codec = None

    def get(self, namespace, identifier):
        part = partition(identifier)
        if part not in self.opened:
            path = self.root / "products" / part
            if not (self.root / "products-checkpoints" / (part + ".json")).exists():
                raise ValueError("Product partition not complete: " + part)
            env = lmdb.open(str(path), readonly=True, lock=False, readahead=False)
            self.opened[part] = (env, env.begin())
        raw = read_value(
            self.opened[part][1], ("_product_" + namespace + ":" + identifier).encode()
        )
        return json.loads(raw) if raw else []

    def projected(self, namespace, identifier):
        # Entrez ENSG fallback precedes isoform projection in the old policy;
        # symbol product expansion follows it. Keep these two paths distinct.
        if self.projections is None:
            import pyarrow.parquet as pq
            from omnipath_resolver.index import Shards

            self.projections = {
                r["isoform"]: r["parent"]
                for r in pq.read_table(self.root / "isoform-projections.parquet").to_pylist()
            }
            self.entity_shards = Shards(self.root / "entities")
            from .compact_index import FORMAT, load_codecs

            contract = json.loads((self.root / "build-contract.json").read_text())
            if contract["format"] == FORMAT:
                self.entity_codec = load_codecs(self.root, contract["dictionaries"])["entities"]
        rows = {}
        for row in self.get(namespace, identifier):
            parent = self.projections.get(row["candidate"][1])
            if parent:
                raw = self.entity_shards.get(partition(parent), parent.encode())
                if raw is None:
                    continue
                value = self.entity_codec.decode(raw) if self.entity_codec else json.loads(raw)
                meta = value["meta"]
                candidate = [
                    int(meta["id"]),
                    meta["entity_id"],
                    meta["kind"],
                    meta["anchor"],
                    meta["quarantined"],
                    meta["reviewed"],
                ]
                row = dict(candidate=candidate, taxon=value["record"]["taxon"], genes=[])
            rows[row["candidate"][0]] = row
        return list(rows.values())

    def close(self):
        if self.entity_shards is not None:
            self.entity_shards.close()
        for env, txn in self.opened.values():
            txn.abort()
            env.close()


def identifier_partition(compiler, part, products):
    ready = compiler.output / "identifiers-checkpoints" / (part + ".json")
    if ready.exists():
        return json.loads(ready.read_text())
    start = time.perf_counter()
    staging, writer = start_writer(compiler, "identifiers", part)
    try:
        with compiler.connection("identifiers-" + part) as c:
            src = source(compiler, c, part)
            c.execute(f"CREATE TEMP TABLE raw AS SELECT * FROM {src} WHERE tag<>'product'")
            # Keep existence markers before joining/filtering entity metadata.
            c.execute("""CREATE TEMP TABLE admitted AS SELECT * FROM raw r
                WHERE (tag<>'chemical_fallback' OR NOT EXISTS(
                    SELECT 1 FROM raw n WHERE n.target=r.target AND n.route=r.route
                    AND n.namespace=r.namespace AND n.identifier=r.identifier AND n.tag='native'))
                AND id IS NOT NULL AND (kind=target OR (target=2 AND route=2 AND kind=3))""")
            c.execute(f"""CREATE TEMP TABLE regular AS SELECT DISTINCT target,route,namespace,identifier,
                id,entity_id,kind,anchor,quarantined,reviewed,taxon
                FROM admitted WHERE namespace NOT IN {SYMBOLS}""")
            # Store the unscoped and each present taxon list. Absent taxa are true misses.
            c.execute("""CREATE TEMP VIEW scoped AS
                SELECT *,'' AS scope FROM regular UNION ALL
                SELECT *,taxon AS scope FROM regular WHERE taxon IS NOT NULL AND taxon<>''""")
            ns_case = (
                "CASE namespace "
                + " ".join(f"WHEN {quote(ns)} THEN '{code:04x}'" for ns, code in CODES.items())
                + " END"
            )
            key_sql = f"from_hex('01'||printf('%02x',target)||printf('%02x',route)||({ns_case})||CASE WHEN scope='' THEN '00' ELSE '01'||printf('%08x',scope::UBIGINT) END)||encode(identifier)"
            # LIST aggregate states do not spill. Sort the complete mapping
            # rows once, then aggregate at most 50,000 complete keys at a time.
            # A key's candidate list is never split by these memory boundaries.
            c.execute(f"""CREATE TEMP TABLE ordered AS
                SELECT {key_sql} AS lookup_key,{FACT} AS candidate,id,
                    target=2 AND namespace IN ('hgnc','ensg') AS gene,
                    target=2 AND namespace IN ('hgnc','ensg') AS products
                FROM scoped ORDER BY lookup_key,id""")
            c.execute("DROP VIEW scoped")
            c.execute("DROP TABLE regular")
            ranges = c.execute("""SELECT min(lookup_key),max(lookup_key) FROM (
                SELECT lookup_key,(row_number() OVER(ORDER BY lookup_key)-1)//50000 AS bucket
                FROM (SELECT DISTINCT lookup_key FROM ordered))
                GROUP BY bucket ORDER BY bucket""").fetchall()
            for lower, upper in ranges:
                q = f"""SELECT lookup_key,encode(to_json(struct_pack(
                    candidates:=list(candidate ORDER BY id),
                    gene:=bool_or(gene),products:=bool_or(products))))
                    FROM ordered WHERE lookup_key BETWEEN from_hex({quote(lower.hex())}) AND from_hex({quote(upper.hex())})
                    GROUP BY lookup_key ORDER BY lookup_key"""
                for batch in c.execute(q).to_arrow_reader(batch_size=8192):
                    compiler.guard()
                    writer.put(list(zip(batch.column(0).to_pylist(), batch.column(1).to_pylist())))
            c.execute("DROP TABLE ordered")
            # For symbols, all gene projection is paid once here, including
            # verification that the expansion does not bridge different genes.
            c.execute(f"""CREATE TEMP TABLE symbols AS SELECT DISTINCT namespace,identifier,id,entity_id,
                kind,anchor,quarantined,reviewed,taxon,gene_ids,ensg_ids
                FROM admitted r WHERE namespace IN {SYMBOLS} AND taxon IS NOT NULL AND taxon<>''
                AND (tag<>'symbol_synonym' OR NOT EXISTS(
                    SELECT 1 FROM admitted e WHERE e.namespace=r.namespace AND e.identifier=r.identifier
                    AND e.taxon=r.taxon AND e.tag<>'symbol_synonym'))""")
            q = f"""SELECT namespace,identifier,taxon,to_json(list(json_object('candidate',{FACT},
                'genes',gene_ids,'ensg',ensg_ids) ORDER BY id)) FROM symbols
                GROUP BY namespace,identifier,taxon ORDER BY namespace,identifier,taxon"""
            buffer = []
            bridges = 0
            for batch in c.execute(q).to_arrow_reader(batch_size=4096):
                for ns, ident, taxon, raw in zip(*(batch.column(i).to_pylist() for i in range(4))):
                    rows = json.loads(raw)
                    genes = {g for r in rows for g in r["genes"]}
                    ensg = {g for r in rows for g in r["ensg"]}
                    bridge = None
                    if len(genes) == 1 and all(r["genes"] for r in rows):
                        bridge = ("entrez", next(iter(genes)))
                    elif len(ensg) == 1 and len(genes) <= 1 and all(r["ensg"] for r in rows):
                        bridge = ("ensg", next(iter(ensg)))
                    selected = [r["candidate"] for r in rows]
                    expanded = []
                    if bridge:
                        expanded = [r for r in products.get(*bridge) if r["taxon"] == taxon]
                        if (
                            bridge[0] == "ensg"
                            and len({g for r in expanded for g in r["genes"]}) > 1
                        ):
                            expanded = []
                        if expanded:
                            selected = [r["candidate"] for r in expanded]
                            bridges += 1
                    buffer.append(
                        (
                            key(2, 1, ns, taxon, ident),
                            dump(
                                dict(
                                    candidates=sorted(selected), gene=True, products=bool(expanded)
                                )
                            ),
                        )
                    )
                compiler.guard()
                writer.put(buffer)
                buffer.clear()
            # NCBI Ensembl gene aliases fill only absent UniProt/native mappings.
            claims = compiler.relation(c, "claims-entrez", part)
            q = f"""SELECT i.identifier,min(i.record_id) FROM {claims} i
                WHERE i.namespace='ensg' AND NOT EXISTS(SELECT 1 FROM raw r
                    WHERE r.target=2 AND r.route=1 AND r.namespace='ensg' AND r.identifier=i.identifier)
                GROUP BY i.identifier HAVING count(DISTINCT i.record_id)=1"""
            fallbacks = 0
            for batch in c.execute(q).to_arrow_reader(batch_size=4096):
                for ident, record in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
                    if not record.startswith("entrez:"):
                        raise ValueError("Unexpected Entrez record key")
                    rows = products.projected("entrez", record[7:])
                    scopes = defaultdict(list)
                    for row in rows:
                        scopes[""].append(row["candidate"])
                        if row["taxon"]:
                            scopes[row["taxon"]].append(row["candidate"])
                    for scope, candidates in scopes.items():
                        buffer.append(
                            (
                                key(2, 1, "ensg", scope, ident),
                                dump(dict(candidates=sorted(candidates), gene=True, products=True)),
                            )
                        )
                    fallbacks += bool(rows)
                compiler.guard()
                writer.put(buffer)
                buffer.clear()
        return finish(
            compiler,
            writer,
            staging,
            "identifiers",
            part,
            start,
            symbol_bridges=bridges,
            ensg_fallbacks=fallbacks,
        )
    except BaseException:
        writer.env.close()
        raise
