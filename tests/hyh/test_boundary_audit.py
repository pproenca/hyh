"""
Red Team Security Audit: Edge Cases and Boundary Vulnerabilities.

These tests target edge cases, bounds checking, and error recovery.

Tests focus on:
- Empty and null input handling
- Boundary value testing
- Resource limits
- Error handling edge cases
"""

import json
import tempfile
from pathlib import Path

import msgspec
import pytest

from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore


class TestEmptyInputHandling:
    """Edge cases with empty or minimal inputs."""

    def test_empty_task_id_rejected(self) -> None:
        """Empty task ID should be rejected."""
        with pytest.raises((ValueError, TypeError)):
            Task(id="", description="x", status=TaskStatus.PENDING, dependencies=[])

    def test_whitespace_task_id_rejected(self) -> None:
        """Whitespace-only task ID should be rejected."""
        with pytest.raises((ValueError, TypeError)):
            Task(id="   ", description="x", status=TaskStatus.PENDING, dependencies=[])

    def test_empty_worker_id_rejected(self) -> None:
        """Empty worker ID should be rejected by claim_task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))
            state = WorkflowState(
                tasks={
                    "t1": Task(id="t1", description="x", status=TaskStatus.PENDING, dependencies=[])
                }
            )
            manager.save(state)

            with pytest.raises(ValueError, match="[Ww]orker"):
                manager.claim_task("")

    def test_whitespace_worker_id_rejected(self) -> None:
        """Whitespace-only worker ID should be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))
            state = WorkflowState(
                tasks={
                    "t1": Task(id="t1", description="x", status=TaskStatus.PENDING, dependencies=[])
                }
            )
            manager.save(state)

            with pytest.raises(ValueError, match="[Ww]orker"):
                manager.claim_task("   ")


class TestPlanEmptyInput:
    """Edge cases for plan parsing."""

    def test_empty_plan_content_raises(self) -> None:
        """Empty plan content should raise clear error."""
        from hyh.plan import parse_plan_content

        with pytest.raises(ValueError, match="[Nn]o valid|[Ee]mpty|[Nn]o plan"):
            parse_plan_content("")

    def test_whitespace_only_plan_raises(self) -> None:
        """Whitespace-only plan should raise clear error."""
        from hyh.plan import parse_plan_content

        with pytest.raises(ValueError, match="[Nn]o valid|[Ee]mpty|[Nn]o plan"):
            parse_plan_content("   \n\t\n   ")

    def test_plan_without_tasks_raises(self) -> None:
        """Plan with header but no tasks should raise."""
        from hyh.plan import parse_plan_content

        # Plan with Goal and Task Group table but no actual tasks
        minimal_plan = """\
# Plan

**Goal:** Nothing

| Task Group | Tasks |
|------------|-------|

No tasks defined.
"""
        with pytest.raises(ValueError, match="[Nn]o.*task|[Ee]mpty|[Nn]o valid"):
            parse_plan_content(minimal_plan)


class TestNullAndNoneHandling:
    """Edge cases with None/null values.

    msgspec philosophy: Trust internal code, validate external data.
    Type validation happens during decode (msgspec.convert), not construction.
    These tests verify validation at the decode boundary.
    """

    def test_none_dependencies_rejected_on_decode(self) -> None:
        """Task with null dependencies in JSON should be rejected during decode."""
        # msgspec validates types during decode, not construction
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": "t1", "description": "x", "dependencies": None},
                Task,
            )

    def test_none_in_dependencies_list_rejected_on_decode(self) -> None:
        """Task with None inside dependencies list rejected during decode."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": "t1", "description": "x", "dependencies": [None, "t2"]},
                Task,
            )

    def test_none_description_rejected_on_decode(self) -> None:
        """Task with null description in JSON should be rejected during decode."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": "t1", "description": None, "dependencies": []},
                Task,
            )


class TestTrajectoryTailBounds:
    """Bounds checking for trajectory tail()."""

    def test_tail_negative_n(self) -> None:
        """tail(n=-1) should handle gracefully."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TrajectoryLogger(Path(tmpdir) / "traj.jsonl")
            logger.log({"event": 1})
            logger.log({"event": 2})

            # Negative n should return empty or all events
            result = logger.tail(-1)
            # Either empty or treats as 0 - both acceptable
            assert isinstance(result, list)

    def test_tail_zero_n(self) -> None:
        """tail(n=0) should return empty list."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TrajectoryLogger(Path(tmpdir) / "traj.jsonl")
            logger.log({"event": 1})

            result = logger.tail(0)
            assert result == []

    def test_tail_huge_n(self) -> None:
        """tail(n=10**9) should not hang or OOM."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TrajectoryLogger(Path(tmpdir) / "traj.jsonl")
            logger.log({"event": 1})

            # Should return all available events (just 1)
            result = logger.tail(10**9)
            assert len(result) == 1

    def test_tail_nonexistent_file(self) -> None:
        """tail() on nonexistent file should return empty."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TrajectoryLogger(Path(tmpdir) / "nonexistent.jsonl")
            result = logger.tail(10)
            assert result == []


class TestTimeoutEdgeCases:
    """Timeout-related edge cases.

    msgspec uses Annotated[int, Meta(ge=1, le=86400)] for constraint validation.
    Constraints are enforced during decode, not construction.
    """

    def test_zero_timeout_rejected_on_decode(self) -> None:
        """Task with timeout_seconds=0 should be rejected during decode (minimum is 1)."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": "t1", "description": "x", "timeout_seconds": 0},
                Task,
            )

    def test_negative_timeout_rejected_on_decode(self) -> None:
        """Negative timeout should be rejected during decode."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": "t1", "description": "x", "timeout_seconds": -1},
                Task,
            )

    def test_huge_timeout_rejected_on_decode(self) -> None:
        """Very large timeout exceeding max (86400) should be rejected during decode."""
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"id": "t1", "description": "x", "timeout_seconds": 10**15},
                Task,
            )


class TestBoundaryValues:
    """Boundary value testing."""

    def test_max_task_id_length(self) -> None:
        """Very long task IDs should be handled."""
        long_id = "a" * 10000
        task = Task(id=long_id, description="x", status=TaskStatus.PENDING, dependencies=[])
        assert task.id == long_id

    def test_max_description_length(self) -> None:
        """Very long descriptions should be handled."""
        long_desc = "x" * 100000
        task = Task(id="t1", description=long_desc, status=TaskStatus.PENDING, dependencies=[])
        assert task.description == long_desc

    def test_max_dependencies_count(self) -> None:
        """Task with many dependencies should be handled."""

        tasks: dict[str, Task] = {}
        for i in range(1000):
            tasks[f"task-{i}"] = Task(
                id=f"task-{i}",
                description=f"Task {i}",
                status=TaskStatus.COMPLETED,
                dependencies=[],
            )

        deps = list(tasks.keys())
        tasks["final"] = Task(
            id="final",
            description="Final task",
            status=TaskStatus.PENDING,
            dependencies=deps,
        )

        state = WorkflowState(tasks=tasks)

        claimable = state.get_claimable_task()
        assert claimable is not None
        assert claimable.id == "final"

    def test_deep_dependency_chain(self) -> None:
        """Very deep dependency chain should not cause stack overflow."""
        # Create chain: task-0 <- task-1 <- ... <- task-999
        depth = 1000
        tasks: dict[str, Task] = {}

        tasks["task-0"] = Task(
            id="task-0",
            description="Task 0",
            status=TaskStatus.COMPLETED,
            dependencies=[],
        )

        for i in range(1, depth):
            tasks[f"task-{i}"] = Task(
                id=f"task-{i}",
                description=f"Task {i}",
                status=TaskStatus.COMPLETED if i < depth - 1 else TaskStatus.PENDING,
                dependencies=[f"task-{i - 1}"],
            )

        state = WorkflowState(tasks=tasks)

        # Should handle without RecursionError
        claimable = state.get_claimable_task()
        assert claimable is not None
        assert claimable.id == f"task-{depth - 1}"


class TestSpecialCharacters:
    """Special character handling."""

    def test_unicode_in_task_description(self) -> None:
        """Unicode characters in descriptions should be handled."""
        unicode_desc = "日本語テスト 🚀 émojis and ñ"
        task = Task(id="t1", description=unicode_desc, status=TaskStatus.PENDING, dependencies=[])
        assert task.description == unicode_desc

    def test_json_special_chars_round_trip(self) -> None:
        """JSON special characters should be properly escaped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            tricky_desc = 'Quote: "test", backslash: \\, newline: \n, tab: \t'
            state = WorkflowState(
                tasks={
                    "t1": Task(
                        id="t1",
                        description=tricky_desc,
                        status=TaskStatus.PENDING,
                        dependencies=[],
                    )
                }
            )
            manager.save(state)

            loaded = manager.load()
            assert loaded is not None
            assert loaded.tasks["t1"].description == tricky_desc


class TestEmptyCommandValidation:
    """Tests for empty command validation in daemon."""

    def test_empty_list_args_check(self) -> None:
        """Document that if not args check catches empty list."""
        args: list[str] = []
        assert not args

    def test_empty_string_args_bypasses_check(self) -> None:
        """Document that [""] bypasses if not args check.

        This is a bug: [""] contains one element (empty string),
        so bool([""]) is True, and "if not args" passes.
        """
        args: list[str] = [""]
        passes_not_check = not args  # False - [""] is truthy

        assert not passes_not_check, "Empty string list passes 'if not args' check"

        # Proper validation should also check first element
        proper_check = not args or not args[0]
        assert proper_check, "Proper validation catches empty command"


class TestResourceLimits:
    """Resource limit testing."""

    def test_trajectory_large_file_tail_efficiency(self) -> None:
        """Trajectory tail should be efficient on large files."""
        import time

        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TrajectoryLogger(Path(tmpdir) / "traj.jsonl")

            for i in range(1000):
                logger.log({"event": i, "data": "x" * 100})

            start = time.monotonic()
            result = logger.tail(10)
            elapsed = time.monotonic() - start

            assert len(result) == 10
            assert elapsed < 0.5, f"tail took {elapsed}s, should be <0.5s"


class TestErrorRecovery:
    """Error recovery edge cases."""

    def test_partial_json_in_state_file(self) -> None:
        """StateManager should handle partial/corrupt JSON gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))
            state_file = manager.state_file
            state_file.parent.mkdir(parents=True, exist_ok=True)

            state_file.write_text('{"tasks": {"t1":')

            with pytest.raises((json.JSONDecodeError, ValueError)):
                manager.load()

    def test_invalid_status_in_state_file(self) -> None:
        """Invalid status values should raise clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))
            state_file = manager.state_file
            state_file.parent.mkdir(parents=True, exist_ok=True)

            state_file.write_text(
                json.dumps(
                    {
                        "tasks": {
                            "t1": {
                                "id": "t1",
                                "description": "x",
                                "status": "invalid_status",
                                "dependencies": [],
                                "timeout_seconds": 600,
                            }
                        }
                    }
                )
            )

            with pytest.raises((ValueError, TypeError, msgspec.ValidationError)):
                manager.load()

    def test_missing_required_fields(self) -> None:
        """Missing required fields should raise clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))
            state_file = manager.state_file
            state_file.parent.mkdir(parents=True, exist_ok=True)

            state_file.write_text(
                json.dumps(
                    {
                        "tasks": {
                            "t1": {
                                # Missing 'id': implicitly described by context
                                "description": "x",
                                "status": "pending",
                                "dependencies": [],
                            }
                        }
                    }
                )
            )

            with pytest.raises((ValueError, TypeError, KeyError, msgspec.ValidationError)):
                manager.load()
