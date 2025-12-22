# Pydantic to msgspec Migration Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-22-pydantic-to-msgspec-migration.md` to implement task-by-task.

**Goal:** Replace Pydantic with msgspec for faster serialization and startup time, then remove Pydantic entirely.

**Architecture:** Surgical migration of 2 source files (`state.py`, `plan.py`) and 1 consumer (`daemon.py`). The client already has zero Pydantic imports. All 4 models in state.py and 2 models in plan.py will be converted to `msgspec.Struct`. Field validators become `__post_init__` methods. Model validators become factory classmethods or `__post_init__`. Private attributes become instance attributes set in `__post_init__`.

**Tech Stack:** Python 3.13t (free-threaded), msgspec, uv

---

## Critical Performance Patterns for Multithreaded Daemon

The daemon uses `ThreadingMixIn` with Python 3.13t (no GIL). Follow these msgspec best practices:

### 1. Reuse Encoders/Decoders (CRITICAL)

**DO NOT** create new encoder/decoder instances per request:
```python
# BAD - allocates internal state on every call
def handle_request():
    return msgspec.json.encode(state)  # Creates encoder each time!
```

**DO** create module-level or class-level encoder/decoder instances:
```python
# GOOD - reuse pre-allocated encoder/decoder
_JSON_ENCODER = msgspec.json.Encoder()
_STATE_DECODER = msgspec.json.Decoder(WorkflowState)

def handle_request():
    return _JSON_ENCODER.encode(state)  # Reuses encoder
```

### 2. Thread Safety of Encoders/Decoders

msgspec Encoder/Decoder instances are **thread-safe for concurrent reads** but the underlying buffer is not. For the daemon:
- Use separate encoder instances per thread, OR
- Use the module-level functions (`msgspec.json.encode()`) which handle this internally

Since we use `ThreadingMixIn`, each request gets its own thread. Using module-level functions is simpler and safe.

### 3. Exclude Private Fields from Serialization

Private fields (`_pending_deque`, `_pending_set`, `_worker_index`) must NOT be serialized. Options:
- Use `encode_hook` to filter fields, OR
- Use a custom `to_dict()` method that excludes them, OR
- Mark fields with `field(default=None)` and handle in `__post_init__`

We'll use the third approach - fields default to `None`, are initialized in `__post_init__`, and msgspec with `omit_defaults=True` won't serialize `None` values.

### 4. DateTime Handling

msgspec serializes `datetime` to ISO 8601 strings automatically. Decoding requires timezone-aware parsing. Use `msgspec.json.Decoder` with proper type hints.

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Add msgspec dependency |
| Group 2    | 2     | Migrate TaskStatus enum (no changes needed, just verify) |
| Group 3    | 3     | Migrate Task model (most complex) |
| Group 4    | 4     | Migrate WorkflowState model (has PrivateAttr indexes) |
| Group 5    | 5     | Migrate PendingHandoff and ClaimResult models (simple frozen structs) |
| Group 6    | 6     | Migrate PlanTaskDefinition and PlanDefinition models |
| Group 7    | 7     | Update daemon.py serialization calls |
| Group 8    | 8     | Remove Pydantic dependency and clean up imports |
| Group 9    | 9     | Run full test suite and lint/typecheck |
| Group 10   | 10    | Code Review |

---

### Task 1: Add msgspec dependency

**Files:**
- Modify: `pyproject.toml:7-9`

**Step 1: Write the test** (1 min)

No test needed - this is a dependency addition. We'll verify by importing.

**Step 2: Add msgspec to dependencies** (1 min)

In `pyproject.toml`, change line 8 from:
```toml
dependencies = [
    "pydantic>=2.0",
]
```

To:
```toml
dependencies = [
    "msgspec>=0.18",
]
```

**Step 3: Sync dependencies** (30 sec)

```bash
uv sync --dev
```

Expected: Dependencies install successfully.

**Step 4: Verify msgspec is importable** (30 sec)

```bash
uv run python -c "import msgspec; print(msgspec.__version__)"
```

Expected: Prints version like `0.18.x`

**Step 5: Commit** (30 sec)

```bash
git add pyproject.toml uv.lock
git commit -m "build: replace pydantic with msgspec dependency"
```

---

### Task 2: Verify TaskStatus enum works unchanged

**Files:**
- Read: `src/harness/state.py:89-99`

**Step 1: Verify enum is stdlib** (30 sec)

TaskStatus is a `str, Enum` subclass from stdlib. msgspec supports this natively. No changes needed.

**Step 2: Run existing enum tests** (30 sec)

```bash
uv run pytest tests/harness/test_state.py -k "TaskStatus or status" -v --timeout=30
```

Expected: All tests pass (TaskStatus is just an enum, no Pydantic dependency).

**Step 3: Commit verification** (30 sec)

No code changes - this is a verification step. Proceed to next task.

---

### Task 3: Migrate Task model to msgspec

**Files:**
- Modify: `src/harness/state.py:101-176`
- Test: `tests/harness/test_state.py`

**Step 1: Write the failing test for msgspec Task** (2 min)

The existing tests should work with msgspec. First, update imports and run to see failure.

Update `src/harness/state.py` imports (lines 26):

From:
```python
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator
```

To:
```python
from typing import Annotated
import msgspec
from msgspec import Meta, Struct, field
from msgspec.structs import replace as struct_replace
```

**Step 2: Run test to verify failure** (30 sec)

```bash
uv run pytest tests/harness/test_state.py::test_task_model_basic_validation -v --timeout=30
```

Expected: FAIL with import errors or `BaseModel` not defined.

**Step 3: Implement Task as msgspec.Struct** (5 min)

Replace the Task class (lines 101-176) with:

```python
# Type aliases with constraints
TaskId = Annotated[str, Meta(min_length=1)]
TimeoutSeconds = Annotated[int, Meta(ge=1, le=86400)]


class Task(Struct, forbid_unknown_fields=True, omit_defaults=True):
    """Individual task in workflow DAG.

    Immutable after creation except via StateManager methods.
    """

    # Class-level clock for testable timeout checking.
    # Tests can inject a mock clock via set_clock() to avoid real time.sleep().
    _clock: ClassVar[Callable[[], datetime]] = lambda: datetime.now(UTC)

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

    @classmethod
    def set_clock(cls, clock: Callable[[], datetime]) -> None:
        """Set custom clock for testing timeout behavior."""
        cls._clock = clock

    @classmethod
    def reset_clock(cls) -> None:
        """Reset to default system clock."""
        cls._clock = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        """Validate and normalize fields after initialization."""
        # Validate id not empty (was @field_validator)
        if not isinstance(self.id, str):
            raise TypeError(f"Task ID must be str, got {type(self.id).__name__}")
        stripped = self.id.strip()
        if not stripped:
            raise ValueError("Task ID cannot be empty or whitespace-only")
        # msgspec Structs are mutable by default, so we can fix the id
        if stripped != self.id:
            object.__setattr__(self, 'id', stripped)

        # Coerce dependencies to tuple (was @field_validator)
        if isinstance(self.dependencies, list):
            object.__setattr__(self, 'dependencies', tuple(self.dependencies))
        elif not isinstance(self.dependencies, tuple):
            raise TypeError(f"dependencies must be list or tuple, got {type(self.dependencies).__name__}")

    def is_timed_out(self) -> bool:
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
        elapsed = Task._clock() - started
        return elapsed.total_seconds() > self.timeout_seconds
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
uv run pytest tests/harness/test_state.py::test_task_model_basic_validation -v --timeout=30
```

Expected: PASS

**Step 5: Run all Task tests** (1 min)

```bash
uv run pytest tests/harness/test_state.py -k "task" -v --timeout=30
```

Expected: All task-related tests pass. Fix any failures before proceeding.

**Step 6: Commit** (30 sec)

```bash
git add src/harness/state.py
git commit -m "refactor(state): migrate Task model to msgspec.Struct"
```

---

### Task 4: Migrate WorkflowState model to msgspec

**Files:**
- Modify: `src/harness/state.py:178-348`
- Test: `tests/harness/test_state.py`

**Step 1: Write failing test** (1 min)

Existing tests should fail after Task migration if WorkflowState still uses Pydantic. Run:

```bash
uv run pytest tests/harness/test_state.py::test_workflow_state_v2_schema_with_tasks_dict -v --timeout=30
```

Expected: FAIL (WorkflowState still references BaseModel).

**Step 2: Implement WorkflowState as msgspec.Struct** (5 min)

Replace WorkflowState class (lines 178-348) with:

**IMPORTANT: Private fields pattern for msgspec**

msgspec doesn't have `PrivateAttr` like Pydantic. We use instance attributes set in `__post_init__` that are NOT declared as struct fields. This means they won't be serialized.

```python
class WorkflowState(Struct, forbid_unknown_fields=True, omit_defaults=True):
    """State for an active workflow execution with task DAG.

    Index invariants maintained by rebuild_indexes():
    - _pending_deque: FIFO of task IDs with status=PENDING, sorted by dep count
    - _pending_set: O(1) membership test for _pending_deque
    - _worker_index: Maps worker_id → task_id for O(1) lookup

    NOTE: Private indexes are instance attributes set in __post_init__, NOT struct fields.
    This ensures they are NOT serialized but ARE available at runtime.
    """

    tasks: dict[str, Task] = field(default_factory=dict)

    # Private indexes are NOT declared as fields - they're set in __post_init__
    # This is the msgspec equivalent of Pydantic's PrivateAttr

    def __post_init__(self) -> None:
        """Initialize indexes and normalize tasks input."""
        # Initialize private attrs as instance attributes (not struct fields)
        # These won't be serialized because they're not declared in the struct
        object.__setattr__(self, '_pending_deque', deque())
        object.__setattr__(self, '_pending_set', set())
        object.__setattr__(self, '_worker_index', {})

        # Normalize tasks from list[Task|dict] to dict[str, Task] (was @model_validator before)
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
            object.__setattr__(self, 'tasks', tasks_dict)

        # Rebuild indexes after initialization
        self.rebuild_indexes()

    def rebuild_indexes(self) -> None:
        """Rebuild all O(1) access indexes from canonical tasks dict.

        Called on: initial load, after updates, after deserialization.
        Sort is O(n log n) but amortized across many O(1) reads.
        """
        pending: list[str] = []
        self._worker_index.clear()

        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING:
                pending.append(task.id)
            elif task.status == TaskStatus.RUNNING and task.claimed_by:
                self._worker_index[task.claimed_by] = task.id

        pending.sort(key=lambda tid: len(self.tasks[tid].dependencies))
        # Clear and repopulate the deque and set (instance attrs, not struct fields)
        self._pending_deque.clear()
        self._pending_deque.extend(pending)
        self._pending_set.clear()
        self._pending_set.update(pending)

    def validate_dag(self) -> None:
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

    def get_claimable_task(self) -> Task | None:
        """Find next task eligible for claiming.

        Priority:
        1. Pending tasks with satisfied dependencies (O(1) from deque)
        2. Timed-out running tasks for reclaim (O(n) scan, rare path)

        Returns:
            Claimable Task or None if no work available.
        """
        rotations = 0
        max_rotations = len(self._pending_deque)

        while self._pending_deque and rotations <= max_rotations:
            task_id = self._pending_deque[0]

            if task_id not in self.tasks:
                self._pending_deque.popleft()
                self._pending_set.discard(task_id)
                max_rotations = len(self._pending_deque)
                continue

            task = self.tasks[task_id]
            if task.status != TaskStatus.PENDING:
                self._pending_deque.popleft()
                self._pending_set.discard(task_id)
                max_rotations = len(self._pending_deque)
                continue

            if self._are_deps_satisfied(task):
                return task

            self._pending_deque.popleft()
            self._pending_deque.append(task_id)
            rotations += 1

        for task in self.tasks.values():
            if (
                task.status == TaskStatus.RUNNING
                and task.is_timed_out()
                and self._are_deps_satisfied(task)
            ):
                return task

        return None

    def _are_deps_satisfied(self, task: Task) -> bool:
        """Check if all task dependencies are COMPLETED."""
        for dep_id in task.dependencies:
            dep_task = self.tasks.get(dep_id)
            if dep_task is None:
                raise ValueError(f"Missing dependency: {dep_id} (in {task.id})")
            if dep_task.status != TaskStatus.COMPLETED:
                return False
        return True

    def get_task_for_worker(self, worker_id: str) -> Task | None:
        """Get worker's current task (idempotent) or assign new claimable task."""
        if existing_tid := self._worker_index.get(worker_id):
            task = self.tasks.get(existing_tid)
            if task and task.status == TaskStatus.RUNNING and task.claimed_by == worker_id:
                return task
            del self._worker_index[worker_id]

        return self.get_claimable_task()
```

**Step 3: Run test to verify it passes** (30 sec)

```bash
uv run pytest tests/harness/test_state.py::test_workflow_state_v2_schema_with_tasks_dict -v --timeout=30
```

Expected: PASS

**Step 4: Run all WorkflowState tests** (1 min)

```bash
uv run pytest tests/harness/test_state.py -k "workflow" -v --timeout=30
```

Expected: All pass.

**Step 5: Commit** (30 sec)

```bash
git add src/harness/state.py
git commit -m "refactor(state): migrate WorkflowState to msgspec.Struct"
```

---

### Task 5: Migrate PendingHandoff and ClaimResult to msgspec

**Files:**
- Modify: `src/harness/state.py:351-371`
- Test: `tests/harness/test_state.py`

**Step 1: Write failing test** (30 sec)

```bash
uv run pytest tests/harness/test_state.py::test_pending_handoff_model -v --timeout=30
```

Expected: FAIL (still using BaseModel).

**Step 2: Implement as frozen msgspec.Struct** (2 min)

Replace PendingHandoff and ClaimResult (lines 351-371):

```python
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
```

**Step 3: Run test to verify it passes** (30 sec)

```bash
uv run pytest tests/harness/test_state.py::test_pending_handoff_model -v --timeout=30
```

Expected: PASS

**Step 4: Commit** (30 sec)

```bash
git add src/harness/state.py
git commit -m "refactor(state): migrate PendingHandoff and ClaimResult to msgspec"
```

---

### Task 6: Migrate PlanTaskDefinition and PlanDefinition to msgspec

**Files:**
- Modify: `src/harness/plan.py:11,38-76`
- Test: `tests/harness/test_plan.py`

**Step 1: Write failing test** (30 sec)

```bash
uv run pytest tests/harness/test_plan.py::test_plan_task_definition_basic -v --timeout=30
```

Expected: FAIL (imports still reference pydantic).

**Step 2: Update imports** (1 min)

Change line 11 from:
```python
from pydantic import BaseModel, Field
```

To:
```python
import msgspec
from msgspec import Struct, field
```

**Step 3: Implement PlanTaskDefinition and PlanDefinition** (3 min)

Replace lines 38-76:

```python
class PlanTaskDefinition(Struct, omit_defaults=True):
    description: str
    dependencies: list[str] = field(default_factory=list)
    timeout_seconds: int = 600
    instructions: str | None = None
    role: str | None = None


class PlanDefinition(Struct):
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
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
uv run pytest tests/harness/test_plan.py::test_plan_task_definition_basic -v --timeout=30
```

Expected: PASS

**Step 5: Run all plan tests** (1 min)

```bash
uv run pytest tests/harness/test_plan.py -v --timeout=30
```

Expected: All pass.

**Step 6: Commit** (30 sec)

```bash
git add src/harness/plan.py
git commit -m "refactor(plan): migrate PlanTaskDefinition and PlanDefinition to msgspec"
```

---

### Task 7: Update StateManager and daemon.py serialization (Thread-Safe Patterns)

**Files:**
- Modify: `src/harness/state.py:373-585` (StateManager class)
- Modify: `src/harness/daemon.py:100,150,165,227`
- Test: `tests/harness/test_daemon.py`

**CRITICAL: Thread-Safety for Python 3.13t Free-Threaded Runtime**

The daemon uses `ThreadingMixIn` with true parallel threads. msgspec encoder/decoder instances share internal buffers that are NOT thread-safe for concurrent writes. Use one of:
1. Module-level functions (`msgspec.json.encode()`) - handles thread-safety internally
2. Thread-local encoder instances
3. Per-call encoder creation (slower, avoid in hot paths)

We'll use option 1 (module-level functions) for simplicity since our hot path is state persistence, not RPC encoding.

**Step 1: Update StateManager with module-level decoder** (3 min)

In `state.py`, add after imports (around line 30):

```python
# Module-level decoder for WorkflowState - thread-safe for concurrent decoding
# Reusing decoder avoids repeated schema compilation overhead
_WORKFLOW_STATE_DECODER: msgspec.json.Decoder[WorkflowState] | None = None

def _get_state_decoder() -> msgspec.json.Decoder[WorkflowState]:
    """Lazy-initialize decoder to avoid circular import issues."""
    global _WORKFLOW_STATE_DECODER
    if _WORKFLOW_STATE_DECODER is None:
        _WORKFLOW_STATE_DECODER = msgspec.json.Decoder(WorkflowState)
    return _WORKFLOW_STATE_DECODER
```

**Step 2: Update _write_atomic method** (2 min)

In `state.py`, the `_write_atomic` method (line 413-428) currently uses:
```python
content = state.model_dump_json(indent=2, exclude_none=True)
```

Replace the entire method with:
```python
def _write_atomic(self, state: WorkflowState) -> None:  # Time: O(n), Space: O(n)
    """Atomically persist state via tmp-fsync-rename. Must hold lock.

    Pattern: write to .tmp → fsync → rename over target.
    Rename is atomic on POSIX; provides crash consistency.

    Note: We exclude private index fields (_pending_deque, _pending_set,
    _worker_index) by using a custom enc_hook. These are rebuilt on load.
    """
    self.state_file.parent.mkdir(parents=True, exist_ok=True)

    # Use msgspec.json.encode - module-level function is thread-safe
    # Private fields (starting with _) are excluded via enc_hook
    def enc_hook(obj: Any) -> Any:
        if isinstance(obj, (deque, set)):
            return None  # Exclude private index structures
        raise NotImplementedError(f"Cannot encode {type(obj)}")

    # Encode state, excluding None values (private fields default to None)
    content = msgspec.json.encode(state).decode('utf-8')
    temp_file = self.state_file.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    temp_file.rename(self.state_file)
```

**Step 3: Update _ensure_state_loaded and load methods** (2 min)

Replace `.model_validate(data)` calls with msgspec decoder:

In `_ensure_state_loaded` (line 409):
```python
# Old: self._state = WorkflowState.model_validate(data)
# New:
self._state = msgspec.convert(data, WorkflowState)
```

In `load` (line 442):
```python
# Old: self._state = WorkflowState.model_validate(data)
# New:
self._state = msgspec.convert(data, WorkflowState)
```

In `update` (line 482):
```python
# Old: validated[tid] = Task.model_validate(tdata)
# New:
validated[tid] = msgspec.convert(tdata, Task)
```

**Step 4: Update model_copy calls with struct_replace** (2 min)

The `model_copy(update={...})` pattern becomes `struct_replace(obj, **updates)`:

In `update` (line 489):
```python
# Old: new_state = state.model_copy(update=kwargs)
# New:
new_state = struct_replace(state, **kwargs)
```

In `claim_task` (lines 525-534):
```python
# Old: updated_task = task.model_copy(update={...})
# New:
updated_task = struct_replace(
    task,
    started_at=datetime.now(UTC),
    status=TaskStatus.RUNNING,
    claimed_by=worker_id,
)

# Old: new_state = state.model_copy(update={"tasks": new_tasks})
# New:
new_state = struct_replace(state, tasks=new_tasks)
```

In `complete_task` (lines 565-573):
```python
# Old: updated_task = task.model_copy(update={...})
# New:
updated_task = struct_replace(
    task,
    status=TaskStatus.COMPLETED,
    completed_at=datetime.now(UTC),
)

# Old: new_state = state.model_copy(update={"tasks": new_tasks})
# New:
new_state = struct_replace(state, tasks=new_tasks)
```

**Step 5: Update daemon.py RPC serialization** (2 min)

The daemon converts state objects to JSON-safe dicts for RPC responses. Use `msgspec.to_builtins()`:

Add import at top of daemon.py:
```python
import msgspec
```

Replace `.model_dump(mode="json")` with `msgspec.to_builtins(obj)`:

Line 100: `state.model_dump(mode="json")` → `msgspec.to_builtins(state)`
Line 150: `t.model_dump(mode="json")` → `msgspec.to_builtins(t)`
Line 165: `updated.model_dump(mode="json")` → `msgspec.to_builtins(updated)`
Line 227: `task.model_dump(mode="json")` → `msgspec.to_builtins(task)`

**Step 6: Run daemon tests** (1 min)

```bash
uv run pytest tests/harness/test_daemon.py -v --timeout=30
```

Expected: All pass.

**Step 7: Run threading/concurrency tests** (1 min)

```bash
uv run pytest tests/harness/test_concurrency_audit.py tests/harness/test_freethreading.py -v --timeout=30
```

Expected: All pass - verifies thread-safety under Python 3.13t.

**Step 8: Commit** (30 sec)

```bash
git add src/harness/state.py src/harness/daemon.py
git commit -m "refactor(daemon): update serialization to use msgspec with thread-safe patterns"
```

---

### Task 8: Remove Pydantic dependency and clean up imports

**Files:**
- Modify: `pyproject.toml:8`
- Modify: `src/harness/state.py:26`
- Modify: `src/harness/plan.py:11`

**Step 1: Verify Pydantic is no longer imported** (1 min)

```bash
uv run grep -r "from pydantic" src/harness/
uv run grep -r "import pydantic" src/harness/
```

Expected: No matches (if any remain, fix them first).

**Step 2: Remove Pydantic from pyproject.toml** (30 sec)

The dependency was already replaced in Task 1. Verify:

```bash
grep pydantic pyproject.toml
```

Expected: No matches.

**Step 3: Sync and verify** (30 sec)

```bash
uv sync --dev
```

Expected: No pydantic in lock file.

**Step 4: Commit** (30 sec)

```bash
git add pyproject.toml uv.lock src/harness/state.py src/harness/plan.py
git commit -m "chore: remove pydantic dependency, migration complete"
```

---

### Task 9: Run full test suite and lint/typecheck

**Files:**
- All test files

**Step 1: Run full test suite** (5 min)

```bash
make test
```

Expected: All tests pass within 30s timeout per test.

**Step 2: Run lint** (1 min)

```bash
make lint
```

Expected: No errors.

**Step 3: Run typecheck** (1 min)

```bash
make typecheck
```

Expected: No errors.

**Step 4: Fix any failures** (variable)

If any tests, lint, or typecheck failures occur, fix them before proceeding.

**Step 5: Commit any fixes** (30 sec)

```bash
git add -A
git commit -m "fix: address test/lint/typecheck issues from msgspec migration"
```

---

### Task 10: Code Review

Review all changes made during the migration:

1. Verify all Pydantic imports are removed
2. Verify msgspec patterns are idiomatic
3. Verify test coverage is maintained
4. Verify no performance regressions
5. Check for any remaining TODO comments

```bash
git diff main..HEAD --stat
git log --oneline main..HEAD
```