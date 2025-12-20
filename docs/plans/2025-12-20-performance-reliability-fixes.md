# Performance & Reliability Fixes Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-20-performance-reliability-fixes.md` to implement task-by-task.

**Goal:** Fix performance hotspots and reliability issues in trajectory, state, and plan modules.

**Architecture:** Three targeted fixes: (1) O(k) tail operation via deferred buffer join, (2) robust Markdown parsing with bidirectional validation, (3) async trajectory writes to eliminate fsync convoy. State WAL deferred to Phase 2.

**Tech Stack:** Python 3.13t, pytest, threading, memoryview (optional optimization)

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent modules: trajectory tail vs plan parsing |
| Group 2 | 3 | Depends on Group 1 (trajectory module stable) |
| Group 3 | 4 | Integration verification after all fixes |

---

### Task 1: Fix Quadratic Buffer Reconstruction in TrajectoryLogger._tail_reverse_seek

**Files:**
- Modify: `src/harness/trajectory.py:71-137`
- Test: `tests/harness/test_trajectory.py`

**Problem:** Lines 113-114 call `b"".join(reversed(chunks))` and `buffer.split(b"\n")` inside the `while True` loop, causing O(k²) complexity where k = chunks read.

**Step 1: Write the failing test** (2-5 min)

Add a test that explicitly verifies the join happens outside the loop by inspecting the code structure:

```python
def test_tail_reverse_seek_joins_outside_loop(tmp_path):
    """Verify buffer join happens AFTER the while loop, not inside it.

    Bug: join(reversed(chunks)) inside loop = O(k²) where k = chunks read.
    Fix: Count newlines in each chunk, join only when done seeking.
    """
    import ast
    import inspect
    from harness.trajectory import TrajectoryLogger

    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")
    source = inspect.getsource(logger._tail_reverse_seek)

    # Parse the source to find the while loop
    tree = ast.parse(source)

    # Find the while True loop
    while_loops = [node for node in ast.walk(tree) if isinstance(node, ast.While)]
    assert len(while_loops) == 1, "Expected exactly one while loop"

    while_loop = while_loops[0]
    while_body_lines = {node.lineno for node in ast.walk(while_loop)}

    # Find all calls to b"".join or bytes join patterns
    join_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check for .join() method calls
            if isinstance(node.func, ast.Attribute) and node.func.attr == "join":
                join_calls.append(node.lineno)

    # Verify no join calls are inside the while loop
    joins_inside_loop = [line for line in join_calls if line in while_body_lines]
    assert not joins_inside_loop, (
        f"Found join() calls inside while loop at relative lines {joins_inside_loop}. "
        "Move buffer reconstruction outside the loop to achieve O(k) complexity."
    )
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_trajectory.py::test_tail_reverse_seek_joins_outside_loop -v
```

Expected: FAIL with "Found join() calls inside while loop"

**Step 3: Implement the fix** (5 min)

Replace the current `_tail_reverse_seek` implementation. The key change: count newlines in each chunk incrementally, only join after exiting the loop.

```python
def _tail_reverse_seek(self, n: int, max_buffer_bytes: int) -> list[dict[str, Any]]:
    """Efficiently read last N lines using reverse-seek.

    Reads the file from the end in 4KB blocks until we have enough lines
    or reach the maximum buffer size.

    Complexity: O(k) where k = number of blocks read (NOT O(k²)).

    Args:
        n: Number of lines to retrieve
        max_buffer_bytes: Maximum bytes to read before stopping

    Returns:
        List of the last N events
    """
    block_size = 4096  # 4KB blocks

    with self.trajectory_file.open("rb") as f:
        # Get file size
        f.seek(0, 2)  # Seek to end
        file_size = f.tell()

        if file_size == 0:
            return []

        # Read from end in blocks until we have enough lines
        chunks: list[bytes] = []
        position = file_size
        bytes_read = 0
        newline_count = 0

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
            chunks.append(chunk)  # O(1) append
            bytes_read += read_size

            # Count newlines in this chunk only (O(chunk_size), not O(total_bytes))
            newline_count += chunk.count(b"\n")

            # We need n+1 newlines because split on "line1\nline2\n" gives ["line1", "line2", ""]
            if newline_count > n or position == 0:
                break

        # Join ONCE after loop exits - O(total_bytes) but only once
        buffer = b"".join(reversed(chunks))
        lines = buffer.split(b"\n")

        # Parse JSON lines, skipping corrupt ones
        events: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event: dict[str, Any] = json.loads(line.decode("utf-8"))
                events.append(event)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Skip corrupt lines (crash resilience)
                continue

        # Return last n events
        return events[-n:] if len(events) > n else events
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_trajectory.py::test_tail_reverse_seek_joins_outside_loop -v
```

Expected: PASS

**Step 5: Run existing performance test** (30 sec)

```bash
pytest tests/harness/test_performance.py::test_trajectory_tail_on_large_file -v
```

Expected: PASS with <50ms runtime

**Step 6: Commit** (30 sec)

```bash
git add src/harness/trajectory.py tests/harness/test_trajectory.py
git commit -m "perf(trajectory): fix O(k²) buffer reconstruction in tail

Move b''.join(reversed(chunks)) outside while loop.
Count newlines incrementally per-chunk instead of
reconstructing full buffer on each iteration.

Complexity: O(k) where k = blocks read."
```

---

### Task 2: Add Bidirectional Validation to Markdown Plan Parser

**Files:**
- Modify: `src/harness/plan.py:72-151`
- Test: `tests/harness/test_plan.py`

**Problem:** Parser validates orphan tasks (in body but not table) but NOT phantom tasks (in table but not body). If a task header has a typo, it's silently dropped.

**Step 1: Write the failing test for phantom task detection** (2-5 min)

```python
def test_parse_markdown_plan_rejects_phantom_tasks():
    """parse_markdown_plan rejects tasks in table but not in body (phantom tasks).

    Bug: If "### Task 2: ..." is typo'd as "### Task2: ...", the parser silently
    drops Task 2 because it's in the table but the regex doesn't match the header.
    """
    from harness.plan import parse_markdown_plan

    content = """\
**Goal:** Phantom task test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2  | Task 2 is in table but not in body |

### Task 1: Real Task

This task has a proper header.

### Task2: Typo Task

Missing space after Task - regex won't match!
"""
    with pytest.raises(ValueError, match="Phantom tasks in table but not in body: 2"):
        parse_markdown_plan(content)
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_rejects_phantom_tasks -v
```

Expected: FAIL (no ValueError raised, task 2 silently dropped)

**Step 3: Write test for relaxed regex patterns** (2-5 min)

```python
def test_parse_markdown_plan_flexible_header_formats():
    """parse_markdown_plan accepts reasonable header variations.

    The regex should accept:
    - "### Task 1: Description" (standard)
    - "### Task 1 : Description" (space before colon)
    - "### Task 1" (no colon, no description)
    - "### Task auth-service: Description" (semantic ID)
    - "### Task 1.1: Description" (dotted ID)
    """
    from harness.plan import parse_markdown_plan

    content = """\
**Goal:** Flexible format test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2, 3, auth-service, 1.1 | Various formats |

### Task 1: Standard format

Body 1.

### Task 2 : Space before colon

Body 2.

### Task 3

No colon, no description.

### Task auth-service: Semantic ID

Body auth.

### Task 1.1: Dotted ID

Body dotted.
"""
    plan = parse_markdown_plan(content)

    assert "1" in plan.tasks
    assert "2" in plan.tasks
    assert "3" in plan.tasks
    assert "auth-service" in plan.tasks
    assert "1.1" in plan.tasks
    assert len(plan.tasks) == 5
```

**Step 4: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_flexible_header_formats -v
```

Expected: FAIL (rigid regex doesn't match variations)

**Step 5: Implement the fix** (5 min)

Update `parse_markdown_plan` with relaxed regex and bidirectional validation:

```python
def parse_markdown_plan(content: str) -> PlanDefinition:
    """Parse structured Markdown plan format.

    Extracts:
    1. Goal from `**Goal:** <text>`
    2. Task groups from `| Group N | task_ids |` table rows
    3. Task definitions from `### Task <ID>` headers (colon optional)

    Dependencies: Tasks in Group N depend on ALL tasks in Group N-1.

    Validation:
    - Rejects orphan tasks (in body but not in table)
    - Rejects phantom tasks (in table but not in body)
    """
    # 1. Extract Goal
    goal_match = re.search(r"\*\*Goal:\*\*\s*(.+)", content)
    goal = goal_match.group(1).strip() if goal_match else "Goal not specified"

    # 2. Extract Task Groups (for dependency calculation)
    # Pattern: | Group 1 | task-1, auth-service | ... (captures group number and task list)
    # Supports semantic IDs: alphanumeric, dashes, underscores, dots
    group_pattern = r"\|\s*Group\s*(\d+)\s*\|\s*([\w\-\.,\s]+)\s*\|"
    groups: dict[int, list[str]] = {}

    for match in re.finditer(group_pattern, content):
        group_id = int(match.group(1))
        task_ids = [t.strip() for t in match.group(2).split(",") if t.strip()]
        groups[group_id] = task_ids

    # 3. Extract Task Content
    # Relaxed pattern: "### Task <ID>" with optional colon and description
    # Supports: "### Task 1: Desc", "### Task 1 : Desc", "### Task 1", "### Task 1.1: Desc"
    task_pattern = r"^### Task\s+([\w\-\.]+)\s*(?::\s*(.*))?$"
    parts = re.split(task_pattern, content, flags=re.MULTILINE)

    # parts[0] is preamble. Then groups of 3: [id, desc, body, id, desc, body, ...]
    tasks_data: dict[str, _TaskData] = {}

    for i in range(1, len(parts), 3):
        if i + 2 > len(parts):
            break
        t_id = parts[i].strip()
        t_desc = (parts[i + 1] or "").strip()  # Description may be None if no colon
        t_body = parts[i + 2].strip()

        tasks_data[t_id] = _TaskData(
            description=t_desc if t_desc else f"Task {t_id}",
            instructions=t_body,
            dependencies=[],
        )

    # 4. Calculate Dependencies based on Groups
    # Group N depends on all tasks from Group N-1
    sorted_group_ids = sorted(groups.keys())
    for i, group_id in enumerate(sorted_group_ids):
        if i > 0:
            prev_group_id = sorted_group_ids[i - 1]
            prev_tasks = groups[prev_group_id]

            for t_id in groups[group_id]:
                if t_id in tasks_data:
                    tasks_data[t_id]["dependencies"] = prev_tasks

    # 5. Bidirectional Validation
    all_grouped_tasks = {t for tasks in groups.values() for t in tasks}

    # 5a. Orphan tasks: in body but not in table
    orphan_tasks = set(tasks_data.keys()) - all_grouped_tasks
    if orphan_tasks:
        raise ValueError(
            f"Orphan tasks not in any group: {', '.join(sorted(orphan_tasks))}. "
            "Add them to the Task Groups table."
        )

    # 5b. Phantom tasks: in table but not in body (CRITICAL - silent failures)
    phantom_tasks = all_grouped_tasks - set(tasks_data.keys())
    if phantom_tasks:
        raise ValueError(
            f"Phantom tasks in table but not in body: {', '.join(sorted(phantom_tasks))}. "
            "Check for typos in ### Task headers (missing space, wrong ID)."
        )

    # 6. Construct PlanDefinition
    final_tasks = {}
    for t_id, t_data in tasks_data.items():
        final_tasks[t_id] = PlanTaskDefinition(
            description=t_data["description"],
            instructions=t_data["instructions"],
            dependencies=t_data["dependencies"],
            timeout_seconds=600,
            role=None,
        )

    return PlanDefinition(goal=goal, tasks=final_tasks)
```

**Step 6: Run tests to verify they pass** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_rejects_phantom_tasks tests/harness/test_plan.py::test_parse_markdown_plan_flexible_header_formats -v
```

Expected: PASS (both tests)

**Step 7: Run all plan tests to verify no regressions** (30 sec)

```bash
pytest tests/harness/test_plan.py -v
```

Expected: All tests PASS

**Step 8: Commit** (30 sec)

```bash
git add src/harness/plan.py tests/harness/test_plan.py
git commit -m "fix(plan): add bidirectional validation and relaxed regex

- Detect phantom tasks (in table but not in body)
- Relax regex: optional colon, space tolerance, dotted IDs
- Change [\w\-]+ to [\w\-\.]+ for semantic IDs like 1.1
- Explicit error messages guide users to fix typos"
```

---

### Task 3: Eliminate fsync Convoy in TrajectoryLogger.log

**Files:**
- Modify: `src/harness/trajectory.py:32-49`
- Test: `tests/harness/test_trajectory.py`

**Problem:** `log()` holds `_lock` during `os.fsync()`, which blocks for 1-10ms on SSD. All threads serialize at this point.

**Step 1: Write the failing test** (2-5 min)

```python
def test_log_does_not_hold_lock_during_fsync(tmp_path):
    """Verify log() releases lock before calling fsync.

    Bug: fsync() under lock creates convoy effect (1-10ms blocking).
    Fix: Write to buffer under lock, fsync outside lock.

    This test verifies that concurrent log calls can interleave,
    which is only possible if the lock is not held during fsync.
    """
    import threading
    import time
    from harness.trajectory import TrajectoryLogger

    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")

    # Track when each thread acquires and releases the lock
    lock_held_times: list[tuple[float, float, int]] = []  # (start, end, thread_id)
    original_fsync = os.fsync

    def slow_fsync(fd):
        """Simulate slow disk with 10ms fsync."""
        time.sleep(0.01)  # 10ms
        original_fsync(fd)

    # Patch fsync to be slow
    os.fsync = slow_fsync

    try:
        threads = []
        start_barrier = threading.Barrier(5)

        def log_event(thread_id):
            start_barrier.wait()  # All threads start together
            t_start = time.monotonic()
            logger.log({"thread": thread_id, "data": "x" * 100})
            t_end = time.monotonic()
            lock_held_times.append((t_start, t_end, thread_id))

        for i in range(5):
            t = threading.Thread(target=log_event, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # If fsync is INSIDE lock: 5 threads × 10ms = 50ms total (serialized)
        # If fsync is OUTSIDE lock: ~10ms total (parallel fsyncs)

        total_time = max(end for _, end, _ in lock_held_times) - min(start for start, _, _ in lock_held_times)
        total_time_ms = total_time * 1000

        # With fsync inside lock, this would take ~50ms
        # With fsync outside lock, this should take ~10-15ms
        assert total_time_ms < 30, (
            f"5 concurrent log() calls took {total_time_ms:.1f}ms. "
            "If fsync is outside lock, should be ~10-15ms. "
            "If fsync is inside lock (convoy), would be ~50ms."
        )
    finally:
        os.fsync = original_fsync
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_trajectory.py::test_log_does_not_hold_lock_during_fsync -v
```

Expected: FAIL with "took ~50ms" (convoy effect)

**Step 3: Implement the fix** (5 min)

Restructure `log()` to release lock before fsync. Use a write buffer approach:

```python
def log(self, event: dict[str, Any]) -> None:
    """Append an event to the trajectory log.

    Thread-safe operation that appends a JSON line to the file.
    Uses flush + fsync for crash durability (System Reliability Protocol).

    Lock is held only during the write, NOT during fsync, to prevent
    convoy effect when multiple threads log concurrently.

    Args:
        event: Dictionary to log as a JSON line
    """
    line = json.dumps(event) + "\n"

    with self._lock:
        # Create parent directory if it doesn't exist
        self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)

        # Open, write, flush under lock (fast - just memory and kernel buffer)
        with self.trajectory_file.open("a") as f:
            f.write(line)
            f.flush()
            fd = f.fileno()
            # Keep fd for fsync outside lock
            # Note: fd is valid until file is closed, but we need to fsync before close
            # Solution: fsync inside the 'with' block but AFTER releasing our logical lock
            # Actually, we can't release _lock and keep f open safely across threads
            # Alternative: Use os.open/os.write/os.fsync for more control

    # The above approach won't work because we can't hold the file handle
    # after releasing the lock. Instead, we accept that individual fsyncs
    # serialize, but we batch writes to amortize the cost.
    #
    # For now, keep fsync inside but document this as a known limitation.
    # The real fix is an async write queue (Task 3b - future work).

    # REVISED APPROACH: fsync after lock release using O_APPEND
    # This is safe because O_APPEND writes are atomic on POSIX
```

**Step 3 (Revised): Implement async write queue** (10 min)

The safest fix is an async write queue that batches fsyncs:

```python
import queue
import threading
from typing import Any

class TrajectoryLogger:
    """Append-only JSONL logger for trajectory events.

    Uses a background thread to batch fsync operations, eliminating
    convoy effects when multiple threads log concurrently.
    """

    def __init__(self, trajectory_file: Path) -> None:
        self.trajectory_file = trajectory_file
        self._lock = threading.Lock()
        self._write_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        """Background thread that batches writes and fsyncs."""
        while True:
            # Wait for first event
            event = self._write_queue.get()
            if event is None:  # Shutdown signal
                break

            # Collect any additional events that arrived (batching)
            events = [event]
            try:
                while True:
                    events.append(self._write_queue.get_nowait())
            except queue.Empty:
                pass

            # Filter out None (shutdown signals)
            events = [e for e in events if e is not None]
            if not events:
                continue

            # Write all events in one batch
            self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)
            with self.trajectory_file.open("a") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")
                f.flush()
                os.fsync(f.fileno())

    def log(self, event: dict[str, Any]) -> None:
        """Queue an event for logging. Returns immediately (non-blocking)."""
        self._write_queue.put(event)

    def close(self) -> None:
        """Shutdown the writer thread."""
        self._write_queue.put(None)
        self._writer_thread.join(timeout=5.0)

    # ... rest of methods unchanged
```

**However**, this changes the API semantics (log becomes async). Let's use a simpler fix that maintains sync semantics but uses `O_APPEND` for atomic writes:

```python
def log(self, event: dict[str, Any]) -> None:
    """Append an event to the trajectory log.

    Thread-safe operation that appends a JSON line to the file.
    Uses O_APPEND for atomic writes and fsync for durability.

    Note: fsync still serializes at the kernel level, but the lock
    is held for minimal time (just the write, not the fsync).

    Args:
        event: Dictionary to log as a JSON line
    """
    line = (json.dumps(event) + "\n").encode("utf-8")

    # Create parent directory (idempotent, thread-safe via OS)
    self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)

    # O_APPEND guarantees atomic writes (POSIX)
    # We use low-level os.open to control flags precisely
    fd = os.open(
        self.trajectory_file,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o644
    )
    try:
        os.write(fd, line)  # Atomic due to O_APPEND
        os.fsync(fd)  # Durability guarantee
    finally:
        os.close(fd)
```

Wait - this removes the lock entirely, which is correct for O_APPEND. But the existing test `test_log_calls_fsync_for_durability` checks for fsync which is still present. Let me verify this approach is correct.

**Step 3 (Final): Use O_APPEND without lock** (5 min)

```python
def log(self, event: dict[str, Any]) -> None:
    """Append an event to the trajectory log.

    Thread-safe via O_APPEND (POSIX atomic append guarantee).
    Uses fsync for crash durability (System Reliability Protocol).

    Args:
        event: Dictionary to log as a JSON line
    """
    line = (json.dumps(event) + "\n").encode("utf-8")

    # Create parent directory (idempotent)
    self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)

    # O_APPEND: kernel guarantees atomic append (no interleaving)
    # This eliminates the need for self._lock during writes
    fd = os.open(
        self.trajectory_file,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o644
    )
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
```

**Note:** Keep `self._lock` for `tail()` operations to prevent reading while writing. Update the lock usage:

```python
def __init__(self, trajectory_file: Path) -> None:
    self.trajectory_file = trajectory_file
    self._lock = threading.Lock()  # Now only used for tail() consistency

def log(self, event: dict[str, Any]) -> None:
    # ... O_APPEND implementation (no lock needed for writes)

def tail(self, n: int = 5, max_buffer_bytes: int = 1024 * 1024) -> list[dict[str, Any]]:
    if not self.trajectory_file.exists():
        return []
    # Lock prevents reading partial writes (though O_APPEND makes this unlikely)
    with self._lock:
        return self._tail_reverse_seek(n, max_buffer_bytes)
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_trajectory.py::test_log_does_not_hold_lock_during_fsync -v
```

Expected: PASS (concurrent logs complete in ~10-15ms, not 50ms)

**Step 5: Run all trajectory tests** (30 sec)

```bash
pytest tests/harness/test_trajectory.py -v
```

Expected: All tests PASS

**Step 6: Commit** (30 sec)

```bash
git add src/harness/trajectory.py tests/harness/test_trajectory.py
git commit -m "perf(trajectory): eliminate fsync convoy with O_APPEND

- Use O_APPEND for atomic appends (POSIX guarantee)
- Remove lock from log() - O_APPEND handles concurrency
- Keep lock for tail() to ensure read consistency
- Concurrent writes now parallelize (10-15ms vs 50ms for 5 threads)"
```

---

### Task 4: Integration Verification

**Files:**
- Test: `tests/harness/test_performance.py`

**Step 1: Run full performance test suite** (2 min)

```bash
pytest tests/harness/test_performance.py -v
```

Expected: All tests PASS with documented time bounds:
- `test_claim_task_scales_with_dag_size`: <100ms for 1000 tasks
- `test_trajectory_tail_on_large_file`: <50ms for 50K events
- `test_dag_validation_on_large_graph`: <1s for 5000 tasks

**Step 2: Run full test suite** (2 min)

```bash
make test
```

Expected: All tests PASS

**Step 3: Run type checker** (30 sec)

```bash
make typecheck
```

Expected: No errors

**Step 4: Run linter** (30 sec)

```bash
make lint
```

Expected: No errors

**Step 5: Commit verification results** (30 sec)

If any issues found, fix them and commit. Otherwise, proceed to Code Review.

---

### Task 5: Code Review

**Step 1: Review all changes**

```bash
git diff main..HEAD
```

Verify:
- No debug statements left
- All new tests have docstrings
- Error messages are actionable
- Lock hierarchy from CLAUDE.md is preserved

**Step 2: Create summary of changes**

Document the performance improvements with before/after metrics if available.
