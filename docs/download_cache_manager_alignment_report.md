# Download/Cache Manager Alignment Report (Local, Pre-Push)

Date: 2026-02-27
Context: Align download/cache handling with **cache-manager philosophy** (SQLite/cache-item first), while keeping `pypath` migration path to `download-manager` + `cache-manager` main branches.

---

## Scope of this report

This document summarizes **local changes only** (nothing pushed yet) in:

- `cache-manager` (local clone at `./cache-manager`, branch `main`)
- `download-manager` (submodule at `./download-manager`, branch `main`)

Goal was to:

1. Keep archive opening compatible with `inputs_v2` (especially plain `.tar` case).
2. Add freshness support in a way that is **aligned with cache-manager’s SQLite/cache-item design**.
3. Keep progress bar improvements in download-manager.

---

## 1) Changes made in `cache-manager`

### Files changed

- `cache_manager/_open.py` (modified)
- `tests/test_open.py` (modified)
- `cache_manager/_freshness.py` (new)
- `tests/test_freshness.py` (new)

### 1.1 Opener: plain `.tar` support

#### What changed

In `cache_manager/_open.py`:

- Added `'tar'` to `ARCHIVES`.
- Updated `open_tar()` mode handling:
  - plain tar (`.tar`) uses `mode='r:'`
  - compressed tars use `mode='r:<compr>'`

#### Why

`pypath.inputs_v2.foodb` uses `ext='tar'`, and the FooDB file in our data directory is plain tar.
Without this change, plain tar may be treated incorrectly.

### 1.2 Freshness module added to cache-manager

#### What changed

Added `cache_manager/_freshness.py` with:

- `get_remote_headers(url, **kwargs)`
- `check_freshness(local_path, remote_headers, local_metadata, method='auto')`
- `metadata_from_item(item)` helper to extract freshness-relevant metadata from `CacheItem.attrs`
- method-specific checks: `etag`, `modified`, `hash`, `size`

#### Why

To keep freshness logic **inside cache-manager domain**, using cache item metadata instead of sidecar files.

### 1.3 Tests added/updated

- `tests/test_open.py`: added plain tar extraction test.
- `tests/test_freshness.py`: added basic freshness tests:
  - metadata extraction from item attrs
  - size-based freshness check

### Validation

Executed:

```bash
PYTHONPATH=cache-manager uv run --with pytest python -m pytest cache-manager/tests/test_open.py cache-manager/tests/test_freshness.py -q
```

Result:

- `10 passed`

---

## 2) Changes made in `download-manager`

### Files changed

- `download_manager/_manager.py` (modified)
- `download_manager/_downloader.py` (modified)
- `download_manager/__init__.py` (modified)
- `pyproject.toml` (modified)

### Files explicitly removed (local working tree)

These were added during an intermediate approach (sidecar metadata), then removed to align with cache-manager design:

- `download_manager/_freshness.py`
- `download_manager/_storage.py`
- `download_manager/check_freshness.py`
- `download_manager/__main__.py`

### 2.1 Freshness integration aligned to cache-manager

#### What changed

In `download_manager/_manager.py`:

- Added optional args in `download()` / `_download()`:
  - `check_freshness=False`
  - `check_method='auto'`
  - `force_download=False`
  - `keep_old=True`
- Freshness check uses:
  - `cache_manager._freshness.get_remote_headers(...)`
  - `cache_manager._freshness.metadata_from_item(item)`
  - `cache_manager._freshness.check_freshness(...)`
- If stale and `keep_old=True`, old cached file is renamed with timestamp before redownload.

#### Metadata storage strategy

No sidecar JSON files.
Metadata is stored in `CacheItem.attrs` via existing cache update flow.
In `_report_finished(...)`, download-manager now stores (in attrs):

- `resp_headers`
- `url`
- `download_method`
- `query_params` (GET) or `post_data` (POST)
- `etag` / `last_modified` (if present)
- `sha256`, `size`, `http_code`

### 2.2 Progress bar improvements kept

In `download_manager/_downloader.py`:

- Added `tqdm`-based progress display.
- Added buffered/throttled updates and proper close/flush.
- Integrated for both requests and curl downloaders.

`pyproject.toml` updated with:

- `tqdm = "*"`

### 2.3 Cleanup

In `download_manager/__init__.py`:

- Removed `from cache_manager import _log` re-export.

### Validation

Executed:

```bash
PYTHONPATH=cache-manager uv run --with pytest python -m pytest download-manager/tests/test_manager.py -q
```

Result:

- `18 passed`

---

## 3) Current design position

The current local state is intentionally:

- **cache-manager** owns freshness utilities and metadata interpretation.
- **download-manager** consumes cache-manager freshness APIs.
- metadata remains in cache DB attrs (SQLite-backed), not in parallel sidecar files.

This is aligned with cache-manager’s "single source of truth" philosophy.

---

## 4) Important local dependency note

`download-manager` now imports `cache_manager._freshness`.
So until cache-manager changes are merged/released (or available in editable local env), download-manager with these changes expects that module to exist.

Local tests were run with:

- `PYTHONPATH=cache-manager` to ensure local cache-manager module is used.

---

## 5) Next steps (after this report)

1. Keep these changes unpushed for now (as requested).
2. Switch local `pypath` submodule to `master` and adapt `pypath.share.downloads` compatibility layer for DM main + CM opener.
3. Run local `inputs_v2` smoke tests (especially FooDB, HMDB, Reactome, SIGNOR, UniProt).
4. If all green, then proceed with commit/push choreography (cache-manager first, download-manager second), check with user before pushing. 
