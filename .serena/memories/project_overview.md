# Harness Project Overview

## Purpose
Autonomous Research Kernel for dev workflows - a thread-safe pull engine for task orchestration.

## Tech Stack
- **Runtime:** Python 3.13t (free-threaded, no GIL)
- **Version:** 2.0.0
- **Validation:** Pydantic v2
- **Testing:** pytest with pytest-timeout (30s default)
- **Linting:** ruff (target py314, strict)
- **Type Checking:** mypy (strict mode)
- **Package Manager:** uv

## Architecture (Clean Architecture)
```
src/harness/
├── client.py       # Dumb client (stdlib only, <50ms startup)
├── daemon.py       # Unix socket server, task claim/execute
├── state.py        # JSON persistence, DAG-based task state
├── trajectory.py   # JSONL event logging with O(1) tail
├── runtime.py      # LocalRuntime, DockerRuntime, PathMapper
├── git.py          # Git operations via runtime abstraction
├── plan.py         # Plan parsing/management (Markdown → WorkflowState)
├── acp.py          # ACP emitter (async event publication)
└── __main__.py     # Entry point
```

## Key Classes

### state.py
- `TaskStatus` - Literal type for task states
- `Task` - Pydantic model with `is_timed_out()` method
- `WorkflowState` - DAG container with `validate_dag()`, `get_claimable_task()`, `get_task_for_worker()`
- `PendingHandoff` - Handoff coordination model
- `ClaimResult` - Task claim result wrapper
- `StateManager` - Thread-safe persistence with atomic writes, `claim_task()`, `complete_task()`
- `detect_cycle()` - DFS cycle detection for DAG validation

### daemon.py
- `HarnessHandler` - Request dispatcher with handlers for all RPC commands
- `HarnessDaemon` - ThreadingMixIn Unix socket server with lock file

### runtime.py
- `Runtime` (Protocol) - Abstract execution interface
- `LocalRuntime` - Direct subprocess execution
- `DockerRuntime` - Container execution with UID mapping
- `PathMapper`, `IdentityMapper`, `VolumeMapper` - Path translation
- `GLOBAL_EXEC_LOCK` - Serialization for git operations

### client.py
Commands: `ping`, `get_state`, `update_state`, `git`, `session_start`, `check_state`, `check_commit`, `shutdown`, `task_claim`, `task_complete`, `exec`, `worker_id`, `plan_import`, `plan_template`

### plan.py
- `PlanTaskDefinition` - Task definition from plan file
- `PlanDefinition` - Full plan with `validate_dag()`, `to_workflow_state()`
- `parse_plan_content()` - Markdown plan parser
- `get_plan_template()` - Template generator

### trajectory.py
- `TrajectoryLogger` - JSONL append-only logger with O(1) `tail()` via reverse seek

### acp.py
- `ACPEmitter` - Background thread event emitter with queue

## Data Flow
1. `client.py` → Unix socket → `daemon.py` (Pydantic validation)
2. `daemon.py` → `state.py` (thread-safe state mutations)
3. `daemon.py` → `trajectory.py` (append-only logging)
4. `daemon.py` → `runtime.py` → subprocess (command execution)

## Key Invariants
- Client has ZERO validation logic - all Pydantic models live in daemon/state
- No asyncio - use `socketserver.ThreadingMixIn`
- Blocking I/O acceptable inside threads
- Use `time.monotonic()` for all durations (never `time.time()`)
- Lock hierarchy: State → Trajectory → Execution (never reversed)
