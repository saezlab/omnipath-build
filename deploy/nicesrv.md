# nicesrv serving deployment — 3 October 2026

The API, explorer and HTTPS file service run from `omnipath-build` commit
`2b04935`, including the bounded API query pool. The [current deployment report](../docs/reports/nicesrv-serving-limits-20261003.json)
records the checks, image IDs and effective limits. The [initial cutover report](../docs/reports/nicesrv-consolidated-serving-20261003.json)
preserves the earlier deployment history.

- Explorer: https://omnipath-metabo-dev.schaul.click/explore
- Files: https://data.omnipath-metabo-dev.schaul.click
- Artifact root: `/root/projects/full_parquet/data`, mounted read-only at `/data`.
- Serving source: `2b04935098e256880e83eed33eb27c6e5f82dfab`, archived under
  `/root/projects/omnipath-releases/20261003-serving-2b04935/source`.
- Maintained checkout: `/root/projects/omnipath-migration/omnipath-build`,
  branch `parquet-migration`. Pulling this checkout does not update deployed images.
- Project: `omnipath-serving-20261003-2b04935`; loopback API/web/files ports:
  `8285` / `8282` / `8280`.

Existing Parquets and serving indexes were reused in place. Latest still selects
the same 46 resource versions. No resource, reference or PostgreSQL rebuild ran.
During the initial cutover, ten public manifest/taxonomy files were made readable
by Nginx without changing their contents. This refresh changed no artifact permissions.

## Inspect or restart the current stack

Use the archived source and its environment file to keep the deployed image tags
and routing configuration explicit:

```sh
ssh -T -o BatchMode=yes -o ForwardAgent=no -o ConnectTimeout=15 nicesrv
deployment_root=/root/projects/omnipath-releases/20261003-serving-2b04935
cd "$deployment_root/source"
make serving-status COMPOSE_ENV="$deployment_root/serving.env" \
  COMPOSE_FILES='-f compose.serving.yaml -f deploy/compose.traefik.yaml'
make serving-logs COMPOSE_ENV="$deployment_root/serving.env" \
  COMPOSE_FILES='-f compose.serving.yaml -f deploy/compose.traefik.yaml'
# Restart only when needed; this starts serving and performs no data build.
make serving-up COMPOSE_ENV="$deployment_root/serving.env" \
  COMPOSE_FILES='-f compose.serving.yaml -f deploy/compose.traefik.yaml'
```

The optional worker image `omnipath-worker:consolidated-c9de08f` was built and
its help entrypoint checked. It is not processing jobs. Starting a worker remains
an explicit, separate action with a writable artifact mount.

## Effective API limits

- Container: 4 GiB RAM and 4 CPUs.
- Query pool: at most two concurrent/retained DuckDB databases.
- Each database: 1 GB DuckDB memory (953.6 MiB) and two threads.
- Capacity wait: up to 30 seconds, then HTTP 503 with a retry hint.

These limits are explicit in this deployment's `serving.env`. They apply to the
API; web and file containers retain their existing configuration. Small protein,
chemical and reaction queries, evidence retrieval and a filtered CSV export
matched the preceding API. Eight requests from four concurrent clients passed.
The API used approximately 233 MiB after these checks; this is not a load benchmark.

## Rollback

The previous `omnipath-consolidated-20261003` containers and images are retained,
stopped. Start them, then remove the new stack's public routing labels:

```sh
ssh -T -o BatchMode=yes -o ForwardAgent=no -o ConnectTimeout=15 nicesrv
docker start omnipath-consolidated-20261003-api-1 \
  omnipath-consolidated-20261003-web-1 omnipath-consolidated-20261003-data-1
deployment_root=/root/projects/omnipath-releases/20261003-serving-2b04935
cd "$deployment_root/source"
make serving-up COMPOSE_ENV="$deployment_root/serving.env" \
  COMPOSE_FILES='-f compose.serving.yaml'
curl --fail --silent --output /dev/null \
  https://omnipath-metabo-dev.schaul.click/api/health
curl --fail --silent --output /dev/null \
  https://data.omnipath-metabo-dev.schaul.click/releases/2026.9.6.4.json
```

The preceding deployment source and configuration remain at
`/root/projects/omnipath-releases/20261003-consolidated-5e46fca`.
To route back to the updated stack, use `serving-up` with the HTTPS override;
its priority 110 exceeds the preceding stack's 100. Check public requests before
stopping the preceding containers again. Keep PostgreSQL, artifacts and other apps.

## HTTPS PostgreSQL input

The maintained checkout accepts an explicit HTTPS release and artifact root:

```sh
cd /root/projects/omnipath-migration/omnipath-build
make load-postgres \
  RELEASE_MANIFEST=https://data.omnipath-metabo-dev.schaul.click/releases/2026.9.6.4.json \
  DATA_ROOT=https://data.omnipath-metabo-dev.schaul.click \
  POSTGRES_SCHEMA=an_explicit_new_schema
```

Set `OMNIPATH_DATABASE_URL` for the intended database first. This command would
perform an import; it was not run on server PostgreSQL during the cutover.
Use the normal user environment, including `HOME`, so DuckDB can load its
HTTP extension. Local DuckDB staging is temporary; no persistent Parquet download
cache is created. Full artifact hash validation also transfers file contents.

The live sample used only the existing 86 KB SIGNOR version `2026.9.5.13`.
All 15 projected table counts and row hashes, dimensions and compatibility metadata
matched local input. Projection took 0.818 s locally and 1.113 s over HTTPS.
This is a functionality check, not a full-release speed estimate.

PostgreSQL's private `2026.9.30.4` snapshot and its monthly schedule remain
independent. Its seven newer resource versions were not added to API Latest or
the file endpoint during this code cutover; publishing them is a separate step.
