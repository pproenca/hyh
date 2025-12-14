# Council Amendments Implementation Plan

**Goal:** Implement three mandatory amendments from the Council Final Pre-Flight Audit to complete Harness v2.0.

**Architecture:** Targeted additions to existing modules - no structural changes. Amendment A adds telemetry to daemon.py, Amendment B adds capability checks to runtime.py, Amendment C adds cycle detection to state.py.

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2, 3 | All amendments are independent - different files, no overlap |
| Group 2 | 4 | Code review after all amendments complete |

---

### Task 1: Amendment A - Execution Telemetry (daemon.py)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/daemon.py` (`_handle_exec` method)
- Modify: `tests/harness/test_daemon.py`

**Council Directive (Sam Gross):**
> "As per the Observability Doctrine, a system that cannot explain its latency is broken. If a test suite takes 10 minutes instead of 1 minute, the Agent needs to know."

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_daemon.py - add to existing test class
   def test_exec_logs_duration_ms(self, harness_daemon):
       """_handle_exec must log duration_ms for every execution."""
       import json

       # Execute a command that takes measurable time
       request = {
           "command": "exec",
           "args": ["sleep", "0.1"],
       }
       response = send_request(harness_daemon.socket_path, request)

       assert response["status"] == "ok"

       # Read trajectory log - last entry should have duration_ms
       trajectory_path = harness_daemon.worktree_root / ".claude" / "trajectory.jsonl"
       with open(trajectory_path) as f:
           lines = f.readlines()

       last_event = json.loads(lines[-1])
       assert last_event["event_type"] == "exec"
       assert "duration_ms" in last_event
       assert last_event["duration_ms"] >= 100  # At least 100ms for sleep 0.1
       assert isinstance(last_event["duration_ms"], int)
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "duration_ms"
   ```
   Expected: FAIL (duration_ms not in log)

3. **Implement MINIMAL code:**
   - Add `import time` at top of daemon.py (if not present)
   - In `_handle_exec`, wrap `runtime.execute()` with timing:
     ```python
     start_time = time.monotonic()
     result = server.runtime.execute(...)
     duration_ms = int((time.monotonic() - start_time) * 1000)
     ```
   - Add `"duration_ms": duration_ms` to the trajectory log dict

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "duration_ms"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(daemon): add duration_ms telemetry to exec handler

   Council Amendment A: Observability Doctrine compliance.
   Every exec event now logs execution time using time.monotonic()."
   ```

---

### Task 2: Amendment B - Capability Check (runtime.py + daemon.py)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/runtime.py` (Runtime protocol, LocalRuntime, DockerRuntime)
- Modify: `src/harness/daemon.py` (HarnessDaemon.__init__)
- Modify: `tests/harness/test_runtime.py`

**Council Directive (Justin Spahr-Summers):**
> "If HARNESS_RUNTIME=docker but the Docker daemon is down, your Harness starts successfully but fails on the first claim. This violates the Determinism Standard."

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_runtime.py - add new test class
   import subprocess
   import pytest


   class TestCapabilityCheck:
       """Tests for runtime capability verification (Council Amendment B)."""

       def test_local_runtime_check_capabilities_succeeds_with_git(self):
           """LocalRuntime.check_capabilities() succeeds when git is available."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()
           # Should not raise - git is available in test environment
           runtime.check_capabilities()

       def test_local_runtime_check_capabilities_fails_without_git(self, monkeypatch):
           """LocalRuntime.check_capabilities() raises when git is not available."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()

           # Mock subprocess.run to simulate git not found
           def mock_run(*args, **kwargs):
               raise FileNotFoundError("git not found")

           monkeypatch.setattr(subprocess, "run", mock_run)

           with pytest.raises(RuntimeError, match="git"):
               runtime.check_capabilities()

       def test_docker_runtime_check_capabilities_fails_when_docker_down(self, monkeypatch):
           """DockerRuntime.check_capabilities() raises when Docker is not running."""
           from harness.runtime import DockerRuntime, IdentityMapper

           runtime = DockerRuntime("test-container", IdentityMapper())

           # Mock subprocess.run to simulate docker info failure
           def mock_run(cmd, *args, **kwargs):
               if cmd[0] == "docker" and cmd[1] == "info":
                   result = type('Result', (), {'returncode': 1, 'stderr': b'Cannot connect'})()
                   return result
               return subprocess.run(cmd, *args, **kwargs)

           monkeypatch.setattr(subprocess, "run", mock_run)

           with pytest.raises(RuntimeError, match="Docker"):
               runtime.check_capabilities()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_runtime.py -v -k "check_capabilities"
   ```
   Expected: FAIL (check_capabilities not implemented)

3. **Implement MINIMAL code:**

   **runtime.py - Add to Runtime protocol:**
   ```python
   class Runtime(Protocol):
       def check_capabilities(self) -> None:
           """Verify runtime dependencies are available. Raises RuntimeError if not."""
           ...

       def execute(self, ...) -> ExecutionResult:
           ...
   ```

   **runtime.py - Add to LocalRuntime:**
   ```python
   def check_capabilities(self) -> None:
       """Verify git is available."""
       try:
           result = subprocess.run(
               ["git", "--version"],
               capture_output=True,
               timeout=5,
           )
           if result.returncode != 0:
               raise RuntimeError("git is not available")
       except FileNotFoundError:
           raise RuntimeError("git is not installed")
   ```

   **runtime.py - Add to DockerRuntime:**
   ```python
   def check_capabilities(self) -> None:
       """Verify Docker daemon is running and container exists."""
       result = subprocess.run(
           ["docker", "info"],
           capture_output=True,
           timeout=10,
       )
       if result.returncode != 0:
           raise RuntimeError(
               f"Docker daemon is not running: {result.stderr.decode()}"
           )

       result = subprocess.run(
           ["docker", "inspect", self.container_id],
           capture_output=True,
           timeout=5,
       )
       if result.returncode != 0:
           raise RuntimeError(
               f"Docker container '{self.container_id}' not found"
           )
   ```

   **daemon.py - Update HarnessDaemon.__init__:**
   ```python
   def __init__(self, socket_path: str, worktree_root: str):
       # ... existing setup ...
       self.runtime = create_runtime()

       # CRITICAL: Fail-fast capability check (Council Amendment B)
       self.runtime.check_capabilities()

       # ... rest of __init__ ...
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_runtime.py -v -k "check_capabilities"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(runtime): add check_capabilities for fail-fast startup

   Council Amendment B: Determinism Standard compliance.
   LocalRuntime checks git, DockerRuntime checks docker info.
   HarnessDaemon.__init__ calls check_capabilities() immediately."
   ```

---

### Task 3: Amendment C - Cycle Detection (state.py)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/state.py` (WorkflowState class)
- Modify: `tests/harness/test_state.py`

**Council Directive (David Soria Parra):**
> "If the Orchestrator hallucinates a dependency cycle (A -> B -> A), your get_claimable_task will simply return None forever. The system hangs silently."

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_state.py - add new test class
   import pytest


   class TestDAGValidation:
       """Tests for DAG cycle detection (Council Amendment C)."""

       def test_validate_dag_accepts_valid_dag(self):
           """validate_dag() accepts a valid DAG with no cycles."""
           from harness.state import WorkflowState, Task, TaskStatus

           state = WorkflowState(
               tasks={
                   "1": Task(id="1", description="First", status=TaskStatus.PENDING, dependencies=[]),
                   "2": Task(id="2", description="Second", status=TaskStatus.PENDING, dependencies=["1"]),
                   "3": Task(id="3", description="Third", status=TaskStatus.PENDING, dependencies=["1", "2"]),
               }
           )

           # Should not raise
           state.validate_dag()

       def test_validate_dag_rejects_simple_cycle(self):
           """validate_dag() raises ValueError for A -> B -> A cycle."""
           from harness.state import WorkflowState, Task, TaskStatus

           state = WorkflowState(
               tasks={
                   "A": Task(id="A", description="Task A", status=TaskStatus.PENDING, dependencies=["B"]),
                   "B": Task(id="B", description="Task B", status=TaskStatus.PENDING, dependencies=["A"]),
               }
           )

           with pytest.raises(ValueError, match="[Cc]ycle"):
               state.validate_dag()

       def test_validate_dag_rejects_self_loop(self):
           """validate_dag() raises ValueError for self-referential dependency."""
           from harness.state import WorkflowState, Task, TaskStatus

           state = WorkflowState(
               tasks={
                   "A": Task(id="A", description="Task A", status=TaskStatus.PENDING, dependencies=["A"]),
               }
           )

           with pytest.raises(ValueError, match="[Cc]ycle"):
               state.validate_dag()

       def test_validate_dag_rejects_long_cycle(self):
           """validate_dag() raises ValueError for A -> B -> C -> A cycle."""
           from harness.state import WorkflowState, Task, TaskStatus

           state = WorkflowState(
               tasks={
                   "A": Task(id="A", description="Task A", status=TaskStatus.PENDING, dependencies=["C"]),
                   "B": Task(id="B", description="Task B", status=TaskStatus.PENDING, dependencies=["A"]),
                   "C": Task(id="C", description="Task C", status=TaskStatus.PENDING, dependencies=["B"]),
               }
           )

           with pytest.raises(ValueError, match="[Cc]ycle"):
               state.validate_dag()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_state.py -v -k "validate_dag"
   ```
   Expected: FAIL (validate_dag not implemented)

3. **Implement MINIMAL code:**
   ```python
   # state.py - Add to WorkflowState class
   def validate_dag(self) -> None:
       """Validate that task dependencies form a DAG (no cycles).

       Uses DFS to detect cycles. Raises ValueError if cycle detected.

       Council Amendment C: Defensive Graph Construction.
       """
       visited: set[str] = set()
       path: set[str] = set()

       def visit(node: str) -> None:
           if node in path:
               raise ValueError(f"Dependency cycle detected at task '{node}'")
           if node in visited:
               return

           visited.add(node)
           path.add(node)

           task = self.tasks.get(node)
           if task:
               for dep in task.dependencies:
                   if dep in self.tasks:
                       visit(dep)

           path.remove(node)

       for task_id in self.tasks:
           visit(task_id)
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_state.py -v -k "validate_dag"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(state): add validate_dag for cycle detection

   Council Amendment C: Defensive Graph Construction.
   DFS-based cycle detection rejects invalid dependency graphs
   with clear error message identifying the cyclic task."
   ```

---

### Task 4: Code Review

**Effort:** simple (3-10 tool calls)

**Files:**
- All modified files from Tasks 1-3

**Instructions:**

1. Review all changes since base commit
2. Check for:
   - **Amendment A:** `duration_ms` in exec trajectory logs, uses `time.monotonic()`
   - **Amendment B:** `check_capabilities()` in Runtime protocol, called in `HarnessDaemon.__init__`
   - **Amendment C:** `validate_dag()` in WorkflowState with DFS cycle detection
3. Final verification:
   ```bash
   uv run pytest tests/harness/ -v
   uv run ruff check src/harness/
   ```

---

## Summary

| Task | Effort | Files | Commit Message | Council Directive |
|------|--------|-------|----------------|-------------------|
| 1. Amendment A: Telemetry | simple | daemon.py | `feat(daemon): add duration_ms telemetry` | Observability Doctrine (Sam Gross) |
| 2. Amendment B: Capability Check | standard | runtime.py, daemon.py | `feat(runtime): add check_capabilities` | Determinism Standard (Justin Spahr-Summers) |
| 3. Amendment C: Cycle Detection | simple | state.py | `feat(state): add validate_dag` | Defensive Graph (David Soria Parra) |
| 4. Code Review | simple | all | (no commit) | - |

**Total estimated tool calls:** 25-40
