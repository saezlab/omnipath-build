# Build performance tuning (Postgres memory & parallelism)

The build runs a handful of heavy one-off statements — large sorts and
aggregations for the derived tables and network views, the RDKit structure
substrate parse (omnipath-metabo), and bulk index builds. These benefit a lot
from extra working memory and intra-query parallelism.

## Why it's session-level, not global config

The Postgres instance that the build writes to is often the **same instance
that serves the web API and web app**. Build and serving have opposite needs:

- the build wants large `work_mem` / `maintenance_work_mem` and many parallel
  workers for a few statements;
- serving wants small per-connection footprints across many concurrent
  connections.

So the build sets these as **session GUCs on its own connection** (they vanish
when the build finishes) and never writes them to the global config. Keep
`shared_buffers`, the global `work_mem`, etc. modest for serving; a small bump
to `effective_cache_size` (a planner hint, no allocation) is the only global
change worth making for a shared instance.

## The knobs

Both `omnipath-build` (derive, network-views, index builds) and
`omnipath-metabo` (RDKit substrate build) read the same environment variables.
Set one to an empty string to leave that GUC at the server default.

| Environment variable | GUC | Default | Notes |
|---|---|---|---|
| `OMNIPATH_BUILD_WORK_MEM` | `work_mem` | `512MB` | Per sort/hash node; a heavy query may use a few × this across parallel workers. |
| `OMNIPATH_BUILD_MAINTENANCE_WORK_MEM` | `maintenance_work_mem` | `2GB` | Index/GiST builds; × `max_parallel_maintenance_workers`. |
| `OMNIPATH_BUILD_MAX_PARALLEL_WORKERS_PER_GATHER` | `max_parallel_workers_per_gather` | `6` (build) / `8` (substrate parse) | Capped by the server's `max_parallel_workers` / `max_worker_processes`. |
| `OMNIPATH_BUILD_MAX_PARALLEL_MAINTENANCE_WORKERS` | `max_parallel_maintenance_workers` | `4` | Parallel index builds. |

Server-side prerequisites for the parallel settings to take full effect (these
are global and need a restart, so set them on a build host or a dedicated build
instance, **not** on a small serving instance):

```
max_worker_processes = 16     # must be >= the parallel worker counts you want
max_parallel_workers = 12
```

## Memory budget / sizing

A single build connection peaks at roughly **`work_mem` × (a few parallel
nodes) + `maintenance_work_mem` × `max_parallel_maintenance_workers`** — with
the defaults, ~8–10 GB at peak. Size the defaults to your host:

- **Lab workstation (beauty):** all Postgres instances + web services run under
  one `docker.service` cgroup with a **hard `MemoryMax = 200 GB`**
  (`MemoryHigh = 150 GB` soft) — see `saez-nixos`
  `modules/services/resource-limits.nix`. One build at ~10 GB is comfortable;
  if several instances build concurrently, keep the sum well under 200 GB.
- **Smaller hosts:** lower `OMNIPATH_BUILD_WORK_MEM` (e.g. `128MB`) and
  `OMNIPATH_BUILD_MAINTENANCE_WORK_MEM` (e.g. `512MB`), and reduce the parallel
  worker counts to match available cores/RAM.

Example (smaller host):

```bash
export OMNIPATH_BUILD_WORK_MEM=128MB
export OMNIPATH_BUILD_MAINTENANCE_WORK_MEM=512MB
export OMNIPATH_BUILD_MAX_PARALLEL_WORKERS_PER_GATHER=2
export OMNIPATH_BUILD_MAX_PARALLEL_MAINTENANCE_WORKERS=2
make all DERIVE=1 DATABASE_URL=... DATA_ROOT=../data PUBCHEM_SHARDS=1
```
