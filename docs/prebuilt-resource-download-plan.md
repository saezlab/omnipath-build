# Plan: prebuilt resource zip downloads served by FastAPI

## Goal

Switch `/resources` single-resource downloads from **on-demand archive creation + frontend `fetch(...).blob()` download handling** to **prebuilt per-resource zip archives** generated in `omnipath_build` and served directly by the `omnipath-present` FastAPI service via normal browser downloads.

This first phase does **not** require a separate static file server. FastAPI will serve the already-built zip files.

---

## Desired end state

### Build side (`omnipath_build`)

For each built resource version, generate and keep a prebuilt zip archive in the corresponding gold version directory, e.g.:

- `data_v2/gold/<resource_id>/<version>/<resource_id>.zip`

Optionally also maintain a stable alias/copy/symlink later, but it is not required for phase 1.

Also expose archive metadata in `resources.parquet`, so the UI/API can know:

- whether a downloadable archive exists
- archive filename
- archive byte size

### Present side (`omnipath-present`)

- `GET /resources/{resource_id}/download` serves the **prebuilt zip file** with `FileResponse`
- the Next frontend uses a **normal link / browser navigation** to that endpoint
- the current JS `fetch -> blob -> object URL` path for single-resource downloads is removed
- multi-resource selection bundle download can remain dynamic for now

---

## Scope

### In scope

- Per-resource prebuilt zip generation in `omnipath_build`
- Resource index metadata updates in `resources.parquet`
- FastAPI single-resource download endpoint changes in `omnipath-present`
- Next frontend single-resource download flow changes in `omnipath-present`
- Docs/tests updates for both repos

### Out of scope for this phase

- Moving downloads to a separate static file host or object storage
- Changing multi-resource bundle downloads to prebuilt archives
- Signed URLs / CDN caching / redirect-based delivery
- Download auth/rate-limit concerns

---

# Part 1: `omnipath_build` changes

## 1. Add a resource-archive builder

### Objective

Create a zip archive for each resource gold version directory after the version's files have been materialized.

### Proposed output convention

For each resource/version:

- archive path: `gold/<resource_id>/<version>/<resource_id>.zip`

Archive contents should include the public downloadable files for that version directory.

### Recommended archive contents

Include all files in the gold version directory **except**:

- the archive file itself (`<resource_id>.zip`)
- any temporary files
- possibly non-public/internal metadata files if present later

Archive member names should likely be the bare filenames, e.g.:

- `entities.parquet`
- `interactions.parquet`
- `annotations.parquet`
- `ontology.obo`

rather than nested under `<resource_id>/...`, because the zip already represents one resource. This keeps extraction simpler.

### Work items

- Add a helper in `omnipath_build` for building a zip from a gold version directory
- Make the helper deterministic and idempotent
- Ensure rebuilds overwrite the previous zip safely
- Ensure the helper never includes the zip file itself

### Suggested module placement

One of:

- `omnipath_build/pipeline/resource_archives.py`
- or extend existing pipeline helpers under `omnipath_build/pipeline/`

---

## 2. Integrate archive building into the gold pipeline

### Objective

Run archive creation as part of the existing gold pipeline after a resource version directory has been finalized.

### Likely integration points

Inspect and update the gold DAG/build path, likely around:

- `omnipath_build/pipeline/dag.py`

Potentially also related path/version helpers if needed.

### Work items

- Find where the gold version directory for each resource is considered complete
- Insert archive generation there
- Ensure archive generation happens for every successful resource build
- Decide failure behavior:
  - preferred: archive generation failure should fail that resource build, because download output is now a first-class artifact

### Notes

If some resources are ontology-only or otherwise unusual, the archive builder should still work so long as there are files in the version directory.

---

## 3. Add archive metadata to `resources.parquet`

### Objective

Expose enough metadata so `omnipath-present` can present/archive-download resources cleanly.

### Recommended new columns

Add at least:

- `download_archive_name: string | null`
- `download_archive_size_bytes: int | null`
- `download_archive_exists: bool`

Optional but useful later:

- `download_endpoint: string | null` (probably not needed in phase 1 because endpoint shape lives in `omnipath-present`)
- `version: string | null`

### Current index builder

Current code is in:

- `omnipath_build/pipeline/resources_index.py`

### Work items

- Detect the archive file in the current gold version directory
- Add archive metadata fields to `_resource_row(...)`
- Keep existing `total_size_bytes` semantics clear

### Recommendation on size semantics

Keep both:

- `total_size_bytes`: sum of raw gold files in the version directory
- `download_archive_size_bytes`: actual zip size delivered to users

The UI should eventually use the archive size for download messaging.

---

## 4. Decide whether `total_size_bytes` should include the zip

### Important design choice

Once the zip is added to the gold version directory, current code in `resources_index.py` will likely include it in:

- `gold_files`
- `total_size_bytes`
- `last_built_at`

That may unintentionally double-count storage because the zip is itself derived from the other files.

### Recommendation

Update the resource indexing logic so that:

- `total_size_bytes` continues to represent the sum of the underlying gold artifacts
- the generated zip is **excluded** from `total_size_bytes`
- the generated zip gets its own dedicated size field: `download_archive_size_bytes`

### Work items

- adjust `_gold_files(...)` or add a separate helper to distinguish:
  - public gold artifacts
n  - generated downloadable archive
- ensure row counts/ontology counting ignore the archive

---

## 5. Update tests in `omnipath_build`

### Test areas

Add/adjust tests for:

1. archive creation helper
2. pipeline integration
3. `resources.parquet` metadata
4. exclusion of the archive from raw artifact totals/counting

### Likely existing relevant tests

- `tests/test_gold_pipeline.py`
- plus any tests around `resources_index.py`

### Suggested cases

- resource version with one file -> archive created containing that file
- resource version with multiple files -> archive created containing all expected files
- archive rebuild overwrites cleanly
- archive file is not recursively included in itself
- `resources.parquet` row reports:
  - `download_archive_exists = true`
  - correct archive filename
  - correct archive size
- `total_size_bytes` excludes the generated zip

---

## 6. Update build docs in `omnipath_build`

### Work items

Update docs describing gold outputs and resources index, likely under:

- `docs/resources_parquet_progress.md`
- `docs/schema_inventory.md`
- any pipeline/build docs referencing downloadable resource outputs

Document:

- archive naming convention
- archive location
- new `resources.parquet` columns
- intended consumption by `omnipath-present`

---

# Part 2: `omnipath-present` changes

## 7. Change FastAPI single-resource download to serve the prebuilt zip

### Objective

Replace on-demand bundling for a single resource with direct serving of the prebuilt archive from the current gold version directory.

### Current relevant files

- `../omnipath-present/api-service/api_service/main.py`
- `../omnipath-present/api-service/api_service/resource_downloads.py`

### Current behavior

`GET /resources/{resource_id}/download` currently:

- lists resource artifacts
- if one artifact exists, may return it directly
- if multiple artifacts exist, builds a temporary zip on demand

### Desired behavior

`GET /resources/{resource_id}/download` should:

- resolve the current gold version directory
- find the prebuilt archive, e.g. `<resource_id>.zip`
- return it with `FileResponse`
- never build a zip dynamically for single-resource downloads

### Work items

- add helper to resolve the prebuilt single-resource archive path
- remove/simplify current single-resource dynamic bundling logic
- preserve existing response headers where useful
- return 404 if the archive is missing

### Suggested helper API

In `resource_downloads.py`, add something like:

- `resolve_single_resource_archive(resource_id: str, gold_root: Path | None = None) -> DownloadArtifact`

or simply return a `Path` and metadata.

---

## 8. Keep multi-resource selection bundle behavior unchanged for now

### Objective

Limit this phase to single-resource downloads.

### Current behavior

- `POST /resources/download` builds a dynamic zip bundle for a list of resources

### Recommendation

Keep this endpoint and implementation for now.

### Minimal code impact

- retain `build_multi_resource_download(...)`
- only replace `build_single_resource_download(...)`

### Optional note

Later, if multi-resource downloads move to a different system, this can be revisited independently.

---

## 9. Expose new archive metadata through the `/resources` catalog

### Objective

Ensure the frontend receives archive-related metadata from the API catalog.

### Current relevant files

- `../omnipath-present/api-service/api_service/resource_catalog.py`
- `../omnipath-present/next-omnipath/src/lib/resources.ts`

### API side

If `resources.parquet` contains the new columns, `resource_catalog.py` likely needs little or no logic change beyond ensuring values pass through as expected.

### Frontend type updates

Extend `ResourceRecord` in:

- `../omnipath-present/next-omnipath/src/lib/resources.ts`

with fields such as:

- `download_archive_exists?: boolean`
- `download_archive_name?: string | null`
- `download_archive_size_bytes?: number | null`

### Why this matters

The UI can:

- disable download if no archive exists
- show actual download size
- later show filename/version info if desired

---

## 10. Remove JS blob download flow for single-resource downloads

### Objective

Let the browser handle file download natively by navigating to the FastAPI endpoint.

### Current relevant files

- `../omnipath-present/next-omnipath/src/lib/resource-downloads.ts`
- `../omnipath-present/next-omnipath/src/features/resources/page.tsx`

### Current behavior

The frontend currently:

- `fetch()`es `/api/resources/{resource_id}/download`
- reads a `Blob`
- creates an object URL
- synthesizes an `<a>` click

### Desired behavior

For single-resource download, the frontend should use:

- a normal anchor element pointing at `/api/resources/{resource_id}/download`
- or `window.location.assign(...)`

Recommendation: use a plain `<a>`-style interaction where possible.

### Work items

- remove `downloadSingleResource(resourceId)` or stop using it
- keep `downloadResourceSelection(resourceIds)` for multi-resource bundles
- update resource cards so the Download action is a normal link for successful resources
- remove now-unnecessary “Preparing...” state for single-resource download

---

## 11. Update `/resources` page UI to use archive metadata

### Objective

Align the UI with the fact that a prebuilt zip is now the downloadable unit.

### Current relevant file

- `../omnipath-present/next-omnipath/src/features/resources/page.tsx`

### Recommended UI adjustments

- Enable single-resource download when:
  - `resource.build_status === "success"`
  - and `resource.download_archive_exists === true`
- Prefer showing `download_archive_size_bytes` for download-related size text if available
- Keep `total_size_bytes` for internal snapshot/artifact summary if still useful

### Optional copy changes

Current labels like “Snapshot Size” may become ambiguous.

Possible refinements:

- `Snapshot Size`: raw artifact size (`total_size_bytes`)
- `Download Size`: zip size (`download_archive_size_bytes`)

Or replace one with the other depending on UX preference.

---

## 12. Simplify backend download logic in `resource_downloads.py`

### Objective

Make single-resource handling focused on archive resolution rather than dynamic packaging.

### Current file

- `../omnipath-present/api-service/api_service/resource_downloads.py`

### Recommended refactor

Split responsibilities more clearly:

1. resolve current gold version dir
2. resolve prebuilt archive path for a resource
3. dynamic multi-resource bundle creation

### Suggested function layout

- `resolve_resource_version_dir(...)` (already exists)
- `resolve_single_resource_archive(...)`
- `build_multi_resource_download(...)`

Potentially remove or stop using:

- `list_resource_artifacts(...)` for single-resource browser download flow
- `_single_artifact_download(...)`
- old `build_single_resource_download(...)` logic

If other code still uses these helpers, keep them only where necessary.

---

## 13. Update API docs in `omnipath-present`

### Files

- `../omnipath-present/api-service/README.md`
- any API docs / OpenAPI-facing descriptions if present

### Document changes

Clarify that:

- `GET /resources/{resource_id}/download` serves the prebuilt archive for the current gold version
- `POST /resources/download` still creates a dynamic bundle for selected resources
- `/resources` now includes archive metadata fields

---

## 14. Update tests in `omnipath-present`

### Backend tests

Add/update tests for:

- single-resource endpoint returns the prebuilt zip
- 404 when archive missing
- content disposition filename matches expected archive name
- multi-resource bundle endpoint still works as before

### Frontend tests

If frontend tests exist around the resources page, update them so that:

- single-resource download action points to the endpoint directly
- no JS blob handling is expected for single-resource download
- selection bundle flow remains unchanged

---

# Implementation order

## Recommended sequence

1. **`omnipath_build`: implement archive creation helper**
2. **`omnipath_build`: integrate archive creation into gold pipeline**
3. **`omnipath_build`: add archive metadata to `resources.parquet`**
4. **`omnipath_build`: update tests/docs**
5. **Rebuild gold outputs / regenerate `resources.parquet`**
6. **`omnipath-present`: update FastAPI single-resource endpoint to serve prebuilt zip**
7. **`omnipath-present`: extend `ResourceRecord` with archive metadata**
8. **`omnipath-present`: update resources page to use direct browser download**
9. **`omnipath-present`: remove unused single-resource blob logic**
10. **`omnipath-present`: update docs/tests**

---

# Acceptance criteria

## Build acceptance

- After a successful gold build for a resource, the current gold version directory contains:
  - normal gold artifacts
  - a prebuilt `<resource_id>.zip`
- `resources.parquet` contains archive metadata for built resources
- `total_size_bytes` does not double-count the archive

## API acceptance

- `GET /resources/{resource_id}/download` returns the prebuilt zip with `application/zip`
- No temporary zip creation occurs for single-resource downloads
- `POST /resources/download` still works for multi-resource bundles

## Frontend acceptance

- Clicking a single-resource Download action causes a normal browser download/navigation to the API endpoint
- No `fetch(...).blob()` is used for single-resource downloads
- Download button state reflects archive availability

---

# Open decisions to confirm before implementation

## 1. Archive filename convention

Recommended:

- `<resource_id>.zip`

Alternative:

- `<resource_id>_<version>.zip`

Recommendation for phase 1: keep the on-disk file in the version dir as `<resource_id>.zip`, since the version is already part of the directory path.

## 2. Archive member layout

Recommended inside zip:

- just filenames at archive root

Alternative:

- nest under `<resource_id>/...`

Recommendation: archive root only.

## 3. Missing archive behavior

Recommended:

- `GET /resources/{resource_id}/download` returns 404 if archive missing
- frontend disables download if archive metadata says not available

## 4. Multi-resource bundle behavior

Recommendation:

- no changes in this phase

---

# Summary

This phase should make the single-resource download path substantially simpler:

- `omnipath_build` produces the zip once
- `omnipath-present` FastAPI serves that file directly
- the browser downloads it natively
- the current dynamic zip creation and JS blob flow for single-resource downloads are removed

The main architectural principle is: **build artifacts in `omnipath_build`, serve them in `omnipath-present`, and keep the frontend as a plain link-based consumer for single-resource downloads.**
