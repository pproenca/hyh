# tests/hyh/test_state.py
"""Tests for Pydantic state models and StateManager."""

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from hyh.state import (
    PendingHandoff,
    Task,
    TaskStatus,
    WorkflowState,
    WorkflowStateStore,
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
    assert task.dependencies == ()
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


def test_task_instructions_field():
    """Task should have instructions field for orchestrator injection."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.PENDING,
        dependencies=[],
        instructions="Step 1: Read the file. Step 2: Modify the function.",
    )
    assert task.instructions == "Step 1: Read the file. Step 2: Modify the function."


def test_task_role_field():
    """Task should have role field for agent specialization."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.PENDING,
        dependencies=[],
        role="frontend",
    )
    assert task.role == "frontend"


def test_task_injection_fields_default_to_none():
    """Injection fields should default to None for backwards compat."""
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.PENDING,
        dependencies=[],
    )
    assert task.instructions is None
    assert task.role is None


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


def test_task_is_timed_out_with_timezone_aware_started_at():
    """is_timed_out should handle timezone-aware started_at without crashing.

    Bug: datetime.now() is naive, but Pydantic can deserialize ISO strings
    to timezone-aware datetimes. Subtracting naive from aware raises TypeError.
    """

    # Create task with timezone-aware started_at (as Pydantic would from JSON)
    task = Task(
        id="task-1",
        description="Test task",
        status=TaskStatus.RUNNING,
        dependencies=[],
        started_at=datetime.now(UTC) - timedelta(seconds=700),
        timeout_seconds=600,
    )
    # This should NOT raise TypeError
    assert task.is_timed_out() is True


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


def test_get_claimable_task_raises_on_missing_dependency():
    """get_claimable_task should raise error if dependency doesn't exist.

    Bug: The code has `if dep_id in self.tasks` which silently treats
    missing dependencies as satisfied. This could allow a task to be
    claimed when its dependency doesn't exist.
    """
    # Create state with missing dependency (bypassing validation for test)
    state = WorkflowState(
        tasks={
            "task-a": Task(
                id="task-a",
                description="Task A",
                status=TaskStatus.PENDING,
                dependencies=["task-nonexistent"],  # References missing task!
            ),
        }
    )
    # Should raise error, not silently return task-a as claimable
    with pytest.raises(ValueError, match="[Mm]issing dependency"):
        state.get_claimable_task()


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
    manager = WorkflowStateStore(tmp_path)
    assert manager.state_file.suffix == ".json"
    assert str(manager.state_file).endswith("dev-workflow-state.json")


def test_state_manager_json_save_creates_valid_json(tmp_path):
    """StateManager.save should create valid JSON file."""
    manager = WorkflowStateStore(tmp_path)
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

    assert manager.state_file.exists()
    content = manager.state_file.read_text()
    data = json.loads(content)  # Should not raise
    assert "tasks" in data
    assert "task-1" in data["tasks"]


def test_state_manager_json_load_reads_json(tmp_path):
    """StateManager.load should read JSON file."""
    manager = WorkflowStateStore(tmp_path)
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

    manager2 = WorkflowStateStore(tmp_path)
    loaded = manager2.load()
    assert loaded is not None
    assert "task-1" in loaded.tasks
    assert loaded.tasks["task-1"].description == "Task 1"


def test_state_manager_json_update_modifies_json(tmp_path):
    """StateManager.update should modify JSON file."""
    manager = WorkflowStateStore(tmp_path)
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

    loaded = WorkflowStateStore(tmp_path).load()
    assert loaded is not None
    assert loaded.tasks["task-1"].description == "Task 1 Updated"


def test_state_manager_no_frontmatter_methods(tmp_path):
    """StateManager should NOT have _parse_frontmatter or _to_frontmatter methods."""
    manager = WorkflowStateStore(tmp_path)
    assert not hasattr(manager, "_parse_frontmatter")
    assert not hasattr(manager, "_to_frontmatter")


# ============================================================================
# TestStateManagerAtomicMethods: claim_task_atomic, claim_task_returns_existing,
# claim_task_race_condition_prevented (threading test), complete_task_atomic,
# complete_task_validates_ownership
# ============================================================================


def test_claim_task_atomic(tmp_path):
    """claim_task should atomically find, update, and save task."""
    manager = WorkflowStateStore(tmp_path)
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

    result = manager.claim_task("worker-1")
    assert result.task is not None
    assert result.task.id == "task-1"
    assert result.task.claimed_by == "worker-1"
    assert result.task.status == TaskStatus.RUNNING
    assert result.task.started_at is not None

    loaded = WorkflowStateStore(tmp_path).load()
    assert loaded is not None
    assert loaded.tasks["task-1"].claimed_by == "worker-1"
    assert loaded.tasks["task-1"].status == TaskStatus.RUNNING


def test_claim_task_returns_existing(tmp_path):
    """claim_task should return existing task for same worker_id (idempotency)."""
    manager = WorkflowStateStore(tmp_path)
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

    result = manager.claim_task("worker-1")
    assert result.task is not None
    assert result.task.id == "task-1"
    assert result.task.claimed_by == "worker-1"


def test_claim_task_renews_lease_on_retry(tmp_path):
    """claim_task should renew started_at on idempotent retry to prevent task stealing."""
    from datetime import timedelta

    manager = WorkflowStateStore(tmp_path)
    old_time = datetime.now(UTC) - timedelta(minutes=5)
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

    before_claim = datetime.now(UTC)
    result = manager.claim_task("worker-1")

    assert result.task is not None
    assert result.task.id == "task-1"
    assert result.task.claimed_by == "worker-1"
    # Critical: started_at must be renewed
    assert result.task.started_at is not None
    assert result.task.started_at >= before_claim, "Lease was not renewed on retry"


def test_claim_task_lease_renewal_prevents_stealing(tmp_path):
    """Verify that lease renewal prevents another worker from stealing a task."""
    from datetime import timedelta

    manager = WorkflowStateStore(tmp_path)
    # Task with nearly-expired lease (9 minutes old, 10 min timeout)
    nearly_expired = datetime.now(UTC) - timedelta(minutes=9)
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Task 1",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker-A",
                started_at=nearly_expired,
                timeout_seconds=600,  # 10 minutes
            ),
        }
    )
    manager.save(state)

    result_a = manager.claim_task("worker-A")
    assert result_a.task is not None
    assert result_a.task.started_at is not None
    assert result_a.task.started_at > nearly_expired, "Lease must be renewed"

    result_b = manager.claim_task("worker-B")
    assert result_b.task is None, "Worker B should not steal task after lease renewal"


def test_claim_task_race_condition_prevented(tmp_path):
    """claim_task should prevent race conditions with threading."""
    manager = WorkflowStateStore(tmp_path)
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
        result = manager.claim_task(worker_id)
        if result.task:
            results.append((worker_id, result.task.id))

    threads = []
    for i in range(5):
        t = threading.Thread(target=claim_worker, args=(f"worker-{i}",))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    claimed_tasks = [task_id for _, task_id in results]
    assert len(claimed_tasks) == len(set(claimed_tasks))  # No duplicates
    assert len(results) <= 2  # Only 2 tasks available


def test_complete_task_atomic(tmp_path):
    """complete_task should atomically update and save task."""
    manager = WorkflowStateStore(tmp_path)
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

    manager.complete_task("task-1", "worker-1")

    loaded = WorkflowStateStore(tmp_path).load()
    assert loaded is not None
    assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED
    assert loaded.tasks["task-1"].completed_at is not None


def test_complete_task_validates_ownership(tmp_path):
    """complete_task should validate worker owns the task."""
    manager = WorkflowStateStore(tmp_path)
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

    with pytest.raises(ValueError, match="Task task-1 not owned by worker-2"):
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


def test_validate_dag_detects_missing_dependency():
    """validate_dag should raise ValueError for missing dependency.

    Bug: WorkflowState.validate_dag only checks cycles, not missing deps.
    PlanDefinition.validate_dag checks both. This inconsistency could allow
    invalid state if constructed directly (not via plan import).
    """
    state = WorkflowState(
        tasks={
            "task-a": Task(
                id="task-a",
                description="Task A",
                status=TaskStatus.PENDING,
                dependencies=["task-nonexistent"],  # References missing task!
            ),
        }
    )
    with pytest.raises(ValueError, match="[Mm]issing dependency"):
        state.validate_dag()


# ============================================================================
# TestStateManagerValidatesDAG: save validates DAG (Amendment C - Part 2)
# ============================================================================


def test_state_manager_save_validates_dag(tmp_path):
    """StateManager.save should validate DAG before saving."""
    manager = WorkflowStateStore(tmp_path)

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


# ============================================================================
# TestStateManagerAutoLoad: auto-load branch coverage tests (Task 1)
# ============================================================================


def test_update_without_prior_load(tmp_path):
    """update() should auto-load state from disk if not in memory."""
    manager = WorkflowStateStore(tmp_path)
    # Create state file directly (bypassing manager.save)
    state_file = tmp_path / ".claude" / "dev-workflow-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "tasks": {
                    "task-1": {
                        "id": "task-1",
                        "description": "Test",
                        "status": "pending",
                        "dependencies": [],
                        "timeout_seconds": 600,
                    }
                }
            }
        )
    )

    # Update without calling load() first
    updated = manager.update(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Updated",
                status=TaskStatus.COMPLETED,
                dependencies=[],
            )
        }
    )
    assert updated is not None
    assert updated.tasks["task-1"].status == TaskStatus.COMPLETED


def test_update_raises_when_no_state(tmp_path):
    """update() should raise ValueError when no state file exists."""
    manager = WorkflowStateStore(tmp_path)
    with pytest.raises(ValueError, match="No workflow state"):
        manager.update(tasks={})


def test_claim_task_auto_loads_state(tmp_path):
    """claim_task() should auto-load state from disk."""
    # Create state file directly
    manager = WorkflowStateStore(tmp_path)
    state_file = tmp_path / ".claude" / "dev-workflow-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "tasks": {
                    "task-1": {
                        "id": "task-1",
                        "description": "Test",
                        "status": "pending",
                        "dependencies": [],
                        "timeout_seconds": 600,
                    }
                }
            }
        )
    )

    # Claim without calling load() - should auto-load
    result = manager.claim_task("worker-1")
    assert result.task is not None
    assert result.task.id == "task-1"


def test_complete_task_auto_loads_state(tmp_path):
    """complete_task() should auto-load state from disk."""
    manager = WorkflowStateStore(tmp_path)
    state_file = tmp_path / ".claude" / "dev-workflow-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "tasks": {
                    "task-1": {
                        "id": "task-1",
                        "description": "Test",
                        "status": "running",
                        "dependencies": [],
                        "timeout_seconds": 600,
                        "claimed_by": "worker-1",
                        "started_at": "2025-01-01T00:00:00Z",
                    }
                }
            }
        )
    )

    # Complete without calling load() - should auto-load
    manager.complete_task("task-1", "worker-1")
    loaded = WorkflowStateStore(tmp_path).load()
    assert loaded is not None
    assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED


def test_complete_task_raises_for_missing_task(tmp_path):
    """complete_task() should raise ValueError for unknown task_id."""
    manager = WorkflowStateStore(tmp_path)
    state_file = tmp_path / ".claude" / "dev-workflow-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"tasks": {}}))

    with pytest.raises(ValueError, match="Task not found: nonexistent"):
        manager.complete_task("nonexistent", "worker-1")


def test_reset_clears_state_file(tmp_path):
    """reset() should delete the state file and clear cached state."""
    manager = WorkflowStateStore(tmp_path)
    state_file = tmp_path / ".claude" / "dev-workflow-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    # Create a state with tasks
    state = WorkflowState(
        tasks={
            "task-1": Task(
                id="task-1",
                description="Test task",
                status=TaskStatus.PENDING,
                dependencies=[],
            )
        }
    )
    manager.save(state)
    assert state_file.exists()

    # Reset should delete the file
    manager.reset()
    assert not state_file.exists()

    # Subsequent load should return None
    assert manager.load() is None


def test_reset_is_idempotent(tmp_path):
    """reset() should not raise if state file doesn't exist."""
    manager = WorkflowStateStore(tmp_path)
    state_file = tmp_path / ".claude" / "dev-workflow-state.json"

    # No state file exists
    assert not state_file.exists()

    # Reset should not raise
    manager.reset()
    assert not state_file.exists()


def test_ensure_state_loaded_raises_when_file_deleted(tmp_path):
    """StateManager should use cached state even if file is deleted.

    The daemon owns the state. Once loaded, external file deletion
    should not affect operations until explicit reload.
    """
    manager = WorkflowStateStore(tmp_path)

    # Create and load state
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

    # Delete the file
    manager.state_file.unlink()

    # Should still work with cached state
    result = manager.claim_task("worker-1")
    assert result.task is not None
    assert result.task.id == "task-1"


def test_state_manager_caches_state_in_memory(tmp_path):
    """StateManager should cache state in memory, not re-read from disk on every operation.

    Bug: _ensure_state_loaded() always reads from disk, even when state is already loaded.
    Fix: Load once at save/load, return cached _state thereafter.
    """
    manager = WorkflowStateStore(tmp_path)
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

    # Delete the file after save - if caching works, claim_task should still work
    manager.state_file.unlink()

    # This should use cached state, not fail with "No state loaded"
    result = manager.claim_task("worker-1")
    assert result.task is not None, "StateManager should use cached state, not re-read from disk"


# ============================================================================
# TestDetectCycle: standalone cycle detection function (Task 3)
# ============================================================================


def test_detect_cycle_returns_none_for_acyclic_graph() -> None:
    """detect_cycle should return None for valid DAG."""
    from hyh.state import detect_cycle

    graph = {"a": ["b"], "b": ["c"], "c": []}
    assert detect_cycle(graph) is None


def test_detect_cycle_returns_cycle_node_for_cyclic_graph() -> None:
    """detect_cycle should return a node in the cycle."""
    from hyh.state import detect_cycle

    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
    result = detect_cycle(graph)
    assert result in {"a", "b", "c"}  # Any node in the cycle


def test_detect_cycle_handles_deep_graph():
    """detect_cycle should handle graphs deeper than Python's recursion limit.

    Bug: Recursive DFS fails with RecursionError for graphs >1000 nodes deep.
    Fix: Use iterative DFS with explicit stack.
    """
    import sys

    from hyh.state import detect_cycle

    # Create chain: node_0 -> node_1 -> ... -> node_1500
    depth = sys.getrecursionlimit() + 500  # Exceed default limit
    graph = {f"node_{i}": [f"node_{i + 1}"] for i in range(depth)}
    graph[f"node_{depth}"] = []  # Terminal node

    # Should NOT raise RecursionError
    result = detect_cycle(graph)
    assert result is None, "Linear chain has no cycle"


# ============================================================================
# TestClaimResult: claim_task returns atomic metadata (is_retry, is_reclaim)
# ============================================================================


def test_claim_task_returns_claim_result_with_is_retry(tmp_path) -> None:
    """claim_task should return ClaimResult with is_retry flag atomically.

    This prevents race conditions where is_retry is computed from stale state.
    """
    from hyh.state import ClaimResult

    manager = WorkflowStateStore(tmp_path)
    state = WorkflowState(
        tasks={
            "task1": Task(
                id="task1",
                description="Test task",
                status=TaskStatus.PENDING,
                dependencies=[],
            ),
        }
    )
    manager.save(state)

    # First claim - should be new claim (not retry)
    result1 = manager.claim_task("worker1")
    assert isinstance(result1, ClaimResult)
    assert result1.task is not None
    assert result1.task.id == "task1"
    assert result1.is_retry is False
    assert result1.is_reclaim is False

    # Second claim by same worker - should be retry
    result2 = manager.claim_task("worker1")
    assert isinstance(result2, ClaimResult)
    assert result2.task is not None
    assert result2.task.id == "task1"
    assert result2.is_retry is True
    assert result2.is_reclaim is False


def test_claim_task_returns_claim_result_with_is_reclaim(tmp_path) -> None:
    """claim_task should return ClaimResult with is_reclaim flag for timed out tasks."""
    from hyh.state import ClaimResult

    manager = WorkflowStateStore(tmp_path)
    # Create a timed out task
    state = WorkflowState(
        tasks={
            "task1": Task(
                id="task1",
                description="Timed out task",
                status=TaskStatus.RUNNING,
                dependencies=[],
                claimed_by="worker_old",
                started_at=datetime.now(UTC) - timedelta(seconds=700),
                timeout_seconds=600,
            ),
        }
    )
    manager.save(state)

    # Reclaim by new worker - should be reclaim
    result = manager.claim_task("worker2")
    assert isinstance(result, ClaimResult)
    assert result.task is not None
    assert result.task.id == "task1"
    assert result.task.claimed_by == "worker2"
    assert result.is_retry is False
    assert result.is_reclaim is True


def test_claim_task_returns_none_result_when_no_tasks(tmp_path) -> None:
    """claim_task should return ClaimResult with task=None when no tasks available."""
    from hyh.state import ClaimResult

    manager = WorkflowStateStore(tmp_path)
    state = WorkflowState(tasks={})
    manager.save(state)

    result = manager.claim_task("worker1")
    assert isinstance(result, ClaimResult)
    assert result.task is None
    assert result.is_retry is False
    assert result.is_reclaim is False


def test_task_extended_fields():
    """Task supports TaskPacket-like extended fields."""
    from hyh.state import Task

    task = Task(
        id="T001",
        description="Test task",
        files_in_scope=("src/a.py", "src/b.py"),
        files_out_of_scope=("src/c.py",),
        input_context="Input data",
        output_contract="Output spec",
        constraints="No new deps",
        tools=("Read", "Edit"),
        verification_commands=("pytest",),
        success_criteria="Tests pass",
        artifacts_to_read=(),
        artifacts_to_write=(".claude/artifacts/T001.md",),
        model="sonnet",
    )

    assert task.files_in_scope == ("src/a.py", "src/b.py")
    assert task.tools == ("Read", "Edit")
    assert task.model == "sonnet"
