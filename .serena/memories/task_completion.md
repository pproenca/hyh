# Task Completion Checklist

Before considering any task complete, run:

## 1. Format Code
```bash
make format
```

## 2. Lint Check
```bash
make lint
```

## 3. Type Check
```bash
make typecheck
```

## 4. Run Tests
```bash
make test
```

## Combined (Recommended)
```bash
make check   # Runs lint + typecheck + test
```

## Verification Checklist
Before every PR, verify:

- [ ] No asyncio used - blocking I/O in threads only
- [ ] Lock order: State → Trajectory → Execution (never reversed)
- [ ] Atomic writes use tmp-fsync-rename pattern
- [ ] Trajectory uses JSONL (append-only, newline-delimited)
- [ ] `claim_task(worker_id)` is idempotent
- [ ] No direct `subprocess.run` in business logic - use runtime abstraction
- [ ] DockerRuntime uses `--user $(id -u):$(id -g)`
- [ ] Negative return codes translated to signal names
- [ ] All durations use `time.monotonic()`
- [ ] Graph validates for cycles on plan load
- [ ] No `Any` types - use `Literal` for states (except Pydantic model_copy kwargs)
- [ ] Client uses only stdlib imports, <50ms startup

## Common Issues
- Mixing naive and aware datetime → TypeError
- Host paths in Docker container → path mapping needed
- O(N) file reads on hot paths → use efficient algorithms
- Reading from Pydantic-parsed datetime as string → it's already datetime
- Forgetting `exclusive=True` for git operations → race conditions

## Test Files
```
tests/harness/
├── conftest.py              # Shared fixtures
├── test_state.py            # StateManager, DAG, claim/complete
├── test_daemon.py           # HarnessDaemon, HarnessHandler
├── test_runtime.py          # LocalRuntime, DockerRuntime, PathMapper
├── test_trajectory.py       # TrajectoryLogger, tail
├── test_git.py              # Git operations
├── test_client.py           # Client RPC
├── test_plan.py             # Plan parsing
├── test_acp.py              # ACP emitter
├── test_performance.py      # Performance benchmarks
├── test_integration.py      # End-to-end tests
└── test_integration_council.py  # Council integration tests
```
