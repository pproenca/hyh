# Task Completion Checklist

Before marking any task as complete, verify:

## 1. Code Quality
```bash
make format       # Auto-fix formatting issues
make lint         # Verify no lint errors
make typecheck    # Verify type checking passes
```

## 2. Testing
```bash
make test         # All tests must pass
```

Or combined:
```bash
make check        # Runs lint + typecheck + test
```

## 3. Pre-commit Validation
The pre-commit hooks will run pyupgrade automatically on commit. Ensure code uses Python 3.13+ syntax.

## 4. Code Review Checklist
- [ ] Type hints on all function signatures
- [ ] Docstrings on public functions/classes
- [ ] Thread-safety documented if applicable
- [ ] No hardcoded timeouts (use condition-based waiting)
- [ ] msgspec.Struct used for data models (not Pydantic)
- [ ] Final annotation on immutable class attributes
- [ ] Tests added/updated for new functionality

## Recommended Workflow
1. Make changes
2. Run `make format` to auto-fix style
3. Run `make check` to verify everything passes
4. Commit changes (pre-commit hooks will run pyupgrade)
