# Architecture Audit: Improvement Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-19-architecture-audit-improvements.md` to implement task-by-task.

**Goal:** Address minor architecture improvements identified during comprehensive codebase audit.

**Architecture Assessment:** The harness codebase demonstrates exemplary clean architecture with 9/10 quality score. Layer separation is excellent (client is stdlib-only, daemon never calls subprocess directly), concurrency is thread-safe with documented lock hierarchy, and persistence uses atomic writes throughout. No critical issues found.

**Tech Stack:** Python 3.13t, Pydantic v2, pytest, ruff, mypy

---

## Audit Summary

### Strengths Confirmed

| Area | Score | Evidence |
|------|-------|----------|
| Layer Separation | 9/10 | Client imports only stdlib; daemon uses runtime abstraction |
| Testability | 9/10 | Each module independently testable |
| Concurrency Safety | 10/10 | Lock hierarchy documented and enforced |
| Persistence Safety | 10/10 | Atomic writes everywhere (tmp-fsync-rename) |
| Performance | 9/10 | Client <50ms, O(1) log tail, non-blocking telemetry |

### Issues Identified

| Priority | Issue | Impact | Effort |
|----------|-------|--------|--------|
| High | Undocumented environment variables | Developer confusion | simple |
| High | Duplicated DAG cycle detection | Maintenance burden | simple |
| Medium | Sparse git.py test coverage | Regression risk | standard |
| Low | No RPC protocol documentation | Onboarding friction | simple |

---

## Task Group 1: Documentation (Parallel)

### Task 1: Document Environment Variables

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `CLAUDE.md` (append new section)

**Step 1: Read current CLAUDE.md ending** (1 min)

Review the end of CLAUDE.md to determine where to append the new section.

**Step 2: Write environment variables section** (3 min)

Append the following to CLAUDE.md after the "Quick Reference" section:

```markdown
---

## X. Environment Variables

All harness environment variables and their defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `HARNESS_SOCKET` | `/tmp/harness-{user}.sock` | Unix socket path for client-daemon IPC |
| `HARNESS_WORKTREE` | Git repository root | Override worktree detection |
| `HARNESS_TIMEOUT` | `10` (seconds) | Daemon spawn timeout |
| `HARNESS_WORKER_ID_FILE` | `$XDG_RUNTIME_DIR/harness-worker-id` | Worker ID persistence file |
| `HARNESS_CONTAINER_ID` | None | Docker container ID for DockerRuntime |
| `HARNESS_HOST_ROOT` | None | Host path for Docker volume mapping |
| `HARNESS_CONTAINER_ROOT` | None | Container path for Docker volume mapping |

**Note:** `XDG_RUNTIME_DIR` follows the XDG Base Directory specification. Falls back to `/tmp` if unset.
```

**Step 3: Verify formatting** (30 sec)

```bash
head -20 CLAUDE.md && echo "..." && tail -30 CLAUDE.md
```

Expected: New section appears at end, markdown table is properly formatted.

**Step 4: Commit** (30 sec)

```bash
git add CLAUDE.md
git commit -m "docs: document all HARNESS_* environment variables"
```

---

### Task 2: Document RPC Protocol

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `CLAUDE.md` (append to new section)

**Step 1: Write RPC protocol section** (3 min)

Append after the Environment Variables section:

```markdown
---

## XI. Client-Daemon RPC Protocol

Communication uses JSON-over-Unix-socket with newline delimiters.

### Request Format

```json
{"command": "<command_name>", ...additional_fields}
```

### Response Format

```json
{"status": "ok" | "error", "data": {...}, "message": "<error_string>"}
```

### Command Reference

| Command | Request Fields | Response Data |
|---------|---------------|---------------|
| `get_state` | - | Full workflow state |
| `task_claim` | `worker_id` | Claimed task or null |
| `task_complete` | `task_id`, `worker_id`, `success`, `output?`, `error?` | Updated task |
| `task_skip` | `task_id`, `worker_id`, `reason` | Updated task |
| `exec` | `args`, `cwd?`, `timeout?`, `exclusive?` | `returncode`, `stdout`, `stderr`, `signal?` |
| `git` | `args`, `cwd?` | `returncode`, `stdout`, `stderr` |
| `plan_import` | `content` | Imported plan |
| `update_state` | `updates` (dict) | Updated state |
| `shutdown` | - | Acknowledgment |
```

**Step 2: Commit** (30 sec)

```bash
git add CLAUDE.md
git commit -m "docs: document client-daemon RPC protocol"
```

---

## Task Group 2: Code Quality (Sequential)

### Task 3: Extract DAG Cycle Detection

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/state.py` (extract function)
- Modify: `src/harness/plan.py` (import and use)
- Test: `tests/harness/test_state.py` (add unit test for extracted function)

**Step 1: Write failing test for extracted function** (2 min)

Add to `tests/harness/test_state.py`:

```python
def test_detect_cycle_returns_none_for_acyclic_graph() -> None:
    from harness.state import detect_cycle

    graph = {"a": ["b"], "b": ["c"], "c": []}
    assert detect_cycle(graph) is None


def test_detect_cycle_returns_cycle_node_for_cyclic_graph() -> None:
    from harness.state import detect_cycle

    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
    result = detect_cycle(graph)
    assert result in {"a", "b", "c"}  # Any node in the cycle
```

**Step 2: Run test to verify failure** (30 sec)

```bash
pytest tests/harness/test_state.py::test_detect_cycle_returns_none_for_acyclic_graph -v
```

Expected: FAIL with `ImportError: cannot import name 'detect_cycle'`

**Step 3: Extract detect_cycle function in state.py** (3 min)

Add before the `WorkflowState` class in `src/harness/state.py`:

```python
def detect_cycle(graph: dict[str, list[str]]) -> str | None:
    """Detect cycle in directed graph using DFS.

    Args:
        graph: Adjacency list mapping node ID to list of dependency IDs.

    Returns:
        First node found in a cycle, or None if graph is acyclic.
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str) -> str | None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if cycle_node := dfs(neighbor):
                    return cycle_node
            elif neighbor in rec_stack:
                return neighbor
        rec_stack.discard(node)
        return None

    for node in graph:
        if node not in visited:
            if cycle_node := dfs(node):
                return cycle_node
    return None
```

**Step 4: Update WorkflowState.validate_dag to use detect_cycle** (2 min)

In `src/harness/state.py`, modify `validate_dag` method:

```python
def validate_dag(self) -> None:
    """Validate task dependencies form a DAG (no cycles)."""
    graph = {t.id: list(t.dependencies) for t in self.tasks}
    if cycle_node := detect_cycle(graph):
        raise ValueError(f"Cycle detected involving task: {cycle_node}")
```

**Step 5: Run tests to verify pass** (30 sec)

```bash
pytest tests/harness/test_state.py -v -k "detect_cycle or validate_dag"
```

Expected: All tests PASS

**Step 6: Update plan.py to import detect_cycle** (2 min)

In `src/harness/plan.py`, replace the inline cycle detection:

```python
from .state import detect_cycle, Task, TaskStatus

# In PlanDefinition.validate_dag method:
def validate_dag(self) -> None:
    """Validate task dependencies form a DAG."""
    graph = {t.id: list(t.dependencies) for t in self.tasks}
    if cycle_node := detect_cycle(graph):
        raise ValueError(f"Cycle detected involving task: {cycle_node}")
```

**Step 7: Run all tests** (30 sec)

```bash
pytest tests/harness/test_plan.py tests/harness/test_state.py -v
```

Expected: All tests PASS

**Step 8: Commit** (30 sec)

```bash
git add src/harness/state.py src/harness/plan.py tests/harness/test_state.py
git commit -m "refactor(state): extract detect_cycle for DAG validation

Consolidates duplicated cycle detection logic from state.py and plan.py
into a single function. Both WorkflowState and PlanDefinition now use
the shared implementation."
```

---

### Task 4: Expand git.py Test Coverage

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_git.py` (add tests)

**Step 1: Read current test_git.py** (1 min)

Understand existing coverage before adding new tests.

**Step 2: Write test for exclusive locking behavior** (3 min)

Add to `tests/harness/test_git.py`:

```python
import threading
import time
from unittest.mock import patch, MagicMock

from harness.git import safe_git_exec, GLOBAL_EXEC_LOCK


def test_safe_git_exec_acquires_global_lock() -> None:
    """Verify git operations use exclusive locking."""
    lock_acquired_during_exec = threading.Event()

    def mock_execute(args, cwd, exclusive):
        # Check if we're inside the lock
        if GLOBAL_EXEC_LOCK.locked():
            lock_acquired_during_exec.set()
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.git._runtime.execute", side_effect=mock_execute):
        safe_git_exec(["status"], "/tmp")

    assert lock_acquired_during_exec.is_set(), "GLOBAL_EXEC_LOCK should be held during git exec"
```

**Step 3: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_git.py::test_safe_git_exec_acquires_global_lock -v
```

Expected: PASS (tests existing behavior)

**Step 4: Write test for safe_commit** (3 min)

```python
def test_safe_commit_stages_and_commits() -> None:
    """Verify safe_commit calls git add and git commit."""
    calls = []

    def mock_execute(args, cwd, exclusive=False):
        calls.append(args)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.git._runtime.execute", side_effect=mock_execute):
        safe_commit("/tmp/repo", "test commit message")

    assert ["git", "add", "-A"] in calls
    assert any("commit" in call and "-m" in call for call in calls)
```

**Step 5: Write test for get_head_sha** (2 min)

```python
def test_get_head_sha_returns_commit_hash() -> None:
    """Verify get_head_sha extracts commit hash from git output."""
    mock_result = MagicMock(returncode=0, stdout="abc123def456\n", stderr="")

    with patch("harness.git._runtime.execute", return_value=mock_result):
        sha = get_head_sha("/tmp/repo")

    assert sha == "abc123def456"
```

**Step 6: Write test for git command failure** (2 min)

```python
def test_safe_git_exec_raises_on_failure() -> None:
    """Verify git failures are properly reported."""
    mock_result = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repository")

    with patch("harness.git._runtime.execute", return_value=mock_result):
        result = safe_git_exec(["status"], "/tmp")

    assert result.returncode == 128
    assert "not a git repository" in result.stderr
```

**Step 7: Run all new tests** (30 sec)

```bash
pytest tests/harness/test_git.py -v
```

Expected: All tests PASS

**Step 8: Commit** (30 sec)

```bash
git add tests/harness/test_git.py
git commit -m "test(git): expand coverage for locking and error handling"
```

---

## Task Group 3: Final Review

### Task 5: Code Review

**Effort:** simple (3-10 tool calls)

**Files:**
- Review all changes from Tasks 1-4

**Step 1: Review diff against main** (2 min)

```bash
git diff main..HEAD --stat
git diff main..HEAD
```

**Step 2: Run full test suite** (1 min)

```bash
make check
```

Expected: All checks pass (lint, typecheck, test)

**Step 3: Verify no regressions** (1 min)

```bash
pytest tests/ -v --tb=short
```

Expected: All tests pass, no new failures

---

## Parallel Execution Groups

| Group | Tasks | Rationale |
|-------|-------|-----------|
| Group 1 | 1, 2 | Both modify CLAUDE.md but different sections (can merge) |
| Group 2 | 3 | Modifies state.py and plan.py (core logic) |
| Group 3 | 4 | Only modifies test files (independent) |
| Group 4 | 5 | Final review (depends on all prior tasks) |

**Recommended teammateCount:** 2 (Groups 1+3 can run parallel, Groups 2+4 sequential)

---

## Appendix: Items NOT Addressed (By Design)

The following were identified but intentionally deferred:

| Item | Reason |
|------|--------|
| plan.py splitting | File is 169 lines, under 200-line threshold |
| Handler extraction | daemon.py is 457 lines, under 600-line threshold |
| Config module | Only 7 env vars, centralized table is sufficient |
| Error hierarchy | Current ValueError/RuntimeError is pragmatic |
| Test fixture factory | Would be premature abstraction |

These should be revisited if the codebase grows significantly.
