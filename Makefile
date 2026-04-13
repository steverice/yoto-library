.PHONY: lint format test test-integ check help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

lint: ## Run linters (ruff check + ruff format check + ty)
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/
	uv run ty check src/

format: ## Auto-fix formatting and lint issues
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

test: ## Run unit tests (excludes integration)
	uv run python -m pytest -m "not integration"

test-integ: ## Run integration tests
	uv run python -m pytest -m integration

check: lint test ## Run all checks (lint + unit tests)
