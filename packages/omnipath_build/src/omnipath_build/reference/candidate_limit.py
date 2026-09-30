"""Archive over-limit mappings and remove them from both private runtime indexes."""

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path

import duckdb
import lmdb
import pyarrow as pa
import pyarrow.parquet as pq

from .compact_index import atomic_json, load_codecs
from .full_index import partition, quote, read_value, storage_pairs
from .full_index_runtime import Shards
from .identifier_admission import clean_record
from .index_rewrite import delete_value, fsync_dir, rewrite
from .projections import specification
from .replay_resources import CODES, key as lookup_key

POLICY = "max-10-candidates-per-scoped-lookup-v1"
SCHEMA = pa.schema(
    [
        ("lookup_key", pa.binary()),
        ("namespace", pa.string()),
        ("identifier", pa.string()),
        ("taxon", pa.uint32()),
        ("target", pa.uint8()),
        ("route", pa.uint8()),
        ("candidate_count", pa.uint64()),
        ("candidate_entity_ids", pa.large_list(pa.string())),
        ("gene_evidence", pa.bool_()),
        ("gene_products", pa.bool_()),
        ("reason", pa.string()),
    ]
)


def checkpoint(index, kind, part, compiler_layout):
    folder = (
        ("entity-checkpoints" if kind == "entities" else "identifiers-checkpoints")
        if compiler_layout
        else "checkpoints/" + kind
    )
    return index / folder / (part + ".json")


def archive_partition(index, work, part, codec, compiler_layout, audit):
    ready = work / "archive-checkpoints" / (part + ".json")
    dest = work / "archive" / (part + ".parquet")
    if ready.exists():
        report = json.loads(ready.read_text())
        if dest.stat().st_size != report["bytes"]:
            raise ValueError("Ambiguous archive changed")
        return report
    cp = json.loads(checkpoint(index, "identifiers", part, compiler_layout).read_text())
    candidates = {}
    if audit is not None:
        report = json.loads((Path(audit) / (part + ".json")).read_text())
        if report["records"] != cp["records"] or report["threshold"] != 11:
            raise ValueError("Candidate audit does not cover this complete partition")
        for row in report["rows"]:
            scope = "" if row["taxon"] is None else str(row["taxon"])
            key = lookup_key(
                row["target"], row["route"], row["namespace"], scope, row["identifier"]
            )
            candidates[key] = row["candidates"]
    else:
        stats = cp.get("candidate_limit_audit")
        if stats is None or stats["limit"] != 10:
            raise ValueError("Supply a complete threshold-11 audit for a converted build")
        candidates = {bytes.fromhex(key): count for key, count in stats["keys"]}
    reverse = {v: k for k, v in CODES.items()}
    dest.parent.mkdir(exist_ok=True)
    rows, count, total = [], 0, 0
    with (
        lmdb.open(
            str(index / "identifiers" / part), readonly=True, lock=False, readahead=False
        ) as env,
        env.begin() as txn,
    ):
        with pq.ParquetWriter(dest, SCHEMA, compression="zstd") as writer:
            for key, expected in sorted(candidates.items()):
                raw = read_value(txn, key)
                if raw is None:
                    raise ValueError("An audited candidate mapping is missing")
                value = codec.decode(raw)
                ids = [r[1] for r in value["candidates"]]
                if expected is not None and len(ids) != expected:
                    raise ValueError("Candidate count changed since the audit")
                if len(set(ids)) != len(ids):
                    raise ValueError(
                        "Duplicate candidates require correction before applying the cutoff"
                    )
                ident = key[10 if key[5] else 6 :].decode()
                if len(ids) <= 10:
                    raise ValueError("Attempted to remove an admitted lookup")
                rows.append(
                    dict(
                        lookup_key=key,
                        namespace=reverse[int.from_bytes(key[3:5], "big")],
                        identifier=ident,
                        taxon=int.from_bytes(key[6:10], "big") if key[5] else None,
                        target=key[1],
                        route=key[2],
                        candidate_count=len(ids),
                        candidate_entity_ids=ids,
                        gene_evidence=value["gene"],
                        gene_products=value["products"],
                        reason="candidate_limit",
                    )
                )
                count += 1
                total += len(ids)
                if len(rows) >= 256 or len(ids) >= 10000:
                    writer.write_table(pa.Table.from_pylist(rows, schema=SCHEMA))
                    rows = []
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=SCHEMA))
    with dest.open("rb") as f:
        os.fsync(f.fileno())
    result = dict(part=part, keys=count, candidate_links=total, bytes=dest.stat().st_size)
    atomic_json(ready, result)
    return result


def combine_archive(index, work, fingerprint):
    output = index / "ambiguous.parquet"
    ready = work / "combined-archive.json"
    if ready.exists():
        report = json.loads(ready.read_text())
        if output.stat().st_size != report["bytes"]:
            raise ValueError("Ambiguous Parquet changed")
        return report
    schema = SCHEMA.with_metadata(
        {b"policy": POLICY.encode(), b"reference_fingerprint": fingerprint.encode()}
    )
    staging = work / "ambiguous.tmp.parquet"
    rows = 0
    with pq.ParquetWriter(staging, schema, compression="zstd") as writer:
        for part in range(256):
            for batch in pq.ParquetFile(work / "archive" / f"{part:02x}.parquet").iter_batches(
                batch_size=256
            ):
                writer.write_batch(batch)
                rows += batch.num_rows
    with staging.open("rb") as f:
        os.fsync(f.fileno())
    staging.replace(output)
    fsync_dir(index)
    with output.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    report = dict(file="ambiguous.parquet", rows=rows, bytes=output.stat().st_size, sha256=digest)
    atomic_json(ready, report)
    return report


def prepare_entities(reference, index, work):
    ready = work / "affected-entities.json"
    target = work / "affected-entities"
    if ready.exists():
        return json.loads(ready.read_text())
    queries = []
    for name, _, _, query in specification(reference):
        if (
            name.startswith(("chemical-", "gene_protein-"))
            and name.endswith(("-forward", "-names"))
        ) or name == "gene-resolution-forward":
            queries.append((name, "SELECT entity_id,namespace,identifier FROM (" + query + ")"))
    if target.exists():
        import shutil

        shutil.rmtree(target)
    with duckdb.connect() as c:
        c.execute("SET threads=2")
        c.execute("SET memory_limit='2GB'")
        c.execute("SET preserve_insertion_order=false")
        c.execute("SET temp_directory=" + quote(work / "spill"))
        c.execute(
            "CREATE TEMP TABLE rejected AS SELECT DISTINCT namespace,identifier FROM read_parquet("
            + quote(index / "ambiguous.parquet")
            + ")"
        )
        # Process sources separately: a union can keep many join/hash states alive.
        # Bound Parquet writer buffers too; 256 default row groups exceed 2 GiB.
        c.execute("SET partitioned_write_max_open_files=8")
        namespaces = {r[0] for r in c.execute("SELECT DISTINCT namespace FROM rejected").fetchall()}
        sources = work / "affected-source-rows"
        checkpoints = work / "affected-source-checkpoints"
        for name, query in queries:
            if name.endswith("-names") and not namespaces.intersection(
                {"name", "synonym", "lipid_shorthand", "systematic_name"}
            ):
                continue
            source = sources / name
            checkpoint = checkpoints / (name + ".json")
            if checkpoint.exists():
                stored = json.loads(checkpoint.read_text())
                if any(
                    not (source / f).exists() or (source / f).stat().st_size != size
                    for f, size in stored["files"].items()
                ):
                    raise ValueError("Affected alias source changed")
                continue
            if source.exists():
                import shutil

                shutil.rmtree(source)
            source.parent.mkdir(exist_ok=True)
            # Keep this an explicit semi-join: correlated EXISTS creates a
            # delimiter join that groups the full reference before filtering.
            count = c.execute(
                "COPY (SELECT a.entity_id,substr(md5(a.entity_id),1,2) part FROM ("
                + query
                + ") a SEMI JOIN rejected r ON "
                "r.namespace=a.namespace AND r.identifier=a.identifier) TO "
                + quote(source)
                + " (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 2048,PARTITION_BY(part))"
            ).fetchone()[0]
            info = dict(
                source=name,
                rows=count,
                files={
                    str(p.relative_to(source)): p.stat().st_size for p in source.rglob("*.parquet")
                },
            )
            atomic_json(checkpoint, info)
            print(
                json.dumps(dict(event="affected_alias_source", source=name, rows=count)), flush=True
            )
        target.mkdir(exist_ok=True)
        count = 0
        for n in range(256):
            part = f"{n:02x}"
            files = sorted(sources.glob("*/part=" + part + "/*.parquet"))
            if not files:
                continue
            dest = target / ("part=" + part)
            dest.mkdir()
            count += c.execute(
                "COPY (SELECT DISTINCT entity_id FROM read_parquet(["
                + ",".join(quote(p) for p in files)
                + "],hive_partitioning=false)) TO "
                + quote(dest / "entities.parquet")
                + " (FORMAT PARQUET,COMPRESSION ZSTD)"
            ).fetchone()[0]
    report = dict(affected_entities=count)
    atomic_json(ready, report)
    return report


class Rules:
    def __init__(self, index, *, shards=None):
        self.rules = defaultdict(lambda: defaultdict(set))
        columns = ["namespace", "identifier", "taxon", "target", "route"]
        for row in pq.read_table(index / "ambiguous.parquet", columns=columns).to_pylist():
            self.rules[(row["namespace"], row["identifier"])][(row["target"], row["route"])].add(
                row["taxon"]
            )
        self.shards = shards if shards is not None else Shards(index / "identifiers")
        self.owns_shards = shards is None

    def excluded(self, record):
        pairs = record["identifiers"]
        if isinstance(pairs, dict):
            pairs = [(ns, v) for ns, values in pairs.items() for v in values]
        target = 1 if record["kind"] == 1 else 2
        taxon = int(record["taxon"]) if record["taxon"] not in (None, "") else None
        excluded = []
        for ns, ident in pairs:
            for (kind, route), scopes in self.rules.get((ns, ident), {}).items():
                if kind != target:
                    continue
                if taxon is not None and taxon in scopes:
                    excluded.append((ns, ident))
                    break
                if None in scopes:
                    # A usable taxon-specific mapping survives a rejected global mapping.
                    scoped = (
                        lookup_key(target, route, ns, str(taxon), ident)
                        if taxon is not None
                        else None
                    )
                    if scoped is None or self.shards.get(partition(ident), scoped) is None:
                        excluded.append((ns, ident))
                        break
        return excluded

    def close(self):
        if self.owns_shards:
            self.shards.close()


def apply_limit(index, reference, *, audit=None):
    """Call under the private generation's build lock; never mutate a deployed index."""
    index, reference = Path(index), Path(reference)
    work = index / "candidate-policy"
    work.mkdir(exist_ok=True)
    before = work / "original-manifest.json"
    if not before.exists():
        original = json.loads((index / "manifest.json").read_text())
        if not original.get("complete") or original.get("candidate_limit"):
            raise ValueError("Require an unfiltered complete private build")
        if (
            json.loads((reference / "manifest.json").read_text())["fingerprint"]
            != original["reference_fingerprint"]
        ):
            raise ValueError("Assigned reference fingerprint differs from the compact index")
        atomic_json(before, original)
    original = json.loads(before.read_text())
    if (work / "complete.json").exists():
        return json.loads(work.joinpath("complete.json").read_text())
    # No reader can open this generation during the coordinated rewrite.
    (index / "manifest.json").unlink(missing_ok=True)
    fsync_dir(index)
    codecs = load_codecs(index, original["dictionaries"])
    layout = original.get("checkpoint_layout") == "compiler"
    archive = []
    for n in range(256):
        archive.append(
            archive_partition(index, work, f"{n:02x}", codecs["identifiers"], layout, audit)
        )
    archived = combine_archive(index, work, original["reference_fingerprint"])
    reports = []
    for n, info in enumerate(archive):
        if not info["keys"]:
            continue
        part = f"{n:02x}"
        keys = (
            pq.read_table(work / "archive" / (part + ".parquet"), columns=["lookup_key"])
            .column(0)
            .to_pylist()
        )

        def remove(txn, keys=keys, *, resuming=False):
            for key in keys:
                if not delete_value(txn, key) and not resuming:
                    raise ValueError("Archived mapping missing before removal")
            return dict(
                removed_keys=len(keys),
                after_sha256=hashlib.sha256(b"".join(sorted(keys))).hexdigest(),
            )

        reports.append(rewrite(index, work, "identifiers", part, remove, compiler_layout=layout))
    targets = prepare_entities(reference, index, work)
    rules = Rules(index)
    entities = []
    try:
        for n in range(256):
            part = f"{n:02x}"
            source = work / "affected-entities" / ("part=" + part)
            if not source.exists():
                continue
            ids = pq.read_table(source, columns=["entity_id"]).column(0).to_pylist()

            def update(txn, ids=ids, *, resuming=False):
                changed = aliases = 0
                digest = hashlib.sha256()
                for eid in sorted(ids):
                    raw = read_value(txn, eid.encode())
                    if raw is None:
                        raise ValueError("Missing entity for an archived alias: " + eid)
                    obj = codecs["entities"].decode(raw)
                    excluded = rules.excluded(obj["record"])
                    corrected = clean_record(obj["record"], excluded)
                    changed_record = corrected != obj["record"]
                    obj["record"] = corrected
                    encoded = codecs["entities"].encode(obj)
                    digest.update(eid.encode())
                    digest.update(encoded)
                    if not changed_record:
                        continue
                    if codecs["entities"].decode(encoded) != obj:
                        raise ValueError("Corrected entity failed exact round-trip validation")
                    delete_value(txn, eid.encode())
                    for key, value in storage_pairs(eid.encode(), encoded):
                        if not txn.put(key, value, overwrite=False):
                            raise ValueError("Unexpected duplicate entity key")
                    changed += 1
                    aliases += len(set(excluded))
                return dict(
                    changed_entities=changed,
                    removed_aliases=aliases,
                    after_sha256=digest.hexdigest(),
                )

            entities.append(rewrite(index, work, "entities", part, update, compiler_layout=layout))
    finally:
        rules.close()
    manifest = dict(original)
    manifest["files"] = {name: (index / name).stat().st_size for name in original["files"]}
    manifest["counts"] = dict(original["counts"])
    removed = sum(r["removed_keys"] for r in reports)
    if removed != archived["rows"]:
        raise ValueError("Removal/archive counts disagree")
    manifest["counts"]["identifiers"] -= removed
    manifest.update(candidate_limit=10, candidate_policy=POLICY, ambiguous=archived)
    report = dict(
        policy=POLICY,
        removed_lookup_keys=removed,
        archived_candidate_links=sum(r["candidate_links"] for r in archive),
        changed_entity_records=sum(r["changed_entities"] for r in entities),
        removed_entity_aliases=sum(r["removed_aliases"] for r in entities),
        **targets,
    )
    atomic_json(index / "manifest.json", manifest)
    atomic_json(work / "complete.json", report)
    return report
