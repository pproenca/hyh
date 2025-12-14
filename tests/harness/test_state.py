# tests/harness/test_state.py
"""Tests for Pydantic state models and StateManager."""

import pytest
import json
import threading
from datetime import datetime, timedelta
from harness.state import (
    TaskStatus,
    Task,
    WorkflowState,
    PendingHandoff,
    StateManager,
)


# ============================================================================
# TestTaskModel: task validation, timeout_seconds default (600), custom timeout,
# claimed_by field, is_timed_out() method
# ============================================================================


def test_task_model_basic_validation():
    """Task should validate and store all fields."""
    task = Task(
        id="task-1",
        description="Implement feature X",
        status=TaskStatus.PENDING,
        dependencies=[],
    )
    assert task.id == "task-1"
    assert task.description == "Implement feature X"
    assert task.status == TaskStatus.PENDING
    assert task.dependencies == []
    assert task.started_at is None
    assert task.completed_at is None
    assert task.claimed_by is None


def test_task_timeout_seconds_default():
    """Task should have default timeout_seconds of 600."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.PENDING,
        dependencies=[],
    )
    assert task.timeout_seconds == 600


def test_task_timeout_seconds_custom():
    """Task should accept custom timeout_seconds."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.PENDING,
        dependencies=[],
        timeout_seconds=1200,
    )
    assert task.timeout_seconds == 1200


def test_task_claimed_by_field():
    """Task should have claimed_by field for worker_id idempotency."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.RUNNING,
        dependencies=[],
        claimed_by="worker-123",
    )
    assert task.claimed_by == "worker-123"


def test_task_is_timed_out_not_started():
    """is_timed_out should return False if task not started."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.PENDING,
        dependencies=[],
        timeout_seconds=10,
    )
    assert task.is_timed_out() is False


def test_task_is_timed_out_within_timeout():
    """is_timed_out should return False if within timeout window."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.RUNNING,
        dependencies=[],
        started_at=datetime.now(),
        timeout_seconds=600,
    )
    assert task.is_timed_out() is False


def test_task_is_timed_out_exceeded():
    """is_timed_out should return True if timeout exceeded."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.RUNNING,
        dependencies=[],
        started_at=datetime.now() - timedelta(seconds=700),
        timeout_seconds=600,
    )
    assert task.is_timed_out() is True


def test_task_is_timed_out_completed():
    """is_timed_out should return False if task completed."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.COMPLETED,
        dependencies=[],
        started_at=datetime.now() - timedelta(seconds=700),
        completed_at=datetime.now(),
        timeout_seconds=600,
    )
    assert task.is_timed_out() is False


# ============================================================================
# TestWorkflowState: v2 schema with tasks dict, get_claimable_task
# (no deps, with deps, multiple deps, none available, reclaims timed-out),
# get_task_for_worker (idempotency, assigns new)
# ============================================================================


def test_workflow_state_v2_schema_with_tasks_dict():
    """WorkflowState should have tasks dict for v2 schema."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Task 2",
                status=TaskStatus.PENDING,
                dependencies=["task-1"],
            ),
        }
    )
    assert len(state.tasks) == 2
    assert "task-1" in state.tasks
    assert "task-2" in state.tasks


def test_get_claimable_task_no_deps():
    """get_claimable_task should return task with no dependencies."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    task = state.get_claimable_task()
    assert task is not None
    assert task.id == "task-1"


def test_get_claimable_task_with_deps():
    """get_claimable_task should not return task with uncompleted dependencies."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Task 2",
                status=TaskStatus.PENDING,
                dependencies=["task-1"],
            ),
        }
    )
    task = state.get_claimable_task()
    assert task is not None
    assert task.id == "task-1"  # Should return task-1, not task-2


def test_get_claimable_task_multiple_deps():
    """get_claimable_task should wait for all dependencies to complete."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.COMPLETED,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Task 2",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-3": Task(
                id="task-3",
                description="Task 3",
                status=TaskStatus.PENDING,
                dependencies=["task-1", "task-2"],
            ),
        }
    )
    task = state.get_claimable_task()
    assert task is not None
    assert task.id == "task-2"  # task-3 still waiting on task-2


def test_get_claimable_task_all_deps_completed():
    """get_claimable_task should return task when all deps completed."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.COMPLETED,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Task 2",
                status=TaskStatus.COMPLETED,
                dependencies=[],
            ),
            "task-3": Task(
                id="task-3",
                description="Task 3",
                status=TaskStatus.PENDING,
                dependencies=["task-1", "task-2"],
            ),
        }
    )
    task = state.get_claimable_task()
    assert task is not None
    assert task.id == "task-3"


def test_get_claimable_task_none_available():
    """get_claimable_task should return None when no tasks available."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Task 2",
                status=TaskStatus.COMPLETED,
                dependencies=[],
            ),
        }
    )
    task = state.get_claimable_task()
    assert task is None


def test_get_claimable_task_reclaims_timed_out():
    """get_claimable_task should reclaim timed-out tasks."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
                started_at=datetime.now() - timedelta(seconds=700),
                timeout_seconds=600,
                claimed_by="worker-old",
            ),
        }
    )
    task = state.get_claimable_task()
    assert task is not None
    assert task.id == "task-1"
    assert task.is_timed_out() is True


def test_get_task_for_worker_idempotency():
    """get_task_for_worker should return same task for same worker_id."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker-123",
            ),
        }
    )
    task = state.get_task_for_worker("worker-123")
    assert task is not None
    assert task.id == "task-1"
    assert task.claimed_by == "worker-123"


def test_get_task_for_worker_assigns_new():
    """get_task_for_worker should assign new task if worker has none."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    task = state.get_task_for_worker("worker-new")
    assert task is not None
    assert task.id == "task-1"


# ============================================================================
# TestStateManagerJSON: state_file is .json, save creates valid JSON,
# load reads JSON, update modifies JSON, no frontmatter methods
# ============================================================================


def test_state_manager_json_state_file_is_json(tmp_path):
    """StateManager should use .json file, not .md."""
    manager = StateManager(tmp_path)
    assert manager.state_file.suffix == ".json"
    assert str(manager.state_file).endswith("dev-workflow-state.json")


def test_state_manager_json_save_creates_valid_json(tmp_path):
    """StateManager.save should create valid JSON file."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    # Verify JSON file exists and is valid
    assert manager.state_file.exists()
    content = manager.state_file.read_text()
    data = json.loads(content)  # Should not raise
    assert "tasks" in data
    assert "task-1" in data["tasks"]


def test_state_manager_json_load_reads_json(tmp_path):
    """StateManager.load should read JSON file."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    # Load in new manager instance
    manager2 = StateManager(tmp_path)
    loaded = manager2.load()
    assert loaded is not None
    assert "task-1" in loaded.tasks
    assert loaded.tasks["task-1"].description == "Task 1"


def test_state_manager_json_update_modifies_json(tmp_path):
    """StateManager.update should modify JSON file."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    # Update with new tasks
    new_tasks = {
        "task-1": Task(
            id="task-1",
            description="Task 1 Updated",
            status=TaskStatus.COMPLETED,
            dependencies=[],
        ),
    }
    updated = manager.update(tasks=new_tasks)
    assert updated.tasks["task-1"].description == "Task 1 Updated"
    assert updated.tasks["task-1"].status == TaskStatus.COMPLETED

    # Verify persisted in JSON
    loaded = StateManager(tmp_path).load()
    assert loaded.tasks["task-1"].description == "Task 1 Updated"


def test_state_manager_no_frontmatter_methods(tmp_path):
    """StateManager should NOT have _parse_frontmatter or _to_frontmatter methods."""
    manager = StateManager(tmp_path)
    assert not hasattr(manager, "_parse_frontmatter")
    assert not hasattr(manager, "_to_frontmatter")


# ============================================================================
# TestStateManagerAtomicMethods: claim_task_atomic, claim_task_returns_existing,
# claim_task_race_condition_prevented (threading test), complete_task_atomic,
# complete_task_validates_ownership
# ============================================================================


def test_claim_task_atomic(tmp_path):
    """claim_task should atomically find, update, and save task."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    # Claim task
    task = manager.claim_task("worker-1")
    assert task is not None
    assert task.id == "task-1"
    assert task.claimed_by == "worker-1"
    assert task.status == TaskStatus.RUNNING
    assert task.started_at is not None

    # Verify persisted
    loaded = StateManager(tmp_path).load()
    assert loaded.tasks["task-1"].claimed_by == "worker-1"
    assert loaded.tasks["task-1"].status == TaskStatus.RUNNING


def test_claim_task_returns_existing(tmp_path):
    """claim_task should return existing task for same worker_id (idempotency)."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker-1",
                started_at=datetime.now(),
            ),
        }
    )
    manager.save(state)

    # Claim task again with same worker
    task = manager.claim_task("worker-1")
    assert task is not None
    assert task.id == "task-1"
    assert task.claimed_by == "worker-1"


def test_claim_task_renews_lease_on_retry(tmp_path):
    """claim_task should renew started_at on idempotent retry to prevent task stealing."""
    from datetime import timedelta

    manager = StateManager(tmp_path)
    old_time = datetime.now() - timedelta(minutes=5)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker-1",
                started_at=old_time,
            ),
        }
    )
    manager.save(state)

    # Retry claim with same worker
    before_claim = datetime.now()
    task = manager.claim_task("worker-1")

    assert task is not None
    assert task.id == "task-1"
    assert task.claimed_by == "worker-1"
    # Critical: started_at must be renewed
    assert task.started_at >= before_claim, "Lease was not renewed on retry"


def test_claim_task_race_condition_prevented(tmp_path):
    """claim_task should prevent race conditions with threading."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Task 2",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    results = []

    def claim_worker(worker_id):
        task = manager.claim_task(worker_id)
        if task:
            results.append((worker_id, task.id))

    # Spawn multiple threads trying to claim tasks
    threads = []
    for i in range(5):
        t = threading.Thread(target=claim_worker, args=(f"worker-{i}",))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify no duplicate claims
    claimed_tasks = [task_id for _, task_id in results]
    assert len(claimed_tasks) == len(set(claimed_tasks))  # No duplicates
    assert len(results) <= 2  # Only 2 tasks available


def test_complete_task_atomic(tmp_path):
    """complete_task should atomically update and save task."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker-1",
                started_at=datetime.now(),
            ),
        }
    )
    manager.save(state)

    # Complete task
    manager.complete_task("task-1", "worker-1")

    # Verify persisted
    loaded = StateManager(tmp_path).load()
    assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED
    assert loaded.tasks["task-1"].completed_at is not None


def test_complete_task_validates_ownership(tmp_path):
    """complete_task should validate worker owns the task."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker-1",
                started_at=datetime.now(),
            ),
        }
    )
    manager.save(state)

    # Try to complete with wrong worker
    with pytest.raises(ValueError, match="Task task-1 is not claimed by worker-2"):
        manager.complete_task("task-1", "worker-2")


def test_pending_handoff_model():
    """PendingHandoff model should validate mode and plan."""
    handoff = PendingHandoff(mode="sequential", plan="/path/to/plan.md")
    assert handoff.mode == "sequential"
    assert handoff.plan == "/path/to/plan.md"


# ============================================================================
# TestValidateDAG: cycle detection tests (Amendment C)
# ============================================================================


def test_validate_dag_no_cycle():
    """validate_dag should not raise for valid DAG."""
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
            "task-2": Task(
                id="task-2",
                description="Task 2",
                status=TaskStatus.PENDING,
                dependencies=["task-1"],
            ),
            "task-3": Task(
                id="task-3",
                description="Task 3",
                status=TaskStatus.PENDING,
                dependencies=["task-1", "task-2"],
            ),
        }
    )
    # Should not raise
    state.validate_dag()


def test_validate_dag_detects_simple_cycle():
    """validate_dag should raise ValueError for A -> B -> A cycle."""
    state = WorkflowState(
        tasks={
            "task-a": Task(
                id="task-a",
                description="Task A",
                status=TaskStatus.PENDING,
                dependencies=["task-b"],
            ),
            "task-b": Task(
                id="task-b",
                description="Task B",
                status=TaskStatus.PENDING,
                dependencies=["task-a"],
            ),
        }
    )
    with pytest.raises(ValueError, match="[Cc]ycle"):
        state.validate_dag()


def test_validate_dag_detects_self_cycle():
    """validate_dag should raise ValueError for self-referencing task."""
    state = WorkflowState(
        tasks={
            "task-a": Task(
                id="task-a",
                description="Task A",
                status=TaskStatus.PENDING,
                dependencies=["task-a"],
            ),
        }
    )
    with pytest.raises(ValueError, match="[Cc]ycle"):
        state.validate_dag()


def test_validate_dag_detects_long_cycle():
    """validate_dag should raise ValueError for A -> B -> C -> A cycle."""
    state = WorkflowState(
        tasks={
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
    )
    with pytest.raises(ValueError, match="[Cc]ycle"):
        state.validate_dag()


def test_validate_dag_empty_tasks():
    """validate_dag should not raise for empty task dict."""
    state = WorkflowState(tasks={})
    # Should not raise
    state.validate_dag()


# ============================================================================
# TestStateManagerValidatesDAG: save validates DAG (Amendment C - Part 2)
# ============================================================================


def test_state_manager_save_validates_dag(tmp_path):
    """StateManager.save should validate DAG before saving."""
    manager = StateManager(tmp_path)

    # Create state with cycle
    state = WorkflowState(
        tasks={
            "task-a": Task(
                id="task-a",
                description="Task A",
                status=TaskStatus.PENDING,
                dependencies=["task-b"],
            ),
            "task-b": Task(
                id="task-b",
                description="Task B",
                status=TaskStatus.PENDING,
                dependencies=["task-a"],
            ),
        }
    )

    with pytest.raises(ValueError, match="[Cc]ycle"):
        manager.save(state)

    # File should not exist (save was rejected)
    assert not manager.state_file.exists()
