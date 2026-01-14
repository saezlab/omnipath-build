# Makefile for omnipath-present development
#
# Prerequisites: Run `make export` from the parent directory to populate data/

.PHONY: dev stop validate-data

# Validate that all required data files exist
validate-data:
	@echo "Checking required data files..."
	@missing=0; \
	if [ ! -f data/entity_identifier.parquet ]; then \
		echo "  ✗ Missing: data/entity_identifier.parquet"; \
		missing=1; \
	else \
		echo "  ✓ data/entity_identifier.parquet"; \
	fi; \
	if [ ! -f data/omnipath_mi.obo ]; then \
		echo "  ✗ Missing: data/omnipath_mi.obo"; \
		missing=1; \
	else \
		echo "  ✓ data/omnipath_mi.obo"; \
	fi; \
	if [ ! -f data/dumps/.dump_file ]; then \
		echo "  ✗ Missing: data/dumps/.dump_file"; \
		missing=1; \
	else \
		DUMP_NAME=$$(cat data/dumps/.dump_file); \
		if [ ! -f "data/dumps/$$DUMP_NAME" ]; then \
			echo "  ✗ Missing: data/dumps/$$DUMP_NAME (referenced in .dump_file)"; \
			missing=1; \
		else \
			echo "  ✓ data/dumps/$$DUMP_NAME"; \
		fi; \
	fi; \
	if [ $$missing -eq 1 ]; then \
		echo ""; \
		echo "Run 'make export' from the parent directory to generate data files."; \
		exit 1; \
	fi
	@echo ""
	@if [ -f data/.data_version ]; then \
		echo "Data version: $$(cat data/.data_version)"; \
	fi
	@echo "✓ All data files present"

# Start all development services: Meilisearch, Entity Service, Ontology Service, and Next.js dev server
dev: validate-data
	COMPOSE_BAKE=true docker compose -f docker-compose.dev.yaml up -d
	@echo ""
	@echo "Backend services starting..."
	@echo "  - Meilisearch:      http://localhost:7700"
	@echo "  - Entity Service:   http://localhost:8080"
	@echo "  - Ontology Service: http://localhost:8081"
	@echo ""
	@echo "Starting Next.js dev server..."
	cd next-omnipath && npm run dev

# Stop Docker services
stop:
	COMPOSE_BAKE=true docker compose -f docker-compose.dev.yaml down

# Force reimport of meilisearch data (useful when dump file changes)
reimport-meilisearch:
	@echo "Removing meilisearch volume to force reimport..."
	docker volume rm omnipath_build_meilisearch_data 2>/dev/null || true
	@echo "Next 'make dev' will import fresh data."
