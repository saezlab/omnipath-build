from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_PER_SOURCE_ROOT = Path('data_v2/build/per_source')
DEFAULT_GLOBAL_RESOLUTION_DIR = Path('data_v2/build/global_resolution_snapshot')
DEFAULT_SOURCE_PACKAGE_ROOT = Path('data_v2/source_packages')
DEFAULT_INPUTS_PACKAGE = 'pypath.inputs_v2'


def load_local_env() -> None:
    for base in (Path.cwd(), *Path.cwd().parents):
        env_path = base / '.env'
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break


@dataclass(frozen=True)
class PackageEmitterPaths:
    per_source_root: Path
    global_resolution_dir: Path
    source_package_root: Path
    inputs_package: str


def get_package_emitter_paths() -> PackageEmitterPaths:
    load_local_env()
    return PackageEmitterPaths(
        per_source_root=Path(os.environ.get('OMNIPATH_BUILD_PER_SOURCE_ROOT', DEFAULT_PER_SOURCE_ROOT)),
        global_resolution_dir=Path(os.environ.get('OMNIPATH_BUILD_GLOBAL_RESOLUTION_DIR', DEFAULT_GLOBAL_RESOLUTION_DIR)),
        source_package_root=Path(os.environ.get('OMNIPATH_BUILD_SOURCE_PACKAGE_ROOT', DEFAULT_SOURCE_PACKAGE_ROOT)),
        inputs_package=os.environ.get('OMNIPATH_BUILD_INPUTS_PACKAGE', DEFAULT_INPUTS_PACKAGE),
    )


def default_silver_dir(source: str) -> Path:
    return get_package_emitter_paths().per_source_root / source / 'silver'


def default_resolution_dir(source: str) -> Path:
    return get_package_emitter_paths().per_source_root / source / 'package_resolution'


def default_package_dir(source: str) -> Path:
    return get_package_emitter_paths().source_package_root / source
