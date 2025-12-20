"""
Plan extraction from LLM output.

The Orchestrator emits markdown with thinking tokens, followed by a JSON block.
We extract the JSON, validate the DAG, and convert to WorkflowState.
"""

import json
import re
from typing import TypedDict

from pydantic import BaseModel, Field

from .state import Task, TaskStatus, WorkflowState, detect_cycle


class _TaskData(TypedDict):
    """Intermediate task data during Markdown parsing."""

    description: str
    instructions: str
    dependencies: list[str]


class PlanTaskDefinition(BaseModel):
    """User-facing task definition."""

    description: str
    dependencies: list[str] = Field(default_factory=list)
    timeout_seconds: int = 600
    instructions: str | None = None
    role: str | None = None


class PlanDefinition(BaseModel):
    """User-facing plan with goal and tasks."""

    goal: str
    tasks: dict[str, PlanTaskDefinition]

    def validate_dag(self) -> None:
        """Reject cycles and missing dependencies."""
        # Check deps exist
        for task_id, task in self.tasks.items():
            for dep in task.dependencies:
                if dep not in self.tasks:
                    raise ValueError(f"Missing dependency: {dep} (in {task_id})")

        # DFS cycle detection
        graph = {task_id: task.dependencies for task_id, task in self.tasks.items()}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Cycle detected at {cycle_node}")

    def to_workflow_state(self) -> WorkflowState:
        """Convert to internal WorkflowState."""
        tasks = {
            tid: Task(
                id=tid,
                description=t.description,
                status=TaskStatus.PENDING,
                dependencies=t.dependencies,
                started_at=None,
                completed_at=None,
                claimed_by=None,
                timeout_seconds=t.timeout_seconds,
                instructions=t.instructions,
                role=t.role,
            )
            for tid, t in self.tasks.items()
        }
        return WorkflowState(tasks=tasks)


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
    tasks_data: dict[str, _TaskData] = {}

    for i in range(1, len(parts), 3):
        if i + 2 > len(parts):
            break
        t_id = parts[i].strip()
        t_desc = parts[i + 1].strip()
        t_body = parts[i + 2].strip()

        tasks_data[t_id] = _TaskData(
            description=t_desc,
            instructions=t_body,
            dependencies=[],
        )

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
        final_tasks[t_id] = PlanTaskDefinition(
            description=t_data["description"],
            instructions=t_data["instructions"],
            dependencies=t_data["dependencies"],
            timeout_seconds=600,
            role=None,
        )

    return PlanDefinition(goal=goal, tasks=final_tasks)


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
