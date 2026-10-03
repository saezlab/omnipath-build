"""Build and atomically publish the single immutable compact reference."""

from __future__ import annotations
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from omnipath_resolver.canonical.policy import LIBRARIES
from ..locking import BuildLock
from ..duckdb_config import build_memory_limit, build_threads


@dataclass
class LibraryBuildResult:
    library_dir: Path
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    taxa: list[str] | None = None
    elapsed_s: float = 0.0
    skipped: list[str] = field(default_factory=list)


def components_binary():
    explicit = os.environ.get("OMNIPATH_COMPONENTS_BINARY")
    binary = explicit or shutil.which("anchor-components")
    if not binary:
        # Source checkout convenience; installed workers carry the binary on PATH.
        candidate = (
            Path(__file__).resolve().parents[3]
            / "resolver/rust/reference/target/release/anchor-components"
        )
        if candidate.is_file():
            binary = str(candidate)
    if not binary or not Path(binary).is_file():
        raise RuntimeError(
            "anchor-components is required; install the build-worker image or set OMNIPATH_COMPONENTS_BINARY"
        )
    return binary


def build_library(
    hubs_dir,
    library_dir=None,
    *,
    taxa=None,
    all_taxa=False,
    libraries=LIBRARIES,
    memory_limit=None,
    threads=None,
    on_progress=None,
):
    """Build all sources and taxa; publish only a complete, finalized reference."""
    from ..reference.build_reference import Build
    from ..reference.finalize_source_reference import finalize

    if taxa and not all_taxa:
        raise ValueError(
            "The compact reference is global; taxon filtering belongs to resource queries"
        )
    if set(libraries or LIBRARIES) != set(LIBRARIES):
        raise ValueError("Build both domains for the single complete compact reference")
    started = time.monotonic()
    target = Path(library_dir or Path(hubs_dir).parent / "library").absolute()
    if target.is_symlink():
        raise ValueError(
            "Publish to a library directory, not a historical reference symlink; migrate it first"
        )
    binary = components_binary()
    memory = build_memory_limit(memory_limit)
    nthreads = threads if threads is not None else build_threads()
    if nthreads < 1:
        raise ValueError("Reference build threads must be positive")
    generation = target / ".generations" / uuid.uuid4().hex
    staging = target / ".generations" / f".{generation.name}.tmp"
    with BuildLock(target / ".build.lock"):
        staging.mkdir(parents=True)
        try:
            if on_progress:
                on_progress("compact", "running")
            Build(
                SimpleNamespace(
                    output=staging / "graph",
                    hubs=hubs_dir,
                    components=binary,
                    domain="all",
                    memory=memory,
                    threads=nthreads,
                    reuse_stages_from=None,
                )
            ).run()
            assigned = staging / "assigned"
            finalize(staging / "graph", assigned, memory, nthreads)
            from ..reference.direct_compact_index import build_direct_compact

            workers = min(2, nthreads)
            build_direct_compact(
                assigned,
                generation,
                memory=memory,
                threads=max(1, nthreads // workers),
                workers=workers,
            )
            # Active readers remain pinned to their generation. Switching the
            # pointer is the only publication mutation.
            pointer = target / f".current-{generation.name}"
            pointer.symlink_to(generation.relative_to(target), target_is_directory=True)
            pointer.replace(target / "current")
        except BaseException:
            if generation.exists():
                shutil.rmtree(generation)
            raise
        finally:
            shutil.rmtree(staging)
    if on_progress:
        on_progress("compact", "done")
    assignment = json.loads((generation / "assignment-manifest.json").read_text())
    counts = {
        d: {
            "nodes": assignment["outputs"][d]["counts"].get("entities.parquet", 0),
            "xrefs": assignment["outputs"][d]["counts"].get("identity_identifiers.parquet", 0),
        }
        for d in LIBRARIES
    }
    return LibraryBuildResult(generation, counts=counts, elapsed_s=time.monotonic() - started)
