from __future__ import annotations

import os
from pathlib import Path

RESOLVER_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = RESOLVER_ROOT.parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PACKAGE_ROOT / 'data'
RAW_DATA_DIR = PROJECT_ROOT / 'pypath-data'
PROTEINS_DATA_DIR = DATA_DIR / 'proteins'
CHEMICALS_DATA_DIR = DATA_DIR / 'chemicals'


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def ensure_raw_data_dir() -> Path:
    ensure_data_dir()
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_DATA_DIR


def activate_raw_download_data_dir() -> Path:
    raw_dir = ensure_raw_data_dir()
    os.environ['PYPATH_DOWNLOAD_DATADIR'] = str(raw_dir)
    return raw_dir


def ensure_proteins_data_dir() -> Path:
    ensure_data_dir()
    PROTEINS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return PROTEINS_DATA_DIR


def ensure_chemicals_data_dir() -> Path:
    ensure_data_dir()
    CHEMICALS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return CHEMICALS_DATA_DIR
