# Task: adapt DuckDB flow to resource-specific gold packages

## Goal
Make the `/resources` page able to open one or more selected resources in the in-browser DuckDB workspace using the new `omnipath_build` gold outputs, instead of the current subset/export flow built around the older search-oriented parquet endpoints.

## Context
What works now:
- `/resources` reads from `data_v2/gold/resources.parquet`
- resource downloads now resolve the current gold package for each resource via FastAPI
- single-resource and multi-resource downloads are wired through FastAPI and `omnipath-present`

What still needs work:
- the DuckDB workspace path is still designed around the current export/subset flow (`/exports/*/parquet`)
- that existing path assumes the current search/export schemas and dataset shapes
- the new resource-entry flow needs to open resource-specific gold packages directly or through a new FastAPI materialization step aligned with those packages

## Main task
Design and implement the next-step integration so a selection from `/resources` can be opened in DuckDB using the new gold resource packages.

## Required investigation
Before implementation, compare the new resource package artifacts with the current DuckDB flow.

Specifically:
1. Inspect the schema of typical gold package files for selected resources, e.g.
   - `entities.parquet`
   - `interactions.parquet`
   - `associations.parquet`
   - `annotations.parquet`
   - `entity_identifiers.parquet`
2. Compare those schemas to the parquet artifacts currently loaded by the DuckDB workspace from the existing subset/export endpoints.
3. Identify mismatches that matter for the current workspace code, including:
   - expected column names
   - expected table names/views
   - filter assumptions
   - join assumptions
   - assumptions about one global mixed dataset vs per-resource packages
4. Document which parts of the current DuckDB implementation can be reused unchanged and which need an adaptation layer.

## Expected output
Produce a short implementation plan covering:
- the FastAPI endpoint shape needed for “open resource(s) in DuckDB”
- whether the frontend should receive:
  - raw resource package files,
  - a normalized/materialized bundle,
  - or a merged parquet set tailored for the current workspace
- how multi-resource selections should be represented in DuckDB
- what schema normalization is required, if any
- whether the existing DuckDB workspace can be extended or whether a parallel resource-workspace path is cleaner initially

## Implementation direction
Prefer an approach where:
- FastAPI resolves the selected current gold package versions
- FastAPI performs any required normalization/materialization
- the frontend DuckDB layer consumes a predictable artifact contract
- old Meilisearch/search-export assumptions are not carried into the resource flow unless explicitly still needed

## Definition of done
- there is a clear schema comparison between current DuckDB inputs and new resource package artifacts
- there is a concrete endpoint/data-contract proposal for loading selected resources into DuckDB
- there is an implementation plan for frontend + FastAPI changes
- the plan is aligned with `data_v2/gold/<resource>/<version>/...` as the source of truth
