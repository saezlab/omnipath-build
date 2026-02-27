.PHONY: setup silver silver-test silver-reprocess silver-local-parallel gold postgres meilisearch meilisearch-parallel meilisearch-entities meilisearch-interactions meilisearch-associations meilisearch-import meilisearch-import-entities meilisearch-import-interactions meilisearch-import-associations meilisearch-import-all meilisearch-delete-indexes gold-meilisearch-import meilisearch-build-dump meilisearch-build-dump-start meilisearch-build-dump-stop pipeline pipeline-full generate-obo export export-entity export-ontology export-search export-meilisearch export-finalize

DATA_VERSION ?= v-$(shell date +%Y%m%d-%H%M%S)
VERSION_DIR = data/$(DATA_VERSION)
BUILD_DIR = $(VERSION_DIR)/build
OUTPUT_DIR = $(VERSION_DIR)/output
BUILD_PER_SOURCE_DIR = $(BUILD_DIR)/per_source
BUILD_COMBINED_DIR = $(BUILD_DIR)/combined
COMBINED_SILVER_DIR = $(BUILD_COMBINED_DIR)/silver
COMBINED_GOLD_DIR = $(BUILD_COMBINED_DIR)/gold
COMBINED_SEARCH_DIR = $(BUILD_COMBINED_DIR)/search

export DATA_VERSION

# =============================================================================
# Full Pipeline - Run everything from silver to export
# =============================================================================
# Usage: make pipeline
# This runs: silver-local-parallel(TEST_MODE=1) -> generate-obo -> gold(entity_identifiers,global_tables)
#            -> meilisearch-parallel -> build-dump -> export-entity -> export-ontology -> export-search -> export-meilisearch -> export-finalize
# The meilisearch-build-dump step starts a temporary local Meilisearch process, imports data, creates dump, and cleans up.
pipeline:
	@echo "======================================================================"
	@echo "Starting full pipeline..."
	@echo "  Data version: $(DATA_VERSION)"
	@echo "  Jobs: $(JOBS)"
	@echo "======================================================================"
	@$(MAKE) silver-local-parallel DATA_VERSION=$(DATA_VERSION) JOBS=$(JOBS) TEST_MODE=1 $(if $(SOURCE),SOURCE=$(SOURCE)) $(if $(INPUTS_PACKAGE),INPUTS_PACKAGE=$(INPUTS_PACKAGE))
	@$(MAKE) generate-obo DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) gold DATA_VERSION=$(DATA_VERSION) entity_identifiers
	@$(MAKE) gold DATA_VERSION=$(DATA_VERSION) global_tables
	@$(MAKE) meilisearch-parallel DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) meilisearch-build-dump DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-entity DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-ontology DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-search DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-meilisearch DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-finalize DATA_VERSION=$(DATA_VERSION)
	@echo ""
	@echo "======================================================================"
	@echo "✓ Full pipeline completed successfully!"
	@echo "======================================================================"

pipeline-full:
	@echo "======================================================================"
	@echo "Starting full pipeline..."
	@echo "  Data version: $(DATA_VERSION)"
	@echo "  Jobs: $(JOBS)"
	@echo "======================================================================"
	@$(MAKE) silver-local-parallel DATA_VERSION=$(DATA_VERSION) JOBS=$(JOBS) $(if $(SOURCE),SOURCE=$(SOURCE)) $(if $(INPUTS_PACKAGE),INPUTS_PACKAGE=$(INPUTS_PACKAGE))
	@$(MAKE) generate-obo DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) gold DATA_VERSION=$(DATA_VERSION) entity_identifiers
	@$(MAKE) gold DATA_VERSION=$(DATA_VERSION) global_tables
	@$(MAKE) meilisearch-parallel DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) meilisearch-build-dump DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-entity DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-ontology DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-search DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-meilisearch DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-finalize DATA_VERSION=$(DATA_VERSION)
	@echo ""
	@echo "======================================================================"
	@echo "✓ Full pipeline completed successfully!"
	@echo "======================================================================"

# Generate OmniPath OBO file (needed for CV term label resolution in gold step) 
generate-obo:
	@echo "Generating OmniPath OBO file..."
	@mkdir -p omnipath_build/data
	@mkdir -p $(BUILD_COMBINED_DIR)
	@uv run python pypath/scripts/export_omnipath_obo.py omnipath_build/data/omnipath_mi.obo
	@cp -f omnipath_build/data/omnipath_mi.obo $(BUILD_COMBINED_DIR)/omnipath_mi.obo
	@echo "✓ OBO file generated"

setup:
	git submodule add -b download-manager-experiment https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b download-manager-experiment https://github.com/saezlab/download-manager.git download-manager || true
	git config -f .gitmodules submodule.pypath.branch download-manager-experiment
	git config -f .gitmodules submodule.download-manager.branch download-manager-experiment
	git submodule update --init --recursive --remote
	uv sync

silver:
	@uv run -m omnipath_build.cli.commands silver --base-path omnipath_build/data --database . \
		$(if $(or $(SOURCE),$(filter-out $@,$(MAKECMDGOALS))),--source $(if $(SOURCE),$(SOURCE),$(filter-out $@,$(MAKECMDGOALS)))) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

silver-test:
	@uv run -m omnipath_build.cli.commands silver --base-path omnipath_build/data --database . --test-mode \
		$(if $(or $(SOURCE),$(filter-out $@,$(MAKECMDGOALS))),--source $(if $(SOURCE),$(SOURCE),$(filter-out $@,$(MAKECMDGOALS)))) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

silver-reprocess:
	@uv run -m omnipath_build.cli.commands silver --override \
		$(if $(or $(SOURCE),$(filter-out $@,$(MAKECMDGOALS))),--source $(if $(SOURCE),$(SOURCE),$(filter-out $@,$(MAKECMDGOALS)))) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

JOBS ?= 4
silver-local-parallel:
	@mkdir -p $(BUILD_PER_SOURCE_DIR)
	@mkdir -p $(BUILD_COMBINED_DIR)
	@uv run python -m omnipath_build.scripts.parallel_build_until_local_tables \
		--jobs $(JOBS) \
		--build-dir $(BUILD_PER_SOURCE_DIR) \
		$(if $(SOURCE),--sources $(SOURCE)) \
		$(if $(TEST_MODE),--test-mode) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

GOLD_STEPS := local_tables entity_identifiers global_tables
gold:
	@mkdir -p $(COMBINED_GOLD_DIR)
	@if [ -n "$(filter $(GOLD_STEPS),$(MAKECMDGOALS))" ]; then \
		uv run -m omnipath_build.cli.commands gold --data-root $(COMBINED_SILVER_DIR) --output-dir $(COMBINED_GOLD_DIR) --local-tables-dir $(BUILD_PER_SOURCE_DIR) --step $(filter $(GOLD_STEPS),$(MAKECMDGOALS)); \
	else \
		uv run -m omnipath_build.cli.commands gold --data-root $(COMBINED_SILVER_DIR) --output-dir $(COMBINED_GOLD_DIR) --local-tables-dir $(BUILD_PER_SOURCE_DIR); \
	fi

postgres:
	@. .env && uv run -m omnipath_build.cli.commands postgres \
		--postgres-uri "postgresql://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@localhost:$${POSTGRES_PORT}/omnipath" \
		--schema public \
		$(if $(DROP),--drop-existing)

# Build search entities parquet
meilisearch-entities:
	@mkdir -p $(COMBINED_SEARCH_DIR)
	@uv run python -m omnipath_build.search_builder.build_search_entities \
		--global-tables-dir $(COMBINED_GOLD_DIR) \
		--output $(COMBINED_SEARCH_DIR)/search_entities.parquet

# Build search interactions parquet
meilisearch-interactions:
	@mkdir -p $(COMBINED_SEARCH_DIR)
	@uv run python -m omnipath_build.search_builder.build_search_interactions \
		--global-tables-dir $(COMBINED_GOLD_DIR) \
		--output $(COMBINED_SEARCH_DIR)/search_interactions.parquet

# Build search associations parquet
meilisearch-associations:
	@mkdir -p $(COMBINED_SEARCH_DIR)
	@uv run python -m omnipath_build.search_builder.build_search_associations \
		--global-tables-dir $(COMBINED_GOLD_DIR) \
		--output $(COMBINED_SEARCH_DIR)/search_associations.parquet

# Build all search parquet files (entities, interactions, associations)
meilisearch: meilisearch-entities meilisearch-interactions meilisearch-associations

# Build all search parquet files in parallel processes
meilisearch-parallel:
	@$(MAKE) -j3 meilisearch-entities meilisearch-interactions meilisearch-associations

# Import entities into Meilisearch
meilisearch-import-entities:
	@uv run python -m omnipath_build.search.importer \
		--dataset entities \
		--entities-parquet-path $(COMBINED_SEARCH_DIR)/search_entities.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key "$(TEMP_MEILI_KEY)"

# Import interactions into Meilisearch
meilisearch-import-interactions:
	@uv run python -m omnipath_build.search.importer \
		--dataset interactions \
		--interactions-parquet-path $(COMBINED_SEARCH_DIR)/search_interactions.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key "$(TEMP_MEILI_KEY)"

# Import associations into Meilisearch
meilisearch-import-associations:
	@uv run python -m omnipath_build.search.importer \
		--dataset associations \
		--associations-parquet-path $(COMBINED_SEARCH_DIR)/search_associations.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key "$(TEMP_MEILI_KEY)"

# Import all datasets (entities, interactions, associations) into Meilisearch
meilisearch-import-all:
	@uv run python -m omnipath_build.search.importer \
		--dataset all \
		--entities-parquet-path $(COMBINED_SEARCH_DIR)/search_entities.parquet \
		--interactions-parquet-path $(COMBINED_SEARCH_DIR)/search_interactions.parquet \
		--associations-parquet-path $(COMBINED_SEARCH_DIR)/search_associations.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key "$(TEMP_MEILI_KEY)"

# Backward compatibility: import entities only (original behavior)
meilisearch-import: meilisearch-import-entities

# Delete Meilisearch indexes (useful before re-importing)
meilisearch-delete-indexes:
	@echo "Deleting Meilisearch indexes..."
	@curl -s -X DELETE "http://localhost:7700/indexes/search_entities" \
		-H "Authorization: Bearer $(TEMP_MEILI_KEY)" || true
	@curl -s -X DELETE "http://localhost:7700/indexes/search_interactions" \
		-H "Authorization: Bearer $(TEMP_MEILI_KEY)" || true
	@curl -s -X DELETE "http://localhost:7700/indexes/search_associations" \
		-H "Authorization: Bearer $(TEMP_MEILI_KEY)" || true
	@echo "Indexes deleted (or did not exist)"

gold-meilisearch-import:
	@$(MAKE) gold
	@$(MAKE) meilisearch
	@$(MAKE) meilisearch-import

# =============================================================================
# Data Export for Deployment
# =============================================================================

# Export all data files to a versioned data directory for deployment
# Output: data/<DATA_VERSION>/output and data/<DATA_VERSION>/build
# The DATA_VERSION variable is set automatically from timestamp, or override with:
#   make export DATA_VERSION=v-custom-name

# Full export (use when you have a running Meilisearch with data)
export: export-entity export-ontology export-search export-meilisearch export-finalize

# Finalize export: create version marker and update 'latest' symlink
# Use this after export-entity, export-ontology, export-search, and meilisearch-build-dump
export-finalize:
	@# Create data version marker (hash of all output files)
	@echo "Creating data version marker..."
	@DUMP_NAME=$$(cat $(OUTPUT_DIR)/dumps/.dump_file); \
	cat $(OUTPUT_DIR)/entity_identifier.parquet \
		$(OUTPUT_DIR)/omnipath_mi.obo \
		$(OUTPUT_DIR)/search_entities.parquet \
		$(OUTPUT_DIR)/search_interactions.parquet \
		$(OUTPUT_DIR)/search_associations.parquet \
		$(OUTPUT_DIR)/dumps/$$DUMP_NAME 2>/dev/null | shasum -a 256 | cut -d' ' -f1 \
		> $(OUTPUT_DIR)/.data_version
	@# Update 'latest' symlink
	@ln -sfn $(DATA_VERSION) data/latest
	@echo ""
	@echo "✓ All data exported to $(OUTPUT_DIR)/"
	@echo "  Data version: $$(cat $(OUTPUT_DIR)/.data_version)"
	@echo "  Dump file: $$(cat $(OUTPUT_DIR)/dumps/.dump_file)"
	@echo "  Symlink: data/latest -> $(DATA_VERSION)"
	@echo ""
	@echo "Data files ready for deployment:"
	@ls -lh $(OUTPUT_DIR)/*.parquet $(OUTPUT_DIR)/*.obo 2>/dev/null || true
	@ls -lh $(OUTPUT_DIR)/dumps/*.dump 2>/dev/null || true
	@echo ""
	@echo "Available versions:"
	@ls -d data/v-* 2>/dev/null | sed 's|data/||'
	@echo ""
	@echo "Promote/deploy using the separate presentation repository."

# Export entity service data
export-entity:
	@echo "Exporting entity service data to $(OUTPUT_DIR)..."
	@mkdir -p $(OUTPUT_DIR)
	@if [ -f $(COMBINED_GOLD_DIR)/entity_identifier.parquet ]; then \
		cp -f $(COMBINED_GOLD_DIR)/entity_identifier.parquet $(OUTPUT_DIR)/entity_identifier.parquet; \
	else \
		echo "Error: $(COMBINED_GOLD_DIR)/entity_identifier.parquet not found. Run 'make gold' first."; \
		exit 1; \
	fi

# Export ontology service data (copies the generated omnipath_mi.obo)
export-ontology:
	@echo "Exporting ontology service data to $(OUTPUT_DIR)..."
	@mkdir -p $(OUTPUT_DIR)
	@if [ -f $(BUILD_COMBINED_DIR)/omnipath_mi.obo ]; then \
		cp -f $(BUILD_COMBINED_DIR)/omnipath_mi.obo $(OUTPUT_DIR)/omnipath_mi.obo; \
	else \
		echo "Error: $(BUILD_COMBINED_DIR)/omnipath_mi.obo not found. Run 'make generate-obo' first."; \
		exit 1; \
	fi

# Export search parquet files from build/combined/search into the versioned output directory
export-search:
	@echo "Exporting search parquet files to $(OUTPUT_DIR)..."
	@mkdir -p $(OUTPUT_DIR)
	@if [ ! -f $(COMBINED_SEARCH_DIR)/search_entities.parquet ] || [ ! -f $(COMBINED_SEARCH_DIR)/search_interactions.parquet ] || [ ! -f $(COMBINED_SEARCH_DIR)/search_associations.parquet ]; then \
		echo "Error: search parquet files missing in $(COMBINED_SEARCH_DIR). Run 'make meilisearch' first."; \
		exit 1; \
	fi
	@cp -f $(COMBINED_SEARCH_DIR)/search_entities.parquet $(OUTPUT_DIR)/search_entities.parquet
	@cp -f $(COMBINED_SEARCH_DIR)/search_interactions.parquet $(OUTPUT_DIR)/search_interactions.parquet
	@cp -f $(COMBINED_SEARCH_DIR)/search_associations.parquet $(OUTPUT_DIR)/search_associations.parquet

# Export meilisearch dump from build/combined/search into output
export-meilisearch:
	@echo "Exporting meilisearch dump to $(OUTPUT_DIR)..."
	@mkdir -p $(OUTPUT_DIR)/dumps
	@if [ ! -f $(COMBINED_SEARCH_DIR)/dumps/.dump_file ]; then \
		echo "Error: dump metadata missing in $(COMBINED_SEARCH_DIR)/dumps. Run 'make meilisearch-build-dump' first."; \
		exit 1; \
	fi
	@DUMP_NAME=$$(cat $(COMBINED_SEARCH_DIR)/dumps/.dump_file); \
	cp -f $(COMBINED_SEARCH_DIR)/dumps/.dump_file $(OUTPUT_DIR)/dumps/.dump_file; \
	cp -f $(COMBINED_SEARCH_DIR)/dumps/$$DUMP_NAME $(OUTPUT_DIR)/dumps/$$DUMP_NAME

# =============================================================================
# Self-contained Meilisearch Dump Builder
# =============================================================================
# Build a Meilisearch dump without requiring Docker.
# This starts a temporary local Meilisearch process via Nix, imports data,
# creates a dump, and cleans up.
# Prerequisites: search parquet files must exist (run 'make meilisearch' first)
TEMP_MEILI_PORT := 7710
ENV_MEILI_KEY := $(shell [ -f .env ] && awk -F= '/^MEILISEARCH_API_KEY=/{v=$$2; gsub(/^[[:space:]]*"/, "", v); gsub(/"[[:space:]]*$$/, "", v); print v; exit}' .env)
TEMP_MEILI_KEY := $(if $(ENV_MEILI_KEY),$(ENV_MEILI_KEY),temp-build-key)
TEMP_MEILI_DIR = $(COMBINED_SEARCH_DIR)/meilisearch-temp
TEMP_MEILI_DB_DIR = $(TEMP_MEILI_DIR)/db
# Write dumps directly to the canonical combined search dumps directory
TEMP_MEILI_DUMP_DIR = $(COMBINED_SEARCH_DIR)/dumps
TEMP_MEILI_PID_FILE = $(TEMP_MEILI_DIR)/meilisearch.pid
TEMP_MEILI_LOG_FILE = $(TEMP_MEILI_DIR)/meilisearch.log

.PHONY: meilisearch-build-dump meilisearch-build-dump-start meilisearch-build-dump-stop

meilisearch-build-dump: meilisearch-build-dump-start
	@echo ""
	@echo "======================================================================" 
	@echo "Building Meilisearch dump (self-contained, local process)"
	@echo "======================================================================"
	@echo ""
	@# Wait for Meilisearch to be ready
	@echo "2. Waiting for Meilisearch to be ready..."
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do \
		if curl -sf http://127.0.0.1:$(TEMP_MEILI_PORT)/health > /dev/null 2>&1; then \
			echo "   Meilisearch is ready!"; \
			break; \
		fi; \
		if [ $$i -eq 20 ]; then \
			echo "   Error: Meilisearch failed to start"; \
			echo "   See logs: $(TEMP_MEILI_LOG_FILE)"; \
			$(MAKE) meilisearch-build-dump-stop; \
			exit 1; \
		fi; \
		echo "   Waiting... ($$i/20)"; \
		sleep 1; \
	done
	@echo ""
	@# Import all datasets
	@echo "3. Importing search data..."
	@uv run python -m omnipath_build.search.importer \
		--dataset all \
		--entities-parquet-path $(COMBINED_SEARCH_DIR)/search_entities.parquet \
		--interactions-parquet-path $(COMBINED_SEARCH_DIR)/search_interactions.parquet \
		--associations-parquet-path $(COMBINED_SEARCH_DIR)/search_associations.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $(TEMP_MEILI_KEY) \
		--meili-url http://127.0.0.1:$(TEMP_MEILI_PORT)
	@echo ""
	@# Create dump
	@echo "4. Creating dump..."
	@mkdir -p $(COMBINED_SEARCH_DIR)/dumps
	@uv run python -m omnipath_build.scripts.create_meilisearch_dump \
		--meili-url http://127.0.0.1:$(TEMP_MEILI_PORT) \
		--api-key $(TEMP_MEILI_KEY) \
		--output-dir $(COMBINED_SEARCH_DIR)/dumps \
		--db-path $(TEMP_MEILI_DB_DIR) \
		--dump-dir $(TEMP_MEILI_DUMP_DIR)
	@echo ""
	@# Cleanup
	@$(MAKE) meilisearch-build-dump-stop
	@echo ""
	@echo "======================================================================"
	@echo "✓ Meilisearch dump created successfully!"
	@echo "  Output: $(COMBINED_SEARCH_DIR)/dumps/"
	@echo "======================================================================"

meilisearch-build-dump-start:
	@echo "1. Starting temporary Meilisearch local process..."
	@$(MAKE) meilisearch-build-dump-stop >/dev/null 2>&1 || true
	@rm -rf $(TEMP_MEILI_DIR)
	@mkdir -p $(TEMP_MEILI_DIR)
	@mkdir -p $(TEMP_MEILI_DB_DIR)
	@mkdir -p $(TEMP_MEILI_DUMP_DIR)
	@meilisearch \
		--http-addr 127.0.0.1:$(TEMP_MEILI_PORT) \
		--db-path $$(pwd)/$(TEMP_MEILI_DB_DIR) \
		--dump-dir $$(pwd)/$(TEMP_MEILI_DUMP_DIR) \
		--master-key "$(TEMP_MEILI_KEY)" > $(TEMP_MEILI_LOG_FILE) 2>&1 & \
	echo $$! > $(TEMP_MEILI_PID_FILE)
	@echo "   PID: $$(cat $(TEMP_MEILI_PID_FILE))"
	@echo "   DB path: $$(pwd)/$(TEMP_MEILI_DB_DIR)"
	@echo "   Log file: $(TEMP_MEILI_LOG_FILE)"

meilisearch-build-dump-stop:
	@echo "5. Stopping temporary Meilisearch process..."
	@if [ -f $(TEMP_MEILI_PID_FILE) ]; then \
		PID=$$(cat $(TEMP_MEILI_PID_FILE)); \
		if kill -0 $$PID 2>/dev/null; then \
			kill $$PID 2>/dev/null || true; \
			wait $$PID 2>/dev/null || true; \
			echo "   Stopped process $$PID."; \
		else \
			echo "   Process $$PID already stopped."; \
		fi; \
		rm -f $(TEMP_MEILI_PID_FILE); \
	else \
		echo "   No PID file found."; \
	fi

%:
	@:
