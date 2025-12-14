# Council Amendments A, B, C Implementation Plan

**Goal:** Implement three mandatory Council amendments: execution telemetry (duration_ms), capability checks at startup, and DAG cycle detection.

**Architecture:** Each amendment targets a specific module following existing patterns. Amendment A adds timing to `_handle_exec` in daemon.py. Amendment B adds `check_capabilities()` to the Runtime protocol and implementations, called during HarnessDaemon initialization. Amendment C adds `validate_dag()` to WorkflowState, called when loading/saving state with tasks.

---

### Task 1: Add duration_ms to exec telemetry (Amendment A)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/daemon.py:194-264` (_handle_exec method)
- Test: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_exec_logs_duration_ms(daemon_with_state, socket_path, worktree):
       """exec should log duration_ms in trajectory."""
       daemon, worktree_path = daemon_with_state

       response = send_command(
           socket_path,
           {
               "command": "exec",
               "args": ["echo", "hello"],
           },
       )

       assert response["status"] == "ok"

       # Verify trajectory has duration_ms
       import json
       trajectory_file = worktree_path / ".claude" / "trajectory.jsonl"
       with open(trajectory_file, "r") as f:
           lines = f.readlines()
           # Find the exec event
           exec_events = [json.loads(line) for line in lines if "exec" in line]
           assert len(exec_events) >= 1
           event = exec_events[-1]
           assert event["event_type"] == "exec"
           assert "duration_ms" in event
           assert isinstance(event["duration_ms"], int)
           assert event["duration_ms"] >= 0
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_daemon.py::test_exec_logs_duration_ms -v
   ```

3. **Implement MINIMAL code:**
   - Add `import time` at top if not present
   - Wrap `server.runtime.execute(...)` with timing:
     ```python
     start_time = time.monotonic()
     result = server.runtime.execute(...)
     duration_ms = int((time.monotonic() - start_time) * 1000)
     ```
   - Add `"duration_ms": duration_ms` to trajectory log event
   - Also add duration_ms to timeout case trajectory log

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(daemon): add duration_ms telemetry to exec events"
   ```

---

### Task 2: Add check_capabilities() to Runtime protocol (Amendment B - Part 1)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/runtime.py:129-153` (Runtime Protocol)
- Test: `tests/harness/test_runtime.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   class TestRuntimeCapabilityCheck:
       """Test check_capabilities() method on Runtime implementations."""

       def test_local_runtime_has_check_capabilities(self):
           """LocalRuntime should have check_capabilities method."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()
           assert hasattr(runtime, "check_capabilities")
           assert callable(runtime.check_capabilities)

       def test_local_runtime_check_capabilities_verifies_git(self):
           """LocalRuntime.check_capabilities should verify git is available."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()
           # Should not raise if git is installed (it is in test env)
           runtime.check_capabilities()

       @patch("subprocess.run")
       def test_local_runtime_check_capabilities_raises_on_missing_git(self, mock_run):
           """LocalRuntime.check_capabilities should raise if git not found."""
           from harness.runtime import LocalRuntime

           mock_run.return_value = MagicMock(returncode=1)

           runtime = LocalRuntime()
           with pytest.raises(RuntimeError, match="git"):
               runtime.check_capabilities()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_runtime.py::TestRuntimeCapabilityCheck -v
   ```

3. **Implement MINIMAL code:**
   - Add `check_capabilities(self) -> None` to Runtime Protocol
   - Implement in LocalRuntime:
     ```python
     def check_capabilities(self) -> None:
         """Verify git is available."""
         result = subprocess.run(["git", "--version"], capture_output=True)
         if result.returncode != 0:
             raise RuntimeError("git not found in PATH")
     ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(runtime): add check_capabilities to LocalRuntime"
   ```

---

### Task 3: Add check_capabilities() to DockerRuntime (Amendment B - Part 2)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/runtime.py:208-286` (DockerRuntime class)
- Test: `tests/harness/test_runtime.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   class TestDockerRuntimeCapabilityCheck:
       """Test check_capabilities() for DockerRuntime."""

       @patch("subprocess.run")
       def test_docker_runtime_has_check_capabilities(self, mock_run):
           """DockerRuntime should have check_capabilities method."""
           from harness.runtime import DockerRuntime, IdentityMapper

           mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
           runtime = DockerRuntime("test-container", IdentityMapper())
           assert hasattr(runtime, "check_capabilities")
           assert callable(runtime.check_capabilities)

       @patch("subprocess.run")
       def test_docker_runtime_check_capabilities_verifies_docker(self, mock_run):
           """DockerRuntime.check_capabilities should run docker info."""
           from harness.runtime import DockerRuntime, IdentityMapper

           mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
           runtime = DockerRuntime("test-container", IdentityMapper())
           runtime.check_capabilities()

           # Verify docker info was called
           calls = [c[0][0] for c in mock_run.call_args_list]
           assert any("docker" in str(c) and "info" in str(c) for c in calls)

       @patch("subprocess.run")
       def test_docker_runtime_check_capabilities_raises_on_docker_failure(self, mock_run):
           """DockerRuntime.check_capabilities should raise if docker not running."""
           from harness.runtime import DockerRuntime, IdentityMapper

           mock_run.return_value = MagicMock(returncode=1, stderr="Cannot connect to Docker daemon")
           runtime = DockerRuntime("test-container", IdentityMapper())

           with pytest.raises(RuntimeError, match="[Dd]ocker"):
               runtime.check_capabilities()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_runtime.py::TestDockerRuntimeCapabilityCheck -v
   ```

3. **Implement MINIMAL code:**
   - Add to DockerRuntime:
     ```python
     def check_capabilities(self) -> None:
         """Verify Docker daemon is running and accessible."""
         result = subprocess.run(["docker", "info"], capture_output=True)
         if result.returncode != 0:
             raise RuntimeError(f"Docker not available: {result.stderr.decode()}")
     ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(runtime): add check_capabilities to DockerRuntime"
   ```

---

### Task 4: Call check_capabilities() in HarnessDaemon.__init__ (Amendment B - Part 3)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/daemon.py:277-306` (HarnessDaemon.__init__)
- Test: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_daemon_calls_check_capabilities_on_init(socket_path, worktree):
       """HarnessDaemon should call runtime.check_capabilities() on init."""
       from harness.daemon import HarnessDaemon
       from unittest.mock import patch, MagicMock

       with patch("harness.daemon.create_runtime") as mock_create:
           mock_runtime = MagicMock()
           mock_create.return_value = mock_runtime

           daemon = HarnessDaemon(socket_path, str(worktree))

           # check_capabilities should have been called
           mock_runtime.check_capabilities.assert_called_once()

           daemon.server_close()


   def test_daemon_fails_fast_on_capability_check_failure(socket_path, worktree):
       """HarnessDaemon should fail immediately if check_capabilities fails."""
       from harness.daemon import HarnessDaemon
       from unittest.mock import patch, MagicMock

       with patch("harness.daemon.create_runtime") as mock_create:
           mock_runtime = MagicMock()
           mock_runtime.check_capabilities.side_effect = RuntimeError("git not found")
           mock_create.return_value = mock_runtime

           with pytest.raises(RuntimeError, match="git not found"):
               HarnessDaemon(socket_path, str(worktree))
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_daemon.py::test_daemon_calls_check_capabilities_on_init -v
   pytest tests/harness/test_daemon.py::test_daemon_fails_fast_on_capability_check_failure -v
   ```

3. **Implement MINIMAL code:**
   - In HarnessDaemon.__init__, after `self.runtime = create_runtime()`:
     ```python
     self.runtime = create_runtime()
     self.runtime.check_capabilities()  # Fail fast if dependencies unavailable
     ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(daemon): call check_capabilities on startup"
   ```

---

### Task 5: Add validate_dag() cycle detection to WorkflowState (Amendment C)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/state.py:50-80` (WorkflowState class)
- Test: `tests/harness/test_state.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_state.py

   def test_validate_dag_no_cycle():
       """validate_dag should not raise for valid DAG."""
       state = WorkflowState(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Task 1",
                   status=TaskStatus.PENDING,
                   dependencies=[],
               ),
               "task-2": Task(
                   id="task-2",
                   description="Task 2",
                   status=TaskStatus.PENDING,
                   dependencies=["task-1"],
               ),
               "task-3": Task(
                   id="task-3",
                   description="Task 3",
                   status=TaskStatus.PENDING,
                   dependencies=["task-1", "task-2"],
               ),
           }
       )
       # Should not raise
       state.validate_dag()


   def test_validate_dag_detects_simple_cycle():
       """validate_dag should raise ValueError for A -> B -> A cycle."""
       state = WorkflowState(
           tasks={
               "task-a": Task(
                   id="task-a",
                   description="Task A",
                   status=TaskStatus.PENDING,
                   dependencies=["task-b"],
               ),
               "task-b": Task(
                   id="task-b",
                   description="Task B",
                   status=TaskStatus.PENDING,
                   dependencies=["task-a"],
               ),
           }
       )
       with pytest.raises(ValueError, match="[Cc]ycle"):
           state.validate_dag()


   def test_validate_dag_detects_self_cycle():
       """validate_dag should raise ValueError for self-referencing task."""
       state = WorkflowState(
           tasks={
               "task-a": Task(
                   id="task-a",
                   description="Task A",
                   status=TaskStatus.PENDING,
                   dependencies=["task-a"],
               ),
           }
       )
       with pytest.raises(ValueError, match="[Cc]ycle"):
           state.validate_dag()


   def test_validate_dag_detects_long_cycle():
       """validate_dag should raise ValueError for A -> B -> C -> A cycle."""
       state = WorkflowState(
           tasks={
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
       )
       with pytest.raises(ValueError, match="[Cc]ycle"):
           state.validate_dag()


   def test_validate_dag_empty_tasks():
       """validate_dag should not raise for empty task dict."""
       state = WorkflowState(tasks={})
       # Should not raise
       state.validate_dag()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_state.py::test_validate_dag_no_cycle -v
   pytest tests/harness/test_state.py::test_validate_dag_detects_simple_cycle -v
   pytest tests/harness/test_state.py::test_validate_dag_detects_self_cycle -v
   pytest tests/harness/test_state.py::test_validate_dag_detects_long_cycle -v
   pytest tests/harness/test_state.py::test_validate_dag_empty_tasks -v
   ```

3. **Implement MINIMAL code:**
   - Add to WorkflowState class:
     ```python
     def validate_dag(self) -> None:
         """Ensure no circular dependencies exist.

         Raises:
             ValueError: If a dependency cycle is detected.
         """
         visited: set[str] = set()
         path: set[str] = set()

         def visit(node: str) -> None:
             if node in path:
                 raise ValueError(f"Cycle detected at {node}")
             if node in visited:
                 return
             visited.add(node)
             path.add(node)
             if node in self.tasks:
                 for dep in self.tasks[node].dependencies:
                     visit(dep)
             path.remove(node)

         for task_id in self.tasks:
             visit(task_id)
     ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(state): add validate_dag cycle detection"
   ```

---

### Task 6: Call validate_dag() in StateManager.save (Amendment C - Part 2)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/state.py:113-125` (StateManager.save method)
- Test: `tests/harness/test_state.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_state_manager_save_validates_dag(tmp_path):
       """StateManager.save should validate DAG before saving."""
       manager = StateManager(tmp_path)

       # Create state with cycle
       state = WorkflowState(
           tasks={
               "task-a": Task(
                   id="task-a",
                   description="Task A",
                   status=TaskStatus.PENDING,
                   dependencies=["task-b"],
               ),
               "task-b": Task(
                   id="task-b",
                   description="Task B",
                   status=TaskStatus.PENDING,
                   dependencies=["task-a"],
               ),
           }
       )

       with pytest.raises(ValueError, match="[Cc]ycle"):
           manager.save(state)

       # File should not exist (save was rejected)
       assert not manager.state_file.exists()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_state.py::test_state_manager_save_validates_dag -v
   ```

3. **Implement MINIMAL code:**
   - In StateManager.save, before persisting:
     ```python
     def save(self, state: WorkflowState) -> None:
         """Save state to disk atomically (thread-safe)."""
         with self._lock:
             state.validate_dag()  # Reject cycles before persisting
             self._state = state
             # ... rest of save logic
     ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(state): validate DAG on save"
   ```

---

### Task 7: Integration test for all amendments

**Effort:** simple (3-10 tool calls)

**Files:**
- Create: `tests/harness/test_integration_council.py`

**TDD Instructions (MANDATORY):**

1. **Write test:**
   ```python
   # tests/harness/test_integration_council.py
   """Integration tests verifying Council Amendments A, B, C work together."""

   import pytest
   import json
   import os
   import socket
   import threading
   import time
   import subprocess
   import uuid
   from unittest.mock import patch, MagicMock


   @pytest.fixture
   def socket_path():
       short_id = uuid.uuid4().hex[:8]
       sock_path = f"/tmp/harness-council-{short_id}.sock"
       yield sock_path
       if os.path.exists(sock_path):
           os.unlink(sock_path)
       lock_path = sock_path + ".lock"
       if os.path.exists(lock_path):
           os.unlink(lock_path)


   @pytest.fixture
   def worktree(tmp_path):
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
       subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
       subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
       (tmp_path / "file.txt").write_text("content")
       subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
       subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True)
       return tmp_path


   def send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
       sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
       sock.settimeout(timeout)
       sock.connect(socket_path)
       sock.sendall(json.dumps(command).encode() + b"\n")
       response = b""
       while True:
           chunk = sock.recv(4096)
           if not chunk:
               break
           response += chunk
           if b"\n" in response:
               break
       sock.close()
       return json.loads(response.decode().strip())


   def test_amendments_work_together(socket_path, worktree):
       """All three Council amendments should work in harmony."""
       from harness.daemon import HarnessDaemon
       from harness.state import WorkflowState, Task, TaskStatus, StateManager

       # Amendment C: Create valid DAG (no cycles)
       manager = StateManager(worktree)
       state = WorkflowState(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="First task",
                   status=TaskStatus.PENDING,
                   dependencies=[],
               ),
               "task-2": Task(
                   id="task-2",
                   description="Second task",
                   status=TaskStatus.PENDING,
                   dependencies=["task-1"],
               ),
           }
       )
       manager.save(state)  # Should not raise (valid DAG)

       # Amendment B: Daemon starts with capability check
       daemon = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.1)

       try:
           # Amendment A: Exec logs duration_ms
           response = send_command(
               socket_path,
               {"command": "exec", "args": ["echo", "test"]},
           )
           assert response["status"] == "ok"

           # Verify duration_ms in trajectory
           trajectory_file = worktree / ".claude" / "trajectory.jsonl"
           with open(trajectory_file, "r") as f:
               lines = f.readlines()
               exec_events = [json.loads(l) for l in lines if "exec" in l]
               assert any("duration_ms" in e for e in exec_events)
       finally:
           daemon.shutdown()
           server_thread.join(timeout=1)


   def test_cyclic_dag_rejected_at_boundary(tmp_path):
       """Amendment C: Cyclic dependencies must be rejected."""
       from harness.state import WorkflowState, Task, TaskStatus, StateManager

       manager = StateManager(tmp_path)
       cyclic_state = WorkflowState(
           tasks={
               "a": Task(id="a", description="A", status=TaskStatus.PENDING, dependencies=["b"]),
               "b": Task(id="b", description="B", status=TaskStatus.PENDING, dependencies=["a"]),
           }
       )

       with pytest.raises(ValueError, match="[Cc]ycle"):
           manager.save(cyclic_state)
   ```

2. **Run tests:**
   ```bash
   pytest tests/harness/test_integration_council.py -v
   ```

3. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add Council amendments integration tests"
   ```

---

### Task 8: Code Review

**Effort:** simple (3-10 tool calls)

**Files:**
- All modified files from Tasks 1-7

**Instructions:**

1. Run full test suite:
   ```bash
   pytest tests/harness/ -v
   ```

2. Verify all Council requirements met:
   - [ ] Amendment A: `duration_ms` in exec trajectory events
   - [ ] Amendment B: `check_capabilities()` called on daemon startup
   - [ ] Amendment C: `validate_dag()` rejects cycles on save

3. Check CLAUDE.md compliance:
   - [ ] Uses `time.monotonic()` for duration (not `time.time()`)
   - [ ] Atomic writes preserved
   - [ ] Lock hierarchy maintained
   - [ ] No asyncio introduced

---

## Parallel Execution Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2, 3 | Independent: daemon exec, LocalRuntime caps, DockerRuntime caps |
| Group 2 | 4, 5 | Depends on Group 1 completion |
| Group 3 | 6 | Depends on Task 5 |
| Group 4 | 7, 8 | Integration and review after all features |
