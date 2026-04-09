.PHONY: lint format test test-integ check help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

lint: ## Run linters (ruff check + ruff format check + ty)
	ruff check src/ tests/
	ruff format --check src/ tests/
	ty check src/

format: ## Auto-fix formatting and lint issues
	ruff format src/ tests/
	ruff check --fix src/ tests/

test: ## Run unit tests (excludes integration)
	python -m pytest -m "not integration"

test-integ: ## Run integration tests
	python -m pytest -m integration

check: lint test ## Run all checks (lint + unit tests)
