# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Harness** is a thread-safe daemon for orchestrating dev workflows. It uses a Unix socket server with a "dumb client" architecture: the CLI client has zero validation logic (stdlib only, <50ms startup), while all Pydantic validation happens in the daemon.

- **Runtime:** Python 3.13t (free-threaded, no GIL)
- **Build System:** uv

## Development Commands

```bash
make install              # Install dependencies (uv sync --dev)
make test                 # Run all tests (30s timeout per test)
make test-fast            # Run tests without timeout
make test-file FILE=tests/harness/test_state.py  # Run specific test file
pytest tests/harness/test_state.py::test_claim_task_atomic -v  # Run specific test
make check                # Run lint + typecheck + test
make lint                 # ruff check + format check
make typecheck            # mypy strict mode
make format               # Auto-format with ruff
```

## Architecture

```
Client (stdlib only) → Unix Socket → Daemon (ThreadingMixIn) → State/Trajectory/Runtime

src/harness/
├── client.py       # CLI client, auto-spawns daemon, zero validation
├── daemon.py       # Unix socket server, command handlers
├── state.py        # Pydantic models (Task, WorkflowState), StateManager
├── trajectory.py   # JSONL append-only event logger with O(1) tail
├── runtime.py      # LocalRuntime, DockerRuntime (subprocess abstraction)
├── git.py          # Thread-safe git operations with mutex
├── plan.py         # Markdown plan parser → WorkflowState
├── registry.py     # Multi-project isolation (socket per worktree)
└── acp.py          # Non-blocking telemetry emitter
```

**Data Flow:**
1. `client.py` packages argv, sends JSON to Unix socket
2. `daemon.py` validates with Pydantic, executes command
3. State mutations go through `StateManager` (thread-safe, atomic writes)
4. Events logged to `trajectory.py` (JSONL, crash-resilient)

## Key Patterns

### Lock Hierarchy (Deadlock Prevention)
Acquire in this order only, never reversed:
1. `StateManager._lock` (highest priority)
2. `TrajectoryLogger._lock`
3. `GLOBAL_EXEC_LOCK` (lowest, only for git operations)

### Release-Then-Log Pattern
```python
# CORRECT: Release state lock before I/O
with self._lock:
    result = self._mutate_state()
# Lock released, now safe to log
self._trajectory.append(event)

# WRONG: I/O while holding lock causes convoy effect
with self._lock:
    result = self._mutate_state()
    self._trajectory.append(event)  # Bad!
```

### Atomic Persistence
All file writes use tmp-fsync-rename:
```python
tmp = path.with_suffix('.tmp')
with open(tmp, 'w') as f:
    f.write(content)
    f.flush()
    os.fsync(f.fileno())
tmp.rename(path)  # POSIX atomic
```

### Idempotent Task Claims
`claim_task(worker_id)` returns the same task on retry. Timestamps are renewed to prevent lease expiration.

## Concurrency Doctrine

- **No asyncio** - Use `ThreadingMixIn` with blocking I/O. Python 3.13t makes real threading efficient.
- **Threads are compute units** - Let the OS schedule; blocking I/O is acceptable inside threads.
- **Use `time.monotonic()`** for all durations, never `time.time()`.
- **Use `datetime.now(UTC)`** for absolute time, always timezone-aware.

## Client Constraints

The client imports **only stdlib**: `sys`, `json`, `socket`, `os`, `subprocess`, `time`, `argparse`, `pathlib`, `hashlib`.

No Pydantic, no validation logic. Startup must be <50ms for git hook compatibility.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HARNESS_SOCKET` | `~/.harness/sockets/{hash}.sock` | Unix socket path |
| `HARNESS_WORKTREE` | Auto-detect | Override git root |
| `HARNESS_TIMEOUT` | `5` seconds | Daemon spawn timeout |

## RPC Protocol

JSON-over-Unix-socket with newline delimiters.

**Request:** `{"command": "<name>", ...fields}`
**Response:** `{"status": "ok"|"error", "data": {...}, "message": "..."}`

**Commands:** `get_state`, `status`, `task_claim`, `task_complete`, `exec`, `git`, `plan_import`, `plan_reset`, `ping`, `shutdown`
