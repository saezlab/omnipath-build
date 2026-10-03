"""Pin an immutable published reference generation for runtime matching."""

from pathlib import Path


def pin_library(library_dir):
    if library_dir is None:
        return None
    root = Path(library_dir).resolve()
    current = root / "current"
    if current.is_symlink():
        root = current.resolve(strict=True)
        if not (root / "manifest.json").is_file():
            raise ValueError(f"Incomplete reference generation: {root}")
    return root
