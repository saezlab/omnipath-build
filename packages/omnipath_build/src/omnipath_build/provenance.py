"""Content fingerprints for reproducible published artifacts and checkpoints."""

from __future__ import annotations

import hashlib
import json
import importlib.metadata
import platform
import subprocess
from pathlib import Path


def file_fingerprint(path: str | Path) -> dict[str, str | int]:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def runtime_provenance() -> dict:
    packages = (
        "omnipath-build",
        "omnipath-core",
        "omnipath-resolver",
        "pypath-omnipath",
        "duckdb",
        "pyarrow",
        "rdkit",
        "pygoslin",
    )
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    root = Path(__file__).resolve().parent
    code = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "tests" not in path.relative_to(root).parts:
            code.update(str(path.relative_to(root)).encode() + b"\0")
            code.update(path.read_bytes())
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return {
        "python": platform.python_version(),
        "dependencies": versions,
        "code_revision": revision,
        "build_code_sha256": code.hexdigest(),
    }


def reference_provenance(library_dir: str | Path | None) -> dict | None:
    if library_dir is None:
        return None
    root = Path(library_dir)
    manifest = root / "manifest.json"
    if manifest.is_file():
        value = json.loads(manifest.read_text())
        return {
            "path": str(root),
            "generation": value.get("generation"),
            "fingerprint": value.get("reference_fingerprint") or value.get("fingerprint"),
            "format": value.get("format"),
            "candidate_limit": value.get("candidate_limit"),
            "ambiguous": value.get("ambiguous"),
            "manifest": file_fingerprint(manifest),
            "files": value.get("files", {}),
        }
    return {
        "path": str(root),
        "manifest": None,
        "files": {
            str(p.relative_to(root)): file_fingerprint(p)
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in (".parquet", ".json")
        },
    }
