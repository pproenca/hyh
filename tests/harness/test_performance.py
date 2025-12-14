"""Performance regression tests for Harness.

These tests verify O(V+E) and O(k) complexity guarantees as documented in CLAUDE.md.
Ensures claim_task, tail(), and validate_dag maintain performance characteristics
at scale.
"""

import time

import pytest

from harness.state import StateManager, Task, TaskStatus, WorkflowState
from harness.trajectory import TrajectoryLogger


def test_claim_task_scales_with_dag_size(tmp_path):
    """claim_task should maintain O(V+E) complexity at 1000 tasks, completing in <100ms.

    Per CLAUDE.md Section VIII: For N < 1000 tasks, O(V+E) iteration is acceptable.
    This test creates a 1000-node DAG and verifies claim_task completes in <100ms.
    """
    manager = StateManager(tmp_path)

    # Create a 1000-task DAG with linear dependencies (chain structure)
    # This represents O(V+E) where V=1000, E=999
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

    # Mark first task as available (no dependencies)
    state = WorkflowState(tasks=tasks)
    manager.save(state)

    # Measure claim_task performance - should find task-0
    start = time.monotonic()
    task = manager.claim_task("worker-1")
    elapsed = (time.monotonic() - start) * 1000  # Convert to ms

    assert task is not None, "Should claim task-0"
    assert task.id == "task-0", "Should claim first task in chain"
    assert elapsed < 100, f"claim_task took {elapsed:.2f}ms, should be < 100ms (O(V+E) guarantee)"


def test_trajectory_tail_on_large_file(tmp_path):
    """tail() should be O(k) not O(N) on large files (50K events), completing in <50ms.

    Per CLAUDE.md Section VIII: O(k) reverse seek where k = block size.
    Verifies tail() does NOT read entire file (O(N)) but instead uses reverse seek.
    """
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)

    # Create a large file with 50K events (~5MB)
    for i in range(50_000):
        logger.log({"event": f"event-{i}", "data": "x" * 100})

    # Verify file size is substantial
    file_size = trajectory_file.stat().st_size
    assert file_size > 5_000_000, "File should be > 5MB for performance test"

    # Measure tail performance - should use O(k) reverse seek, not O(N) full read
    start = time.monotonic()
    result = logger.tail(10)
    elapsed = (time.monotonic() - start) * 1000  # Convert to ms

    assert len(result) == 10, "Should return exactly 10 events"
    assert result[-1]["event"] == "event-49999", "Should return last event"
    assert result[0]["event"] == "event-49990", "Should return 10th-from-last event"
    assert elapsed < 50, f"tail(10) took {elapsed:.2f}ms, should be < 50ms (O(k) guarantee)"


def test_dag_validation_on_large_graph(tmp_path):
    """validate_dag should complete in reasonable time for 1000 nodes.

    Per CLAUDE.md Section VII: Defensive Graph Construction requires cycle detection
    on every load. This test verifies DFS-based validation scales to 1000 nodes.
    Target: <100ms for 1000-node validation (generous budget for DFS).
    """
    # Create a 1000-node DAG with diamond structure (multiple paths between nodes)
    # This creates a more complex graph than linear chain
    tasks = {}

    # Layer 0: 1 root task
    tasks["root"] = Task(
        id="root",
        description="Root task",
        status=TaskStatus.PENDING,
        dependencies=[],
    )

    # Layer 1-3: Create layers with dependencies to previous layer
    # This creates a wide DAG with multiple paths (tests DFS thoroughly)
    prev_layer = ["root"]
    for layer in range(1, 4):
        current_layer = []
        for i in range(250):  # 250 nodes per layer = 1000 total (1 + 3*250 = 751, adjust)
            task_id = f"layer-{layer}-task-{i}"
            # Each task depends on multiple tasks from previous layer
            dependencies = prev_layer[: min(3, len(prev_layer))]
            tasks[task_id] = Task(
                id=task_id,
                description=f"Layer {layer} task {i}",
                status=TaskStatus.PENDING,
                dependencies=dependencies,
            )
            current_layer.append(task_id)
        prev_layer = current_layer

    # Adjust to exactly 1000 tasks
    while len(tasks) < 1000:
        task_id = f"extra-task-{len(tasks)}"
        tasks[task_id] = Task(
            id=task_id,
            description=f"Extra task {len(tasks)}",
            status=TaskStatus.PENDING,
            dependencies=["root"],
        )

    state = WorkflowState(tasks=tasks)

    # Measure validation performance
    start = time.monotonic()
    state.validate_dag()  # Should not raise (no cycles)
    elapsed = (time.monotonic() - start) * 1000  # Convert to ms

    assert elapsed < 100, f"validate_dag took {elapsed:.2f}ms for 1000 nodes, should be < 100ms"


def test_claim_task_with_complex_dependencies(tmp_path):
    """claim_task should efficiently traverse complex dependency graphs.

    Verifies O(V+E) iteration works correctly when there are multiple claimable
    tasks in a complex DAG structure.
    """
    manager = StateManager(tmp_path)

    # Create a complex DAG:
    # - 100 independent task groups
    # - Each group has 10 tasks with internal dependencies
    # Total: 1000 tasks with complex structure
    tasks = {}
    for group in range(100):
        for i in range(10):
            task_id = f"group-{group}-task-{i}"
            # Each task depends on previous task in group (except first)
            dependencies = [f"group-{group}-task-{i - 1}"] if i > 0 else []
            tasks[task_id] = Task(
                id=task_id,
                description=f"Group {group} task {i}",
                status=TaskStatus.PENDING,
                dependencies=dependencies,
            )

    state = WorkflowState(tasks=tasks)
    manager.save(state)

    # Should quickly find one of the 100 claimable tasks (first task of each group)
    start = time.monotonic()
    task = manager.claim_task("worker-1")
    elapsed = (time.monotonic() - start) * 1000

    assert task is not None, "Should claim one of the 100 claimable tasks"
    assert task.id.endswith("-task-0"), "Should claim first task of a group"
    assert elapsed < 100, f"claim_task took {elapsed:.2f}ms, should be < 100ms"


def test_trajectory_tail_performance_with_large_events(tmp_path):
    """tail() should maintain O(k) even with large event payloads.

    Verifies that large JSON objects don't degrade tail() performance
    beyond block-read overhead.
    """
    trajectory_file = tmp_path / "trajectory.jsonl"
    logger = TrajectoryLogger(trajectory_file)

    # Create events with large payloads (1KB each)
    large_payload = "x" * 1000
    for i in range(10_000):
        logger.log(
            {
                "event": i,
                "large_data": large_payload,
                "metadata": {"index": i, "timestamp": i * 1000},
            }
        )

    # File should be ~10MB
    file_size = trajectory_file.stat().st_size
    assert file_size > 10_000_000, "File should be > 10MB"

    # tail() should still be fast (O(k) block reads, not O(N) full parse)
    start = time.monotonic()
    result = logger.tail(5)
    elapsed = (time.monotonic() - start) * 1000

    assert len(result) == 5
    assert result[-1]["event"] == 9999
    assert elapsed < 50, f"tail(5) took {elapsed:.2f}ms on 10MB file, should be < 50ms"


def test_dag_validation_detects_cycles_efficiently(tmp_path):
    """validate_dag should quickly detect cycles even in large graphs.

    Verifies that cycle detection doesn't degrade when encountering cycles
    early in the traversal.
    """
    # Create a 1000-node graph with a cycle introduced early
    tasks = {}
    for i in range(1000):
        task_id = f"task-{i}"
        if i == 0:
            # Create cycle: task-0 depends on task-999
            dependencies = ["task-999"]
        elif i < 999:
            dependencies = [f"task-{i - 1}"]
        else:
            # task-999 depends on task-998, creating cycle via task-0
            dependencies = [f"task-{i - 1}"]

        tasks[task_id] = Task(
            id=task_id,
            description=f"Task {i}",
            status=TaskStatus.PENDING,
            dependencies=dependencies,
        )

    state = WorkflowState(tasks=tasks)

    # Should detect cycle quickly (doesn't need to traverse entire graph)
    start = time.monotonic()
    with pytest.raises(ValueError, match="[Cc]ycle"):
        state.validate_dag()
    elapsed = (time.monotonic() - start) * 1000

    # Should be very fast since cycle is detected early
    assert elapsed < 50, f"Cycle detection took {elapsed:.2f}ms, should be < 50ms"


def test_claim_task_performance_with_completed_tasks(tmp_path):
    """claim_task should efficiently skip completed tasks in large DAGs.

    Verifies that iteration over completed tasks doesn't add significant overhead
    when searching for claimable tasks.
    """
    manager = StateManager(tmp_path)

    # Create 1000 tasks: 900 completed, 100 pending
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

    # Should quickly find one of the 100 pending tasks
    start = time.monotonic()
    task = manager.claim_task("worker-1")
    elapsed = (time.monotonic() - start) * 1000

    assert task is not None
    assert task.status == TaskStatus.RUNNING  # Now claimed
    assert int(task.id.split("-")[1]) >= 900  # One of the pending tasks
    assert elapsed < 100, f"claim_task took {elapsed:.2f}ms, should be < 100ms"
