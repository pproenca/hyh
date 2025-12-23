# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**hyh** (hold your horses) is a CLI orchestration tool for agentic workflows. It coordinates tasks with claude-code and AI agents through a daemon-based task management system using Unix socket RPC.

## Commands

```bash
make install          # Install dependencies
make test             # Run all tests
make test-file FILE=tests/hyh/test_state.py  # Run single test file
make lint             # Check code style (no auto-fix)
make typecheck        # Run ty type checker
make format           # Auto-format code
make check            # Run lint + typecheck + test
make dev              # Start daemon in development mode
```

## Architecture

```text
Client (hyh CLI) ──Unix Socket RPC──► Daemon (per-project)
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    │                      │                      │
              WorkflowStateStore      Runtime              ACPEmitter
              (atomic task ops)    (cmd execution)      (agent protocol)
```

**Key modules:**

- `client.py` - CLI entry point, RPC client, daemon spawning
- `daemon.py` - Unix socket server handling RPC requests
- `state.py` - `Task` and `WorkflowState` structs, `WorkflowStateStore` for atomic operations
- `runtime.py` - Command execution with mutex protection
- `plan.py` - Markdown plan parsing into task DAG

**Data flow:** Client sends JSON-RPC over Unix socket → Daemon routes to handler → State mutations via `WorkflowStateStore.update()` with lock → Response back to client

## Code Conventions

Use **msgspec.Struct** (not dataclasses):

```python
class Task(Struct, forbid_unknown_fields=True):
    id: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()  # tuples, not lists
```

Type hints mandatory everywhere. Use `Final` for immutable attributes, `ClassVar` for class-level. Always use `datetime.now(UTC)` for timezone-aware datetimes.

## Tech Stack

- Python 3.13+ (targeting 3.14 freethreaded)
- uv for package management
- msgspec for serialization
- ruff for linting/formatting
- ty for type checking
