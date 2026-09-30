.PHONY: setup test test-pypath test-postgres check build sample load-postgres test-subsets build-subsets test-api api web

export PKG_INFRA_CONFIG ?= $(CURDIR)/config/pkg_infra_quiet.yaml

SOURCE ?= signor
VERSION ?= 0.1.0
MAX_RECORDS ?= 20
DATA_ROOT ?= data

setup:
	git submodule update --init --recursive
	uv sync --frozen --all-packages

test:
	uv run --frozen pytest -m 'not integration'

test-pypath:
	uv run --frozen pytest pypath/test/test_tabular_execution.py pypath/test/test_signor_identifiers.py pypath/test/test_inputs_v2_chemical_migration.py pypath/test/test_interaction_profiles.py pypath/test/test_measurement_review_fixes.py pypath/test/test_model_rule_review_fixes.py -q

test-postgres:
	OMNIPATH_TEST_POSTGRES=1 uv run --frozen pytest packages/omnipath_postgres/tests -q

check:
	uv run --frozen ruff check packages scripts conftest.py
	uv run --frozen ruff format --check packages scripts conftest.py

build:
	uv run --frozen omnipath-build build $(SOURCE) --version $(VERSION) --max-records $(MAX_RECORDS) --output-dir $(DATA_ROOT)

sample:
	uv run --frozen python scripts/migration_smoke.py --output-dir $(DATA_ROOT)/migration-smoke --version $(VERSION) --max-records $(MAX_RECORDS)

# RELEASE_MANIFEST and OMNIPATH_DATABASE_URL select an explicit input and database.
POSTGRES_SCHEMA ?= omnipath
load-postgres:
	uv run --frozen omnipath-postgres $(RELEASE_MANIFEST) --data-root $(DATA_ROOT) --schema $(POSTGRES_SCHEMA)

# Build products against one loaded, immutable release.
SUBSET_PRODUCTS ?= metsigdb network_views cosmos
build-subsets:
	uv run --frozen omnipath-subsets build --schema $(POSTGRES_SCHEMA) --products $(SUBSET_PRODUCTS)

test-subsets:
	OMNIPATH_TEST_POSTGRES=1 uv run --frozen pytest packages/omnipath_postgres/tests packages/omnipath_subsets/tests -q

# Parquet serving is independent of PostgreSQL snapshot release scheduling.
API_PORT ?= 8085
WEB_PORT ?= 5173
api:
	uv run --frozen omnipath-api --host 127.0.0.1 --port $(API_PORT) --data-root $(DATA_ROOT)

web:
	API_SERVICE_URL=http://127.0.0.1:$(API_PORT) pnpm --dir packages/omnipath_web dev --host 127.0.0.1 --port $(WEB_PORT)

test-api:
	uv run --frozen pytest packages/omnipath_api/tests -q
