# tests/hyh/test_state_coverage.py
"""
Additional tests to increase coverage for state.py edge cases.

These tests target specific uncovered branches identified by coverage analysis.
"""

from datetime import UTC, datetime, timedelta

import msgspec
import pytest

from hyh.state import (
    Task,
    TaskStatus,
    WorkflowState,
    detect_cycle,
)


class TestDetectCycleEdgeCases:
    """Cover edge cases in detect_cycle function."""

    def test_cycle_detected_on_gray_node_revisit(self) -> None:
        """Should detect cycle when DFS revisits a gray (in-progress) node."""
        # A → B → C → A (cycle back to A while A is still gray)
        graph = {
            "A": ["B"],
            "B": ["C"],
            "C": ["A"],  # Back edge to A
        }
        result = detect_cycle(graph)
        assert result is not None
        assert result in ("A", "B", "C")  # Any node in cycle is valid

    def test_skip_black_node_no_revisit(self) -> None:
        """Should skip already-processed (black) nodes efficiently."""
        # Diamond pattern: A → B, A → C, B → D, C → D
        # D should only be visited once
        graph = {
            "A": ["B", "C"],
            "B": ["D"],
            "C": ["D"],
            "D": [],
        }
        result = detect_cycle(graph)
        assert result is None  # No cycle

    def test_edge_to_nonexistent_node(self) -> None:
        """Should handle edges to nodes not in graph keys.

        Edges to external nodes (not in graph.keys()) are treated as leaves.
        """
        graph: dict[str, list[str]] = {
            "A": ["B"],
            # B is not a key - treated as external leaf node
        }
        # Should not crash - B is treated as a leaf with no outgoing edges
        result = detect_cycle(graph)
        assert result is None  # No cycle


class TestTaskValidatorEdgeCases:
    """Cover edge cases in Task field validators.

    msgspec philosophy: Trust internal code, validate external data.
    Type validation occurs during decode (msgspec.convert), not construction.
    """

    def test_non_string_task_id_rejected_on_decode(self) -> None:
        """Task ID must be string - validated during decode."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": 123, "description": "test"},
                Task,
            )

    def test_dependencies_as_tuple_passthrough(self) -> None:
        """Dependencies passed as tuple should work directly."""
        task = Task(
            id="task-1",
            description="test",
            status=TaskStatus.PENDING,
            dependencies=("dep-1", "dep-2"),
        )
        assert task.dependencies == ("dep-1", "dep-2")

    def test_dependencies_invalid_type_rejected_on_decode(self) -> None:
        """Dependencies as dict rejected during decode."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": "task-1", "description": "test", "dependencies": {"a": 1}},
                Task,
            )


class TestWorkflowStateListInput:
    """Cover validate_tasks_input list parsing branches."""

    def test_tasks_list_with_task_objects(self) -> None:
        """WorkflowState should accept list of Task objects."""
        task1 = Task(id="task-1", description="First", status=TaskStatus.PENDING, dependencies=[])
        task2 = Task(id="task-2", description="Second", status=TaskStatus.PENDING, dependencies=[])

        state = WorkflowState(tasks=[task1, task2])  # type: ignore[arg-type]

        assert "task-1" in state.tasks
        assert "task-2" in state.tasks

    def test_tasks_list_with_dicts_with_id(self) -> None:
        """WorkflowState should accept list of dicts with 'id' field."""
        state = WorkflowState(
            tasks=[  # type: ignore[arg-type]
                {"id": "task-1", "description": "First", "status": "pending", "dependencies": []},
                {"id": "task-2", "description": "Second", "status": "pending", "dependencies": []},
            ]
        )

        assert "task-1" in state.tasks
        assert "task-2" in state.tasks

    def test_tasks_list_with_dict_missing_id_raises(self) -> None:
        """Dict in tasks list without 'id' field should raise ValueError."""
        with pytest.raises(ValueError, match="Task dict must contain 'id' field"):
            WorkflowState(
                tasks=[{"description": "No ID", "status": "pending", "dependencies": []}]  # type: ignore[arg-type]
            )

    def test_tasks_list_with_invalid_type_raises(self) -> None:
        """Invalid type in tasks list should raise TypeError."""
        with pytest.raises(TypeError, match="Invalid task type"):
            WorkflowState(tasks=[123, 456])  # type: ignore[arg-type]


class TestGetClaimableTaskEdgeCases:
    """Cover edge cases in get_claimable_task."""

    def test_all_pending_tasks_blocked_returns_none(self) -> None:
        """When all pending tasks have unsatisfied deps, should return None."""
        state = WorkflowState(
            tasks={
                "task-1": Task(
                    id="task-1",
                    description="Task 1",
                    status=TaskStatus.RUNNING,  # Blocking task-2 and task-3
                    dependencies=[],
                    claimed_by="worker-1",
                    started_at=datetime.now(UTC),
                ),
                "task-2": Task(
                    id="task-2",
                    description="Task 2",
                    status=TaskStatus.PENDING,
                    dependencies=["task-1"],  # Blocked
                ),
                "task-3": Task(
                    id="task-3",
                    description="Task 3",
                    status=TaskStatus.PENDING,
                    dependencies=["task-1"],  # Blocked
                ),
            }
        )

        result = state.get_claimable_task()
        assert result is None  # No claimable tasks


class TestStateManagerUpdateEdgeCases:
    """Cover edge cases in StateManager.update."""

    def test_update_with_task_objects(self, tmp_path) -> None:
        """update() should accept Task objects in tasks dict."""
        from hyh.state import WorkflowStateStore

        manager = WorkflowStateStore(tmp_path)
        manager.save(
            WorkflowState(
                tasks={
                    "task-1": Task(
                        id="task-1",
                        description="Original",
                        status=TaskStatus.PENDING,
                        dependencies=[],
                    )
                }
            )
        )

        # Update using Task object directly
        new_task = Task(
            id="task-1",
            description="Updated",
            status=TaskStatus.COMPLETED,
            dependencies=[],
        )
        updated = manager.update(tasks={"task-1": new_task})

        assert updated.tasks["task-1"].description == "Updated"
        assert updated.tasks["task-1"].status == TaskStatus.COMPLETED


class TestTimeoutEdgeCasesExtended:
    """Extended timeout edge cases for is_timed_out."""

    def test_min_timeout_boundary(self) -> None:
        """Task with timeout_seconds=1 should timeout after 1 second."""
        task = Task(
            id="task-1",
            description="Minimal timeout",
            status=TaskStatus.RUNNING,
            dependencies=[],
            started_at=datetime.now(UTC) - timedelta(seconds=2),
            timeout_seconds=1,
        )
        assert task.is_timed_out() is True

    def test_max_timeout_boundary(self) -> None:
        """Task with timeout_seconds=86400 (24h) should not timeout immediately."""
        task = Task(
            id="task-1",
            description="Max timeout",
            status=TaskStatus.RUNNING,
            dependencies=[],
            started_at=datetime.now(UTC),
            timeout_seconds=86400,
        )
        assert task.is_timed_out() is False
