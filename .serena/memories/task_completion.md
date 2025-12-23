# Task Completion Checklist

When a task is completed, run these commands to verify quality:

## Required Checks

### 1. Format Code

````bash
make format
```text
Auto-fixes formatting issues with ruff and pyupgrade.

### 2. Lint Check

```bash
make lint
```text
Verifies:

- pyupgrade (Python 3.13+ syntax)
- ruff linting rules
- Code formatting compliance

### 3. Type Check

```bash
make typecheck
```text
Runs `ty check` on source code.

### 4. Run Tests

```bash
make test
```text
Runs full test suite with 30-second timeout per test.

## All-in-One Command

```bash
make check
```text
Runs lint → typecheck → test in sequence.

## Pre-Commit Hooks

The project uses pre-commit with pyupgrade. Git commits will automatically:

- Upgrade syntax to Python 3.13+

To run manually:

```bash
uv run pre-commit run --all-files
```text
## Before Creating a PR

1. `make format` - Fix any formatting issues
1. `make check` - Ensure all checks pass
1. Verify tests cover new functionality
1. Update CHANGELOG.md if needed

## Common Issues

### Lint Failures

- ANN: Missing type annotations (required for src/, optional for tests/)
- S101: assert in source (allowed, just ignored)
- DTZ: Timezone-naive datetime (use `datetime.now(UTC)`)

### Type Check Failures

- Ensure all function parameters and returns have type hints
- Use `X | None` instead of `Optional[X]`

### Test Failures

- Check for 30-second timeout (use `--timeout=0` for debugging)
- Run specific file: `make test-file FILE=tests/hyh/test_xxx.py`
````
