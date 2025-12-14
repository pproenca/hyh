# tests/harness/test_integration_council.py
"""Integration tests verifying Council Amendments A, B, C work together."""

import json
import os
import socket
import subprocess
import threading
import time
import uuid

import pytest


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
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
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
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

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
        with open(trajectory_file) as f:
            lines = f.readlines()
            exec_events = [json.loads(line) for line in lines if "exec" in line]
            assert any("duration_ms" in e for e in exec_events)
    finally:
        daemon.shutdown()
        server_thread.join(timeout=1)


def test_cyclic_dag_rejected_at_boundary(tmp_path):
    """Amendment C: Cyclic dependencies must be rejected."""
    from harness.state import StateManager, Task, TaskStatus, WorkflowState

    manager = StateManager(tmp_path)
    cyclic_state = WorkflowState(
        tasks={
            "a": Task(id="a", description="A", status=TaskStatus.PENDING, dependencies=["b"]),
            "b": Task(id="b", description="B", status=TaskStatus.PENDING, dependencies=["a"]),
        }
    )

    with pytest.raises(ValueError, match="[Cc]ycle"):
        manager.save(cyclic_state)
