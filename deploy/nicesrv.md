# nicesrv serving deployment — 8 October 2026

The API, explorer and HTTPS file service follow branch `parquet-migration`:
`deploy.sh` checks out the branch head, builds images tagged with its short
commit and restarts the stack. The deployed commit is recorded in
`source-commit.txt`.

- Explorer: https://omnipath-metabo-dev.schaul.click/explore
- Files: https://data.omnipath-metabo-dev.schaul.click
- Deployment root: `/root/projects/omnipath-releases/20261007-serving-tables`
  (`deploy.sh`, `serving.env`, `source-commit.txt` and one log per deploy).
- Source checkout used by `deploy.sh`: `/root/projects/omnipath-migration/wt-normalized`
  (detached at `origin/parquet-migration`).
- Artifact root: `/root/data/release-20261007-native` (release `2026.10.9`, all 46
  resources), mounted read-only at `/data`.
- Project: `omnipath-serving-20261007-full`; loopback API/web/files ports:
  `8295` / `8292` / `8290`.
- Maintained checkout for builds and loads: `/root/projects/omnipath-migration/omnipath-build`.

## Deploy, inspect or restart

```sh
ssh -T -o BatchMode=yes -o ForwardAgent=no -o ConnectTimeout=15 nicesrv
deployment_root=/root/projects/omnipath-releases/20261007-serving-tables
# Deploy the current head of parquet-migration (push first).
"$deployment_root/deploy.sh"
# Inspect the running stack.
cd /root/projects/omnipath-migration/wt-normalized
make serving-status COMPOSE_ENV="$deployment_root/serving.env" \
  COMPOSE_FILES='-f compose.serving.yaml -f deploy/compose.traefik.yaml'
make serving-logs COMPOSE_ENV="$deployment_root/serving.env" \
  COMPOSE_FILES='-f compose.serving.yaml -f deploy/compose.traefik.yaml'
```

A deploy builds and starts serving only; it runs no resource build, identity
build or PostgreSQL load. Changing the served data means changing `DATA_DIR` in
`serving.env`.

## Effective API limits

Set in `serving.env`:

- Container: 6 GB RAM and 4 CPUs.
- Query pool: two concurrent DuckDB queries, each with two threads and 2,500 MB.
- Capacity wait: up to 30 seconds, then HTTP 503 with a retry hint.
- Cache warming at startup (`API_WARM_CACHE=true`): examples and relation filter
  counts are prepared before the first request.

`GET /api/status` reports the query slots in use and waiting, recent response
times and coarse host load; the explorer header shows it.

## Rollback

Every deployed commit keeps its images (`omnipath-api:serving-<commit>`,
`omnipath-web:serving-<commit>`). To roll back, set `API_IMAGE` and `WEB_IMAGE`
in `serving.env` to an earlier tag and run `make serving-up` with the same
`COMPOSE_ENV` and `COMPOSE_FILES` as above. Running `deploy.sh` again returns to
the branch head.

Backups of the configuration before the switch to the normalized tables are
`deploy.sh.bak-normalized-tables` and `serving.env.bak-tables`. Earlier
deployment roots remain under `/root/projects/omnipath-releases/`. Keep
PostgreSQL, artifacts and other apps.

## HTTPS PostgreSQL input

The data host serves releases and tables with byte ranges, so PostgreSQL can
load a release without copying files:

```sh
make load-postgres \
  RELEASE_MANIFEST=https://data.omnipath-metabo-dev.schaul.click/releases/2026.10.9.json \
  DATA_ROOT=https://data.omnipath-metabo-dev.schaul.click \
  POSTGRES_SCHEMA=an_explicit_new_schema
```

Set `OMNIPATH_DATABASE_URL` for the intended database first. Use the normal user
environment, including `HOME`, so DuckDB can load its HTTP extension. Local
DuckDB staging is temporary; no persistent Parquet download cache is created.
Release `2026.10.9` was loaded this way into dev5 on beauty in 1 h 47 min.
