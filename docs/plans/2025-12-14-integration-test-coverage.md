# Integration Test Coverage Expansion

**Goal:** Add missing integration test flows to `tests/harness/test_integration.py` covering task claim idempotency, lease renewal, timeout reclaim, ownership validation, parallel workers, CLI task commands, exec with timeout, signal decoding, and DAG cycle rejection.

**Architecture:** All tests use the existing `workflow_with_tasks` fixture pattern (daemon in thread + `send_command` helper). CLI tests use subprocess with `HARNESS_SOCKET`/`HARNESS_WORKTREE` env vars. No new files needed - all tests append to `test_integration.py`.

---

## Task 1: Task Claim Idempotency

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_task_claim_idempotency(workflow_with_tasks):
       """Same worker claiming twice returns same task with is_retry=True."""
       send_command = workflow_with_tasks["send_command"]

       # First claim
       resp1 = send_command({"command": "task_claim", "worker_id": "worker-1"})
       assert resp1["status"] == "ok"
       assert resp1["data"]["task"]["id"] == "task-1"
       assert resp1["data"]["is_retry"] is False

       # Second claim by same worker - should return same task
       resp2 = send_command({"command": "task_claim", "worker_id": "worker-1"})
       assert resp2["status"] == "ok"
       assert resp2["data"]["task"]["id"] == "task-1"  # Same task
       assert resp2["data"]["is_retry"] is True  # Flagged as retry
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_task_claim_idempotency -v
   ```

3. **Implement MINIMAL code** - Test only, implementation already exists in daemon

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add task claim idempotency test"
   ```

---

## Task 2: Lease Renewal on Re-claim

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_lease_renewal_on_reclaim(workflow_with_tasks):
       """Re-claiming updates started_at timestamp (lease renewal)."""
       from datetime import datetime, timezone

       send_command = workflow_with_tasks["send_command"]
       manager = workflow_with_tasks["manager"]

       # First claim
       resp1 = send_command({"command": "task_claim", "worker_id": "worker-1"})
       assert resp1["status"] == "ok"

       # Get initial started_at
       state1 = manager.load()
       started_at_1 = state1.tasks["task-1"].started_at
       assert started_at_1 is not None

       # Brief delay to ensure timestamp difference
       import time
       time.sleep(0.1)

       # Re-claim (lease renewal)
       resp2 = send_command({"command": "task_claim", "worker_id": "worker-1"})
       assert resp2["status"] == "ok"

       # Verify started_at was updated
       state2 = manager.load()
       started_at_2 = state2.tasks["task-1"].started_at
       assert started_at_2 > started_at_1  # Timestamp advanced
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_lease_renewal_on_reclaim -v
   ```

3. **Implement MINIMAL code** - Test only

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add lease renewal verification test"
   ```

---

## Task 3: Timeout and Reclaim by Different Worker

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test + fixture)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   @pytest.fixture
   def workflow_with_short_timeout(integration_worktree):
       """Set up workflow with very short task timeout for reclaim testing."""
       import socket as socket_module
       import threading

       from harness.daemon import HarnessDaemon
       from harness.state import StateManager, Task, TaskStatus, WorkflowState

       worktree = integration_worktree["worktree"]
       socket_path = integration_worktree["socket"]

       # Create task with 1 second timeout
       manager = StateManager(worktree)
       state = WorkflowState(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Short timeout task",
                   status=TaskStatus.PENDING,
                   dependencies=[],
                   timeout_seconds=1,  # Very short for testing
               ),
           }
       )
       manager.save(state)

       daemon = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.2)

       def send_command(cmd, max_retries=3):
           for attempt in range(max_retries):
               sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
               sock.settimeout(10.0)
               try:
                   sock.connect(socket_path)
                   sock.sendall(json.dumps(cmd).encode() + b"\n")
                   response = b""
                   while True:
                       chunk = sock.recv(4096)
                       if not chunk:
                           break
                       response += chunk
                       if b"\n" in response:
                           break
                   return json.loads(response.decode().strip())
               except ConnectionRefusedError:
                   if attempt < max_retries - 1:
                       time.sleep(0.1 * (attempt + 1))
                       continue
                   raise
               finally:
                   sock.close()

       yield {
           "worktree": worktree,
           "socket": socket_path,
           "manager": manager,
           "daemon": daemon,
           "send_command": send_command,
       }

       daemon.shutdown()
       server_thread.join(timeout=1)


   def test_timeout_reclaim_by_different_worker(workflow_with_short_timeout):
       """Timed-out task can be reclaimed by different worker with is_reclaim=True."""
       send_command = workflow_with_short_timeout["send_command"]

       # Worker-1 claims task
       resp1 = send_command({"command": "task_claim", "worker_id": "worker-1"})
       assert resp1["status"] == "ok"
       assert resp1["data"]["task"]["id"] == "task-1"

       # Wait for timeout (1 second + buffer)
       import time
       time.sleep(1.5)

       # Worker-2 can now reclaim the timed-out task
       resp2 = send_command({"command": "task_claim", "worker_id": "worker-2"})
       assert resp2["status"] == "ok"
       assert resp2["data"]["task"]["id"] == "task-1"
       assert resp2["data"]["is_reclaim"] is True  # Flagged as reclaim
       assert resp2["data"]["task"]["claimed_by"] == "worker-2"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_timeout_reclaim_by_different_worker -v
   ```

3. **Implement MINIMAL code** - Test only

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add timeout reclaim with is_reclaim flag test"
   ```

---

## Task 4: Ownership Validation on Complete

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_ownership_validation_on_complete(workflow_with_tasks):
       """Worker B cannot complete Worker A's task."""
       send_command = workflow_with_tasks["send_command"]

       # Worker-1 claims task-1
       resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
       assert resp["status"] == "ok"
       assert resp["data"]["task"]["id"] == "task-1"

       # Worker-2 tries to complete Worker-1's task - should fail
       resp = send_command({
           "command": "task_complete",
           "task_id": "task-1",
           "worker_id": "worker-2"
       })
       assert resp["status"] == "error"
       assert "not claimed by" in resp["message"].lower()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_ownership_validation_on_complete -v
   ```

3. **Implement MINIMAL code** - Test only

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add ownership validation test"
   ```

---

## Task 5: Parallel Workers Get Unique Tasks

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test + modified fixture)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   @pytest.fixture
   def workflow_with_parallel_tasks(integration_worktree):
       """Set up workflow with multiple independent tasks for parallel claiming."""
       import socket as socket_module
       import threading

       from harness.daemon import HarnessDaemon
       from harness.state import StateManager, Task, TaskStatus, WorkflowState

       worktree = integration_worktree["worktree"]
       socket_path = integration_worktree["socket"]

       # Create 3 independent tasks (no dependencies)
       manager = StateManager(worktree)
       state = WorkflowState(
           tasks={
               "task-1": Task(id="task-1", description="Independent 1", status=TaskStatus.PENDING, dependencies=[]),
               "task-2": Task(id="task-2", description="Independent 2", status=TaskStatus.PENDING, dependencies=[]),
               "task-3": Task(id="task-3", description="Independent 3", status=TaskStatus.PENDING, dependencies=[]),
           }
       )
       manager.save(state)

       daemon = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.2)

       def send_command(cmd, max_retries=3):
           for attempt in range(max_retries):
               sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
               sock.settimeout(10.0)
               try:
                   sock.connect(socket_path)
                   sock.sendall(json.dumps(cmd).encode() + b"\n")
                   response = b""
                   while True:
                       chunk = sock.recv(4096)
                       if not chunk:
                           break
                       response += chunk
                       if b"\n" in response:
                           break
                   return json.loads(response.decode().strip())
               except ConnectionRefusedError:
                   if attempt < max_retries - 1:
                       time.sleep(0.1 * (attempt + 1))
                       continue
                   raise
               finally:
                   sock.close()

       yield {
           "worktree": worktree,
           "socket": socket_path,
           "manager": manager,
           "daemon": daemon,
           "send_command": send_command,
       }

       daemon.shutdown()
       server_thread.join(timeout=1)


   def test_parallel_workers_get_unique_tasks(workflow_with_parallel_tasks):
       """Multiple workers claiming in parallel get different tasks."""
       import threading

       send_command = workflow_with_parallel_tasks["send_command"]

       claimed_tasks = []
       errors = []
       lock = threading.Lock()

       def claim_task(worker_id):
           try:
               resp = send_command({"command": "task_claim", "worker_id": worker_id})
               if resp["status"] == "ok" and resp["data"]["task"]:
                   with lock:
                       claimed_tasks.append(resp["data"]["task"]["id"])
           except Exception as e:
               with lock:
                   errors.append(str(e))

       # Launch 3 workers in parallel
       threads = [threading.Thread(target=claim_task, args=(f"worker-{i}",)) for i in range(3)]
       for t in threads:
           t.start()
       for t in threads:
           t.join()

       assert len(errors) == 0, f"Errors: {errors}"
       assert len(claimed_tasks) == 3
       # All tasks should be unique
       assert len(set(claimed_tasks)) == 3
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_parallel_workers_get_unique_tasks -v
   ```

3. **Implement MINIMAL code** - Test only

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add parallel workers unique task assignment test"
   ```

---

## Task 6: CLI Task Claim and Complete

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_cli_task_claim_and_complete(workflow_with_tasks):
       """Test task claim and complete via CLI subprocess."""
       import sys

       worktree = workflow_with_tasks["worktree"]
       socket_path = workflow_with_tasks["socket"]

       env = {
           "HARNESS_SOCKET": socket_path,
           "HARNESS_WORKTREE": str(worktree),
           "PATH": os.environ.get("PATH", ""),
           "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
       }

       # Claim task via CLI
       result = subprocess.run(
           [sys.executable, "-m", "harness", "task", "claim"],
           capture_output=True,
           text=True,
           env=env,
       )
       assert result.returncode == 0, f"task claim failed: {result.stderr}"
       output = json.loads(result.stdout)
       assert output["status"] == "ok"
       assert output["data"]["task"]["id"] == "task-1"

       # Complete task via CLI
       result = subprocess.run(
           [sys.executable, "-m", "harness", "task", "complete", "--id", "task-1"],
           capture_output=True,
           text=True,
           env=env,
       )
       assert result.returncode == 0, f"task complete failed: {result.stderr}"
       output = json.loads(result.stdout)
       assert output["status"] == "ok"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_cli_task_claim_and_complete -v
   ```

3. **Implement MINIMAL code** - Test only

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add CLI task claim and complete test"
   ```

---

## Task 7: CLI Exec with Timeout and Signal Decoding

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_cli_exec_with_timeout_and_signal(workflow_with_tasks):
       """Test exec command with timeout produces signal_name in response."""
       import sys

       worktree = workflow_with_tasks["worktree"]
       socket_path = workflow_with_tasks["socket"]

       env = {
           "HARNESS_SOCKET": socket_path,
           "HARNESS_WORKTREE": str(worktree),
           "PATH": os.environ.get("PATH", ""),
           "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
       }

       # Exec a command that exceeds timeout (sleep 10 with 1s timeout)
       result = subprocess.run(
           [sys.executable, "-m", "harness", "exec", "--timeout", "1", "--", "sleep", "10"],
           capture_output=True,
           text=True,
           env=env,
       )
       # exec should return 0 (command ran, but inner process was killed)
       assert result.returncode == 0, f"exec failed: {result.stderr}"

       output = json.loads(result.stdout)
       assert output["status"] == "ok"
       # Process was killed with SIGTERM (signal 15)
       assert output["data"]["returncode"] < 0
       assert output["data"]["signal_name"] == "SIGTERM"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_cli_exec_with_timeout_and_signal -v
   ```

3. **Implement MINIMAL code** - Test only

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add CLI exec timeout and signal decoding test"
   ```

---

## Task 8: DAG Cycle Rejection at Save

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_dag_cycle_rejection(integration_worktree):
       """Saving workflow with cyclic dependencies raises error."""
       from harness.state import StateManager, Task, TaskStatus, WorkflowState

       worktree = integration_worktree["worktree"]
       manager = StateManager(worktree)

       # Create cyclic dependency: task-1 -> task-2 -> task-1
       state = WorkflowState(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="First",
                   status=TaskStatus.PENDING,
                   dependencies=["task-2"],  # Depends on task-2
               ),
               "task-2": Task(
                   id="task-2",
                   description="Second",
                   status=TaskStatus.PENDING,
                   dependencies=["task-1"],  # Depends on task-1 -> CYCLE!
               ),
           }
       )

       with pytest.raises(ValueError, match="[Cc]ycle"):
           manager.save(state)
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_dag_cycle_rejection -v
   ```

3. **Implement MINIMAL code** - Test only (cycle detection should exist in StateManager.save)

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add DAG cycle rejection test"
   ```

---

## Task 9: Worker ID Stability

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py` (append new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_worker_id_stability_across_invocations(integration_worktree, tmp_path):
       """Worker ID persisted to file and consistent across process invocations."""
       import sys

       worktree = integration_worktree["worktree"]
       socket_path = integration_worktree["socket"]

       # Use a unique worker ID file location for this test
       worker_id_file = tmp_path / "worker.id"

       env = {
           "HARNESS_SOCKET": socket_path,
           "HARNESS_WORKTREE": str(worktree),
           "HARNESS_WORKER_ID_FILE": str(worker_id_file),
           "PATH": os.environ.get("PATH", ""),
           "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
       }

       # First invocation - generates worker ID
       result1 = subprocess.run(
           [sys.executable, "-m", "harness", "worker-id"],
           capture_output=True,
           text=True,
           env=env,
       )
       assert result1.returncode == 0, f"worker-id failed: {result1.stderr}"
       worker_id_1 = result1.stdout.strip()

       # Second invocation - should return same ID
       result2 = subprocess.run(
           [sys.executable, "-m", "harness", "worker-id"],
           capture_output=True,
           text=True,
           env=env,
       )
       assert result2.returncode == 0
       worker_id_2 = result2.stdout.strip()

       assert worker_id_1 == worker_id_2  # Same ID across invocations
       assert worker_id_file.exists()  # File was created
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_worker_id_stability_across_invocations -v
   ```

3. **Implement MINIMAL code** - May need to add `worker-id` CLI command if not exists

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add worker ID stability test"
   ```

---

## Parallel Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2, 4, 8 | Independent tests, no file overlap (all append to test_integration.py but non-conflicting) |
| Group 2 | 3, 5 | Both add new fixtures + tests |
| Group 3 | 6, 7, 9 | CLI subprocess tests |

---

## Task 10: Code Review

**Effort:** standard (10-15 tool calls)

**Files:**
- Review: `tests/harness/test_integration.py` (all new tests)

**Instructions:**
1. Dispatch code-reviewer agent
2. Address any feedback
3. Run full test suite: `pytest tests/harness/test_integration.py -v`
4. Verify all new tests pass
