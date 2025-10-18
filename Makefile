.PHONY: setup silver visualize

setup:
	uv sync
	pnpm --dir nextjs install

silver:
	uv run -m omnipath_build.database_manager silver

visualize:
	pnpm --dir nextjs dev
