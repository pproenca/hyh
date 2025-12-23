# Contributing to hyh

First off, thank you for considering contributing to `hyh`! It's people like you that make the agentic tool space better for everyone.

## Development Setup

`hyh` uses `uv` for dependency management and development.

1. Clone the repository:
   ```bash
   git clone https://github.com/pproenca/hyh.git
   cd hyh
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Install pre-commit hooks:
   ```bash
   uv run pre-commit install
   ```

## Workflow

1. Create a new branch for your feature or bugfix.
2. Write tests for your changes.
3. Ensure all tests pass: `make test`
4. Lint your code: `make lint`
5. Submit a Pull Request.

## Code Style

- We use `ruff` for linting and formatting.
- Type hints are required for all public APIs.
- We follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) and other standard Python practices.

## Commit Messages

We encourage the use of [Conventional Commits](https://www.conventionalcommits.org/).

## Questions?

Feel free to open an issue for questions or join our community discussions.
