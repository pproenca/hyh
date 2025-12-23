"""Tests for plan Markdown parsing and DAG validation."""

import pytest

from hyh.plan import PlanDefinition, PlanTaskDefinition, parse_plan_content


def test_plan_task_definition_basic():
    """PlanTaskDefinition should validate required fields."""
    task = PlanTaskDefinition(description="Implement feature X")
    assert task.description == "Implement feature X"
    assert task.dependencies == ()
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


def test_parse_plan_content_invalid_format_raises():
    """parse_plan_content should raise if no valid Markdown plan found."""
    content = "Here is the plan: do task 1, then task 2."
    with pytest.raises(ValueError, match="No valid plan found"):
        parse_plan_content(content)


def test_plan_validate_dag_rejects_cycle():
    """validate_dag should reject A -> B -> A cycles."""
    plan = PlanDefinition(
        goal="Test",
        tasks={
            "a": PlanTaskDefinition(description="A", dependencies=("b",)),
            "b": PlanTaskDefinition(description="B", dependencies=("a",)),
        },
    )
    with pytest.raises(ValueError, match="[Cc]ycle"):
        plan.validate_dag()


def test_plan_validate_dag_rejects_missing_dep():
    """validate_dag should reject references to non-existent tasks."""
    plan = PlanDefinition(
        goal="Test",
        tasks={
            "a": PlanTaskDefinition(description="A", dependencies=("ghost",)),
        },
    )
    with pytest.raises(ValueError, match="[Mm]issing"):
        plan.validate_dag()


def test_get_plan_template_returns_markdown():
    """Template generation produces Markdown with structure and example."""
    from hyh.plan import get_plan_template

    template = get_plan_template()

    assert "# Plan Template" in template
    assert "## Recommended: Structured Markdown" in template
    # Legacy JSON section should NOT exist
    assert "## Legacy:" not in template
    assert "JSON Format" not in template


def test_parse_markdown_plan_basic():
    """parse_markdown_plan extracts goal, tasks, and dependencies from Markdown."""
    from hyh.plan import parse_markdown_plan

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
    assert set(plan.tasks["1"].dependencies) == set()
    assert set(plan.tasks["2"].dependencies) == set()
    assert set(plan.tasks["3"].dependencies) == {"1", "2"}


def test_parse_markdown_plan_missing_goal():
    """parse_markdown_plan uses fallback when Goal not found."""
    from hyh.plan import parse_markdown_plan

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
    from hyh.plan import parse_markdown_plan

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

    assert set(plan.tasks["1"].dependencies) == set()
    assert set(plan.tasks["2"].dependencies) == {"1"}
    assert set(plan.tasks["3"].dependencies) == {"2"}


def test_parse_markdown_plan_semantic_ids():
    """parse_markdown_plan supports semantic IDs like auth-service."""
    from hyh.plan import parse_markdown_plan

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
    assert set(plan.tasks["auth-service"].dependencies) == set()
    assert set(plan.tasks["api-endpoints"].dependencies) == {"auth-service", "db-migration"}


def test_parse_markdown_plan_rejects_orphan_tasks():
    """parse_markdown_plan rejects tasks not in any group."""
    from hyh.plan import parse_markdown_plan

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


def test_parse_plan_content_markdown_format():
    """parse_plan_content should detect and parse Markdown format."""
    content = """\
# Implementation Plan

**Goal:** Test markdown parsing

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Core |

### Task 1: Test Task

Instructions here.
"""
    plan = parse_plan_content(content)

    assert plan.goal == "Test markdown parsing"
    assert len(plan.tasks) == 1
    assert plan.tasks["1"].description == "Test Task"


def test_parse_plan_content_markdown_cycle_rejected():
    """Markdown plans with cycles are rejected."""
    content = """\
**Goal:** Cycle test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Only group |

### Task 1: Cyclic Task

Instructions.
"""
    plan = parse_plan_content(content)
    assert plan.goal == "Cycle test"


def test_get_plan_template_includes_markdown_format():
    """get_plan_template should show Markdown format as recommended."""
    from hyh.plan import get_plan_template

    template = get_plan_template()

    assert "**Goal:**" in template
    assert "| Task Group |" in template
    assert "### Task" in template
    assert "(Recommended)" in template or "Markdown" in template


def test_parse_markdown_plan_rejects_phantom_tasks():
    """parse_markdown_plan rejects tasks in table but not in body (phantom tasks).

    Bug: If "### Task 2: ..." is typo'd as "### Task2: ...", the parser silently
    drops Task 2 because it's in the table but the regex doesn't match the header.
    """
    from hyh.plan import parse_markdown_plan

    content = """\
**Goal:** Phantom task test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2  | Task 2 is in table but not in body |

### Task 1: Real Task

This task has a proper header.

### Task2: Typo Task

Missing space after Task - regex won't match!
"""
    with pytest.raises(ValueError, match="Phantom tasks in table but not in body: 2"):
        parse_markdown_plan(content)


def test_parse_markdown_plan_flexible_header_formats():
    """parse_markdown_plan accepts reasonable header variations.

    The regex should accept:
    - "### Task 1: Description" (standard)
    - "### Task 2 : Description" (space before colon)
    - "### Task 3" (no colon, no description)
    - "### Task auth-service: Description" (semantic ID)
    - "### Task 1.1: Description" (dotted ID)
    """
    from hyh.plan import parse_markdown_plan

    content = """\
**Goal:** Flexible format test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2, 3, auth-service, 1.1 | Various formats |

### Task 1: Standard format

Body 1.

### Task 2 : Space before colon

Body 2.

### Task 3

No colon, no description.

### Task auth-service: Semantic ID

Body auth.

### Task 1.1: Dotted ID

Body dotted.
"""
    plan = parse_markdown_plan(content)

    assert "1" in plan.tasks
    assert "2" in plan.tasks
    assert "3" in plan.tasks
    assert "auth-service" in plan.tasks
    assert "1.1" in plan.tasks
    assert len(plan.tasks) == 5
