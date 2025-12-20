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


def test_get_plan_template_returns_markdown():
    """Template generation produces Markdown with structure and example."""
    from harness.plan import get_plan_template

    template = get_plan_template()

    # Verify it's Markdown with expected sections
    assert "# Plan Template" in template
    assert "## Template Structure" in template
    assert "## Complete Example" in template
    # Verify JSON blocks are present
    assert "```json" in template
    assert '"goal":' in template
    assert '"tasks":' in template
    assert '"description":' in template
    assert '"dependencies":' in template


def test_get_plan_template_includes_all_fields():
    """Template documents all PlanTaskDefinition fields."""
    from harness.plan import get_plan_template

    template = get_plan_template()

    # All task fields should be mentioned
    assert "timeout_seconds" in template
    assert "instructions" in template
    assert "role" in template


def test_parse_markdown_plan_basic():
    """parse_markdown_plan extracts goal, tasks, and dependencies from Markdown."""
    from harness.plan import parse_markdown_plan

    content = """\
# Feature Plan

**Goal:** Implement user authentication

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2  | Core setup |
| Group 2    | 3     | Depends on Group 1 |

---

### Task 1: Create User Model

**Files:**
- Create: `src/models/user.py`

**Step 1: Define User class**
```python
class User:
    pass
```

### Task 2: Add Password Hashing

**Files:**
- Modify: `src/models/user.py`

**Step 1: Add bcrypt**
Use bcrypt for hashing.

### Task 3: Create Login Endpoint

**Files:**
- Create: `src/routes/auth.py`

**Step 1: Implement /login**
Return JWT token.
"""
    plan = parse_markdown_plan(content)

    assert plan.goal == "Implement user authentication"
    assert len(plan.tasks) == 3
    assert plan.tasks["1"].description == "Create User Model"
    assert plan.tasks["2"].description == "Add Password Hashing"
    assert plan.tasks["3"].description == "Create Login Endpoint"
    # Group 1 tasks have no dependencies
    assert plan.tasks["1"].dependencies == []
    assert plan.tasks["2"].dependencies == []
    # Group 2 tasks depend on all Group 1 tasks
    assert set(plan.tasks["3"].dependencies) == {"1", "2"}


def test_parse_markdown_plan_missing_goal():
    """parse_markdown_plan uses fallback when Goal not found."""
    from harness.plan import parse_markdown_plan

    content = """\
## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Only task |

### Task 1: Solo Task

Do something.
"""
    plan = parse_markdown_plan(content)
    assert plan.goal == "Goal not specified"
    assert len(plan.tasks) == 1


def test_parse_markdown_plan_multi_group_dependencies():
    """Tasks in Group 3 depend on Group 2, not Group 1."""
    from harness.plan import parse_markdown_plan

    content = """\
**Goal:** Three group test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | First |
| Group 2    | 2     | Second |
| Group 3    | 3     | Third |

### Task 1: First

Content 1.

### Task 2: Second

Content 2.

### Task 3: Third

Content 3.
"""
    plan = parse_markdown_plan(content)

    assert plan.tasks["1"].dependencies == []
    assert plan.tasks["2"].dependencies == ["1"]
    assert plan.tasks["3"].dependencies == ["2"]


def test_parse_markdown_plan_semantic_ids():
    """parse_markdown_plan supports semantic IDs like auth-service."""
    from harness.plan import parse_markdown_plan

    content = """\
**Goal:** Semantic ID test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | auth-service, db-migration | Core |
| Group 2    | api-endpoints | Depends on core |

### Task auth-service: Authentication Service

Set up auth.

### Task db-migration: Database Migration

Run migrations.

### Task api-endpoints: API Endpoints

Create endpoints.
"""
    plan = parse_markdown_plan(content)

    assert len(plan.tasks) == 3
    assert "auth-service" in plan.tasks
    assert "db-migration" in plan.tasks
    assert "api-endpoints" in plan.tasks
    assert plan.tasks["auth-service"].dependencies == []
    assert set(plan.tasks["api-endpoints"].dependencies) == {"auth-service", "db-migration"}


def test_parse_markdown_plan_rejects_orphan_tasks():
    """parse_markdown_plan rejects tasks not in any group."""
    import pytest

    from harness.plan import parse_markdown_plan

    content = """\
**Goal:** Orphan test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Only task 1 in group |

### Task 1: Grouped Task

In a group.

### Task 2: Orphan Task

Not in any group - should fail!
"""
    with pytest.raises(ValueError, match="Orphan tasks not in any group: 2"):
        parse_markdown_plan(content)
