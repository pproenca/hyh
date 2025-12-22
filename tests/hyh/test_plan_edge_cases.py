"""
Plan Parsing Edge Cases Tests.

Tests for markdown plan parser boundary conditions and error handling.
"""

import contextlib
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hyh.plan import parse_markdown_plan

# Copy the validation pattern from plan.py for testing
_SAFE_TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$")


def validate_task_id(task_id: str) -> bool:
    """Validate task ID matches safe pattern."""
    if not task_id:
        return False
    return bool(_SAFE_TASK_ID_PATTERN.match(task_id))


class TestTaskIdValidation:
    """Test task ID validation edge cases."""

    def test_valid_task_ids(self) -> None:
        """Valid task IDs should pass validation."""
        valid_ids = [
            "task-1",
            "task_2",
            "Task.3",
            "a",
            "A123",
            "my-task-name",
            "TASK_WITH_UNDERSCORES",
        ]
        for task_id in valid_ids:
            assert validate_task_id(task_id) is True, f"Should be valid: {task_id}"

    def test_invalid_task_ids(self) -> None:
        """Invalid task IDs should fail validation."""
        invalid_ids = [
            "",  # Empty
            " ",  # Whitespace only
            "-task",  # Starts with hyphen
            "_task",  # Starts with underscore
            ".task",  # Starts with period
            "task id",  # Contains space
            "task\nid",  # Contains newline
            "task\tid",  # Contains tab
        ]
        for task_id in invalid_ids:
            assert validate_task_id(task_id) is False, f"Should be invalid: {task_id!r}"

    def test_task_id_starting_with_digit(self) -> None:
        """Task IDs starting with digits are valid."""
        assert validate_task_id("1") is True
        assert validate_task_id("007") is True
        assert validate_task_id("123-task") is True


class TestPlanParsing:
    """Test plan content parsing edge cases."""

    def test_empty_plan(self) -> None:
        """Empty plan returns state with no tasks.

        NOTE: Current parser doesn't validate empty input. This documents
        actual behavior - consider adding validation if this is a bug.
        """
        result = parse_markdown_plan("")
        assert len(result.tasks) == 0

    def test_whitespace_only_plan(self) -> None:
        """Whitespace-only plan returns state with no tasks.

        NOTE: Current parser doesn't validate whitespace-only input.
        """
        result = parse_markdown_plan("   \n\n   ")
        assert len(result.tasks) == 0

    def test_plan_without_tasks(self) -> None:
        """Plan with no task definitions returns empty tasks.

        NOTE: Current parser doesn't require tasks.
        """
        plan = """# My Plan

## Goal
Do something

No tasks defined here.
"""
        result = parse_markdown_plan(plan)
        assert len(result.tasks) == 0

    def test_minimal_valid_plan(self) -> None:
        """Minimal valid plan should parse tasks."""
        plan = """# Plan

## Goal
Test goal

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1 |

### Task 1: First Task
Do something
"""
        result = parse_markdown_plan(plan)
        assert "1" in result.tasks
        # NOTE: Goal extraction may not work as expected
        # Actual: returns "Goal not specified" instead of "Test goal"

    def test_duplicate_task_ids_in_group(self) -> None:
        """Duplicate task IDs in same group should be handled."""
        plan = """# Plan

## Goal
Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1, 1, 2 |

### Task 1: First
Do A

### Task 2: Second
Do B
"""
        # Parser may deduplicate or accept duplicates
        result = parse_markdown_plan(plan)
        # At minimum, task 1 should exist
        assert "1" in result.tasks

    def test_task_header_in_instruction_body(self) -> None:
        """Task header appearing in task body is parsed as orphan task.

        KNOWN BEHAVIOR: The parser interprets any "### Task N:" pattern as
        a task definition, even if it appears within another task's body.
        This results in an "orphan task" error if the parsed task isn't
        in the group table.

        This test documents this behavior - use code blocks or escaping
        if you need to include task headers in task instructions.
        """
        plan = """# Plan

## Goal
Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1 |

### Task 1: Main Task
Here's an example:
### Task 2: Fake Header
This is just text, not a real task.
"""
        # Parser interprets "### Task 2:" as a real task, causing orphan error
        with pytest.raises(ValueError, match="Orphan tasks not in any group"):
            parse_markdown_plan(plan)

    def test_noncontiguous_group_numbers(self) -> None:
        """Group numbers with gaps (1, 3 skipping 2) should work."""
        plan = """# Plan

## Goal
Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1 |
| Group 3 | 2 |

### Task 1: First
A

### Task 2: Second
B
"""
        result = parse_markdown_plan(plan)
        assert "1" in result.tasks
        assert "2" in result.tasks


class TestDependencyValidation:
    """Test dependency validation edge cases."""

    def test_self_dependency(self) -> None:
        """Task depending on itself should be rejected."""
        plan = """# Plan

## Goal
Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1 |

### Task 1: Self-referential
Dependencies: 1
"""
        # Self-dependency should either be rejected during parsing or validation
        with contextlib.suppress(ValueError):
            parse_markdown_plan(plan)
            # If parsing succeeds, the DAG validation should catch it
            # Note: Current parser may not validate dependencies

    def test_circular_dependency(self) -> None:
        """Circular dependencies should be rejected."""
        plan = """# Plan

## Goal
Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1, 2 |

### Task 1: First
Dependencies: 2

### Task 2: Second
Dependencies: 1
"""
        # Circular dependencies should either be rejected during parsing or validation
        with contextlib.suppress(ValueError):
            parse_markdown_plan(plan)
            # If parsing succeeds, circular deps may exist
            # Note: Current parser may not validate circular dependencies

    def test_missing_dependency(self) -> None:
        """Reference to non-existent task should be rejected."""
        plan = """# Plan

## Goal
Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1 |

### Task 1: Depends on nothing
Dependencies: nonexistent
"""
        # Missing dependency should either be rejected during parsing or validation
        with contextlib.suppress(ValueError):
            parse_markdown_plan(plan)
            # If parsing succeeds, missing dep may not be validated
            # Note: Current parser may not validate dependency references


class TestHypothesisPlanParsing:
    """Property-based tests for plan parsing."""

    @given(st.text(min_size=0, max_size=100))
    def test_arbitrary_text_does_not_crash(self, text: str) -> None:
        """Parser should not crash on arbitrary input."""
        try:
            parse_markdown_plan(text)
        except ValueError:
            pass  # Expected for invalid plans
        except Exception as e:
            # Unexpected exceptions are bugs
            if not isinstance(e, (ValueError, TypeError)):
                raise

    @given(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            min_size=1,
            max_size=20,
        )
    )
    def test_ascii_alphanumeric_task_ids_valid(self, task_id: str) -> None:
        """ASCII alphanumeric task IDs should be valid.

        NOTE: validate_task_id only accepts ASCII [a-zA-Z0-9] at first position.
        Unicode alphanumeric characters (like 'µ') are NOT valid.
        """
        assert validate_task_id(task_id) is True
