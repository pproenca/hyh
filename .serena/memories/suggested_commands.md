# Suggested Commands

## Setup
```bash
make install          # Install all dependencies (uv sync --dev)
make install-global   # Install hyh globally (editable)
make uninstall-global # Remove global installation
```

## Development
```bash
make dev              # Start the daemon (development mode)
make shell            # Open interactive Python shell with project loaded
```

## Testing
```bash
make test             # Run all tests
make test-fast        # Run tests without timeout (faster iteration)
make test-file FILE=tests/hyh/test_state.py  # Run specific test file

# Performance testing
make benchmark        # Run benchmark tests
make memcheck         # Run memory profiling tests
make perf             # Run all performance tests (benchmark + memory)
```

## Code Quality
```bash
make lint             # Check code style and quality (no auto-fix)
make typecheck        # Run type checking with ty
make format           # Auto-format code with ruff
make check            # Run all checks (lint + typecheck + test)
```

## Build
```bash
make build            # Build wheel for distribution
```

## Cleanup
```bash
make clean            # Remove build artifacts, caches, and venv
```

## Running Tests Directly
```bash
uv run pytest -v                           # All tests
uv run pytest tests/hyh/test_state.py  # Specific file
uv run pytest -k "test_claim"              # By name pattern
uv run pytest -m "not slow"                # Exclude slow tests
uv run pytest -m benchmark --benchmark-enable  # Benchmarks only
```

## Linting Directly
```bash
uv run ruff check src tests              # Check only
uv run ruff check --fix src tests        # Check and fix
uv run ruff format src tests             # Format code
uv run ty check src                      # Type check
```

## System Commands (macOS/Darwin)
```bash
ls -la                  # List files with details
find . -name "*.py"     # Find Python files
grep -r "pattern" src/  # Search in files
git status              # Check git status
git diff                # Show changes
```
