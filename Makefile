# Harness Makefile
# Run 'make help' for available targets

.DELETE_ON_ERROR:
.DEFAULT_GOAL := all

# ============================================================================
# Configuration
# ============================================================================

# Override via environment or command line: make test PYTHON=python3.13t
UV ?= uv
PYTHON := $(UV) run python
PYTEST := $(UV) run pytest
TY := $(UV) run ty
PYUPGRADE := $(UV) run pyupgrade

# Source directories
SRC_DIR := src
TEST_DIR := tests
SCRIPTS_DIR := scripts

# ============================================================================
# Computed Variables (use := for shell commands)
# ============================================================================

SRC_FILES := $(shell find $(SRC_DIR) -name '*.py' 2>/dev/null)
TEST_FILES := $(shell find $(TEST_DIR) -name '*.py' 2>/dev/null)

# ============================================================================
# Targets
# ============================================================================

##@ Setup

.PHONY: all
all: install  ## Default: bootstrap project for development

.PHONY: install
install:  ## Install all dependencies and setup pre-commit hooks
	$(UV) sync --dev
	$(UV) run pre-commit install
	@echo "Dependencies installed and pre-commit hooks setup"

.PHONY: install-global
install-global:  ## Install hyh globally (editable, uses repo code)
	$(UV) tool install --editable . --force
	@echo ""
	@echo "Installed globally. Run 'hyh --help' from anywhere."
	@echo "Changes to repo code take effect immediately."

.PHONY: uninstall-global
uninstall-global:  ## Remove global hyh installation
	$(UV) tool uninstall hyh || true
	$(UV) tool uninstall hyh || true
	@echo "Uninstalled global hyh"

##@ Development

.PHONY: dev
dev:  ## Start the daemon (development mode)
	$(PYTHON) -m hyh.daemon

.PHONY: shell
shell:  ## Open interactive Python shell with project loaded
	$(PYTHON) -c "from hyh import *; import code; code.interact(local=dict(globals()))"

##@ Testing

.PHONY: test
test:  ## Run affected tests only (fast dev feedback via testmon)
	$(PYTEST) -v

.PHONY: test-all
test-all:  ## Run full test suite in parallel (4 workers, tight timeout)
	$(PYTEST) -v --testmon-noselect -n 4 --timeout=10 --cov=hyh --cov-report=term-missing

.PHONY: test-seq
test-seq:  ## Run full suite sequentially (for debugging flaky tests)
	$(PYTEST) -v --testmon-noselect --timeout=30 --cov=hyh --cov-report=term-missing

.PHONY: test-reset
test-reset:  ## Reset testmon database (run after major refactors)
	$(RM) -f .testmondata
	$(PYTEST) -v

.PHONY: coverage
coverage:  ## Run tests with coverage reporting
	$(PYTEST) -v --testmon-noselect --cov=hyh --cov-report=html --cov-report=term-missing
	@echo "Coverage report generated in htmlcov/index.html"

.PHONY: test-fast
test-fast:  ## Run affected tests without timeout (faster iteration)
	$(PYTEST) -v --timeout=0

.PHONY: test-file
test-file:  ## Run specific test file: make test-file FILE=tests/hyh/test_state.py
	$(PYTEST) $(FILE) -v --testmon-noselect

.PHONY: benchmark
benchmark:  ## Run benchmark tests
	$(PYTEST) -v -m benchmark --testmon-noselect --benchmark-enable --benchmark-autosave --benchmark-disable-gc --benchmark-warmup=on --benchmark-min-rounds=5

.PHONY: memcheck
memcheck:  ## Run memory profiling tests
	$(PYTEST) -v -m memcheck --testmon-noselect --memray

.PHONY: perf
perf: benchmark memcheck  ## Run all performance tests (benchmark + memory)

.PHONY: check
check: lint typecheck test-all  ## Run all checks (lint + typecheck + full test suite)

##@ Code Quality

.PHONY: lint
lint:  ## Check code style and quality (no auto-fix)
	@find $(SRC_DIR) $(TEST_DIR) $(SCRIPTS_DIR) -name '*.py' -exec $(PYUPGRADE) --py314-plus {} +
	$(UV) run ruff check $(SRC_DIR) $(TEST_DIR) $(SCRIPTS_DIR)
	UV_PREVIEW=1 $(UV) format --check

.PHONY: typecheck
typecheck:  ## Run type checking with ty
	$(TY) check $(SRC_DIR)

.PHONY: format
format:  ## Auto-format code
	UV_PREVIEW=1 $(UV) format
	$(UV) run ruff check --fix $(SRC_DIR) $(TEST_DIR)

##@ Build & Publish

.PHONY: build
build:  ## Build wheel for distribution
	$(UV) build --no-sources

.PHONY: publish-test
publish-test: build  ## Publish to TestPyPI (for testing)
	$(UV) publish --index testpypi
	@echo ""
	@echo "Published to TestPyPI. Test install with:"
	@echo "  uv tool install hyh --index https://test.pypi.org/simple/"

.PHONY: publish
publish: build  ## Publish to PyPI (manual release)
	$(UV) publish
	@echo ""
	@echo "Published to PyPI. Install with:"
	@echo "  uv tool install hyh"

##@ Release Automation

.PHONY: changelog
changelog:  ## Generate CHANGELOG.md from conventional commits
	git-cliff --output CHANGELOG.md
	@echo "CHANGELOG.md updated"

.PHONY: release
release:  ## Release with version bump (Usage: make release TYPE=patch)
	@if [ -z "$(TYPE)" ]; then \
		echo "Usage: make release TYPE=[major|minor|patch|alpha|beta|rc|stable]"; \
		exit 1; \
	fi
	./scripts/release.sh $(TYPE)

.PHONY: release-alpha
release-alpha:  ## Bump alpha version and release
	./scripts/release.sh alpha

.PHONY: release-patch
release-patch:  ## Bump patch version and release
	./scripts/release.sh patch

.PHONY: release-minor
release-minor:  ## Bump minor version and release
	./scripts/release.sh minor

##@ Cleanup

.PHONY: clean
clean:  ## Remove build artifacts, caches, and venv
	$(RM) -r build dist .venv
	$(RM) -r *.egg-info src/*.egg-info .eggs
	$(RM) -r .pytest_cache .ruff_cache .ty_cache .benchmarks
	$(RM) -r __pycache__ src/hyh/__pycache__ tests/__pycache__ tests/hyh/__pycache__
	$(RM) -r .coverage htmlcov .testmondata
	@echo "Cleaned"

##@ Help

.PHONY: help
help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
