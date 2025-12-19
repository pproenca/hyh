# Code Style and Conventions

## Python Version
Python 3.13t (free-threaded build, no GIL)

## Type Hints
- **Strict typing required** - No `Any` type (except Pydantic's model_copy kwargs)
- Use `Literal` for finite states: `Literal["pending", "running", "completed"]`
- mypy strict mode enforced

## Imports
- isort enforced via ruff (I rules)
- Client imports ONLY stdlib: `sys`, `json`, `socket`, `argparse`, `os`, `hashlib`

## Formatting
- Line length: 100 characters
- ruff format used (Black-compatible)

## Naming (PEP8)
- Classes: PascalCase
- Functions/methods: snake_case
- Constants: UPPER_SNAKE_CASE
- Private: `_single_underscore`

## Docstrings
- Not strictly required but encouraged for public APIs
- Focus on WHY not WHAT

## Concurrency Patterns
- No asyncio - use `socketserver.ThreadingMixIn`
- Blocking I/O acceptable in threads
- Lock hierarchy: State → Trajectory → Execution (never reversed)

## Persistence Patterns
- Atomic writes: tmp-fsync-rename pattern
- JSONL for append-only logs (not JSON arrays)
- Pydantic at boundaries only

## Time
- Always use `time.monotonic()` for durations
- All datetime should be UTC-aware

## Security Rules (via ruff)
- S101 allowed (assert for invariants)
- S603/S607 allowed in client/daemon/runtime (subprocess execution layer)
- S108 allowed in client (/tmp for Unix sockets)

## Ruff Rules Enabled
E, F, UP, B, SIM, I, N, ANN, S, DTZ, PTH, RET, ARG, RUF
