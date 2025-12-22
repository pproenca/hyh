# Harness - Project Overview

## Purpose
Harness is a thread-safe daemon for orchestrating development workflows. It uses a Unix socket server with a "dumb client" architecture: the CLI client has zero validation logic (stdlib only, <50ms startup), while all validation happens in the daemon.

## Architecture
- **Client**: Thin CLI (`src/harness/client.py`) - stdlib only, minimal logic
- **Daemon**: Long-running process (`src/harness/daemon.py`) - handles all business logic
- **Communication**: Unix socket (JSON protocol)
- **State**: Workflow state management (`src/harness/state.py`) with thread-safe operations

## Tech Stack
- **Runtime**: Python 3.13t (free-threaded, no GIL)
- **Build System**: uv (with PEP 735 dependency-groups)
- **Serialization**: msgspec (zero-copy, high performance)
- **Testing**: pytest with hypothesis, pytest-benchmark, pytest-memray
- **Linting/Formatting**: ruff
- **Type Checking**: mypy

## Dependencies
- Runtime: `msgspec>=0.18`
- Dev: pytest, ruff, mypy, pre-commit, pyupgrade, hypothesis, time-machine, pytest-benchmark, pytest-memray

## Project Structure
```
src/harness/
├── __init__.py       # Package exports
├── __main__.py       # Entry point
├── client.py         # CLI client (thin, stdlib only)
├── daemon.py         # Unix socket daemon
├── state.py          # Workflow state management (msgspec Structs)
├── registry.py       # Service registry
├── runtime.py        # Command execution layer
├── plan.py           # Plan management
├── trajectory.py     # Execution trajectory tracking
├── git.py            # Git operations
└── acp.py            # Agent communication protocol

tests/harness/
├── conftest.py       # Shared fixtures
├── helpers/          # Test utilities
└── test_*.py         # Test modules
```

## Entry Points
- `harness` CLI command (via `project.scripts`)
- `python -m harness.daemon` for development daemon
