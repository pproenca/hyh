# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Harness is an Autonomous Research Kernel with Thread-Safe Pull Engine - a daemon-based task orchestration system for managing workflow execution. It provides:
- Task state management with dependency-aware execution (DAG validation)
- Thread-safe operations for concurrent task handling
- Client-daemon architecture via Unix sockets
- Command execution runtimes (local and Docker)
- Git integration for safe operations

## Commands

```bash
# Setup
make install              # Install dependencies (uv sync --dev)
make install-global       # Install harness CLI globally (editable)

# Development
make dev                  # Start daemon
make shell                # Python REPL with harness loaded

# Testing
make test                 # Run all tests
make test-fast            # Tests without timeout
make test-file FILE=tests/harness/test_state.py  # Single file

# Run specific test by name
uv run pytest -k "test_claim"

# Code Quality
make lint                 # Check style (ruff + pyupgrade)
make typecheck            # ty
make format               # Auto-format
make check                # All checks (lint + typecheck + test)

# Performance
make benchmark            # Benchmark tests
make memcheck             # Memory profiling (memray)
```

## Architecture

```
src/harness/
├── client.py      # CLI client, sends RPC to daemon via Unix socket
├── daemon.py      # HarnessDaemon + HarnessHandler, processes RPC requests
├── state.py       # Task, WorkflowState, StateManager - core state machine
├── runtime.py     # Runtime abstraction (LocalRuntime, DockerRuntime)
├── plan.py        # Markdown plan parsing → PlanDefinition → WorkflowState
├── git.py         # Safe git execution with dangerous option validation
├── registry.py    # ProjectRegistry - thread-safe project hash storage
├── trajectory.py  # TrajectoryLogger - append-only execution logging
├── acp.py         # ACPEmitter - Agent Communication Protocol output
```

### Data Flow

1. **Client** (`harness <cmd>`) → Unix socket RPC → **Daemon**
2. **Daemon** dispatches to **HarnessHandler** methods
3. **StateManager** handles atomic task state transitions with file locking
4. **Runtime** executes commands (local subprocess or Docker container)

### Key Design Patterns

- **Pull-based task claiming**: Workers call `claim_task(worker_id)` to atomically claim next available task
- **DAG validation**: Dependency cycles detected before execution via `detect_cycle()`
- **Atomic file operations**: `StateManager._write_atomic()` uses temp file + rename
- **Thread safety**: `GLOBAL_EXEC_LOCK` serializes execution; state operations are lock-protected

## Code Style

- **Python 3.13+** (targets 3.14), uses modern syntax (`|` unions, `list[T]` generics)
- **Type hints required** on all functions (ANN rules enforced)
- **Use `msgspec.Struct`** for data classes, not dataclasses/Pydantic
- **Use `pathlib.Path`** for file operations (PTH rules)
- **Timezone-aware datetimes** required (DTZ rules)

### msgspec Struct Example

```python
from msgspec import Struct

class Task(Struct, forbid_unknown_fields=True):
    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()  # Use tuple, not list
```

### Enum Pattern

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
```

## Testing Patterns

Tests mirror source structure in `tests/harness/`. Key test categories:
- `test_state*.py` - State machine transitions
- `test_concurrency_audit.py`, `test_freethreading.py` - Thread safety
- `test_security_audit.py` - Input validation, git safety
- `test_performance.py`, `test_memory.py` - Benchmarks (marked `@pytest.mark.benchmark`, `@pytest.mark.memcheck`)

Use condition-based waiting (`wait_until`) instead of `time.sleep` for async tests.
