#!/usr/bin/env python3
"""
Simple benchmark driver for the entity lookup service.

It samples identifiers from the Parquet file (without loading the whole column),
then issues POST /lookup requests in batches and reports latency/throughput.
"""

import argparse
import json
import random
import statistics
import time
from typing import Iterable, List
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pyarrow.parquet as pq


def sample_identifiers(path: str, sample_size: int, batch_size: int = 8192) -> List[str]:
    """Reservoir sample identifiers so we don't load the full column into memory."""
    pf = pq.ParquetFile(path)
    reservoir: List[str] = []
    seen = 0

    for batch in pf.iter_batches(columns=["identifier"], batch_size=batch_size):
        arr = batch.column(0)
        valid = arr.is_valid()
        for i in range(len(arr)):
            if not valid[i].as_py():
                continue
            seen += 1
            val = arr[i].as_py()
            if len(reservoir) < sample_size:
                reservoir.append(val)
            else:
                j = random.randrange(seen)
                if j < sample_size:
                    reservoir[j] = val

    return reservoir


def chunked(seq: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def post_batch(url: str, batch: List[str]):
    payload = json.dumps({"identifiers": batch}).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as resp:
        resp.read()
        return resp.status


def run_benchmark(url: str, batches: List[List[str]], runs: int, warmup: int):
    def do_run():
        timings = []
        for batch in batches:
            start = time.perf_counter()
            status = post_batch(url, batch)
            elapsed = time.perf_counter() - start
            if status != 200:
                raise RuntimeError(f"unexpected status code {status}")
            timings.append(elapsed)
        return timings

    for _ in range(warmup):
        do_run()

    all_timings = []
    for _ in range(runs):
        all_timings.extend(do_run())

    return all_timings


def main():
    parser = argparse.ArgumentParser(description="Benchmark the entity lookup service.")
    parser.add_argument(
        "--url", default="http://localhost:8080/lookup", help="lookup endpoint URL"
    )
    parser.add_argument(
        "--parquet-path",
        default="../databases/omnipath/output/entity_identifier.parquet",
        help="path to entity_identifier.parquet",
    )
    parser.add_argument("--sample-size", type=int, default=1000, help="identifiers to sample")
    parser.add_argument("--batch-size", type=int, default=128, help="identifiers per request")
    parser.add_argument("--runs", type=int, default=5, help="timed runs (excludes warmups)")
    parser.add_argument("--warmup", type=int, default=1, help="warmup runs")
    parser.add_argument("--seed", type=int, default=42, help="random seed for sampling")
    args = parser.parse_args()

    random.seed(args.seed)

    identifiers = sample_identifiers(args.parquet_path, args.sample_size)
    print(f"Sampled {len(identifiers)} identifiers from {args.parquet_path}")
    print(f"First 5 identifiers: {identifiers[:5]}")

    batches = list(chunked(identifiers, args.batch_size))
    parsed = urlparse(args.url)
    print(
        f"Target: {args.url} (host: {parsed.hostname}, port: {parsed.port or 80}) | "
        f"{len(batches)} batches of up to {args.batch_size}"
    )

    timings = run_benchmark(args.url, batches, args.runs, args.warmup)
    total_seconds = sum(timings)
    total_requests = len(timings)
    total_ids = len(identifiers) * (args.runs if args.runs else 1)

    if not timings:
        print("No timings recorded.")
        return

    ms = [t * 1000 for t in timings]
    p50 = statistics.median(ms)
    p95 = statistics.quantiles(ms, n=20)[18] if len(ms) > 1 else ms[0]
    mean = statistics.mean(ms)
    reqs_per_sec = total_requests / total_seconds
    ids_per_sec = total_ids / total_seconds

    print(
        f"Latency per request (batch): mean={mean:.2f}ms p50={p50:.2f}ms p95={p95:.2f}ms "
        f"| Throughput: {reqs_per_sec:.1f} req/s, {ids_per_sec:.1f} ids/s"
    )


if __name__ == "__main__":
    main()
