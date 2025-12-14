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
