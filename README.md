# harness-cli

[![PyPI version](https://img.shields.io/pypi/v/harness-cli.svg)](https://pypi.org/project/harness-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/harness-cli.svg)](https://pypi.org/project/harness-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

CLI orchestration tool for agentic workflows. Coordinate tasks with claude-code, AI agents, and development tools through a daemon-based task management system.

## Features

- **Task orchestration** - DAG-based dependency resolution with cycle detection
- **Thread-safe operations** - Concurrent task claiming with atomic state transitions
- **Client-daemon architecture** - Unix socket RPC for fast, reliable communication
- **Pull-based task claiming** - Workers claim tasks atomically via `harness task claim`
- **Command execution** - Run commands with mutex protection (local or Docker)
- **Git integration** - Safe git operations with dangerous option validation

## Installation

### Recommended: uv tool (persistent installation)

```bash
uv tool install harness-cli
```

### One-off execution

```bash
uvx harness-cli status
```

### Traditional pip

```bash
pip install harness-cli
```

### From source (development)

```bash
uv tool install git+https://github.com/pproenca/harness
```

### Curl install script

```bash
curl -fsSL https://raw.githubusercontent.com/pproenca/harness/master/install.sh | bash
```

## Quick Start

```bash
# Check daemon is running
harness ping

# Import a plan file
harness plan import --file plan.md

# Show workflow status
harness status

# Claim and execute tasks
harness task claim
harness task complete --id task-1

# Execute commands with mutex
harness exec -- make test

# Safe git operations
harness git -- status
```

## Architecture

```
┌─────────────┐     Unix Socket RPC     ┌──────────────┐
│   Client    │ ──────────────────────► │    Daemon    │
│  (harness)  │                         │  (per-project)│
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
git clone https://github.com/pproenca/harness.git
cd harness
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
