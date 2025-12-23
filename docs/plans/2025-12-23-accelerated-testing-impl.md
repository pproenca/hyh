# Accelerated Testing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce local test feedback from ~53s to <5s for typical edits using testmon, and ~15-20s for full suite using xdist.

**Architecture:** Add three pytest plugins (xdist, testmon, asyncio), update pytest config for testmon-by-default, restructure Makefile targets to separate dev flow (fast) from CI flow (comprehensive).

**Tech Stack:** pytest-xdist, pytest-testmon, pytest-asyncio, Python 3.14 free-threaded

---

## Task 1: Add New Dependencies

**Files:**
- Modify: `pyproject.toml:40-53`

**Step 1: Add the three new dependencies to dev group**

In `pyproject.toml`, replace lines 40-53:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-timeout>=2.0",
    "pytest-xdist>=3.5",
    "pytest-testmon>=2.1",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
    "pre-commit>=4.0",
    "pyupgrade>=3.19",
    "pytest-cov>=7.0.0",
    "ty",
    "big-o>=0.11.0",
    "hypothesis>=6.100.0",
    "time-machine>=2.10.0",
    "pytest-benchmark>=4.0",
    "pytest-memray>=1.7",
]
```

**Step 2: Run uv sync to install new deps**

Run: `uv sync`

Expected: Dependencies install successfully, no conflicts.

**Step 3: Verify plugins are available**

Run: `uv run pytest --version`

Expected: Output includes pytest version. No import errors.

Run: `uv run python -c "import xdist; import pytest_testmon; import pytest_asyncio; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
build(deps): add pytest-xdist, pytest-testmon, pytest-asyncio

- pytest-xdist>=3.5: Parallel test execution across workers
- pytest-testmon>=2.1: Run only tests affected by code changes
- pytest-asyncio>=0.24: Async test support for daemon I/O tests
EOF
)"
```

---

## Task 2: Update Pytest Configuration

**Files:**
- Modify: `pyproject.toml:76-84`

**Step 1: Update pytest.ini_options with new config**

Replace lines 76-84 in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
# Default: testmon for fast dev feedback (no coverage overhead)
addopts = "--testmon --timeout=30 --benchmark-disable"
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "benchmark: marks benchmark tests (run with 'make benchmark')",
    "memcheck: marks memory profiling tests (run with 'make memcheck')",
]
# pytest-asyncio: auto-detect async tests without explicit markers
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

**Step 2: Run pytest to verify config is valid**

Run: `uv run pytest --collect-only -q 2>&1 | tail -5`

Expected: Shows test count, no config errors. May show testmon initializing database.

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
config(pytest): enable testmon by default, add asyncio auto-mode

- Remove --cov from default addopts (20% overhead reduction)
- Add --testmon for affected-test-only runs
- Add asyncio_mode=auto to detect async tests automatically
- Add asyncio_default_fixture_loop_scope=function
EOF
)"
```

---

## Task 3: Add .testmondata to .gitignore

**Files:**
- Modify: `.gitignore:35-41`

**Step 1: Add testmon database to gitignore**

After line 41 (after `.hypothesis/`), add:

```
.testmondata
```

**Step 2: Verify it's ignored**

Run: `echo ".testmondata" >> .gitignore && git status`

Expected: `.gitignore` shows as modified, no `.testmondata` in untracked.

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore(gitignore): add .testmondata (pytest-testmon database)"
```

---

## Task 4: Update Makefile Test Targets

**Files:**
- Modify: `Makefile:67-98`

**Step 1: Replace the Testing section in Makefile**

Replace lines 67-98 with:

```makefile
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
	$(PYTEST) --testmon-noselect --testmon-forceselect -v

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
```

**Step 2: Verify make targets work**

Run: `make help | grep -A 20 "Testing"`

Expected: Shows new test targets with descriptions.

Run: `make test 2>&1 | head -20`

Expected: Pytest runs with testmon enabled. First run collects all tests.

**Step 3: Commit**

```bash
git add Makefile
git commit -m "$(cat <<'EOF'
build(makefile): restructure test targets for speed

- make test: testmon-only (2-5s for typical edits)
- make test-all: xdist parallel with 4 workers (~15-20s)
- make test-seq: sequential full suite (debugging flaky tests)
- make test-reset: force testmon database rebuild
- make check: now uses test-all for comprehensive CI
EOF
)"
```

---

## Task 5: Add xdist Worker ID Fixture

**Files:**
- Modify: `tests/hyh/conftest.py:369-391`

**Step 1: Add worker_id fixture after git_template_dir fixture**

After line 391 (end of `fast_worktree` fixture), add:

```python


@pytest.fixture(scope="session")
def worker_id(request: pytest.FixtureRequest) -> str:
    """Return xdist worker id or 'master' for non-parallel runs.

    Useful for creating worker-specific resources when running
    tests in parallel with pytest-xdist.
    """
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"
```

**Step 2: Verify fixture is available**

Run: `uv run pytest --collect-only -q 2>&1 | tail -3`

Expected: No errors. Tests still collect.

**Step 3: Commit**

```bash
git add tests/hyh/conftest.py
git commit -m "test(conftest): add worker_id fixture for xdist compatibility"
```

---

## Task 6: Add Async Socket Helper

**Files:**
- Modify: `tests/hyh/conftest.py:1-20` (imports)
- Modify: `tests/hyh/conftest.py:200-218` (after send_command)

**Step 1: Add asyncio import**

At line 9, after `import json`, add:

```python
import asyncio
```

**Step 2: Add async_send_command function after send_command**

After line 218 (end of `send_command` function), add:

```python


async def async_send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
    """Async version of send_command using asyncio streams.

    Use this in async tests for non-blocking socket communication
    with the daemon.

    Args:
        socket_path: Path to Unix socket.
        command: Command dict to send.
        timeout: Timeout in seconds.

    Returns:
        Response dict from daemon.

    Raises:
        asyncio.TimeoutError: If connection or response times out.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(socket_path),
        timeout=timeout,
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

**Step 3: Verify no syntax errors**

Run: `uv run python -c "from tests.hyh.conftest import async_send_command; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add tests/hyh/conftest.py
git commit -m "test(conftest): add async_send_command for async daemon tests"
```

---

## Task 7: Verify Full Integration

**Step 1: Run testmon to build initial database**

Run: `make test`

Expected: All tests run (first run builds testmon database). Takes ~53s.

**Step 2: Make a trivial change and re-run**

Run: `echo "# test" >> src/hyh/__init__.py && make test`

Expected: Only tests depending on `__init__.py` run. Should be <5s.

Run: `git checkout src/hyh/__init__.py`

**Step 3: Run parallel full suite**

Run: `make test-all`

Expected: Tests run with 4 workers. Should complete in ~15-20s.

**Step 4: Run sequential for comparison**

Run: `make test-seq`

Expected: Tests run sequentially with coverage. ~53s.

**Step 5: Commit any remaining changes**

```bash
git status
# If clean, no action needed
```

---

## Task 8: Update Clean Target for Testmon

**Files:**
- Modify: `Makefile:161-168`

**Step 1: Add .testmondata to clean target**

Replace lines 161-168:

```makefile
.PHONY: clean
clean:  ## Remove build artifacts, caches, and venv
	$(RM) -r build dist .venv
	$(RM) -r *.egg-info src/*.egg-info .eggs
	$(RM) -r .pytest_cache .ruff_cache .ty_cache .benchmarks
	$(RM) -r __pycache__ src/hyh/__pycache__ tests/__pycache__ tests/hyh/__pycache__
	$(RM) -r .coverage htmlcov .testmondata
	@echo "Cleaned"
```

**Step 2: Verify clean works**

Run: `make clean && ls -la .testmondata 2>&1`

Expected: "No such file or directory" (testmondata was removed).

**Step 3: Commit**

```bash
git add Makefile
git commit -m "chore(makefile): add .testmondata to clean target"
```

---

## Summary

After completing all tasks:

| Command | Behavior | Expected Time |
|---------|----------|---------------|
| `make test` | Testmon: affected tests only | 2-5s |
| `make test-all` | xdist: 4 workers, coverage | 15-20s |
| `make test-seq` | Sequential, coverage | ~53s |
| `make test-reset` | Rebuild testmon database | ~53s |
| `make check` | lint + typecheck + test-all | ~30s |
