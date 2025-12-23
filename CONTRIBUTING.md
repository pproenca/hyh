# Contributing to hyh

Thank you for your interest in contributing to hyh!

## Getting started

### Prerequisites

- Python 3.13+ (3.14 freethreaded supported)
- [uv](https://docs.astral.sh/uv/) for package management
- [git-cliff](https://git-cliff.org/) for changelog generation (optional)

### Setup

Clone the repository and install dependencies:

```shell
git clone https://github.com/pproenca/hyh.git
cd hyh
make install
```

This installs all dependencies and sets up pre-commit hooks.

### Running tests

```shell
make test        # Run affected tests (fast, via testmon)
make test-all    # Run full test suite in parallel
make check       # Run lint + typecheck + tests
```

### Code quality

```shell
make lint        # Check code style
make typecheck   # Run ty type checker
make format      # Auto-format code
```

## Submitting changes

### Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/) for automatic changelog generation via git-cliff.

| Prefix      | Description                                             |
| ----------- | ------------------------------------------------------- |
| `feat:`     | New feature                                             |
| `fix:`      | Bug fix                                                 |
| `docs:`     | Documentation only                                      |
| `refactor:` | Code change that neither fixes a bug nor adds a feature |
| `perf:`     | Performance improvement                                 |
| `test:`     | Adding or updating tests                                |
| `ci:`       | CI/CD changes                                           |
| `chore:`    | Other changes                                           |

Example: `feat: add support for Docker runtime`

### Pull requests

1. Create a branch for your changes
1. Write tests for new functionality
1. Ensure `make check` passes
1. Submit a pull request with a clear description

## Code style

- Type hints are required for all public APIs
- We use `ruff` for linting and formatting
- We use `msgspec.Struct` instead of dataclasses
- See [CLAUDE.md](CLAUDE.md) for detailed conventions

## Questions?

Open an [issue](https://github.com/pproenca/hyh/issues) for questions or discussion.
