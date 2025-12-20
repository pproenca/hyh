# Markdown Plan Parser Implementation

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-20-markdown-plan-parser.md` to implement task-by-task.

**Goal:** Add native Markdown plan parsing to `src/harness/plan.py` with Task Groups table for dependency inference, while preserving JSON fallback for backward compatibility.

**Architecture:** Dual-mode parser in `parse_plan_content()` - detect Markdown signature first, fall back to JSON. New `parse_markdown_plan()` function handles extraction of goal, task groups, and task content.

**Tech Stack:** Python 3.13t, Pydantic, regex

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Core parser function with unit tests |
| Group 2    | 2     | Integration into main entry point |
| Group 3    | 3     | Update template to show new format |

---

### Task 1: Implement `parse_markdown_plan()` Function

**Files:**
- Modify: `src/harness/plan.py:64` (insert new function before `parse_plan_content`)
- Test: `tests/harness/test_plan.py`

**Step 1: Write failing test for basic Markdown plan parsing** (2-5 min)

Add this test to `tests/harness/test_plan.py`:

```python
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
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_basic -v
```

Expected: FAIL with `ImportError: cannot import name 'parse_markdown_plan'`

**Step 3: Implement `parse_markdown_plan()` function** (5 min)

Insert this function in `src/harness/plan.py` before `parse_plan_content` (around line 64):

```python
def parse_markdown_plan(content: str) -> PlanDefinition:
    """Parse structured Markdown plan format.

    Extracts:
    1. Goal from `**Goal:** <text>`
    2. Task groups from `| Group N | task_ids |` table rows
    3. Task definitions from `### Task <ID>: <Description>` headers

    Dependencies: Tasks in Group N depend on ALL tasks in Group N-1.
    """
    # 1. Extract Goal
    goal_match = re.search(r"\*\*Goal:\*\*\s*(.+)", content)
    goal = goal_match.group(1).strip() if goal_match else "Goal not specified"

    # 2. Extract Task Groups (for dependency calculation)
    # Pattern: | Group 1 | task-1, auth-service | ... (captures group number and task list)
    # Supports semantic IDs: alphanumeric, dashes, underscores
    group_pattern = r"\|\s*Group\s*(\d+)\s*\|\s*([\w\-,\s]+)\s*\|"
    groups: dict[int, list[str]] = {}

    for match in re.finditer(group_pattern, content):
        group_id = int(match.group(1))
        task_ids = [t.strip() for t in match.group(2).split(",") if t.strip()]
        groups[group_id] = task_ids

    # 3. Extract Task Content
    # Split by "### Task <ID>: <Description>" headers
    # Supports semantic IDs: "Task 1", "Task auth-service", "Task db_migration"
    task_pattern = r"^### Task ([\w\-]+):\s*(.+)$"
    parts = re.split(task_pattern, content, flags=re.MULTILINE)

    # parts[0] is preamble. Then groups of 3: [id, desc, body, id, desc, body, ...]
    tasks_data: dict[str, dict[str, str | list[str]]] = {}

    for i in range(1, len(parts), 3):
        if i + 2 > len(parts):
            break
        t_id = parts[i].strip()
        t_desc = parts[i + 1].strip()
        t_body = parts[i + 2].strip()

        tasks_data[t_id] = {
            "description": t_desc,
            "instructions": t_body,
            "dependencies": [],
        }

    # 4. Calculate Dependencies based on Groups
    # Group N depends on all tasks from Group N-1
    sorted_group_ids = sorted(groups.keys())
    for i, group_id in enumerate(sorted_group_ids):
        if i > 0:
            prev_group_id = sorted_group_ids[i - 1]
            prev_tasks = groups[prev_group_id]

            for t_id in groups[group_id]:
                if t_id in tasks_data:
                    tasks_data[t_id]["dependencies"] = prev_tasks

    # 5. Validate: All tasks must be in a group (no orphans)
    all_grouped_tasks = {t for tasks in groups.values() for t in tasks}
    orphan_tasks = set(tasks_data.keys()) - all_grouped_tasks
    if orphan_tasks:
        raise ValueError(
            f"Orphan tasks not in any group: {', '.join(sorted(orphan_tasks))}. "
            "Add them to the Task Groups table."
        )

    # 6. Construct PlanDefinition
    final_tasks = {}
    for t_id, t_data in tasks_data.items():
        deps = t_data["dependencies"]
        final_tasks[t_id] = PlanTaskDefinition(
            description=str(t_data["description"]),
            instructions=str(t_data["instructions"]),
            dependencies=deps if isinstance(deps, list) else [],
            timeout_seconds=600,
            role=None,
        )

    return PlanDefinition(goal=goal, tasks=final_tasks)
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_basic -v
```

Expected: PASS (1 passed)

**Step 5: Write test for missing goal fallback** (2 min)

```python
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
```

**Step 6: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_missing_goal -v
```

Expected: PASS

**Step 7: Write test for multi-group dependencies** (2 min)

```python
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
```

**Step 8: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_multi_group_dependencies -v
```

Expected: PASS

**Step 9: Write test for semantic task IDs** (2 min)

```python
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
```

**Step 10: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_semantic_ids -v
```

Expected: PASS

**Step 11: Write test for orphan task detection** (2 min)

```python
def test_parse_markdown_plan_rejects_orphan_tasks():
    """parse_markdown_plan rejects tasks not in any group."""
    from harness.plan import parse_markdown_plan
    import pytest

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
```

**Step 12: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_markdown_plan_rejects_orphan_tasks -v
```

Expected: PASS

**Step 13: Commit** (30 sec)

```bash
git add src/harness/plan.py tests/harness/test_plan.py
git commit -m "feat(plan): add parse_markdown_plan() with semantic IDs and orphan detection"
```

---

### Task 2: Integrate Markdown Parser into `parse_plan_content()`

**Files:**
- Modify: `src/harness/plan.py:64-83` (the existing `parse_plan_content` function)
- Test: `tests/harness/test_plan.py`

**Step 1: Write failing test for Markdown detection in parse_plan_content** (2-5 min)

```python
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
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_markdown_format -v
```

Expected: FAIL with `ValueError: No JSON plan block found`

**Step 3: Update `parse_plan_content()` to detect Markdown first** (3 min)

Replace the existing `parse_plan_content` function body:

```python
def parse_plan_content(content: str) -> PlanDefinition:
    """Extract plan from LLM output (Markdown preferred, JSON fallback).

    Detection:
    - Markdown: Contains `**Goal:**` AND `| Task Group |`
    - JSON: Contains ```json block with valid JSON object

    Markdown is preferred as it's more readable and less error-prone.
    """
    # Check for Markdown Plan signature
    if "**Goal:**" in content and "| Task Group |" in content:
        plan = parse_markdown_plan(content)
        plan.validate_dag()
        return plan

    # Fallback: JSON parsing (legacy format)
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not match:
        raise ValueError("No valid plan found (neither Markdown nor JSON block)")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    plan = PlanDefinition(**data)
    plan.validate_dag()
    return plan
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_markdown_format -v
```

Expected: PASS

**Step 5: Run all existing tests to verify backward compatibility** (30 sec)

```bash
pytest tests/harness/test_plan.py -v
```

Expected: All tests PASS (existing JSON tests still work)

**Step 6: Write test for Markdown parsing errors** (2 min)

```python
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
    # This should pass (no cycle with single task)
    plan = parse_plan_content(content)
    assert plan.goal == "Cycle test"
```

**Step 7: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_markdown_cycle_rejected -v
```

Expected: PASS

**Step 8: Commit** (30 sec)

```bash
git add src/harness/plan.py tests/harness/test_plan.py
git commit -m "feat(plan): integrate Markdown parser with JSON fallback"
```

---

### Task 3: Update Plan Template to Show Markdown Format

**Files:**
- Modify: `src/harness/plan.py:86-154` (the `get_plan_template` function)
- Test: `tests/harness/test_plan.py`

**Step 1: Write failing test for new template format** (2 min)

```python
def test_get_plan_template_includes_markdown_format():
    """get_plan_template should show Markdown format as recommended."""
    template = get_plan_template()

    assert "**Goal:**" in template
    assert "| Task Group |" in template
    assert "### Task" in template
    assert "(Recommended)" in template or "Markdown" in template
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_get_plan_template_includes_markdown_format -v
```

Expected: FAIL with `AssertionError` (current template only shows JSON)

**Step 3: Replace `get_plan_template()` with Markdown-first template** (3 min)

```python
def get_plan_template() -> str:
    """Generate Markdown template for plan format.

    Shows the recommended Markdown format with Task Groups,
    plus legacy JSON format for backward compatibility.
    """
    return """\
# Plan Template

## Recommended: Structured Markdown

```markdown
# Implementation Plan Title

> **Execution:** Use `/dev-workflow:execute-plan path/to/plan.md` to implement.

**Goal:** One sentence description of the objective

**Architecture:** Brief architectural summary
**Tech Stack:** Python 3.13t, etc.

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2  | Core infrastructure (parallel) |
| Group 2    | 3     | Feature (depends on Group 1) |
| Group 3    | 4     | Tests (depends on Group 2) |

---

### Task 1: Create User Model

**Files:**
- Create: `src/models/user.py`

**Step 1: Write failing test**
```python
def test_user_model():
    user = User(email="test@example.com")
    assert user.email == "test@example.com"
```

**Step 2: Run test to verify failure**
```bash
pytest tests/test_user.py::test_user_model -v
```

**Step 3: Implement minimal code**
```python
class User:
    def __init__(self, email: str):
        self.email = email
```

### Task 2: Add Password Hashing

**Files:**
- Modify: `src/models/user.py`

**Step 1: Write failing test**
Test password hashing with bcrypt.

### Task 3: Create Login Endpoint

**Files:**
- Create: `src/routes/auth.py`

**Step 1: Implement /login**
Return JWT on success.

### Task 4: Integration Tests

**Files:**
- Create: `tests/test_auth_integration.py`

**Step 1: Test full auth flow**
Test registration, login, protected routes.
```

**Dependency Rules:**
- Tasks in Group N depend on ALL tasks in Group N-1
- Tasks within the same group are independent (can run in parallel)

---

## Legacy: JSON Format (Backward Compatible)

```json
{
  "goal": "Add user authentication with JWT tokens",
  "tasks": {
    "models": {
      "description": "Create User model with password hashing",
      "dependencies": [],
      "timeout_seconds": 300,
      "instructions": "Use bcrypt for password hashing.",
      "role": "backend"
    },
    "auth-endpoints": {
      "description": "Implement /login and /register endpoints",
      "dependencies": ["models"],
      "timeout_seconds": 600
    }
  }
}
```

**Field Reference:**
- `goal`: High-level objective (required)
- `tasks`: Dictionary keyed by unique task IDs (required)
- `description`: Brief task summary (required)
- `dependencies`: List of task IDs that must complete first (default: [])
- `timeout_seconds`: Max execution time in seconds (default: 600)
- `instructions`: Detailed guidance for the agent (optional)
- `role`: Specialist designation for task routing (optional)
"""
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_get_plan_template_includes_markdown_format -v
```

Expected: PASS

**Step 5: Run all tests to ensure nothing broke** (30 sec)

```bash
pytest tests/harness/test_plan.py -v
```

Expected: All tests PASS

**Step 6: Run full test suite and lint** (1 min)

```bash
make check
```

Expected: All checks pass

**Step 7: Commit** (30 sec)

```bash
git add src/harness/plan.py tests/harness/test_plan.py
git commit -m "docs(plan): update template to recommend Markdown format"
```

---

### Task 4: Code Review

**Files:**
- All modified files from Tasks 1-3

**Step 1: Review changes**

```bash
git diff main..HEAD
```

Verify:
- [ ] `parse_markdown_plan()` correctly extracts goal, groups, and tasks
- [ ] Dependencies calculated correctly (Group N depends on Group N-1)
- [ ] JSON fallback still works (backward compatibility)
- [ ] All tests pass
- [ ] No type errors

**Step 2: Run final verification**

```bash
make check
```

Expected: All checks pass (lint, typecheck, test)
