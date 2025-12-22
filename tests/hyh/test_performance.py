"""Performance regression tests for Harness using pytest-benchmark.

These tests verify O(V+E) and O(k) complexity guarantees as documented in CLAUDE.md.
Ensures claim_task, tail(), and validate_dag maintain performance characteristics
at scale.

Run with: make benchmark
"""

import contextlib

import pytest
from msgspec.structs import replace as struct_replace

from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore
from hyh.trajectory import TrajectoryLogger

pytestmark = pytest.mark.benchmark


# =============================================================================
# StateManager Benchmarks
# =============================================================================


@pytest.fixture
def dag_1000_linear(tmp_path):
    """1000-task DAG with linear chain dependencies (O(V+E) where V=1000, E=999)."""
    manager = WorkflowStateStore(tmp_path)
    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        dependencies = [f"task-{i - 1}"] if i > 0 else []
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=dependencies,
            timeout_seconds=600,
        )
    state = WorkflowState(tasks=tasks)
    manager.save(state)
    return manager


@pytest.fixture
def dag_1000_groups(tmp_path):
    """1000-task DAG with 100 independent groups (10 tasks each)."""
    manager = WorkflowStateStore(tmp_path)
    tasks = {}
    for group in range(100):
        for i in range(10):
            task_id = f"group-{group}-task-{i}"
            dependencies = [f"group-{group}-task-{i - 1}"] if i > 0 else []
            tasks[task_id] = Task(
                id=task_id,
                description=f"Group {group} task {i}",
                status=TaskStatus.PENDING,
                dependencies=dependencies,
            )
    state = WorkflowState(tasks=tasks)
    manager.save(state)
    return manager


@pytest.fixture
def dag_900_completed(tmp_path):
    """1000 tasks where 900 are completed, 100 pending."""
    manager = WorkflowStateStore(tmp_path)
    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        status = TaskStatus.COMPLETED if i < 900 else TaskStatus.PENDING
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=status,
            dependencies=[],
            timeout_seconds=600,
        )
    state = WorkflowState(tasks=tasks)
    manager.save(state)
    return manager


def test_claim_task_linear_dag(benchmark, dag_1000_linear):
    """claim_task should maintain O(V+E) complexity at 1000 tasks.

    Per CLAUDE.md Section VIII: For N < 1000 tasks, O(V+E) iteration is acceptable.
    """
    manager = dag_1000_linear

    def claim():
        # Reload state to reset for each benchmark iteration
        state = manager.load()
        # Reset first task to pending for re-claiming
        state.tasks["task-0"] = struct_replace(
            state.tasks["task-0"], status=TaskStatus.PENDING, claimed_by=None
        )
        manager.save(state)
        return manager.claim_task("worker-1")

    result = benchmark(claim)
    assert result.task is not None
    assert result.task.id == "task-0"


def test_claim_task_grouped_dag(benchmark, dag_1000_groups):
    """claim_task should efficiently find claimable tasks in complex DAGs."""
    manager = dag_1000_groups

    def claim():
        state = manager.load()
        # Reset all group-0 tasks to pending
        for i in range(10):
            task_id = f"group-0-task-{i}"
            state.tasks[task_id] = struct_replace(
                state.tasks[task_id], status=TaskStatus.PENDING, claimed_by=None
            )
        manager.save(state)
        return manager.claim_task("worker-1")

    result = benchmark(claim)
    assert result.task is not None
    assert result.task.id.endswith("-task-0")


def test_claim_task_mostly_completed(benchmark, dag_900_completed):
    """claim_task should efficiently skip completed tasks."""
    manager = dag_900_completed

    def claim():
        state = manager.load()
        # Reset one pending task
        state.tasks["task-900"] = struct_replace(
            state.tasks["task-900"], status=TaskStatus.PENDING, claimed_by=None
        )
        manager.save(state)
        return manager.claim_task("worker-1")

    result = benchmark(claim)
    assert result.task is not None
    assert int(result.task.id.split("-")[1]) >= 900


# =============================================================================
# TrajectoryLogger Benchmarks
# =============================================================================


@pytest.fixture
def large_trajectory(tmp_path):
    """10K events trajectory file (~1MB)."""
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)
    for i in range(10_000):
        logger.log({"event": f"event-{i}", "data": "x" * 100})
    return logger


@pytest.fixture
def large_payload_trajectory(tmp_path):
    """10K events with 1KB payloads (>10MB)."""
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)
    large_payload = "x" * 1000
    for i in range(10_000):
        logger.log(
            {
                "event": i,
                "large_data": large_payload,
                "metadata": {"index": i, "timestamp": i * 1000},
            }
        )
    return logger


def test_trajectory_tail_10k(benchmark, large_trajectory):
    """tail() should be O(k) not O(N) on large files.

    Per CLAUDE.md Section VIII: O(k) reverse seek where k = block size.
    """
    result = benchmark(large_trajectory.tail, 10)
    assert len(result) == 10
    assert result[-1]["event"] == "event-9999"
    assert result[0]["event"] == "event-9990"


def test_trajectory_tail_large_payloads(benchmark, large_payload_trajectory):
    """tail() should maintain O(k) even with large event payloads."""
    result = benchmark(large_payload_trajectory.tail, 5)
    assert len(result) == 5
    assert result[-1]["event"] == 9999


# =============================================================================
# DAG Validation Benchmarks
# =============================================================================


@pytest.fixture
def diamond_dag():
    """1000-node DAG with diamond structure (multiple paths between nodes)."""
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

    return WorkflowState(tasks=tasks)


def test_dag_validation_1000_nodes(benchmark, diamond_dag):
    """validate_dag should complete in reasonable time for 1000 nodes.

    Per CLAUDE.md Section VII: Defensive Graph Construction requires cycle detection.
    """
    benchmark(diamond_dag.validate_dag)


def test_dag_cycle_detection(benchmark):
    """validate_dag should quickly detect cycles even in large graphs."""
    tasks = {
        "task-a": Task(
            id="task-a",
            description="Task A",
            status=TaskStatus.PENDING,
            dependencies=["task-c"],
        ),
        "task-b": Task(
            id="task-b",
            description="Task B",
            status=TaskStatus.PENDING,
            dependencies=["task-a"],
        ),
        "task-c": Task(
            id="task-c",
            description="Task C",
            status=TaskStatus.PENDING,
            dependencies=["task-b"],
        ),
    }
    for i in range(997):
        task_id = f"task-{i}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=[],
        )
    state = WorkflowState(tasks=tasks)

    def detect_cycle():
        with contextlib.suppress(ValueError):
            state.validate_dag()

    benchmark(detect_cycle)
