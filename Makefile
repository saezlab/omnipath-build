.PHONY: setup silver silver-reprocess gold visualize

setup:
	git submodule add -b download-manager-experiment https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b download-manager-experiment https://github.com/saezlab/download-manager.git download-manager || true
	git config -f .gitmodules submodule.pypath.branch download-manager-experiment
	git config -f .gitmodules submodule.download-manager.branch download-manager-experiment
	git submodule update --init --recursive --remote
	uv sync
	pnpm --dir nextjs install

silver:
	@uv run -m omnipath_build.database_manager silver $(if $(filter-out $@,$(MAKECMDGOALS)),--source $(filter-out $@,$(MAKECMDGOALS)))

silver-reprocess:
	@uv run -m omnipath_build.database_manager silver --override $(if $(filter-out $@,$(MAKECMDGOALS)),--source $(filter-out $@,$(MAKECMDGOALS)))

gold:
	@if [ "$(filter-out $@,$(MAKECMDGOALS))" = "global_tables" ]; then \
		uv run -m omnipath_build.database_manager gold --phase 2; \
	elif [ -n "$(filter-out $@,$(MAKECMDGOALS))" ]; then \
		uv run -m omnipath_build.database_manager gold --step $(filter-out $@,$(MAKECMDGOALS)) $(if $(PHASE),--phase $(PHASE)); \
	else \
		uv run -m omnipath_build.database_manager gold $(if $(PHASE),--phase $(PHASE)); \
	fi

%:
	@:

visualize:
	pnpm --dir nextjs dev
