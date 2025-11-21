.PHONY: setup silver silver-test silver-reprocess gold postgres meilisearch meilisearch-entities meilisearch-interactions meilisearch-import meilisearch-import-entities meilisearch-import-interactions meilisearch-import-all visualize

setup:
	git submodule add -b download-manager-experiment https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b download-manager-experiment https://github.com/saezlab/download-manager.git download-manager || true
	git config -f .gitmodules submodule.pypath.branch download-manager-experiment
	git config -f .gitmodules submodule.download-manager.branch download-manager-experiment
	git submodule update --init --recursive --remote
	uv sync
	pnpm --dir nextjs install

silver:
	@uv run -m omnipath_build.database_manager silver \
		$(if $(or $(SOURCE),$(filter-out $@,$(MAKECMDGOALS))),--source $(if $(SOURCE),$(SOURCE),$(filter-out $@,$(MAKECMDGOALS)))) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

silver-test:
	@uv run -m omnipath_build.database_manager silver --test-mode \
		$(if $(or $(SOURCE),$(filter-out $@,$(MAKECMDGOALS))),--source $(if $(SOURCE),$(SOURCE),$(filter-out $@,$(MAKECMDGOALS)))) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

silver-reprocess:
	@uv run -m omnipath_build.database_manager silver --override \
		$(if $(or $(SOURCE),$(filter-out $@,$(MAKECMDGOALS))),--source $(if $(SOURCE),$(SOURCE),$(filter-out $@,$(MAKECMDGOALS)))) \
		$(if $(FUNCTION),--function $(FUNCTION)) \
		$(if $(INPUTS_PACKAGE),--inputs-package $(INPUTS_PACKAGE))

gold:
	@if [ -n "$(filter-out $@,$(MAKECMDGOALS))" ]; then \
		uv run -m omnipath_build.database_manager gold --step $(filter-out $@,$(MAKECMDGOALS)); \
	else \
		uv run -m omnipath_build.database_manager gold; \
	fi

postgres:
	@. .env && uv run -m omnipath_build.database_manager postgres \
		--postgres-uri "postgresql://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@localhost:$${POSTGRES_PORT}/omnipath" \
		--schema public \
		$(if $(DROP),--drop-existing)

# Build search entities parquet
meilisearch-entities:
	@uv run python -m omnipath_build.search_builder.build_search_entities \
		--global-tables-dir databases/omnipath/output \
		--output databases/omnipath/output/search_entities.parquet

# Build search interactions parquet
meilisearch-interactions:
	@uv run python -m omnipath_build.search_builder.build_search_interactions \
		--global-tables-dir databases/omnipath/output \
		--output databases/omnipath/output/search_interactions.parquet

# Build both entities and interactions parquet files
meilisearch: meilisearch-entities meilisearch-interactions

# Import entities into Meilisearch
meilisearch-import-entities:
	@uv run python -m omnipath_build.import_search_entities \
		--dataset entities \
		--importer-path meilisearch-importer-main \
		--api-key ou2PElyoy2vTITMltS183DR0KOgy8cWERDkr8lX2UKc

# Import interactions into Meilisearch
meilisearch-import-interactions:
	@uv run python -m omnipath_build.import_search_entities \
		--dataset interactions \
		--importer-path meilisearch-importer-main \
		--api-key ou2PElyoy2vTITMltS183DR0KOgy8cWERDkr8lX2UKc

# Import both entities and interactions into Meilisearch
meilisearch-import-all:
	@uv run python -m omnipath_build.import_search_entities \
		--dataset both \
		--importer-path meilisearch-importer-main \
		--api-key ou2PElyoy2vTITMltS183DR0KOgy8cWERDkr8lX2UKc

# Backward compatibility: import entities only (original behavior)
meilisearch-import: meilisearch-import-entities

%:
	@:

visualize:
	pnpm --dir nextjs dev
