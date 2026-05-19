.PHONY: setup resolver ontology-artifacts db-setup db-reset reset-content drop-source ingest canonicalize derive load pipeline all test

DATA_ROOT ?= data
DATABASE ?= omnipath
DATABASE_URL ?= postgresql://omnipath:omnipath@localhost:55432/omnipath
SCHEMA ?= public
INPUTS_PACKAGE ?= pypath.inputs_v2
SOURCE ?=
SOURCES ?=
DATASET ?=
source ?=
sources ?=
SELECTED_SOURCES = $(strip $(if $(SOURCES),$(SOURCES),$(if $(SOURCE),$(SOURCE),$(if $(sources),$(sources),$(source)))))
LOAD_SOURCES = $(strip $(SELECTED_SOURCES))

MAPPING_DIR ?= $(DATA_ROOT)
RESOLVER_SOURCES ?=
BATCH_SIZE ?= 50000
RESOLVER_BATCH_SIZE ?= 100000
PROGRESS_EVERY ?= 1000
OBO_DIR ?= $(DATA_ROOT)/obo

DROP_EXISTING ?=
RESET_DROP_INDEXES ?=
DERIVE ?=
PROFILE ?=
MAX_RECORDS ?=
FORCE_REFRESH ?=
PUBCHEM_URL ?=
PUBCHEM_SHARDS ?=
OBO_ARTIFACTS ?= 1
DUCKDB_MAX_RECORDS_ARG = $(if $(MAX_RECORDS),--max-records "$(MAX_RECORDS)",--max-records 0)

setup:
	git submodule add -b main https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b main https://github.com/saezlab/download-manager.git download-manager || true
	git submodule update --init --recursive --remote
	uv sync
	-uv pip uninstall pypath-omnipath dlmachine cachedir
	uv pip install -e ./cache-manager -e ./download-manager -e ./pypath

resolver:
	@echo "[omnipath_build] build-resolver output_dir=$(MAPPING_DIR) sources=$(if $(RESOLVER_SOURCES),$(RESOLVER_SOURCES),ALL)"
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli build-resolver \
		--output-dir "$(MAPPING_DIR)" \
		$(if $(MAX_RECORDS),--max-records $(MAX_RECORDS)) \
		$(if $(PUBCHEM_URL),--pubchem-url "$(PUBCHEM_URL)") \
		$(if $(PUBCHEM_SHARDS),--pubchem-shards $(PUBCHEM_SHARDS)) \
		$(if $(FORCE_REFRESH),--no-skip-existing) \
		$(RESOLVER_SOURCES)

ontology-artifacts:
	@echo "[omnipath_build] ontology-artifacts output_dir=$(OBO_DIR) sources=$(if $(LOAD_SOURCES),$(LOAD_SOURCES),ALL)"
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.ontology_artifacts \
		--output-dir "$(OBO_DIR)" \
		--database "$(DATABASE)" \
		--inputs-package "$(INPUTS_PACKAGE)" \
		$(if $(LOAD_SOURCES),--sources "$(LOAD_SOURCES)") \
		$(if $(DATASET),--dataset "$(DATASET)") \
		$(if $(FORCE_REFRESH),--force-refresh)

db-setup:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make db-setup DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@set -e; \
	echo "[omnipath_build] init-db schema=$(SCHEMA)"; \
	PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(SCHEMA)" \
		init-db \
		$(if $(DROP_EXISTING),--drop-existing --no-indexes); \
	if [ -z "$(DROP_EXISTING)" ]; then \
		echo "[omnipath_build] create secondary indexes schema=$(SCHEMA)"; \
		PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(SCHEMA)" \
			derive \
			--no-tables \
			--no-bitmaps; \
	else \
		echo "[omnipath_build] deferring secondary indexes until after load"; \
	fi

db-reset:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make db-reset DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@echo "[omnipath_build] reset database schema=$(SCHEMA)"
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(SCHEMA)" \
		init-db \
		--drop-existing

reset-content:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make reset-content DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@echo "[omnipath_build] reset content tables schema=$(SCHEMA)"
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(SCHEMA)" \
		reset-content \
		$(if $(RESET_DROP_INDEXES),--drop-indexes)

drop-source:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make drop-source SOURCE=uniprot DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@if [ -z "$(SELECTED_SOURCES)" ]; then \
		echo "SOURCE or SOURCES is required, e.g. make drop-source SOURCE=uniprot"; \
		exit 1; \
	fi
	@set -e; \
	SOURCE_LIST=$$(printf '%s' "$(SELECTED_SOURCES)" | tr ',' ' '); \
	for source in $$SOURCE_LIST; do \
		echo "[omnipath_build] drop-source source=$$source schema=$(SCHEMA)"; \
		PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(SCHEMA)" \
			drop-source \
			--source "$$source"; \
	done

ingest:
	@echo "[omnipath_build] the Makefile supports only the DuckDB direct load path; use 'make load'"
	@exit 1

canonicalize:
	@echo "[omnipath_build] canonicalization is part of DuckDB direct load; use 'make load'"
	@exit 1

derive:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make derive DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@echo "[omnipath_build] refresh derived tables and bitmaps schema=$(SCHEMA)"
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(SCHEMA)" \
		derive

load:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make load SOURCE=uniprot DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@echo "[omnipath_build] duckdb-load sources=$(LOAD_SOURCES) schema=$(SCHEMA) batch_size=$(BATCH_SIZE)"
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.duckdb_direct_pipeline \
		--database-url "$(DATABASE_URL)" \
		--schema "$(SCHEMA)" \
		--database "$(DATABASE)" \
		--inputs-package "$(INPUTS_PACKAGE)" \
		--sources "$(LOAD_SOURCES)" \
		--resolver-dir "$(MAPPING_DIR)" \
		$(if $(DATASET),--dataset "$(DATASET)") \
		$(DUCKDB_MAX_RECORDS_ARG) \
		--batch-size "$(BATCH_SIZE)" \
		$(if $(filter 0 false no,$(OBO_ARTIFACTS)),--no-obo-artifacts,--obo-artifacts) \
		--obo-output-dir "$(OBO_DIR)" \
		$(if $(FORCE_REFRESH),--force-refresh) \
		--append

pipeline: load
	@if [ "$(DERIVE)" != "" ]; then \
		$(MAKE) derive; \
	fi

all:
	@$(MAKE) resolver
	@$(MAKE) db-setup
	@$(MAKE) pipeline

test:
	@uv run python -m compileall -q omnipath_build
