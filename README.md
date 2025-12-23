# hyh

[![PyPI version](https://img.shields.io/pypi/v/hyh.svg)](https://pypi.org/project/hyh/)
[![Python versions](https://img.shields.io/pypi/pyversions/hyh.svg)](https://pypi.org/project/hyh/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Coverage](https://img.shields.io/badge/coverage-80%25-green)](https://github.com/pproenca/hyh)

CLI orchestration tool for agentic workflows. Coordinate tasks with claude-code, AI agents, and development tools through a daemon-based task management system.

## Features

- **Task orchestration** - DAG-based dependency resolution with cycle detection
- **Thread-safe operations** - Concurrent task claiming with atomic state transitions
- **Client-daemon architecture** - Unix socket RPC for fast, reliable communication
- **Pull-based task claiming** - Workers claim tasks atomically via `hyh task claim`
- **Command execution** - Run commands with mutex protection (local or Docker)
- **Git integration** - Safe git operations with dangerous option validation

## Installation

### Recommended: uv tool (persistent installation)

```bash
uv tool install hyh
```

### One-off execution

```bash
uvx hyh status
```

### Traditional pip

```bash
pip install hyh
```

### From source (development)

```bash
uv tool install git+https://github.com/pproenca/hyh
```

### Curl install script

```bash
curl -fsSL https://raw.githubusercontent.com/pproenca/hyh/master/install.sh | bash
```

## Quick Start

```bash
# Check daemon is running
hyh ping

# Import a plan file
hyh plan import --file plan.md

# Show workflow status
hyh status

# Claim and execute tasks
hyh task claim
hyh task complete --id task-1

# Execute commands with mutex
hyh exec -- make test

# Safe git operations
hyh git -- status
```

## Architecture

```
┌─────────────┐     Unix Socket RPC     ┌──────────────┐
│   Client    │ ──────────────────────► │    Daemon    │
│  (hyh)  │                         │  (per-project)│
└─────────────┘                         └──────┬───────┘
                                               │
                          ┌────────────────────┼────────────────────┐
                          │                    │                    │
                    ┌─────▼─────┐       ┌──────▼──────┐      ┌──────▼──────┐
                    │   State   │       │   Runtime   │      │  Trajectory │
                    │  Manager  │       │ (Local/Docker)│    │   Logger    │
                    └───────────┘       └─────────────┘      └─────────────┘
```

## Requirements

- Python 3.13+
- macOS or Linux
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Development

```bash
# Clone and setup
git clone https://github.com/pproenca/hyh.git
cd hyh
make install

# Run tests
make test

# Start development daemon
make dev
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Documentation

See [CLAUDE.md](CLAUDE.md) for detailed architecture and code style documentation.
