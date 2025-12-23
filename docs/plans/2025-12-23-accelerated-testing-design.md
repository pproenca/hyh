# Accelerated Local Testing Design

> **Goal:** Reduce local test feedback loop from ~53s to <5s for typical edits, and ~15-20s for full suite.

**Date:** 2025-12-23

## Overview

Add pytest-xdist, pytest-testmon, and pytest-asyncio to accelerate local testing:

- **testmon** - Default for `make test`, runs only tests affected by code changes
- **xdist** - For `make test-all`, parallelizes full suite across 4 workers
- **asyncio** - Enable async test patterns for I/O-bound daemon tests

## Dependencies

```toml
[dependency-groups]
dev = [
    # ... existing ...
    "pytest-xdist>=3.5",      # Parallel test execution
    "pytest-testmon>=2.1",    # Changed-file test selection
    "pytest-asyncio>=0.24",   # Async test support
]
```

## Pytest Configuration

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
# Default: testmon for fast dev feedback (no xdist, no coverage)
addopts = "--testmon --timeout=30 --benchmark-disable"
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "benchmark: marks benchmark tests (run with 'make benchmark')",
    "memcheck: marks memory profiling tests (run with 'make memcheck')",
]
# pytest-asyncio configuration
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

**Key changes from current:**
- Removed `--cov` from default (adds ~20% overhead)
- Added `--testmon` as default
- Added asyncio auto-mode

## Makefile Targets

```makefile
# Fast dev feedback - testmon runs only affected tests (single-process)
test:
	uv run pytest

# Full parallel suite - xdist with 4 workers, tighter timeout
test-all:
	uv run pytest --testmon-noselect -n 4 --timeout=10 --cov=hyh --cov-report=term-missing

# Force full suite without parallelization (debugging flaky tests)
test-seq:
	uv run pytest --testmon-noselect --timeout=30 --cov=hyh --cov-report=term-missing

# Reset testmon database (after major refactors)
test-reset:
	uv run pytest --testmon-noselect --testmon-forceselect

# Single file (bypass testmon)
test-file:
	uv run pytest $(FILE) --testmon-noselect

# Benchmarks (bypass testmon)
benchmark:
	uv run pytest -m benchmark --benchmark-enable --testmon-noselect

# Memory profiling (bypass testmon)
memcheck:
	uv run pytest -m memcheck --memray --testmon-noselect
```

## Async Test Infrastructure

Add to `tests/hyh/conftest.py`:

```python
async def async_send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
    """Async version of send_command using asyncio streams."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(socket_path),
        timeout=timeout
    )
    try:
        writer.write(json.dumps(command).encode() + b"\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return json.loads(response.decode().strip())
    finally:
        writer.close()
        await writer.wait_closed()
```

Async conversion is incremental - sync and async patterns coexist.

## xdist Worker Isolation

Add optional fixture for worker-specific resources:

```python
@pytest.fixture(scope="session")
def worker_id(request):
    """Return xdist worker id or 'master' for non-parallel runs."""
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"
```

Existing fixtures already use UUID-based socket paths - no conflicts expected.

## Housekeeping

Add to `.gitignore`:
```
.testmondata
```

## Expected Performance

| Scenario | Before | After |
|----------|--------|-------|
| `make test` (typical edit) | 53s | 2-5s |
| `make test-all` (full suite) | 53s | 15-20s |
| `make test-file FILE=...` | 5-10s | 5-10s |

## Python 3.14 Free-Threaded Compatibility

All plugins confirmed compatible:

| Plugin | Why Compatible |
|--------|----------------|
| pytest-xdist | Uses multiprocessing, not threading |
| pytest-testmon | Pure Python + SQLite |
| pytest-asyncio | Official Python 3.14 support |

## References

- [pytest-xdist docs](https://pytest-xdist.readthedocs.io/)
- [pytest-testmon docs](https://testmon.org/)
- [pytest-asyncio docs](https://pytest-asyncio.readthedocs.io/)
- [Python Free-Threading Guide](https://py-free-threading.github.io/)
