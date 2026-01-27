.PHONY: setup silver silver-test silver-reprocess gold postgres meilisearch meilisearch-entities meilisearch-interactions meilisearch-associations meilisearch-import meilisearch-import-entities meilisearch-import-interactions meilisearch-import-associations meilisearch-import-all meilisearch-delete-indexes gold-meilisearch-import visualize meilisearch-dump meilisearch-build-dump meilisearch-build-dump-start meilisearch-build-dump-stop docker-data-setup docker-build docker-up docker-up-fresh pipeline generate-obo

# =============================================================================
# Full Pipeline - Run everything from silver to export
# =============================================================================
# Usage: make pipeline
# This runs: silver-test -> generate-obo -> gold -> meilisearch -> build-dump -> export-entity -> export-ontology -> export-finalize
# The meilisearch-build-dump step spins up a temporary container, imports data, creates dump, and cleans up.
pipeline:
	@echo "======================================================================"
	@echo "Starting full pipeline..."
	@echo "======================================================================"
	@$(MAKE) silver-test
	@$(MAKE) generate-obo
	@$(MAKE) gold
	@$(MAKE) meilisearch
	@$(MAKE) meilisearch-build-dump
	@$(MAKE) export-entity
	@$(MAKE) export-ontology
	@$(MAKE) export-finalize
	@echo ""
	@echo "======================================================================"
	@echo "✓ Full pipeline completed successfully!"
	@echo "======================================================================"

pipeline-full:
	@echo "======================================================================"
	@echo "Starting full pipeline..."
	@echo "======================================================================"
	@$(MAKE) silver
	@$(MAKE) generate-obo
	@$(MAKE) gold
	@$(MAKE) meilisearch
	@$(MAKE) meilisearch-build-dump
	@$(MAKE) export-entity
	@$(MAKE) export-ontology
	@$(MAKE) export-finalize
	@echo ""
	@echo "======================================================================"
	@echo "✓ Full pipeline completed successfully!"
	@echo "======================================================================"

# Generate OmniPath OBO file (needed for CV term label resolution in gold step)
generate-obo:
	@echo "Generating OmniPath OBO file..."
	@mkdir -p omnipath-present/data
	@uv run python pypath/scripts/export_omnipath_obo.py omnipath-present/data/omnipath_mi.obo
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
	pnpm --dir omnipath-present/next-omnipath install

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

# Export all data files to omnipath-present/data/ for deployment
# This creates the complete data package needed for Docker deployment
# A version marker is created to enable automatic meilisearch rebuild on data changes
.PHONY: export export-entity export-ontology export-meilisearch export-finalize

# Full export (use when you have a running Meilisearch with data)
export: export-entity export-ontology export-meilisearch export-finalize

# Finalize export: copy scripts and create version marker
# Use this after export-entity, export-ontology, and meilisearch-build-dump
export-finalize:
	@# Copy startup scripts
	@mkdir -p omnipath-present/data/scripts
	@cp omnipath-present/scripts/meilisearch-start.sh omnipath-present/data/scripts/
	@# Create data version marker (hash of all data files)
	@echo "Creating data version marker..."
	@DUMP_NAME=$$(cat omnipath-present/data/dumps/.dump_file); \
	cat omnipath-present/data/entity_identifier.parquet \
		omnipath-present/data/omnipath_mi.obo \
		omnipath-present/data/dumps/$$DUMP_NAME 2>/dev/null | shasum -a 256 | cut -d' ' -f1 \
		> omnipath-present/data/.data_version
	@echo ""
	@echo "✓ All data exported to omnipath-present/data/"
	@echo "  Data version: $$(cat omnipath-present/data/.data_version)"
	@echo "  Dump file: $$(cat omnipath-present/data/dumps/.dump_file)"
	@echo ""
	@echo "Data files ready for deployment:"
	@ls -lh omnipath-present/data/*.parquet omnipath-present/data/*.obo 2>/dev/null || true
	@ls -lh omnipath-present/data/dumps/*.dump 2>/dev/null || true
	@echo ""
	@echo "To deploy, upload omnipath-present/data/ to /root/omnipath2-data/ on the server"

# Export entity service data
export-entity:
	@echo "Exporting entity service data..."
	@mkdir -p omnipath-present/data
	@if [ -f omnipath_build/data/gold/entity_identifier.parquet ]; then \
		cp -v omnipath_build/data/gold/entity_identifier.parquet omnipath-present/data/; \
	else \
		echo "Error: entity_identifier.parquet not found. Run 'make gold' first."; \
		exit 1; \
	fi

# Export ontology service data (generates omnipath_mi.obo)
export-ontology:
	@echo "Exporting ontology service data..."
	@mkdir -p omnipath-present/data
	@uv run python pypath/scripts/export_omnipath_obo.py omnipath-present/data/omnipath_mi.obo

# Export meilisearch dump (must have meilisearch running with data)
export-meilisearch:
	@echo "Exporting meilisearch dump..."
	@mkdir -p omnipath-present/data/dumps
	@. .env && uv run python -m omnipath_build.scripts.create_meilisearch_dump \
		--meili-url http://localhost:7700 \
		--api-key $${MEILISEARCH_API_KEY} \
		--output-dir omnipath-present/data/dumps

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
	@mkdir -p omnipath-present/data/dumps
	@uv run python -m omnipath_build.scripts.create_meilisearch_dump \
		--meili-url http://localhost:$(TEMP_MEILI_PORT) \
		--api-key $(TEMP_MEILI_KEY) \
		--output-dir omnipath-present/data/dumps \
		--container-name $(TEMP_MEILI_CONTAINER)
	@echo ""
	@# Cleanup
	@$(MAKE) meilisearch-build-dump-stop
	@echo ""
	@echo "======================================================================"
	@echo "✓ Meilisearch dump created successfully!"
	@echo "  Output: omnipath-present/data/dumps/"
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

# =============================================================================
# Docker Deployment Targets (Legacy - use 'make export' instead)
# =============================================================================

# Create a Meilisearch dump for deployment
# Prerequisites: Meilisearch must be running with data imported
meilisearch-dump: export-meilisearch

# Build all Docker images
docker-build:
	docker compose -f omnipath-present/docker-compose.yaml build

# Start all services
docker-up:
	docker compose -f omnipath-present/docker-compose.yaml up -d

# Start with fresh Meilisearch data from dump
# Usage: make docker-up-fresh
docker-up-fresh:
	@if [ -f omnipath-present/data/dumps/latest.dump ]; then \
		MEILI_IMPORT_DUMP=/dumps/latest.dump docker compose -f omnipath-present/docker-compose.yaml up -d; \
	else \
		echo "No dump file found at omnipath-present/data/dumps/latest.dump"; \
		echo "Run 'make meilisearch-dump' first to create one."; \
		exit 1; \
	fi

%:
	@:

visualize:
	pnpm --dir nextjs dev
