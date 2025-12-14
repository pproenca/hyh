# tests/harness/test_state.py
"""Tests for Pydantic state models and StateManager."""
import pytest
from harness.state import WorkflowState, PendingHandoff, StateManager


def test_workflow_state_validation():
    """WorkflowState should validate and store all fields."""
    state = WorkflowState(
        workflow="subagent",
        plan="/path/to/plan.md",
        current_task=3,
        total_tasks=10,
        worktree="/path/to/worktree",
        base_sha="abc123def",
        last_commit="def456abc",
        parallel_mode=True,
    )
    assert state.current_task == 3
    assert state.workflow == "subagent"


def test_workflow_state_rejects_invalid_workflow():
    """WorkflowState should reject invalid workflow types."""
    with pytest.raises(ValueError):
        WorkflowState(
            workflow="invalid",
            plan="/path/to/plan.md",
            current_task=0,
            total_tasks=5,
            worktree="/path",
            base_sha="abc",
        )


def test_pending_handoff_model():
    """PendingHandoff model should validate mode and plan."""
    handoff = PendingHandoff(mode="sequential", plan="/path/to/plan.md")
    assert handoff.mode == "sequential"


def test_state_manager_save_and_load(tmp_path):
    """StateManager should save and load state from disk."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        workflow="execute-plan",
        plan="/path/to/plan.md",
        current_task=0,
        total_tasks=5,
        worktree=str(tmp_path),
        base_sha="abc123",
    )
    manager.save(state)

    # Load in new manager instance
    manager2 = StateManager(tmp_path)
    loaded = manager2.load()
    assert loaded is not None
    assert loaded.current_task == 0
    assert loaded.total_tasks == 5


def test_state_manager_update(tmp_path):
    """StateManager should update specific fields atomically."""
    manager = StateManager(tmp_path)
    state = WorkflowState(
        workflow="subagent",
        plan="/p.md",
        current_task=0,
        total_tasks=3,
        worktree=str(tmp_path),
        base_sha="abc",
    )
    manager.save(state)

    updated = manager.update(current_task=1, last_commit="def456")
    assert updated.current_task == 1
    assert updated.last_commit == "def456"

    # Verify persisted
    loaded = StateManager(tmp_path).load()
    assert loaded.current_task == 1
