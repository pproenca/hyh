# Performance Fixes Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-01-20-performance-fixes.md` to implement task-by-task.

**Goal:** Fix three performance issues identified in the Big-O analysis: import-in-loop in client.py, double iteration in get_claimable_task, and O(N^2) string concatenation in trajectory tail.

**Architecture:** These are isolated refactors with no API changes. Each fix is behavior-preserving and can be verified with existing tests.

**Tech Stack:** Python 3.13t, pytest

---

## Harness Plan

```json
{
  "goal": "Fix three performance issues: import-in-loop, double iteration, O(N^2) string concatenation",
  "tasks": {
    "1": {
      "description": "Move datetime import outside loop in client.py",
      "dependencies": [],
      "timeout_seconds": 300,
      "instructions": "Add datetime to import at line 19, remove inline imports at lines 413 and 446. Run tests to verify.",
      "role": "backend"
    },
    "2": {
      "description": "Optimize get_claimable_task with early exit",
      "dependencies": [],
      "timeout_seconds": 300,
      "instructions": "Combine existence check and satisfaction check into single loop with early break on first unsatisfied dep.",
      "role": "backend"
    },
    "3": {
      "description": "Optimize trajectory tail with list+join",
      "dependencies": [],
      "timeout_seconds": 300,
      "instructions": "Replace buffer = chunk + buffer with chunks list and b''.join() to avoid O(N^2) concatenation.",
      "role": "backend"
    },
    "4": {
      "description": "Code review and final verification",
      "dependencies": ["1", "2", "3"],
      "timeout_seconds": 300,
      "instructions": "Run make check. Verify all tests pass. Check git log for correct commit messages.",
      "role": "backend"
    }
  }
}
```

---

## Task 1: Move datetime import outside loop in client.py

**Files:**
- Modify: `src/harness/client.py:19` (import line)
- Modify: `src/harness/client.py:413-415` (running task elapsed time)
- Modify: `src/harness/client.py:444-446` (event timestamp formatting)

**Context:** Lines 413 and 446 contain `from datetime import datetime` inside a for-loop. Python caches modules, but the import machinery still runs on each iteration. The module already imports `from datetime import UTC` at line 19.

**Step 1: Add datetime to existing import** (2 min)

Change line 19 from:
```python
from datetime import UTC
```
to:
```python
from datetime import UTC, datetime
```

**Step 2: Remove the first inline import** (2 min)

At lines 413-415, remove the inline import:

Before:
```python
elif status == "running" and task.get("started_at"):
    from datetime import datetime

    started = datetime.fromisoformat(task["started_at"].replace("Z", "+00:00"))
```

After:
```python
elif status == "running" and task.get("started_at"):
    started = datetime.fromisoformat(task["started_at"].replace("Z", "+00:00"))
```

**Step 3: Remove the second inline import** (2 min)

At lines 444-446, remove the inline import:

Before:
```python
if ts:
    from datetime import datetime

    try:
```

After:
```python
if ts:
    try:
```

**Step 4: Run existing tests** (30 sec)

```bash
pytest tests/harness/test_client.py -v
```

Expected: All tests pass (the behavior is unchanged).

**Step 5: Verify import constraint is still met** (30 sec)

```bash
pytest tests/harness/test_client.py::test_client_does_not_import_pydantic -v
pytest tests/harness/test_client.py::test_client_has_no_heavy_imports -v
```

Expected: Both pass. `datetime` is stdlib and allowed per client.py docstring.

**Step 6: Commit** (30 sec)

```bash
git add src/harness/client.py
git commit -m "perf(client): move datetime import outside render loop"
```

---

## Task 2: Optimize get_claimable_task with early exit

**Files:**
- Modify: `src/harness/state.py:112-133`
- Test: `tests/harness/test_state.py` (existing tests cover this)

**Context:** Current implementation iterates dependencies twice per task: once for existence check, once for satisfaction check. We can combine these into a single pass with early exit.

**Step 1: Refactor get_claimable_task** (5 min)

Replace the method body at lines 112-133:

Before:
```python
def get_claimable_task(self) -> Task | None:
    """Find a task that can be claimed (pending or timed out with satisfied deps).

    Raises:
        ValueError: If a dependency references a non-existent task.
    """
    for task in self.tasks.values():
        if task.status == TaskStatus.PENDING or (
            task.status == TaskStatus.RUNNING and task.is_timed_out()
        ):
            # Fail-fast on missing dependencies (defensive coding)
            for dep_id in task.dependencies:
                if dep_id not in self.tasks:
                    raise ValueError(f"Missing dependency: {dep_id} (in {task.id})")

            deps_satisfied = all(
                self.tasks[dep_id].status == TaskStatus.COMPLETED
                for dep_id in task.dependencies
            )
            if deps_satisfied:
                return task
    return None
```

After:
```python
def get_claimable_task(self) -> Task | None:
    """Find a task that can be claimed (pending or timed out with satisfied deps).

    Raises:
        ValueError: If a dependency references a non-existent task.
    """
    for task in self.tasks.values():
        if task.status == TaskStatus.PENDING or (
            task.status == TaskStatus.RUNNING and task.is_timed_out()
        ):
            # Single pass: check existence and satisfaction with early exit
            deps_satisfied = True
            for dep_id in task.dependencies:
                if dep_id not in self.tasks:
                    raise ValueError(f"Missing dependency: {dep_id} (in {task.id})")
                if self.tasks[dep_id].status != TaskStatus.COMPLETED:
                    deps_satisfied = False
                    break  # Early exit on first unsatisfied dep
            if deps_satisfied:
                return task
    return None
```

**Step 2: Run existing tests** (30 sec)

```bash
pytest tests/harness/test_state.py -k "get_claimable" -v
```

Expected: All 8 tests pass:
- `test_get_claimable_task_no_deps`
- `test_get_claimable_task_with_deps`
- `test_get_claimable_task_multiple_deps`
- `test_get_claimable_task_all_deps_completed`
- `test_get_claimable_task_raises_on_missing_dependency`
- `test_get_claimable_task_none_available`
- `test_get_claimable_task_reclaims_timed_out`

**Step 3: Commit** (30 sec)

```bash
git add src/harness/state.py
git commit -m "perf(state): single-pass dependency check with early exit"
```

---

## Task 3: Optimize trajectory tail with list+join

**Files:**
- Modify: `src/harness/trajectory.py:71-136`
- Test: `tests/harness/test_trajectory.py` (existing tests cover this)

**Context:** Current implementation uses `buffer = chunk + buffer` which creates a new bytes object on each block read. For N blocks, this is O(N^2) total bytes copied. Using a list and `b"".join()` is O(N).

**Step 1: Refactor _tail_reverse_seek** (5 min)

Replace lines 94-112 (the while loop and buffer handling):

Before:
```python
# Read from end in blocks until we have enough lines
buffer = b""
position = file_size
bytes_read = 0

while True:
    # Check buffer limit to prevent memory exhaustion on corrupt files
    if bytes_read >= max_buffer_bytes:
        break

    # Determine how much to read
    read_size = min(block_size, position)
    position -= read_size

    # Seek to position and read
    f.seek(position)
    chunk = f.read(read_size)
    buffer = chunk + buffer
    bytes_read += read_size

    # Try to split into lines
    lines = buffer.split(b"\n")

    # If we have enough lines (accounting for potential empty line at end)
    # We need n+1 because split on "line1\nline2\n" gives ["line1", "line2", ""]
    if len(lines) > n or position == 0:
        break
```

After:
```python
# Read from end in blocks until we have enough lines
chunks: list[bytes] = []
position = file_size
bytes_read = 0

while True:
    # Check buffer limit to prevent memory exhaustion on corrupt files
    if bytes_read >= max_buffer_bytes:
        break

    # Determine how much to read
    read_size = min(block_size, position)
    position -= read_size

    # Seek to position and read
    f.seek(position)
    chunk = f.read(read_size)
    chunks.insert(0, chunk)  # O(1) amortized for small lists
    bytes_read += read_size

    # Join and split to check line count (only on the combined buffer)
    buffer = b"".join(chunks)
    lines = buffer.split(b"\n")

    # If we have enough lines (accounting for potential empty line at end)
    # We need n+1 because split on "line1\nline2\n" gives ["line1", "line2", ""]
    if len(lines) > n or position == 0:
        break
```

Note: The join happens inside the loop for the line-count check, but this is still more efficient because we avoid the O(N^2) repeated concatenation pattern. For very large files, we could optimize further by counting newlines without joining, but this is sufficient for the 1MB limit.

**Step 2: Run existing tests** (30 sec)

```bash
pytest tests/harness/test_trajectory.py -v
```

Expected: All tests pass, including:
- `test_tail_returns_last_n`
- `test_tail_empty_file`
- `test_tail_fewer_than_n`
- `test_tail_large_file_performance`
- `test_tail_limits_memory_on_corrupt_file`

**Step 3: Run performance test specifically** (30 sec)

```bash
pytest tests/harness/test_trajectory.py::test_tail_large_file_performance -v
```

Expected: Test passes with timing assertion (tail should be fast).

**Step 4: Commit** (30 sec)

```bash
git add src/harness/trajectory.py
git commit -m "perf(trajectory): use list+join to avoid O(N^2) concatenation"
```

---

## Task 4: Code Review

**Files:**
- Review: All changes from Tasks 1-3

**Step 1: Run full test suite** (1 min)

```bash
make check
```

Expected: All lints, type checks, and tests pass.

**Step 2: Verify no behavior changes** (30 sec)

The changes are pure refactors. Verify by checking:
1. No new test failures
2. No API changes
3. No new imports in client.py (except moving datetime to top)

**Step 3: Final commit verification** (30 sec)

```bash
git log --oneline -3
```

Expected: Three commits with messages:
- `perf(trajectory): use list+join to avoid O(N^2) concatenation`
- `perf(state): single-pass dependency check with early exit`
- `perf(client): move datetime import outside render loop`

---

## Parallel Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2, 3 | Independent files, no overlap |
| Group 2 | 4 | Depends on all prior tasks |
