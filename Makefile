.PHONY: setup silver-list gold-mappings gold-source gold-all

JOBS ?= 4
BATCH_SIZE ?= 10000
DATA_ROOT ?= data_v2
INPUTS_PACKAGE ?= pypath.inputs_v2
RESOLVER_MAPPING_DIR ?= id_resolver/data

setup:
	git submodule add -b main https://github.com/saezlab/pypath.git pypath || true
	git submodule add -b main https://github.com/saezlab/download-manager.git download-manager || true
	git submodule update --init --recursive --remote
	uv sync

silver-list:
	@uv run python -m omnipath_build.cli.commands silver --list --inputs-package $(INPUTS_PACKAGE)

gold-mappings:
	@uv run python -m omnipath_build.pipeline.cli mappings \
		--data-root $(DATA_ROOT) \
		--jobs 1 \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR))

gold-source:
	@if [ -z "$(SOURCES)" ]; then \
		echo 'Error: set SOURCES="signor reactome"'; \
		exit 1; \
	fi
	@uv run python -m omnipath_build.pipeline.cli source $(SOURCES) \
		--data-root $(DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		--jobs $(JOBS) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR))

gold-all:
	@uv run python -m omnipath_build.pipeline.cli all $(SOURCES) \
		--data-root $(DATA_ROOT) \
		--inputs-package $(INPUTS_PACKAGE) \
		--batch-size $(BATCH_SIZE) \
		--jobs $(JOBS) \
		$(if $(TEST_MODE),--silver-test-mode) \
		$(if $(RESOLVER_MAPPING_DIR),--resolver-mapping-dir $(RESOLVER_MAPPING_DIR))
