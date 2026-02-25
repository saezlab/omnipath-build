.PHONY: setup silver silver-test silver-reprocess gold postgres meilisearch meilisearch-entities meilisearch-interactions meilisearch-associations meilisearch-import meilisearch-import-entities meilisearch-import-interactions meilisearch-import-associations meilisearch-import-all meilisearch-delete-indexes gold-meilisearch-import meilisearch-build-dump meilisearch-build-dump-start meilisearch-build-dump-stop pipeline pipeline-full generate-obo export export-entity export-ontology export-meilisearch export-finalize

# =============================================================================
# Full Pipeline - Run everything from silver to export
# =============================================================================
# Usage: make pipeline
# This runs: silver-test -> generate-obo -> gold -> meilisearch -> build-dump -> export-entity -> export-ontology -> export-finalize
# The meilisearch-build-dump step spins up a temporary container, imports data, creates dump, and cleans up.
pipeline:
	@echo "======================================================================"
	@echo "Starting full pipeline..."
	@echo "  Data version: $(DATA_VERSION)"
	@echo "======================================================================"
	@$(MAKE) silver-test
	@$(MAKE) generate-obo
	@$(MAKE) gold
	@$(MAKE) meilisearch
	@$(MAKE) meilisearch-build-dump DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-entity DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-ontology DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-finalize DATA_VERSION=$(DATA_VERSION)
	@echo ""
	@echo "======================================================================"
	@echo "✓ Full pipeline completed successfully!"
	@echo "======================================================================"

pipeline-full:
	@echo "======================================================================"
	@echo "Starting full pipeline..."
	@echo "  Data version: $(DATA_VERSION)"
	@echo "======================================================================"
	@$(MAKE) silver
	@$(MAKE) generate-obo
	@$(MAKE) gold
	@$(MAKE) meilisearch
	@$(MAKE) meilisearch-build-dump DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-entity DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-ontology DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-finalize DATA_VERSION=$(DATA_VERSION)
	@echo ""
	@echo "======================================================================"
	@echo "✓ Full pipeline completed successfully!"
	@echo "======================================================================"

# Generate OmniPath OBO file (needed for CV term label resolution in gold step) 
generate-obo:
	@echo "Generating OmniPath OBO file..."
	@mkdir -p omnipath_build/data
	@uv run python pypath/scripts/export_omnipath_obo.py omnipath_build/data/omnipath_mi.obo
	@echo "✓ OBO file generated"

setup:
	git submodule add -b download-manager-experiment https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b download-manager-experiment https://github.com/saezlab/download-manager.git download-manager || true
	git submodule add -b main https://github.com/saezlab/ontograph.git ontograph || true
	git config -f .gitmodules submodule.pypath.branch download-manager-experiment
	git config -f .gitmodules submodule.download-manager.branch download-manager-experiment
	git config -f .gitmodules submodule.ontograph.branch main
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

GOLD_STEPS := local_tables entity_identifiers global_tables
gold:
	@if [ -n "$(filter $(GOLD_STEPS),$(MAKECMDGOALS))" ]; then \
		uv run -m omnipath_build.cli.commands gold --step $(filter $(GOLD_STEPS),$(MAKECMDGOALS)); \
	else \
		uv run -m omnipath_build.cli.commands gold; \
	fi

postgres:
	@. .env && uv run -m omnipath_build.cli.commands postgres \
		--postgres-uri "postgresql://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@localhost:$${POSTGRES_PORT}/omnipath" \
		--schema public \
		$(if $(DROP),--drop-existing)

# Build search entities parquet
meilisearch-entities:
	@uv run python -m omnipath_build.search_builder.build_search_entities \
		--global-tables-dir omnipath_build/data/gold \
		--output omnipath_build/data/gold/search_entities.parquet

# Build search interactions parquet
meilisearch-interactions:
	@uv run python -m omnipath_build.search_builder.build_search_interactions \
		--global-tables-dir omnipath_build/data/gold \
		--output omnipath_build/data/gold/search_interactions.parquet

# Build search associations parquet
meilisearch-associations:
	@uv run python -m omnipath_build.search_builder.build_search_associations \
		--global-tables-dir omnipath_build/data/gold \
		--output omnipath_build/data/gold/search_associations.parquet

# Build all search parquet files (entities, interactions, associations)
meilisearch: meilisearch-entities meilisearch-interactions meilisearch-associations

# Import entities into Meilisearch
meilisearch-import-entities:
	@. .env && uv run python -m omnipath_build.search.importer \
		--dataset entities \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $${MEILISEARCH_API_KEY}

# Import interactions into Meilisearch
meilisearch-import-interactions:
	@. .env && uv run python -m omnipath_build.search.importer \
		--dataset interactions \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $${MEILISEARCH_API_KEY}

# Import associations into Meilisearch
meilisearch-import-associations:
	@. .env && uv run python -m omnipath_build.search.importer \
		--dataset associations \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $${MEILISEARCH_API_KEY}

# Import all datasets (entities, interactions, associations) into Meilisearch
meilisearch-import-all:
	@. .env && uv run python -m omnipath_build.search.importer \
		--dataset all \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $${MEILISEARCH_API_KEY}

# Backward compatibility: import entities only (original behavior)
meilisearch-import: meilisearch-import-entities

# Delete Meilisearch indexes (useful before re-importing)
meilisearch-delete-indexes:
	@echo "Deleting Meilisearch indexes..."
	@. .env && curl -s -X DELETE "http://localhost:7700/indexes/search_entities" \
		-H "Authorization: Bearer $${MEILISEARCH_API_KEY}" || true
	@. .env && curl -s -X DELETE "http://localhost:7700/indexes/search_interactions" \
		-H "Authorization: Bearer $${MEILISEARCH_API_KEY}" || true
	@. .env && curl -s -X DELETE "http://localhost:7700/indexes/search_associations" \
		-H "Authorization: Bearer $${MEILISEARCH_API_KEY}" || true
	@echo "Indexes deleted (or did not exist)"

gold-meilisearch-import:
	@$(MAKE) gold
	@$(MAKE) meilisearch
	@$(MAKE) meilisearch-import

# =============================================================================
# Data Export for Deployment
# =============================================================================

# Export all data files to a versioned data directory for deployment
# Output: data/releases/v-YYYYMMDD-HHMMSS/ with a 'latest' symlink
# The DATA_VERSION variable is set automatically from timestamp, or override with:
#   make export DATA_VERSION=v-custom-name

DATA_VERSION ?= v-$(shell date +%Y%m%d-%H%M%S)
EXPORT_DIR := data/releases/$(DATA_VERSION)

.PHONY: export export-entity export-ontology export-meilisearch export-finalize

# Full export (use when you have a running Meilisearch with data)
export: export-entity export-ontology export-meilisearch export-finalize

# Finalize export: create version marker and update 'latest' symlink
# Use this after export-entity, export-ontology, and meilisearch-build-dump
export-finalize:
	@# Create data version marker (hash of all data files)
	@echo "Creating data version marker..."
	@DUMP_NAME=$$(cat $(EXPORT_DIR)/dumps/.dump_file); \
	cat $(EXPORT_DIR)/entity_identifier.parquet \
		$(EXPORT_DIR)/omnipath_mi.obo \
		$(EXPORT_DIR)/dumps/$$DUMP_NAME 2>/dev/null | shasum -a 256 | cut -d' ' -f1 \
		> $(EXPORT_DIR)/.data_version
	@# Update 'latest' symlink
	@ln -sfn $(DATA_VERSION) data/releases/latest
	@echo ""
	@echo "✓ All data exported to $(EXPORT_DIR)/"
	@echo "  Data version: $$(cat $(EXPORT_DIR)/.data_version)"
	@echo "  Dump file: $$(cat $(EXPORT_DIR)/dumps/.dump_file)"
	@echo "  Symlink: data/releases/latest -> $(DATA_VERSION)"
	@echo ""
	@echo "Data files ready for deployment:"
	@ls -lh $(EXPORT_DIR)/*.parquet $(EXPORT_DIR)/*.obo 2>/dev/null || true
	@ls -lh $(EXPORT_DIR)/dumps/*.dump 2>/dev/null || true
	@echo ""
	@echo "Available versions:"
	@ls -d data/releases/v-* 2>/dev/null | sed 's|data/releases/||'
	@echo ""
	@echo "Promote/deploy using the separate presentation repository."

# Export entity service data
export-entity:
	@echo "Exporting entity service data to $(EXPORT_DIR)..."
	@mkdir -p $(EXPORT_DIR)
	@if [ -f omnipath_build/data/gold/entity_identifier.parquet ]; then \
		cp -v omnipath_build/data/gold/entity_identifier.parquet $(EXPORT_DIR)/; \
	else \
		echo "Error: entity_identifier.parquet not found. Run 'make gold' first."; \
		exit 1; \
	fi

# Export ontology service data (copies the generated omnipath_mi.obo)
export-ontology:
	@echo "Exporting ontology service data to $(EXPORT_DIR)..."
	@mkdir -p $(EXPORT_DIR)
	@if [ -f omnipath_build/data/omnipath_mi.obo ]; then \
		cp -v omnipath_build/data/omnipath_mi.obo $(EXPORT_DIR)/; \
	else \
		echo "Error: omnipath_mi.obo not found. Run 'make generate-obo' first."; \
		exit 1; \
	fi

# Export meilisearch dump (must have meilisearch running with data)
export-meilisearch:
	@echo "Exporting meilisearch dump to $(EXPORT_DIR)..."
	@mkdir -p $(EXPORT_DIR)/dumps
	@. .env && uv run python -m omnipath_build.scripts.create_meilisearch_dump \
		--meili-url http://localhost:7700 \
		--api-key $${MEILISEARCH_API_KEY} \
		--output-dir $(EXPORT_DIR)/dumps

# =============================================================================
# Self-contained Meilisearch Dump Builder
# =============================================================================
# Build a Meilisearch dump without requiring a running Meilisearch instance.
# This spins up a temporary container, imports data, creates dump, and cleans up.
# Prerequisites: search parquet files must exist (run 'make meilisearch' first)
TEMP_MEILI_CONTAINER := omnipath-temp-meilisearch
TEMP_MEILI_PORT := 7710
TEMP_MEILI_KEY := temp-build-key

.PHONY: meilisearch-build-dump meilisearch-build-dump-start meilisearch-build-dump-stop

meilisearch-build-dump: meilisearch-build-dump-start
	@echo ""
	@echo "======================================================================" 
	@echo "Building Meilisearch dump (self-contained)"
	@echo "======================================================================"
	@echo ""
	@# Wait for Meilisearch to be ready
	@echo "2. Waiting for Meilisearch to be ready..."
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do \
		if curl -sf http://localhost:$(TEMP_MEILI_PORT)/health > /dev/null 2>&1; then \
			echo "   Meilisearch is ready!"; \
			break; \
		fi; \
		if [ $$i -eq 20 ]; then \
			echo "   Error: Meilisearch failed to start"; \
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
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $(TEMP_MEILI_KEY) \
		--meili-url http://localhost:$(TEMP_MEILI_PORT)
	@echo ""
	@# Create dump
	@echo "4. Creating dump..."
	@mkdir -p $(EXPORT_DIR)/dumps
	@uv run python -m omnipath_build.scripts.create_meilisearch_dump \
		--meili-url http://localhost:$(TEMP_MEILI_PORT) \
		--api-key $(TEMP_MEILI_KEY) \
		--output-dir $(EXPORT_DIR)/dumps \
		--container-name $(TEMP_MEILI_CONTAINER)
	@echo ""
	@# Cleanup
	@$(MAKE) meilisearch-build-dump-stop
	@echo ""
	@echo "======================================================================"
	@echo "✓ Meilisearch dump created successfully!"
	@echo "  Output: $(EXPORT_DIR)/dumps/"
	@echo "======================================================================"

meilisearch-build-dump-start:
	@echo "1. Starting temporary Meilisearch container..."
	@docker rm -f $(TEMP_MEILI_CONTAINER) 2>/dev/null || true
	@docker run -d \
		--name $(TEMP_MEILI_CONTAINER) \
		-p $(TEMP_MEILI_PORT):7700 \
		-e MEILI_MASTER_KEY=$(TEMP_MEILI_KEY) \
		getmeili/meilisearch:v1.6

meilisearch-build-dump-stop:
	@echo "5. Cleaning up temporary container..."
	@docker rm -f $(TEMP_MEILI_CONTAINER) 2>/dev/null || true
	@echo "   Temporary container removed."

%:
	@:
