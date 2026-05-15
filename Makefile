.PHONY: setup silver silver-list resolver-mappings combined postgres pipeline minimal-resolver db-setup minimal-reset-content ingest canonicalize derive load minimal_pipeline_setup minimal_pipeline minimal-all bronze-rewrite silver-rewrite gold-rewrite combined-rewrite rewrite_pipeline test

JOBS ?= 4
BATCH_SIZE ?= 10000
COMBINE_ENTITY_BATCH_SIZE ?= 50000
COMBINE_RELATION_BATCH_SIZE ?= 50000
COMBINE_MIN_PART_SIZE_MB ?= 100
DATA_ROOT ?= data
INPUTS_PACKAGE ?= pypath.inputs_v2
RESOLVER_MAPPING_DIR ?= id_resolver/data
TEST_MODE ?=
DATABASE ?= omnipath
SOURCE ?=
SOURCES ?=
source ?=
sources ?=
SELECTED_SOURCES = $(strip $(if $(SOURCES),$(SOURCES),$(if $(SOURCE),$(SOURCE),$(if $(sources),$(sources),$(source)))))
FROM ?= download
FUNCTION ?=
DATASET ?=
BASE_PATH ?=
DRY_RUN ?=
SILVER_OVERRIDE ?=
COMBINED_GOLD_ROOT ?= $(DATA_ROOT)/gold
COMBINED_OUTPUT_DIR ?= $(DATA_ROOT)/combined
AFFECTED_ENTITIES ?=
AFFECTED_RELATIONS ?=
CHANGED_SOURCE ?=
FREEZE_MONTHLY ?=
DATABASE_URL ?= postgresql://omnipath:omnipath@localhost:55432/omnipath
POSTGRES_URI ?= $(DATABASE_URL)
POSTGRES_SCHEMA ?= public
POSTGRES_DROP_EXISTING ?=
POSTGRES_BATCH_SIZE ?= 200000
POSTGRES_UNLOGGED_TABLES ?= 1
POSTGRES_FOREIGN_KEYS ?=
MINIMAL_SCHEMA ?= public
MINIMAL_MAPPING_DIR ?= $(DATA_ROOT)
MINIMAL_RESOLVER_SOURCES ?= uniprot chebi hmdb lipidmaps swisslipids pubchem
MINIMAL_BACKEND ?= bulk
MINIMAL_BATCH_SIZE ?= 50000
MINIMAL_RESOLVER_BATCH_SIZE ?= 100000
MINIMAL_COMMIT_EVERY ?= 1000
MINIMAL_PROGRESS_EVERY ?= 1000
MINIMAL_DERIVE ?=
MINIMAL_DROP_EXISTING ?=
MINIMAL_OBO_DIR ?= $(DATA_ROOT)/obo
COMBINE_RUN_DIR ?=
LOAD_POSTGRES ?=
YES ?=
STEP ?= all
MEMORY_SAMPLE_INTERVAL_SECONDS ?= 5
BRONZE_REWRITE_DATA_ROOT ?= data_rewrite
MAX_RECORDS ?=
FORCE_REFRESH ?=
GOLD_BUCKET_COUNT ?= 4096
GOLD_PART_COUNT ?= 128
COMBINED_REWRITE_PART_COUNT ?= 16
GOLD_MIN_PART_SIZE_MB ?= 200
GOLD_DUCKDB_MEMORY_LIMIT ?=
GOLD_DUCKDB_THREADS ?=
GOLD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE ?=
GOLD_DUCKDB_PARTITIONED_WRITE_MAX_OPEN_FILES ?= 16

setup:
	git submodule add -b main https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b main https://github.com/saezlab/download-manager.git download-manager || true
	git submodule update --init --recursive --remote
	uv sync
	-uv pip uninstall pypath-omnipath dlmachine cachedir
	uv pip install -e ./cache-manager -e ./download-manager -e ./pypath

silver:
	@if [ -z "$(SOURCE)" ]; then \
		echo "SOURCE is required, e.g. make silver SOURCE=uniprot [FUNCTION=all_uniprots]"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands silver \
		--database $(DATABASE) \
		--inputs-package $(INPUTS_PACKAGE) \
		--source $(SOURCE) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(BASE_PATH),--base-path $(BASE_PATH)) \
		--batch-size $(BATCH_SIZE) \
		$(if $(TEST_MODE),--test-mode) \
		$(if $(DRY_RUN),--dry-run) \
		$(if $(SILVER_OVERRIDE),--override)

silver-list:
	@uv run python -m omnipath_build.cli.commands silver --list --inputs-package $(INPUTS_PACKAGE)

resolver-mappings:
	@PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli.commands pipeline \
		--data-root $(DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--jobs 1 \
		--no-build-sources \
		--no-combine \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR))

combined:
	@uv run python -m omnipath_build.cli.commands combined \
		--gold-root $(COMBINED_GOLD_ROOT) \
		--output-dir $(COMBINED_OUTPUT_DIR) \
		--entity-batch-size $(COMBINE_ENTITY_BATCH_SIZE) \
		--relation-batch-size $(COMBINE_RELATION_BATCH_SIZE) \
		--min-part-size-mb $(COMBINE_MIN_PART_SIZE_MB) \
		$(if $(AFFECTED_ENTITIES),--affected-entities $(AFFECTED_ENTITIES)) \
		$(if $(AFFECTED_RELATIONS),--affected-relations $(AFFECTED_RELATIONS)) \
		$(if $(CHANGED_SOURCE),--changed-source $(CHANGED_SOURCE)) \
		$(if $(FREEZE_MONTHLY),--freeze-monthly)

postgres:
	@if [ -z "$(POSTGRES_URI)" ]; then \
		echo "POSTGRES_URI is required, e.g. make postgres POSTGRES_URI=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi; \
	if [ "$(STEP)" = "all" ]; then \
		STEP_ARGS=""; \
	elif [ "$(STEP)" = "tables" ]; then \
		STEP_ARGS="--no-indexes --no-bitmaps --no-views"; \
	elif [ "$(STEP)" = "indexes" ]; then \
		STEP_ARGS="--no-tables --no-bitmaps --no-views"; \
	elif [ "$(STEP)" = "bitmaps" ]; then \
		STEP_ARGS="--no-tables --no-indexes --no-views"; \
	elif [ "$(STEP)" = "views" ]; then \
		STEP_ARGS="--no-tables --no-indexes --no-bitmaps"; \
	else \
		echo "Unknown STEP=$(STEP). Supported values: all, tables, indexes, bitmaps, views"; \
		exit 1; \
	fi; \
	echo "Loading PostgreSQL schema=$(POSTGRES_SCHEMA) step=$(STEP) output=$(COMBINED_OUTPUT_DIR)"; \
	PYTHONUNBUFFERED=1 uv run python -m omnipath_build.cli.commands postgres \
		--output-dir $(COMBINED_OUTPUT_DIR) \
			--postgres-uri $(POSTGRES_URI) \
			--schema $(POSTGRES_SCHEMA) \
			--batch-size $(POSTGRES_BATCH_SIZE) \
			$(if $(COMBINE_RUN_DIR),--combine-run-dir $(COMBINE_RUN_DIR)) \
			$(if $(POSTGRES_DROP_EXISTING),--drop-existing) \
		$(if $(POSTGRES_UNLOGGED_TABLES),--unlogged-tables) \
		$(if $(POSTGRES_FOREIGN_KEYS),--foreign-keys) \
		$$STEP_ARGS

pipeline:
	@if [ "$(LOAD_POSTGRES)" != "" ] && [ -z "$(POSTGRES_URI)" ]; then \
		echo "POSTGRES_URI is required when LOAD_POSTGRES=1"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands pipeline \
		$(if $(SOURCES),--sources $(SOURCES)) \
		--from $(FROM) \
		--data-root $(DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		--combine-entity-batch-size $(COMBINE_ENTITY_BATCH_SIZE) \
		--combine-relation-batch-size $(COMBINE_RELATION_BATCH_SIZE) \
		--combine-min-part-size-mb $(COMBINE_MIN_PART_SIZE_MB) \
		--jobs $(JOBS) \
		$(if $(TEST_MODE),--test-mode) \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR)) \
		--combined-output-dir $(COMBINED_OUTPUT_DIR) \
		$(if $(LOAD_POSTGRES),--postgres-uri $(POSTGRES_URI) --postgres-schema $(POSTGRES_SCHEMA)) \
		$(if $(POSTGRES_DROP_EXISTING),--postgres-drop-existing) \
		--memory-sample-interval-seconds $(MEMORY_SAMPLE_INTERVAL_SECONDS) \
		$(if $(YES),--yes)

minimal-resolver:
	@echo "[minimal] build-resolver output_dir=$(MINIMAL_MAPPING_DIR) sources=$(MINIMAL_RESOLVER_SOURCES)"
	@PYTHONUNBUFFERED=1 uv run python -m minimal.cli build-resolver \
		--output-dir "$(MINIMAL_MAPPING_DIR)" \
		$(if $(MAX_RECORDS),--max-records $(MAX_RECORDS)) \
		$(if $(MINIMAL_PUBCHEM_URL),--pubchem-url "$(MINIMAL_PUBCHEM_URL)") \
		$(MINIMAL_RESOLVER_SOURCES)

db-setup:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make db-setup DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@set -e; \
	echo "[minimal] init-db schema=$(MINIMAL_SCHEMA)"; \
	PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(MINIMAL_SCHEMA)" \
		init-db \
		$(if $(MINIMAL_DROP_EXISTING),--drop-existing --no-indexes); \
	echo "[minimal] load-resolver mapping_dir=$(MINIMAL_MAPPING_DIR)"; \
	PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(MINIMAL_SCHEMA)" \
		load-resolver \
		--mapping-dir "$(MINIMAL_MAPPING_DIR)" \
		--batch-size "$(MINIMAL_RESOLVER_BATCH_SIZE)"; \
	if [ -z "$(MINIMAL_DROP_EXISTING)" ]; then \
		echo "[minimal] create secondary indexes schema=$(MINIMAL_SCHEMA)"; \
		PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(MINIMAL_SCHEMA)" \
			derive \
			--no-tables \
			--no-bitmaps; \
	else \
		echo "[minimal] deferring secondary indexes until after ingest"; \
	fi

minimal-reset-content:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make minimal-reset-content DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@echo "[minimal] reset content tables schema=$(MINIMAL_SCHEMA)"
	@PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(MINIMAL_SCHEMA)" \
		reset-content

ingest:
	@if [ -z "$(DATABASE_URL)" ]; then \
		echo "DATABASE_URL is required, e.g. make ingest SOURCES=uniprot DATABASE_URL=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@set -e; \
	if [ -z "$(SELECTED_SOURCES)" ]; then \
		echo "[minimal] ingest source=ALL"; \
		PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(MINIMAL_SCHEMA)" \
			ingest \
			--database "$(DATABASE)" \
			--inputs-package "$(INPUTS_PACKAGE)" \
			--backend "$(MINIMAL_BACKEND)" \
			--batch-size "$(MINIMAL_BATCH_SIZE)" \
			--commit-every "$(MINIMAL_COMMIT_EVERY)" \
			--progress-every "$(MINIMAL_PROGRESS_EVERY)" \
			--obo-output-dir "$(MINIMAL_OBO_DIR)" \
			--no-ensure-schema \
			$(if $(MAX_RECORDS),--max-records "$(MAX_RECORDS)") \
			$(if $(FORCE_REFRESH),--force-refresh); \
	else \
		SOURCE_LIST=$$(printf '%s' "$(SELECTED_SOURCES)" | tr ',' ' '); \
		for source in $$SOURCE_LIST; do \
			echo "[minimal] ingest source=$$source"; \
			PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
				--database-url "$(DATABASE_URL)" \
				--schema "$(MINIMAL_SCHEMA)" \
				ingest \
				--source "$$source" \
				--database "$(DATABASE)" \
				--inputs-package "$(INPUTS_PACKAGE)" \
				--backend "$(MINIMAL_BACKEND)" \
				--batch-size "$(MINIMAL_BATCH_SIZE)" \
				--commit-every "$(MINIMAL_COMMIT_EVERY)" \
				--progress-every "$(MINIMAL_PROGRESS_EVERY)" \
				--obo-output-dir "$(MINIMAL_OBO_DIR)" \
				--no-ensure-schema \
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
		echo "[minimal] canonicalize source=ALL"; \
		PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
			--database-url "$(DATABASE_URL)" \
			--schema "$(MINIMAL_SCHEMA)" \
			canonicalize \
			--no-ensure-schema; \
	else \
		SOURCE_LIST=$$(printf '%s' "$(SELECTED_SOURCES)" | tr ',' ' '); \
		for source in $$SOURCE_LIST; do \
			echo "[minimal] canonicalize source=$$source"; \
			PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
				--database-url "$(DATABASE_URL)" \
				--schema "$(MINIMAL_SCHEMA)" \
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
	@echo "[minimal] refresh derived tables and bitmaps schema=$(MINIMAL_SCHEMA)"
	@PYTHONUNBUFFERED=1 uv run python -m minimal.cli \
		--database-url "$(DATABASE_URL)" \
		--schema "$(MINIMAL_SCHEMA)" \
		derive

load: ingest canonicalize

minimal_pipeline_setup: db-setup

minimal_pipeline: load
	@if [ "$(MINIMAL_DERIVE)" != "" ]; then \
		$(MAKE) derive; \
	fi

minimal-all:
	@$(MAKE) minimal-resolver
	@$(MAKE) db-setup
	@$(MAKE) minimal_pipeline MINIMAL_DERIVE=1

bronze-rewrite:
	@if [ -z "$(SOURCE)" ]; then \
		echo "SOURCE is required, e.g. make bronze-rewrite SOURCE=uniprot [FUNCTION=proteins]"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands bronze-rewrite $(SOURCE) \
		--data-root $(BRONZE_REWRITE_DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(MAX_RECORDS),--max-records $(MAX_RECORDS)) \
		$(if $(FORCE_REFRESH),--force-refresh)

silver-rewrite:
	@if [ -z "$(SOURCES)$(SOURCE)" ]; then \
		echo "SOURCES or SOURCE is required, e.g. make silver-rewrite SOURCES=signor,uniprot"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands silver-rewrite \
		$(if $(SOURCES),$(SOURCES),$(SOURCE)) \
		--data-root $(BRONZE_REWRITE_DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		$(if $(FUNCTION),--function $(FUNCTION))

gold-rewrite:
	@if [ -z "$(SOURCES)$(SOURCE)" ]; then \
		echo "SOURCES or SOURCE is required, e.g. make gold-rewrite SOURCES=signor,uniprot"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands gold-rewrite \
		$(if $(SOURCES),$(SOURCES),$(SOURCE)) \
		--data-root $(BRONZE_REWRITE_DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--resolver-mapping-dir $(RESOLVER_MAPPING_DIR) \
		--bucket-count $(GOLD_BUCKET_COUNT) \
		--part-count $(GOLD_PART_COUNT) \
		--min-part-size-mb $(GOLD_MIN_PART_SIZE_MB) \
		--duckdb-partitioned-write-max-open-files $(GOLD_DUCKDB_PARTITIONED_WRITE_MAX_OPEN_FILES) \
		$(if $(GOLD_DUCKDB_MEMORY_LIMIT),--duckdb-memory-limit $(GOLD_DUCKDB_MEMORY_LIMIT)) \
		$(if $(GOLD_DUCKDB_THREADS),--duckdb-threads $(GOLD_DUCKDB_THREADS)) \
		$(if $(GOLD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE),--duckdb-max-temp-directory-size $(GOLD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE))

combined-rewrite:
	@if [ -z "$(SOURCES)$(SOURCE)" ]; then \
		echo "SOURCES or SOURCE is required, e.g. make combined-rewrite SOURCES=signor,uniprot"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands combined-rewrite \
		$(if $(SOURCES),$(SOURCES),$(SOURCE)) \
		--data-root $(BRONZE_REWRITE_DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--bucket-count $(GOLD_BUCKET_COUNT) \
		--part-count $(COMBINED_REWRITE_PART_COUNT) \
		$(if $(GOLD_DUCKDB_MEMORY_LIMIT),--duckdb-memory-limit $(GOLD_DUCKDB_MEMORY_LIMIT)) \
		$(if $(GOLD_DUCKDB_THREADS),--duckdb-threads $(GOLD_DUCKDB_THREADS)) \
		$(if $(GOLD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE),--duckdb-max-temp-directory-size $(GOLD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE))

rewrite_pipeline:
	@if [ -z "$(SOURCES)$(SOURCE)" ]; then \
		echo "SOURCES or SOURCE is required, e.g. make rewrite_pipeline SOURCES=signor,uniprot"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands rewrite-pipeline \
		$(if $(SOURCES),$(SOURCES),$(SOURCE)) \
		--data-root $(BRONZE_REWRITE_DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(MAX_RECORDS),--max-records $(MAX_RECORDS)) \
		$(if $(FORCE_REFRESH),--force-refresh) \
		--resolver-mapping-dir $(RESOLVER_MAPPING_DIR) \
		--bucket-count $(GOLD_BUCKET_COUNT) \
		--gold-part-count $(GOLD_PART_COUNT) \
		--combined-part-count $(COMBINED_REWRITE_PART_COUNT) \
		--gold-min-part-size-mb $(GOLD_MIN_PART_SIZE_MB) \
		--duckdb-partitioned-write-max-open-files $(GOLD_DUCKDB_PARTITIONED_WRITE_MAX_OPEN_FILES) \
		$(if $(GOLD_DUCKDB_MEMORY_LIMIT),--duckdb-memory-limit $(GOLD_DUCKDB_MEMORY_LIMIT)) \
		$(if $(GOLD_DUCKDB_THREADS),--duckdb-threads $(GOLD_DUCKDB_THREADS)) \
		$(if $(GOLD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE),--duckdb-max-temp-directory-size $(GOLD_DUCKDB_MAX_TEMP_DIRECTORY_SIZE)) \
		--memory-sample-interval-seconds $(MEMORY_SAMPLE_INTERVAL_SECONDS)

test: TEST_MODE=1
test: pipeline
