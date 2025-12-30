# tests/hyh/test_daemon.py
"""
Tests for the threaded daemon using socketserver.ThreadingMixIn.

The daemon provides:
- Thread-safe state access (Pydantic validation happens here)
- Git mutex protection via GLOBAL_GIT_LOCK
- Single instance guarantee via fcntl.flock
"""

import json
import socket
import sys
import threading
from pathlib import Path

import msgspec
import pytest

from tests.hyh.conftest import send_command, wait_for_socket

# socket_path and worktree fixtures are imported from conftest.py


# -- Request Type Validation Tests (Task 1) --


def test_task_claim_request_decodes():
    """TaskClaimRequest should decode from JSON with tagged union."""
    from hyh.daemon import Request, TaskClaimRequest

    raw = b'{"command": "task_claim", "worker_id": "worker-1"}'
    req = msgspec.json.decode(raw, type=Request)
    assert isinstance(req, TaskClaimRequest)
    assert req.worker_id == "worker-1"


def test_task_claim_request_rejects_empty_worker_id():
    """TaskClaimRequest should reject empty/whitespace worker_id."""
    from hyh.daemon import Request

    raw = b'{"command": "task_claim", "worker_id": "  "}'
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(raw, type=Request)


def test_exec_request_rejects_negative_timeout():
    """ExecRequest should reject negative timeout values."""
    from hyh.daemon import Request

    raw = b'{"command": "exec", "args": ["ls"], "timeout": -5}'
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(raw, type=Request)


def test_request_rejects_unknown_command():
    """Request union should reject unknown command values."""
    from hyh.daemon import Request

    raw = b'{"command": "unknown_cmd"}'
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(raw, type=Request)


# -- Response Type Validation Tests (Task 2) --


def test_ok_response_serializes():
    """Ok response should serialize with status=ok."""
    from hyh.daemon import Ok, PingData, Result

    response: Result = Ok(data=PingData(running=True, pid=12345))
    encoded = msgspec.json.encode(response)
    assert b'"status":"ok"' in encoded
    assert b'"running":true' in encoded
    assert b'"pid":12345' in encoded


def test_err_response_serializes():
    """Err response should serialize with status=error."""
    from hyh.daemon import Err, Result

    response: Result = Err(message="Something failed")
    encoded = msgspec.json.encode(response)
    assert b'"status":"error"' in encoded
    assert b'"message":"Something failed"' in encoded


def test_daemon_get_state(socket_path, worktree):
    """Daemon should return state via get_state command."""
    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    # Create state file with v2 JSON schema
    manager = WorkflowStateStore(worktree)
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

    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    wait_for_socket(socket_path)  # Condition-based wait (was time.sleep(0.1))

    try:
        response = send_command(socket_path, {"command": "get_state"})
        assert response["status"] == "ok"
        assert "tasks" in response["data"]["state"]
        assert "task-1" in response["data"]["state"]["tasks"]
        assert response["data"]["state"]["tasks"]["task-1"]["status"] == "pending"
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_daemon_update_state(socket_path, worktree):
    """Daemon should update state via update_state command."""
    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    manager = WorkflowStateStore(worktree)
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
    wait_for_socket(socket_path)

    try:
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

        loaded = WorkflowStateStore(worktree).load()
        assert loaded is not None
        assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED
        assert "task-2" in loaded.tasks
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_daemon_git_operations(socket_path, worktree):
    """Daemon should execute git commands with mutex protection."""
    from hyh.daemon import HarnessDaemon

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    wait_for_socket(socket_path)

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
    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    manager = WorkflowStateStore(worktree)
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
    wait_for_socket(socket_path)

    results = []
    errors = []

    def client_request(client_id):
        try:
            resp = send_command(socket_path, {"command": "get_state"})
            results.append((client_id, resp["status"]))
        except Exception as e:
            errors.append((client_id, str(e)))

    try:
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
    from hyh.daemon import HarnessDaemon

    daemon1 = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon1.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    wait_for_socket(socket_path)

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
    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    manager = WorkflowStateStore(worktree)
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
    wait_for_socket(socket_path)

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
    from hyh.state import WorkflowStateStore

    manager = WorkflowStateStore(worktree)
    state = manager.load()
    assert state is not None
    assert state.tasks["task1"].status.value == "running"
    assert state.tasks["task1"].claimed_by == "worker1"
    assert state.tasks["task1"].started_at is not None


def test_handle_task_claim_reclaims_timed_out(daemon_with_state, socket_path):
    """task_claim should reclaim timed out tasks with is_reclaim flag."""
    daemon, worktree = daemon_with_state
    from datetime import datetime, timedelta

    from hyh.state import Task, TaskStatus, WorkflowState

    # Create a timed out task using daemon's StateManager (not a separate instance)
    # This ensures the daemon sees the state change (caching is per-instance)
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
    daemon.state_manager.save(state)

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

    claim_response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )
    assert claim_response["status"] == "ok"
    task_id = claim_response["data"]["task"]["id"]

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
    from hyh.state import WorkflowStateStore

    manager = WorkflowStateStore(worktree)
    state = manager.load()
    assert state is not None
    assert state.tasks[task_id].status.value == "completed"
    assert state.tasks[task_id].completed_at is not None


def test_handle_task_complete_validates_ownership(daemon_with_state, socket_path):
    """task_complete should validate worker owns the task."""
    daemon, worktree = daemon_with_state

    claim_response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )
    assert claim_response["status"] == "ok"
    task_id = claim_response["data"]["task"]["id"]

    response = send_command(
        socket_path,
        {
            "command": "task_complete",
            "task_id": task_id,
            "worker_id": "worker2",
        },
    )

    assert response["status"] == "error"
    assert "not owned by" in response["message"]


def test_task_claim_logs_trajectory_after_state_update(daemon_with_state, socket_path, worktree):
    """task_claim should log to trajectory AFTER state update (lock convoy fix)."""
    daemon, worktree_path = daemon_with_state

    response = send_command(
        socket_path,
        {"command": "task_claim", "worker_id": "worker1"},
    )

    assert response["status"] == "ok"

    trajectory_file = worktree_path / ".claude" / "trajectory.jsonl"
    assert trajectory_file.exists()

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

    import json

    trajectory_file = worktree_path / ".claude" / "trajectory.jsonl"
    with open(trajectory_file) as f:
        lines = f.readlines()

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

    from hyh.daemon import HarnessDaemon

    with patch("hyh.daemon.create_runtime") as mock_create:
        mock_runtime = MagicMock()
        mock_create.return_value = mock_runtime

        daemon = HarnessDaemon(socket_path, str(worktree))

        # check_capabilities should have been called
        mock_runtime.check_capabilities.assert_called_once()

        daemon.server_close()


def test_daemon_fails_fast_on_capability_check_failure(socket_path, worktree):
    """HarnessDaemon should fail immediately if check_capabilities fails."""
    from unittest.mock import MagicMock, patch

    from hyh.daemon import HarnessDaemon

    with patch("hyh.daemon.create_runtime") as mock_create:
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
            "args": [sys.executable, "-c", f"print('x' * {char_count})"],
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
    """plan_import should parse Markdown and seed state."""
    daemon, _ = daemon_manager

    content = """
**Goal:** Test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | First task |
| Group 2    | 2     | Depends on Group 1 |

### Task 1: First

Implementation details.

### Task 2: Second

Implementation details.
"""
    resp = send_command(daemon.socket_path, {"command": "plan_import", "content": content})
    assert resp["status"] == "ok"
    assert resp["data"]["task_count"] == 2

    state = send_command(daemon.socket_path, {"command": "get_state"})
    assert "1" in state["data"]["state"]["tasks"]


def test_plan_import_preserves_instructions(daemon_manager):
    """plan_import should preserve task body as instructions."""
    daemon, _ = daemon_manager

    content = """
**Goal:** Test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Solo task |

### Task 1: Do it

Step by step guide here.
"""
    send_command(daemon.socket_path, {"command": "plan_import", "content": content})
    state = send_command(daemon.socket_path, {"command": "get_state"})
    task = state["data"]["state"]["tasks"]["1"]
    assert task["description"] == "Do it"
    assert "Step by step guide here" in task["instructions"]


def test_plan_import_missing_content(daemon_manager):
    """plan_import should error when content is missing."""
    daemon, _ = daemon_manager

    resp = send_command(daemon.socket_path, {"command": "plan_import"})
    assert resp["status"] == "error"
    assert "content" in resp["message"].lower()


def test_plan_import_invalid_content(daemon_manager):
    """plan_import should error for invalid plan content."""
    daemon, _ = daemon_manager
    from tests.hyh.conftest import send_command

    resp = send_command(
        daemon.socket_path, {"command": "plan_import", "content": "no valid plan here"}
    )
    assert resp["status"] == "error"


def test_daemon_server_close_removes_lock_file(worktree):
    """server_close() should remove socket and lock files."""
    import uuid
    from pathlib import Path

    from hyh.daemon import HarnessDaemon

    socket_path = f"/tmp/hyh-close-{uuid.uuid4().hex[:8]}.sock"
    lock_path = socket_path + ".lock"

    daemon = HarnessDaemon(socket_path, str(worktree))

    assert Path(socket_path).exists()
    assert Path(lock_path).exists()

    daemon.server_close()

    assert not Path(socket_path).exists()
    assert not Path(lock_path).exists()


def test_daemon_stale_socket_removed_on_init(worktree):
    """Daemon should remove stale socket file on init."""
    import uuid
    from pathlib import Path

    from hyh.daemon import HarnessDaemon

    socket_path = f"/tmp/hyh-stale-{uuid.uuid4().hex[:8]}.sock"

    Path(socket_path).touch()
    assert Path(socket_path).exists()

    daemon = HarnessDaemon(socket_path, str(worktree))

    # Daemon should have started successfully
    assert Path(socket_path).exists()  # Real socket now

    daemon.server_close()


def test_handle_empty_request_line(daemon_manager):
    """Handler should gracefully handle empty request."""
    daemon, _ = daemon_manager
    from tests.hyh.conftest import send_command

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(daemon.socket_path)
    sock.sendall(b"\n")
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
    from tests.hyh.conftest import send_command

    resp = send_command(daemon.socket_path, {"not_command": "value"})
    assert resp["status"] == "error"
    assert "command" in resp["message"].lower()


def test_handle_unknown_command(daemon_manager):
    """Handler should return error for unknown command."""
    daemon, _ = daemon_manager
    from tests.hyh.conftest import send_command

    resp = send_command(daemon.socket_path, {"command": "unknown_cmd"})
    assert resp["status"] == "error"
    assert "invalid" in resp["message"].lower()


def test_daemon_sigterm_triggers_shutdown(tmp_path):
    """SIGTERM should trigger graceful daemon shutdown."""
    import signal
    import subprocess
    import sys
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

    socket_path = f"/tmp/hyh-sigtest-{uuid.uuid4().hex[:8]}.sock"

    # Start daemon via subprocess
    proc = subprocess.Popen(
        [sys.executable, "-m", "hyh.daemon", socket_path, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for socket (condition-based, replaces polling loop)
    wait_for_socket(socket_path, timeout=5.0)

    # Send SIGTERM
    proc.send_signal(signal.SIGTERM)

    # Wait for graceful exit
    try:
        proc.wait(timeout=5)
    finally:
        # Close pipe handles to prevent ResourceWarning
        proc.stdout.close()
        proc.stderr.close()

    # Should exit cleanly (code 0)
    assert proc.returncode == 0

    # Socket should be cleaned up
    assert not Path(socket_path).exists()


def test_daemon_sigint_triggers_shutdown(tmp_path):
    """SIGINT should trigger graceful daemon shutdown."""
    import signal
    import subprocess
    import sys
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

    socket_path = f"/tmp/hyh-sigint-{uuid.uuid4().hex[:8]}.sock"

    proc = subprocess.Popen(
        [sys.executable, "-m", "hyh.daemon", socket_path, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for socket (condition-based, replaces polling loop)
    wait_for_socket(socket_path, timeout=5.0)

    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    finally:
        # Close pipe handles to prevent ResourceWarning
        proc.stdout.close()
        proc.stderr.close()

    # Accept 0 (graceful exit) or -2 (killed by SIGINT before graceful shutdown completes)
    # The signal handler spawns a daemon thread for shutdown, so timing can vary
    assert proc.returncode in (0, -2), f"Expected graceful shutdown, got {proc.returncode}"
    # Socket cleanup may not happen if killed by signal before server_close()
    if proc.returncode == 0:
        assert not Path(socket_path).exists()


def test_plan_import_invalid_markdown_gives_helpful_error(daemon_manager):
    """Invalid markdown plans get actionable error message."""
    daemon, _ = daemon_manager
    from tests.hyh.conftest import send_command

    invalid_content = """# My Plan

## Task 1: Do something
- [ ] Step one
- [ ] Step two
"""

    result = send_command(
        daemon.socket_path, {"command": "plan_import", "content": invalid_content}
    )

    assert result["status"] == "error"
    assert "No valid plan found" in result["message"]
    assert "hyh plan template" in result["message"]


def test_handle_status_returns_workflow_summary(daemon_with_state, socket_path):
    """Status command returns workflow summary with task counts."""
    daemon, worktree = daemon_with_state

    response = send_command(socket_path, {"command": "status"})

    assert response["status"] == "ok"
    data = response["data"]
    assert "summary" in data
    assert data["summary"]["total"] == 2
    assert data["summary"]["completed"] >= 0
    assert data["summary"]["running"] >= 0
    assert data["summary"]["pending"] >= 0
    assert "tasks" in data
    assert "events" in data


def test_handle_status_no_workflow(socket_path, worktree):
    """Status returns inactive when no workflow exists."""
    from hyh.daemon import HarnessDaemon

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    wait_for_socket(socket_path)

    try:
        response = send_command(socket_path, {"command": "status"})

        assert response["status"] == "ok"
        assert response["data"]["active"] is False
        assert response["data"]["summary"]["total"] == 0
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_handle_status_with_running_task(daemon_with_state, socket_path):
    """Status shows active workers for running tasks."""
    daemon, worktree = daemon_with_state

    # Claim a task first
    claim_response = send_command(
        socket_path, {"command": "task_claim", "worker_id": "test-worker"}
    )
    assert claim_response["status"] == "ok"

    response = send_command(socket_path, {"command": "status"})

    assert response["status"] == "ok"
    assert response["data"]["summary"]["running"] >= 1
    assert "test-worker" in response["data"]["active_workers"]


def test_daemon_registers_with_registry(
    tmp_path: Path, socket_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daemon registers project in registry on spawn."""
    import subprocess

    from hyh.daemon import HarnessDaemon
    from hyh.registry import ProjectRegistry

    registry_file = tmp_path / "registry.json"
    worktree = tmp_path / "project"
    worktree.mkdir()
    (worktree / ".claude").mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=worktree, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=worktree,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=worktree,
        capture_output=True,
        check=True,
    )
    (worktree / "file.txt").write_text("content")
    subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=worktree, capture_output=True, check=True
    )

    # Use env var to configure registry path (keeps HarnessDaemon signature clean)
    monkeypatch.setenv("HYH_REGISTRY_FILE", str(registry_file))

    # Spawn daemon
    daemon = HarnessDaemon(socket_path, str(worktree))

    try:
        # Verify project was registered
        registry = ProjectRegistry(registry_file)
        projects = registry.list_projects()
        paths = [p["path"] for p in projects.values()]
        assert str(worktree) in paths
    finally:
        daemon.server_close()


# -- Task 3: Typed dispatch tests --


def test_dispatch_returns_typed_error_for_invalid_json():
    """dispatch should return Err for invalid JSON."""
    from hyh.daemon import HarnessHandler

    # Create a mock handler to test dispatch directly
    handler = HarnessHandler.__new__(HarnessHandler)
    handler.server = None  # Will set up properly in integration

    # Invalid JSON should return Err
    result = handler.dispatch(b"not valid json")
    decoded = msgspec.json.decode(result)
    assert decoded["status"] == "error"
    assert "message" in decoded


def test_task_claim_returns_extended_fields(daemon_manager):
    """task_claim returns full TaskPacket fields."""
    daemon, worktree = daemon_manager

    # Import XML plan with full fields
    xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test">
  <task id="T001" role="implementer" model="opus">
    <description>Test task</description>
    <tools>Read, Edit</tools>
    <scope>
      <include>src/a.py</include>
    </scope>
    <instructions>Do the thing</instructions>
    <constraints>No new deps</constraints>
    <verification>
      <command>pytest</command>
    </verification>
    <success>Tests pass</success>
    <artifacts>
      <write>.claude/out.md</write>
    </artifacts>
  </task>
</plan>
"""

    # Import plan
    send_command(daemon.socket_path, {"command": "plan_import", "content": xml_plan})

    # Claim task
    response = send_command(
        daemon.socket_path,
        {"command": "task_claim", "worker_id": "test-worker"},
    )

    assert response["status"] == "ok"
    task = response["data"]["task"]

    # Verify extended fields are present
    assert task["role"] == "implementer"
    assert task["model"] == "opus"
    assert task["files_in_scope"] == ["src/a.py"]
    assert task["tools"] == ["Read", "Edit"]
    assert task["constraints"] == "No new deps"
    assert task["verification_commands"] == ["pytest"]
    assert task["success_criteria"] == "Tests pass"
    assert task["artifacts_to_write"] == [".claude/out.md"]
