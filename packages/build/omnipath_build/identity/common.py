"""Shared plumbing for the identity build: context, checkpointed stages, SQL fragments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import duckdb

from omnipath_build.reference.build_reference import (  # noqa: F401  (re-exported helpers)
    CHEMICAL,
    EMPTY_KEYS,
    PROTEIN,
    norm,
    quote,
    scan,
)

FORMAT = "omnipath-identity-v1"
PARTS = tuple(f"{n:02x}" for n in range(256))
HUBS = CHEMICAL + PROTEIN
# Rule 4: preferred hub for the id of a grouped entity (earlier wins).
HUB_PREFERENCE = (
    "chebi",
    "lipidmaps",
    "swisslipids",
    "hmdb",
    "chembl",
    "pubchem",
    "kegg",
    "metanetx",
    "bigg",
    "refmet",
    "ramp",
    "ramp_gene",
)
# Structure/formula attributes are never identifiers or labels: they stay in the hub files.
ATTRIBUTE_TYPES = ("smiles", "inchi", "formula")
STRUCTURE_LEVELS = ("full_structure", "complete_structure")
INCHIKEY_RE = "^[A-Z]{14}-[A-Z]{10}-[A-Z]$"
ISOFORM_RE = "[A-Z0-9]+-[0-9]+"
ACCESS_TARGETS = {1: "chemical", 2: "gene_protein"}
# Precedence when the same (route, ns, identifier, entity) arrives with several tags.
TAG_RANK = ("native", "regular", "secondary", "fallback", "symbol_synonym")



def hub_rank(column="hub") -> str:
    return (
        f"CASE {column} "
        + " ".join(f"WHEN {quote(h)} THEN {i}" for i, h in enumerate(HUB_PREFERENCE))
        + f" ELSE {len(HUB_PREFERENCE)} END"
    )


def sql_list(values) -> str:
    return "(" + ",".join(quote(v) for v in values) + ")"


def part_of(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()[:2]


def rules_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def files(*patterns) -> str:
    """DuckDB read_parquet over glob patterns (hive columns off: partition dirs are not columns)."""
    return "read_parquet([" + ",".join(quote(p) for p in patterns) + "],hive_partitioning=false)"


def part_dirs(root: Path, parts) -> list[str]:
    """Glob patterns for the existing `part=XX` directories of the requested parts."""
    return [str(root / f"part={p}" / "*.parquet") for p in parts if (root / f"part={p}").is_dir()]


def log(event, **fields):
    print(
        json.dumps(dict(time=time.strftime("%FT%TZ", time.gmtime()), event=event, **fields)),
        flush=True,
    )


class Context:
    """Paths, DuckDB settings and checkpoint bookkeeping of one snapshot build."""

    def __init__(
        self,
        hubs_dir,
        out,
        memory="7GB",
        threads=6,
        goslin_cache=None,
        min_free_gib=50,
    ):
        self.out = Path(out).resolve()
        self.work = self.out / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        self.memory, self.threads = memory, int(threads)
        self.min_free = min_free_gib * 1024**3
        self.goslin_cache = Path(goslin_cache) if goslin_cache else self.work / "goslin-cache"
        self.hubs = {
            h: Path(hubs_dir, h + ".parquet").resolve()
            for h in HUBS
            if Path(hubs_dir, h + ".parquet").is_file()
        }
        if not self.hubs:
            raise RuntimeError(f"No hub parquet files in {hubs_dir}")
        self.timings: dict[str, float] = {}
        self.info: dict[str, dict] = {}

    # ------------------------------------------------------------------ paths and guards
    def guard(self):
        if shutil.disk_usage(self.out).free < self.min_free:
            raise RuntimeError("Identity build stopped at the free-disk reserve")

    def present(self, *hubs) -> list[str]:
        return [h for h in hubs if h in self.hubs]

    # --------------------------------------------------------------------------- duckdb
    def connect(self, name: str):
        self.guard()
        c = duckdb.connect()
        c.execute(f"SET memory_limit={quote(self.memory)}")
        c.execute(f"SET threads={self.threads}")
        c.execute("SET preserve_insertion_order=false")
        spill = self.work / "spill" / name
        spill.mkdir(parents=True, exist_ok=True)
        c.execute(f"SET temp_directory={quote(spill)}")
        c.execute("SET max_temp_directory_size='64GiB'")
        c.execute("SET partitioned_write_max_open_files=1024")
        c.execute("SET partitioned_write_flush_threshold=131072")
        return c

    def copy(self, c, query: str, path: Path, options: str = "") -> int:
        """COPY a query to one Parquet file atomically (temp file, then rename)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        n = c.execute(
            f"COPY ({query}) TO {quote(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD{options})"
        ).fetchone()[0]
        os.replace(tmp, path)
        return n

    def copy_partitioned(self, c, query: str, directory: Path, columns: str, pattern: str):
        """COPY a query into hive-partitioned directories; `pattern` keeps file names unique."""
        directory.mkdir(parents=True, exist_ok=True)
        return c.execute(
            f"COPY ({query}) TO {quote(directory)} (FORMAT PARQUET, COMPRESSION ZSTD, "
            f"PARTITION_BY ({columns}), FILENAME_PATTERN {quote(pattern + '{i}')}, "
            "OVERWRITE_OR_IGNORE true, ROW_GROUP_SIZE 131072)"
        ).fetchone()[0]

    # ------------------------------------------------------------------------- stages
    def stage(self, name: str, outputs, fn):
        """Run `fn(self, directory)` unless its _SUCCESS.json and listed outputs are present."""
        directory = self.work / name
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / "_SUCCESS.json"
        outputs = [Path(o) for o in outputs]
        if marker.exists() and all(o.exists() for o in outputs):
            done = json.loads(marker.read_text())
            self.timings[name] = done["seconds"]
            self.info[name] = done["info"]
            log("stage_reused", stage=name, seconds=done["seconds"])
            return done["info"]
        self.guard()
        marker.unlink(missing_ok=True)
        log("stage_start", stage=name)
        start = time.monotonic()
        info = fn(self, directory) or {}
        seconds = round(time.monotonic() - start, 2)
        marker.write_text(json.dumps(dict(seconds=seconds, info=info), indent=2))
        shutil.rmtree(self.work / "spill" / name, ignore_errors=True)
        self.timings[name] = seconds
        self.info[name] = info
        log("stage_done", stage=name, seconds=seconds, **{k: v for k, v in info.items() if k != "x"})
        return info


def finish_part_loop(directory: Path, name: str):
    """Mark one iteration of a part loop as done (idempotent checkpoint inside a stage)."""
    (directory / "done").mkdir(exist_ok=True)
    (directory / "done" / name).write_text("")


def part_done(directory: Path, name: str) -> bool:
    return (directory / "done" / name).exists()
