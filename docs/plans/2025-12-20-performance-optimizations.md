# Performance Optimizations Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-20-performance-optimizations.md` to implement task-by-task.

**Goal:** Fix 4 identified performance bottlenecks: O(n^2) tail algorithm, disk I/O in critical sections, recursive cycle detection, and over-aggressive git locking.

**Architecture:** Each fix is isolated to a single file with no cross-dependencies. All preserve existing behavior while improving performance characteristics.

**Tech Stack:** Python 3.13t, pytest, threading

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 3, 4 | Independent files: trajectory.py, state.py (detect_cycle), git.py |
| Group 2 | 2 | state.py (StateManager) - isolated from detect_cycle changes |
| Group 3 | 5 | Code Review |

---

### Task 1: Fix O(n^2) tail algorithm in TrajectoryLogger

**Files:**
- Modify: `src/harness/trajectory.py:71-137`
- Test: `tests/harness/test_trajectory.py`

**Problem:** `chunks.insert(0, chunk)` is O(n) per insertion, making the loop O(n^2).

**Step 1: Write the failing performance test** (2-5 min)

```python
def test_tail_reverse_seek_uses_append_not_insert(tmp_path):
    """Verify _tail_reverse_seek uses O(1) append, not O(n) insert.

    Bug: chunks.insert(0, chunk) shifts all elements right on each call.
    Fix: Use chunks.append(chunk) then reversed(chunks) for O(1) per operation.
    """
    from harness.trajectory import TrajectoryLogger
    import inspect

    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")
    source = inspect.getsource(logger._tail_reverse_seek)

    # The fix should use append + reversed, not insert(0, ...)
    assert "insert(0" not in source, (
        "_tail_reverse_seek uses insert(0, chunk) which is O(n). "
        "Use chunks.append(chunk) and reversed(chunks) instead."
    )
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_trajectory.py::test_tail_reverse_seek_uses_append_not_insert -v
```

Expected: FAIL with `AssertionError: _tail_reverse_seek uses insert(0, chunk) which is O(n)`

**Step 3: Write minimal implementation** (2-5 min)

In `src/harness/trajectory.py`, replace lines 108-112:

```python
# OLD (O(n)):
# chunks.insert(0, chunk)  # Insert at beginning to maintain order
# ...
# buffer = b"".join(chunks)

# NEW (O(1)):
chunks.append(chunk)  # O(1) append
# ...
buffer = b"".join(reversed(chunks))  # Reverse once at the end
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_trajectory.py::test_tail_reverse_seek_uses_append_not_insert -v
```

Expected: PASS (1 passed)

**Step 5: Run existing tail tests to verify correctness** (30 sec)

```bash
pytest tests/harness/test_trajectory.py -k "tail" -v
```

Expected: All tail tests pass (no regression)

**Step 6: Commit** (30 sec)

```bash
git add src/harness/trajectory.py tests/harness/test_trajectory.py
git commit -m "$(cat <<'EOF'
perf(trajectory): use append+reversed for O(1) chunk assembly

chunks.insert(0, chunk) is O(n) per call, making the loop O(n^2).
Changed to chunks.append() + reversed() for O(1) per operation.
EOF
)"
```

---

### Task 2: Implement in-memory state caching in StateManager

**Files:**
- Modify: `src/harness/state.py:174-192` (_ensure_state_loaded)
- Modify: `src/harness/state.py:150-172` (save method to update cache)
- Test: `tests/harness/test_state.py`

**Problem:** Every `update()` reads entire state file from disk, defeating the purpose of a resident daemon.

**Step 1: Write the failing test for cached state** (2-5 min)

```python
def test_state_manager_caches_state_in_memory(tmp_path):
    """StateManager should cache state in memory, not re-read from disk on every operation.

    Bug: _ensure_state_loaded() always reads from disk, even when state is already loaded.
    Fix: Load once at save/load, return cached _state thereafter.
    """
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    # Delete the file after save - if caching works, claim_task should still work
    manager.state_file.unlink()

    # This should use cached state, not fail with "No state loaded"
    result = manager.claim_task("worker-1")
    assert result.task is not None, "StateManager should use cached state, not re-read from disk"
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_state.py::test_state_manager_caches_state_in_memory -v
```

Expected: FAIL with `ValueError: No state loaded`

**Step 3: Write minimal implementation** (5 min)

In `src/harness/state.py`, modify `_ensure_state_loaded`:

```python
def _ensure_state_loaded(self) -> WorkflowState:
    """Return cached state. Must be called with lock held.

    The daemon is the source of truth. State is loaded once via load()
    or set via save(), then cached in memory for all subsequent operations.

    Returns:
        The cached WorkflowState.

    Raises:
        ValueError: If no state has been loaded or saved yet.
    """
    if self._state is None:
        raise ValueError("No state loaded and no state file exists")
    return self._state
```

Also update `save()` to populate the cache:

```python
def save(self, state: WorkflowState) -> None:
    """Save state to disk and cache in memory."""
    with self._lock:
        state.validate_dag()
        self._state = state  # Cache the state
        self._write_atomic(state)
```

And update `load()` to populate the cache:

```python
def load(self) -> WorkflowState | None:
    """Load state from disk into memory cache."""
    with self._lock:
        if not self.state_file.exists():
            return None
        content = self.state_file.read_text()
        data = json.loads(content)
        self._state = WorkflowState(**data)
        return self._state
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_state.py::test_state_manager_caches_state_in_memory -v
```

Expected: PASS

**Step 5: Update conflicting test** (2 min)

The test `test_ensure_state_loaded_raises_when_file_deleted` now tests the OLD behavior. Update it:

```python
def test_state_manager_uses_cached_state_after_file_deleted(tmp_path):
    """StateManager should use cached state even if file is deleted.

    The daemon owns the state. Once loaded, external file deletion
    should not affect operations until explicit reload.
    """
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    # Delete the file
    manager.state_file.unlink()

    # Should still work with cached state
    result = manager.claim_task("worker-1")
    assert result.task is not None
    assert result.task.id == "task-1"
```

**Step 6: Run all state tests** (30 sec)

```bash
pytest tests/harness/test_state.py -v
```

Expected: All tests pass

**Step 7: Commit** (30 sec)

```bash
git add src/harness/state.py tests/harness/test_state.py
git commit -m "$(cat <<'EOF'
perf(state): cache state in memory instead of re-reading from disk

StateManager now caches state in _state after load() or save().
_ensure_state_loaded() returns cached state instead of re-reading disk.
Daemon is source of truth; external file modifications are not supported.
EOF
)"
```

---

### Task 3: Convert detect_cycle from recursive to iterative DFS

**Files:**
- Modify: `src/harness/state.py:19-46`
- Test: `tests/harness/test_state.py`

**Problem:** Recursive DFS with Python's 1000-frame limit causes `RecursionError` on deep graphs.

**Step 1: Write the failing test for deep graphs** (2-5 min)

```python
def test_detect_cycle_handles_deep_graph():
    """detect_cycle should handle graphs deeper than Python's recursion limit.

    Bug: Recursive DFS fails with RecursionError for graphs >1000 nodes deep.
    Fix: Use iterative DFS with explicit stack.
    """
    from harness.state import detect_cycle
    import sys

    # Create chain: node_0 -> node_1 -> ... -> node_1500
    depth = sys.getrecursionlimit() + 500  # Exceed default limit
    graph = {f"node_{i}": [f"node_{i+1}"] for i in range(depth)}
    graph[f"node_{depth}"] = []  # Terminal node

    # Should NOT raise RecursionError
    result = detect_cycle(graph)
    assert result is None, "Linear chain has no cycle"
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_state.py::test_detect_cycle_handles_deep_graph -v
```

Expected: FAIL with `RecursionError: maximum recursion depth exceeded`

**Step 3: Write minimal implementation** (5 min)

Replace `detect_cycle` in `src/harness/state.py`:

```python
def detect_cycle(graph: dict[str, list[str]]) -> str | None:
    """Detect cycle in directed graph using iterative DFS.

    Uses explicit stack to avoid RecursionError on deep graphs.

    Args:
        graph: Adjacency list mapping node ID to list of dependency IDs.

    Returns:
        First node found in a cycle, or None if graph is acyclic.
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()

    for start_node in graph:
        if start_node in visited:
            continue

        # Stack entries: (node, iterator over neighbors, entering)
        # entering=True means we're entering the node, False means we're leaving
        stack: list[tuple[str, list[str], int]] = [(start_node, graph.get(start_node, []), 0)]

        while stack:
            node, neighbors, idx = stack.pop()

            if idx == 0:
                # First time visiting this node
                if node in rec_stack:
                    return node  # Cycle detected
                if node in visited:
                    continue
                visited.add(node)
                rec_stack.add(node)

            # Process neighbors
            if idx < len(neighbors):
                neighbor = neighbors[idx]
                # Push current node back with incremented index
                stack.append((node, neighbors, idx + 1))
                if neighbor in rec_stack:
                    return neighbor  # Cycle detected
                if neighbor not in visited:
                    stack.append((neighbor, graph.get(neighbor, []), 0))
            else:
                # Done with all neighbors, leaving node
                rec_stack.discard(node)

    return None
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_state.py::test_detect_cycle_handles_deep_graph -v
```

Expected: PASS

**Step 5: Run existing cycle detection tests** (30 sec)

```bash
pytest tests/harness/test_state.py -k "cycle" -v
```

Expected: All cycle tests pass

**Step 6: Commit** (30 sec)

```bash
git add src/harness/state.py tests/harness/test_state.py
git commit -m "$(cat <<'EOF'
perf(state): convert detect_cycle to iterative DFS

Recursive DFS fails with RecursionError on graphs deeper than 1000 nodes.
Iterative implementation uses explicit stack, handling arbitrary depth.
EOF
)"
```

---

### Task 4: Add read_only flag to safe_git_exec

**Files:**
- Modify: `src/harness/git.py:14-38`
- Modify: `src/harness/git.py:68-72` (get_head_sha)
- Test: `tests/harness/test_git.py`

**Problem:** All git commands use `exclusive=True`, serializing even read operations like `git status` and `git rev-parse`.

**Step 1: Write the failing test for read-only git commands** (2-5 min)

```python
def test_safe_git_exec_read_only_skips_lock():
    """safe_git_exec with read_only=True should NOT acquire GLOBAL_EXEC_LOCK.

    Bug: All git commands use exclusive=True, serializing parallel reads.
    Fix: Add read_only parameter, only lock on write operations.
    """
    from unittest.mock import patch, MagicMock
    from harness.git import safe_git_exec

    execute_calls = []

    def mock_execute(command, cwd, timeout, exclusive):
        execute_calls.append({"command": command, "exclusive": exclusive})
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.git._runtime.execute", mock_execute):
        # Read-only command should NOT use exclusive lock
        safe_git_exec(["status"], cwd="/tmp", read_only=True)
        assert execute_calls[-1]["exclusive"] is False, (
            "read_only=True should pass exclusive=False to skip GLOBAL_EXEC_LOCK"
        )

        # Write command should still use exclusive lock
        safe_git_exec(["commit", "-m", "test"], cwd="/tmp", read_only=False)
        assert execute_calls[-1]["exclusive"] is True, (
            "read_only=False (default) should pass exclusive=True"
        )
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_git.py::test_safe_git_exec_read_only_skips_lock -v
```

Expected: FAIL with `TypeError: safe_git_exec() got an unexpected keyword argument 'read_only'`

**Step 3: Write minimal implementation** (2-5 min)

In `src/harness/git.py`, modify `safe_git_exec`:

```python
def safe_git_exec(
    args: list[str],
    cwd: str,
    timeout: int = 60,
    read_only: bool = False,
) -> ExecutionResult:
    """
    Execute git command with optional exclusive locking via runtime.

    Blocking call is fine because we're in a ThreadingMixIn server.
    Other clients are handled by other threads while we wait.

    Args:
        args: Git command arguments (without 'git' prefix)
        cwd: Working directory for git command
        timeout: Command timeout in seconds
        read_only: If True, skip GLOBAL_EXEC_LOCK (for parallel reads)

    Returns:
        ExecutionResult with returncode, stdout, stderr
    """
    return _runtime.execute(
        ["git", *args],
        cwd=cwd,
        timeout=timeout,
        exclusive=not read_only,
    )
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_git.py::test_safe_git_exec_read_only_skips_lock -v
```

Expected: PASS

**Step 5: Update get_head_sha to use read_only=True** (2 min)

In `src/harness/git.py`, modify `get_head_sha`:

```python
def get_head_sha(cwd: str) -> str | None:
    """Get current HEAD commit SHA."""
    result = safe_git_exec(["rev-parse", "HEAD"], cwd=cwd, read_only=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None
```

**Step 6: Update existing test that asserts exclusive=True** (2 min)

In `tests/harness/test_git.py`, update `test_git_uses_exclusive_locking`:

```python
def test_git_uses_exclusive_locking():
    """Git write operations should use exclusive locking.

    Read operations can run in parallel; write operations must be serialized.
    """
    from unittest.mock import patch, MagicMock
    from harness.git import safe_commit, safe_git_exec

    execute_calls = []

    def mock_execute(command, cwd, timeout=None, exclusive=False):
        execute_calls.append({"command": command, "exclusive": exclusive})
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")

    with patch("harness.git._runtime.execute", mock_execute):
        # Write operation: safe_commit uses exclusive=True (default)
        safe_commit("Test commit", cwd="/tmp")
        # Find the commit call
        commit_calls = [c for c in execute_calls if "commit" in c["command"]]
        assert any(c["exclusive"] for c in commit_calls), (
            "safe_commit must use exclusive=True for write operations"
        )

        execute_calls.clear()

        # Read operation with explicit read_only=True
        safe_git_exec(["status"], cwd="/tmp", read_only=True)
        assert execute_calls[-1]["exclusive"] is False, (
            "read_only=True should skip exclusive lock"
        )

        # Default behavior (read_only=False) still locks
        safe_git_exec(["status"], cwd="/tmp")
        assert execute_calls[-1]["exclusive"] is True, (
            "Default read_only=False should use exclusive lock"
        )
```

**Step 7: Run all git tests** (30 sec)

```bash
pytest tests/harness/test_git.py -v
```

Expected: All tests pass

**Step 8: Commit** (30 sec)

```bash
git add src/harness/git.py tests/harness/test_git.py
git commit -m "$(cat <<'EOF'
perf(git): add read_only flag to skip GLOBAL_EXEC_LOCK for parallel reads

safe_git_exec now accepts read_only=True to skip exclusive locking.
Write operations (default) still serialize via GLOBAL_EXEC_LOCK.
get_head_sha updated to use read_only=True for parallel execution.
EOF
)"
```

---

### Task 5: Code Review

**Files:**
- Review all changes from Tasks 1-4

**Step 1: Run full test suite** (2 min)

```bash
make check
```

Expected: All checks pass (lint, typecheck, tests)

**Step 2: Review changes** (5 min)

```bash
git diff main..HEAD
```

Verify:
- [ ] No `Any` types introduced
- [ ] All tests use proper assertions
- [ ] Commit messages follow conventional format
- [ ] No accidental debug code

**Step 3: Summarize changes**

Document the performance improvements achieved.
