# Test Coverage to 95%+ Implementation Plan

**Goal:** Add missing edge case and integration tests to achieve 95%+ coverage, focusing on critical paths per CLAUDE.md doctrines.

**Architecture:** Add new test cases to existing test files following established patterns (pytest fixtures, threading tests, atomic write verification). Focus on uncovered lines identified by `pytest-cov`: state.py (89%), daemon.py (84%), runtime.py (94%), client.py (25%), acp.py (91%).

**Current Coverage:** 67% overall (146 tests passing)

**Target Coverage:** 95%+ on critical modules (state, daemon, runtime, trajectory, git)

---

## Coverage Gaps Analysis

| Module | Current | Missing Lines | Priority |
|--------|---------|---------------|----------|
| state.py | 89% | 172-177, 206-211, 246-251, 255 (auto-load branches) | HIGH |
| daemon.py | 84% | 55, 60-62, 85, 89, 199-200, 309-310, 379, 423-434 | HIGH |
| runtime.py | 94% | 79, 84, 120, 128, 315-316 | MEDIUM |
| client.py | 25% | Most CLI commands | LOW (integration tests exist) |
| acp.py | 91% | 66-70 (worker shutdown path) | MEDIUM |
| trajectory.py | 98% | 85 (error path) | LOW |

---

### Task 1: StateManager Auto-Load Branch Coverage

**Effort:** simple (5-8 tool calls)

**Files:**
- Modify: `tests/harness/test_state.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_update_without_prior_load(tmp_path):
       """update() should auto-load state from disk if not in memory."""
       manager = StateManager(tmp_path)
       # Create state file directly (bypassing manager.save)
       state_file = tmp_path / ".claude" / "dev-workflow-state.json"
       state_file.parent.mkdir(parents=True, exist_ok=True)
       state_file.write_text(json.dumps({
           "tasks": {
               "task-1": {
                   "id": "task-1",
                   "description": "Test",
                   "status": "pending",
                   "dependencies": [],
                   "timeout_seconds": 600
               }
           }
       }))

       # Update without calling load() first
       updated = manager.update(tasks={
           "task-1": Task(id="task-1", description="Updated",
                         status=TaskStatus.COMPLETED, dependencies=[])
       })
       assert updated.tasks["task-1"].status == TaskStatus.COMPLETED

   def test_update_raises_when_no_state(tmp_path):
       """update() should raise ValueError when no state file exists."""
       manager = StateManager(tmp_path)
       with pytest.raises(ValueError, match="No state loaded"):
           manager.update(tasks={})

   def test_claim_task_auto_loads_state(tmp_path):
       """claim_task() should auto-load state from disk."""
       # Similar pattern - test lines 206-211

   def test_complete_task_auto_loads_state(tmp_path):
       """complete_task() should auto-load state from disk."""
       # Similar pattern - test lines 246-251

   def test_complete_task_raises_for_missing_task(tmp_path):
       """complete_task() should raise ValueError for unknown task_id."""
       # Test line 255
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_state.py -v -k "auto_load or raises"
   ```

3. **Implement MINIMAL code** - Tests target existing code paths

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_state.py -v --cov=src/harness/state --cov-report=term-missing
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(state): add auto-load branch coverage"
   ```

---

### Task 2: Daemon Handler Edge Cases

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_handle_empty_request_line(daemon_manager):
       """Handler should gracefully handle empty request."""
       # Test line 55 - empty line handling
       socket_path, send_command = daemon_manager
       sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
       sock.connect(socket_path)
       sock.sendall(b"\n")  # Empty line
       sock.close()
       # Should not crash daemon - verify with ping
       resp = send_command({"command": "ping"})
       assert resp["status"] == "ok"

   def test_handle_malformed_json_request(daemon_manager):
       """Handler should return error for malformed JSON."""
       # Test lines 60-62 - exception handling
       socket_path, send_command = daemon_manager
       sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
       sock.connect(socket_path)
       sock.sendall(b"not json\n")
       response = sock.recv(4096)
       sock.close()
       data = json.loads(response.decode().strip())
       assert data["status"] == "error"

   def test_handle_missing_command(daemon_manager):
       """Handler should return error when command field missing."""
       # Test line 85
       socket_path, send_command = daemon_manager
       resp = send_command({"not_command": "value"})
       assert resp["status"] == "error"
       assert "Missing command" in resp["message"]

   def test_handle_unknown_command(daemon_manager):
       """Handler should return error for unknown command."""
       # Test line 89
       socket_path, send_command = daemon_manager
       resp = send_command({"command": "unknown_cmd"})
       assert resp["status"] == "error"
       assert "Unknown command" in resp["message"]

   def test_task_claim_exception_handling(daemon_manager, worktree):
       """task_claim should handle state errors gracefully."""
       # Test lines 199-200 - exception in claim
       # Delete state file mid-operation to trigger error

   def test_exec_general_exception(daemon_manager):
       """exec should handle unexpected errors gracefully."""
       # Test lines 309-310
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "empty_request or malformed or missing_command or unknown_command"
   ```

3. **Implement MINIMAL code** - Tests target existing code paths

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v --cov=src/harness/daemon --cov-report=term-missing
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(daemon): add handler edge case coverage"
   ```

---

### Task 3: Runtime PathMapper Edge Cases

**Effort:** simple (5-8 tool calls)

**Files:**
- Modify: `tests/harness/test_runtime.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_path_mapper_to_host_abstract():
       """PathMapper.to_host is abstract - cannot instantiate."""
       # Test line 84 - abstract method
       from harness.runtime import PathMapper
       with pytest.raises(TypeError):
           PathMapper()

   def test_volume_mapper_path_outside_root():
       """VolumeMapper should pass through paths outside mapped root."""
       # Test lines 120, 128 - paths that don't start with root
       mapper = VolumeMapper("/host/workspace", "/container/workspace")
       # Host path outside root
       assert mapper.to_runtime("/other/path") == "/other/path"
       # Container path outside root
       assert mapper.to_host("/other/container/path") == "/other/container/path"

   def test_docker_runtime_exclusive_lock():
       """DockerRuntime should acquire lock when exclusive=True."""
       # Test lines 315-316
       # Mock docker to verify lock acquisition
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_runtime.py -v -k "abstract or outside_root or exclusive"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_runtime.py -v --cov=src/harness/runtime --cov-report=term-missing
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(runtime): add path mapper edge cases"
   ```

---

### Task 4: ACP Emitter Shutdown and Error Paths

**Effort:** simple (5-8 tool calls)

**Files:**
- Modify: `tests/harness/test_acp.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_acp_worker_send_error_disables():
       """Worker should disable emitter after send failure."""
       # Test lines 66-70 - send failure path
       import socket

       # Create server that accepts then closes immediately
       server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
       server.bind(("127.0.0.1", 0))
       port = server.getsockname()[1]
       server.listen(1)

       emitter = ACPEmitter(host="127.0.0.1", port=port)

       # Accept connection then close it to trigger send error
       def accept_and_close():
           conn, _ = server.accept()
           time.sleep(0.1)  # Let emitter connect
           conn.close()

       threading.Thread(target=accept_and_close).start()

       # Emit while connection is alive
       emitter.emit({"event": "test1"})
       time.sleep(0.2)

       # Emit after connection closed - should trigger error path
       emitter.emit({"event": "test2"})
       time.sleep(0.2)

       assert emitter._disabled is True
       emitter.close()
       server.close()

   def test_acp_worker_cleanup_on_shutdown():
       """Worker should clean up socket on shutdown."""
       # Test lines 73-75 - socket cleanup
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_acp.py -v -k "send_error or cleanup"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_acp.py -v --cov=src/harness/acp --cov-report=term-missing
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(acp): add worker error and cleanup coverage"
   ```

---

### Task 5: Daemon Signal Handling and Shutdown

**Effort:** standard (10-12 tool calls)

**Files:**
- Modify: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_daemon_sigterm_triggers_shutdown(tmp_path):
       """SIGTERM should trigger graceful daemon shutdown."""
       # Test lines 423-428 - signal handling in run_daemon
       import signal
       import subprocess
       import sys

       # Initialize git repo
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
       subprocess.run(["git", "config", "user.email", "test@test.com"],
                     cwd=tmp_path, capture_output=True, check=True)
       subprocess.run(["git", "config", "user.name", "Test"],
                     cwd=tmp_path, capture_output=True, check=True)
       (tmp_path / "f.txt").write_text("x")
       subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
       subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)

       socket_path = f"/tmp/harness-sigtest-{uuid.uuid4().hex[:8]}.sock"

       # Start daemon via subprocess
       proc = subprocess.Popen(
           [sys.executable, "-m", "harness.daemon", socket_path, str(tmp_path)],
           stdout=subprocess.PIPE,
           stderr=subprocess.PIPE,
       )

       # Wait for socket
       for _ in range(50):
           if Path(socket_path).exists():
               break
           time.sleep(0.1)

       # Send SIGTERM
       proc.send_signal(signal.SIGTERM)

       # Wait for graceful exit
       proc.wait(timeout=5)

       # Should exit cleanly (code 0)
       assert proc.returncode == 0

       # Socket should be cleaned up
       assert not Path(socket_path).exists()

   def test_daemon_sigint_triggers_shutdown(tmp_path):
       """SIGINT should trigger graceful daemon shutdown."""
       # Similar test for SIGINT (Ctrl+C)
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "sigterm or sigint"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v --cov=src/harness/daemon --cov-report=term-missing
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(daemon): add signal handling coverage"
   ```

---

### Task 6: Daemon Lock File Cleanup

**Effort:** simple (5-8 tool calls)

**Files:**
- Modify: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_daemon_server_close_removes_lock_file(worktree):
       """server_close() should remove socket and lock files."""
       # Test lines 405-418
       socket_path = f"/tmp/harness-close-{uuid.uuid4().hex[:8]}.sock"
       lock_path = socket_path + ".lock"

       daemon = HarnessDaemon(socket_path, str(worktree))

       # Verify files exist after init
       assert Path(socket_path).exists()
       assert Path(lock_path).exists()

       # Close daemon
       daemon.server_close()

       # Verify files are cleaned up
       assert not Path(socket_path).exists()
       assert not Path(lock_path).exists()

   def test_daemon_stale_socket_removed_on_init(worktree):
       """Daemon should remove stale socket file on init."""
       # Test lines 377-379
       socket_path = f"/tmp/harness-stale-{uuid.uuid4().hex[:8]}.sock"

       # Create stale socket file
       Path(socket_path).touch()
       assert Path(socket_path).exists()

       # Init should remove stale socket
       daemon = HarnessDaemon(socket_path, str(worktree))

       # Verify daemon is functional
       # ... start server and send ping

       daemon.server_close()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "lock_file or stale_socket"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(daemon): add lock file cleanup coverage"
   ```

---

### Task 7: Trajectory Logger Error Handling

**Effort:** simple (3-5 tool calls)

**Files:**
- Modify: `tests/harness/test_trajectory.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_trajectory_tail_handles_decode_error(tmp_path):
       """tail() should skip lines that fail JSON decode."""
       # Test line 85 - JSONDecodeError handling
       trajectory_file = tmp_path / "trajectory.jsonl"

       # Write mix of valid and invalid lines
       with trajectory_file.open("w") as f:
           f.write('{"event": "valid1"}\n')
           f.write('invalid json line\n')
           f.write('{"event": "valid2"}\n')

       logger = TrajectoryLogger(trajectory_file)
       events = logger.tail(5)

       # Should only return valid events
       assert len(events) == 2
       assert events[0]["event"] == "valid1"
       assert events[1]["event"] == "valid2"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_trajectory.py -v -k "decode_error"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_trajectory.py -v --cov=src/harness/trajectory --cov-report=term-missing
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(trajectory): add decode error handling coverage"
   ```

---

### Task 8: Performance Regression Tests (Critical Path)

**Effort:** standard (10-12 tool calls)

**Files:**
- Create: `tests/harness/test_performance.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   """Performance regression tests for critical paths."""
   import time
   import pytest
   from harness.state import StateManager, Task, TaskStatus, WorkflowState
   from harness.trajectory import TrajectoryLogger

   class TestPerformanceRegression:
       """Performance tests per CLAUDE.md Algorithmic Efficiency doctrine."""

       def test_claim_task_scales_with_dag_size(self, tmp_path):
           """claim_task should maintain O(V+E) complexity at 1000 tasks."""
           manager = StateManager(tmp_path)

           # Create 1000 task DAG with chain dependencies
           tasks = {}
           for i in range(1000):
               deps = [f"task-{i-1}"] if i > 0 else []
               tasks[f"task-{i}"] = Task(
                   id=f"task-{i}",
                   description=f"Task {i}",
                   status=TaskStatus.COMPLETED if i < 999 else TaskStatus.PENDING,
                   dependencies=deps,
               )

           manager.save(WorkflowState(tasks=tasks))

           # Measure claim time
           start = time.monotonic()
           task = manager.claim_task("worker-1")
           elapsed = time.monotonic() - start

           assert task is not None
           assert task.id == "task-999"
           # Should complete in <100ms even with 1000 tasks
           assert elapsed < 0.1, f"claim_task took {elapsed:.3f}s, expected <0.1s"

       def test_trajectory_tail_on_large_file(self, tmp_path):
           """tail() should be O(k) not O(N) on large files."""
           trajectory_file = tmp_path / "trajectory.jsonl"
           logger = TrajectoryLogger(trajectory_file)

           # Write 50K events (~5MB file)
           for i in range(50000):
               logger.log({"event": f"event-{i}", "data": "x" * 100})

           # Measure tail time
           start = time.monotonic()
           events = logger.tail(10)
           elapsed = time.monotonic() - start

           assert len(events) == 10
           # Should complete in <50ms regardless of file size
           assert elapsed < 0.05, f"tail took {elapsed:.3f}s, expected <0.05s"

       def test_dag_validation_on_large_graph(self, tmp_path):
           """validate_dag should complete in reasonable time for 1000 nodes."""
           tasks = {}
           # Create wide DAG (10 roots, each with 99 children)
           for root in range(10):
               tasks[f"root-{root}"] = Task(
                   id=f"root-{root}",
                   description=f"Root {root}",
                   status=TaskStatus.PENDING,
                   dependencies=[],
               )
               for child in range(99):
                   tasks[f"child-{root}-{child}"] = Task(
                       id=f"child-{root}-{child}",
                       description=f"Child {root}-{child}",
                       status=TaskStatus.PENDING,
                       dependencies=[f"root-{root}"],
                   )

           state = WorkflowState(tasks=tasks)

           start = time.monotonic()
           state.validate_dag()  # Should not raise
           elapsed = time.monotonic() - start

           # Should complete in <100ms
           assert elapsed < 0.1, f"validate_dag took {elapsed:.3f}s, expected <0.1s"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_performance.py -v
   ```

3. **Implement MINIMAL code** - Tests verify existing implementations meet performance requirements

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(perf): add performance regression tests"
   ```

---

### Task 9: Client Import Constraints Verification

**Effort:** simple (3-5 tool calls)

**Files:**
- Modify: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_client_does_not_import_pydantic():
       """Client module must not import pydantic (startup performance)."""
       # This test already exists but verify it's comprehensive
       import sys

       # Remove harness modules to test fresh import
       modules_to_remove = [k for k in sys.modules if k.startswith("harness")]
       for mod in modules_to_remove:
           del sys.modules[mod]

       # Import client in isolation
       import importlib
       client = importlib.import_module("harness.client")

       # Check pydantic is NOT in sys.modules
       pydantic_modules = [k for k in sys.modules if "pydantic" in k]
       assert len(pydantic_modules) == 0, f"Client imported pydantic: {pydantic_modules}"

   def test_client_startup_time():
       """Client module should import in <50ms."""
       import subprocess
       import sys

       # Measure import time
       result = subprocess.run(
           [sys.executable, "-c", """
import time
start = time.monotonic()
import harness.client
elapsed = time.monotonic() - start
print(elapsed)
"""],
           capture_output=True,
           text=True,
       )

       elapsed = float(result.stdout.strip())
       # Should import in <50ms (allow 100ms for CI variance)
       assert elapsed < 0.1, f"Client import took {elapsed:.3f}s, expected <0.1s"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_client.py -v -k "import_pydantic or startup_time"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(client): verify import constraints and startup time"
   ```

---

### Task 10: Integration Test - Lock Hierarchy Verification

**Effort:** standard (10-12 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_lock_hierarchy_trajectory_after_state(workflow_with_tasks):
       """Trajectory logging must happen AFTER state lock is released.

       CLAUDE.md Lock Hierarchy: State (1) > Trajectory (2) > Execution (3)
       This test verifies the release-then-log pattern.
       """
       import threading

       send_command = workflow_with_tasks["send_command"]
       worktree = workflow_with_tasks["worktree"]
       trajectory_file = worktree / ".claude" / "trajectory.jsonl"

       # Clear trajectory
       if trajectory_file.exists():
           trajectory_file.unlink()

       errors = []
       results = []
       lock = threading.Lock()

       def claim_and_check(worker_id):
           try:
               # Claim task
               resp = send_command({"command": "task_claim", "worker_id": worker_id})

               # Immediately read trajectory to verify it was written
               # (this would deadlock if state lock was held during log)
               if trajectory_file.exists():
                   lines = trajectory_file.read_text().strip().split("\n")
                   with lock:
                       results.append((worker_id, len(lines)))
           except Exception as e:
               with lock:
                   errors.append((worker_id, str(e)))

       # Run multiple claims in parallel
       threads = [threading.Thread(target=claim_and_check, args=(f"worker-{i}",))
                  for i in range(5)]
       for t in threads:
           t.start()
       for t in threads:
           t.join(timeout=10)

       # No deadlocks or errors
       assert len(errors) == 0, f"Errors: {errors}"
       # Trajectory was written (proves release-then-log pattern works)
       assert trajectory_file.exists()

   def test_concurrent_state_and_trajectory_operations(workflow_with_tasks):
       """Concurrent operations should not deadlock."""
       import threading
       import time

       send_command = workflow_with_tasks["send_command"]
       worktree = workflow_with_tasks["worktree"]

       errors = []
       completed = []
       lock = threading.Lock()

       def state_operation(op_id):
           try:
               start = time.monotonic()
               resp = send_command({"command": "get_state"})
               elapsed = time.monotonic() - start
               with lock:
                   completed.append((op_id, "state", elapsed))
           except Exception as e:
               with lock:
                   errors.append((op_id, str(e)))

       def exec_operation(op_id):
           try:
               start = time.monotonic()
               resp = send_command({
                   "command": "exec",
                   "args": ["echo", "test"],
                   "cwd": str(worktree),
               })
               elapsed = time.monotonic() - start
               with lock:
                   completed.append((op_id, "exec", elapsed))
           except Exception as e:
               with lock:
                   errors.append((op_id, str(e)))

       # Mix state and exec operations
       threads = []
       for i in range(10):
           if i % 2 == 0:
               threads.append(threading.Thread(target=state_operation, args=(i,)))
           else:
               threads.append(threading.Thread(target=exec_operation, args=(i,)))

       for t in threads:
           t.start()
       for t in threads:
           t.join(timeout=30)

       assert len(errors) == 0, f"Errors: {errors}"
       assert len(completed) == 10, f"Only {len(completed)} completed"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_integration.py -v -k "lock_hierarchy or concurrent"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add lock hierarchy verification"
   ```

---

### Task 11: Plan Import Error Handling

**Effort:** simple (5-8 tool calls)

**Files:**
- Modify: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_plan_import_missing_content(daemon_manager):
       """plan_import should error when content is missing."""
       socket_path, send_command = daemon_manager
       resp = send_command({"command": "plan_import"})
       assert resp["status"] == "error"
       assert "content required" in resp["message"].lower()

   def test_plan_import_invalid_json(daemon_manager):
       """plan_import should error for invalid JSON in content."""
       socket_path, send_command = daemon_manager
       resp = send_command({"command": "plan_import", "content": "no json here"})
       assert resp["status"] == "error"

   def test_plan_import_cyclic_deps(daemon_manager):
       """plan_import should reject plans with cyclic dependencies."""
       socket_path, send_command = daemon_manager
       content = '''```json
       {
           "goal": "Test",
           "tasks": {
               "a": {"description": "A", "dependencies": ["b"]},
               "b": {"description": "B", "dependencies": ["a"]}
           }
       }
       ```'''
       resp = send_command({"command": "plan_import", "content": content})
       assert resp["status"] == "error"
       assert "cycle" in resp["message"].lower()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "plan_import"
   ```

3. **Implement MINIMAL code**

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(daemon): add plan import error handling"
   ```

---

### Task 12: Final Coverage Verification and Report

**Effort:** simple (3-5 tool calls)

**Files:**
- No new files

**TDD Instructions (MANDATORY):**

1. **Run full test suite with coverage:**
   ```bash
   uv run pytest --cov=src/harness --cov-report=term-missing --cov-report=html -v
   ```

2. **Verify coverage targets:**
   - state.py: >= 95%
   - daemon.py: >= 95%
   - runtime.py: >= 95%
   - trajectory.py: >= 98%
   - git.py: >= 95%
   - Overall: >= 90%

3. **Generate coverage badge/report**

4. **Commit:**
   ```bash
   git add -A && git commit -m "test: verify 95%+ coverage achieved"
   ```

---

## Parallel Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 3, 4, 7 | Independent modules (state, runtime, acp, trajectory) |
| Group 2 | 2, 5, 6, 11 | All daemon.py tests |
| Group 3 | 8, 9 | Performance and client tests |
| Group 4 | 10 | Integration tests (depends on daemon tests) |
| Group 5 | 12 | Final verification (depends on all) |

---

### Final Task: Code Review

Review all test additions for:
- Proper TDD cycle followed
- Test isolation (no shared state between tests)
- Descriptive test names explaining what's tested
- Coverage targets achieved per module
