# tests/hyh/test_integration_council.py
"""Integration tests verifying Council Amendments A, B, C work together."""

import json
import threading
import time

import pytest

from tests.hyh.conftest import send_command

# socket_path and worktree fixtures are imported from conftest.py


def test_amendments_work_together(socket_path, worktree):
    """All three Council amendments should work in harmony."""
    from hyh.daemon import HarnessDaemon
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    # Amendment C: Create valid DAG (no cycles)
    manager = WorkflowStateStore(worktree)
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
    manager.save(state)

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

        trajectory_file = worktree / ".claude" / "trajectory.jsonl"
        with open(trajectory_file) as f:
            lines = f.readlines()
            exec_events = [json.loads(line) for line in lines if "exec" in line]
            assert any("duration_ms" in e for e in exec_events)
    finally:
        daemon.shutdown()
        daemon.server_close()
        server_thread.join(timeout=2)


def test_cyclic_dag_rejected_at_boundary(tmp_path):
    """Amendment C: Cyclic dependencies must be rejected."""
    from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

    manager = WorkflowStateStore(tmp_path)
    cyclic_state = WorkflowState(
        tasks={
            "a": Task(id="a", description="A", status=TaskStatus.PENDING, dependencies=["b"]),
            "b": Task(id="b", description="B", status=TaskStatus.PENDING, dependencies=["a"]),
        }
    )

    with pytest.raises(ValueError, match="[Cc]ycle"):
        manager.save(cyclic_state)
