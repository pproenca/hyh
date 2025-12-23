# msgspec Immutability Audit

> **Goal:** Ensure all msgspec.Struct types follow Python 3.14 free-threading best practices with consistent frozen/immutable configuration.

**Date:** 2025-12-23

---

## Context

The hyh project is multithreaded (daemon with socket handlers) and targets Python 3.14. Python 3.14's free-threading guidance recommends:

> Use `threading.Lock` or other synchronization primitives instead of relying on internal locks of built-in types.

Having mutable state inside structs creates hidden mutation points that violate this principle. The codebase already uses `struct_replace()` for all modifications, making the transition to fully frozen structs straightforward.

## Current State

| File          | Struct               | `frozen` | `forbid_unknown_fields` | Issues                      |
| ------------- | -------------------- | -------- | ----------------------- | --------------------------- |
| state.py      | `Task`               | No       | Yes                     | Should be frozen            |
| state.py      | `WorkflowState`      | No       | Yes                     | Has internal mutable caches |
| state.py      | `PendingHandoff`     | Yes      | Yes                     | Correct                     |
| state.py      | `ClaimResult`        | Yes      | Yes                     | Correct                     |
| plan.py       | `_TaskData`          | No       | No                      | Missing both                |
| plan.py       | `PlanTaskDefinition` | No       | No                      | Missing both                |
| plan.py       | `PlanDefinition`     | No       | No                      | Missing both                |
| runtime.py    | `ExecutionResult`    | Yes      | Yes                     | Correct                     |
| daemon.py     | All 24 types         | Yes      | Yes                     | Correct                     |
| tests/helpers | `LockInfo`           | No       | No                      | Missing both                |

## Problems Identified

### 1. WorkflowState Internal Caches

`WorkflowState` has three internal mutable caches:

- `_pending_deque` — round-robin rotation of pending tasks
- `_pending_set` — **dead code** (never read, only `.discard()` called)
- `_worker_index` — worker→task lookup

These are rebuilt from scratch after every `struct_replace()`, so:

- Round-robin position doesn't persist across claims
- O(1) index is rebuilt via O(n) scan anyway
- Hidden mutable state violates free-threading guidance

### 2. plan.py Types Lack Validation

`PlanTaskDefinition` and `PlanDefinition` are serialized to files but lack `forbid_unknown_fields=True`, allowing typos/invalid fields to pass silently.

## Design

### Principle

All structs should be `frozen=True, forbid_unknown_fields=True` with no exceptions.

### Changes

#### state.py

**Task** — add `frozen=True`:

```python
class Task(Struct, frozen=True, forbid_unknown_fields=True):
    ...
```

**WorkflowState** — add `frozen=True`, remove internal caches:

```python
class WorkflowState(Struct, frozen=True, forbid_unknown_fields=True, omit_defaults=True):
    tasks: dict[str, Task] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Only keep list→dict conversion
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

    def get_claimable_task(self) -> Task | None:
        """Find first pending task with satisfied dependencies."""
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING and self._are_deps_satisfied(task):
                return task
        # Check for timed-out tasks to reclaim
        for task in self.tasks.values():
            if task.status == TaskStatus.RUNNING and task.is_timed_out():
                return task
        return None

    def get_task_for_worker(self, worker_id: str) -> Task | None:
        """Find task owned by worker, or get a new claimable task."""
        for task in self.tasks.values():
            if task.status == TaskStatus.RUNNING and task.claimed_by == worker_id:
                return task
        return self.get_claimable_task()

    # Remove: rebuild_indexes(), _pending_deque, _pending_set, _worker_index
```

#### plan.py

```python
class _TaskData(Struct, frozen=True, forbid_unknown_fields=True):
    description: str
    instructions: str
    dependencies: list[str]

class PlanTaskDefinition(Struct, frozen=True, forbid_unknown_fields=True, omit_defaults=True):
    description: str
    dependencies: list[str] = field(default_factory=list)
    timeout_seconds: int = 600
    instructions: str | None = None
    role: str | None = None

class PlanDefinition(Struct, frozen=True, forbid_unknown_fields=True):
    goal: str
    tasks: dict[str, PlanTaskDefinition]
```

#### tests/helpers/lock_tracker.py

```python
class LockInfo(Struct, frozen=True, forbid_unknown_fields=True):
    name: str
    priority: int
    lock: threading.Lock
```

### Removals

From `WorkflowState`:

- `_pending_deque`, `_pending_set`, `_worker_index` attributes
- `rebuild_indexes()` method
- `hasattr` checks in `rebuild_indexes()`

From `WorkflowStateStore`:

- All `rebuild_indexes()` calls after `struct_replace()`

### Behavior Changes

| Before                     | After                     | Impact                               |
| -------------------------- | ------------------------- | ------------------------------------ |
| Round-robin task selection | First-available selection | Round-robin wasn't persisting anyway |
| O(1) worker→task lookup    | O(n) scan                 | Negligible for <1000 tasks           |
| `_pending_set` tracking    | Removed                   | Was dead code                        |

## Trade-offs

**Pros:**

- Truly immutable structs — thread-safe by design
- Simpler code — ~50 lines removed
- Removes dead code (`_pending_set`)
- Consistent configuration across all structs
- Catches invalid plan files via `forbid_unknown_fields`

**Cons:**

- O(n) vs O(1) for worker lookup — acceptable for typical workflow sizes
- Loses round-robin fairness — but it wasn't working correctly anyway

## Testing

Existing tests should pass without modification since:

- External APIs unchanged
- Serialization format unchanged
- Lock protection unchanged

Run full test suite after changes:

```bash
make check
```
