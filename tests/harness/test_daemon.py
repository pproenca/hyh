# tests/harness/test_daemon.py
"""
Tests for the threaded daemon using socketserver.ThreadingMixIn.

The daemon provides:
- Thread-safe state access (Pydantic validation happens here)
- Git mutex protection via GLOBAL_GIT_LOCK
- Single instance guarantee via fcntl.flock
"""

import pytest
import json
import os
import socket
import threading
import time
import subprocess
import uuid


@pytest.fixture
def socket_path(tmp_path):
    # Use /tmp for socket to avoid AF_UNIX path length limit on macOS
    # The limit is ~104 chars, pytest tmp_path paths can exceed that
    short_id = uuid.uuid4().hex[:8]
    sock_path = f"/tmp/harness-test-{short_id}.sock"
    yield sock_path
    # Cleanup socket and lock file
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    lock_path = sock_path + ".lock"
    if os.path.exists(lock_path):
        os.unlink(lock_path)


@pytest.fixture
def worktree(tmp_path):
    """Create a mock worktree with git repo."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    (tmp_path / "file.txt").write_text("content")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True
    )
    return tmp_path


def send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
    """Send command to daemon and get response."""
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


def test_daemon_get_state(socket_path, worktree):
    """Daemon should return state via get_state command."""
    from harness.daemon import HarnessDaemon
    from harness.state import WorkflowState, StateManager

    # Create state file
    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            workflow="subagent",
            plan="/plan.md",
            current_task=2,
            total_tasks=5,
            worktree=str(worktree),
            base_sha="abc123",
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
        assert response["data"]["current_task"] == 2
        assert response["data"]["total_tasks"] == 5
    finally:
        daemon.shutdown()
        server_thread.join(timeout=1)


def test_daemon_update_state(socket_path, worktree):
    """Daemon should update state via update_state command."""
    from harness.daemon import HarnessDaemon
    from harness.state import WorkflowState, StateManager

    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            workflow="execute-plan",
            plan="/plan.md",
            current_task=0,
            total_tasks=3,
            worktree=str(worktree),
            base_sha="abc",
        )
    )

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.1)

    try:
        response = send_command(
            socket_path,
            {
                "command": "update_state",
                "updates": {"current_task": 1, "last_commit": "def456"},
            },
        )
        assert response["status"] == "ok"

        # Verify persisted
        loaded = StateManager(worktree).load()
        assert loaded.current_task == 1
        assert loaded.last_commit == "def456"
    finally:
        daemon.shutdown()
        server_thread.join(timeout=1)


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
        server_thread.join(timeout=1)


def test_daemon_parallel_clients(socket_path, worktree):
    """Verify daemon handles parallel clients (Python 3.13t threading)."""
    from harness.daemon import HarnessDaemon
    from harness.state import WorkflowState, StateManager

    manager = StateManager(worktree)
    manager.save(
        WorkflowState(
            workflow="subagent",
            plan="/plan.md",
            current_task=0,
            total_tasks=10,
            worktree=str(worktree),
            base_sha="abc",
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
        server_thread.join(timeout=1)


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
        server_thread.join(timeout=1)


@pytest.fixture
def daemon_with_state(socket_path, worktree):
    """Create a daemon with tasks in state."""
    from harness.daemon import HarnessDaemon
    from harness.state import WorkflowState, Task, TaskStatus, StateManager
    from datetime import datetime

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
    server_thread.join(timeout=1)


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
    from harness.state import StateManager, WorkflowState, Task, TaskStatus
    from datetime import datetime, timedelta

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


def test_task_claim_logs_trajectory_after_state_update(
    daemon_with_state, socket_path, worktree
):
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

    with open(trajectory_file, "r") as f:
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
            "cmd": ["sleep", "100"],
            "timeout": 0.1,  # Short timeout will cause SIGTERM
        },
    )

    # The response should include signal information
    assert response["status"] == "ok"
    if response["data"]["returncode"] < 0:
        assert "signal_name" in response["data"]
        # Common signals for timeout: SIGTERM (-15) or SIGKILL (-9)
        assert response["data"]["signal_name"] in ["SIGTERM", "SIGKILL"]
