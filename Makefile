.PHONY: setup silver silver-list gold-mappings gold-source gold-all combined postgres pipeline test overwrite-gold overwrite-silver overwrite

JOBS ?= 4
BATCH_SIZE ?= 10000
DATA_ROOT ?= data
INPUTS_PACKAGE ?= pypath.inputs_v2
RESOLVER_MAPPING_DIR ?= id_resolver/data
OVERWRITE ?=
TEST_MODE ?=
STEP ?= all
DATABASE ?= omnipath
SOURCE ?=
FUNCTION ?=
BASE_PATH ?=
DRY_RUN ?=
SILVER_OVERRIDE ?=
COMBINED_GOLD_ROOT ?= $(DATA_ROOT)/gold
COMBINED_OUTPUT_DIR ?= $(DATA_ROOT)/combined
DATABASE_URL ?= postgresql://omnipath:omnipath@localhost:55432/omnipath
POSTGRES_URI ?= $(DATABASE_URL)
POSTGRES_SCHEMA ?= public
POSTGRES_DROP_EXISTING ?= 1
PIPELINE_SCRIPT ?= omnipath_build/pipeline/pipeline.py

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

gold-mappings:
	@uv run python -m omnipath_build.pipeline.cli mappings \
		--data-root $(DATA_ROOT) \
		--jobs 1 \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR))

gold-source:
	@uv run python -m omnipath_build.pipeline.cli source $(SOURCES) \
		--data-root $(DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		--jobs $(JOBS) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(OVERWRITE),--overwrite $(OVERWRITE)) \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR))

gold-all:
	@uv run python -m omnipath_build.pipeline.cli all $(SOURCES) \
		--data-root $(DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		--jobs $(JOBS) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(OVERWRITE),--overwrite $(OVERWRITE)) \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR))

combined:
	@uv run python -m omnipath_build.cli.commands combined \
		--gold-root $(COMBINED_GOLD_ROOT) \
		--output-dir $(COMBINED_OUTPUT_DIR)

postgres:
	@if [ -z "$(POSTGRES_URI)" ]; then \
		echo "POSTGRES_URI is required, e.g. make postgres POSTGRES_URI=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi; \
	if [ "$(STEP)" = "all" ]; then \
		STEP_ARGS=""; \
	elif [ "$(STEP)" = "tables" ]; then \
		STEP_ARGS="--no-indexes --no-bitmaps"; \
	elif [ "$(STEP)" = "indexes" ]; then \
		STEP_ARGS="--no-tables --no-bitmaps"; \
	elif [ "$(STEP)" = "bitmaps" ]; then \
		STEP_ARGS="--no-tables --no-indexes"; \
	else \
		echo "Unknown STEP=$(STEP). Supported values: all, tables, indexes, bitmaps"; \
		exit 1; \
	fi; \
	uv run python -m omnipath_build.cli.commands postgres \
		--output-dir $(COMBINED_OUTPUT_DIR) \
		--postgres-uri $(POSTGRES_URI) \
		--schema $(POSTGRES_SCHEMA) \
		$(if $(POSTGRES_DROP_EXISTING),--drop-existing) \
		$$STEP_ARGS

pipeline:
	@if [ "$(STEP)" = "all" ]; then \
		STEP_ARGS=""; \
	elif [ "$(STEP)" = "gold" ]; then \
		STEP_ARGS="--no-combine"; \
	elif [ "$(STEP)" = "combined" ]; then \
		STEP_ARGS="--no-build-mappings --no-build-sources"; \
	elif [ "$(STEP)" = "postgres" ]; then \
		STEP_ARGS="--no-build-mappings --no-build-sources --no-combine --postgres-uri $(POSTGRES_URI) --postgres-schema $(POSTGRES_SCHEMA)"; \
	else \
		echo "Unknown STEP=$(STEP). Supported values: all, gold, combined, postgres"; \
		exit 1; \
	fi; \
	if [ "$(STEP)" = "postgres" ] && [ -z "$(POSTGRES_URI)" ]; then \
		echo "POSTGRES_URI is required, e.g. make pipeline STEP=postgres POSTGRES_URI=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi; \
	uv run python $(PIPELINE_SCRIPT) $(SOURCES) \
		--data-root $(DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		--jobs $(JOBS) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(OVERWRITE),--overwrite $(OVERWRITE)) \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR)) \
		--combined-output-dir $(COMBINED_OUTPUT_DIR) \
		$$STEP_ARGS \
		$(if $(POSTGRES_DROP_EXISTING),--postgres-drop-existing)

test: TEST_MODE=1
test: pipeline

overwrite-gold: OVERWRITE=gold
overwrite-gold: pipeline

overwrite-silver: OVERWRITE=silver
overwrite-silver: pipeline

overwrite: OVERWRITE=both
overwrite: pipeline
