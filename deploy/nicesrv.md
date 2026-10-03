# nicesrv serving deployment — 3 October 2026

The API, explorer and HTTPS file service now run from consolidated `omnipath-build`
images. The [deployment report](../docs/reports/nicesrv-consolidated-serving-20261003.json)
records the checks, image IDs and exact resource versions.

- Explorer: https://omnipath-metabo-dev.schaul.click/explore
- Files: https://data.omnipath-metabo-dev.schaul.click
- Artifact root: `/root/projects/full_parquet/data`, mounted read-only at `/data`.
- Serving source: `5e46fcafd4173ee48921e3964154fa3051ecf8c4`, archived under
  `/root/projects/omnipath-releases/20261003-consolidated-5e46fca/source`.
- Maintained checkout: `/root/projects/omnipath-migration/omnipath-build`,
  branch `parquet-migration`. Later worker/docs commits do not change these serving images.
- Project: `omnipath-consolidated-20261003`; loopback API/web/files ports:
  `8185` / `8182` / `8180`.

Existing Parquets and serving indexes were reused in place. Latest still selects
the same 46 resource versions. No resource, reference or PostgreSQL rebuild ran.
Ten existing public manifest/taxonomy files were made readable by Nginx without
changing their contents; the publishers now set the correct permissions.

## Inspect or restart the current stack

Use the archived source and its environment file to keep the deployed image tags
and routing configuration explicit:

```sh
ssh -T -o BatchMode=yes -o ForwardAgent=no -o ConnectTimeout=15 nicesrv
deployment_root=/root/projects/omnipath-releases/20261003-consolidated-5e46fca
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

## Rollback

The three previous serving containers and their images remain available, stopped.
From the deployment directory above:

```sh
docker start full_parquet-api-1 full_parquet-web-1 full_parquet-data-1
# Recreate the new stack without its public routing labels.
make serving-up COMPOSE_ENV="$deployment_root/serving.env" \
  COMPOSE_FILES='-f compose.serving.yaml'
curl --fail --silent --output /dev/null \
  https://omnipath-metabo-dev.schaul.click/api/health
curl --fail --silent --output /dev/null \
  https://data.omnipath-metabo-dev.schaul.click/releases/2026.9.6.4.json
```

To route back to the consolidated stack, use `serving-up` with the HTTPS
override, confirm public requests, then stop only the three old serving containers.
Keep the artifact directory, PostgreSQL containers, volumes and other applications.

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
