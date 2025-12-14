# tests/harness/test_integration.py
"""
Integration tests for the complete harness system.
Tests daemon + client + state + git working together.
"""

import pytest
import subprocess
import threading
import time
import json
import os
import uuid


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
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True
    )
    (tmp_path / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True
    )

    # Use short socket path in /tmp to avoid macOS AF_UNIX path length limit
    socket_id = uuid.uuid4().hex[:8]
    socket_path = f"/tmp/harness-integ-{socket_id}.sock"

    yield {"worktree": tmp_path, "socket": socket_path}

    # Cleanup daemon
    subprocess.run(
        ["pkill", "-f", f"harness.daemon.*{socket_path}"], capture_output=True
    )
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
    from harness.daemon import HarnessDaemon
    import socket as socket_module

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
            sock = socket_module.socket(
                socket_module.AF_UNIX, socket_module.SOCK_STREAM
            )
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
            resp = send_command(
                {"command": "git", "args": ["status"], "cwd": str(worktree)}
            )
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
    from harness.state import WorkflowState, StateManager

    socket_path = integration_worktree["socket"]
    worktree = integration_worktree["worktree"]

    # Create initial state
    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            workflow="subagent",
            plan="/plan.md",
            current_task=3,
            total_tasks=10,
            worktree=str(worktree),
            base_sha="abc123",
        )
    )

    # Connect and update state (auto-spawns daemon)
    resp = send_rpc(
        socket_path,
        {"command": "update_state", "updates": {"current_task": 5}},
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
    assert resp["data"]["current_task"] == 5  # State persisted


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
    import sys
    from harness.state import WorkflowState, StateManager

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    # Create initial state
    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            workflow="execute-plan",
            plan="/plan.md",
            current_task=1,
            total_tasks=5,
            worktree=str(worktree),
            base_sha="abc123",
        )
    )

    env = {
        "HARNESS_SOCKET": socket_path,
        "HARNESS_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    # Update state via CLI
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "update-state",
            "--field",
            "current_task",
            "3",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"update-state failed: {result.stderr}"
    assert "Updated" in result.stdout

    # Verify state was updated
    loaded = StateManager(worktree).load()
    assert loaded.current_task == 3


def test_cli_session_start_with_active_workflow(integration_worktree):
    """Test session-start hook outputs correct JSON."""
    import sys
    from harness.state import WorkflowState, StateManager

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    # Create active workflow state
    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            workflow="subagent",
            plan="/plan.md",
            current_task=2,
            total_tasks=8,
            worktree=str(worktree),
            base_sha="abc123",
        )
    )

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
    assert result.returncode == 0

    output = json.loads(result.stdout)
    assert "hookSpecificOutput" in output
    assert "Resuming subagent" in output["hookSpecificOutput"]["additionalContext"]
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
    from harness.state import StateManager, WorkflowState, Task, TaskStatus
    from harness.daemon import HarnessDaemon
    import threading
    import socket as socket_module

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
            sock = socket_module.socket(
                socket_module.AF_UNIX, socket_module.SOCK_STREAM
            )
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
    resp = send_command({
        "command": "task_complete",
        "task_id": "task-1",
        "worker_id": "worker-1"
    })
    assert resp["status"] == "ok"

    # Now task-2 and task-3 should be claimable
    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] in ["task-2", "task-3"]

    # Verify state
    state = manager.load()
    assert state.tasks["task-1"].status == TaskStatus.COMPLETED
    assert state.tasks["task-2"].status == TaskStatus.RUNNING or state.tasks["task-3"].status == TaskStatus.RUNNING


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
    resp = send_command({
        "command": "task_complete",
        "task_id": "task-1",
        "worker_id": "worker-1"
    })
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

    resp = send_command({
        "command": "task_complete",
        "task_id": "task-1",
        "worker_id": "worker-1"
    })
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
