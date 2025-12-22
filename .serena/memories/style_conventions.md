# Code Style and Conventions

## General Style
- **Line length**: 100 characters
- **Python version**: 3.13+ (uses modern syntax via pyupgrade --py313-plus)
- **Type hints**: Required everywhere (enforced by ruff ANN rules)
- **Imports**: Sorted by isort (via ruff I rules)

## Naming Conventions
- **Classes**: PascalCase (e.g., `StateManager`, `WorkflowState`)
- **Functions/Methods**: snake_case (e.g., `get_claimable_task`)
- **Constants**: UPPER_SNAKE_CASE
- **Private**: Leading underscore (e.g., `_state`, `_lock`)
- **Type aliases**: PascalCase (e.g., `TimeoutSeconds`)

## Docstrings
- Use triple-quoted docstrings for all public functions/classes
- Include `Args:` and `Returns:` sections for non-trivial functions
- Document thread-safety and complexity when relevant
- Example:
  ```python
  def is_timed_out(self) -> bool:  # Time: O(1), Space: O(1)
      """Check if task has exceeded timeout window.

      Returns:
          True if task is RUNNING and elapsed time exceeds timeout_seconds.
      """
  ```

## Data Models
- Use `msgspec.Struct` instead of dataclasses/Pydantic for performance
- Use `forbid_unknown_fields=True` for strict validation
- Prefer immutable structures (tuples over lists for collections)
- Use `Final` annotation for immutable instance variables

## Thread Safety
- Use `threading.Lock()` for shared state
- Document thread-safety guarantees in class docstrings
- Use `Final` for lock instances

## Error Handling
- Raise `ValueError` for business logic violations
- Use type narrowing over broad exception catching

## Testing Patterns
- Use condition-based waiting (`wait_until`) instead of `time.sleep`
- Inject clocks for timeout testing (see `Task.set_clock`)
- Use hypothesis for property-based tests
- Mark slow tests with `@pytest.mark.slow`
