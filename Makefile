.PHONY: help
.DEFAULT_GOAL := help

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies (telemetry + macOS menubar extras)
	uv sync --extra telemetry --extra menubar

lint: ## Run linting with ruff
	uv run ruff check .

format: ## Format code with ruff
	uv run ruff format .

typecheck: ## Run type checking with mypy
	uv run mypy openjev_tool

test: ## Run tests (no coverage)
	uv run pytest tests/

coverage: ## Run tests with coverage and enforce the minimum (currently 40%)
	uv run pytest tests/ --cov=openjev_tool --cov-report=term-missing --cov-fail-under=40

quality: ## Run code-quality checks (dead code + complexity gate + metrics)
	uv run vulture openjev_tool vulture_whitelist.py
	# Complexity gate: xenon fails if any block/maintainability/average exceeds grade C.
	# Current baseline is all-B; tighten to -b B -m B -a B once stable.
	uv run xenon -b C -m C -a C openjev_tool
	uv run radon cc openjev_tool -a -n B
	uv run radon mi openjev_tool -s

security-bandit: ## Run bandit security linter
	uv run bandit -r openjev_tool -c pyproject.toml

security-pip-audit: ## Run pip-audit for dependency vulnerabilities
	uv run pip-audit

security-gitleaks: ## Run gitleaks secret scanner
	@command -v gitleaks >/dev/null 2>&1 || { echo "❌ gitleaks not found. Install: brew install gitleaks"; exit 1; }
	gitleaks detect --source . --config .gitleaks.toml --verbose

security: security-bandit security-pip-audit security-gitleaks ## Run all security checks

check: install lint typecheck coverage quality security ## Run all checks (lint, typecheck, coverage, quality, security)

pipeline: format install lint typecheck coverage quality security build install-global ## Run full pipeline (format, lint, typecheck, coverage, quality, security, build, install-global)

clean: ## Remove build artifacts and cache
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

run: ## Run openjev-tool (usage: make run ARGS="...")
	uv run openjev-tool $(ARGS)

build: ## Build package (force rebuild)
	uv build --force-pep517

install-global: build ## Install globally with uv tool (rebuilds first; includes rumps for menubar)
	-uv tool uninstall openjev-tool 2>/dev/null
	uv tool install . --reinstall --force --with rumps

uninstall-global: ## Uninstall global installation
	uv tool uninstall openjev-tool
