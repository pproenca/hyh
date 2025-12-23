# Threading & Big-O Test Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix critical issues in threading and big-O complexity tests identified during audit.

**Architecture:** Surgical fixes to existing test files—no new features, only correctness improvements. Remove misleading Hypothesis stateful tests, add Barrier timeouts, fix big-O measurement parameters.

**Tech Stack:** pytest, big_o library, threading module, Python 3.14 free-threaded

---

## Task 1: Remove Hypothesis Stateful Tests

**Why:** These tests run single-threaded and provide false confidence about concurrency safety. The deterministic threading tests already cover these invariants better.

**Files:**
- Modify: `tests/hyh/test_freethreading.py:30-34` (imports)
- Delete: `tests/hyh/test_freethreading.py:371-508` (Hypothesis classes + complexity docstring)

**Step 1: Update imports - remove Hypothesis**

Change lines 30-32 from:
```python
from hypothesis import settings
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule
```

To:
```python
# Hypothesis stateful tests removed - they run single-threaded and don't test
# true concurrency. See docs/plans/2025-12-23-threading-bigo-audit-design.md
```

**Step 2: Delete Hypothesis classes and complexity docstring**

Delete lines 371-508 entirely. This removes:
- `StateManagerStateMachine` class (lines 377-468)
- `TestStateManagerConcurrency` assignment (line 470)
- `ExtendedStateManagerStateMachine` class (lines 475-478)
- `TestStateManagerConcurrencyExtended` assignments (lines 481-483)
- Complexity analysis docstring (lines 486-508)

**Step 3: Update module docstring**

Change line 10 from:
```python
3. Hypothesis stateful testing for randomized operation sequences
```

To:
```python
3. Deterministic stress tests with synchronized thread start
```

**Step 4: Run tests to verify removal didn't break anything**

Run: `pytest tests/hyh/test_freethreading.py -v --tb=short`

Expected: All remaining tests pass (TestNoDoubleAssignment, TestHighContentionSerialization, TestMemoryVisibility, TestLockContention)

**Step 5: Commit**

```bash
git add tests/hyh/test_freethreading.py
git commit -m "test(freethreading): remove misleading Hypothesis stateful tests

Hypothesis stateful tests run single-threaded and don't test true
concurrency - they only test sequential correctness which unit tests
already cover. The deterministic threading tests with Barrier provide
stronger concurrency coverage.

See: docs/plans/2025-12-23-threading-bigo-audit-design.md"
```

---

## Task 2: Add Barrier Timeouts to test_freethreading.py

**Why:** Without timeout, if one thread crashes before `wait()`, all others deadlock forever.

**Files:**
- Modify: `tests/hyh/test_freethreading.py:73,163,339`

**Step 1: Fix line 73**

Change:
```python
            barrier = threading.Barrier(num_threads)
```

To:
```python
            barrier = threading.Barrier(num_threads, timeout=30.0)
```

**Step 2: Fix line 163**

Change:
```python
            barrier = threading.Barrier(num_threads)
```

To:
```python
            barrier = threading.Barrier(num_threads, timeout=30.0)
```

**Step 3: Fix line 339**

Change:
```python
            barrier = threading.Barrier(20)
```

To:
```python
            barrier = threading.Barrier(20, timeout=30.0)
```

**Step 4: Run tests**

Run: `pytest tests/hyh/test_freethreading.py -v --tb=short`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/hyh/test_freethreading.py
git commit -m "test(freethreading): add Barrier timeouts to prevent deadlocks

Per Python threading docs, Barrier without timeout can deadlock if one
thread crashes before wait(). 30s timeout prevents CI hangs."
```

---

## Task 3: Add Barrier Timeouts to Other Test Files

**Why:** Same deadlock prevention needed across all threading tests.

**Files:**
- Modify: `tests/hyh/test_state_machine.py:129,289,363`
- Modify: `tests/hyh/test_trajectory_boundaries.py:125,170,220,319`
- Modify: `tests/hyh/test_registry_stress.py:26`

**Step 1: Fix test_state_machine.py**

Line 129:
```python
                barrier = threading.Barrier(2, timeout=30.0)
```

Line 289:
```python
            barrier = threading.Barrier(10, timeout=30.0)
```

Line 363:
```python
            barrier = threading.Barrier(20, timeout=30.0)
```

**Step 2: Fix test_trajectory_boundaries.py**

Line 125:
```python
            barrier = threading.Barrier(num_threads, timeout=30.0)
```

Line 170:
```python
            barrier = threading.Barrier(num_threads, timeout=30.0)
```

Line 220:
```python
            barrier = threading.Barrier(num_threads, timeout=30.0)
```

Line 319:
```python
            barrier = threading.Barrier(num_threads, timeout=30.0)
```

**Step 3: Fix test_registry_stress.py**

Line 26:
```python
            barrier = threading.Barrier(10, timeout=30.0)
```

**Step 4: Run all modified tests**

Run: `pytest tests/hyh/test_state_machine.py tests/hyh/test_trajectory_boundaries.py tests/hyh/test_registry_stress.py -v --tb=short`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/hyh/test_state_machine.py tests/hyh/test_trajectory_boundaries.py tests/hyh/test_registry_stress.py
git commit -m "test: add Barrier timeouts across all threading tests

Consistent 30s timeout prevents CI hangs if threads crash before barrier."
```

---

## Task 4: Fix big-O n_repeats Parameter

**Why:** `n_repeats=3` is too low for reliable timing—library examples use 100. Using 50 as balance between reliability and test speed.

**Files:**
- Modify: `tests/hyh/test_complexity.py:40,75,121,166,213`

**Step 1: Update all n_repeats values**

Change all 5 occurrences of:
```python
            n_repeats=3,
```

To:
```python
            n_repeats=50,
```

**Step 2: Run complexity tests**

Run: `pytest tests/hyh/test_complexity.py -v --tb=short`

Expected: PASS (tests will take longer but be more reliable)

**Step 3: Commit**

```bash
git add tests/hyh/test_complexity.py
git commit -m "test(complexity): increase n_repeats from 3 to 50

Per big_o library docs, n_repeats=3 causes high timing variance.
50 repeats provides reliable complexity class detection while keeping
test runtime reasonable."
```

---

## Task 5: Fix Measurement Overhead in Complexity Tests

**Why:** State construction inside `measure_func` adds O(n) overhead to O(1) lookup measurement.

**Files:**
- Modify: `tests/hyh/test_complexity.py:91-131`

**Step 1: Refactor test_get_claimable_task_with_satisfied_deps**

Replace the entire test method (lines 91-131) with:

```python
    @pytest.mark.slow
    def test_get_claimable_task_with_satisfied_deps(self) -> None:
        """get_claimable_task should be O(1) when first task is claimable."""
        # Pre-build states at each size to exclude construction from timing
        sizes = [100, 500, 1000, 2000, 3000, 4000, 5000]
        prebuilt_states: dict[int, WorkflowState] = {}

        for n in sizes:
            tasks = {}
            # First task has no deps - immediately claimable
            tasks["claimable"] = Task(
                id="claimable",
                description="Claimable task",
                status=TaskStatus.PENDING,
                dependencies=(),
            )
            # Rest have deps on first task (will be blocked)
            for i in range(n):
                tasks[f"blocked-{i}"] = Task(
                    id=f"blocked-{i}",
                    description=f"Blocked task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=("claimable",),
                )
            prebuilt_states[n] = WorkflowState(tasks=tasks)

        def measure_func(n: int) -> None:
            n = int(n)
            # Find closest prebuilt size
            closest = min(sizes, key=lambda s: abs(s - n))
            state = prebuilt_states[closest]
            state.get_claimable_task()

        best, others = big_o.big_o(
            measure_func,
            big_o.datagen.n_,
            min_n=100,
            max_n=5000,
            n_measures=10,
            n_repeats=50,
        )

        # Should be constant or logarithmic - the actual lookup is O(1)
        acceptable = (
            big_o.complexities.Constant,
            big_o.complexities.Logarithmic,
        )
        assert isinstance(best, acceptable), (
            f"Expected O(1) or O(log n), got {best}. "
            f"Residuals: {[(type(c).__name__, r) for c, r in sorted(others.items(), key=lambda x: x[1])[:3]]}"
        )
```

**Step 2: Run test**

Run: `pytest tests/hyh/test_complexity.py::TestWorkflowStateComplexity::test_get_claimable_task_with_satisfied_deps -v`

Expected: PASS with O(1) or O(log n) detected

**Step 3: Commit**

```bash
git add tests/hyh/test_complexity.py
git commit -m "test(complexity): fix measurement overhead conflation

Pre-build WorkflowState objects outside timed function to measure
actual get_claimable_task() complexity without O(n) construction overhead.
Also tighten acceptable classes to O(1) or O(log n)."
```

---

## Task 6: Add sys.setswitchinterval Fixture

**Why:** On GIL-enabled Python builds, tests won't expose races. Aggressive switch interval forces more context switches.

**Files:**
- Modify: `tests/hyh/conftest.py` (add after line 20, before first function)

**Step 1: Add import**

After line 13 (`import threading`), add:
```python
import sys
```

**Step 2: Add fixture after imports section**

After line 20 (`import pytest`), add:

```python

# =============================================================================
# Free-Threading Compatibility
# =============================================================================


@pytest.fixture(autouse=True)
def aggressive_thread_switching():
    """Force frequent context switches to expose races on GIL-enabled builds.

    On free-threaded Python (3.13t/3.14t), this is a no-op since there's no GIL.
    On standard Python, setting switch interval to 1 microsecond forces the GIL
    to release frequently, making race conditions more likely to manifest.

    See: https://py-free-threading.github.io/testing/
    """
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.000001)  # 1 microsecond
    yield
    sys.setswitchinterval(old_interval)
```

**Step 3: Run a threading test to verify fixture works**

Run: `pytest tests/hyh/test_freethreading.py::TestNoDoubleAssignment::test_no_double_assignment_deterministic -v`

Expected: PASS

**Step 4: Commit**

```bash
git add tests/hyh/conftest.py
git commit -m "test(conftest): add aggressive_thread_switching fixture

Forces 1 microsecond GIL switch interval to expose races on standard
Python builds. No-op on free-threaded builds (no GIL).

See: https://py-free-threading.github.io/testing/"
```

---

## Task 7: Remove Artificial sleep in Stress Test

**Why:** `time.sleep(0.001)` masks real timing behavior. CPU-bound work behaves differently under free-threading.

**Files:**
- Modify: `tests/hyh/test_freethreading.py:173-174`

**Step 1: Remove sleep**

Delete these lines (around line 173-174):
```python
                        # Simulate work
                        time.sleep(0.001)
```

**Step 2: Run test to verify still passes**

Run: `pytest tests/hyh/test_freethreading.py::TestHighContentionSerialization::test_100_threads_5_tasks_serialization -v`

Expected: PASS (test now measures actual contention behavior)

**Step 3: Commit**

```bash
git add tests/hyh/test_freethreading.py
git commit -m "test(freethreading): remove artificial sleep from stress test

time.sleep masks real contention behavior. Removing it tests actual
lock contention under free-threading without artificial delays."
```

---

## Task 8: Final Verification

**Step 1: Run full test suite**

Run: `make check`

Expected: All tests pass, no regressions

**Step 2: Run slow tests specifically**

Run: `pytest tests/hyh/test_complexity.py tests/hyh/test_freethreading.py -v`

Expected: PASS

**Step 3: Final commit (if any cleanup needed)**

If all passes, the implementation is complete.

---

## Summary of Changes

| File | Change |
|------|--------|
| `test_freethreading.py` | Remove Hypothesis (~140 lines), add Barrier timeouts, remove sleep |
| `test_complexity.py` | Increase n_repeats, fix measurement overhead, tighten assertions |
| `test_state_machine.py` | Add Barrier timeouts |
| `test_trajectory_boundaries.py` | Add Barrier timeouts |
| `test_registry_stress.py` | Add Barrier timeout |
| `conftest.py` | Add aggressive_thread_switching fixture |

**Total commits:** 7
**Estimated time:** 30-45 minutes
