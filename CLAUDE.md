# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**Project:** Autonomous Research Kernel for dev workflows
**Architecture:** Clean Architecture with thread-safe state management
**Runtime:** Python 3.13t (free-threaded, no GIL)

---

## Development Commands

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run all tests
pytest

# Run specific test file
pytest tests/harness/test_state.py

# Run specific test
pytest tests/harness/test_state.py::test_claim_task_atomic -v

# Run tests with timeout (default 2min per test)
pytest --timeout=30

# Type checking (Pydantic models are self-validating)
# No mypy configured - rely on Pydantic runtime validation
```

---

## Architecture

```
src/harness/
├── client.py       # Dumb client (stdlib only, <50ms startup)
├── daemon.py       # Unix socket server, task claim/execute
├── state.py        # JSON persistence, DAG-based task state
├── trajectory.py   # JSONL event logging with O(1) tail
├── runtime.py      # LocalRuntime, DockerRuntime, PathMapper
└── git.py          # Git operations via runtime abstraction
```

**Data Flow:**
1. `client.py` → Unix socket → `daemon.py` (Pydantic validation)
2. `daemon.py` → `state.py` (thread-safe state mutations)
3. `daemon.py` → `trajectory.py` (append-only logging)
4. `daemon.py` → `runtime.py` → subprocess (command execution)

**Key Invariant:** Client has ZERO validation logic. All Pydantic models live in daemon/state.

---

## I. Python 3.13t Concurrency Doctrine

**Core Principle:** Threads are compute units, not I/O waiters.

### Rules

1. **No asyncio** - Use `socketserver.ThreadingMixIn`. Let the OS schedule threads.
2. **Blocking I/O is acceptable** inside threads - simplifies control flow and stack traces.
3. **Use `time.monotonic()`** for all duration/timeout logic - never `time.time()`.

### Lock Hierarchy (Deadlock Prevention)

Acquire locks in this order only. **NEVER** acquire a higher-priority lock while holding a lower one.

| Priority | Lock | Protects |
|----------|------|----------|
| 1 (highest) | `StateManager._lock` | DAG state |
| 2 | `TrajectoryLogger._lock` | Event history |
| 3 (lowest) | `GLOBAL_EXEC_LOCK` | Git index / worktree |

**Release-then-Log Pattern:**
```python
# CORRECT: Release state lock before logging
with self._lock:
    result = self._calculate_state_change()
# Lock released - now safe to log
with self._trajectory_lock:
    self._log_event(result)

# WRONG: Holding state lock during I/O
with self._lock:
    result = self._calculate_state_change()
    self._log_event(result)  # I/O while holding lock = convoy effect
```

### Global Execution Lock

**Rule:** Only acquire `GLOBAL_EXEC_LOCK` for git operations (worktree/index sensitive).

```python
# CORRECT: Parallel execution by default
def execute(self, args, cwd, exclusive=False):
    if exclusive:
        with GLOBAL_EXEC_LOCK:
            return self._run(args, cwd)
    return self._run(args, cwd)

# WRONG: Lock on every execution
def execute(self, args, cwd):
    with GLOBAL_EXEC_LOCK:  # Serializes entire swarm!
        return self._run(args, cwd)
```

---

## II. Type Soundness & Purity Standard

**Core Principle:** The code IS the schema. Never invent custom parsers.

### Rules

1. **Pydantic at the boundary only** - Daemon validates, Client sends raw JSON
2. **No `Any` type** - Model your domain correctly
3. **Use `Literal` for finite states** - Enables static verification

### Dumb Client Contract

The Client imports **ONLY** stdlib: `sys`, `json`, `socket`, `argparse`

```python
# client.py - ALLOWED imports
import sys
import json
import socket
import argparse
import os
import hashlib

# client.py - FORBIDDEN
import pydantic      # NO - validation in daemon only
import requests      # NO - stdlib only
import click         # NO - stdlib only
```

**Client startup must be <50ms.** Zero validation, zero logic. Package argv, send to socket, print response.

### State Schema

Use JSON for persistence, not Markdown frontmatter:

```python
# CORRECT: JSON with Pydantic
class State(BaseModel):
    tasks: dict[str, Task]

    def save(self, path: Path):
        tmp = path.with_suffix('.tmp')
        tmp.write_text(self.model_dump_json(indent=2))
        os.fsync(tmp.fileno())  # Flush to disk
        tmp.rename(path)        # Atomic rename

# WRONG: Markdown frontmatter parsing
def parse_state(content: str) -> dict:
    if content.startswith('---'):
        yaml_part = content.split('---')[1]  # Fragile!
```

---

## III. System Reliability Protocol

**Core Principle:** Assume the process will crash at any nanosecond.

### Atomic Persistence Pattern

**ALWAYS** use write-tmp-fsync-rename:

```python
# CORRECT: Atomic write
def save(self, path: Path):
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        f.write(self.model_dump_json())
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(path)  # POSIX atomic

# WRONG: Direct write (corruption on crash)
def save(self, path: Path):
    with open(path, 'w') as f:
        f.write(self.model_dump_json())  # Power fails here = empty file
```

### Append-Only Trajectory (JSONL)

Use newline-delimited JSON. Partial writes corrupt only the last line:

```python
# CORRECT: JSONL format
{"event": "task_claimed", "task_id": "1", "ts": 1702500000}
{"event": "task_completed", "task_id": "1", "ts": 1702500060}

# WRONG: JSON array (truncation corrupts everything)
[
  {"event": "task_claimed", "task_id": "1"},
  {"event": "task_comple  # <-- Crash here = invalid JSON
```

### Idempotent Operations

`claim_task(worker_id)` must be idempotent - retry returns same task:

```python
def claim_task(self, worker_id: str) -> Task | None:
    with self._lock:
        # Check if worker already has a task (idempotency)
        for task in self.tasks.values():
            if task.worker_id == worker_id and task.status == "running":
                return task  # Return existing claim

        # Find new claimable task
        for task in self._get_claimable():
            task.status = "running"
            task.worker_id = worker_id
            return task
        return None
```

---

## IV. Security & Safety Doctrine

**Core Principle:** The Agent is an untrusted guest. Isolate it.

### Runtime Abstraction

**NEVER** call `subprocess.run` directly in business logic:

```python
# CORRECT: Use runtime abstraction
class Daemon:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    def execute(self, args):
        return self.runtime.execute(args, cwd=self.workdir)

# WRONG: Direct subprocess
class Daemon:
    def execute(self, args):
        return subprocess.run(args, cwd=self.workdir)  # No isolation!
```

### Docker UID Mapping

Prevent root-owned files escaping container:

```python
# CORRECT: Map container user to host user
class DockerRuntime:
    def execute(self, args, cwd, ...):
        uid_gid = f"{os.getuid()}:{os.getgid()}"
        cmd = ["docker", "exec", "--user", uid_gid, "-w", cwd, ...]

# WRONG: Run as root in container
cmd = ["docker", "exec", "-w", cwd, ...]  # Creates root-owned files!
```

### Signal Transparency

Decode signals for LLM comprehension:

```python
def decode_signal(returncode: int) -> str | None:
    """Translate -9 to 'SIGKILL', -11 to 'SIGSEGV', etc."""
    if returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"SIG{-returncode}"

# Usage in logs
result = runtime.execute(cmd)
if result.returncode < 0:
    sig = decode_signal(result.returncode)
    log(f"Process killed by {sig}")  # "SIGKILL" not "-9"
```

---

## V. Observability Doctrine

**Core Principle:** A system that cannot explain its latency is broken.

### Structured Telemetry

Every operation records timing:

```python
@dataclass
class TrajectoryEvent:
    event: str
    task_id: str | None
    timestamp: float  # time.monotonic()
    duration_ms: float | None
    reason: str | None  # WHY this happened

# Every state transition carries a reason
{"event": "task_skipped", "task_id": "3", "reason": "dependency_failed:task_2"}
{"event": "task_claimed", "task_id": "4", "duration_ms": 2.3}
```

### The "Why" Trace

State transitions must explain themselves:

```python
# CORRECT: Include reason
task.status = "skipped"
task.reason = f"dependency_failed:{dep.id}"

# WRONG: Silent state change
task.status = "skipped"  # Why? Timeout? Dependency? User cancel?
```

---

## VI. Determinism Standard

**Core Principle:** The Harness must be the constant in a chaotic system.

### Capability Checks at Startup

Fail fast with descriptive errors:

```python
def check_capabilities(self):
    """Verify required tools exist before accepting work."""
    # Check git
    result = subprocess.run(["git", "--version"], capture_output=True)
    if result.returncode != 0:
        raise SystemExit("ERROR: git not found. Install git and retry.")

    # Check docker (if docker runtime)
    if self.runtime == "docker":
        result = subprocess.run(["docker", "info"], capture_output=True)
        if result.returncode != 0:
            raise SystemExit("ERROR: Docker not running. Start Docker and retry.")
```

### Monotonic Time Only

```python
# CORRECT: Monotonic for durations
start = time.monotonic()
result = execute(cmd)
duration = time.monotonic() - start

# WRONG: Wall clock (drifts, can go backwards)
start = time.time()
result = execute(cmd)
duration = time.time() - start  # User changed clock = negative duration
```

---

## VII. Defensive Graph Construction

**Core Principle:** Inputs are adversaries.

### Cycle Detection on Load

```python
def load_plan(self, tasks: list[Task]):
    """Load task graph, rejecting cycles."""
    # Build adjacency for cycle detection
    graph = {t.id: t.dependencies for t in tasks}

    if cycle := self._find_cycle(graph):
        raise ValueError(f"Cycle detected: {' -> '.join(cycle)}")

    self.tasks = {t.id: t for t in tasks}

def _find_cycle(self, graph: dict) -> list[str] | None:
    """DFS cycle detection. Returns cycle path or None."""
    visited = set()
    rec_stack = set()

    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        for dep in graph.get(node, []):
            if dep not in visited:
                if cycle := dfs(dep, path + [dep]):
                    return cycle
            elif dep in rec_stack:
                return path + [dep]
        rec_stack.remove(node)
        return None

    for node in graph:
        if node not in visited:
            if cycle := dfs(node, [node]):
                return cycle
    return None
```

### Input Sanitization

Strip non-printable characters at the edge:

```python
def sanitize_output(output: str) -> str:
    """Remove non-printable chars that could corrupt IPC stream."""
    return ''.join(c for c in output if c.isprintable() or c in '\n\t')
```

---

## VIII. Algorithmic Efficiency

**Core Principle:** No O(N) operations on the hot path.

### Efficient Log Tail

```python
# CORRECT: O(k) reverse seek (k = block size)
def tail(self, n: int = 5) -> list[dict]:
    """Read last n lines without loading entire file."""
    with open(self.path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        block_size = 4096
        lines = []

        while len(lines) <= n and size > 0:
            read_size = min(block_size, size)
            size -= read_size
            f.seek(size)
            block = f.read(read_size).decode()
            lines = block.splitlines() + lines

        return [json.loads(line) for line in lines[-n:]]

# WRONG: O(N) full file read
def tail(self, n: int = 5) -> list[dict]:
    lines = self.path.read_text().splitlines()  # Reads entire file!
    return [json.loads(line) for line in lines[-n:]]
```

### Efficient Task Claiming

For N < 1000 tasks, O(V+E) iteration is acceptable:

```python
def _get_claimable(self) -> Iterator[Task]:
    """Yield tasks with all dependencies satisfied."""
    for task in self.tasks.values():
        if task.status != "pending":
            continue
        if all(self.tasks[d].status == "completed" for d in task.dependencies):
            yield task
```

---

## Validation Checklist

Before every PR, verify:

- [ ] **Concurrency:** No `asyncio`. Blocking I/O only in threads.
- [ ] **Lock Order:** State → Trajectory → Execution. Never reversed.
- [ ] **Atomic Writes:** All persistence uses tmp-fsync-rename pattern.
- [ ] **JSONL Trajectory:** Append-only, newline-delimited.
- [ ] **Idempotent Claims:** `claim_task(worker_id)` returns same task on retry.
- [ ] **Runtime Abstraction:** No direct `subprocess.run` in business logic.
- [ ] **UID Mapping:** DockerRuntime uses `--user $(id -u):$(id -g)`.
- [ ] **Signal Decoding:** Negative return codes translated to signal names.
- [ ] **Monotonic Time:** All durations use `time.monotonic()`.
- [ ] **Cycle Detection:** Graph validated on plan load.
- [ ] **Capability Check:** Daemon verifies git/docker at startup.
- [ ] **No Any Types:** All types explicit, use `Literal` for states.
- [ ] **Client Purity:** Only stdlib imports, <50ms startup.

---

## Quick Reference

| Principle | Rule | Anti-Pattern |
|-----------|------|--------------|
| Concurrency | `ThreadingMixIn` | `asyncio` |
| Persistence | tmp-fsync-rename | direct `open(f,'w')` |
| Logging | JSONL append | JSON array `[...]` |
| Time | `time.monotonic()` | `time.time()` |
| Subprocess | `runtime.execute()` | `subprocess.run()` |
| Types | `Literal["pending","running"]` | `str` status |
| Validation | Pydantic in Daemon | Manual parsing |
| Locking | Release-then-Log | Hold lock during I/O |

---

## IX. Foundational Tenants (Bug Prevention)

These principles stem from real bugs. Violating them causes production failures.

### I. Data is Invariant; Representation is Fluid

**Bug:** Passing `datetime` object to `datetime.fromisoformat()`.

Pydantic converts JSON strings to Python objects automatically. Know the type at every line.

```python
# WRONG: Assuming string when Pydantic gave you an object
started_at = datetime.fromisoformat(task.started_at)  # Crashes if already datetime

# CORRECT: Check type or use Pydantic's validated object directly
started_at = task.started_at  # Already datetime from Pydantic
```

### II. Every Line of Code Has a Cost Function

**Bug:** Reading entire 100MB log file to get last 5 lines.

Label I/O operations with Big-O complexity. O(N) on hot paths is rejected.

```python
# WRONG: O(N) - reads entire file
lines = log_file.read_text().splitlines()[-5:]

# CORRECT: O(k) - seeks to end, reads only needed bytes
# See trajectory.py:_tail_reverse_seek()
```

### III. Units Matter (Time Vectors)

**Bug:** Subtracting naive datetime from UTC-aware datetime.

Time is a vector with a reference frame. Always normalize to UTC at boundaries.

```python
# WRONG: Mixing naive and aware (raises TypeError)
elapsed = datetime.now() - task.started_at  # started_at may be UTC-aware

# CORRECT: Normalize both to UTC
from datetime import timezone
now = datetime.now(timezone.utc)
started = task.started_at
if started.tzinfo is None:
    started = started.replace(tzinfo=timezone.utc)
elapsed = now - started
```

### IV. There is No Global Scope (Context Mapping)

**Bug:** Passing host `cwd` to Docker container without path mapping.

When crossing boundaries (host→container), every argument must be transformed.

```python
# WRONG: Host path passed directly to container
docker exec -w /Users/host/path container cmd  # Path doesn't exist in container

# CORRECT: Map paths across boundaries
container_cwd = self.path_mapper.to_runtime(host_cwd)
docker exec -w {container_cwd} container cmd
```

### V. Reads are not Writes (Lease Renewal)

**Bug:** Returning existing task without updating `started_at` timestamp.

In distributed systems, authority (leases) must be explicitly renewed via write operations.

```python
# WRONG: Read-only idempotency (lease expires, task stolen)
if task.claimed_by == worker_id:
    return task  # No timestamp update = stale lease

# CORRECT: Renew lease on every claim (idempotent write)
if task.claimed_by == worker_id:
    task.started_at = datetime.now(timezone.utc)  # Lease renewal
    self._persist(task)
    return task
```

### Summary Table

| Tenant | Rule | Runtime assert |
|--------|------|----------------|
| Type Precision | Know `str` vs `datetime` at every line | `assert isinstance(x, expected_type)` |
| Complexity Audit | Label I/O with Big-O; reject O(N) on hot paths | Code review |
| Unit Hygiene | All Time is UTC, all Paths are Absolute | `assert dt.tzinfo is not None` |
| Boundary Mapping | Transform every arg crossing system boundaries | Integration tests |
| Lease Writes | Authority extension requires a write operation | `test_lease_renewal_prevents_stealing` |
