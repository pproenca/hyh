"""
Plan extraction from LLM output.

Parses Markdown format with Task Groups table and `### Task ID:` sections.
Validates DAG and converts to WorkflowState.
"""

import re
from typing import Final, TypedDict

from msgspec import Struct, field

from .state import Task, TaskStatus, WorkflowState, detect_cycle

# Regex for safe task IDs: alphanumeric, hyphens, underscores, dots
# NO shell metacharacters: $ ` ; | & ( ) < > ' " \ ! etc.
_SAFE_TASK_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$")


def _validate_task_id(task_id: str) -> None:
    if not task_id:
        raise ValueError("Task ID cannot be empty")

    if not _SAFE_TASK_ID_PATTERN.match(task_id):
        raise ValueError(
            f"Invalid task ID '{task_id}': Task IDs must start with alphanumeric "
            "and contain only letters, digits, hyphens, underscores, and dots. "
            "Special characters like $, `, ;, |, etc. are not allowed."
        )


class _TaskData(TypedDict):
    description: str
    instructions: str
    dependencies: list[str]


class PlanTaskDefinition(Struct, omit_defaults=True):
    """Task definition from plan parsing."""

    description: str
    dependencies: list[str] = field(default_factory=list)
    timeout_seconds: int = 600
    instructions: str | None = None
    role: str | None = None


class PlanDefinition(Struct):
    """Complete plan with goal and tasks."""

    goal: str
    tasks: dict[str, PlanTaskDefinition]

    def validate_dag(self) -> None:
        for task_id, task in self.tasks.items():
            for dep in task.dependencies:
                if dep not in self.tasks:
                    raise ValueError(f"Missing dependency: {dep} (in {task_id})")

        graph = {task_id: task.dependencies for task_id, task in self.tasks.items()}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Cycle detected at {cycle_node}")

    def to_workflow_state(self) -> WorkflowState:
        tasks = {
            tid: Task(
                id=tid,
                description=t.description,
                status=TaskStatus.PENDING,
                dependencies=tuple(t.dependencies),
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
    3. Task definitions from `### Task <ID>` headers (colon optional)

    Dependencies: Tasks in Group N depend on ALL tasks in Group N-1.

    Validation:
    - Rejects orphan tasks (in body but not in table)
    - Rejects phantom tasks (in table but not in body)
    """
    goal_match = re.search(r"\*\*Goal:\*\*\s*(.+)", content)
    goal = goal_match.group(1).strip() if goal_match else "Goal not specified"

    group_pattern = r"\|\s*Group\s*(\d+)\s*\|\s*([\w\-\.,\s]+)\s*\|"
    groups: dict[int, list[str]] = {}

    for match in re.finditer(group_pattern, content):
        group_id = int(match.group(1))
        task_ids = [t.strip() for t in match.group(2).split(",") if t.strip()]
        for tid in task_ids:
            _validate_task_id(tid)
        groups[group_id] = task_ids

    task_pattern = r"^### Task\s+([\w\-\.]+)\s*(?::\s*(.*))?$"
    parts = re.split(task_pattern, content, flags=re.MULTILINE)

    tasks_data: dict[str, _TaskData] = {}

    for i in range(1, len(parts), 3):
        if i + 2 > len(parts):
            break
        t_id = parts[i].strip()
        t_desc = (parts[i + 1] or "").strip()
        t_body = parts[i + 2].strip()

        _validate_task_id(t_id)

        tasks_data[t_id] = _TaskData(
            description=t_desc if t_desc else f"Task {t_id}",
            instructions=t_body,
            dependencies=[],
        )

    sorted_group_ids = sorted(groups.keys())
    for i, group_id in enumerate(sorted_group_ids):
        if i > 0:
            prev_group_id = sorted_group_ids[i - 1]
            prev_tasks = groups[prev_group_id]

            for t_id in groups[group_id]:
                if t_id in tasks_data:
                    tasks_data[t_id]["dependencies"] = prev_tasks

    all_grouped_tasks = {t for tasks in groups.values() for t in tasks}

    orphan_tasks = set(tasks_data.keys()) - all_grouped_tasks
    if orphan_tasks:
        raise ValueError(
            f"Orphan tasks not in any group: {', '.join(sorted(orphan_tasks))}. "
            "Add them to the Task Groups table."
        )

    phantom_tasks = all_grouped_tasks - set(tasks_data.keys())
    if phantom_tasks:
        raise ValueError(
            f"Phantom tasks in table but not in body: {', '.join(sorted(phantom_tasks))}. "
            "Check for typos in ### Task headers (missing space, wrong ID)."
        )

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
    if not content or not content.strip():
        raise ValueError("No valid plan found: content is empty or whitespace-only")

    if "**Goal:**" in content and "| Task Group |" in content:
        plan = parse_markdown_plan(content)
        if not plan.tasks:
            raise ValueError("No valid plan found: no tasks defined in plan")
        plan.validate_dag()
        return plan

    raise ValueError("No valid plan found. Use `harness plan template` for format reference.")


def get_plan_template() -> str:
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
"""
