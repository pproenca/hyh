# Development Commands

## Setup
```bash
make install          # Install all dependencies (uv sync --dev)
```

## Testing
```bash
make test             # Run all tests with timeout (30s default)
make test-fast        # Run tests without timeout
make test-file FILE=tests/harness/test_state.py  # Run specific file
pytest tests/harness/test_state.py::test_claim_task_atomic -v  # Specific test
```

## Code Quality
```bash
make check            # Run all checks (lint + typecheck + test)
make lint             # Check style (ruff check + format check)
make typecheck        # Run mypy strict
make format           # Auto-format code (ruff format + check --fix)
```

## Development
```bash
make dev              # Start the daemon
make shell            # Interactive Python with project loaded
harness               # CLI entry point (after install)
```

## Build & Clean
```bash
make build            # Build wheel
make clean            # Remove caches and artifacts
make clean-all        # Remove everything including .venv
```

## Direct UV Commands
```bash
uv run pytest -v                    # Run tests
uv run ruff check src tests         # Lint
uv run ruff format src tests        # Format
uv run mypy src                     # Type check
uv run python -m harness.daemon     # Start daemon
```

## Git (macOS/Darwin)
Standard Unix commands: `git`, `ls`, `cd`, `grep`, `find`
Note: `find` on macOS is BSD find (differs from GNU find)
