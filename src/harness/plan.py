"""
Plan extraction from LLM output.

The Orchestrator emits markdown with thinking tokens, followed by a JSON block.
We extract the JSON, validate the DAG, and convert to WorkflowState.
"""

import json
import re

from pydantic import BaseModel, Field

from .state import Task, TaskStatus, WorkflowState


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
        visited: set[str] = set()
        path: set[str] = set()

        def visit(node: str) -> None:
            if node in path:
                raise ValueError(f"Cycle detected at {node}")
            if node in visited:
                return
            visited.add(node)
            path.add(node)
            for dep in self.tasks[node].dependencies:
                visit(dep)
            path.remove(node)

        for task_id in self.tasks:
            visit(task_id)

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


def parse_plan_content(content: str) -> PlanDefinition:
    """Extract JSON plan from LLM output.

    Finds the first ```json block, parses it, validates the DAG.
    Everything outside the JSON block is ignored (thinking tokens, markdown).
    """
    # Forgiving wrapper: handles ```json or plain ```
    # Strict on payload: must be valid JSON object
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not match:
        raise ValueError("No JSON plan block found")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    plan = PlanDefinition(**data)
    plan.validate_dag()
    return plan


def get_plan_template() -> str:
    """Generate Markdown template for plan format.

    Provides LLM-friendly documentation with:
    1. Template structure showing all fields
    2. Complete realistic example
    """
    return """\
# Plan Template

Submit plans as a JSON block inside markdown fences.

## Template Structure

```json
{
  "goal": "<one-sentence description of what this plan achieves>",
  "tasks": {
    "<task_id>": {
      "description": "<what this task does>",
      "dependencies": ["<task_id>", "..."],
      "timeout_seconds": 600,
      "instructions": "<detailed step-by-step instructions or null>",
      "role": "<specialist role like 'backend' or 'frontend' or null>"
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

## Complete Example

```json
{
  "goal": "Add user authentication with JWT tokens",
  "tasks": {
    "models": {
      "description": "Create User model with password hashing",
      "dependencies": [],
      "timeout_seconds": 300,
      "instructions": "Use bcrypt for password hashing. Include email, hashed_password fields.",
      "role": "backend"
    },
    "auth-endpoints": {
      "description": "Implement /login and /register endpoints",
      "dependencies": ["models"],
      "timeout_seconds": 600,
      "instructions": "Return JWT on successful login. Validate email format on register.",
      "role": "backend"
    },
    "auth-tests": {
      "description": "Write integration tests for auth flow",
      "dependencies": ["auth-endpoints"],
      "timeout_seconds": 300,
      "instructions": "Test: valid login, invalid password, duplicate registration, token expiry.",
      "role": "backend"
    }
  }
}
```
"""
