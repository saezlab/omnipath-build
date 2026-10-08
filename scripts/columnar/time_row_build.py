"""Time the row path's build of one resource: wall and CPU seconds of the whole process tree.

    uv run python scripts/columnar/time_row_build.py bindingdb OUT --library-dir DIR --workers 8
"""

import argparse
import json
import resource
import time

from omnipath_build.pipeline import build_resource


def cpu():
    own, children = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("out")
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--ram-gib", type=int, default=16)
    args = parser.parse_args()
    wall, cpu0 = time.perf_counter(), cpu()
    result = build_resource(
        args.source, version="2026.10.7", output_dir=args.out, library_dir=args.library_dir,
        batch_workers=args.workers, resource_ram_bytes=args.ram_gib * 1024**3,
    )
    report = dict(
        wall_seconds=round(time.perf_counter() - wall, 1), cpu_seconds=round(cpu() - cpu0, 1),
        phases=result.get("phase_metrics"), workers=args.workers,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
