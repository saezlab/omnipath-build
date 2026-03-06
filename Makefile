.PHONY: setup pipeline pipeline-data pipeline-index generate-obo meilisearch-delete-indexes meilisearch-reset

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

pipeline: pipeline-data pipeline-index

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
