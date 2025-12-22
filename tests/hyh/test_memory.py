"""Memory profiling tests for Harness using pytest-memray.

These tests verify memory bounds for critical operations, ensuring
no unbounded memory growth or leaks.

Run with: make memcheck
"""

import pytest

from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore
from hyh.trajectory import TrajectoryLogger

pytestmark = pytest.mark.memcheck


# =============================================================================
# TrajectoryLogger Memory Tests
# =============================================================================


@pytest.mark.limit_memory("100 MB")
def test_trajectory_log_bounded_memory(tmp_path):
    """Logging 10K events should not allocate unbounded memory.

    The TrajectoryLogger appends to disk; memory should remain bounded
    regardless of how many events are logged.
    """
    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")
    for i in range(10_000):
        logger.log({"event": f"event-{i}", "data": "x" * 100})


@pytest.mark.limit_memory("50 MB")
def test_trajectory_tail_bounded_memory(tmp_path):
    """tail() should not load entire file into memory.

    Reading last 10 events from a 10K event file should use O(k) memory,
    not O(N) where N is file size.
    """
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)

    # Write 10K events first
    for i in range(10_000):
        logger.log({"event": f"event-{i}", "data": "x" * 100})

    # This should not load the entire file
    result = logger.tail(10)
    assert len(result) == 10
    assert result[-1]["event"] == "event-9999"


# =============================================================================
# StateManager Memory Tests
# =============================================================================


@pytest.mark.limit_memory("100 MB")
def test_state_save_load_bounded_memory(tmp_path):
    """Save/load cycle for 1000 tasks should have bounded memory."""
    manager = WorkflowStateStore(tmp_path)

    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i} with some description text",
            status=TaskStatus.PENDING,
            dependencies=[],
            timeout_seconds=600,
        )

    state = WorkflowState(tasks=tasks)
    manager.save(state)

    # Reload multiple times to check for leaks
    for _ in range(10):
        loaded = manager.load()
        assert len(loaded.tasks) == 1000


@pytest.mark.limit_memory("100 MB")
def test_claim_task_repeated_bounded_memory(tmp_path):
    """Repeated claim_task operations should not leak memory."""
    manager = WorkflowStateStore(tmp_path)

    tasks = {}
    for i in range(100):
        task_id = f"task-{i}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=[],
            timeout_seconds=600,
        )

    state = WorkflowState(tasks=tasks)
    manager.save(state)

    # Claim all 100 tasks
    for i in range(100):
        worker_id = f"worker-{i}"
        result = manager.claim_task(worker_id)
        if result.task:
            manager.complete_task(result.task.id, worker_id)


# =============================================================================
# DAG Validation Memory Tests
# =============================================================================


@pytest.mark.limit_memory("50 MB")
def test_dag_validation_bounded_memory():
    """DAG validation for 1000 nodes should use bounded memory.

    DFS-based cycle detection should not create excessive intermediate state.
    """
    tasks = {}
    tasks["root"] = Task(
        id="root",
        description="Root task",
        status=TaskStatus.PENDING,
        dependencies=[],
    )

    prev_layer = ["root"]
    for layer in range(1, 4):
        current_layer = []
        for i in range(250):
            task_id = f"layer-{layer}-task-{i}"
            dependencies = prev_layer[: min(3, len(prev_layer))]
            tasks[task_id] = Task(
                id=task_id,
                description=f"Layer {layer} task {i}",
                status=TaskStatus.PENDING,
                dependencies=dependencies,
            )
            current_layer.append(task_id)
        prev_layer = current_layer

    while len(tasks) < 1000:
        task_id = f"extra-task-{len(tasks)}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Extra task {len(tasks)}",
            status=TaskStatus.PENDING,
            dependencies=["root"],
        )

    state = WorkflowState(tasks=tasks)
    state.validate_dag()
