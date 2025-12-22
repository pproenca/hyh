# Task Completion Checklist

When completing a task in the harness project, run these checks:

## 1. Format Code
```bash
make format
```
Ensures consistent code style with ruff.

## 2. Lint Check
```bash
make lint
```
Checks:
- pyupgrade (py313+ patterns)
- ruff check (E, F, UP, B, SIM, I, N, ANN, S, DTZ, PTH, RET, ARG, RUF)
- ruff format --check

## 3. Type Check
```bash
make typecheck
```
Runs mypy on src/ directory.

## 4. Run Tests
```bash
make test
```
Runs all tests with 30-second timeout per test.

## 5. All-in-One Check
```bash
make check
```
Runs lint + typecheck + test in sequence.

## Pre-commit Hooks
The project uses pre-commit with pyupgrade hook (--py313-plus).
Run manually if needed:
```bash
uv run pre-commit run --all-files
```

## Before Committing
1. `make format` - Auto-fix formatting
2. `make check` - Verify all checks pass
3. Ensure no type errors (mypy)
4. Ensure all tests pass
5. Write meaningful commit message

## Common Issues to Avoid
- Missing type annotations (ANN rules)
- Using naive datetimes (use UTC)
- Using os.path instead of pathlib
- Using mutable default arguments
- Leaving unused imports or variables
