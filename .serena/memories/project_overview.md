# hyh - Project Overview

## Purpose

CLI orchestration tool for agentic workflows. Coordinates tasks with claude-code, AI agents, and development tools through a daemon-based task management system.

## Key Features

- **Task orchestration** - DAG-based dependency resolution with cycle detection
- **Thread-safe operations** - Concurrent task claiming with atomic state transitions
- **Client-daemon architecture** - Unix socket RPC for fast, reliable communication
- **Pull-based task claiming** - Workers claim tasks atomically via `hyh task claim`
- **Command execution** - Run commands with mutex protection (local or Docker)
- **Git integration** - Safe git operations with dangerous option validation

## Tech Stack

- **Python 3.13+** (targeting Python 3.14)
- **uv** - Package and dependency management
- **msgspec** - High-performance data serialization (replaces dataclasses/Pydantic)
- **pytest** - Testing framework with hypothesis for property-based testing
- **ruff** - Linting and formatting
- **ty** - Type checking
- **pre-commit** - Git hooks for pyupgrade (py314+)

## Architecture

````text
┌─────────────┐     Unix Socket RPC     ┌──────────────┐
│   Client    │ ──────────────────────► │    Daemon    │
│    (hyh)    │                         │ (per-project)│
└─────────────┘                         └──────┬───────┘
                                               │
                          ┌────────────────────┼────────────────────┐
                          │                    │                    │
                    ┌─────▼─────┐       ┌──────▼──────┐      ┌──────▼──────┐
                    │   State   │       │   Runtime   │      │  Trajectory │
                    │  Manager  │       │(Local/Docker)│     │   Logger    │
                    └───────────┘       └─────────────┘      └─────────────┘
```text
## Source Structure

```text
src/hyh/
├── __init__.py      # Package init with version
├── __main__.py      # Entry point for `python -m hyh`
├── client.py        # CLI client, RPC communication, daemon spawning
├── daemon.py        # Daemon server, Unix socket handler
├── state.py         # Task/WorkflowState structs, state store
├── runtime.py       # Command execution layer
├── git.py           # Git integration with safety validation
├── plan.py          # Plan parsing from markdown
├── trajectory.py    # Event/action trajectory logging
├── registry.py      # Worker registry for task claiming
└── acp.py           # Agent Communication Protocol emitter
```text
## Tests Structure

```text
tests/
└── hyh/
    ├── conftest.py               # Shared fixtures
    ├── helpers/                  # Test helpers (lock tracker)
    ├── test_state*.py            # State management tests
    ├── test_client*.py           # Client tests
    ├── test_daemon.py            # Daemon tests
    ├── test_runtime*.py          # Runtime execution tests
    ├── test_plan*.py             # Plan parsing tests
    ├── test_git.py               # Git integration tests
    ├── test_*_audit.py           # Security/concurrency/boundary audits
    ├── test_integration*.py      # Integration tests
    └── test_performance.py       # Performance benchmarks
```text
## Requirements

- Python 3.13+ (macOS or Linux)
- uv for package management
````
