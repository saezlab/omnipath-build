"""Goslin 2 normalized shorthand identifiers for normalized reference hubs.

IDs are local level-qualified descriptors, not accession numbers issued by Goslin.
Only the most specific successfully parsed name level on each record is admitted.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.reference.goslin_cache import NormalizationCache

SOURCES = ("swisslipids", "lipidmaps", "hmdb", "chebi", "refmet")
_PARSER = None
SCHEMA = pa.schema(
    [(n, pa.string()) for n in ("name", "goslin", "level", "status")]
    + [("specificity", pa.int32())]
)


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def normalize(name):
    global _PARSER
    from pygoslin.parser.Parser import LipidParser
    from pygoslin.domain.LipidLevel import LipidLevel
    from pygoslin.domain.LipidExceptions import LipidException

    if _PARSER is None:
        _PARSER = LipidParser()
    result = dict(name=name, goslin=None, level=None, status="unparsed", specificity=0)
    try:
        lipid = _PARSER.parse(name)
    except LipidException:
        return result
    if lipid is None:
        return result
    level = lipid.lipid.info.level
    if level.value < LipidLevel.SPECIES.value:
        result["status"] = "class_only"
        return result
    if lipid.adduct is not None:
        result["status"] = "adduct_or_isotope"
        return result
    lipid.sort_fatty_acyl_chains()  # only unordered chains; preserves known sn positions
    label = lipid.get_lipid_string()
    result.update(
        goslin=level.name.lower() + ":" + label,
        level=level.name.lower(),
        specificity=level.value,
        status="parsed",
    )
    return result


def parse_batch(names):
    return [normalize(name) for name in names]


def build(hubs, output, workers=6, memory="4GB", cache_dir=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute(f"SET memory_limit={quote(memory)}")
    c.execute(f"SET threads={int(workers)}")
    c.execute("SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory={quote(output / 'spill')}")
    pieces = []
    for hub, path in hubs.items():
        if hub not in SOURCES:
            continue
        # Preserve export shorthand columns and systematic fatty-acyl names.
        # Goslin 2 can parse names without a carbon:double-bond token; the parser
        # decides eligibility, without manual chemical-name translations.
        pieces.append(f"""SELECT {quote(hub)} hub,hub_id,
            {quote(hub + ":")} || CASE WHEN {quote(hub)}='chebi' THEN
            'CHEBI:' || regexp_replace(upper(trim(hub_id)), '^CHEBI:', '') ELSE trim(hub_id) END record_id,
            source_type name_type,source_id source_name,trim(part.name) AS name FROM read_parquet({quote(path)}),
            UNNEST(CASE WHEN {quote(hub)}='swisslipids' AND source_type IN ('synonym','lipid_shorthand') THEN string_split(source_id,' | ')
                        WHEN {quote(hub)}='lipidmaps' AND source_type='synonym' THEN string_split(source_id,'; ')
                        ELSE [source_id] END) part(name)
            WHERE source_type IN ('name','synonym','lipid_shorthand','systematic_name') AND hub_id IS NOT NULL
            AND trim(hub_id)<>'' AND source_id IS NOT NULL
            AND trim(part.name)<>''""")
    raw = (
        " UNION ALL ".join(pieces)
        if pieces
        else "SELECT NULL::VARCHAR hub,NULL::VARCHAR hub_id,NULL::VARCHAR record_id,NULL::VARCHAR name_type,NULL::VARCHAR source_name,NULL::VARCHAR AS name WHERE false"
    )
    c.execute(
        f"COPY (SELECT DISTINCT * FROM ({raw})) TO {quote(output / 'names.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    c.execute(
        f"COPY (SELECT DISTINCT name FROM read_parquet({quote(output / 'names.parquet')})) TO {quote(output / 'unique-names.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    total = pq.ParquetFile(output / "unique-names.parquet").metadata.num_rows
    cache = NormalizationCache(cache_dir or output.parent / ".goslin-cache", normalize)
    try:
        cached = 0
        with (
            pq.ParquetWriter(output / "cached.parquet", SCHEMA, compression="zstd") as hits,
            pq.ParquetWriter(
                output / "pending-names.parquet",
                pa.schema([("name", pa.string())]),
                compression="zstd",
            ) as misses,
        ):
            for batch in pq.ParquetFile(output / "unique-names.parquet").iter_batches(
                batch_size=4096
            ):
                names = batch.column("name").to_pylist()
                found = cache.lookup(names)
                seen = {row["name"] for row in found}
                if found:
                    hits.write_table(pa.Table.from_pylist(found, schema=SCHEMA))
                    cached += len(found)
                missing = [name for name in names if name not in seen]
                if missing:
                    misses.write_table(pa.table({"name": missing}))
        print(
            json.dumps(
                dict(
                    event="goslin_cache",
                    cached_names=cached,
                    new_names=total - cached,
                    cache=str(cache.path),
                )
            ),
            flush=True,
        )
        print(
            json.dumps(dict(event="goslin_start", total_names=total, workers=workers)),
            flush=True,
        )
        start = time.monotonic()
        done = 0
        tick = start
        with pq.ParquetWriter(output / "normalized.parquet", SCHEMA, compression="zstd") as writer:
            for batch in pq.ParquetFile(output / "cached.parquet").iter_batches():
                writer.write_batch(batch)
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
                pending = set()

                def drain(block=False):
                    nonlocal pending, done, tick
                    ready, pending = concurrent.futures.wait(
                        pending,
                        timeout=1 if block else 0,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in ready:
                        results = future.result()
                        cache.store(results)
                        writer.write_table(pa.Table.from_pylist(results, schema=SCHEMA))
                        done += len(results)
                    if time.monotonic() - tick >= 10:
                        print(
                            json.dumps(
                                dict(
                                    event="goslin_progress",
                                    parsed_names=done,
                                    total_names=total,
                                    cached_names=cached,
                                    names_per_second=round(
                                        done / max(time.monotonic() - start, 0.001), 1
                                    ),
                                    seconds=round(time.monotonic() - start),
                                )
                            ),
                            flush=True,
                        )
                        tick = time.monotonic()

                for b in pq.ParquetFile(output / "pending-names.parquet").iter_batches(
                    batch_size=128
                ):
                    while len(pending) >= workers * 2:
                        drain(True)
                    pending.add(pool.submit(parse_batch, b.column("name").to_pylist()))
                    drain()
                while pending:
                    drain(True)
        # Keeping only the most specific aliases prevents a record's generic synonym
        # from bridging otherwise different lipid descriptions or precision levels.
        c.execute(f"""COPY (SELECT n.*,p.goslin,p.level FROM read_parquet({quote(output / "names.parquet")}) n
            JOIN read_parquet({quote(output / "normalized.parquet")}) p USING(name)
            WHERE p.status='parsed'
            QUALIFY p.specificity=max(p.specificity) OVER(PARTITION BY record_id))
            TO {quote(output / "claims.parquet")} (FORMAT PARQUET, COMPRESSION ZSTD)""")
        counts = c.execute(f"""SELECT hub,count(DISTINCT record_id) records,count(DISTINCT goslin) identifiers
            FROM read_parquet({quote(output / "claims.parquet")}) GROUP BY hub""").fetchall()
        summary = dict(
            sources=counts,
            unique_names=total,
            parsed_names=done,
            cached_names=cached,
            seconds=round(time.monotonic() - start, 2),
            parse_status=c.execute(
                f"SELECT status,count(*) FROM read_parquet({quote(output / 'normalized.parquet')}) GROUP BY status"
            ).fetchall(),
        )
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(dict(event="goslin_complete", **summary)), flush=True)
    finally:
        cache.close()
        c.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hubs", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--memory", default="4GB")
    p.add_argument("--cache-dir")
    args = p.parse_args()
    build(
        {s: str(Path(args.hubs) / (s + ".parquet")) for s in SOURCES},
        args.output,
        args.workers,
        args.memory,
        args.cache_dir,
    )
