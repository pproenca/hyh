# src/harness/state.py
"""
msgspec state models for workflow management.

WorkflowState is the canonical schema for dev-workflow state.
StateManager handles persistence to JSON format.

Performance characteristics:
- Task lookup: O(1) via dict
- Worker task lookup: O(1) via _worker_index
- Pending task claim: O(1) amortized via indexed deque
- DAG validation: O(V + E) via iterative DFS
- Serialization: Zero-copy with msgspec (faster than Pydantic)
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

import msgspec
from msgspec import Meta, Struct, field
from msgspec.structs import replace as struct_replace


def detect_cycle(graph: dict[str, list[str]]) -> str | None:  # Time: O(V+E), Space: O(V)
    """Detect cycle in directed graph using iterative DFS with explicit stack.

    Uses three-color marking (white/gray/black) to identify back edges.
    Explicit stack prevents RecursionError on deep graphs (>1000 nodes).

    Args:
        graph: Adjacency list mapping node ID to list of dependency IDs.

    Returns:
        First node found in a cycle, or None if graph is acyclic.
    """
    white: Final = 0  # Unvisited
    gray: Final = 1  # In current DFS path (recursion stack)
    black: Final = 2  # Fully processed

    color: dict[str, int] = {node: white for node in graph}

    for start_node in graph:
        if color[start_node] != white:
            continue

        # Stack: (node, iterator over neighbors, is_entering)
        # is_entering=True means first visit; False means returning from children
        stack: list[tuple[str, Iterator[str], bool]] = [
            (start_node, iter(graph.get(start_node, [])), True)
        ]

        while stack:
            node, neighbors_iter, is_entering = stack.pop()

            if is_entering:
                node_color = color.get(node, white)  # External nodes are white
                if node_color == gray:
                    return node  # Back edge → cycle
                if node_color == black:
                    continue  # Already fully processed
                color[node] = gray
                # Re-push with is_entering=False for post-processing
                stack.append((node, neighbors_iter, False))
            else:
                # Try next neighbor
                try:
                    neighbor = next(neighbors_iter)
                    # Re-push current node to continue iteration
                    stack.append((node, neighbors_iter, False))
                    if color.get(neighbor, white) == gray:
                        return neighbor  # Back edge → cycle
                    if color.get(neighbor, white) == white:
                        stack.append((neighbor, iter(graph.get(neighbor, [])), True))
                except StopIteration:
                    # All neighbors processed; mark black
                    color[node] = black

    return None


class TaskStatus(str, Enum):
    """Task execution status.

    State machine: PENDING → RUNNING → COMPLETED | FAILED
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Constrained types using msgspec.Meta for validation during decode
TimeoutSeconds = Annotated[int, Meta(ge=1, le=86400)]


class Task(Struct, forbid_unknown_fields=True):
    """Individual task in workflow DAG.

    Thread-safe: Immutable after creation except via StateManager methods.
    Uses msgspec.Struct for zero-copy serialization performance.
    """

    # Required fields (no defaults)
    id: str
    description: str

    # Optional fields with defaults
    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None
    claimed_by: str | None = None
    timeout_seconds: TimeoutSeconds = 600
    instructions: str | None = None
    role: str | None = None

    # Class-level clock for testable timeout checking.
    # Tests can inject a mock clock via set_clock() to avoid real time.sleep().
    _clock: ClassVar[Callable[[], datetime]] = lambda: datetime.now(UTC)

    @classmethod
    def set_clock(cls, clock: Callable[[], datetime]) -> None:
        """Set custom clock for testing timeout behavior."""
        cls._clock = clock

    @classmethod
    def reset_clock(cls) -> None:
        """Reset to default system clock."""
        cls._clock = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        """Validate and normalize fields after initialization.

        Called by msgspec after struct creation and during decode.

        NOTE: msgspec validates types during decode (from JSON/external data),
        but trusts internal code per its philosophy. We only validate:
        1. Business logic constraints (empty ID, whitespace stripping)
        2. Normalization (list→tuple coercion for dependencies)

        Type validation happens automatically when decoding via msgspec.convert().
        """
        # Business logic: strip whitespace from ID and reject empty
        if isinstance(self.id, str):
            stripped = self.id.strip()
            if not stripped:
                raise ValueError("Task ID cannot be empty or whitespace-only")
            if stripped != self.id:
                object.__setattr__(self, "id", stripped)

        # Normalize: coerce list to tuple for immutability
        if isinstance(self.dependencies, list):
            object.__setattr__(self, "dependencies", tuple(self.dependencies))

    def is_timed_out(self) -> bool:  # Time: O(1), Space: O(1)
        """Check if task has exceeded timeout window.

        Returns:
            True if task is RUNNING and elapsed time exceeds timeout_seconds.
        """
        if self.status != TaskStatus.RUNNING or self.started_at is None:
            return False

        started = (
            self.started_at
            if self.started_at.tzinfo is not None
            else self.started_at.replace(tzinfo=UTC)
        )
        elapsed = Task._clock() - started  # Use injectable clock for testability
        return elapsed.total_seconds() > self.timeout_seconds


class WorkflowState(Struct, forbid_unknown_fields=True, omit_defaults=True, dict=True):
    """State for an active workflow execution with task DAG.

    Index invariants maintained by rebuild_indexes():
    - _pending_deque: FIFO of task IDs with status=PENDING, sorted by dep count
    - _pending_set: O(1) membership test for _pending_deque
    - _worker_index: Maps worker_id → task_id for O(1) lookup

    NOTE: Uses dict=True to allow private attributes stored in __dict__.
    These are NOT serialized (only struct fields are), but ARE available at runtime.
    This is the msgspec equivalent of Pydantic's PrivateAttr pattern.
    """

    tasks: dict[str, Task] = field(default_factory=dict)

    # Private indexes stored in __dict__ (enabled via dict=True)
    # These won't be serialized because they're not struct fields

    def __post_init__(self) -> None:
        """Initialize indexes and normalize tasks input.

        Called by msgspec after struct creation and during decode.
        """
        # Initialize private attrs in __dict__ (enabled by dict=True)
        # These won't be serialized because they're not struct fields
        self._pending_deque: deque[str] = deque()
        self._pending_set: set[str] = set()
        self._worker_index: dict[str, str] = {}

        # Normalize tasks from list[Task|dict] to dict[str, Task]
        if isinstance(self.tasks, list):
            tasks_dict: dict[str, Task] = {}
            for item in self.tasks:
                match item:
                    case Task() as t:
                        tasks_dict[t.id] = item
                    case {"id": str(task_id)} as d:
                        tasks_dict[task_id] = msgspec.convert(d, Task)
                    case dict():
                        raise ValueError("Task dict must contain 'id' field")
                    case _:
                        raise TypeError(f"Invalid task type: {type(item).__name__}")
            object.__setattr__(self, "tasks", tasks_dict)

        # Rebuild indexes after initialization
        self.rebuild_indexes()

    def rebuild_indexes(self) -> None:  # Time: O(n log n), Space: O(n)
        """Rebuild all O(1) access indexes from canonical tasks dict.

        Called on: initial load, after updates, after deserialization.
        Sort is O(n log n) but amortized across many O(1) reads.

        NOTE: Initializes private attrs if missing. This handles the case where
        struct_replace() creates a new instance without calling __post_init__.
        """
        # Ensure private attrs exist (struct_replace skips __post_init__)
        if not hasattr(self, "_pending_deque"):
            self._pending_deque = deque()
        if not hasattr(self, "_pending_set"):
            self._pending_set = set()
        if not hasattr(self, "_worker_index"):
            self._worker_index = {}

        pending: list[str] = []
        self._worker_index.clear()

        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING:
                pending.append(task.id)
            elif task.status == TaskStatus.RUNNING and task.claimed_by:
                self._worker_index[task.claimed_by] = task.id

        # Sort by dependency count: fewer deps → earlier in queue (heuristic)
        pending.sort(key=lambda tid: len(self.tasks[tid].dependencies))
        # Clear and repopulate (instance attrs, not struct fields)
        self._pending_deque.clear()
        self._pending_deque.extend(pending)
        self._pending_set.clear()
        self._pending_set.update(pending)

    def validate_dag(self) -> None:  # Time: O(V + E), Space: O(V)
        """Validate DAG integrity: no missing deps, no cycles.

        Raises:
            ValueError: If dependency missing or cycle detected.
        """
        task_ids = set(self.tasks.keys())

        for task_id, task in self.tasks.items():
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise ValueError(f"Missing dependency: {dep} (required by {task_id})")

        graph = {tid: list(t.dependencies) for tid, t in self.tasks.items()}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Dependency cycle detected at: {cycle_node}")

    def get_claimable_task(self) -> Task | None:  # Time: O(n) worst, O(1) amortized, Space: O(1)
        """Find next task eligible for claiming.

        Priority:
        1. Pending tasks with satisfied dependencies (O(1) from deque)
        2. Timed-out running tasks for reclaim (O(n) scan, rare path)

        Returns:
            Claimable Task or None if no work available.
        """
        # Fast path: check pending deque
        # Track rotations to avoid infinite loop when all pending tasks are blocked
        rotations = 0
        max_rotations = len(self._pending_deque)

        while self._pending_deque and rotations <= max_rotations:
            task_id = self._pending_deque[0]

            # Validate deque entry still valid
            if task_id not in self.tasks:
                self._pending_deque.popleft()
                self._pending_set.discard(task_id)
                max_rotations = len(self._pending_deque)  # Adjust after removal
                continue

            task = self.tasks[task_id]
            if task.status != TaskStatus.PENDING:
                self._pending_deque.popleft()
                self._pending_set.discard(task_id)
                max_rotations = len(self._pending_deque)  # Adjust after removal
                continue

            if self._are_deps_satisfied(task):
                return task

            # Deps not satisfied; rotate to back for retry later
            self._pending_deque.popleft()
            self._pending_deque.append(task_id)
            rotations += 1

        # Slow path: scan for timed-out tasks (fault recovery only)
        for task in self.tasks.values():
            if (
                task.status == TaskStatus.RUNNING
                and task.is_timed_out()
                and self._are_deps_satisfied(task)
            ):
                return task

        return None

    def _are_deps_satisfied(self, task: Task) -> bool:  # Time: O(d), Space: O(1)
        """Check if all task dependencies are COMPLETED.

        Args:
            task: Task to check dependencies for.

        Returns:
            True if all dependencies have status=COMPLETED.

        Raises:
            ValueError: If a dependency task ID doesn't exist.
        """
        for dep_id in task.dependencies:
            dep_task = self.tasks.get(dep_id)
            if dep_task is None:
                raise ValueError(f"Missing dependency: {dep_id} (in {task.id})")
            if dep_task.status != TaskStatus.COMPLETED:
                return False
        return True

    def get_task_for_worker(self, worker_id: str) -> Task | None:  # Time: O(1), Space: O(1)
        """Get worker's current task (idempotent) or assign new claimable task.

        Uses O(1) _worker_index for existing assignment lookup.
        """
        # O(1) lookup for existing assignment
        if existing_tid := self._worker_index.get(worker_id):
            task = self.tasks.get(existing_tid)
            if task and task.status == TaskStatus.RUNNING and task.claimed_by == worker_id:
                return task
            # Stale index entry; clean up (keyed by worker_id, not task_id)
            del self._worker_index[worker_id]

        return self.get_claimable_task()


class PendingHandoff(Struct, frozen=True, forbid_unknown_fields=True):
    """Handoff file for session resume."""

    mode: Literal["sequential", "subagent"]
    plan: str


class ClaimResult(Struct, frozen=True, forbid_unknown_fields=True):
    """Result of claim_task operation with atomic metadata.

    Flags computed atomically with claim to prevent TOCTOU races.
    """

    task: Task | None = None
    is_retry: bool = False
    is_reclaim: bool = False


class StateManager:
    """Thread-safe workflow state persistence manager.

    Guarantees:
    - All public methods are serialized via Lock
    - Disk writes are atomic (tmp + fsync + rename)
    - Memory state only updated after successful disk write

    Thread-safety: Uses coarse-grained locking. For high contention,
    consider per-task fine-grained locking or optimistic concurrency.

    Performance: Uses msgspec module-level functions which are thread-safe
    and avoid repeated encoder/decoder allocation.
    """

    __slots__ = ("_lock", "_state", "state_file", "worktree_root")

    def __init__(self, worktree_root: Path) -> None:  # Time: O(1), Space: O(1)
        self.worktree_root: Final[Path] = Path(worktree_root)
        self.state_file: Final[Path] = self.worktree_root / ".claude" / "dev-workflow-state.json"
        self._state: WorkflowState | None = None
        self._lock: Final[threading.Lock] = threading.Lock()

    def _ensure_state_loaded(self) -> WorkflowState:  # Time: O(n log n), Space: O(n)
        """Lazy-load state from disk if not cached. Must hold lock.

        Returns:
            Cached or freshly loaded WorkflowState.

        Raises:
            ValueError: If no state cached and no state file exists.
        """
        if self._state is not None:
            return self._state

        if not self.state_file.exists():
            raise ValueError("No workflow state: file not found and no cached state")

        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self._state = msgspec.convert(data, WorkflowState)
        return self._state

    def _write_atomic(self, state: WorkflowState) -> None:  # Time: O(n), Space: O(n)
        """Atomically persist state via tmp-fsync-rename. Must hold lock.

        Pattern: write to .tmp → fsync → rename over target.
        Rename is atomic on POSIX; provides crash consistency.

        Uses msgspec.json.encode which is thread-safe (module-level function).
        """
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        # msgspec.json.encode is thread-safe and handles datetime serialization
        content = msgspec.json.encode(state).decode("utf-8")
        temp_file = self.state_file.with_suffix(".tmp")

        with temp_file.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        temp_file.rename(self.state_file)

    def load(self) -> WorkflowState | None:  # Time: O(n log n), Space: O(n)
        """Load state from disk, replacing any cached state.

        Returns:
            WorkflowState if file exists, None otherwise.
        """
        with self._lock:
            if not self.state_file.exists():
                self._state = None
                return None

            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self._state = msgspec.convert(data, WorkflowState)
            return self._state

    def save(self, state: WorkflowState) -> None:  # Time: O(V + E + n log n), Space: O(n)
        """Validate and persist state, replacing cache.

        Args:
            state: New workflow state to persist.

        Raises:
            ValueError: If DAG validation fails.
        """
        with self._lock:
            state.validate_dag()
            state.rebuild_indexes()
            self._write_atomic(state)
            self._state = state

    def update(self, **kwargs: Any) -> WorkflowState:  # Time: O(n log n), Space: O(n)
        """Atomically update state fields.

        Args:
            **kwargs: Fields to update on WorkflowState.

        Returns:
            Updated WorkflowState.

        Raises:
            ValueError: If no state loaded.
        """
        with self._lock:
            state = self._ensure_state_loaded()

            # Validate task dicts at boundary
            match kwargs.get("tasks"):
                case dict() as tasks_dict:
                    validated: dict[str, Task] = {}
                    for tid, tdata in tasks_dict.items():
                        match tdata:
                            case dict():
                                validated[tid] = msgspec.convert(tdata, Task)
                            case Task():
                                validated[tid] = tdata
                            case _:
                                validated[tid] = tdata
                    kwargs["tasks"] = validated

            new_state = struct_replace(state, **kwargs)
            new_state.rebuild_indexes()
            self._write_atomic(new_state)
            self._state = new_state
            return new_state

    def claim_task(self, worker_id: str) -> ClaimResult:  # Time: O(1) amortized, Space: O(n)
        """Atomically claim next available task for worker.

        Idempotent: returns existing claim if worker already has one.

        Args:
            worker_id: Unique identifier for claiming worker.

        Returns:
            ClaimResult with task and retry/reclaim flags.

        Raises:
            ValueError: If worker_id is empty.
        """
        if not worker_id or not worker_id.strip():
            raise ValueError("Worker ID cannot be empty or whitespace-only")

        with self._lock:
            state = self._ensure_state_loaded()
            task = state.get_task_for_worker(worker_id)

            if task is None:
                return ClaimResult(task=None, is_retry=False, is_reclaim=False)

            # Compute flags atomically with claim
            was_mine = task.claimed_by == worker_id
            is_retry = was_mine and task.status == TaskStatus.RUNNING
            is_reclaim = not was_mine and task.status == TaskStatus.RUNNING and task.is_timed_out()

            # Copy-on-write: modify copy, persist, then update cache
            updated_task = struct_replace(
                task,
                started_at=datetime.now(UTC),
                status=TaskStatus.RUNNING,
                claimed_by=worker_id,
            )

            new_tasks = {**state.tasks, updated_task.id: updated_task}
            new_state = struct_replace(state, tasks=new_tasks)
            new_state.rebuild_indexes()

            self._write_atomic(new_state)
            self._state = new_state

            return ClaimResult(task=updated_task, is_retry=is_retry, is_reclaim=is_reclaim)

    def complete_task(self, task_id: str, worker_id: str) -> None:  # Time: O(n log n), Space: O(n)
        """Atomically mark task completed with ownership validation.

        Args:
            task_id: ID of task to complete.
            worker_id: ID of worker claiming completion.

        Raises:
            ValueError: If task not found or not owned by worker.
        """
        with self._lock:
            state = self._ensure_state_loaded()

            task = state.tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task not found: {task_id}")

            if task.claimed_by != worker_id:
                raise ValueError(
                    f"Task {task_id} not owned by {worker_id} "
                    f"(owned by {task.claimed_by or 'nobody'})"
                )

            updated_task = struct_replace(
                task,
                status=TaskStatus.COMPLETED,
                completed_at=datetime.now(UTC),
            )

            new_tasks = {**state.tasks, task_id: updated_task}
            new_state = struct_replace(state, tasks=new_tasks)
            new_state.rebuild_indexes()

            self._write_atomic(new_state)
            self._state = new_state

    def reset(self) -> None:  # Time: O(1), Space: O(1)
        """Clear all workflow state, deleting state file if present."""
        with self._lock:
            if self.state_file.exists():
                self.state_file.unlink()
            self._state = None
