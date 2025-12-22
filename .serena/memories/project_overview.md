# Project Overview: Harness

## Description
Autonomous Research Kernel with Thread-Safe Pull Engine - a daemon-based task orchestration system.

## Purpose
Harness is a task management and workflow orchestration system that provides:
- Task state management (pending, running, completed, failed)
- Dependency-aware task execution (DAG validation)
- Thread-safe operations for concurrent task handling
- Git integration for worktree management
- Command execution runtime
- Client-daemon architecture via Unix sockets

## Tech Stack
- **Python**: 3.13+ (targets 3.14)
- **Package Manager**: uv (modern Python package manager)
- **Serialization**: msgspec (fast struct-based serialization)
- **Testing**: pytest (with hypothesis, time-machine, pytest-benchmark, pytest-memray)
- **Linting**: ruff (all-in-one linter and formatter)
- **Type Checking**: mypy
- **Pre-commit**: pyupgrade hook for Python modernization

## Architecture
```
src/harness/
├── __init__.py      # Package exports
├── __main__.py      # Entry point
├── client.py        # CLI client
├── daemon.py        # Unix socket daemon server
├── state.py         # Task and WorkflowState models
├── runtime.py       # Command execution runtime
├── plan.py          # Plan parsing/management
├── git.py           # Git operations
├── registry.py      # Worker registry
├── trajectory.py    # Execution trajectory tracking
└── acp.py           # Agent Communication Protocol
```

## Entry Points
- `harness` CLI command (defined in pyproject.toml project.scripts)
- `python -m harness.daemon` to start daemon
