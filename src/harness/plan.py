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


def get_plan_schema() -> str:
    """Generate JSON schema for plan format.

    Provides the 'instruction manual' for agents to understand
    how to structure valid JSON plans.
    """
    schema = PlanDefinition.model_json_schema()
    return json.dumps(schema, indent=2)
