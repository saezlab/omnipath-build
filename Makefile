.PHONY: setup silver silver-test silver-reprocess gold postgres meilisearch meilisearch-entities meilisearch-interactions meilisearch-import meilisearch-import-entities meilisearch-import-interactions meilisearch-import-all gold-meilisearch-import visualize meilisearch-dump docker-data-setup docker-build docker-up docker-up-fresh

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
	@uv run -m omnipath_build.cli.commands silver \
		$(if $(or $(SOURCE),$(filter-out $@,$(MAKECMDGOALS))),--source $(if $(SOURCE),$(SOURCE),$(filter-out $@,$(MAKECMDGOALS)))) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

silver-test:
	@uv run -m omnipath_build.cli.commands silver --test-mode \
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

# Build both entities and interactions parquet files
meilisearch: meilisearch-entities meilisearch-interactions

# Import entities into Meilisearch
meilisearch-import-entities:
	@. .env && uv run python -m omnipath_build.search.importer \
		--dataset entities \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $${MEILI_MASTER_KEY}

# Import interactions into Meilisearch
meilisearch-import-interactions:
	@. .env && uv run python -m omnipath_build.search.importer \
		--dataset interactions \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $${MEILI_MASTER_KEY}

# Import both entities and interactions into Meilisearch
meilisearch-import-all:
	@. .env && uv run python -m omnipath_build.search.importer \
		--dataset both \
		--importer-path omnipath_build/meilisearch-importer \
		--api-key $${MEILI_MASTER_KEY}

# Backward compatibility: import entities only (original behavior)
meilisearch-import: meilisearch-import-entities

gold-meilisearch-import:
	@$(MAKE) gold
	@$(MAKE) meilisearch
	@$(MAKE) meilisearch-import

# =============================================================================
# Docker Deployment Targets
# =============================================================================

# Create a Meilisearch dump for deployment
# Prerequisites: Meilisearch must be running with data imported
meilisearch-dump:
	@. .env && uv run python -m omnipath_build.scripts.create_meilisearch_dump \
		--meili-url http://localhost:7700 \
		--api-key $${MEILI_MASTER_KEY} \
		--output-dir omnipath-present/data/dumps

# Set up the data directory with required files for Docker deployment
# This copies the necessary parquet files to data/
docker-data-setup:
	@echo "Setting up data directory..."
	@mkdir -p omnipath-present/data/dumps
	@if [ -f omnipath_build/data/gold/entity_identifier.parquet ]; then \
		cp -v omnipath_build/data/gold/entity_identifier.parquet omnipath-present/data/; \
	else \
		echo "Warning: entity_identifier.parquet not found"; \
	fi
	@if [ -f omnipath_build/data/gold/search_entities.parquet ]; then \
		cp -v omnipath_build/data/gold/search_entities.parquet omnipath-present/data/; \
	else \
		echo "Warning: search_entities.parquet not found"; \
	fi
	@if [ -f omnipath_build/data/gold/search_interactions.parquet ]; then \
		cp -v omnipath_build/data/gold/search_interactions.parquet omnipath-present/data/; \
	else \
		echo "Warning: search_interactions.parquet not found"; \
	fi
	@echo "Data directory setup complete."

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
