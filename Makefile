.PHONY: setup silver silver-test silver-reprocess silver-local-parallel gold local_tables entity_identifiers global_tables postgres meilisearch meilisearch-parallel meilisearch-entities meilisearch-interactions meilisearch-associations meilisearch-sources meilisearch-import meilisearch-import-entities meilisearch-import-interactions meilisearch-import-associations meilisearch-import-sources meilisearch-import-all meilisearch-deploy meilisearch-delete-indexes meilisearch-reset gold-meilisearch-import meilisearch-build-dump meilisearch-build-dump-start meilisearch-build-dump-stop pipeline pipeline-full generate-obo export export-entity export-ontology export-search export-meilisearch export-finalize

# Load local environment defaults (e.g. MEILISEARCH_API_KEY) when available.
ifneq (,$(wildcard .env))
include .env
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env)
endif

# Default to the latest data version when available; otherwise create a new timestamped one.
LATEST_DATA_VERSION := $(shell if [ -L data/latest ]; then readlink data/latest; fi)
DATA_VERSION ?= $(if $(LATEST_DATA_VERSION),$(LATEST_DATA_VERSION),v-$(shell date +%Y%m%d-%H%M%S))
VERSION_DIR = data/$(DATA_VERSION)
BUILD_DIR = $(VERSION_DIR)/build
OUTPUT_DIR = $(VERSION_DIR)/output
BUILD_PER_SOURCE_DIR = $(BUILD_DIR)/per_source
BUILD_COMBINED_DIR = $(BUILD_DIR)/combined
COMBINED_SILVER_DIR = $(BUILD_COMBINED_DIR)/silver
COMBINED_GOLD_DIR = $(BUILD_COMBINED_DIR)/gold
COMBINED_SEARCH_DIR = $(BUILD_COMBINED_DIR)/search

# Direct Meilisearch deployment target (used by importer targets).
MEILI_URL ?= http://localhost:7700
strip_quotes = $(patsubst "%",%,$(1))
MEILI_API_KEY ?= $(call strip_quotes,$(if $(MEILISEARCH_API_KEY),$(MEILISEARCH_API_KEY),$(TEMP_MEILI_KEY)))
# Optional previous data version for local parquet-based incremental diff.
# If omitted, importer auto-infers previous from data/v-* layout.
PREVIOUS_DATA_VERSION ?=

export DATA_VERSION

# =============================================================================
# Full Pipeline - Run everything from silver to export
# =============================================================================
# Usage: make pipeline
# This runs: silver-local-parallel(TEST_MODE=1) -> generate-obo -> gold(entity_identifiers,global_tables)
#            -> meilisearch-parallel -> export-entity -> export-ontology -> export-search -> export-finalize
# Meilisearch is now populated directly from omnipath_build via meilisearch-import-* targets.
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
	@$(MAKE) export-entity DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-ontology DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-search DATA_VERSION=$(DATA_VERSION)
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
	@$(MAKE) export-entity DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-ontology DATA_VERSION=$(DATA_VERSION)
	@$(MAKE) export-search DATA_VERSION=$(DATA_VERSION)
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

# Step aliases for `make gold <step>` and recursive calls from `pipeline`.
local_tables entity_identifiers global_tables:
	@:

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

# Build search sources parquet (source provenance for search/indexing)
meilisearch-sources:
	@mkdir -p $(COMBINED_SEARCH_DIR)
	@uv run python -m omnipath_build.search_builder.build_sources \
		--per-source-root $(BUILD_PER_SOURCE_DIR) \
		--output $(COMBINED_SEARCH_DIR)/search_sources.parquet

# Build all search parquet files
meilisearch: meilisearch-entities meilisearch-interactions meilisearch-associations meilisearch-sources

# Build all search parquet files in parallel processes
meilisearch-parallel:
	@$(MAKE) -j4 meilisearch-entities meilisearch-interactions meilisearch-associations meilisearch-sources

# Import entities into Meilisearch
meilisearch-import-entities:
	@uv run python -m omnipath_build.search.importer \
		--dataset entities \
		--entities-parquet-path $(COMBINED_SEARCH_DIR)/search_entities.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--meili-url "$(MEILI_URL)" \
		--api-key "$(MEILI_API_KEY)" \
		$(if $(FULL_REINDEX),--full-reindex)

# Import interactions into Meilisearch
meilisearch-import-interactions:
	@uv run python -m omnipath_build.search.importer \
		--dataset interactions \
		--interactions-parquet-path $(COMBINED_SEARCH_DIR)/search_interactions.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--meili-url "$(MEILI_URL)" \
		--api-key "$(MEILI_API_KEY)" \
		$(if $(PREVIOUS_DATA_VERSION),--previous-interactions-parquet-path data/$(PREVIOUS_DATA_VERSION)/build/combined/search/search_interactions.parquet) \
		$(if $(FULL_REINDEX),--full-reindex)

# Import associations into Meilisearch
meilisearch-import-associations:
	@uv run python -m omnipath_build.search.importer \
		--dataset associations \
		--associations-parquet-path $(COMBINED_SEARCH_DIR)/search_associations.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--meili-url "$(MEILI_URL)" \
		--api-key "$(MEILI_API_KEY)" \
		$(if $(FULL_REINDEX),--full-reindex)

# Import sources into Meilisearch
meilisearch-import-sources:
	@uv run python -m omnipath_build.search.importer \
		--dataset sources \
		--sources-parquet-path $(COMBINED_SEARCH_DIR)/search_sources.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--meili-url "$(MEILI_URL)" \
		--api-key "$(MEILI_API_KEY)" \
		$(if $(FULL_REINDEX),--full-reindex)

# Import all datasets into Meilisearch
meilisearch-import-all:
	@uv run python -m omnipath_build.search.importer \
		--dataset all \
		--entities-parquet-path $(COMBINED_SEARCH_DIR)/search_entities.parquet \
		--interactions-parquet-path $(COMBINED_SEARCH_DIR)/search_interactions.parquet \
		--associations-parquet-path $(COMBINED_SEARCH_DIR)/search_associations.parquet \
		--sources-parquet-path $(COMBINED_SEARCH_DIR)/search_sources.parquet \
		--importer-path omnipath_build/meilisearch-importer \
		--meili-url "$(MEILI_URL)" \
		--api-key "$(MEILI_API_KEY)" \
		$(if $(PREVIOUS_DATA_VERSION),--previous-entities-parquet-path data/$(PREVIOUS_DATA_VERSION)/build/combined/search/search_entities.parquet) \
		$(if $(PREVIOUS_DATA_VERSION),--previous-interactions-parquet-path data/$(PREVIOUS_DATA_VERSION)/build/combined/search/search_interactions.parquet) \
		$(if $(PREVIOUS_DATA_VERSION),--previous-associations-parquet-path data/$(PREVIOUS_DATA_VERSION)/build/combined/search/search_associations.parquet) \
		$(if $(PREVIOUS_DATA_VERSION),--previous-sources-parquet-path data/$(PREVIOUS_DATA_VERSION)/build/combined/search/search_sources.parquet) \
		$(if $(FULL_REINDEX),--full-reindex)

# Build search docs and directly deploy to Meilisearch
meilisearch-deploy: meilisearch-parallel meilisearch-import-all

# Backward compatibility: import entities only (original behavior)
meilisearch-import: meilisearch-import-entities

# Delete Meilisearch indexes (useful before re-importing)
meilisearch-delete-indexes:
	@echo "Deleting Meilisearch indexes on $(MEILI_URL)..."
	@curl -s -X DELETE "$(MEILI_URL)/indexes/search_entities" \
		-H "Authorization: Bearer $(MEILI_API_KEY)" || true
	@curl -s -X DELETE "$(MEILI_URL)/indexes/search_interactions" \
		-H "Authorization: Bearer $(MEILI_API_KEY)" || true
	@curl -s -X DELETE "$(MEILI_URL)/indexes/search_associations" \
		-H "Authorization: Bearer $(MEILI_API_KEY)" || true
	@curl -s -X DELETE "$(MEILI_URL)/indexes/search_sources" \
		-H "Authorization: Bearer $(MEILI_API_KEY)" || true
	@echo "Indexes deleted (or did not exist)"

# Fully reset Meilisearch by removing every index (not just OmniPath indexes)
# and clearing the task queue (best effort, endpoint availability depends on version).
meilisearch-reset:
	@echo "Fully resetting Meilisearch on $(MEILI_URL)..."
	@set -e; \
	INDEX_JSON=$$(curl -s "$(MEILI_URL)/indexes?limit=1000" -H "Authorization: Bearer $(MEILI_API_KEY)"); \
	INDEX_UIDS=$$(printf '%s' "$$INDEX_JSON" | grep -oE '"uid":"[^"]+"' | cut -d '"' -f4); \
	if [ -z "$$INDEX_UIDS" ]; then \
		echo "No indexes found."; \
	else \
		echo "Deleting all indexes..."; \
		for uid in $$INDEX_UIDS; do \
			echo "  - $$uid"; \
			curl -s -X DELETE "$(MEILI_URL)/indexes/$$uid" -H "Authorization: Bearer $(MEILI_API_KEY)" >/dev/null || true; \
		done; \
	fi; \
	echo "Attempting to cancel enqueued/processing tasks..."; \
	curl -s -X POST "$(MEILI_URL)/tasks/cancel" -H "Authorization: Bearer $(MEILI_API_KEY)" -H "Content-Type: application/json" -d '{"statuses":["enqueued","processing"]}' >/dev/null || true; \
	echo "Attempting to delete task history..."; \
	curl -s -X DELETE "$(MEILI_URL)/tasks" -H "Authorization: Bearer $(MEILI_API_KEY)" >/dev/null || true; \
	echo "✓ Meilisearch reset requested"

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

# Full export (search parquet + ontology/entity artifacts; no Meilisearch dump)
export: export-entity export-ontology export-search export-finalize

# Finalize export: create version marker and update 'latest' symlink
# Use this after export-entity, export-ontology, and export-search
export-finalize:
	@# Create data version marker (hash of exported files)
	@echo "Creating data version marker..."
	@cat $(OUTPUT_DIR)/entity_identifier.parquet \
		$(OUTPUT_DIR)/omnipath_mi.obo \
		$(OUTPUT_DIR)/search_entities.parquet \
		$(OUTPUT_DIR)/search_interactions.parquet \
		$(OUTPUT_DIR)/search_associations.parquet \
		$(OUTPUT_DIR)/search_sources.parquet 2>/dev/null | shasum -a 256 | cut -d' ' -f1 \
		> $(OUTPUT_DIR)/.data_version
	@# Update 'latest' symlink
	@ln -sfn $(DATA_VERSION) data/latest
	@echo ""
	@echo "✓ All data exported to $(OUTPUT_DIR)/"
	@echo "  Data version: $$(cat $(OUTPUT_DIR)/.data_version)"
	@echo "  Symlink: data/latest -> $(DATA_VERSION)"
	@echo ""
	@echo "Data files ready for deployment:"
	@ls -lh $(OUTPUT_DIR)/*.parquet $(OUTPUT_DIR)/*.obo 2>/dev/null || true
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
	@if [ ! -f $(COMBINED_SEARCH_DIR)/search_entities.parquet ] || [ ! -f $(COMBINED_SEARCH_DIR)/search_interactions.parquet ] || [ ! -f $(COMBINED_SEARCH_DIR)/search_associations.parquet ] || [ ! -f $(COMBINED_SEARCH_DIR)/search_sources.parquet ]; then \
		echo "Error: search parquet files missing in $(COMBINED_SEARCH_DIR). Run 'make meilisearch' first."; \
		exit 1; \
	fi
	@cp -f $(COMBINED_SEARCH_DIR)/search_entities.parquet $(OUTPUT_DIR)/search_entities.parquet
	@cp -f $(COMBINED_SEARCH_DIR)/search_interactions.parquet $(OUTPUT_DIR)/search_interactions.parquet
	@cp -f $(COMBINED_SEARCH_DIR)/search_associations.parquet $(OUTPUT_DIR)/search_associations.parquet
	@cp -f $(COMBINED_SEARCH_DIR)/search_sources.parquet $(OUTPUT_DIR)/search_sources.parquet