"""Command-line interface for the OmniPath full-parquet build pipeline.

Provides commands to discover and build input sources into the 3 required Parquet files.
"""

from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path

from .hubs.export import HUB_NAMES, export_hubs
from .pipeline import build_all, build_resource


def main(args: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="omnipath-build",
        description="Build nested Parquet files directly from inputs_v2 datasets.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build", help="Build Parquet datasets from an inputs_v2 source"
    )
    build_parser.add_argument(
        "source", nargs="?", help="Resource source name (e.g. signor, uniprot, chebi)"
    )
    build_parser.add_argument("--sources", nargs="+", help="Multiple resource sources to build")
    build_parser.add_argument("--version", help="Explicit numeric resource version (e.g. 1.0.0)")
    build_parser.add_argument(
        "--versions", type=Path, help="JSON file mapping each source to its explicit version"
    )
    build_parser.add_argument("--output-dir", default="data", help="Output root directory")
    build_parser.add_argument(
        "--max-records", type=int, help="Limit number of records processed per dataset"
    )
    build_parser.add_argument(
        "--batch-size",
        type=int,
        default=50000,
        help="Streaming entity batch limit (default: 50000)",
    )
    build_parser.add_argument(
        "--memory-limit",
        help="DuckDB memory budget per connection (default: OMNIPATH_BUILD_DUCKDB_MEMORY or 1GB)",
    )
    build_parser.add_argument(
        "--parallel", "-j", type=int, default=1, help="Number of parallel worker jobs"
    )

    run_parser = subparsers.add_parser(
        "run", help="Schedule isolated resource builds under a shared RAM/CPU budget"
    )
    run_parser.add_argument("resources", nargs="*", help="Resources to build")
    run_parser.add_argument(
        "--sources", nargs="+", help="Named resources (alternative to positional names)"
    )
    run_parser.add_argument("--all", action="store_true", help="Build all discoverable resources")
    run_parser.add_argument(
        "--ram", required=True, help="Aggregate worker memory ceiling, e.g. 10GiB"
    )
    run_parser.add_argument(
        "--cpus", required=True, type=int, help="Maximum CPU budget; always leaves CPU headroom"
    )
    run_parser.add_argument(
        "--reserve-cpus",
        type=int,
        default=1,
        help="CPUs excluded from the build (minimum/default: 1)",
    )
    run_parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Concurrent resources (default: 1, with parallel extraction batches)",
    )
    run_parser.add_argument(
        "--worker-ram", default="4GiB", help="Initial reservation for resources without history"
    )
    run_parser.add_argument(
        "--profiles", type=Path, help='JSON resource overrides, e.g. {"chembl": {"ram": "6GiB"}}'
    )
    run_parser.add_argument("--version", help="Numeric version for every selected resource")
    run_parser.add_argument("--versions", type=Path, help="JSON per-resource versions")
    run_parser.add_argument("--output-dir", type=Path, default=Path("data"))
    run_parser.add_argument("--library-dir", type=Path)
    run_parser.add_argument("--max-records", type=int, help="Record limit per dataset")
    run_parser.add_argument("--batch-size", type=int, default=50000)
    run_parser.add_argument(
        "--skip-existing", action="store_true", help="Skip existing complete resource versions"
    )
    run_parser.add_argument(
        "--min-free-disk",
        default="20GiB",
        help="Stop safely below this free disk reserve (default: 20GiB)",
    )
    run_parser.add_argument(
        "--max-retries", type=int, default=2, help="Retries with larger RAM after memory failures"
    )
    run_parser.add_argument(
        "--executor",
        choices=("systemd", "local"),
        default="systemd",
        help="systemd enforces limits; local is advisory development mode",
    )
    run_parser.add_argument(
        "--user",
        action="store_true",
        help="Use the user systemd manager with delegated controllers",
    )

    hubs_parser = subparsers.add_parser(
        "export-hubs",
        help="Stream identifier sources into per-hub Parquet lookup files",
    )
    hubs_parser.add_argument(
        "--output-dir",
        default="data/reference/hubs",
        help="Directory for hub Parquet files",
    )
    hubs_parser.add_argument(
        "--hubs",
        nargs="+",
        choices=list(HUB_NAMES),
        help="Hubs to export (default: all)",
    )
    hubs_parser.add_argument(
        "--max-records",
        type=int,
        default=100,
        help="Cap output rows per hub file (default: 100). Pass 0 for no cap.",
    )
    hubs_parser.add_argument(
        "--parallel",
        "-j",
        type=int,
        default=1,
        help="Number of parallel hub worker threads",
    )
    hubs_parser.add_argument(
        "--no-library",
        action="store_true",
        help="Skip building the reference entity library after the hub files",
    )

    library_parser = subparsers.add_parser(
        "build-library",
        help="Build and publish the complete compact reference from all source hubs",
    )
    library_parser.add_argument(
        "--hubs-dir",
        default="data/reference/hubs",
        help="Directory containing per-source hub Parquet files",
    )
    library_parser.add_argument(
        "--output-dir",
        default=None,
        help="Library directory (default: <hubs-dir>/../library)",
    )
    library_parser.add_argument(
        "--taxa",
        nargs="+",
        help="Explicitly restrict NCBI taxonomy ids (default: every taxon in the hubs)",
    )
    library_parser.add_argument(
        "--all-taxa",
        action="store_true",
        help="Include every taxon present in the hubs (the default; overrides --taxa)",
    )
    library_parser.add_argument(
        "--libraries",
        nargs="+",
        choices=["gene_protein", "chemical"],
        default=None,
        help="Libraries to build (default: both)",
    )
    library_parser.add_argument(
        "--memory-limit", default=None, help="DuckDB memory limit, e.g. 8GB"
    )
    library_parser.add_argument("--threads", type=int, default=None, help="DuckDB thread count")

    from .regression import cli as regression_cli

    regression_cli.add_parser(subparsers)

    parsed = parser.parse_args(args)

    if parsed.command == "resolution-regression":
        return regression_cli.run(parsed)

    if parsed.command == "export-hubs":
        max_records = None if parsed.max_records == 0 else parsed.max_records
        counts = export_hubs(
            parsed.output_dir,
            hubs=parsed.hubs,
            max_records=max_records,
            build_library=not parsed.no_library,
            parallel=parsed.parallel,
        )
        for name, count in counts.items():
            print(f"{name}: {count}")
        return 0

    if parsed.command == "build-library":
        from .canonical import LIBRARIES, build_library

        result = build_library(
            parsed.hubs_dir,
            parsed.output_dir,
            taxa=parsed.taxa,
            all_taxa=parsed.all_taxa,
            libraries=parsed.libraries or LIBRARIES,
            memory_limit=parsed.memory_limit,
            threads=parsed.threads,
            on_progress=lambda name, status: print(f"{name}: {status}", file=sys.stderr),
        )
        for name, counts in result.counts.items():
            print(f"{name}: {counts['nodes']:,} nodes, {counts['xrefs']:,} xrefs")
        for name in result.skipped:
            print(f"{name}: skipped (missing hubs)", file=sys.stderr)
        print(f"library: {result.library_dir} ({result.elapsed_s:.1f}s)")
        return 0 if result.counts else 1

    if parsed.command == "run":
        from .orchestrator import (
            Budget,
            ConsoleProgress,
            LocalExecutor,
            SystemdExecutor,
            memory_bytes,
            orchestrate,
        )
        from .discovery import list_sources
        import signal

        if (parsed.all and (parsed.resources or parsed.sources)) or (
            parsed.resources and parsed.sources
        ):
            parser.error("Use resource names, --sources, or --all, not a combination")
        try:
            budget = Budget(
                memory_bytes(parsed.ram),
                parsed.cpus,
                parsed.jobs,
                memory_bytes(parsed.worker_ram),
                parsed.reserve_cpus,
            )
            sources = (
                [item["source"] for item in list_sources()]
                if parsed.all
                else parsed.sources or parsed.resources
            )
            if not sources:
                parser.error("Specify resources or --all")
            versions = json.loads(parsed.versions.read_text()) if parsed.versions else {}
            profiles = json.loads(parsed.profiles.read_text()) if parsed.profiles else {}
            cancelled = False

            def cancel(*_):
                nonlocal cancelled
                cancelled = True

            old_handler = signal.signal(signal.SIGTERM, cancel)
            try:
                result = orchestrate(
                    sources,
                    budget=budget,
                    version=parsed.version,
                    versions=versions,
                    output_dir=parsed.output_dir,
                    max_records=parsed.max_records,
                    batch_size=parsed.batch_size,
                    library_dir=parsed.library_dir,
                    skip_existing=parsed.skip_existing,
                    profiles=profiles,
                    max_retries=parsed.max_retries,
                    min_free_disk=memory_bytes(parsed.min_free_disk),
                    executor=SystemdExecutor(user=parsed.user)
                    if parsed.executor == "systemd"
                    else LocalExecutor(),
                    on_progress=ConsoleProgress(),
                    should_cancel=lambda: cancelled,
                )
            finally:
                signal.signal(signal.SIGTERM, old_handler)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print(f"Scheduler error: {exc}", file=sys.stderr)
            return 1
        print(f"Run summary: {result['run_dir']}/summary.json")
        return 0 if result["status"] == "success" else 130 if result["status"] == "cancelled" else 1

    if parsed.command == "build":
        sources = parsed.sources or ([parsed.source] if parsed.source else [])
        if not sources:
            print("Error: Specify a source or --sources", file=sys.stderr)
            return 1

        versions = json.loads(parsed.versions.read_text()) if parsed.versions else {}
        from .versioning import validate_version

        try:
            for source in sources:
                validate_version(versions.get(source, parsed.version))
        except (ValueError, AttributeError) as exc:
            parser.error(str(exc))

        if len(sources) == 1:
            build_resource(
                source=sources[0],
                version=versions.get(sources[0], parsed.version),
                output_dir=parsed.output_dir,
                max_records=parsed.max_records,
                batch_size=parsed.batch_size,
                duckdb_memory_limit=parsed.memory_limit,
            )
        else:
            build_all(
                sources=sources,
                version=parsed.version,
                versions=versions,
                output_dir=parsed.output_dir,
                max_records=parsed.max_records,
                batch_size=parsed.batch_size,
                duckdb_memory_limit=parsed.memory_limit,
                parallel=parsed.parallel,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
