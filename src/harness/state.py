# src/harness/state.py
"""
Pydantic state models for workflow management.

WorkflowState is the canonical schema for dev-workflow state.
StateManager handles persistence to JSON format.
"""

import json
import os
import threading
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def detect_cycle(graph: dict[str, list[str]]) -> str | None:
    """Detect cycle in directed graph using DFS.

    Args:
        graph: Adjacency list mapping node ID to list of dependency IDs.

    Returns:
        First node found in a cycle, or None if graph is acyclic.
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str) -> str | None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if cycle_node := dfs(neighbor):
                    return cycle_node
            elif neighbor in rec_stack:
                return neighbor
        rec_stack.discard(node)
        return None

    for node in graph:
        if node not in visited and (cycle_node := dfs(node)):
            return cycle_node
    return None


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    """Individual task in workflow DAG."""

    id: str = Field(..., description="Unique task identifier")
    description: str = Field(..., description="Task description")
    status: TaskStatus = Field(..., description="Current task status")
    dependencies: list[str] = Field(..., description="List of task IDs that must complete first")
    started_at: datetime | None = Field(None, description="Task start timestamp")
    completed_at: datetime | None = Field(None, description="Task completion timestamp")
    claimed_by: str | None = Field(None, description="Worker ID that claimed this task")
    timeout_seconds: int = Field(600, description="Timeout for task execution")
    # Orchestrator Injection (v2.5)
    instructions: str | None = Field(None, description="Detailed prompt for agent")
    role: str | None = Field(None, description="Agent role: frontend, backend, etc.")

    def is_timed_out(self) -> bool:
        """Check if task has exceeded timeout window."""
        if self.status != TaskStatus.RUNNING:
            return False
        if not self.started_at:
            return False

        # Ensure we compare UTC to UTC
        started = self.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        elapsed = now - started
        return elapsed.total_seconds() > self.timeout_seconds


class WorkflowState(BaseModel):
    """State for an active workflow execution with task DAG."""

    tasks: dict[str, Task] = Field(default_factory=dict, description="Task DAG")

    def validate_dag(self) -> None:
        """Ensure no circular dependencies exist.

        Raises:
            ValueError: If a dependency cycle is detected.
        """
        graph = {task_id: task.dependencies for task_id, task in self.tasks.items()}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Cycle detected at {cycle_node}")

    def get_claimable_task(self) -> Task | None:
        """Find a task that can be claimed (pending or timed out with satisfied deps)."""
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING or (
                task.status == TaskStatus.RUNNING and task.is_timed_out()
            ):
                deps_satisfied = all(
                    self.tasks[dep_id].status == TaskStatus.COMPLETED
                    for dep_id in task.dependencies
                    if dep_id in self.tasks
                )
                if deps_satisfied:
                    return task
        return None

    def get_task_for_worker(self, worker_id: str) -> Task | None:
        """Get task for specific worker (idempotent - returns existing or assigns new)."""
        for task in self.tasks.values():
            if task.claimed_by == worker_id and task.status == TaskStatus.RUNNING:
                return task
        return self.get_claimable_task()


class PendingHandoff(BaseModel):
    """Handoff file for session resume."""

    mode: Literal["sequential", "subagent"]
    plan: str


class StateManager:
    """Manages workflow state with file persistence.

    Thread-safe: All public methods are protected by a Lock.
    """

    def __init__(self, worktree_root: Path) -> None:
        self.worktree_root = Path(worktree_root)
        self.state_file = self.worktree_root / ".claude" / "dev-workflow-state.json"
        self._state: WorkflowState | None = None
        self._lock = threading.Lock()

    def _ensure_state_loaded(self) -> WorkflowState:
        """Load state from disk if not cached. Must be called with lock held.

        Returns:
            The cached or freshly loaded WorkflowState.

        Raises:
            ValueError: If no state file exists and no state is cached.
        """
        if self._state:
            return self._state
        if self.state_file.exists():
            content = self.state_file.read_text()
            data = json.loads(content)
            self._state = WorkflowState(**data)
        if not self._state:
            raise ValueError("No state loaded and no state file exists")
        return self._state

    def _write_atomic(self, state: WorkflowState) -> None:
        """Write state to disk atomically using tmp-fsync-rename. Must be called with lock held."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        content = state.model_dump_json(indent=2)
        temp_file = self.state_file.with_suffix(".tmp")
        with temp_file.open("w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        temp_file.rename(self.state_file)

    def load(self) -> WorkflowState | None:
        """Load state from disk (thread-safe)."""
        with self._lock:
            if not self.state_file.exists():
                return None

            content = self.state_file.read_text()
            data = json.loads(content)
            self._state = WorkflowState(**data)
            return self._state

    def save(self, state: WorkflowState) -> None:
        """Save state to disk atomically (thread-safe)."""
        with self._lock:
            state.validate_dag()  # Reject cycles before persisting
            self._state = state
            self._write_atomic(state)

    def update(self, **kwargs: Any) -> WorkflowState:
        """Update specific fields atomically (thread-safe).

        Auto-loads state from disk if not already loaded.
        Validates incoming data (Pydantic at the boundary).
        """
        with self._lock:
            self._ensure_state_loaded()

            # Convert raw dicts to Task objects (Pydantic validation at boundary)
            if "tasks" in kwargs and isinstance(kwargs["tasks"], dict):
                validated_tasks: dict[str, Task] = {}
                for task_id, task_data in kwargs["tasks"].items():
                    if isinstance(task_data, dict):
                        validated_tasks[task_id] = Task(**task_data)
                    else:
                        validated_tasks[task_id] = task_data
                kwargs["tasks"] = validated_tasks

            self._state = self._state.model_copy(update=kwargs)  # type: ignore[union-attr]
            self._write_atomic(self._state)
            return self._state

    def claim_task(self, worker_id: str) -> Task | None:
        """Atomically claim a task for worker (find + update + save in one critical section)."""
        with self._lock:
            state = self._ensure_state_loaded()

            # Check if worker already has a task (idempotency)
            task = state.get_task_for_worker(worker_id)
            if not task:
                return None

            is_new_claim = task.claimed_by != worker_id

            # ALWAYS renew the lease (prevents task stealing on retry)
            now = datetime.now(UTC)
            task.started_at = now

            if is_new_claim:
                task.status = TaskStatus.RUNNING
                task.claimed_by = worker_id

            state.tasks[task.id] = task
            self._write_atomic(state)

            return task

    def complete_task(self, task_id: str, worker_id: str) -> None:
        """Atomically complete a task with ownership validation."""
        with self._lock:
            state = self._ensure_state_loaded()

            if task_id not in state.tasks:
                raise ValueError(f"Task {task_id} not found")

            task = state.tasks[task_id]

            if task.claimed_by != worker_id:
                raise ValueError(f"Task {task_id} is not claimed by {worker_id}")

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)

            state.tasks[task_id] = task
            self._write_atomic(state)
