# tests/harness/test_daemon.py
"""
Tests for the threaded daemon using socketserver.ThreadingMixIn.

The daemon provides:
- Thread-safe state access (Pydantic validation happens here)
- Git mutex protection via GLOBAL_GIT_LOCK
- Single instance guarantee via fcntl.flock
"""

import json
import socket
import threading
import time

import pytest

# socket_path and worktree fixtures are imported from conftest.py


def send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
    """Send command to daemon and get response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
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
        return json.loads(response.decode().strip())
    finally:
        sock.close()


def test_daemon_get_state(socket_path, worktree):
    """Daemon should return state via get_state command."""
    from harness.daemon import HarnessDaemon
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    # Create state file with v2 JSON schema (task DAG)
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
                "task-2": Task(
                    id="task-2",
                    description="Second task",
                    status=TaskStatus.PENDING,
                    dependencies=["task-1"],
                ),
            }
        )
    )

    daemon = HarnessDaemon(socket_path, str(worktree))

    # Start daemon in background thread
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.1)  # Let it start

    try:
        response = send_command(socket_path, {"command": "get_state"})
        assert response["status"] == "ok"
        assert "tasks" in response["data"]
        assert "task-1" in response["data"]["tasks"]
        assert response["data"]["tasks"]["task-1"]["status"] == "pending"
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_daemon_update_state(socket_path, worktree):
    """Daemon should update state via update_state command."""
    from harness.daemon import HarnessDaemon
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    # Create state with v2 JSON schema (task DAG)
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

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.1)

    try:
        # Update by adding a new task to the task DAG
        new_tasks = {
            "task-1": {
                "id": "task-1",
                "description": "First task",
                "status": "completed",
                "dependencies": [],
                "started_at": None,
                "completed_at": None,
                "claimed_by": None,
                "timeout_seconds": 600,
            },
            "task-2": {
                "id": "task-2",
                "description": "Second task",
                "status": "pending",
                "dependencies": ["task-1"],
                "started_at": None,
                "completed_at": None,
                "claimed_by": None,
                "timeout_seconds": 600,
            },
        }
        response = send_command(
            socket_path,
            {
                "command": "update_state",
                "updates": {"tasks": new_tasks},
            },
        )
        assert response["status"] == "ok"

        # Verify persisted
        loaded = StateManager(worktree).load()
        assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED
        assert "task-2" in loaded.tasks
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_daemon_git_operations(socket_path, worktree):
    """Daemon should execute git commands with mutex protection."""
    from harness.daemon import HarnessDaemon

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.1)

    try:
        response = send_command(
            socket_path,
            {
                "command": "git",
                "args": ["rev-parse", "HEAD"],
                "cwd": str(worktree),
            },
        )
        assert response["status"] == "ok"
        assert response["data"]["returncode"] == 0
        assert len(response["data"]["stdout"].strip()) == 40
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_daemon_parallel_clients(socket_path, worktree):
    """Verify daemon handles parallel clients (Python 3.13t threading)."""
    from harness.daemon import HarnessDaemon
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    # Create state with v2 JSON schema (task DAG)
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

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.1)

    results = []
    errors = []

    def client_request(client_id):
        try:
            resp = send_command(socket_path, {"command": "get_state"})
            results.append((client_id, resp["status"]))
        except Exception as e:
            errors.append((client_id, str(e)))

    try:
        # Launch 5 parallel clients
        threads = [threading.Thread(target=client_request, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 5
        assert all(status == "ok" for _, status in results)
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_daemon_single_instance_lock(socket_path, worktree):
    """fcntl.flock should prevent multiple daemons."""
    from harness.daemon import HarnessDaemon

    daemon1 = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon1.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.1)

    try:
        # Second daemon should fail to acquire lock
        with pytest.raises(RuntimeError, match="Another daemon"):
            HarnessDaemon(socket_path, str(worktree))
    finally:
        daemon1.shutdown()
        daemon1.server_close()
        server_thread.join(timeout=2)


@pytest.fixture
def daemon_with_state(socket_path, worktree):
    """Create a daemon with tasks in state."""
    from harness.daemon import HarnessDaemon
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    # Create state with tasks
    manager = StateManager(worktree)
    state = WorkflowState(
        tasks={
            "task1": Task(
                id="task1",
                description="First task",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task2": Task(
                id="task2",
                description="Second task",
                status=TaskStatus.PENDING,
                dependencies=["task1"],
            ),
        }
    )
    manager.save(state)

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.1)

    yield daemon, worktree

    daemon.shutdown()
    daemon.server_close()
    server_thread.join(timeout=2)


def test_handle_task_claim_returns_claimable(daemon_with_state, socket_path):
    """task_claim should return a claimable task."""
    daemon, worktree = daemon_with_state

    response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )

    assert response["status"] == "ok"
    assert response["data"]["task"]["id"] == "task1"
    assert response["data"]["task"]["status"] == "running"
    assert response["data"]["task"]["claimed_by"] == "worker1"
    assert response["data"]["is_retry"] is False
    assert response["data"]["is_reclaim"] is False


def test_handle_task_claim_requires_worker_id(daemon_with_state, socket_path):
    """task_claim should require worker_id parameter."""
    daemon, worktree = daemon_with_state

    response = send_command(
        socket_path,
        {"command": "task_claim"},
    )

    assert response["status"] == "error"
    assert "worker_id" in response["message"]


def test_handle_task_claim_idempotency(daemon_with_state, socket_path):
    """task_claim should return the same task for the same worker with is_retry flag."""
    daemon, worktree = daemon_with_state

    # First claim
    response1 = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )
    assert response1["status"] == "ok"
    assert response1["data"]["task"]["id"] == "task1"
    assert response1["data"]["is_retry"] is False

    # Second claim by same worker
    response2 = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )
    assert response2["status"] == "ok"
    assert response2["data"]["task"]["id"] == "task1"
    assert response2["data"]["is_retry"] is True


def test_handle_task_claim_marks_running(daemon_with_state, socket_path):
    """task_claim should mark task as RUNNING in state."""
    daemon, worktree = daemon_with_state

    response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )

    assert response["status"] == "ok"

    # Verify state was updated
    from harness.state import StateManager

    manager = StateManager(worktree)
    state = manager.load()
    assert state.tasks["task1"].status.value == "running"
    assert state.tasks["task1"].claimed_by == "worker1"
    assert state.tasks["task1"].started_at is not None


def test_handle_task_claim_reclaims_timed_out(daemon_with_state, socket_path):
    """task_claim should reclaim timed out tasks with is_reclaim flag."""
    daemon, worktree = daemon_with_state
    from datetime import datetime, timedelta

    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    # Create a timed out task
    manager = StateManager(worktree)
    state = WorkflowState(
        tasks={
            "task1": Task(
                id="task1",
                description="Timed out task",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker_old",
                started_at=datetime.now() - timedelta(seconds=700),
                timeout_seconds=600,
            ),
        }
    )
    manager.save(state)

    # Reclaim by new worker
    response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker2"},
    )

    assert response["status"] == "ok"
    assert response["data"]["task"]["id"] == "task1"
    assert response["data"]["task"]["claimed_by"] == "worker2"
    assert response["data"]["is_retry"] is False
    assert response["data"]["is_reclaim"] is True


def test_handle_task_complete_marks_completed(daemon_with_state, socket_path):
    """task_complete should mark task as COMPLETED."""
    daemon, worktree = daemon_with_state

    # First claim the task
    claim_response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )
    assert claim_response["status"] == "ok"
    task_id = claim_response["data"]["task"]["id"]

    # Complete the task
    response = send_command(
        socket_path,
        {
            "command": "task_complete",
            "task_id": task_id,
            "worker_id": "worker1",
        },
    )

    assert response["status"] == "ok"

    # Verify state was updated
    from harness.state import StateManager

    manager = StateManager(worktree)
    state = manager.load()
    assert state.tasks[task_id].status.value == "completed"
    assert state.tasks[task_id].completed_at is not None


def test_handle_task_complete_validates_ownership(daemon_with_state, socket_path):
    """task_complete should validate worker owns the task."""
    daemon, worktree = daemon_with_state

    # Claim task with worker1
    claim_response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )
    assert claim_response["status"] == "ok"
    task_id = claim_response["data"]["task"]["id"]

    # Try to complete with different worker
    response = send_command(
        socket_path,
        {
            "command": "task_complete",
            "task_id": task_id,
            "worker_id": "worker2",
        },
    )

    assert response["status"] == "error"
    assert "not claimed by" in response["message"]


def test_task_claim_logs_trajectory_after_state_update(daemon_with_state, socket_path, worktree):
    """task_claim should log to trajectory AFTER state update (lock convoy fix)."""
    daemon, worktree_path = daemon_with_state

    # Claim a task
    response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )

    assert response["status"] == "ok"

    # Verify trajectory was logged
    trajectory_file = worktree_path / ".claude" / "trajectory.jsonl"
    assert trajectory_file.exists()

    # Read the trajectory
    import json

    with open(trajectory_file) as f:
        lines = f.readlines()
        assert len(lines) >= 1
        event = json.loads(lines[-1])
        assert event["event_type"] == "task_claim"
        assert event["task_id"] == "task1"
        assert event["worker_id"] == "worker1"


def test_exec_decodes_signal_on_negative_returncode(daemon_with_state, socket_path):
    """exec should decode negative return codes to signal names."""
    daemon, worktree = daemon_with_state

    # Execute a command that will be killed (use kill -15 on sleep)
    # We'll simulate this by testing the handler's signal decoding logic
    response = send_command(
        socket_path,
        {
            "command": "exec",
            "args": ["sleep", "100"],
            "timeout": 0.1,  # Short timeout will cause SIGTERM
        },
    )

    # The response should include signal information
    assert response["status"] == "ok"
    if response["data"]["returncode"] < 0:
        assert "signal_name" in response["data"]
        # Common signals for timeout: SIGTERM (-15) or SIGKILL (-9)
        assert response["data"]["signal_name"] in ["SIGTERM", "SIGKILL"]


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
    with open(trajectory_file) as f:
        lines = f.readlines()
        # Find the exec event
        exec_events = [json.loads(line) for line in lines if "exec" in line]
        assert len(exec_events) >= 1
        event = exec_events[-1]
        assert event["event_type"] == "exec"
        assert "duration_ms" in event
        assert isinstance(event["duration_ms"], int)
        assert event["duration_ms"] >= 0


def test_daemon_calls_check_capabilities_on_init(socket_path, worktree):
    """HarnessDaemon should call runtime.check_capabilities() on init."""
    from unittest.mock import MagicMock, patch

    from harness.daemon import HarnessDaemon

    with patch("harness.daemon.create_runtime") as mock_create:
        mock_runtime = MagicMock()
        mock_create.return_value = mock_runtime

        daemon = HarnessDaemon(socket_path, str(worktree))

        # check_capabilities should have been called
        mock_runtime.check_capabilities.assert_called_once()

        daemon.server_close()


def test_daemon_fails_fast_on_capability_check_failure(socket_path, worktree):
    """HarnessDaemon should fail immediately if check_capabilities fails."""
    from unittest.mock import MagicMock, patch

    from harness.daemon import HarnessDaemon

    with patch("harness.daemon.create_runtime") as mock_create:
        mock_runtime = MagicMock()
        mock_runtime.check_capabilities.side_effect = RuntimeError("git not found")
        mock_create.return_value = mock_runtime

        with pytest.raises(RuntimeError, match="git not found"):
            HarnessDaemon(socket_path, str(worktree))


def test_exec_trajectory_log_truncation_limit(daemon_with_state, socket_path, worktree):
    """Trajectory log should capture enough output for debugging (4KB, not 200 chars).

    Bug: Truncating to 200 chars leaves agents unable to debug failures.
    The error summary is often at the bottom of long stack traces.
    """
    daemon, worktree_path = daemon_with_state

    # Generate output > 200 chars but < 4096 chars
    # This simulates a test failure with a meaningful stack trace
    char_count = 500
    response = send_command(
        socket_path,
        {
            "command": "exec",
            "args": ["python", "-c", f"print('x' * {char_count})"],
        },
    )

    assert response["status"] == "ok"
    # Response to client should have full output
    assert len(response["data"]["stdout"]) >= char_count

    # Verify trajectory log captures enough context (not truncated to 200)
    import json

    trajectory_file = worktree_path / ".claude" / "trajectory.jsonl"
    with open(trajectory_file) as f:
        lines = f.readlines()
        exec_events = [json.loads(line) for line in lines if '"exec"' in line]
        assert len(exec_events) >= 1
        event = exec_events[-1]
        # This assertion will FAIL with 200-char limit
        # The logged stdout should be > 200 chars (proving fix works)
        assert len(event["stdout"]) > 200, (
            f"Trajectory log truncated to {len(event['stdout'])} chars. "
            f"Agents need 4KB for debugging."
        )


def test_plan_import_handler(daemon_manager):
    """plan_import should parse JSON and seed state."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    content = """
Here is the plan:

```json
{
  "goal": "Test",
  "tasks": {
    "task-1": {"description": "First"},
    "task-2": {"description": "Second", "dependencies": ["task-1"]}
  }
}
```
"""
    resp = send_command(daemon.socket_path, {"command": "plan_import", "content": content})
    assert resp["status"] == "ok"
    assert resp["data"]["task_count"] == 2

    # Verify state
    state = send_command(daemon.socket_path, {"command": "get_state"})
    assert "task-1" in state["data"]["tasks"]


def test_plan_import_preserves_injection(daemon_manager):
    """plan_import should preserve instructions and role."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    content = """
```json
{
  "goal": "Test",
  "tasks": {
    "task-1": {
      "description": "Do it",
      "instructions": "Step by step guide here",
      "role": "backend"
    }
  }
}
```
"""
    send_command(daemon.socket_path, {"command": "plan_import", "content": content})
    state = send_command(daemon.socket_path, {"command": "get_state"})
    task = state["data"]["tasks"]["task-1"]
    assert task["instructions"] == "Step by step guide here"
    assert task["role"] == "backend"


def test_plan_import_rejects_cycle(daemon_manager):
    """plan_import should reject cyclic dependencies."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    content = """
```json
{
  "goal": "Bad",
  "tasks": {
    "a": {"description": "A", "dependencies": ["b"]},
    "b": {"description": "B", "dependencies": ["a"]}
  }
}
```
"""
    resp = send_command(daemon.socket_path, {"command": "plan_import", "content": content})
    assert resp["status"] == "error"
    assert "cycle" in resp["message"].lower()


def test_plan_import_missing_content(daemon_manager):
    """plan_import should error when content is missing."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    resp = send_command(daemon.socket_path, {"command": "plan_import"})
    assert resp["status"] == "error"
    assert "content required" in resp["message"].lower()


def test_plan_import_invalid_json(daemon_manager):
    """plan_import should error for invalid JSON in content."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    resp = send_command(daemon.socket_path, {"command": "plan_import", "content": "no json here"})
    assert resp["status"] == "error"


def test_plan_import_cyclic_deps(daemon_manager):
    """plan_import should reject plans with cyclic dependencies."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    content = """```json
{
    "goal": "Test",
    "tasks": {
        "a": {"description": "A", "dependencies": ["b"]},
        "b": {"description": "B", "dependencies": ["a"]}
    }
}
```"""
    resp = send_command(daemon.socket_path, {"command": "plan_import", "content": content})
    assert resp["status"] == "error"
    assert "cycle" in resp["message"].lower()


def test_daemon_server_close_removes_lock_file(worktree):
    """server_close() should remove socket and lock files."""
    import uuid
    from pathlib import Path

    from harness.daemon import HarnessDaemon

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
    import uuid
    from pathlib import Path

    from harness.daemon import HarnessDaemon

    socket_path = f"/tmp/harness-stale-{uuid.uuid4().hex[:8]}.sock"

    # Create stale socket file
    Path(socket_path).touch()
    assert Path(socket_path).exists()

    # Init should remove stale socket
    daemon = HarnessDaemon(socket_path, str(worktree))

    # Daemon should have started successfully
    assert Path(socket_path).exists()  # Real socket now

    daemon.server_close()


def test_handle_empty_request_line(daemon_manager):
    """Handler should gracefully handle empty request."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(daemon.socket_path)
    sock.sendall(b"\n")  # Empty line
    sock.close()
    # Should not crash daemon - verify with ping
    resp = send_command(daemon.socket_path, {"command": "ping"})
    assert resp["status"] == "ok"


def test_handle_malformed_json_request(daemon_manager):
    """Handler should return error for malformed JSON."""
    daemon, _ = daemon_manager
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(daemon.socket_path)
    sock.sendall(b"not json\n")
    response = sock.recv(4096)
    sock.close()
    data = json.loads(response.decode().strip())
    assert data["status"] == "error"


def test_handle_missing_command(daemon_manager):
    """Handler should return error when command field missing."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    resp = send_command(daemon.socket_path, {"not_command": "value"})
    assert resp["status"] == "error"
    assert "Missing command" in resp["message"]


def test_handle_unknown_command(daemon_manager):
    """Handler should return error for unknown command."""
    daemon, _ = daemon_manager
    from tests.harness.conftest import send_command

    resp = send_command(daemon.socket_path, {"command": "unknown_cmd"})
    assert resp["status"] == "error"
    assert "Unknown command" in resp["message"]


def test_daemon_sigterm_triggers_shutdown(tmp_path):
    """SIGTERM should trigger graceful daemon shutdown."""
    import signal
    import subprocess
    import sys
    import time
    import uuid
    from pathlib import Path

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True
    )
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
    import signal
    import subprocess
    import sys
    import time
    import uuid
    from pathlib import Path

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True
    )
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)

    socket_path = f"/tmp/harness-sigint-{uuid.uuid4().hex[:8]}.sock"

    proc = subprocess.Popen(
        [sys.executable, "-m", "harness.daemon", socket_path, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    for _ in range(50):
        if Path(socket_path).exists():
            break
        time.sleep(0.1)

    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=5)

    assert proc.returncode == 0
    assert not Path(socket_path).exists()
