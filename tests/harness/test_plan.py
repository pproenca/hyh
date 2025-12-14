"""Tests for plan JSON extraction and DAG validation."""

import pytest

from harness.plan import PlanDefinition, PlanTaskDefinition, parse_plan_content


def test_plan_task_definition_basic():
    """PlanTaskDefinition should validate required fields."""
    task = PlanTaskDefinition(description="Implement feature X")
    assert task.description == "Implement feature X"
    assert task.dependencies == []
    assert task.instructions is None
    assert task.role is None


def test_plan_task_definition_with_injection():
    """PlanTaskDefinition should accept injection fields."""
    task = PlanTaskDefinition(
        description="Build component",
        instructions="Use React hooks pattern",
        role="frontend",
    )
    assert task.instructions == "Use React hooks pattern"
    assert task.role == "frontend"


def test_parse_plan_content_extracts_json():
    """parse_plan_content should extract JSON from markdown chatter."""
    content = """
Here is the implementation plan for your feature.

I will break this down into tasks:

```json
{
  "goal": "Implement auth",
  "tasks": {
    "task-1": {"description": "Add login endpoint"},
    "task-2": {"description": "Add logout", "dependencies": ["task-1"]}
  }
}
```

Let me know if you want changes.
"""
    plan = parse_plan_content(content)
    assert plan.goal == "Implement auth"
    assert len(plan.tasks) == 2
    assert plan.tasks["task-2"].dependencies == ["task-1"]


def test_parse_plan_content_no_json_raises():
    """parse_plan_content should raise if no JSON block found."""
    content = "Here is the plan: do task 1, then task 2."
    with pytest.raises(ValueError, match="[Nn]o JSON"):
        parse_plan_content(content)


def test_parse_plan_content_invalid_json_raises():
    """parse_plan_content should raise for malformed JSON."""
    content = """
```json
{goal: "broken}
```
"""
    with pytest.raises(ValueError, match="[Ii]nvalid JSON"):
        parse_plan_content(content)


def test_plan_validate_dag_rejects_cycle():
    """validate_dag should reject A -> B -> A cycles."""
    plan = PlanDefinition(
        goal="Test",
        tasks={
            "a": PlanTaskDefinition(description="A", dependencies=["b"]),
            "b": PlanTaskDefinition(description="B", dependencies=["a"]),
        },
    )
    with pytest.raises(ValueError, match="[Cc]ycle"):
        plan.validate_dag()


def test_plan_validate_dag_rejects_missing_dep():
    """validate_dag should reject references to non-existent tasks."""
    plan = PlanDefinition(
        goal="Test",
        tasks={
            "a": PlanTaskDefinition(description="A", dependencies=["ghost"]),
        },
    )
    with pytest.raises(ValueError, match="[Mm]issing"):
        plan.validate_dag()
