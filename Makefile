.PHONY: help setup setup-python setup-web native-reference test test-pypath test-postgres test-subsets test-api check check-web build sample hubs reference publish-release serving-indexes api web dev load-postgres finish-postgres build-subsets serving-build serving-up serving-status serving-logs serving-stop worker-up

export PKG_INFRA_CONFIG ?= $(CURDIR)/config/pkg_infra_quiet.yaml
SOURCE ?= signor
VERSION ?= 0.1.0
MAX_RECORDS ?= 20
DATA_ROOT ?= data
POSTGRES_SCHEMA ?= omnipath
POSTGRES_ARGS ?=
SUBSET_PRODUCTS ?= metsigdb network_views cosmos
API_PORT ?= 8085
WEB_PORT ?= 5173
COMPOSE_FILES ?= -f compose.serving.yaml
COMPOSE_ENV ?=
COMPOSE = docker compose $(if $(COMPOSE_ENV),--env-file "$(COMPOSE_ENV)") $(COMPOSE_FILES)

help:
	@echo 'setup | native-reference | sample | build | hubs | reference | publish-release | serving-indexes'
	@echo 'dev | api | web | serving-build | serving-up | serving-status | serving-logs | serving-stop'
	@echo 'load-postgres | finish-postgres | build-subsets | test | check | check-web'
	@echo 'See README.md for variables and deploy/README.md for container deployment.'

setup: setup-python setup-web
setup-python:
	git submodule update --init --recursive
	uv sync --frozen --all-packages
	$(MAKE) native-reference
native-reference:
	cargo build --release --locked --manifest-path packages/omnipath_resolver/rust/reference/Cargo.toml --features parquet-input --bin anchor-components
setup-web:
	pnpm --dir packages/omnipath_web install --frozen-lockfile

test:
	uv run --frozen pytest -m 'not integration'
test-pypath:
	uv run --frozen pytest pypath/test/test_tabular_execution.py pypath/test/test_signor_identifiers.py pypath/test/test_inputs_v2_chemical_migration.py pypath/test/test_interaction_profiles.py pypath/test/test_measurement_review_fixes.py pypath/test/test_model_rule_review_fixes.py -q
test-postgres:
	OMNIPATH_TEST_POSTGRES=1 uv run --frozen pytest packages/omnipath_postgres/tests -q
test-subsets:
	OMNIPATH_TEST_POSTGRES=1 uv run --frozen pytest packages/omnipath_postgres/tests packages/omnipath_subsets/tests -q
test-api:
	uv run --frozen pytest packages/omnipath_api/tests -q
check:
	uv run --frozen ruff check packages scripts conftest.py
	uv run --frozen ruff format --check packages scripts conftest.py
check-web:
	pnpm --dir packages/omnipath_web check
	pnpm --dir packages/omnipath_web test

# Resource processing is bounded unless explicitly overridden.
build:
	uv run --frozen omnipath-build build "$(SOURCE)" --version "$(VERSION)" --max-records $(MAX_RECORDS) --output-dir "$(DATA_ROOT)"
sample:
	uv run --frozen python scripts/migration_smoke.py --output-dir "$(DATA_ROOT)/migration-smoke" --version "$(VERSION)" --max-records $(MAX_RECORDS)
hubs: native-reference
	uv run --frozen omnipath-build export-hubs $(HUB_ARGS)
reference: native-reference
	uv run --frozen omnipath-build build-library $(REFERENCE_ARGS)
publish-release:
	@test -n "$(RELEASE_MANIFEST)" || (echo 'Set RELEASE_MANIFEST to an explicit pinned JSON file'; exit 2)
	uv run --frozen python -m omnipath_api.publish_release "$(RELEASE_MANIFEST)" --data-root "$(DATA_ROOT)"
serving-indexes:
	uv run --frozen python -m omnipath_api.serving_index --data-root "$(DATA_ROOT)" $(INDEX_ARGS)

api:
	uv run --frozen omnipath-api --host 127.0.0.1 --port $(API_PORT) --data-root "$(DATA_ROOT)"
web:
	API_SERVICE_URL=http://127.0.0.1:$(API_PORT) pnpm --dir packages/omnipath_web dev --host 127.0.0.1 --port $(WEB_PORT)
dev:
	OMNIPATH_DATA_ROOT="$(DATA_ROOT)" ./run_all.sh $(API_PORT) $(WEB_PORT)

load-postgres:
	@test -n "$(RELEASE_MANIFEST)" || (echo 'Set RELEASE_MANIFEST to a pinned JSON file or HTTPS URL'; exit 2)
	uv run --frozen omnipath-postgres "$(RELEASE_MANIFEST)" --data-root "$(DATA_ROOT)" --schema "$(POSTGRES_SCHEMA)" $(POSTGRES_ARGS)
finish-postgres:
	uv run --frozen omnipath-postgres --finish --schema "$(POSTGRES_SCHEMA)" $(POSTGRES_ARGS)
# Use the aligned PostgreSQL pipeline; durable completed phases are skipped.
build-subsets:
	uv run --frozen omnipath-postgres --finish --schema "$(POSTGRES_SCHEMA)" --products $(SUBSET_PRODUCTS)

serving-build:
	$(COMPOSE) build api web
serving-up:
	$(COMPOSE) up -d --wait api web data
serving-status:
	$(COMPOSE) ps
serving-logs:
	$(COMPOSE) logs --tail 50 api web data
serving-stop:
	$(COMPOSE) stop api web data
# Only this explicit target starts resource-job processing.
worker-up:
	$(COMPOSE) --profile build up -d --build worker
