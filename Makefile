.PHONY: setup silver silver-reprocess gold postgres meilisearch meilisearch-import visualize

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

meilisearch:
	@uv run python -m omnipath_build.search_builder.cli \
		--global-tables-dir /Users/jschaul/Code/omnipath_build/databases/omnipath/output \
		--output /Users/jschaul/Code/omnipath_build/databases/omnipath/output/search_entities.parquet

meilisearch-import:
	@uv run python scripts/import_search_entities.py \
		--importer-path meilisearch-importer-main \
		--api-key ou2PElyoy2vTITMltS183DR0KOgy8cWERDkr8lX2UKc

%:
	@:

visualize:
	pnpm --dir nextjs dev
