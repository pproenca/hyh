# Development Commands

## Setup
```bash
make install              # Install all dependencies (uv sync --dev)
make install-global       # Install harness globally (editable)
make uninstall-global     # Remove global installation
```

## Development
```bash
make dev                  # Start daemon in development mode
make shell                # Open interactive Python shell with project loaded
```

## Testing
```bash
make test                 # Run all tests (pytest -v)
make test-fast            # Run tests without timeout
make test-file FILE=path  # Run specific test file
make benchmark            # Run benchmark tests (pytest -m benchmark)
make memcheck             # Run memory profiling tests (pytest -m memcheck --memray)
make perf                 # Run all performance tests
```

## Code Quality
```bash
make lint                 # Check code style (pyupgrade + ruff check + ruff format --check)
make typecheck            # Run mypy type checking
make format               # Auto-format code (ruff format + ruff check --fix)
make check                # Run all checks (lint + typecheck + test)
```

## Build & Cleanup
```bash
make build                # Build wheel for distribution
make clean                # Remove build artifacts and caches
make clean-all            # Remove everything including .venv
```

## Direct Tool Usage
```bash
uv run pytest -v                    # Run tests directly
uv run ruff check src tests         # Check linting
uv run ruff format src tests        # Format code
uv run mypy src                     # Type check
```

## Git Workflow
Standard git commands work as expected. The project uses pre-commit hooks for pyupgrade.
