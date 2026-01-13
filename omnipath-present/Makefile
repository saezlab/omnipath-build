# Makefile for omnipath-present development

.PHONY: dev stop

# Start all development services: Meilisearch, Entity Service, and Next.js dev server
dev:
	COMPOSE_BAKE=true docker compose -f docker-compose.dev.yaml up -d
	@echo "Backend services started. Starting Next.js dev server..."
	cd next-omnipath && npm run dev

# Stop Docker services
stop:
	COMPOSE_BAKE=true docker compose -f docker-compose.dev.yaml down
