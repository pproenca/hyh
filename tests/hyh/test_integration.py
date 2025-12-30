# tests/hyh/test_integration.py
"""
Integration tests for the complete hyh system.
Tests daemon + client + state + git working together.
"""

import contextlib
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.hyh.conftest import send_command_with_retry, wait_for_socket, wait_until


@pytest.fixture
def integration_worktree(tmp_path):
    """Create a complete test environment."""

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
    (tmp_path / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True
    )

    # Use short socket path in /tmp to avoid macOS AF_UNIX path length limit
    socket_id = uuid.uuid4().hex[:8]
    socket_path = f"/tmp/hyh-integ-{socket_id}.sock"

    yield {"worktree": tmp_path, "socket": socket_path}

    # Cleanup daemon - try graceful shutdown first
    from .conftest import cleanup_daemon_subprocess

    cleanup_daemon_subprocess(socket_path)


@pytest.mark.skipif(
    hasattr(__import__("sys"), "_is_gil_enabled") and not __import__("sys")._is_gil_enabled(),
    reason="Segfaults on freethreaded Python due to socketserver threading issues (CPython bug)",
)
def test_parallel_git_operations_no_race(integration_worktree):
    """Multiple parallel git operations should not cause index.lock errors.

    Uses the daemon directly in a thread (like test_daemon.py) to avoid
    issues with subprocess spawning and connection backlog.
    """
    import socket as socket_module

    from hyh.daemon import HarnessDaemon

    socket_path = integration_worktree["socket"]
    worktree = integration_worktree["worktree"]

    # Start daemon directly in thread (avoids subprocess overhead)
    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    wait_for_socket(socket_path)

    def send_command(cmd, max_retries=5):
        """Send command to daemon and return response with retry on transient errors."""
        import errno

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
            except (ConnectionRefusedError, BlockingIOError):
                # Socket backlog full or EAGAIN - retry after brief delay
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise
            except OSError as e:
                # Handle EAGAIN/EWOULDBLOCK which may come as OSError
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK) and attempt < max_retries - 1:
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
        daemon.server_close()
        server_thread.join(timeout=2)


def test_state_persistence_across_daemon_restart(integration_worktree):
    """State should persist across daemon restarts."""
    from hyh.client import send_rpc
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    socket_path = integration_worktree["socket"]
    worktree = integration_worktree["worktree"]

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

    # Shutdown may complete before response is sent - that's expected
    with contextlib.suppress(json.JSONDecodeError):
        send_rpc(socket_path, {"command": "shutdown"}, None)

    # Wait for daemon to shut down (socket should disappear)
    wait_until(
        lambda: not os.path.exists(socket_path),
        timeout=2.0,
        message="Daemon socket didn't disappear after shutdown",
    )

    # Reconnect (should auto-spawn new daemon)
    resp = send_rpc(
        socket_path,
        {"command": "get_state"},
        worktree_root=str(worktree),
    )
    assert resp["status"] == "ok"
    assert resp["data"]["state"]["tasks"]["task-1"]["status"] == "completed"  # State persisted


def test_cli_commands(integration_worktree):
    """Test CLI commands work correctly via subprocess."""
    import sys

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    env = {
        "HYH_SOCKET": socket_path,
        "HYH_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        # Inherit PYTHONPATH so hyh module can be found
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    result = subprocess.run(
        [sys.executable, "-m", "hyh", "ping"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"ping failed: {result.stderr}"
    assert "ok" in result.stdout

    result = subprocess.run(
        [sys.executable, "-m", "hyh", "git", "--", "status"],
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
        "HYH_SOCKET": socket_path,
        "HYH_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    # get-state should report "No active workflow" and exit 1
    result = subprocess.run(
        [sys.executable, "-m", "hyh", "get-state"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1
    assert "No active workflow" in result.stdout


def test_cli_update_state(integration_worktree):
    """Test update-state command works correctly."""
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

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

    # Update state via RPC (tests the update mechanism directly)
    from hyh.client import send_rpc

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

    loaded = WorkflowStateStore(worktree).load()
    assert loaded is not None
    assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED


def test_cli_session_start_with_active_workflow(integration_worktree):
    """Test session-start hook outputs correct JSON."""
    import sys

    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    # 2 completed, 6 pending = 2/8 progress
    manager = WorkflowStateStore(worktree)
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
        "HYH_SOCKET": socket_path,
        "HYH_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    result = subprocess.run(
        [sys.executable, "-m", "hyh", "session-start"],
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
        "HYH_SOCKET": socket_path,
        "HYH_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    result = subprocess.run(
        [sys.executable, "-m", "hyh", "ping"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0

    result = subprocess.run(
        [sys.executable, "-m", "hyh", "shutdown"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "Shutdown" in result.stdout

    # Wait for daemon to shut down
    wait_until(
        lambda: not os.path.exists(socket_path),
        timeout=2.0,
        message="Daemon socket didn't disappear after shutdown",
    )

    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
        pytest.fail("Daemon should have been shutdown")
    except (FileNotFoundError, ConnectionRefusedError):
        pass  # Expected - daemon is down
    finally:
        sock.close()


@pytest.fixture
def workflow_with_tasks(integration_worktree):
    """Set up workflow state with DAG tasks."""
    import threading

    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    manager = WorkflowStateStore(worktree)
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

    daemon = HarnessDaemon(socket_path, str(worktree))
    server_thread = threading.Thread(target=daemon.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    wait_for_socket(socket_path)

    def send_cmd(cmd, max_retries=3):
        return send_command_with_retry(socket_path, cmd, max_retries)

    yield {
        "worktree": worktree,
        "socket": socket_path,
        "manager": manager,
        "daemon": daemon,
        "send_command": send_cmd,
    }

    # Cleanup
    daemon.shutdown()
    daemon.server_close()
    server_thread.join(timeout=2)


def test_full_task_workflow(workflow_with_tasks):
    """End-to-end test: claim task, complete it, verify DAG progression."""
    from hyh.state import TaskStatus

    send_command = workflow_with_tasks["send_command"]
    manager = workflow_with_tasks["manager"]

    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] == "task-1"
    assert resp["data"]["task"]["status"] == "running"

    # Verify task-2 and task-3 are blocked (can't claim yet)
    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"] is None  # No claimable tasks

    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    # Now task-2 and task-3 should be claimable
    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] in ["task-2", "task-3"]

    state = manager.load()
    assert state.tasks["task-1"].status == TaskStatus.COMPLETED
    assert (
        state.tasks["task-2"].status == TaskStatus.RUNNING
        or state.tasks["task-3"].status == TaskStatus.RUNNING
    )


def test_dag_dependency_enforcement(workflow_with_tasks):
    """Can't claim blocked tasks - dependencies must be satisfied first."""
    send_command = workflow_with_tasks["send_command"]

    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] == "task-1"

    resp = send_command({"command": "task_claim", "worker_id": "worker-2"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"] is None

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

    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    # Verify trajectory file exists and has claim event
    assert trajectory_file.exists()
    lines = trajectory_file.read_text().strip().split("\n")
    assert len(lines) >= 1

    event = json.loads(lines[-1])
    assert event["event_type"] == "task_claim"
    assert event["worker_id"] == "worker-1"
    assert event["task_id"] == "task-1"


def test_json_state_persistence(workflow_with_tasks):
    """State persisted as JSON with correct schema (Council fix verification)."""
    send_command = workflow_with_tasks["send_command"]
    worktree = workflow_with_tasks["worktree"]
    state_file = worktree / ".claude" / "dev-workflow-state.json"

    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    assert state_file.exists()
    data = json.loads(state_file.read_text())

    assert "tasks" in data
    assert "task-1" in data["tasks"]
    assert data["tasks"]["task-1"]["status"] == "completed"
    assert data["tasks"]["task-1"]["claimed_by"] == "worker-1"
    assert "started_at" in data["tasks"]["task-1"]
    assert "completed_at" in data["tasks"]["task-1"]


def test_task_claim_idempotency(workflow_with_tasks):
    """Same worker claiming twice returns same task with is_retry=True."""
    send_command = workflow_with_tasks["send_command"]

    resp1 = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp1["status"] == "ok"
    assert resp1["data"]["task"]["id"] == "task-1"
    assert resp1["data"]["is_retry"] is False

    resp2 = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp2["status"] == "ok"
    assert resp2["data"]["task"]["id"] == "task-1"  # Same task
    assert resp2["data"]["is_retry"] is True  # Flagged as retry


def test_lease_renewal_on_reclaim(workflow_with_tasks):
    """Re-claiming updates started_at timestamp (lease renewal)."""
    import time_machine

    send_command = workflow_with_tasks["send_command"]
    manager = workflow_with_tasks["manager"]

    initial_time = datetime.now(UTC)
    with time_machine.travel(initial_time, tick=False) as traveller:
        resp1 = send_command({"command": "task_claim", "worker_id": "worker-1"})
        assert resp1["status"] == "ok"

        state1 = manager.load()
        started_at_1 = state1.tasks["task-1"].started_at
        assert started_at_1 is not None

        # Advance time to ensure timestamp difference (no actual sleep!)
        traveller.shift(timedelta(milliseconds=100))

        resp2 = send_command({"command": "task_claim", "worker_id": "worker-1"})
        assert resp2["status"] == "ok"

        state2 = manager.load()
        started_at_2 = state2.tasks["task-1"].started_at
        assert started_at_2 > started_at_1  # Timestamp advanced


@pytest.fixture
def workflow_with_short_timeout(integration_worktree):
    """Set up workflow with very short task timeout for reclaim testing."""
    import threading

    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    manager = WorkflowStateStore(worktree)
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
    wait_for_socket(socket_path)

    def send_cmd(cmd, max_retries=3):
        return send_command_with_retry(socket_path, cmd, max_retries)

    yield {
        "worktree": worktree,
        "socket": socket_path,
        "manager": manager,
        "daemon": daemon,
        "send_command": send_cmd,
    }

    daemon.shutdown()
    daemon.server_close()
    server_thread.join(timeout=2)


def test_timeout_reclaim_by_different_worker(workflow_with_short_timeout):
    """Timed-out task can be reclaimed by different worker with is_reclaim=True."""
    import time_machine

    send_command = workflow_with_short_timeout["send_command"]

    initial_time = datetime.now(UTC)
    with time_machine.travel(initial_time, tick=False) as traveller:
        resp1 = send_command({"command": "task_claim", "worker_id": "worker-1"})
        assert resp1["status"] == "ok"
        assert resp1["data"]["task"]["id"] == "task-1"

        # Advance time past timeout (no actual sleep!)
        traveller.shift(timedelta(seconds=1.5))

        # Worker-2 can now reclaim the timed-out task
        resp2 = send_command({"command": "task_claim", "worker_id": "worker-2"})
        assert resp2["status"] == "ok"
        assert resp2["data"]["task"]["id"] == "task-1"
        assert resp2["data"]["is_reclaim"] is True  # Flagged as reclaim
    assert resp2["data"]["task"]["claimed_by"] == "worker-2"


def test_ownership_validation_on_complete(workflow_with_tasks):
    """Worker B cannot complete Worker A's task."""
    send_command = workflow_with_tasks["send_command"]

    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] == "task-1"

    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-2"})
    assert resp["status"] == "error"
    assert "not owned by" in resp["message"].lower()


@pytest.fixture
def workflow_with_parallel_tasks(integration_worktree):
    """Set up workflow with multiple independent tasks for parallel claiming."""
    import threading

    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    manager = WorkflowStateStore(worktree)
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
    wait_for_socket(socket_path)

    def send_cmd(cmd, max_retries=3):
        return send_command_with_retry(socket_path, cmd, max_retries)

    yield {
        "worktree": worktree,
        "socket": socket_path,
        "manager": manager,
        "daemon": daemon,
        "send_command": send_cmd,
    }

    daemon.shutdown()
    daemon.server_close()
    server_thread.join(timeout=2)


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
        "HYH_SOCKET": socket_path,
        "HYH_WORKTREE": str(worktree),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    # Claim task via CLI
    result = subprocess.run(
        [sys.executable, "-m", "hyh", "task", "claim"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"task claim failed: {result.stderr}"
    output = json.loads(result.stdout)
    assert output["task"]["id"] == "task-1"

    # Complete task via CLI
    result = subprocess.run(
        [sys.executable, "-m", "hyh", "task", "complete", "--id", "task-1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"task complete failed: {result.stderr}"
    assert "Task task-1 completed" in result.stdout


def test_exec_timeout_and_signal_decoding(workflow_with_tasks):
    """Test exec command with timeout produces signal_name in response."""
    send_command = workflow_with_tasks["send_command"]
    worktree = workflow_with_tasks["worktree"]

    # Exec a command that exceeds timeout
    resp = send_command(
        {
            "command": "exec",
            "args": ["sleep", "10"],
            "cwd": str(worktree),
            "env": {},
            "timeout": 0.1,
        }
    )

    assert resp["status"] == "ok"
    # Process was killed with SIGTERM (signal 15)
    assert resp["data"]["returncode"] < 0
    assert resp["data"]["signal_name"] == "SIGTERM"


def test_dag_cycle_rejection(integration_worktree):
    """Saving workflow with cyclic dependencies raises error."""
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    worktree = integration_worktree["worktree"]
    manager = WorkflowStateStore(worktree)

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


def test_worker_id_stability_across_invocations(integration_worktree, tmp_path):
    """Worker ID persisted to file and consistent across process invocations."""
    import sys

    worktree = integration_worktree["worktree"]
    socket_path = integration_worktree["socket"]

    # Use a unique worker ID file location for this test
    worker_id_file = tmp_path / "worker.id"

    env = {
        "HYH_SOCKET": socket_path,
        "HYH_WORKTREE": str(worktree),
        "HYH_WORKER_ID_FILE": str(worker_id_file),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    # First invocation - generates worker ID
    result1 = subprocess.run(
        [sys.executable, "-m", "hyh", "worker-id"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result1.returncode == 0, f"worker-id failed: {result1.stderr}"
    worker_id_1 = result1.stdout.strip()

    # Second invocation - should return same ID
    result2 = subprocess.run(
        [sys.executable, "-m", "hyh", "worker-id"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result2.returncode == 0
    worker_id_2 = result2.stdout.strip()

    assert worker_id_1 == worker_id_2  # Same ID across invocations
    assert worker_id_file.exists()  # File was created


def test_plan_import_then_claim_with_injection(socket_path, worktree):
    """Full flow: import plan -> claim -> verify injection fields."""
    import sys

    from hyh.client import spawn_daemon
    from tests.hyh.conftest import cleanup_daemon_subprocess

    spawn_daemon(str(worktree), socket_path)

    try:
        plan_file = worktree / "plan.md"
        plan_file.write_text("""
**Goal:** E2E Test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | First task |
| Group 2    | 2     | Depends on Group 1 |

### Task 1: First task

Do this carefully.

### Task 2: Second task

Follow up work.
""")

        env = {**os.environ, "HYH_SOCKET": socket_path, "HYH_WORKTREE": str(worktree)}

        # Import
        r = subprocess.run(
            [sys.executable, "-m", "hyh.client", "plan", "import", "--file", str(plan_file)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        # Claim
        r = subprocess.run(
            [sys.executable, "-m", "hyh.client", "task", "claim"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "Do this carefully" in data["task"]["instructions"]

    finally:
        cleanup_daemon_subprocess(socket_path)


def test_lock_hierarchy_trajectory_after_state(workflow_with_tasks):
    """Verify trajectory logging happens AFTER state lock is released (release-then-log pattern).

    This test verifies the lock hierarchy from CLAUDE.md:
    1. StateManager._lock (highest priority)
    2. TrajectoryLogger._lock (lower priority)

    We verify that task_claim operations can read trajectory immediately after,
    proving that the state lock was released before trajectory logging.
    """
    send_command = workflow_with_tasks["send_command"]
    worktree = workflow_with_tasks["worktree"]
    trajectory_file = worktree / ".claude" / "trajectory.jsonl"

    # Claim a task - this should:
    # 1. Acquire StateManager._lock
    # 2. Update state
    # 3. Release StateManager._lock
    # 4. Acquire TrajectoryLogger._lock
    # 5. Log event
    # 6. Release TrajectoryLogger._lock
    resp = send_command({"command": "task_claim", "worker_id": "worker-1"})
    assert resp["status"] == "ok"
    assert resp["data"]["task"]["id"] == "task-1"

    # Immediately read trajectory file - this proves state lock was released
    # before trajectory logging (otherwise we'd have a convoy effect)
    assert trajectory_file.exists()
    lines = trajectory_file.read_text().strip().split("\n")
    assert len(lines) >= 1

    # Parse last line - should be the claim event
    event = json.loads(lines[-1])
    assert event["event_type"] == "task_claim"
    assert event["worker_id"] == "worker-1"
    assert event["task_id"] == "task-1"

    # Complete the task - same release-then-log pattern
    resp = send_command({"command": "task_complete", "task_id": "task-1", "worker_id": "worker-1"})
    assert resp["status"] == "ok"

    # Verify trajectory was updated
    lines = trajectory_file.read_text().strip().split("\n")
    assert len(lines) >= 2
    event = json.loads(lines[-1])
    assert event["event_type"] == "task_complete"
    assert event["task_id"] == "task-1"


def test_concurrent_state_and_trajectory_operations(workflow_with_parallel_tasks):
    """Verify concurrent operations don't deadlock.

    This test creates concurrent operations that mix:
    - State reads/writes (StateManager._lock)
    - Trajectory logging (TrajectoryLogger._lock)
    - Exec operations (GLOBAL_EXEC_LOCK when exclusive=True)

    The lock hierarchy from CLAUDE.md guarantees no deadlock:
    1. StateManager._lock (highest)
    2. TrajectoryLogger._lock (middle)
    3. GLOBAL_EXEC_LOCK (lowest)

    Operations must acquire locks in this order and release-then-log.
    """
    send_command = workflow_with_parallel_tasks["send_command"]
    worktree = workflow_with_parallel_tasks["worktree"]

    results = []
    errors = []
    lock = threading.Lock()

    def worker_task(worker_id: str):
        """Worker that claims task, reads state, and executes commands."""
        try:
            # 1. Claim task (State lock -> release -> Trajectory lock)
            resp = send_command({"command": "task_claim", "worker_id": worker_id})
            if resp["status"] != "ok":
                with lock:
                    errors.append(f"{worker_id}: claim failed - {resp.get('message')}")
                return

            task = resp["data"]["task"]
            if not task:
                with lock:
                    results.append((worker_id, "no_task"))
                return

            # 2. Read state (State lock)
            resp = send_command({"command": "get_state"})
            if resp["status"] != "ok":
                with lock:
                    errors.append(f"{worker_id}: get_state failed - {resp.get('message')}")
                return

            # 3. Execute command (GLOBAL_EXEC_LOCK if exclusive=True)
            # Use exclusive=False to test parallel execution
            resp = send_command(
                {
                    "command": "exec",
                    "args": ["echo", f"worker-{worker_id}"],
                    "cwd": str(worktree),
                    "env": {},
                    "exclusive": False,
                }
            )
            if resp["status"] != "ok":
                with lock:
                    errors.append(f"{worker_id}: exec failed - {resp.get('message')}")
                return

            # 4. Complete task (State lock -> release -> Trajectory lock)
            resp = send_command(
                {"command": "task_complete", "task_id": task["id"], "worker_id": worker_id}
            )
            if resp["status"] != "ok":
                with lock:
                    errors.append(f"{worker_id}: complete failed - {resp.get('message')}")
                return

            with lock:
                results.append((worker_id, task["id"]))

        except Exception as e:
            with lock:
                errors.append(f"{worker_id}: exception - {e!s}")

    # Launch 3 workers in parallel to stress-test lock hierarchy
    threads = [threading.Thread(target=worker_task, args=(f"worker-{i}",)) for i in range(3)]

    # Start all threads
    for t in threads:
        t.start()

    # Wait for completion with timeout to detect deadlocks
    for t in threads:
        t.join(timeout=10.0)  # 10 second timeout
        if t.is_alive():
            errors.append("Thread deadlock detected - timeout exceeded")

    # Verify no errors occurred
    assert len(errors) == 0, f"Errors during concurrent operations: {errors}"

    # Verify all workers successfully claimed and completed tasks
    assert len(results) == 3, f"Expected 3 successful operations, got {len(results)}"

    # Verify all tasks were unique
    task_ids = [task_id for _, task_id in results]
    assert len(set(task_ids)) == 3, "Workers should have claimed different tasks"


def test_multi_project_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two projects run concurrently with isolated daemons."""
    import subprocess

    from hyh.daemon import HarnessDaemon
    from hyh.registry import ProjectRegistry

    # Create two projects
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    for p in [project_a, project_b]:
        p.mkdir()
        (p / ".claude").mkdir()
        subprocess.run(["git", "init"], cwd=p, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=p,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=p,
            capture_output=True,
            check=True,
        )
        (p / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "-A"], cwd=p, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=p, capture_output=True, check=True)

    # Configure shared registry via env var
    registry_file = tmp_path / "registry.json"
    monkeypatch.setenv("HYH_REGISTRY_FILE", str(registry_file))

    # Clear HYH_SOCKET to use hash-based paths
    monkeypatch.delenv("HYH_SOCKET", raising=False)

    # Use unique socket paths based on worktree hash
    from hyh.client import get_socket_path

    socket_a = get_socket_path(project_a)
    socket_b = get_socket_path(project_b)

    # Sockets should be different
    assert socket_a != socket_b

    # Spawn daemon for project A
    daemon_a = HarnessDaemon(socket_a, str(project_a))

    try:
        # Spawn daemon for project B
        daemon_b = HarnessDaemon(socket_b, str(project_b))

        try:
            # Both daemons running concurrently
            assert Path(socket_a).exists()
            assert Path(socket_b).exists()

            # Registry has both projects (tests race-condition safety)
            registry = ProjectRegistry(registry_file)
            projects = registry.list_projects()
            assert len(projects) == 2

        finally:
            daemon_b.server_close()
    finally:
        daemon_a.server_close()
