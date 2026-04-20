.PHONY: setup silver-list gold-mappings gold-source gold-all combined postgres-combined pipeline test overwrite-gold overwrite-silver overwrite

JOBS ?= 4
BATCH_SIZE ?= 10000
DATA_ROOT ?= data_v2
INPUTS_PACKAGE ?= pypath.inputs_v2
RESOLVER_MAPPING_DIR ?= id_resolver/data
OVERWRITE ?=
TEST_MODE ?=
STEP ?= all
COMBINED_GOLD_ROOT ?= $(DATA_ROOT)/gold
COMBINED_OUTPUT_DIR ?= $(DATA_ROOT)/combined
DATABASE_URL ?= postgresql://omnipath:omnipath@localhost:55432/omnipath
POSTGRES_URI ?= $(DATABASE_URL)
POSTGRES_SCHEMA ?= public
POSTGRES_DROP_EXISTING ?= 1

setup:
	git submodule add -b main https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b main https://github.com/saezlab/download-manager.git download-manager || true
	git submodule update --init --recursive --remote
	uv sync
	-uv pip uninstall pypath-omnipath dlmachine cachedir
	uv pip install -e ./cache-manager -e ./download-manager -e ./pypath

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

postgres-combined:
	@if [ -z "$(POSTGRES_URI)" ]; then \
		echo "POSTGRES_URI is required, e.g. make postgres-combined POSTGRES_URI=postgresql://user:pass@host:5432/dbname"; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.cli.commands postgres \
		--output-dir $(COMBINED_OUTPUT_DIR) \
		--postgres-uri $(POSTGRES_URI) \
		--schema $(POSTGRES_SCHEMA) \
		$(if $(POSTGRES_DROP_EXISTING),--drop-existing)

pipeline:
	@if [ "$(STEP)" = "all" ]; then \
		$(MAKE) gold-all SOURCES="$(SOURCES)" DATA_ROOT="$(DATA_ROOT)" INPUTS_PACKAGE="$(INPUTS_PACKAGE)" BATCH_SIZE="$(BATCH_SIZE)" JOBS="$(JOBS)" TEST_MODE="$(TEST_MODE)" OVERWRITE="$(OVERWRITE)" RESOLVER_MAPPING_DIR="$(RESOLVER_MAPPING_DIR)" && \
		$(MAKE) combined DATA_ROOT="$(DATA_ROOT)" COMBINED_GOLD_ROOT="$(COMBINED_GOLD_ROOT)" COMBINED_OUTPUT_DIR="$(COMBINED_OUTPUT_DIR)"; \
	elif [ "$(STEP)" = "gold" ]; then \
		$(MAKE) gold-all SOURCES="$(SOURCES)" DATA_ROOT="$(DATA_ROOT)" INPUTS_PACKAGE="$(INPUTS_PACKAGE)" BATCH_SIZE="$(BATCH_SIZE)" JOBS="$(JOBS)" TEST_MODE="$(TEST_MODE)" OVERWRITE="$(OVERWRITE)" RESOLVER_MAPPING_DIR="$(RESOLVER_MAPPING_DIR)"; \
	elif [ "$(STEP)" = "combined" ]; then \
		$(MAKE) combined DATA_ROOT="$(DATA_ROOT)" COMBINED_GOLD_ROOT="$(COMBINED_GOLD_ROOT)" COMBINED_OUTPUT_DIR="$(COMBINED_OUTPUT_DIR)"; \
	elif [ "$(STEP)" = "postgres" ]; then \
		$(MAKE) postgres-combined COMBINED_OUTPUT_DIR="$(COMBINED_OUTPUT_DIR)" POSTGRES_URI="$(POSTGRES_URI)" POSTGRES_SCHEMA="$(POSTGRES_SCHEMA)" POSTGRES_DROP_EXISTING="$(POSTGRES_DROP_EXISTING)"; \
	else \
		echo "Unknown STEP=$(STEP). Supported values: all, gold, combined, postgres"; \
		exit 1; \
	fi

test: TEST_MODE=1
test: pipeline

overwrite-gold: OVERWRITE=gold
overwrite-gold: pipeline

overwrite-silver: OVERWRITE=silver
overwrite-silver: pipeline

overwrite: OVERWRITE=both
overwrite: pipeline
