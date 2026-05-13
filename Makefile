.PHONY: setup silver silver-list resolver-mappings combined postgres pipeline bronze-rewrite silver-rewrite gold-rewrite rewrite_pipeline test

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
FROM ?= download
FUNCTION ?=
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

rewrite_pipeline:
	@if [ -z "$(SOURCES)$(SOURCE)" ]; then \
		echo "SOURCES or SOURCE is required, e.g. make rewrite_pipeline SOURCES=signor,uniprot"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands bronze-rewrite \
		$(if $(SOURCES),$(SOURCES),$(SOURCE)) \
		--data-root $(BRONZE_REWRITE_DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(MAX_RECORDS),--max-records $(MAX_RECORDS)) \
		$(if $(FORCE_REFRESH),--force-refresh)
	@uv run python -m omnipath_build.cli.commands silver-rewrite \
		$(if $(SOURCES),$(SOURCES),$(SOURCE)) \
		--data-root $(BRONZE_REWRITE_DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		$(if $(FUNCTION),--function $(FUNCTION))
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

test: TEST_MODE=1
test: pipeline
