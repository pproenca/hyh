# hyh

[![PyPI](https://img.shields.io/pypi/v/hyh.svg)](https://pypi.org/project/hyh/)
[![Python](https://img.shields.io/pypi/pyversions/hyh.svg)](https://pypi.org/project/hyh/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/pproenca/hyh/actions/workflows/ci.yml/badge.svg)](https://github.com/pproenca/hyh/actions/workflows/ci.yml)

A CLI orchestration tool for agentic workflows. Coordinate tasks with AI agents through a daemon-based task management system.

hyh (hold your horses) provides DAG-based task orchestration with atomic state transitions, designed for coordinating claude-code and other AI agents in development workflows.

## Highlights

- **DAG-based orchestration** - Dependency resolution with cycle detection and topological validation
- **Thread-safe operations** - Concurrent task claiming with atomic state transitions via mutex protection
- **Client-daemon architecture** - Unix socket RPC for fast, reliable inter-process communication
- **Pull-based task claiming** - Workers claim tasks atomically, preventing race conditions
- **Command execution** - Run commands with mutex protection (local or Docker runtimes)
- **Git integration** - Safe git operations with dangerous option validation
- **Python 3.13+ / 3.14 freethreaded** - Modern Python with full type annotations

## Getting started

Run hyh with [uvx](https://docs.astral.sh/uv/guides/tools/#running-tools) to get started quickly:

```shell
uvx hyh status
```

Or start a workflow from a plan file:

```shell
uvx hyh plan import --file plan.md
uvx hyh status
```

## Installation

Install hyh as a persistent tool:

```shell
uv tool install hyh
```

Or with pip:

```shell
pip install hyh
```

For development installation, see [Contributing](#contributing).

## Usage

```shell
# Check daemon status
hyh ping

# Import and manage plans
hyh plan import --file plan.md
hyh status

# Claim and complete tasks
hyh task claim
hyh task complete --id task-1

# Execute commands with mutex protection
hyh exec -- make test

# Safe git operations
hyh git -- status
```

## Requirements

- Python 3.13+ (3.14 freethreaded supported)
- macOS or Linux
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Getting help

If you have questions or want to report a bug, please open an
[issue](https://github.com/pproenca/hyh/issues) in this repository.

## Contributing

We welcome contributions! To get started:

```shell
git clone https://github.com/pproenca/hyh.git
cd hyh
make install
make test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE) for details.
