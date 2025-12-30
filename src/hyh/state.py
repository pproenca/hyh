import json
import os
import threading
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

import msgspec
from msgspec import Meta, Struct, field
from msgspec.structs import replace as struct_replace


def detect_cycle(graph: dict[str, Sequence[str]]) -> str | None:
    white: Final = 0
    gray: Final = 1
    black: Final = 2

    color: dict[str, int] = {node: white for node in graph}

    for start_node in graph:
        if color[start_node] != white:
            continue

        stack: list[tuple[str, Iterator[str], bool]] = [
            (start_node, iter(graph.get(start_node, [])), True)
        ]

        while stack:
            node, neighbors_iter, is_entering = stack.pop()

            if is_entering:
                node_color = color.get(node, white)
                if node_color == gray:
                    return node
                if node_color == black:
                    continue
                color[node] = gray

                stack.append((node, neighbors_iter, False))
            else:
                try:
                    neighbor = next(neighbors_iter)

                    stack.append((node, neighbors_iter, False))
                    if color.get(neighbor, white) == gray:
                        return neighbor
                    if color.get(neighbor, white) == white:
                        stack.append((neighbor, iter(graph.get(neighbor, [])), True))
                except StopIteration:
                    color[node] = black

    return None


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


TimeoutSeconds = Annotated[int, Meta(ge=1, le=86400)]


class Task(Struct, frozen=True, forbid_unknown_fields=True):
    id: str
    description: str

    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None
    claimed_by: str | None = None
    timeout_seconds: TimeoutSeconds = 600
    instructions: str | None = None
    role: str | None = None

    # TaskPacket extended fields
    model: str | None = None
    files_in_scope: tuple[str, ...] = ()
    files_out_of_scope: tuple[str, ...] = ()
    input_context: str = ""
    output_contract: str = ""
    constraints: str = ""
    tools: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    success_criteria: str = ""
    artifacts_to_read: tuple[str, ...] = ()
    artifacts_to_write: tuple[str, ...] = ()

    _clock: ClassVar[Callable[[], datetime]] = lambda: datetime.now(UTC)

    @classmethod
    def set_clock(cls, clock: Callable[[], datetime]) -> None:
        cls._clock = clock

    @classmethod
    def reset_clock(cls) -> None:
        cls._clock = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        if isinstance(self.id, str):
            stripped = self.id.strip()
            if not stripped:
                raise ValueError("Task ID cannot be empty or whitespace-only")
            if stripped != self.id:
                object.__setattr__(self, "id", stripped)

        if isinstance(self.dependencies, list):
            object.__setattr__(self, "dependencies", tuple(self.dependencies))

    def is_timed_out(self) -> bool:
        if self.status != TaskStatus.RUNNING or self.started_at is None:
            return False

        started = (
            self.started_at
            if self.started_at.tzinfo is not None
            else self.started_at.replace(tzinfo=UTC)
        )
        elapsed = Task._clock() - started
        return elapsed.total_seconds() > self.timeout_seconds


class WorkflowState(Struct, frozen=True, forbid_unknown_fields=True, omit_defaults=True):
    tasks: dict[str, Task] = field(default_factory=dict)

    def __post_init__(self) -> None:
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

    def validate_dag(self) -> None:
        task_ids = set(self.tasks.keys())

        for task_id, task in self.tasks.items():
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise ValueError(f"Missing dependency: {dep} (required by {task_id})")

        graph = {tid: list(t.dependencies) for tid, t in self.tasks.items()}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Dependency cycle detected at: {cycle_node}")

    def get_claimable_task(self) -> Task | None:
        """Find first pending task with satisfied dependencies."""
        # First pass: find pending tasks with satisfied deps
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING and self._are_deps_satisfied(task):
                return task

        # Second pass: find timed-out running tasks to reclaim
        for task in self.tasks.values():
            if (
                task.status == TaskStatus.RUNNING
                and task.is_timed_out()
                and self._are_deps_satisfied(task)
            ):
                return task

        return None

    def _are_deps_satisfied(self, task: Task) -> bool:
        for dep_id in task.dependencies:
            dep_task = self.tasks.get(dep_id)
            if dep_task is None:
                raise ValueError(f"Missing dependency: {dep_id} (in {task.id})")
            if dep_task.status != TaskStatus.COMPLETED:
                return False
        return True

    def get_task_for_worker(self, worker_id: str) -> Task | None:
        """Find task owned by worker, or get a new claimable task."""
        for task in self.tasks.values():
            if task.status == TaskStatus.RUNNING and task.claimed_by == worker_id:
                return task
        return self.get_claimable_task()


class PendingHandoff(Struct, frozen=True, forbid_unknown_fields=True):
    mode: Literal["sequential", "subagent"]
    plan: str


class ClaimResult(Struct, frozen=True, forbid_unknown_fields=True):
    task: Task | None = None
    is_retry: bool = False
    is_reclaim: bool = False


class WorkflowStateStore:
    __slots__ = ("_state", "_state_lock", "state_file", "worktree_root")

    def __init__(self, worktree_root: Path) -> None:
        self.worktree_root: Final[Path] = Path(worktree_root)
        self.state_file: Final[Path] = self.worktree_root / ".claude" / "dev-workflow-state.json"
        self._state: WorkflowState | None = None
        self._state_lock: Final[threading.Lock] = threading.Lock()

    def _ensure_state_loaded(self) -> WorkflowState:
        if self._state is not None:
            return self._state

        if not self.state_file.exists():
            raise ValueError("No workflow state: file not found and no cached state")

        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self._state = msgspec.convert(data, WorkflowState)
        return self._state

    def _write_atomic(self, state: WorkflowState) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        content = msgspec.json.encode(state).decode("utf-8")
        temp_file = self.state_file.with_suffix(".tmp")

        with temp_file.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        temp_file.rename(self.state_file)

    def load(self) -> WorkflowState | None:
        with self._state_lock:
            if not self.state_file.exists():
                self._state = None
                return None

            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self._state = msgspec.convert(data, WorkflowState)
            return self._state

    def save(self, state: WorkflowState) -> None:
        with self._state_lock:
            state.validate_dag()
            self._write_atomic(state)
            self._state = state

    def update(self, **kwargs: Any) -> WorkflowState:
        with self._state_lock:
            state = self._ensure_state_loaded()

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
            self._write_atomic(new_state)
            self._state = new_state
            return new_state

    def claim_task(self, worker_id: str) -> ClaimResult:
        if not worker_id or not worker_id.strip():
            raise ValueError("Worker ID cannot be empty or whitespace-only")

        with self._state_lock:
            state = self._ensure_state_loaded()
            task = state.get_task_for_worker(worker_id)

            if task is None:
                return ClaimResult(task=None, is_retry=False, is_reclaim=False)

            was_mine = task.claimed_by == worker_id
            is_retry = was_mine and task.status == TaskStatus.RUNNING
            is_reclaim = not was_mine and task.status == TaskStatus.RUNNING and task.is_timed_out()

            updated_task = struct_replace(
                task,
                started_at=datetime.now(UTC),
                status=TaskStatus.RUNNING,
                claimed_by=worker_id,
            )

            new_tasks = {**state.tasks, updated_task.id: updated_task}
            new_state = struct_replace(state, tasks=new_tasks)

            self._write_atomic(new_state)
            self._state = new_state

            return ClaimResult(task=updated_task, is_retry=is_retry, is_reclaim=is_reclaim)

    def complete_task(self, task_id: str, worker_id: str, *, force: bool = False) -> None:
        with self._state_lock:
            state = self._ensure_state_loaded()

            task = state.tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task not found: {task_id}")

            if not force and task.claimed_by != worker_id:
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

            self._write_atomic(new_state)
            self._state = new_state

    def reset(self) -> None:
        with self._state_lock:
            if self.state_file.exists():
                self.state_file.unlink()
            self._state = None
