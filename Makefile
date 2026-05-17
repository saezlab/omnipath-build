.PHONY: setup resolver db-setup db-reset reset-content drop-source ingest canonicalize derive load pipeline all test

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

MAPPING_DIR ?= $(DATA_ROOT)
RESOLVER_SOURCES ?= uniprot chebi hmdb lipidmaps swisslipids pubchem
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

setup:
	git submodule add -b main https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b main https://github.com/saezlab/download-manager.git download-manager || true
	git submodule update --init --recursive --remote
	uv sync
	-uv pip uninstall pypath-omnipath dlmachine cachedir
	uv pip install -e ./cache-manager -e ./download-manager -e ./pypath

resolver:
	@echo "[omnipath_build] build-resolver output_dir=$(MAPPING_DIR) sources=$(RESOLVER_SOURCES)"
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli build-resolver \
		--output-dir "$(MAPPING_DIR)" \
		$(if $(MAX_RECORDS),--max-records $(MAX_RECORDS)) \
		$(if $(PUBCHEM_URL),--pubchem-url "$(PUBCHEM_URL)") \
		$(RESOLVER_SOURCES)

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
	echo "[omnipath_build] load-resolver mapping_dir=$(MAPPING_DIR)"; \
	PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(SCHEMA)" \
		load-resolver \
		--mapping-dir "$(MAPPING_DIR)" \
		--batch-size "$(RESOLVER_BATCH_SIZE)"; \
	if [ -z "$(DROP_EXISTING)" ]; then \
		echo "[omnipath_build] create secondary indexes schema=$(SCHEMA)"; \
		PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(SCHEMA)" \
			derive \
			--no-tables \
			--no-bitmaps; \
	else \
		echo "[omnipath_build] deferring secondary indexes until after ingest"; \
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
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make ingest SOURCES=uniprot DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@set -e; \
	if [ -z "$(SELECTED_SOURCES)" ]; then \
		echo "[omnipath_build] ingest source=ALL"; \
		PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(SCHEMA)" \
			ingest \
			--database "$(DATABASE)" \
			--inputs-package "$(INPUTS_PACKAGE)" \
			$(if $(DATASET),--dataset "$(DATASET)") \
			--batch-size "$(BATCH_SIZE)" \
			--progress-every "$(PROGRESS_EVERY)" \
			--obo-output-dir "$(OBO_DIR)" \
			--no-ensure-schema \
			$(if $(PROFILE),--profile) \
			$(if $(MAX_RECORDS),--max-records "$(MAX_RECORDS)") \
			$(if $(FORCE_REFRESH),--force-refresh); \
	else \
		SOURCE_LIST=$$(printf '%s' "$(SELECTED_SOURCES)" | tr ',' ' '); \
		for source in $$SOURCE_LIST; do \
			echo "[omnipath_build] ingest source=$$source"; \
			PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
				--database-url "$(DATABASE_URL)" \
				--schema "$(SCHEMA)" \
				ingest \
				--source "$$source" \
				--database "$(DATABASE)" \
				--inputs-package "$(INPUTS_PACKAGE)" \
				$(if $(DATASET),--dataset "$(DATASET)") \
				--batch-size "$(BATCH_SIZE)" \
				--progress-every "$(PROGRESS_EVERY)" \
				--obo-output-dir "$(OBO_DIR)" \
				--no-ensure-schema \
				$(if $(PROFILE),--profile) \
				$(if $(MAX_RECORDS),--max-records "$(MAX_RECORDS)") \
				$(if $(FORCE_REFRESH),--force-refresh); \
		done; \
	fi

canonicalize:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make canonicalize SOURCES=uniprot DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@set -e; \
	if [ -z "$(SELECTED_SOURCES)" ]; then \
		echo "[omnipath_build] canonicalize source=ALL"; \
		PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(SCHEMA)" \
			canonicalize \
			--no-ensure-schema; \
	else \
		SOURCE_LIST=$$(printf '%s' "$(SELECTED_SOURCES)" | tr ',' ' '); \
		for source in $$SOURCE_LIST; do \
			echo "[omnipath_build] canonicalize source=$$source"; \
			PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli \
				--database-url "$(DATABASE_URL)" \
				--schema "$(SCHEMA)" \
				canonicalize \
				--source "$$source" \
				--no-ensure-schema; \
		done; \
	fi

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

load: ingest canonicalize

pipeline: load
	@if [ "$(DERIVE)" != "" ]; then \
		$(MAKE) derive; \
	fi

all:
	@$(MAKE) resolver
	@$(MAKE) db-setup
	@$(MAKE) pipeline DERIVE=1

test:
	@uv run python -m compileall -q omnipath_build
