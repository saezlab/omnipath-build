# Local pypath Main-Branch Merge + Compatibility Report (Pre-Push)

Date: 2026-02-27
Status: local only, nothing pushed.

## What was done

## 1) Merged pypath experimental work into local `master`

In `./pypath`:

- Switched from `download-manager-experiment` to `master`
- Fast-forward merged `download-manager-experiment` into `master`

Result:
- local `pypath/master` now contains the `inputs_v2` and related experimental work.

## 2) Updated pypath download compatibility for DM main + CM opener

### `pypath/pypath/share/downloads.py`

Changes:

- `DownloadManager(data_folder=...)` -> `DownloadManager(path=..., config={'backend': 'requests'})`
- `Opener` import switched from `download_manager._open` to `cache_manager._open`
- deterministic destination path enforced via:
  - `<resolved_data_dir> / subfolder / filename`
  - `dm.download(url, dest=str(file_path), **download_kwargs)`
- **default data dir is no longer hardcoded to repo `pypath-data/`**:
  - first uses env: `PYPATH_DOWNLOAD_DATADIR`
  - fallback uses pypath setting: `cachedir` (platform default for regular users)

This avoids changing pypath default behavior for external users.

### `pypath/pypath/inputs_v2/base.py`

- changed download refresh arg passed to manager from:
  - `force=force_refresh`
  - to `force_download=force_refresh`

This aligns with the updated DM manager argument naming.

### `pypath/pypath/inputs/input_module.py`

- removed import of non-existent `DownloadManagerExtended`
- switched construction to plain `DownloadManager(...)`

This prevents import/runtime breakage with DM main.

### `omnipath_build/loaders/silver.py`

Added project-local runtime configuration:

- `_configure_pypath_download_dir()`
  - if `PYPATH_DOWNLOAD_DATADIR` is not already set, sets it to
    `<project_root>/pypath-data`
  - ensures directory exists
- called at start of `discover_resources(...)` before importing `pypath.inputs_v2`

This makes `omnipath_build` own the project-local cache/download location,
without forcing the same default on all pypath users.

## 3) Updated submodule branch intents

In top-level `.gitmodules`:

- `pypath` branch changed to `master`
- `download-manager` branch changed to `main`

## 4) Local smoke tests run

Because cache-manager freshness changes are local (not published yet), tests were run with:

```bash
PYTHONPATH=cache-manager ...
```

### Passed checks

- `pypath.inputs_v2.base` import
- `pypath.share.downloads` import and manager init
- `pypath.inputs_v2.foodb` first entity generation (verifies plain `.tar` opener path)
- `omnipath_build.loaders.silver.discover_resources('tmpdb')` discovery
  - discovered expected resources (e.g. bindingdb, corum, foodb, guidetopharma, hmdb)

### Observed/expected caveat

Without `PYTHONPATH=cache-manager`, import currently fails because local `download-manager` now imports `cache_manager._freshness`, which is only present in the local modified cache-manager checkout.

This will resolve once cache-manager changes are merged/released and environment dependencies are refreshed.

---

## Current local state summary

- `pypath`: on `master`, with compatibility edits applied
- `download-manager`: local unpushed edits (progress + freshness integration using cache-manager metadata)
- `cache-manager`: local unpushed edits (plain tar opener + freshness module/tests)
- no pushes performed
