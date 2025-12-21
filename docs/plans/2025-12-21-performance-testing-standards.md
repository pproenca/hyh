# Performance Testing Standards Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-21-performance-testing-standards.md` to implement task-by-task.

**Goal:** Integrate pytest-benchmark and pytest-memray into the test suite with CI enforcement and convenient local development commands.

**Architecture:** Add pytest plugins to dev dependencies, configure global memory baseline with per-test overrides, convert existing manual timing tests to pytest-benchmark, and add Makefile targets for developer visibility. The big-O library is already integrated via `test_complexity.py`.

**Tech Stack:** pytest-benchmark >= 4.0, pytest-memray >= 1.4, existing big-o >= 0.11.0

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent: dependencies + pytest config (no file overlap) |
| Group 2 | 3 | Makefile targets (depends on deps being installed) |
| Group 3 | 4 | Migrate test_performance.py to pytest-benchmark |
| Group 4 | 5 | Add memory profiling tests |
| Group 5 | 6 | Code review |

---

### Task 1: Add pytest-benchmark and pytest-memray Dependencies

**Files:**
- Modify: `pyproject.toml:14-25`

**Step 1: Add dependencies to pyproject.toml** (2-5 min)

Open `pyproject.toml` and add these two lines after line 24 (after `time-machine>=2.10.0`):

```toml
    "pytest-benchmark>=4.0",
    "pytest-memray>=1.7",
```

The full `[dependency-groups]` section should look like:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-timeout>=2.0",
    "ruff>=0.8",
    "pre-commit>=4.0",
    "pyupgrade>=3.19",
    "pytest-cov>=7.0.0",
    "mypy>=1.0",
    "big-o>=0.11.0",
    "hypothesis>=6.100.0",
    "time-machine>=2.10.0",
    "pytest-benchmark>=4.0",
    "pytest-memray>=1.7",
]
```

**Step 2: Sync dependencies** (30 sec)

```bash
uv sync --dev
```

Expected: Successfully resolved and installed packages

**Step 3: Verify installation** (30 sec)

```bash
uv run python -c "import pytest_benchmark; import memray; print('OK')"
```

Expected: `OK`

**Step 4: Commit** (30 sec)

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add pytest-benchmark and pytest-memray"
```

---

### Task 2: Configure Pytest Markers and Options

**Files:**
- Modify: `pyproject.toml:44-50`

**Step 1: Add benchmark and memcheck markers** (2-5 min)

Update the `[tool.pytest.ini_options]` section in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "--timeout=30 --benchmark-disable"
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "benchmark: marks benchmark tests (run with 'make benchmark')",
    "memcheck: marks memory profiling tests (run with 'make memcheck')",
]

[tool.pytest-benchmark]
disable_gc = true
warmup = true
min_rounds = 5
```

Key settings:
- `--benchmark-disable` in addopts: Benchmarks are skipped by default (don't slow down regular test runs)
- `disable_gc = true`: Disable garbage collection during benchmarks for consistent measurements
- `warmup = true`: Run warmup iterations before measuring
- `min_rounds = 5`: At least 5 rounds for statistical significance

**Step 2: Run pytest to verify config** (30 sec)

```bash
uv run pytest --co -q 2>&1 | head -20
```

Expected: No errors, tests collected normally

**Step 3: Verify benchmark marker works** (30 sec)

```bash
uv run pytest -v -m benchmark --collect-only 2>&1 | tail -5
```

Expected: `no tests ran` or `0 selected` (no tests marked yet)

**Step 4: Commit** (30 sec)

```bash
git add pyproject.toml
git commit -m "build(pytest): configure benchmark and memcheck markers"
```

---

### Task 3: Add Makefile Targets for Benchmarking and Memory Profiling

**Files:**
- Modify: `Makefile:66-81`

**Step 1: Add benchmark and memcheck targets** (2-5 min)

Add these targets after line 78 (after `test-file` target), before the `check` target:

```makefile
.PHONY: benchmark
benchmark:  ## Run benchmark tests
	$(PYTEST) -v -m benchmark --benchmark-enable --benchmark-autosave

.PHONY: memcheck
memcheck:  ## Run memory profiling tests
	$(PYTEST) -v -m memcheck --memray

.PHONY: perf
perf: benchmark memcheck  ## Run all performance tests (benchmark + memory)
```

**Step 2: Verify Makefile syntax** (30 sec)

```bash
make help 2>&1 | grep -E "(benchmark|memcheck|perf)"
```

Expected:
```
  benchmark       Run benchmark tests
  memcheck        Run memory profiling tests
  perf            Run all performance tests (benchmark + memory)
```

**Step 3: Test that targets work (expect no tests yet)** (30 sec)

```bash
make benchmark 2>&1 | tail -3
```

Expected: `0 passed` or `no tests ran` (no benchmarks yet)

**Step 4: Commit** (30 sec)

```bash
git add Makefile
git commit -m "build(make): add benchmark, memcheck, and perf targets"
```

---

### Task 4: Migrate test_performance.py to pytest-benchmark

**Files:**
- Modify: `tests/harness/test_performance.py`

**Step 1: Create benchmark fixtures in conftest.py** (2-5 min)

Add this fixture to `tests/harness/conftest.py` (at the end of the file):

```python
@pytest.fixture
def benchmark_state_manager(tmp_path):
    """Pre-configured StateManager for benchmark tests."""
    return StateManager(tmp_path)


@pytest.fixture
def large_dag_state(tmp_path):
    """Create a 1000-task DAG for benchmark tests.

    Returns (manager, state) tuple where state has linear chain dependencies.
    """
    manager = StateManager(tmp_path)
    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        dependencies = [f"task-{i - 1}"] if i > 0 else []
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=dependencies,
            timeout_seconds=600,
        )
    state = WorkflowState(tasks=tasks)
    manager.save(state)
    return manager, state
```

Also add imports at the top of conftest.py if not present:

```python
from harness.state import Task, TaskStatus, WorkflowState
```

**Step 2: Run conftest to verify no syntax errors** (30 sec)

```bash
uv run python -c "import tests.harness.conftest; print('OK')"
```

Expected: `OK`

**Step 3: Rewrite test_performance.py with pytest-benchmark** (5 min)

Replace the entire contents of `tests/harness/test_performance.py`:

```python
"""Performance regression tests for Harness using pytest-benchmark.

These tests verify O(V+E) and O(k) complexity guarantees as documented in CLAUDE.md.
Ensures claim_task, tail(), and validate_dag maintain performance characteristics
at scale.

Run with: make benchmark
"""

import pytest

from harness.state import StateManager, Task, TaskStatus, WorkflowState
from harness.trajectory import TrajectoryLogger


pytestmark = pytest.mark.benchmark


# =============================================================================
# StateManager Benchmarks
# =============================================================================


@pytest.fixture
def dag_1000_linear(tmp_path):
    """1000-task DAG with linear chain dependencies (O(V+E) where V=1000, E=999)."""
    manager = StateManager(tmp_path)
    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        dependencies = [f"task-{i - 1}"] if i > 0 else []
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=dependencies,
            timeout_seconds=600,
        )
    state = WorkflowState(tasks=tasks)
    manager.save(state)
    return manager


@pytest.fixture
def dag_1000_groups(tmp_path):
    """1000-task DAG with 100 independent groups (10 tasks each)."""
    manager = StateManager(tmp_path)
    tasks = {}
    for group in range(100):
        for i in range(10):
            task_id = f"group-{group}-task-{i}"
            dependencies = [f"group-{group}-task-{i - 1}"] if i > 0 else []
            tasks[task_id] = Task(
                id=task_id,
                description=f"Group {group} task {i}",
                status=TaskStatus.PENDING,
                dependencies=dependencies,
            )
    state = WorkflowState(tasks=tasks)
    manager.save(state)
    return manager


@pytest.fixture
def dag_900_completed(tmp_path):
    """1000 tasks where 900 are completed, 100 pending."""
    manager = StateManager(tmp_path)
    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        status = TaskStatus.COMPLETED if i < 900 else TaskStatus.PENDING
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=status,
            dependencies=[],
            timeout_seconds=600,
        )
    state = WorkflowState(tasks=tasks)
    manager.save(state)
    return manager


def test_claim_task_linear_dag(benchmark, dag_1000_linear):
    """claim_task should maintain O(V+E) complexity at 1000 tasks.

    Per CLAUDE.md Section VIII: For N < 1000 tasks, O(V+E) iteration is acceptable.
    """
    manager = dag_1000_linear

    def claim():
        # Reload state to reset for each benchmark iteration
        state = manager.load()
        # Reset first task to pending for re-claiming
        state.tasks["task-0"] = state.tasks["task-0"].model_copy(
            update={"status": TaskStatus.PENDING, "claimed_by": None}
        )
        manager.save(state)
        return manager.claim_task("worker-1")

    result = benchmark(claim)
    assert result.task is not None
    assert result.task.id == "task-0"


def test_claim_task_grouped_dag(benchmark, dag_1000_groups):
    """claim_task should efficiently find claimable tasks in complex DAGs."""
    manager = dag_1000_groups

    def claim():
        state = manager.load()
        # Reset all group-0 tasks to pending
        for i in range(10):
            task_id = f"group-0-task-{i}"
            state.tasks[task_id] = state.tasks[task_id].model_copy(
                update={"status": TaskStatus.PENDING, "claimed_by": None}
            )
        manager.save(state)
        return manager.claim_task("worker-1")

    result = benchmark(claim)
    assert result.task is not None
    assert result.task.id.endswith("-task-0")


def test_claim_task_mostly_completed(benchmark, dag_900_completed):
    """claim_task should efficiently skip completed tasks."""
    manager = dag_900_completed

    def claim():
        state = manager.load()
        # Reset one pending task
        state.tasks["task-900"] = state.tasks["task-900"].model_copy(
            update={"status": TaskStatus.PENDING, "claimed_by": None}
        )
        manager.save(state)
        return manager.claim_task("worker-1")

    result = benchmark(claim)
    assert result.task is not None
    assert int(result.task.id.split("-")[1]) >= 900


# =============================================================================
# TrajectoryLogger Benchmarks
# =============================================================================


@pytest.fixture
def large_trajectory(tmp_path):
    """50K events trajectory file (>5MB)."""
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)
    for i in range(50_000):
        logger.log({"event": f"event-{i}", "data": "x" * 100})
    return logger


@pytest.fixture
def large_payload_trajectory(tmp_path):
    """10K events with 1KB payloads (>10MB)."""
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)
    large_payload = "x" * 1000
    for i in range(10_000):
        logger.log({
            "event": i,
            "large_data": large_payload,
            "metadata": {"index": i, "timestamp": i * 1000},
        })
    return logger


def test_trajectory_tail_50k(benchmark, large_trajectory):
    """tail() should be O(k) not O(N) on large files.

    Per CLAUDE.md Section VIII: O(k) reverse seek where k = block size.
    """
    result = benchmark(large_trajectory.tail, 10)
    assert len(result) == 10
    assert result[-1]["event"] == "event-49999"
    assert result[0]["event"] == "event-49990"


def test_trajectory_tail_large_payloads(benchmark, large_payload_trajectory):
    """tail() should maintain O(k) even with large event payloads."""
    result = benchmark(large_payload_trajectory.tail, 5)
    assert len(result) == 5
    assert result[-1]["event"] == 9999


# =============================================================================
# DAG Validation Benchmarks
# =============================================================================


@pytest.fixture
def diamond_dag():
    """1000-node DAG with diamond structure (multiple paths between nodes)."""
    tasks = {}
    tasks["root"] = Task(
        id="root",
        description="Root task",
        status=TaskStatus.PENDING,
        dependencies=[],
    )
    prev_layer = ["root"]
    for layer in range(1, 4):
        current_layer = []
        for i in range(250):
            task_id = f"layer-{layer}-task-{i}"
            dependencies = prev_layer[: min(3, len(prev_layer))]
            tasks[task_id] = Task(
                id=task_id,
                description=f"Layer {layer} task {i}",
                status=TaskStatus.PENDING,
                dependencies=dependencies,
            )
            current_layer.append(task_id)
        prev_layer = current_layer

    while len(tasks) < 1000:
        task_id = f"extra-task-{len(tasks)}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Extra task {len(tasks)}",
            status=TaskStatus.PENDING,
            dependencies=["root"],
        )

    return WorkflowState(tasks=tasks)


def test_dag_validation_1000_nodes(benchmark, diamond_dag):
    """validate_dag should complete in reasonable time for 1000 nodes.

    Per CLAUDE.md Section VII: Defensive Graph Construction requires cycle detection.
    """
    benchmark(diamond_dag.validate_dag)


def test_dag_cycle_detection(benchmark):
    """validate_dag should quickly detect cycles even in large graphs."""
    tasks = {
        "task-a": Task(
            id="task-a",
            description="Task A",
            status=TaskStatus.PENDING,
            dependencies=["task-c"],
        ),
        "task-b": Task(
            id="task-b",
            description="Task B",
            status=TaskStatus.PENDING,
            dependencies=["task-a"],
        ),
        "task-c": Task(
            id="task-c",
            description="Task C",
            status=TaskStatus.PENDING,
            dependencies=["task-b"],
        ),
    }
    for i in range(997):
        task_id = f"task-{i}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=[],
        )
    state = WorkflowState(tasks=tasks)

    def detect_cycle():
        try:
            state.validate_dag()
        except ValueError:
            pass

    benchmark(detect_cycle)
```

**Step 4: Run the new benchmarks** (30 sec)

```bash
make benchmark 2>&1 | tail -20
```

Expected: Benchmark results with timing stats (min, max, mean, etc.)

**Step 5: Commit** (30 sec)

```bash
git add tests/harness/test_performance.py tests/harness/conftest.py
git commit -m "test(benchmark): migrate performance tests to pytest-benchmark"
```

---

### Task 5: Add Memory Profiling Tests with pytest-memray

**Files:**
- Create: `tests/harness/test_memory.py`

**Step 1: Create memory profiling test file** (5 min)

Create `tests/harness/test_memory.py`:

```python
"""Memory profiling tests for Harness using pytest-memray.

These tests verify memory bounds for critical operations, ensuring
no unbounded memory growth or leaks.

Run with: make memcheck
"""

import pytest

from harness.state import StateManager, Task, TaskStatus, WorkflowState
from harness.trajectory import TrajectoryLogger


pytestmark = pytest.mark.memcheck


# =============================================================================
# TrajectoryLogger Memory Tests
# =============================================================================


@pytest.mark.limit_memory("100 MB")
def test_trajectory_log_bounded_memory(tmp_path):
    """Logging 50K events should not allocate unbounded memory.

    The TrajectoryLogger appends to disk; memory should remain bounded
    regardless of how many events are logged.
    """
    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")
    for i in range(50_000):
        logger.log({"event": f"event-{i}", "data": "x" * 100})


@pytest.mark.limit_memory("50 MB")
def test_trajectory_tail_bounded_memory(tmp_path):
    """tail() should not load entire file into memory.

    Reading last 10 events from a 50K event file should use O(k) memory,
    not O(N) where N is file size.
    """
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)

    # Write 50K events first
    for i in range(50_000):
        logger.log({"event": f"event-{i}", "data": "x" * 100})

    # This should not load the entire file
    result = logger.tail(10)
    assert len(result) == 10


# =============================================================================
# StateManager Memory Tests
# =============================================================================


@pytest.mark.limit_memory("100 MB")
def test_state_save_load_bounded_memory(tmp_path):
    """Save/load cycle for 1000 tasks should have bounded memory."""
    manager = StateManager(tmp_path)

    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i} with some description text",
            status=TaskStatus.PENDING,
            dependencies=[],
            timeout_seconds=600,
        )

    state = WorkflowState(tasks=tasks)
    manager.save(state)

    # Reload multiple times to check for leaks
    for _ in range(10):
        loaded = manager.load()
        assert len(loaded.tasks) == 1000


@pytest.mark.limit_memory("100 MB")
def test_claim_task_repeated_bounded_memory(tmp_path):
    """Repeated claim_task operations should not leak memory."""
    manager = StateManager(tmp_path)

    tasks = {}
    for i in range(100):
        task_id = f"task-{i}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=[],
            timeout_seconds=600,
        )

    state = WorkflowState(tasks=tasks)
    manager.save(state)

    # Claim all 100 tasks
    for i in range(100):
        result = manager.claim_task(f"worker-{i}")
        if result.task:
            manager.complete_task(result.task.id)


# =============================================================================
# DAG Validation Memory Tests
# =============================================================================


@pytest.mark.limit_memory("50 MB")
def test_dag_validation_bounded_memory():
    """DAG validation for 1000 nodes should use bounded memory.

    DFS-based cycle detection should not create excessive intermediate state.
    """
    tasks = {}
    tasks["root"] = Task(
        id="root",
        description="Root task",
        status=TaskStatus.PENDING,
        dependencies=[],
    )

    prev_layer = ["root"]
    for layer in range(1, 4):
        current_layer = []
        for i in range(250):
            task_id = f"layer-{layer}-task-{i}"
            dependencies = prev_layer[: min(3, len(prev_layer))]
            tasks[task_id] = Task(
                id=task_id,
                description=f"Layer {layer} task {i}",
                status=TaskStatus.PENDING,
                dependencies=dependencies,
            )
            current_layer.append(task_id)
        prev_layer = current_layer

    while len(tasks) < 1000:
        task_id = f"extra-task-{len(tasks)}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Extra task {len(tasks)}",
            status=TaskStatus.PENDING,
            dependencies=["root"],
        )

    state = WorkflowState(tasks=tasks)
    state.validate_dag()
```

**Step 2: Run the memory tests** (30 sec)

```bash
make memcheck 2>&1 | tail -20
```

Expected: Tests pass with memray profiling output

**Step 3: Verify memory limits are enforced** (30 sec)

```bash
uv run pytest tests/harness/test_memory.py -v --memray 2>&1 | tail -30
```

Expected: Tests pass, memory usage reported

**Step 4: Commit** (30 sec)

```bash
git add tests/harness/test_memory.py
git commit -m "test(memory): add pytest-memray memory profiling tests"
```

---

### Task 6: Code Review

**Files:** All files modified in Tasks 1-5

Review checklist:
- [ ] Dependencies added correctly to pyproject.toml
- [ ] Pytest markers configured properly
- [ ] Makefile targets work (`make benchmark`, `make memcheck`, `make perf`)
- [ ] Benchmarks produce meaningful timing data
- [ ] Memory limits are reasonable (not too strict, not too loose)
- [ ] All tests pass with `make check`

**Step 1: Run full test suite** (30 sec)

```bash
make check
```

Expected: All checks pass (lint + typecheck + test)

**Step 2: Run performance tests** (30 sec)

```bash
make perf
```

Expected: Benchmarks and memory tests pass

**Step 3: Verify no regressions** (30 sec)

```bash
uv run pytest -v --timeout=60 2>&1 | tail -20
```

Expected: All tests pass
