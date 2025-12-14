# tests/harness/test_integration.py
"""
Integration tests for the complete harness system.
Tests daemon + client + state + git working together.
"""

import json
import os
import subprocess
import threading
import time
import uuid

import pytest


@pytest.fixture
def integration_worktree(tmp_path):
    """Create a complete test environment."""
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True)

    # Use short socket path in /tmp to avoid macOS AF_UNIX path length limit
    socket_id = uuid.uuid4().hex[:8]
    socket_path = f"/tmp/harness-integ-{socket_id}.sock"

    yield {"worktree": tmp_path, "socket": socket_path}

    # Cleanup daemon
    subprocess.run(["pkill", "-f", f"harness.daemon.*{socket_path}"], capture_output=True)
    # Give daemon time to shutdown
    time.sleep(0.2)
    # Clean up socket files
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    if os.path.exists(socket_path + ".lock"):
        os.unlink(socket_path + ".lock")


def test_parallel_git_operations_no_race(integration_worktree):
    """Multiple parallel git operations should not cause index.lock errors.

    Uses the daemon directly in a thread (like test_daemon.py) to avoid
    issues with subprocess spawning and connection backlog.
    """
    import socket as socket_module

    from harness.daemon import HarnessDaemon

    socket_path = integration_worktree["socket"]
    worktree = integration_worktree["worktree"]

    # Start daemon directly in thread (avoids subprocess overhead)
    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.2)  # Let daemon fully start

    def send_command(cmd, max_retries=3):
        """Send command to daemon and return response with retry on connection refused."""
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
                # Socket backlog full - retry after brief delay
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise
            finally:
                sock.close()

    errors = []
    results = []
    lock = threading.Lock()

    def git_status(client_id):
        try:
            resp = send_command({"command": "git", "args": ["status"], "cwd": str(worktree)})
            with lock:
                results.append((client_id, resp["status"]))
            # Check for index.lock errors in stderr
            if resp.get("data", {}).get("stderr"):
                stderr = resp["data"]["stderr"]
                if "index.lock" in stderr.lower():
                    with lock:
                        errors.append((client_id, f"Race condition detected: {stderr}"))
        except Exception as e:
            with lock:
                errors.append((client_id, str(e)))

    try:
        # Launch 10 parallel git status commands
        threads = [threading.Thread(target=git_status, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 10, f"Expected 10 results, got {len(results)}"
        assert all(status == "ok" for _, status in results)
    finally:
        daemon.shutdown()
        server_thread.join(timeout=1)


def test_state_persistence_across_daemon_restart(integration_worktree):
    """State should persist across daemon restarts."""
    from harness.client import send_rpc
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    socket_path = integration_worktree["socket"]
    worktree = integration_worktree["worktree"]

    # Create initial state with v2 JSON schema (task DAG)
    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            tasks={
                "task-1": Task(
                    id="task-1",
                    description="First task",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                ),
            }
        )
    )

    # Connect and update state (auto-spawns daemon)
    new_tasks = {
        "task-1": {
            "id": "task-1",
            "description": "First task",
            "status": "completed",
            "dependencies": [],
            "started_at": None,
            "completed_at": None,
            "claimed_by": "worker-1",
            "timeout_seconds": 600,
        },
    }
    resp = send_rpc(
        socket_path,
        {"command": "update_state", "updates": {"tasks": new_tasks}},
        worktree_root=str(worktree),
    )
    assert resp["status"] == "ok"

    # Shutdown daemon
    send_rpc(socket_path, {"command": "shutdown"}, None)
    # Wait for daemon to fully shutdown
    time.sleep(0.5)

    # Reconnect (should auto-spawn new daemon)
    resp = send_rpc(
        socket_path,
        {"command": "get_state"},
        worktree_root=str(worktree),
    )
    assert resp["status"] == "ok"
    assert resp["data"]["tasks"]["task-1"]["status"] == "completed"  # State persisted


def test_cli_commands(integration_worktree):
    """Test CLI commands work correctly via subprocess."""
    import sys

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    env = {
        "HARNESS_SOCKET": socket_path,
        "HARNESS_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        # Inherit PYTHONPATH so harness module can be found
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    # Test ping (auto-spawns daemon)
    result = subprocess.run(
        [sys.executable, "-m", "harness", "ping"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"ping failed: {result.stderr}"
    assert "ok" in result.stdout

    # Test git command
    result = subprocess.run(
        [sys.executable, "-m", "harness", "git", "--", "status"],
        capture_output=True,
        text=True,
        env=env,
        cwd=worktree,
    )
    assert result.returncode == 0, f"git status failed: {result.stderr}"


def test_cli_get_state_without_workflow(integration_worktree):
    """Test get-state command when no workflow is active."""
    import sys

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    env = {
        "HARNESS_SOCKET": socket_path,
        "HARNESS_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    # get-state should report "No active workflow" and exit 1
    result = subprocess.run(
        [sys.executable, "-m", "harness", "get-state"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1
    assert "No active workflow" in result.stdout


def test_cli_update_state(integration_worktree):
    """Test update-state command works correctly."""
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    # Create initial state with v2 JSON schema (task DAG)
    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            tasks={
                "task-1": Task(
                    id="task-1",
                    description="First task",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                ),
            }
        )
    )

    # Update state via RPC (tests the update mechanism directly)
    from harness.client import send_rpc

    new_tasks = {
        "task-1": {
            "id": "task-1",
            "description": "First task (updated)",
            "status": "completed",
            "dependencies": [],
            "started_at": None,
            "completed_at": None,
            "claimed_by": "worker-1",
            "timeout_seconds": 600,
        },
    }
    resp = send_rpc(
        socket_path,
        {"command": "update_state", "updates": {"tasks": new_tasks}},
        worktree_root=str(worktree),
    )
    assert resp["status"] == "ok"

    # Verify state was updated
    loaded = StateManager(worktree).load()
    assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED


def test_cli_session_start_with_active_workflow(integration_worktree):
    """Test session-start hook outputs correct JSON."""
    import sys

    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    # Create active workflow state with v2 JSON schema (task DAG)
    # 2 completed, 6 pending = 2/8 progress
    manager = StateManager(worktree)
    tasks = {}
    for i in range(1, 9):
        tasks[f"task-{i}"] = Task(
            id=f"task-{i}",
            description=f"Task {i}",
            status=TaskStatus.COMPLETED if i <= 2 else TaskStatus.PENDING,
            dependencies=[f"task-{i - 1}"] if i > 1 else [],
        )
    manager.save(WorkflowState(tasks=tasks))

    env = {
        "HARNESS_SOCKET": socket_path,
        "HARNESS_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    result = subprocess.run(
        [sys.executable, "-m", "harness", "session-start"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"session-start failed: {result.stderr}"

    output = json.loads(result.stdout)
    assert "hookSpecificOutput" in output
    assert "Resuming workflow" in output["hookSpecificOutput"]["additionalContext"]
    assert "2/8" in output["hookSpecificOutput"]["additionalContext"]


def test_cli_shutdown(integration_worktree):
    """Test shutdown command stops the daemon."""
    import sys

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    env = {
        "HARNESS_SOCKET": socket_path,
        "HARNESS_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    # First spawn daemon via ping
    result = subprocess.run(
        [sys.executable, "-m", "harness", "ping"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0

    # Now shutdown
    result = subprocess.run(
        [sys.executable, "-m", "harness", "shutdown"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "Shutdown" in result.stdout

    # Wait for daemon to shutdown
    time.sleep(0.5)

    # Verify daemon is gone by trying to connect without auto-spawn
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
        sock.close()
        pytest.fail("Daemon should have been shutdown")
    except (FileNotFoundError, ConnectionRefusedError):
        pass  # Expected - daemon is down


@pytest.fixture
def workflow_with_tasks(integration_worktree):
    """Set up workflow state with DAG tasks."""
    import socket as socket_module
    import threading

    from harness.daemon import HarnessDaemon
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    # Create workflow state with DAG
    manager = StateManager(worktree)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="First task (no deps)",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Second task (depends on task-1)",
                status=TaskStatus.PENDING,
                dependencies=["task-1"],
            ),
            "task-3": Task(
                id="task-3",
                description="Third task (depends on task-1)",
                status=TaskStatus.PENDING,
                dependencies=["task-1"],
            ),
        }
    )
    manager.save(state)

    # Start daemon
    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.2)  # Let daemon fully start

    def send_command(cmd, max_retries=3):
        """Send command to daemon and return response with retry on connection refused."""
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
                # Socket backlog full - retry after brief delay
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

    # Cleanup
    daemon.shutdown()
    server_thread.join(timeout=1)


def test_full_task_workflow(workflow_with_tasks):
    """End-to-end test: claim task, complete it, verify DAG progression."""
    from harness.state import TaskStatus

    send_command = workflow_with_tasks["send_command"]
    manager = workflow_with_tasks["manager"]

    # Worker 1 claims a task (should get task-1 - no deps)
    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] == "task-1"
    assert resp["data"]["task"]["status"] == "running"

    # Verify task-2 and task-3 are blocked (can't claim yet)
    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"] is None  # No claimable tasks

    # Complete task-1
    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    # Now task-2 and task-3 should be claimable
    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] in ["task-2", "task-3"]

    # Verify state
    state = manager.load()
    assert state.tasks["task-1"].status == TaskStatus.COMPLETED
    assert (
        state.tasks["task-2"].status == TaskStatus.RUNNING
        or state.tasks["task-3"].status == TaskStatus.RUNNING
    )


def test_dag_dependency_enforcement(workflow_with_tasks):
    """Can't claim blocked tasks - dependencies must be satisfied first."""
    send_command = workflow_with_tasks["send_command"]

    # Try to claim any task - should only get task-1 (no deps)
    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] == "task-1"

    # Second worker tries to claim - should get nothing (task-2/3 blocked, task-1 taken)
    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"] is None

    # Complete task-1
    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    # Now worker-2 can claim task-2 or task-3
    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] in ["task-2", "task-3"]


def test_trajectory_logging(workflow_with_tasks):
    """Trajectory file captures claim events."""
    send_command = workflow_with_tasks["send_command"]
    worktree = workflow_with_tasks["worktree"]
    trajectory_file = worktree / ".claude" / "trajectory.jsonl"

    # Claim task
    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    # Verify trajectory file exists and has claim event
    assert trajectory_file.exists()
    lines = trajectory_file.read_text().strip().split("\n")
    assert len(lines) >= 1

    # Parse last line (claim event)
    event = json.loads(lines[-1])
    assert event["event_type"] == "task_claim"
    assert event["worker_id"] == "worker-1"
    assert event["task_id"] == "task-1"


def test_json_state_persistence(workflow_with_tasks):
    """State persisted as JSON with correct schema (Council fix verification)."""
    send_command = workflow_with_tasks["send_command"]
    worktree = workflow_with_tasks["worktree"]
    state_file = worktree / ".claude" / "dev-workflow-state.json"

    # Claim and complete a task
    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    # Verify state file exists and is valid JSON
    assert state_file.exists()
    data = json.loads(state_file.read_text())

    # Verify schema structure
    assert "tasks" in data
    assert "task-1" in data["tasks"]
    assert data["tasks"]["task-1"]["status"] == "completed"
    assert data["tasks"]["task-1"]["claimed_by"] == "worker-1"
    assert "started_at" in data["tasks"]["task-1"]
    assert "completed_at" in data["tasks"]["task-1"]


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


def test_lease_renewal_on_reclaim(workflow_with_tasks):
    """Re-claiming updates started_at timestamp (lease renewal)."""
    import time

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
    time.sleep(0.1)

    # Re-claim (lease renewal)
    resp2 = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp2["status"] == "ok"

    # Verify started_at was updated
    state2 = manager.load()
    started_at_2 = state2.tasks["task-1"].started_at
    assert started_at_2 > started_at_1  # Timestamp advanced


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
    import time

    send_command = workflow_with_short_timeout["send_command"]

    # Worker-1 claims task
    resp1 = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp1["status"] == "ok"
    assert resp1["data"]["task"]["id"] == "task-1"

    # Wait for timeout (1 second + buffer)
    time.sleep(1.5)

    # Worker-2 can now reclaim the timed-out task
    resp2 = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp2["status"] == "ok"
    assert resp2["data"]["task"]["id"] == "task-1"
    assert resp2["data"]["is_reclaim"] is True  # Flagged as reclaim
    assert resp2["data"]["task"]["claimed_by"] == "worker-2"


def test_ownership_validation_on_complete(workflow_with_tasks):
    """Worker B cannot complete Worker A's task."""
    send_command = workflow_with_tasks["send_command"]

    # Worker-1 claims task-1
    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] == "task-1"

    # Worker-2 tries to complete Worker-1's task - should fail
    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-2"})
    assert resp["status"] == "error"
    assert "not claimed by" in resp["message"].lower()


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
            "task-1": Task(
                id="task-1",
                description="Independent 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Independent 2",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-3": Task(
                id="task-3",
                description="Independent 3",
                status=TaskStatus.PENDING,
                dependencies=[],
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
    assert output["task"]["id"] == "task-1"

    # Complete task via CLI
    result = subprocess.run(
        [sys.executable, "-m", "harness", "task", "complete", "--id", "task-1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"task complete failed: {result.stderr}"
    assert "Task task-1 completed" in result.stdout


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
