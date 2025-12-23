# Development Commands

## Setup

````bash
# Install all dependencies (first time setup)
make install

# Install globally for use anywhere (editable - uses repo code)
make install-global

# Uninstall global installation
make uninstall-global
```text
## Development

```bash
# Start the daemon (development mode)
make dev

# Open interactive Python shell with project loaded
make shell
```text
## Testing

```bash
# Run all tests
make test

# Run tests without timeout (faster iteration)
make test-fast

# Run specific test file
make test-file FILE=tests/hyh/test_state.py

# Run benchmark tests
make benchmark

# Run memory profiling tests
make memcheck

# Run all performance tests (benchmark + memory)
make perf
```text
## Code Quality

```bash
# Check code style and quality (no auto-fix)
make lint

# Run type checking with ty
make typecheck

# Auto-format code
make format

# Run all checks (lint + typecheck + test)
make check
```text
## Build & Publish

```bash
# Build wheel for distribution
make build

# Publish to TestPyPI (for testing)
make publish-test

# Publish to PyPI
make publish
```text
## Release

```bash
# Release with version bump
make release TYPE=patch   # or: major, minor, alpha, beta, rc, stable

# Shorthand commands
make release-alpha
make release-patch
make release-minor
```text
## Cleanup

```bash
# Remove build artifacts, caches, and venv
make clean
```text
## CLI Usage

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

# Get worker ID
hyh worker-id
```text
## Quick Reference

| Task             | Command        |
| ---------------- | -------------- |
| Setup project    | `make install` |
| Run tests        | `make test`    |
| Check everything | `make check`   |
| Format code      | `make format`  |
| Start daemon     | `make dev`     |
````
