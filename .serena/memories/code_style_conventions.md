# Code Style & Conventions

## General Style
- **Line length**: 100 characters
- **Target Python version**: 3.14 (py314)
- **Import sorting**: isort via ruff (I rules)

## Naming Conventions
- `snake_case` for functions, methods, variables
- `PascalCase` for classes
- `SCREAMING_SNAKE_CASE` for constants
- Private methods/attributes prefixed with `_`

## Type Hints
**Mandatory** - ruff ANN rules are enabled. All functions must have:
- Parameter type annotations
- Return type annotations

```python
def process_task(task: Task, timeout: float = 5.0) -> bool:
    ...
```

## Data Structures
Use **msgspec.Struct** instead of dataclasses or Pydantic:

```python
from msgspec import Struct

class Task(Struct, forbid_unknown_fields=True):
    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()  # Use tuple, not list
```

## Enums
Inherit from both `str` and `Enum` for JSON serialization:

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
```

## Modern Python Patterns (py313+)
- Use `|` for union types: `datetime | None`
- Use `tuple[str, ...]` for variable-length tuples
- Use `list[str]` not `List[str]` (no typing imports needed)
- Use `dict[str, Any]` not `Dict[str, Any]`
- Use `pathlib.Path` for file operations (PTH rules)

## Datetime Handling
- Always use timezone-aware datetimes (DTZ rules enabled)
- Use `datetime.now(UTC)` not `datetime.now()`
- Avoid naive datetime comparisons

## Security
- Bandit (S) rules enabled
- `assert` allowed in general code (S101 ignored)
- Subprocess calls audited per-file

## Docstrings
- Use triple quotes for multi-line docstrings
- Follow Google style (Args, Returns, Raises sections)
- Module-level docstrings for test files

## Test File Conventions
- Tests in `tests/harness/` mirroring `src/harness/`
- Fixtures in `conftest.py`
- Use condition-based waiting (`wait_until`) instead of `time.sleep`
- Thread isolation for free-threaded Python (3.14t)
