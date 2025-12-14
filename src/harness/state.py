# src/harness/state.py
"""
Pydantic state models for workflow management.

WorkflowState is the canonical schema for dev-workflow state.
StateManager handles persistence to markdown frontmatter format.
"""

from pydantic import BaseModel, Field
from typing import Literal
from pathlib import Path
import re
import threading


class WorkflowState(BaseModel):
    """State for an active workflow execution."""

    workflow: Literal["execute-plan", "subagent"] = Field(
        ..., description="Execution mode"
    )
    plan: str = Field(..., description="Absolute path to plan file")
    current_task: int = Field(ge=0, description="Last completed task (0=not started)")
    total_tasks: int = Field(gt=0, description="Total tasks in plan")
    worktree: str = Field(..., description="Absolute path to worktree")
    base_sha: str = Field(..., description="Base commit SHA before workflow")
    last_commit: str | None = Field(None, description="Last commit SHA")
    current_group: int = Field(1, ge=1, description="Current parallel group")
    total_groups: int = Field(1, ge=1, description="Total parallel groups")
    parallel_mode: bool = Field(True, description="Enable parallel execution")
    batch_size: int = Field(5, ge=0, description="Tasks per batch (0=unbatched)")
    retry_count: int = Field(0, ge=0, le=2, description="Retries for current task")
    failed_tasks: str = Field("", description="Comma-separated failed task numbers")
    enabled: bool = Field(True, description="Workflow active")


class PendingHandoff(BaseModel):
    """Handoff file for session resume."""

    mode: Literal["sequential", "subagent"]
    plan: str


class StateManager:
    """Manages workflow state with file persistence.

    Thread-safe: All public methods are protected by a Lock.
    """

    def __init__(self, worktree_root: Path):
        self.worktree_root = Path(worktree_root)
        self.state_file = self.worktree_root / ".claude" / "dev-workflow-state.local.md"
        self._state: WorkflowState | None = None
        self._lock = threading.Lock()

    def load(self) -> WorkflowState | None:
        """Load state from disk (thread-safe)."""
        with self._lock:
            if not self.state_file.exists():
                return None

            content = self.state_file.read_text()
            frontmatter = self._parse_frontmatter(content)
            if not frontmatter:
                return None

            self._state = WorkflowState(**frontmatter)
            return self._state

    def save(self, state: WorkflowState) -> None:
        """Save state to disk atomically (thread-safe)."""
        with self._lock:
            self._state = state
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            content = self._to_frontmatter(state)
            temp_file = self.state_file.with_suffix(".tmp")
            temp_file.write_text(content)
            temp_file.rename(self.state_file)

    def update(self, **kwargs) -> WorkflowState:
        """Update specific fields atomically (thread-safe).

        Auto-loads state from disk if not already loaded.
        """
        with self._lock:
            if not self._state:
                # Auto-load if state not in memory
                if self.state_file.exists():
                    content = self.state_file.read_text()
                    frontmatter = self._parse_frontmatter(content)
                    if frontmatter:
                        self._state = WorkflowState(**frontmatter)
                if not self._state:
                    raise ValueError("No state loaded and no state file exists")

            self._state = self._state.model_copy(update=kwargs)
            # Save without lock (we already hold it)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            content = self._to_frontmatter(self._state)
            temp_file = self.state_file.with_suffix(".tmp")
            temp_file.write_text(content)
            temp_file.rename(self.state_file)
            return self._state

    def _parse_frontmatter(self, content: str) -> dict | None:
        """Parse YAML frontmatter from markdown."""
        match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
        if not match:
            return None

        result = {}
        for line in match.group(1).split("\n"):
            # Handle malformed lines gracefully
            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            # Pydantic handles str -> int/bool conversion
            result[key] = value

        return result

    def _to_frontmatter(self, state: WorkflowState) -> str:
        """Convert state to markdown frontmatter."""
        lines = ["---"]
        for key, value in state.model_dump().items():
            if isinstance(value, bool):
                value = str(value).lower()
            lines.append(f"{key}: {value}")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)
