# OmniPath Build - Developer Makefile
# ===================================
# Streamlined developer experience with just 5 essential commands

.DEFAULT_GOAL := help
.PHONY: help setup start new run stop

# Colors for output
GREEN := \033[32m
YELLOW := \033[33m
BLUE := \033[34m
RED := \033[31m
RESET := \033[0m

# Default database if none specified
DB ?= omnipath


help: ## Show this help message
	@echo "$(GREEN)OmniPath Build - Developer Commands$(RESET)"
	@echo ""
	@echo "$(BLUE)Essential Commands:$(RESET)"
	@echo "  $(GREEN)make setup$(RESET)     - One-time complete setup (install deps, start postgres, etc.)"
	@echo "  $(GREEN)make start$(RESET)     - Daily startup (ensure postgres running, show status)"
	@echo "  $(GREEN)make new DB=mydb$(RESET) - Create & configure a new database with resources"
	@echo "  $(GREEN)make run DB=mydb$(RESET) - Run/update an existing database"
	@echo "  $(GREEN)make stop$(RESET)      - Clean shutdown (stop postgres, cleanup logs)"
	@echo ""
	@echo "$(BLUE)Examples:$(RESET)"
	@echo "  make setup                    # First time setup"
	@echo "  make new DB=signaling        # Create signaling database"
	@echo "  make run DB=signaling        # Update signaling database"
	@echo ""

setup: ## Complete one-time setup
	@echo "$(BLUE)🚀 Setting up OmniPath Build development environment...$(RESET)"
	@echo ""
	
	# Check for required tools
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "$(RED)Error: docker is required but not installed$(RESET)"; \
		echo "Please install docker and try again"; \
		exit 1; \
	fi
	@if ! command -v docker-compose >/dev/null 2>&1; then \
		echo "$(RED)Error: docker-compose is required but not installed$(RESET)"; \
		echo "Please install docker-compose and try again"; \
		exit 1; \
	fi
	
	# Install uv if not present
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "$(BLUE)ℹ Installing uv package manager...$(RESET)"; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		export PATH="$$HOME/.cargo/bin:$$PATH"; \
	fi
	@echo "$(GREEN)✓ uv package manager ready$(RESET)"
	
	# Install dependencies
	@echo "$(BLUE)ℹ Installing Python dependencies...$(RESET)"
	@uv sync --extra dev --extra tests
	@echo "$(GREEN)✓ Dependencies installed$(RESET)"
	
	# Setup environment file
	@if [ ! -f .env ]; then \
		echo "$(BLUE)ℹ Creating .env file from template...$(RESET)"; \
		cp .env.example .env; \
		echo "$(GREEN)✓ .env file created$(RESET)"; \
	else \
		echo "$(GREEN)✓ .env file already exists$(RESET)"; \
	fi
	
	# Start PostgreSQL
	@echo "$(BLUE)ℹ Starting PostgreSQL container...$(RESET)"
	@if docker-compose up -d postgres 2>/dev/null; then \
		echo "$(GREEN)✓ PostgreSQL container started$(RESET)"; \
		sleep 5; \
	elif docker-compose ps postgres | grep -q "Up"; then \
		echo "$(GREEN)✓ PostgreSQL already running$(RESET)"; \
	else \
		echo "$(YELLOW)⚠ PostgreSQL container issue, but checking if service is available...$(RESET)"; \
	fi
	
	# Wait for PostgreSQL to be ready
	@echo "$(BLUE)ℹ Checking PostgreSQL connectivity...$(RESET)"
	@if docker-compose exec postgres pg_isready -U postgres >/dev/null 2>&1; then \
		echo "$(GREEN)✓ PostgreSQL is ready (container)$(RESET)"; \
	elif pg_isready -h localhost -p 5436 -U postgres >/dev/null 2>&1; then \
		echo "$(GREEN)✓ PostgreSQL is ready (localhost:5436)$(RESET)"; \
	else \
		echo "$(YELLOW)⚠ PostgreSQL connectivity issue - you may need to check your setup$(RESET)"; \
		echo "$(BLUE)ℹ Trying to connect for 15 seconds...$(RESET)"; \
		timeout=15; \
		while [ $$timeout -gt 0 ]; do \
			if docker-compose exec postgres pg_isready -U postgres >/dev/null 2>&1 || pg_isready -h localhost -p 5436 -U postgres >/dev/null 2>&1; then \
				echo "$(GREEN)✓ PostgreSQL is now ready$(RESET)"; \
				break; \
			fi; \
			sleep 1; \
			timeout=$$((timeout-1)); \
		done; \
		if [ $$timeout -eq 0 ]; then \
			echo "$(YELLOW)⚠ PostgreSQL may not be ready, but continuing setup...$(RESET)"; \
		fi; \
	fi
	
	# Generate PyPath resources list
	@echo "$(BLUE)ℹ Generating PyPath resources list...$(RESET)"
	@uv run python omnipath_build/tools/list_pypath_resources.py
	@echo "$(GREEN)✓ PyPath resources list generated$(RESET)"
	
	@echo ""
	@echo "$(GREEN)🎉 Setup complete! You're ready to work.$(RESET)"
	@echo ""
	@echo "$(BLUE)Next steps:$(RESET)"
	@echo "  make new DB=myproject    # Create your first database"
	@echo "  make help               # See all available commands"

start: ## Daily startup - ensure everything is running
	@echo "$(BLUE)🌅 Starting daily development session...$(RESET)"
	@echo ""
	
	# Check dependencies
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "$(RED)Error: uv is required but not installed$(RESET)"; \
		echo "Please run 'make setup' first"; \
		exit 1; \
	fi
	@if ! command -v docker-compose >/dev/null 2>&1; then \
		echo "$(RED)Error: docker-compose is required but not installed$(RESET)"; \
		echo "Please install docker-compose and try again"; \
		exit 1; \
	fi
	
	# Ensure PostgreSQL is running
	@if ! docker-compose ps postgres | grep -q "Up"; then \
		echo "$(BLUE)ℹ Starting PostgreSQL...$(RESET)"; \
		if docker-compose up -d postgres 2>/dev/null; then \
			sleep 5; \
			echo "$(GREEN)✓ PostgreSQL started$(RESET)"; \
		else \
			echo "$(YELLOW)⚠ PostgreSQL container issue, but checking if service is available...$(RESET)"; \
		fi; \
	else \
		echo "$(GREEN)✓ PostgreSQL already running$(RESET)"; \
	fi
	
	# Check PostgreSQL health
	@if docker-compose exec postgres pg_isready -U postgres >/dev/null 2>&1; then \
		echo "$(GREEN)✓ PostgreSQL is healthy (container)$(RESET)"; \
	elif pg_isready -h localhost -p 5436 -U postgres >/dev/null 2>&1; then \
		echo "$(GREEN)✓ PostgreSQL is healthy (localhost:5436)$(RESET)"; \
	else \
		echo "$(YELLOW)⚠ PostgreSQL may not be ready yet$(RESET)"; \
	fi
	
	# Show available databases
	@echo "$(BLUE)ℹ Available database configurations:$(RESET)"
	@if [ -d "omnipath_build/databases" ]; then \
		ls -1 omnipath_build/databases/ | sed 's/^/  - /'; \
	else \
		echo "  (no databases configured yet)"; \
	fi
	
	@echo ""
	@echo "$(GREEN)✨ Ready to work!$(RESET)"
	@echo ""
	@echo "$(BLUE)Quick commands:$(RESET)"
	@echo "  make new DB=myproject    # Create new database"
	@echo "  make run DB=existing     # Update existing database"
	@echo "  make help               # Show all commands"

new: ## Create and configure a new database
	@if [ -z "$(DB)" ] || [ "$(DB)" = "omnipath" ]; then \
		echo "$(RED)Please specify a database name: make new DB=myproject$(RESET)"; \
		exit 1; \
	fi
	
	@echo "$(BLUE)🆕 Creating new database: $(DB)$(RESET)"
	@echo ""
	
	# Initialize database structure
	@echo "$(BLUE)ℹ Initializing database structure...$(RESET)"
	@uv run --env-file .env python omnipath_build/database_manager.py init --database $(DB)
	@echo "$(GREEN)✓ Database $(DB) initialized$(RESET)"
	
	# Show available resources
	@echo ""
	@echo "$(BLUE)ℹ Available PyPath resources (showing first 20):$(RESET)"
	@if [ -f pypath_resources.txt ]; then \
		head -20 pypath_resources.txt | tail -15; \
	else \
		uv run python omnipath_build/tools/list_pypath_resources.py; \
		head -20 pypath_resources.txt | tail -15; \
	fi
	
	@echo ""
	@echo "$(YELLOW)📝 Next steps:$(RESET)"
	@echo "1. Add resources to your database:"
	@echo "   $(GREEN)uv run --env-file .env python omnipath_build/database_manager.py add-resources --database $(DB) --resources signor.signor_interactions$(RESET)"
	@echo ""
	@echo "2. Load the data:"
	@echo "   $(GREEN)make run DB=$(DB)$(RESET)"
	@echo ""
	@echo "3. Or see all resources: $(GREEN)cat pypath_resources.txt$(RESET)"

run: ## Run/update an existing database
	@if [ -z "$(DB)" ]; then \
		echo "$(RED)Please specify a database name: make run DB=myproject$(RESET)"; \
		exit 1; \
	fi
	
	@if [ ! -d "omnipath_build/databases/$(DB)" ]; then \
		echo "$(RED)Database $(DB) not found. Create it first with: make new DB=$(DB)$(RESET)"; \
		exit 1; \
	fi
	
	@echo "$(BLUE)🔄 Running database: $(DB)$(RESET)"
	@echo ""
	
	# Load all data layers
	@echo "$(BLUE)ℹ Loading all data layers...$(RESET)"
	@uv run --env-file .env python omnipath_build/database_manager.py load --database $(DB) --log-level DEBUG
	
	@echo ""
	@echo "$(GREEN)✓ Database $(DB) processing complete!$(RESET)"
	
	# Show status
	@echo ""
	@echo "$(BLUE)ℹ Database status:$(RESET)"
	@uv run --env-file .env python omnipath_build/database_manager.py status --database $(DB)

stop: ## Clean shutdown
	@echo "$(BLUE)🛑 Shutting down development environment...$(RESET)"
	@echo ""
	
	# Stop PostgreSQL
	@echo "$(BLUE)ℹ Stopping PostgreSQL...$(RESET)"
	@docker-compose down
	@echo "$(GREEN)✓ PostgreSQL stopped$(RESET)"
	
	# Clean up old logs (keep last 7 days)
	@echo "$(BLUE)ℹ Cleaning up old log files...$(RESET)"
	@find . -name "*.log" -type f -mtime +7 -delete 2>/dev/null || true
	@find . -name "*_log" -type d -exec find {} -name "*.log" -type f -mtime +7 -delete \; 2>/dev/null || true
	@echo "$(GREEN)✓ Old logs cleaned$(RESET)"
	
	@echo ""
	@echo "$(GREEN)👋 Environment shut down cleanly$(RESET)"

# Hidden targets for development
.PHONY: _check-db _ensure-postgres _list-resources

_check-db:
	@if [ -z "$(DB)" ]; then \
		echo "$(RED)Error: Database name required$(RESET)"; \
		exit 1; \
	fi

_ensure-postgres:
	@if ! docker-compose ps postgres | grep -q "Up"; then \
		docker-compose up -d postgres; \
		sleep 5; \
	fi

_list-resources:
	@if [ ! -f pypath_resources.txt ]; then \
		uv run python omnipath_build/tools/list_pypath_resources.py; \
	fi
