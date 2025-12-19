# Harness Project Overview

## Purpose
Autonomous Research Kernel for dev workflows - a thread-safe pull engine for task orchestration.

## Tech Stack
- **Runtime:** Python 3.13t (free-threaded, no GIL)
- **Validation:** Pydantic v2
- **Testing:** pytest with pytest-timeout
- **Linting:** ruff (strict, extensive rules)
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
├── plan.py         # Plan parsing/management
├── acp.py          # ACP (Agent Communication Protocol)
└── __main__.py     # Entry point
```

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
