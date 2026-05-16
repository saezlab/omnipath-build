# omnipath-build

A general database builder on top of pypath.

## Setup

```bash
make setup
```

This project uses `uv` for dependency management.

## Minimal PostgreSQL Pipeline

The minimal pipeline writes `inputs_v2` sources directly to PostgreSQL evidence
tables, then canonicalizes them into entity and relation tables.

The default database URL is:

```bash
postgresql://omnipath:omnipath@localhost:55432/omnipath
```

Override it with `DATABASE_URL=...` when needed.

### Build Resolver Files

Build local resolver parquet files:

```bash
make minimal-resolver
```

Limit resolver builds for smoke tests:

```bash
make minimal-resolver MAX_RECORDS=100000
```

Use a single PubChem SDF shard during development:

```bash
make minimal-resolver MINIMAL_PUBCHEM_URL=https://example.org/pubchem.sdf.gz
```

### Prepare Database

Create schema, load resolver tables, and create indexes:

```bash
make db-setup
```

Start from a clean schema. This defers secondary evidence indexes until
canonicalization, so ingest is faster:

```bash
make db-setup MINIMAL_DROP_EXISTING=1
```

Use another schema:

```bash
make db-setup MINIMAL_SCHEMA=minimal_test MINIMAL_DROP_EXISTING=1
```

### Ingest And Canonicalize

Ingest all sources and canonicalize them:

```bash
make load
```

Ingest and canonicalize one source:

```bash
make load SOURCE=bindingdb
```

Ingest and canonicalize multiple sources:

```bash
make load SOURCES=uniprot,bindingdb,intact
```

Run phases separately:

```bash
make ingest SOURCE=bindingdb
make canonicalize SOURCE=bindingdb
make derive
```

`canonicalize` creates any deferred evidence indexes first if they are missing.
Run `make derive` after loading the selected sources to refresh query indexes,
derived count/search tables, and bitmaps.

### Full Scratch Build

Build resolver files, recreate the database schema, load all sources,
canonicalize, and derive query tables:

```bash
make minimal-all MINIMAL_DROP_EXISTING=1
```

If resolver files already exist, run:

```bash
make db-setup MINIMAL_DROP_EXISTING=1
make load
make derive
```

### Test Runs

Limit source rows per dataset:

```bash
make load SOURCE=bindingdb MAX_RECORDS=200000 MINIMAL_SCHEMA=minimal_test
```

Ingest only, without canonicalization:

```bash
make ingest SOURCE=bindingdb MAX_RECORDS=200000 MINIMAL_SCHEMA=minimal_test
```

Use one final bulk flush instead of 50k-row chunks:

```bash
make ingest SOURCE=bindingdb MAX_RECORDS=200000 MINIMAL_BATCH_SIZE=0
```

The default chunk size is `MINIMAL_BATCH_SIZE=50000`, which is safer for full
loads.

### Maintenance

Reset minimal content tables without dropping resolver tables:

```bash
make minimal-reset-content
```

Run the Python tests:

```bash
uv run pytest tests -q
```

Check table sizes when PostgreSQL runs in Docker:

```bash
docker exec -i omnipathv2-main-mmvxvb-omnipathv2-postgres-1 \
  psql -U omnipath -d omnipath -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  table_name,
  pg_size_pretty(pg_total_relation_size(format('%I.%I', table_schema, table_name)::regclass)) AS total_size,
  pg_size_pretty(pg_relation_size(format('%I.%I', table_schema, table_name)::regclass)) AS table_size,
  pg_size_pretty(pg_indexes_size(format('%I.%I', table_schema, table_name)::regclass)) AS indexes_size
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY pg_total_relation_size(format('%I.%I', table_schema, table_name)::regclass) DESC;
SQL
```
