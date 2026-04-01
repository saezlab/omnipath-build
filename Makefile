.PHONY: setup pipeline pipeline-data pipeline-index generate-obo meilisearch-delete-indexes meilisearch-reset restart-entity-service restart-api-service target-schema-source target-schema-mappings target-schema-global target-schema-all

# Load local environment defaults (e.g. MEILISEARCH_API_KEY) when available.
ifneq (,$(wildcard .env))
include .env
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env)
endif

# Direct Meilisearch deployment target (used by index import).
MEILI_URL ?= http://localhost:7700
strip_quotes = $(patsubst "%",%,$(1))
MEILI_API_KEY ?= $(call strip_quotes,$(if $(MEILISEARCH_API_KEY),$(MEILISEARCH_API_KEY),$(TEMP_MEILI_KEY)))

JOBS ?= 4

# Service restart defaults.
# If *_CONTAINER is set, that exact container is restarted.
# Otherwise we auto-detect a Docker Compose container by service label.
ENTITY_SERVICE_NAME ?= entity-service
ENTITY_SERVICE_CONTAINER ?=
API_SERVICE_NAME ?= api-service
API_SERVICE_CONTAINER ?=

# =============================================================================
# Setup
# =============================================================================

setup:
	git submodule add -b main https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b main https://github.com/saezlab/download-manager.git download-manager || true
	git submodule update --init --recursive --remote
	uv sync

# =============================================================================
# DAG Pipeline
# =============================================================================
# The pipeline is driven by the DAG scheduler which handles silver, gold, and
# search parquet generation with incremental fingerprinting.
#
# Usage:
#   make pipeline-data              # Build data artifacts (silver → gold → search parquet)
#   make pipeline-index             # Import search parquet into Meilisearch
#   make pipeline                   # Both: data + index
#
# Options (via env vars):
#   JOBS=8                          # Parallel workers (default 4)
#   TEST_MODE=1                     # Silver test-mode record caps
#   INPUTS_PACKAGE=...              # Override inputs package
#   FULL_REINDEX=1                  # Force full Meilisearch reindex

pipeline-data:
	@echo "======================================================================"
	@echo "Running data pipeline (DAG)..."
	@echo "  Jobs: $(JOBS)"
	@echo "======================================================================"
	@uv run python -m omnipath_build.pipeline.run_dag \
		--jobs $(JOBS) \
		$(if $(TEST_MODE),--test-mode) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE)) \
		$(if $(FRESHNESS_CHECKS),--freshness-checks)
	@echo ""
	@echo "======================================================================"
	@echo "✓ Data pipeline completed."
	@echo "======================================================================"

pipeline-index:
	@echo "======================================================================"
	@echo "Running index import..."
	@echo "======================================================================"
	@uv run python -m omnipath_build.pipeline.import_indexes \
		--jobs $(JOBS) \
		$(if $(FULL_REINDEX),--full-reindex)
	@echo ""
	@echo "======================================================================"
	@echo "✓ Index import completed."
	@echo "======================================================================"

pipeline:
	@echo "======================================================================"
	@echo "Running full pipeline..."
	@echo "  Stage 1/2: data pipeline"
	@echo "  Stage 2/2: index import"
	@echo "======================================================================"
	@$(MAKE) pipeline-data
	@echo ""
	@echo "----------------------------------------------------------------------"
	@echo "Proceeding to stage 2/2: index import"
	@echo "----------------------------------------------------------------------"
	@$(MAKE) pipeline-index

# =============================================================================
# Target-schema pipeline shortcuts
# =============================================================================
# Usage:
#   make target-schema-source SOURCES="signor reactome"
#   make target-schema-source SOURCES="signor" TEST_MODE=1
#   make target-schema-mappings
#   make target-schema-global SOURCES="signor reactome"
#   make target-schema-all SOURCES="signor reactome hmdb" TEST_MODE=1
#
# Options (via env vars):
#   SOURCES="signor reactome"        # Required for target-schema-source/all
#   TEST_MODE=1                       # Pass --silver-test-mode
#   WITH_GLOBAL=1                     # Pass --with-global to target-schema-source
#   NO_OVERWRITE=1                    # Pass --no-overwrite
#   SKIP_SILVER=1                     # Pass --skip-silver
#   SKIP_MAPPINGS=1                   # Pass --skip-mappings
#   PRESERVE_SILVER=1                 # For target-schema-mappings, reuse existing silver (default 1)
#   INPUTS_PACKAGE=...                # Override inputs package
#   BATCH_SIZE=5000                   # Override batch size

BATCH_SIZE ?= 10000
PRESERVE_SILVER ?= 1

target-schema-source:
	@if [ -z "$(SOURCES)" ]; then \
		echo 'Error: set SOURCES="signor reactome"'; \
		exit 1; \
	fi
	@uv run python scripts/target_schema_pipeline.py source $(SOURCES) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(WITH_GLOBAL),--with-global) \
		$(if $(NO_OVERWRITE),--no-overwrite) \
		$(if $(SKIP_SILVER),--skip-silver) \
		$(if $(SKIP_MAPPINGS),--skip-mappings) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE)) \
		--batch-size $(BATCH_SIZE)

target-schema-mappings:
	@uv run python scripts/target_schema_pipeline.py mappings \
		$(if $(filter 1,$(PRESERVE_SILVER)),--preserve-silver) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(NO_OVERWRITE),--no-overwrite) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE)) \
		--batch-size $(BATCH_SIZE)

target-schema-global:
	@uv run python scripts/target_schema_pipeline.py global $(SOURCES) \
		$(if $(NO_OVERWRITE),--no-overwrite)

target-schema-all:
	@if [ -z "$(SOURCES)" ]; then \
		echo 'Error: set SOURCES="signor reactome"'; \
		exit 1; \
	fi
	@uv run python scripts/target_schema_pipeline.py all $(SOURCES) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(NO_OVERWRITE),--no-overwrite) \
		$(if $(SKIP_SILVER),--skip-silver) \
		$(if $(SKIP_MAPPINGS),--skip-mappings) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE)) \
		--batch-size $(BATCH_SIZE)

# =============================================================================
# Meilisearch admin utilities
# =============================================================================

# Delete OmniPath Meilisearch indexes
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

# Fully reset Meilisearch by removing every index and clearing task queue.
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

# Restart the entity service so it reloads rebuilt parquet data.
# Usage:
#   make restart-entity-service
#   make restart-entity-service ENTITY_SERVICE_CONTAINER=omnipath-staging-entity-service-1
#   make restart-entity-service ENTITY_SERVICE_NAME=entity-service
restart-entity-service:
	@set -e; \
	if [ -n "$(ENTITY_SERVICE_CONTAINER)" ]; then \
		TARGET="$(ENTITY_SERVICE_CONTAINER)"; \
	else \
		MATCHES=$$(docker ps -a --filter "label=com.docker.compose.service=$(ENTITY_SERVICE_NAME)" --format '{{.ID}} {{.Names}}'); \
		COUNT=$$(printf '%s\n' "$$MATCHES" | sed '/^$$/d' | wc -l | tr -d ' '); \
		if [ "$$COUNT" = "0" ]; then \
			echo "No container found for compose service '$(ENTITY_SERVICE_NAME)'."; \
			echo "Set ENTITY_SERVICE_CONTAINER=<container-name> to restart it explicitly."; \
			exit 1; \
		fi; \
		if [ "$$COUNT" != "1" ]; then \
			echo "Multiple containers matched compose service '$(ENTITY_SERVICE_NAME)':"; \
			printf '%s\n' "$$MATCHES"; \
			echo "Set ENTITY_SERVICE_CONTAINER=<container-name> to choose one explicitly."; \
			exit 1; \
		fi; \
		TARGET=$$(printf '%s\n' "$$MATCHES" | awk 'NR==1 {print $$1}'); \
	fi; \
	echo "Restarting entity service: $$TARGET"; \
	docker restart "$$TARGET" >/dev/null; \
	echo "✓ Entity service restarted"

# Restart the API service so it reloads rebuilt data.
# Usage:
#   make restart-api-service
#   make restart-api-service API_SERVICE_CONTAINER=omnipath-staging-api-service-1
#   make restart-api-service API_SERVICE_NAME=api-service
restart-api-service:
	@set -e; \
	if [ -n "$(API_SERVICE_CONTAINER)" ]; then \
		TARGET="$(API_SERVICE_CONTAINER)"; \
	else \
		MATCHES=$$(docker ps -a --filter "label=com.docker.compose.service=$(API_SERVICE_NAME)" --format '{{.ID}} {{.Names}}'); \
		COUNT=$$(printf '%s\n' "$$MATCHES" | sed '/^$$/d' | wc -l | tr -d ' '); \
		if [ "$$COUNT" = "0" ]; then \
			echo "No container found for compose service '$(API_SERVICE_NAME)'."; \
			echo "Set API_SERVICE_CONTAINER=<container-name> to restart it explicitly."; \
			exit 1; \
		fi; \
		if [ "$$COUNT" != "1" ]; then \
			echo "Multiple containers matched compose service '$(API_SERVICE_NAME)':"; \
			printf '%s\n' "$$MATCHES"; \
			echo "Set API_SERVICE_CONTAINER=<container-name> to choose one explicitly."; \
			exit 1; \
		fi; \
		TARGET=$$(printf '%s\n' "$$MATCHES" | awk 'NR==1 {print $$1}'); \
	fi; \
	echo "Restarting API service: $$TARGET"; \
	docker restart "$$TARGET" >/dev/null; \
	echo "✓ API service restarted"
