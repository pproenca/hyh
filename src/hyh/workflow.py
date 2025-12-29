"""Workflow state detection and management."""

from pathlib import Path

from msgspec import Struct

from .plan import parse_speckit_tasks


class WorkflowPhase(Struct, frozen=True, forbid_unknown_fields=True):
    """Current workflow phase and suggested next action."""

    phase: str  # "none", "specify", "plan", "implement", "complete"
    next_action: str | None  # "specify", "plan", "implement", None
    spec_exists: bool = False
    plan_exists: bool = False
    tasks_total: int = 0
    tasks_complete: int = 0


def detect_phase(worktree: Path) -> WorkflowPhase:
    """Detect current workflow phase based on artifacts.

    Args:
        worktree: Path to worktree root.

    Returns:
        WorkflowPhase with current state and suggested action.
    """
    worktree = Path(worktree)
    specs_dir = worktree / "specs"

    spec_path = specs_dir / "spec.md"
    plan_path = specs_dir / "plan.md"
    tasks_path = specs_dir / "tasks.md"

    spec_exists = spec_path.exists()
    plan_exists = plan_path.exists()
    tasks_total = 0
    tasks_complete = 0

    # No spec = nothing started
    if not spec_exists:
        return WorkflowPhase(
            phase="none",
            next_action="specify",
            spec_exists=False,
            plan_exists=False,
        )

    # Has spec but no plan
    if not plan_exists:
        return WorkflowPhase(
            phase="specify",
            next_action="plan",
            spec_exists=True,
            plan_exists=False,
        )

    # Has plan, check tasks
    if tasks_path.exists():
        content = tasks_path.read_text()
        task_list = parse_speckit_tasks(content)
        tasks_total = len(task_list.tasks)
        tasks_complete = sum(1 for t in task_list.tasks.values() if t.status == "completed")

        if tasks_complete >= tasks_total and tasks_total > 0:
            return WorkflowPhase(
                phase="complete",
                next_action=None,
                spec_exists=True,
                plan_exists=True,
                tasks_total=tasks_total,
                tasks_complete=tasks_complete,
            )

    return WorkflowPhase(
        phase="plan",
        next_action="implement",
        spec_exists=True,
        plan_exists=True,
        tasks_total=tasks_total,
        tasks_complete=tasks_complete,
    )
