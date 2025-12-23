# Code Style & Conventions

## Data Structures

Use **msgspec.Struct** instead of dataclasses or Pydantic:

````python
from msgspec import Struct

class Task(Struct, forbid_unknown_fields=True):
    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()
    timeout_seconds: int = 600
```text
Key patterns:

- Use `forbid_unknown_fields=True` for strict validation
- Use tuples for immutable collections (not lists)
- Provide sensible defaults

## Type Hints

**Mandatory everywhere** - ruff ANN rules enforce this:

```python
from typing import Any, Final, ClassVar

def send_rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    ...

class ACPEmitter:
    _host: Final[str]
    _clock: ClassVar[Callable[[], datetime]]
```text
- Use `Final` for immutable instance attributes
- Use `ClassVar` for class-level attributes
- Use `X | None` syntax (not `Optional[X]`)
- Use builtin generics (`list`, `dict`, `tuple`) not `typing.List`

## Class Design

Use `__slots__` for performance in non-Struct classes:

```python
class ACPEmitter:
    __slots__ = ("_disabled_event", "_host", "_port", "_queue", "_thread", "_warned_event")
```text
## Datetime Handling

Always use UTC and timezone-aware datetimes:

```python
from datetime import UTC, datetime

now = datetime.now(UTC)
```text
## Naming

- **snake_case** for functions and variables
- **PascalCase** for classes
- **UPPER_SNAKE_CASE** for constants
- Private attributes: `_single_underscore`
- Module-level type aliases: `TimeoutSeconds = int`

## Imports

Order enforced by ruff isort:

1. Standard library
1. Third-party packages
1. Local imports

## Error Handling

- Raise specific exceptions with helpful messages
- Use `contextlib.suppress()` for expected exceptions

```python
import contextlib

with contextlib.suppress(OSError):
    sock.close()
```text
## Testing Conventions

- Use pytest with fixtures
- Test file naming: `test_<module>.py`
- Test function naming: `test_<behavior>` or `test_<feature>_<condition>`
- Use hypothesis for property-based testing where appropriate
- Arrange-Act-Assert pattern

```python
def test_task_model_basic_validation():
    """Task should validate and store all fields."""
    task = Task(
        id="task-1",
        description="Implement feature X",
        status=TaskStatus.PENDING,
        dependencies=[],
    )
    assert task.id == "task-1"
```text
## Ruff Configuration

Line length: 100 characters
Target: Python 3.14

Enabled rule sets:

- E (pycodestyle), F (Pyflakes), UP (pyupgrade)
- B (bugbear), SIM (simplify), I (isort)
- N (pep8-naming), ANN (annotations), S (bandit security)
- DTZ (datetimez), PTH (pathlib), RET (return), ARG (unused-arguments)
- RUF (ruff-specific)

## Documentation

- Minimal docstrings (not enforced)
- Use type hints as primary documentation
- Module-level comments for complex logic
- Section comments with `# ===...===` separators
````
