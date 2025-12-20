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
RUFF := $(UV) run ruff
MYPY := $(UV) run mypy
PYUPGRADE := $(UV) run pyupgrade

# Source directories
SRC_DIR := src
TEST_DIR := tests

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
install:  ## Install all dependencies (local dev)
	$(UV) sync --dev
	@echo "Dependencies installed"

.PHONY: install-global
install-global:  ## Install harness globally (editable, uses repo code)
	$(UV) tool install --editable . --force
	@echo ""
	@echo "Installed globally. Run 'harness --help' from anywhere."
	@echo "Changes to repo code take effect immediately."

.PHONY: uninstall-global
uninstall-global:  ## Remove global harness installation
	$(UV) tool uninstall harness || true
	@echo "Uninstalled global harness"

##@ Development

.PHONY: dev
dev:  ## Start the daemon (development mode)
	$(PYTHON) -m harness.daemon

.PHONY: shell
shell:  ## Open interactive Python shell with project loaded
	$(PYTHON) -c "from harness import *; import code; code.interact(local=dict(globals()))"

##@ Testing

.PHONY: test
test:  ## Run all tests
	$(PYTEST) -v

.PHONY: test-fast
test-fast:  ## Run tests without timeout (faster iteration)
	$(PYTEST) -v --timeout=0

.PHONY: test-file
test-file:  ## Run specific test file: make test-file FILE=tests/harness/test_state.py
	$(PYTEST) $(FILE) -v

.PHONY: check
check: lint typecheck test  ## Run all checks (lint + typecheck + test)

##@ Code Quality

.PHONY: lint
lint:  ## Check code style and quality (no auto-fix)
	@find $(SRC_DIR) $(TEST_DIR) -name '*.py' -exec $(PYUPGRADE) --py313-plus {} +
	$(RUFF) check $(SRC_DIR) $(TEST_DIR)
	$(RUFF) format --check $(SRC_DIR) $(TEST_DIR)

.PHONY: typecheck
typecheck:  ## Run type checking with mypy
	$(MYPY) $(SRC_DIR)

.PHONY: format
format:  ## Auto-format code
	$(RUFF) format $(SRC_DIR) $(TEST_DIR)
	$(RUFF) check --fix $(SRC_DIR) $(TEST_DIR)

##@ Build

.PHONY: build
build:  ## Build wheel for distribution
	$(UV) build

##@ Cleanup

.PHONY: clean
clean:  ## Remove build artifacts and caches
	$(RM) -r build dist
	$(RM) -r *.egg-info src/*.egg-info
	$(RM) -r .pytest_cache .ruff_cache .mypy_cache
	$(RM) -r __pycache__ src/harness/__pycache__ tests/__pycache__ tests/harness/__pycache__
	$(RM) -r .coverage htmlcov
	@echo "Cleaned"

.PHONY: clean-all
clean-all: clean  ## Remove everything including venv
	$(RM) -r .venv
	@echo "Cleaned all (including .venv)"

##@ Help

.PHONY: help
help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
