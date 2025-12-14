# Harness v2.0: The Pull Engine (Council Revision)

**Goal:** Transform the Harness from a "Task Counter" to an Autonomous Research Kernel with runtime abstraction, trajectory logging, DAG-based state, and a pull protocol—while addressing seven critical architectural issues identified by the Council.

**Architecture:** Clean Architecture with proper separation of concerns. New modules: `runtime.py` (execution abstraction with UID mapping), `trajectory.py` (JSONL event logging). State persisted as **JSON** (not Markdown frontmatter). Locking redesigned to avoid convoy effects. Worker ID idempotency for crash recovery.

---

## Council Fixes Applied

| Fix | Reviewer | Problem | Solution |
|-----|----------|---------|----------|
| **JSON State** | Guido van Rossum | Markdown frontmatter can't serialize DAG cleanly | `dev-workflow-state.json` with `model_dump_json()` |
| **Lock Convoy** | Sam Gross | Holding state lock during trajectory I/O serializes swarm | Release-then-Log: separate critical sections |
| **Lost Ack** | David Soria Parra | Network failures leave tasks RUNNING forever | `worker_id` idempotency: return existing task on retry |
| **Root Escape** | Justin Spahr-Summers | Docker exec creates root-owned files | `--user $(id -u):$(id -g)` in DockerRuntime |
| **Missing Signal** | Justin Spahr-Summers | Negative return codes (-9, -11) are cryptic to LLM | Decode signals to names (SIGKILL, SIGSEGV) in logs |
| **Global Lock Suicide** | Sam Gross | GLOBAL_EXEC_LOCK on every execute() serializes entire swarm | `exclusive` flag, only lock for git operations |
| **Check-Then-Act Race** | David Soria Parra | load() → check → update() is not atomic, causes double-claim | Push logic into `StateManager.claim_task()` atomic method |

---

## File Structure

```
src/harness/
├── runtime.py      # NEW: LocalRuntime, DockerRuntime (with UID), PathMapper
├── trajectory.py   # NEW: TrajectoryLogger with O(1) reverse-seek tail()
├── state.py        # MODIFIED: JSON persistence, Task model with worker_id tracking
├── daemon.py       # MODIFIED: task_claim with idempotency, separate lock sections
├── client.py       # MODIFIED: WORKER_ID constant, task/exec commands
└── git.py          # MODIFIED: delegates to runtime.py
```

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent new modules (runtime.py + trajectory.py), no file overlap |
| Group 2 | 3 | State schema migration (JSON + DAG + worker_id tracking) |
| Group 3 | 4, 5, 6 | Daemon + client + git delegation (dependent on state schema) |
| Group 4 | 7, 8 | Integration tests + code review |

---

### Task 1: Runtime Abstraction with UID Mapping (runtime.py) - NEW FILE

**Effort:** standard (10-15 tool calls)

**Files:**
- Create: `src/harness/runtime.py`
- Create: `tests/harness/test_runtime.py`

**Council Fixes Applied:**
- **Root Escape:** DockerRuntime passes `--user $(id -u):$(id -g)` to docker exec
- **Blind Execution:** Add `env` parameter to `execute()` for API keys
- **Missing Signal:** Add `decode_signal()` helper to translate negative return codes to signal names
- **Global Lock Suicide:** Add `exclusive: bool = False` parameter - only acquire lock when `True`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_runtime.py
   import os
   import signal
   import subprocess
   import threading
   import pytest


   class TestSignalDecoding:
       """Tests for signal decoding (Council: Missing Signal fix)."""

       def test_decode_signal_returns_name_for_negative_codes(self):
           """decode_signal translates negative return codes to signal names."""
           from harness.runtime import decode_signal

           assert decode_signal(-9) == "SIGKILL"
           assert decode_signal(-11) == "SIGSEGV"
           assert decode_signal(-15) == "SIGTERM"
           assert decode_signal(-6) == "SIGABRT"

       def test_decode_signal_returns_none_for_positive_codes(self):
           """decode_signal returns None for normal exit codes."""
           from harness.runtime import decode_signal

           assert decode_signal(0) is None
           assert decode_signal(1) is None
           assert decode_signal(127) is None

       def test_decode_signal_handles_unknown_signals(self):
           """decode_signal returns generic format for unknown signals."""
           from harness.runtime import decode_signal

           # Unknown signal number
           result = decode_signal(-99)
           assert result is not None
           assert "99" in result  # Should mention the signal number


   class TestPathMapper:
       """Tests for PathMapper abstraction."""

       def test_identity_mapper_returns_same_path(self):
           """IdentityMapper returns path unchanged (for LocalRuntime)."""
           from harness.runtime import IdentityMapper

           mapper = IdentityMapper()
           assert mapper.to_execution("/Users/dev/project") == "/Users/dev/project"
           assert mapper.to_execution("/tmp/foo") == "/tmp/foo"

       def test_volume_mapper_translates_paths(self):
           """VolumeMapper translates host paths to container paths."""
           from harness.runtime import VolumeMapper

           mapper = VolumeMapper(host_path="/Users/dev/project", container_path="/app")

           assert mapper.to_execution("/Users/dev/project") == "/app"
           assert mapper.to_execution("/Users/dev/project/src") == "/app/src"
           assert mapper.to_execution("/Users/dev/project/src/main.py") == "/app/src/main.py"

       def test_volume_mapper_ignores_non_matching_paths(self):
           """VolumeMapper returns unchanged path if prefix doesn't match."""
           from harness.runtime import VolumeMapper

           mapper = VolumeMapper(host_path="/Users/dev/project", container_path="/app")
           assert mapper.to_execution("/tmp/other") == "/tmp/other"


   class TestLocalRuntime:
       """Tests for LocalRuntime."""

       def test_execute_success(self, tmp_path):
           """LocalRuntime executes command and returns CompletedProcess."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()
           result = runtime.execute(["echo", "hello"], cwd=str(tmp_path))

           assert result.returncode == 0
           assert result.stdout.strip() == "hello"

       def test_execute_failure(self, tmp_path):
           """LocalRuntime returns non-zero for failed commands."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()
           result = runtime.execute(["false"], cwd=str(tmp_path))

           assert result.returncode != 0

       def test_execute_timeout(self, tmp_path):
           """LocalRuntime raises TimeoutExpired for slow commands."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()

           with pytest.raises(subprocess.TimeoutExpired):
               runtime.execute(["sleep", "10"], cwd=str(tmp_path), timeout=0.1)

       def test_execute_with_env(self, tmp_path):
           """LocalRuntime passes env vars to subprocess."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()
           result = runtime.execute(
               ["sh", "-c", "echo $TEST_API_KEY"],
               cwd=str(tmp_path),
               env={"TEST_API_KEY": "secret123"},
           )

           assert result.returncode == 0
           assert "secret123" in result.stdout

       def test_env_merges_with_os_environ(self, tmp_path):
           """LocalRuntime merges custom env with os.environ (PATH needed)."""
           from harness.runtime import LocalRuntime

           runtime = LocalRuntime()
           result = runtime.execute(
               ["echo", "works"],
               cwd=str(tmp_path),
               env={"CUSTOM_VAR": "value"},
           )

           assert result.returncode == 0
           assert "works" in result.stdout

       def test_no_lock_by_default(self, tmp_path):
           """LocalRuntime does NOT acquire lock by default (Council: Global Lock Suicide fix)."""
           from harness.runtime import LocalRuntime, GLOBAL_EXEC_LOCK
           import time

           runtime = LocalRuntime()

           # Run two commands concurrently - they should NOT block each other
           results = []
           def run_cmd(name):
               start = time.perf_counter()
               runtime.execute(["sleep", "0.1"], cwd=str(tmp_path))
               elapsed = time.perf_counter() - start
               results.append((name, elapsed))

           t1 = threading.Thread(target=run_cmd, args=("A",))
           t2 = threading.Thread(target=run_cmd, args=("B",))
           t1.start()
           t2.start()
           t1.join()
           t2.join()

           # Both should complete in ~0.1s (parallel), not ~0.2s (serial)
           total_time = max(r[1] for r in results)
           assert total_time < 0.15, f"Commands ran serially: {total_time:.2f}s"

       def test_exclusive_acquires_lock(self, tmp_path):
           """LocalRuntime acquires lock when exclusive=True (for git)."""
           from harness.runtime import LocalRuntime, GLOBAL_EXEC_LOCK
           import time

           runtime = LocalRuntime()

           # Run two exclusive commands - they SHOULD block each other
           results = []
           def run_exclusive(name):
               start = time.perf_counter()
               runtime.execute(["sleep", "0.1"], cwd=str(tmp_path), exclusive=True)
               elapsed = time.perf_counter() - start
               results.append((name, elapsed))

           t1 = threading.Thread(target=run_exclusive, args=("A",))
           t2 = threading.Thread(target=run_exclusive, args=("B",))
           t1.start()
           t2.start()
           t1.join()
           t2.join()

           # One should wait for the other: total time ~0.2s (serial)
           # The second thread should see elapsed > 0.1s
           max_time = max(r[1] for r in results)
           assert max_time >= 0.15, f"Commands ran in parallel: {max_time:.2f}s"


   class TestDockerRuntime:
       """Tests for DockerRuntime."""

       def test_execute_calls_docker_exec(self, mocker):
           """DockerRuntime calls docker exec with correct args."""
           from harness.runtime import DockerRuntime

           mock_run = mocker.patch("harness.runtime.subprocess.run")
           mock_run.return_value = subprocess.CompletedProcess(
               args=[], returncode=0, stdout="output", stderr=""
           )

           runtime = DockerRuntime(container="test-container")
           runtime.execute(["pytest", "tests/"], cwd="/app")

           mock_run.assert_called_once()
           call_args = mock_run.call_args[0][0]
           assert call_args[0] == "docker"
           assert "exec" in call_args
           assert "-w" in call_args
           assert "test-container" in call_args

       def test_path_mapping_with_volume_mapper(self, mocker):
           """DockerRuntime uses PathMapper to translate paths."""
           from harness.runtime import DockerRuntime, VolumeMapper

           mock_run = mocker.patch("harness.runtime.subprocess.run")
           mock_run.return_value = subprocess.CompletedProcess(
               args=[], returncode=0, stdout="", stderr=""
           )

           mapper = VolumeMapper("/Users/dev/project", "/app")
           runtime = DockerRuntime(container="test-container", path_mapper=mapper)
           runtime.execute(["ls"], cwd="/Users/dev/project/src")

           call_args = mock_run.call_args[0][0]
           w_index = call_args.index("-w")
           assert call_args[w_index + 1] == "/app/src"

       def test_env_vars_passed_via_e_flags(self, mocker):
           """DockerRuntime passes env vars via -e flags."""
           from harness.runtime import DockerRuntime

           mock_run = mocker.patch("harness.runtime.subprocess.run")
           mock_run.return_value = subprocess.CompletedProcess(
               args=[], returncode=0, stdout="", stderr=""
           )

           runtime = DockerRuntime(container="test-container")
           runtime.execute(
               ["echo", "test"],
               cwd="/app",
               env={"API_KEY": "secret", "DEBUG": "1"},
           )

           call_args = mock_run.call_args[0][0]
           env_args = []
           for i, arg in enumerate(call_args):
               if arg == "-e" and i + 1 < len(call_args):
                   env_args.append(call_args[i + 1])

           assert "API_KEY=secret" in env_args
           assert "DEBUG=1" in env_args

       def test_uid_mapping_passes_user_flag(self, mocker):
           """DockerRuntime passes --user flag with current UID:GID (Council: Root Escape fix)."""
           from harness.runtime import DockerRuntime

           mock_run = mocker.patch("harness.runtime.subprocess.run")
           mock_run.return_value = subprocess.CompletedProcess(
               args=[], returncode=0, stdout="", stderr=""
           )

           # DockerRuntime should auto-detect UID:GID
           runtime = DockerRuntime(container="test-container")
           runtime.execute(["ls"], cwd="/app")

           call_args = mock_run.call_args[0][0]
           # Find --user flag
           assert "--user" in call_args
           user_index = call_args.index("--user")
           user_value = call_args[user_index + 1]
           # Should be in format UID:GID
           assert ":" in user_value

       def test_uid_mapping_can_be_disabled(self, mocker):
           """DockerRuntime allows disabling UID mapping for trusted containers."""
           from harness.runtime import DockerRuntime

           mock_run = mocker.patch("harness.runtime.subprocess.run")
           mock_run.return_value = subprocess.CompletedProcess(
               args=[], returncode=0, stdout="", stderr=""
           )

           runtime = DockerRuntime(container="test-container", map_uid=False)
           runtime.execute(["ls"], cwd="/app")

           call_args = mock_run.call_args[0][0]
           assert "--user" not in call_args


   class TestRuntimeFactory:
       """Tests for runtime factory function."""

       def test_create_local_runtime(self):
           """create_runtime returns LocalRuntime by default."""
           from harness.runtime import create_runtime, LocalRuntime

           runtime = create_runtime()
           assert isinstance(runtime, LocalRuntime)

       def test_create_docker_runtime_from_env(self, monkeypatch):
           """create_runtime returns DockerRuntime when HARNESS_RUNTIME=docker."""
           from harness.runtime import create_runtime, DockerRuntime

           monkeypatch.setenv("HARNESS_RUNTIME", "docker")
           monkeypatch.setenv("HARNESS_DOCKER_CONTAINER", "my-container")
           monkeypatch.setenv("HARNESS_DOCKER_HOST_PATH", "/Users/dev")
           monkeypatch.setenv("HARNESS_DOCKER_CONTAINER_PATH", "/app")

           runtime = create_runtime()
           assert isinstance(runtime, DockerRuntime)
           assert runtime.container == "my-container"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_runtime.py -v
   ```
   Expected: FAIL (module harness.runtime not found)

3. **Implement MINIMAL code:**

   Create `src/harness/runtime.py`:
   ```python
   """Runtime abstraction for command execution.

   Provides LocalRuntime (host execution) and DockerRuntime (container execution)
   with unified interface for path mapping and environment injection.

   Council Fixes Applied:
   - Root Escape: DockerRuntime passes --user $(id -u):$(id -g) by default
   - Blind Execution: All runtimes accept env parameter for API keys
   """

   from __future__ import annotations

   import os
   import signal
   import subprocess
   import threading
   from abc import ABC, abstractmethod
   from typing import Protocol


   # Global lock for all command execution (protects .git/index, etc.)
   GLOBAL_EXEC_LOCK = threading.Lock()


   # =============================================================================
   # Signal Decoding (Council: Missing Signal fix)
   # =============================================================================

   def decode_signal(returncode: int) -> str | None:
       """Decode negative return code to signal name.

       Council Fix (Missing Signal): LLM agents can't interpret -9 or -11.
       This gives them the 'why' (SIGKILL = OOM, SIGSEGV = crash) not just 'what'.

       Args:
           returncode: Process return code (negative if killed by signal)

       Returns:
           Signal name (e.g., "SIGKILL") or None if not a signal
       """
       if returncode >= 0:
           return None

       sig_num = -returncode
       try:
           return signal.Signals(sig_num).name
       except ValueError:
           return f"SIG{sig_num}"  # Unknown signal


   # =============================================================================
   # Path Mapping
   # =============================================================================

   class PathMapper(ABC):
       """Abstract base class for path translation."""

       @abstractmethod
       def to_execution(self, host_path: str) -> str:
           """Translate host path to execution environment path."""
           ...


   class IdentityMapper(PathMapper):
       """Identity mapper - returns path unchanged (for LocalRuntime)."""

       def to_execution(self, host_path: str) -> str:
           return host_path


   class VolumeMapper(PathMapper):
       """Maps host paths to container paths based on volume mount."""

       def __init__(self, host_path: str, container_path: str):
           self.host_path = host_path.rstrip("/")
           self.container_path = container_path.rstrip("/")

       def to_execution(self, host_path: str) -> str:
           if host_path.startswith(self.host_path):
               return host_path.replace(self.host_path, self.container_path, 1)
           return host_path


   # =============================================================================
   # Runtime Protocol and Implementations
   # =============================================================================

   class Runtime(Protocol):
       """Protocol for command execution runtimes."""

       def execute(
           self,
           args: list[str],
           cwd: str,
           timeout: int = 60,
           env: dict[str, str] | None = None,
           exclusive: bool = False,
       ) -> subprocess.CompletedProcess:
           """Execute command and return result.

           Args:
               exclusive: If True, acquire GLOBAL_EXEC_LOCK (for git operations).
                         If False (default), run without locking (parallel OK).
           """
           ...


   class LocalRuntime:
       """Execute commands locally.

       Council Fix (Global Lock Suicide): Does NOT lock by default.
       Only acquires GLOBAL_EXEC_LOCK when exclusive=True (for git).
       """

       def __init__(self, path_mapper: PathMapper | None = None):
           self.path_mapper = path_mapper or IdentityMapper()

       def execute(
           self,
           args: list[str],
           cwd: str,
           timeout: int = 60,
           env: dict[str, str] | None = None,
           exclusive: bool = False,
       ) -> subprocess.CompletedProcess:
           # Merge custom env with os.environ (need PATH, etc.)
           merged_env = {**os.environ, **(env or {})}
           exec_cwd = self.path_mapper.to_execution(cwd)

           def run_subprocess():
               return subprocess.run(
                   args,
                   cwd=exec_cwd,
                   capture_output=True,
                   text=True,
                   timeout=timeout,
                   env=merged_env,
               )

           # Council Fix: Only lock when exclusive=True (git operations)
           if exclusive:
               with GLOBAL_EXEC_LOCK:
                   return run_subprocess()
           else:
               return run_subprocess()


   class DockerRuntime:
       """Execute commands in existing Docker container.

       Council Fixes:
       - Root Escape: Passes --user $(id -u):$(id -g) by default
       - Global Lock Suicide: Only locks when exclusive=True
       """

       def __init__(
           self,
           container: str,
           path_mapper: PathMapper | None = None,
           map_uid: bool = True,
       ):
           self.container = container
           self.path_mapper = path_mapper or IdentityMapper()
           self.map_uid = map_uid
           self._uid_gid = f"{os.getuid()}:{os.getgid()}" if map_uid else None

       def execute(
           self,
           args: list[str],
           cwd: str,
           timeout: int = 60,
           env: dict[str, str] | None = None,
           exclusive: bool = False,
       ) -> subprocess.CompletedProcess:
           container_cwd = self.path_mapper.to_execution(cwd)

           # Build docker exec command
           cmd = ["docker", "exec"]

           # Council Fix: Add --user flag for UID mapping
           if self._uid_gid:
               cmd.extend(["--user", self._uid_gid])

           # Add env vars via -e flags
           for key, value in (env or {}).items():
               cmd.extend(["-e", f"{key}={value}"])

           cmd.extend(["-w", container_cwd, self.container])
           cmd.extend(args)

           def run_docker():
               return subprocess.run(
                   cmd,
                   capture_output=True,
                   text=True,
                   timeout=timeout,
               )

           # Council Fix: Only lock when exclusive=True (git operations)
           if exclusive:
               with GLOBAL_EXEC_LOCK:
                   return run_docker()
           else:
               return run_docker()


   # =============================================================================
   # Factory
   # =============================================================================

   def create_runtime() -> Runtime:
       """Create runtime based on environment configuration.

       Environment variables:
           HARNESS_RUNTIME: "local" (default) or "docker"
           HARNESS_DOCKER_CONTAINER: Container name/ID (required for docker)
           HARNESS_DOCKER_HOST_PATH: Host path prefix for volume mapping
           HARNESS_DOCKER_CONTAINER_PATH: Container path prefix for volume mapping
           HARNESS_DOCKER_MAP_UID: "true" (default) or "false" for UID mapping
       """
       runtime_type = os.environ.get("HARNESS_RUNTIME", "local")

       if runtime_type == "docker":
           container = os.environ.get("HARNESS_DOCKER_CONTAINER")
           if not container:
               raise ValueError("HARNESS_DOCKER_CONTAINER required for docker runtime")

           host_path = os.environ.get("HARNESS_DOCKER_HOST_PATH", "")
           container_path = os.environ.get("HARNESS_DOCKER_CONTAINER_PATH", "")
           map_uid = os.environ.get("HARNESS_DOCKER_MAP_UID", "true").lower() == "true"

           path_mapper: PathMapper
           if host_path and container_path:
               path_mapper = VolumeMapper(host_path, container_path)
           else:
               path_mapper = IdentityMapper()

           return DockerRuntime(
               container=container,
               path_mapper=path_mapper,
               map_uid=map_uid,
           )

       return LocalRuntime()
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_runtime.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(runtime): add runtime.py with LocalRuntime, DockerRuntime (UID mapping), and PathMapper"
   ```

---

### Task 2: Trajectory Logger with Efficient Tail (trajectory.py) - NEW FILE

**Effort:** standard (10-15 tool calls)

**Files:**
- Create: `src/harness/trajectory.py`
- Create: `tests/harness/test_trajectory.py`

**Council Fixes Applied:**
- **Lock Convoy (preparation):** TrajectoryLogger has its own lock, separate from StateManager

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_trajectory.py
   import json
   import time
   import threading
   import pytest
   from concurrent.futures import ThreadPoolExecutor


   class TestTrajectoryLogger:
       """Tests for TrajectoryLogger."""

       def test_creates_file_on_first_log(self, tmp_path):
           """TrajectoryLogger creates .claude/trajectory.jsonl on first log."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)
           logger.log({"event": "test", "data": 123})

           log_file = tmp_path / ".claude" / "trajectory.jsonl"
           assert log_file.exists()

       def test_appends_jsonl(self, tmp_path):
           """TrajectoryLogger appends JSON lines, not replaces."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)
           logger.log({"event": "first"})
           logger.log({"event": "second"})

           log_file = tmp_path / ".claude" / "trajectory.jsonl"
           lines = log_file.read_text().strip().split("\n")

           assert len(lines) == 2
           assert json.loads(lines[0])["event"] == "first"
           assert json.loads(lines[1])["event"] == "second"

       def test_thread_safe(self, tmp_path):
           """TrajectoryLogger handles concurrent writes without corruption."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)

           def write_event(i):
               logger.log({"event": "concurrent", "id": i})

           with ThreadPoolExecutor(max_workers=10) as executor:
               list(executor.map(write_event, range(100)))

           log_file = tmp_path / ".claude" / "trajectory.jsonl"
           lines = log_file.read_text().strip().split("\n")

           assert len(lines) == 100
           for line in lines:
               parsed = json.loads(line)
               assert parsed["event"] == "concurrent"

       def test_tail_returns_last_n(self, tmp_path):
           """tail(n) returns last n events in chronological order."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)
           for i in range(10):
               logger.log({"event": "test", "id": i})

           tail = logger.tail(3)

           assert len(tail) == 3
           assert tail[0]["id"] == 7
           assert tail[1]["id"] == 8
           assert tail[2]["id"] == 9

       def test_tail_empty_file(self, tmp_path):
           """tail() handles empty/missing file."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)
           assert logger.tail(5) == []

       def test_tail_fewer_than_n(self, tmp_path):
           """tail(n) returns all if fewer than n entries."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)
           logger.log({"event": "one"})
           logger.log({"event": "two"})

           tail = logger.tail(10)

           assert len(tail) == 2
           assert tail[0]["event"] == "one"
           assert tail[1]["event"] == "two"

       def test_tail_large_file_performance(self, tmp_path):
           """tail(n) on large file is O(1), not O(file_size)."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)

           # Create ~1MB log file (10000 entries, ~100 bytes each)
           for i in range(10000):
               logger.log({"event": "bulk", "id": i, "padding": "x" * 50})

           log_file = tmp_path / ".claude" / "trajectory.jsonl"
           file_size = log_file.stat().st_size
           assert file_size > 500_000  # At least 500KB

           # tail(5) should complete in < 50ms (not read whole file)
           start = time.perf_counter()
           tail = logger.tail(5)
           elapsed = time.perf_counter() - start

           assert len(tail) == 5
           assert tail[-1]["id"] == 9999
           assert elapsed < 0.05, f"tail() took {elapsed:.3f}s, should be < 50ms"

       def test_crash_resilient_jsonl_format(self, tmp_path):
           """JSONL format survives partial writes (last line may be corrupt)."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)
           logger.log({"event": "one"})
           logger.log({"event": "two"})

           # Simulate crash: append partial JSON
           log_file = tmp_path / ".claude" / "trajectory.jsonl"
           with log_file.open("a") as f:
               f.write('{"event": "corrupt')  # No closing brace or newline

           # tail should return valid entries, skip corrupt last line
           tail = logger.tail(5)
           assert len(tail) == 2
           assert tail[0]["event"] == "one"
           assert tail[1]["event"] == "two"

       def test_separate_lock_from_state(self, tmp_path):
           """TrajectoryLogger has its own lock (Council: Lock Convoy fix)."""
           from harness.trajectory import TrajectoryLogger

           logger = TrajectoryLogger(tmp_path)
           # Verify logger has its own lock instance
           assert hasattr(logger, "_lock")
           assert isinstance(logger._lock, type(threading.Lock()))
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_trajectory.py -v
   ```
   Expected: FAIL (module harness.trajectory not found)

3. **Implement MINIMAL code:**

   Create `src/harness/trajectory.py`:
   ```python
   """Trajectory logging for agent event history.

   Uses JSONL format for crash resilience and efficient reverse-seek tail()
   for O(1) reads regardless of log size.

   Council Fix (Lock Convoy): Has its own lock, separate from StateManager.
   Handlers should release state lock BEFORE acquiring trajectory lock.
   """

   from __future__ import annotations

   import json
   import threading
   from pathlib import Path


   class TrajectoryLogger:
       """JSONL append-only log at .claude/trajectory.jsonl.

       Features:
       - Thread-safe writes with dedicated lock (separate from StateManager)
       - O(1) tail() using reverse-seek (reads from end of file)
       - JSONL format for crash resilience (only last line lost on crash)
       """

       # Block size for reverse reading (4KB is good for SSD)
       _BLOCK_SIZE = 4096

       def __init__(self, worktree_root: Path):
           self.log_file = Path(worktree_root) / ".claude" / "trajectory.jsonl"
           # Council Fix: Separate lock from StateManager to avoid convoy
           self._lock = threading.Lock()

       def log(self, event: dict) -> None:
           """Append event to trajectory (thread-safe)."""
           with self._lock:
               self.log_file.parent.mkdir(parents=True, exist_ok=True)
               with self.log_file.open("a") as f:
                   f.write(json.dumps(event) + "\n")

       def tail(self, n: int) -> list[dict]:
           """Return last n events using efficient reverse-seek.

           Reads from end of file in blocks, O(1) regardless of file size.
           Skips corrupt lines (crash resilience).
           """
           if not self.log_file.exists():
               return []

           with self._lock:
               return self._tail_reverse_seek(n)

       def _tail_reverse_seek(self, n: int) -> list[dict]:
           """Read last n lines by seeking from end of file."""
           lines: list[bytes] = []
           remaining_bytes = b""

           with self.log_file.open("rb") as f:
               # Seek to end, get file size
               f.seek(0, 2)
               file_size = f.tell()

               if file_size == 0:
                   return []

               position = file_size

               while len(lines) < n and position > 0:
                   # Calculate how much to read
                   read_size = min(self._BLOCK_SIZE, position)
                   position -= read_size

                   # Seek and read block
                   f.seek(position)
                   block = f.read(read_size)

                   # Prepend to remaining bytes and split into lines
                   data = block + remaining_bytes
                   split_lines = data.split(b"\n")

                   # First element is partial (unless at start of file)
                   if position > 0:
                       remaining_bytes = split_lines[0]
                       complete_lines = split_lines[1:]
                   else:
                       remaining_bytes = b""
                       complete_lines = split_lines

                   # Add complete lines (in reverse order)
                   for line in reversed(complete_lines):
                       if line.strip():
                           lines.append(line)
                           if len(lines) >= n:
                               break

           # Handle any remaining bytes at start of file
           if remaining_bytes.strip() and len(lines) < n:
               lines.append(remaining_bytes)

           # Reverse to get chronological order and take last n
           lines = list(reversed(lines[-n:]))

           # Parse JSON, skip corrupt lines
           result = []
           for line in lines:
               try:
                   result.append(json.loads(line.decode()))
               except (json.JSONDecodeError, UnicodeDecodeError):
                   # Skip corrupt lines (crash resilience)
                   continue

           return result
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_trajectory.py -v
   ```
   Expected: PASS (all tests green, including performance test)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(trajectory): add trajectory.py with O(1) reverse-seek tail and separate lock"
   ```

---

### Task 3: JSON State Schema with DAG and Worker ID (state.py)

**Effort:** complex (15-25 tool calls)

**Files:**
- Modify: `src/harness/state.py`
- Modify: `tests/harness/test_state.py`

**Council Fixes Applied:**
- **Markdown Database:** Delete `_parse_frontmatter` and `_to_frontmatter`, use JSON
- **Lost Ack:** Add `claimed_by` field to Task for worker_id idempotency
- **Zombie Deadlock:** Add `timeout_seconds` and `is_timed_out()` for dead task recovery
- **Check-Then-Act Race:** Add atomic `claim_task()` and `complete_task()` methods to StateManager

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_state.py (replace existing content)

   from datetime import datetime, timezone, timedelta
   import json
   import threading
   import pytest
   from harness.state import Task, TaskStatus, WorkflowState, StateManager


   class TestTaskModel:
       """Tests for Task model."""

       def test_task_model_validation(self):
           """Task model validates required fields."""
           task = Task(
               id="task-1",
               description="Setup environment",
               status=TaskStatus.PENDING,
               dependencies=[],
           )

           assert task.id == "task-1"
           assert task.status == TaskStatus.PENDING

       def test_task_model_has_timeout(self):
           """Task model has timeout_seconds field with default."""
           task = Task(
               id="task-1",
               description="Test",
               status=TaskStatus.PENDING,
               dependencies=[],
           )

           # Default timeout is 10 minutes (600 seconds)
           assert task.timeout_seconds == 600

       def test_task_model_custom_timeout(self):
           """Task model accepts custom timeout."""
           task = Task(
               id="task-1",
               description="Long task",
               status=TaskStatus.PENDING,
               dependencies=[],
               timeout_seconds=3600,
           )

           assert task.timeout_seconds == 3600

       def test_task_has_claimed_by_field(self):
           """Task model has claimed_by for worker_id tracking (Council: Lost Ack fix)."""
           task = Task(
               id="task-1",
               description="Test",
               status=TaskStatus.RUNNING,
               dependencies=[],
               claimed_by="worker-abc123",
           )

           assert task.claimed_by == "worker-abc123"

       def test_task_is_timed_out_method(self):
           """Task.is_timed_out() correctly identifies expired tasks."""
           # Not started - not timed out
           task_pending = Task(id="t1", description="", status=TaskStatus.PENDING, dependencies=[])
           assert not task_pending.is_timed_out()

           # Just started - not timed out
           now = datetime.now(timezone.utc).isoformat()
           task_recent = Task(
               id="t2", description="", status=TaskStatus.RUNNING,
               dependencies=[], started_at=now, timeout_seconds=60
           )
           assert not task_recent.is_timed_out()

           # Started long ago - timed out
           old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
           task_old = Task(
               id="t3", description="", status=TaskStatus.RUNNING,
               dependencies=[], started_at=old, timeout_seconds=60
           )
           assert task_old.is_timed_out()


   class TestWorkflowState:
       """Tests for WorkflowState model."""

       def test_workflow_state_v2_schema(self):
           """WorkflowState v2 has tasks dict."""
           state = WorkflowState(
               workflow="execute-plan",
               plan="docs/plans/test.md",
               worktree="/tmp/test",
               base_sha="abc123",
               tasks={
                   "task-1": Task(
                       id="task-1",
                       description="First",
                       status=TaskStatus.PENDING,
                       dependencies=[],
                   ),
               },
           )

           assert "task-1" in state.tasks
           assert state.tasks["task-1"].description == "First"

       def test_get_claimable_task_no_deps(self):
           """get_claimable_task returns first pending task with no deps."""
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree="/tmp",
               base_sha="abc",
               tasks={
                   "task-1": Task(id="task-1", description="A", status=TaskStatus.PENDING, dependencies=[]),
                   "task-2": Task(id="task-2", description="B", status=TaskStatus.PENDING, dependencies=[]),
               },
           )

           claimable = state.get_claimable_task()
           assert claimable == "task-1"

       def test_get_claimable_task_with_deps(self):
           """get_claimable_task respects dependency order."""
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree="/tmp",
               base_sha="abc",
               tasks={
                   "task-1": Task(id="task-1", description="A", status=TaskStatus.COMPLETED, dependencies=[]),
                   "task-2": Task(id="task-2", description="B", status=TaskStatus.PENDING, dependencies=["task-1"]),
                   "task-3": Task(id="task-3", description="C", status=TaskStatus.PENDING, dependencies=["task-2"]),
               },
           )

           claimable = state.get_claimable_task()
           assert claimable == "task-2"

       def test_get_claimable_task_multiple_deps(self):
           """get_claimable_task waits for all dependencies."""
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree="/tmp",
               base_sha="abc",
               tasks={
                   "task-1": Task(id="task-1", description="A", status=TaskStatus.COMPLETED, dependencies=[]),
                   "task-2": Task(id="task-2", description="B", status=TaskStatus.PENDING, dependencies=[]),
                   "task-3": Task(id="task-3", description="C", status=TaskStatus.PENDING, dependencies=["task-1", "task-2"]),
               },
           )

           claimable = state.get_claimable_task()
           assert claimable == "task-2"

       def test_get_claimable_task_none_available(self):
           """get_claimable_task returns None when all running/blocked (not timed out)."""
           now = datetime.now(timezone.utc).isoformat()
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree="/tmp",
               base_sha="abc",
               tasks={
                   "task-1": Task(
                       id="task-1",
                       description="A",
                       status=TaskStatus.RUNNING,
                       dependencies=[],
                       started_at=now,
                   ),
                   "task-2": Task(id="task-2", description="B", status=TaskStatus.PENDING, dependencies=["task-1"]),
               },
           )

           claimable = state.get_claimable_task()
           assert claimable is None

       def test_get_claimable_task_reclaims_timed_out(self):
           """get_claimable_task returns timed-out RUNNING task as claimable."""
           old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree="/tmp",
               base_sha="abc",
               tasks={
                   "task-1": Task(
                       id="task-1",
                       description="Zombie task",
                       status=TaskStatus.RUNNING,
                       dependencies=[],
                       started_at=old_time,
                       timeout_seconds=3600,
                   ),
               },
           )

           claimable = state.get_claimable_task()
           assert claimable == "task-1"

       def test_get_task_for_worker_idempotency(self):
           """get_task_for_worker returns existing task for worker (Council: Lost Ack fix)."""
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree="/tmp",
               base_sha="abc",
               tasks={
                   "task-1": Task(
                       id="task-1",
                       description="Already claimed",
                       status=TaskStatus.RUNNING,
                       dependencies=[],
                       claimed_by="worker-123",
                       started_at=datetime.now(timezone.utc).isoformat(),
                   ),
                   "task-2": Task(id="task-2", description="B", status=TaskStatus.PENDING, dependencies=[]),
               },
           )

           # Same worker should get their existing task back (idempotency)
           task_id = state.get_task_for_worker("worker-123")
           assert task_id == "task-1"

       def test_get_task_for_worker_assigns_new(self):
           """get_task_for_worker assigns new task if worker has none."""
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree="/tmp",
               base_sha="abc",
               tasks={
                   "task-1": Task(id="task-1", description="A", status=TaskStatus.PENDING, dependencies=[]),
               },
           )

           task_id = state.get_task_for_worker("worker-new")
           assert task_id == "task-1"


   class TestStateManagerJSON:
       """Tests for StateManager with JSON persistence (Council: Markdown Database fix)."""

       def test_state_file_is_json(self, tmp_path):
           """StateManager uses .json file, not .md (Council fix)."""
           manager = StateManager(tmp_path)
           assert manager.state_file.suffix == ".json"
           assert "dev-workflow-state" in manager.state_file.name

       def test_save_creates_valid_json(self, tmp_path):
           """StateManager.save() creates valid JSON file."""
           manager = StateManager(tmp_path)
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_path),
               base_sha="abc123",
               tasks={
                   "task-1": Task(id="task-1", description="Test", status=TaskStatus.PENDING, dependencies=[]),
               },
           )

           manager.save(state)

           # Verify it's valid JSON
           content = manager.state_file.read_text()
           parsed = json.loads(content)
           assert parsed["workflow"] == "execute-plan"
           assert "task-1" in parsed["tasks"]

       def test_load_reads_json(self, tmp_path):
           """StateManager.load() reads JSON file."""
           manager = StateManager(tmp_path)
           state = WorkflowState(
               workflow="subagent",
               plan="/plan.md",
               worktree=str(tmp_path),
               base_sha="def456",
               tasks={
                   "task-1": Task(id="task-1", description="First", status=TaskStatus.COMPLETED, dependencies=[]),
                   "task-2": Task(id="task-2", description="Second", status=TaskStatus.PENDING, dependencies=["task-1"]),
               },
           )

           manager.save(state)
           loaded = manager.load()

           assert loaded.workflow == "subagent"
           assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED
           assert loaded.tasks["task-2"].dependencies == ["task-1"]

       def test_update_modifies_json(self, tmp_path):
           """StateManager.update() modifies JSON atomically."""
           manager = StateManager(tmp_path)
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_path),
               base_sha="abc",
               tasks={
                   "task-1": Task(id="task-1", description="Test", status=TaskStatus.PENDING, dependencies=[]),
               },
           )
           manager.save(state)

           # Update a task
           new_task = Task(id="task-1", description="Test", status=TaskStatus.COMPLETED, dependencies=[])
           manager.update(tasks={"task-1": new_task})

           loaded = manager.load()
           assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED

       def test_no_frontmatter_methods(self, tmp_path):
           """StateManager should NOT have _parse_frontmatter or _to_frontmatter (Council fix)."""
           manager = StateManager(tmp_path)

           assert not hasattr(manager, "_parse_frontmatter")
           assert not hasattr(manager, "_to_frontmatter")


   class TestStateManagerAtomicMethods:
       """Tests for atomic claim_task/complete_task (Council: Check-Then-Act Race fix)."""

       def test_claim_task_atomic(self, tmp_path):
           """StateManager.claim_task() is atomic - find, update, save in one lock."""
           manager = StateManager(tmp_path)
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_path),
               base_sha="abc123",
               tasks={
                   "task-1": Task(id="task-1", description="First", status=TaskStatus.PENDING, dependencies=[]),
               },
           )
           manager.save(state)

           result = manager.claim_task("worker-123")

           assert result is not None
           assert result.id == "task-1"
           assert result.status == TaskStatus.RUNNING
           assert result.claimed_by == "worker-123"

           # Verify persisted
           loaded = manager.load()
           assert loaded.tasks["task-1"].status == TaskStatus.RUNNING

       def test_claim_task_returns_existing_for_same_worker(self, tmp_path):
           """claim_task returns existing task for same worker (idempotency)."""
           manager = StateManager(tmp_path)
           now = datetime.now(timezone.utc).isoformat()
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_path),
               base_sha="abc123",
               tasks={
                   "task-1": Task(
                       id="task-1",
                       description="Already claimed",
                       status=TaskStatus.RUNNING,
                       dependencies=[],
                       claimed_by="worker-123",
                       started_at=now,
                   ),
                   "task-2": Task(id="task-2", description="Pending", status=TaskStatus.PENDING, dependencies=[]),
               },
           )
           manager.save(state)

           # Same worker should get same task back
           result = manager.claim_task("worker-123")
           assert result.id == "task-1"

       def test_claim_task_race_condition_prevented(self, tmp_path):
           """Concurrent claim_task calls don't double-assign (Council fix)."""
           manager = StateManager(tmp_path)
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_path),
               base_sha="abc123",
               tasks={
                   "task-1": Task(id="task-1", description="Only one", status=TaskStatus.PENDING, dependencies=[]),
               },
           )
           manager.save(state)

           results = []
           def claim_as_worker(worker_id):
               result = manager.claim_task(worker_id)
               results.append((worker_id, result))

           # Two workers try to claim simultaneously
           t1 = threading.Thread(target=claim_as_worker, args=("worker-A",))
           t2 = threading.Thread(target=claim_as_worker, args=("worker-B",))
           t1.start()
           t2.start()
           t1.join()
           t2.join()

           # Only ONE should get the task, the other gets None
           claimed = [r for r in results if r[1] is not None]
           assert len(claimed) == 1, f"Double-claim detected: {results}"

       def test_complete_task_atomic(self, tmp_path):
           """StateManager.complete_task() is atomic."""
           manager = StateManager(tmp_path)
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_path),
               base_sha="abc123",
               tasks={
                   "task-1": Task(
                       id="task-1",
                       description="Running",
                       status=TaskStatus.RUNNING,
                       dependencies=[],
                       claimed_by="worker-123",
                   ),
               },
           )
           manager.save(state)

           result = manager.complete_task("task-1", "worker-123")

           assert result is not None
           assert result.status == TaskStatus.COMPLETED

           # Verify persisted
           loaded = manager.load()
           assert loaded.tasks["task-1"].status == TaskStatus.COMPLETED

       def test_complete_task_validates_ownership(self, tmp_path):
           """complete_task fails if worker doesn't own task."""
           manager = StateManager(tmp_path)
           state = WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_path),
               base_sha="abc123",
               tasks={
                   "task-1": Task(
                       id="task-1",
                       description="Running",
                       status=TaskStatus.RUNNING,
                       dependencies=[],
                       claimed_by="worker-123",
                   ),
               },
           )
           manager.save(state)

           # Different worker tries to complete
           result = manager.complete_task("task-1", "worker-other")
           assert result is None  # Should fail
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_state.py -v
   ```
   Expected: FAIL (Task, TaskStatus not defined, frontmatter methods still exist)

3. **Implement MINIMAL code:**

   Replace `src/harness/state.py`:
   ```python
   # src/harness/state.py
   """
   Pydantic state models for workflow management.

   WorkflowState is the canonical schema for dev-workflow state.
   StateManager handles persistence to JSON format.

   Council Fixes Applied:
   - Markdown Database: Uses JSON, not Markdown frontmatter
   - Lost Ack: Task model has claimed_by for worker_id idempotency
   - Zombie Deadlock: Task.is_timed_out() for dead task recovery
   """

   from pydantic import BaseModel, Field
   from typing import Literal
   from pathlib import Path
   from datetime import datetime, timezone
   from enum import Enum
   import threading
   import json


   class TaskStatus(str, Enum):
       """Task execution status."""
       PENDING = "pending"
       RUNNING = "running"
       COMPLETED = "completed"
       FAILED = "failed"


   class Task(BaseModel):
       """Individual task in a workflow plan.

       Council Fix (Lost Ack): claimed_by tracks which worker owns this task.
       Council Fix (Zombie): is_timed_out() detects abandoned tasks.
       """
       id: str
       description: str
       status: TaskStatus = TaskStatus.PENDING
       dependencies: list[str] = Field(default_factory=list)
       started_at: str | None = None
       completed_at: str | None = None
       claimed_by: str | None = None  # Council: Lost Ack fix - worker_id
       timeout_seconds: int = 600  # Default 10 minutes

       def is_timed_out(self) -> bool:
           """Check if task has exceeded its timeout.

           Returns True if:
           - Task is RUNNING
           - started_at exists
           - Current time > started_at + timeout_seconds
           """
           if self.status != TaskStatus.RUNNING:
               return False
           if not self.started_at:
               return False

           try:
               started = datetime.fromisoformat(self.started_at)
               if started.tzinfo is None:
                   started = started.replace(tzinfo=timezone.utc)
               now = datetime.now(timezone.utc)
               elapsed = (now - started).total_seconds()
               return elapsed > self.timeout_seconds
           except (ValueError, TypeError):
               return False


   class WorkflowState(BaseModel):
       """Workflow state with DAG-based task tracking."""
       workflow: Literal["execute-plan", "subagent"] = Field(
           ..., description="Execution mode"
       )
       plan: str = Field(..., description="Path to plan file")
       worktree: str = Field(..., description="Absolute path to worktree")
       base_sha: str = Field(..., description="Base commit SHA before workflow")
       last_commit: str | None = Field(None, description="Last commit SHA")
       enabled: bool = Field(True, description="Workflow active")

       # v2.0: DAG-based task tracking
       tasks: dict[str, Task] = Field(default_factory=dict)

       def get_claimable_task(self) -> str | None:
           """Find first claimable task (O(N) traversal).

           Returns task_id or None if no tasks ready.
           A task is claimable if:
           - status is PENDING and all dependencies COMPLETED, OR
           - status is RUNNING but has timed out (zombie recovery)
           """
           for task_id, task in self.tasks.items():
               # Case 1: Timed-out RUNNING task (zombie recovery)
               if task.status == TaskStatus.RUNNING and task.is_timed_out():
                   return task_id

               # Case 2: PENDING task with all deps completed
               if task.status != TaskStatus.PENDING:
                   continue

               deps_ready = all(
                   self.tasks[dep].status == TaskStatus.COMPLETED
                   for dep in task.dependencies
                   if dep in self.tasks
               )
               if deps_ready:
                   return task_id

           return None

       def get_task_for_worker(self, worker_id: str) -> str | None:
           """Get task for worker with idempotency (Council: Lost Ack fix).

           If worker already has a RUNNING task, return that task (retry recovery).
           Otherwise, find a new claimable task.
           """
           # First: Check if worker already owns a running task
           for task_id, task in self.tasks.items():
               if task.status == TaskStatus.RUNNING and task.claimed_by == worker_id:
                   if not task.is_timed_out():
                       return task_id

           # No existing task - find a new claimable one
           return self.get_claimable_task()


   class PendingHandoff(BaseModel):
       """Handoff file for session resume."""
       mode: Literal["sequential", "subagent"]
       plan: str


   class StateManager:
       """Manages workflow state with JSON file persistence.

       Thread-safe: All public methods are protected by a Lock.

       Council Fix (Markdown Database): Uses JSON, not Markdown frontmatter.
       """

       def __init__(self, worktree_root: Path):
           self.worktree_root = Path(worktree_root)
           # Council Fix: Use .json instead of .md
           self.state_file = self.worktree_root / ".claude" / "dev-workflow-state.json"
           self._state: WorkflowState | None = None
           self._lock = threading.Lock()

       def load(self) -> WorkflowState | None:
           """Load state from JSON file (thread-safe)."""
           with self._lock:
               if not self.state_file.exists():
                   return None

               content = self.state_file.read_text()
               data = json.loads(content)
               self._state = WorkflowState.model_validate(data)
               return self._state

       def save(self, state: WorkflowState) -> None:
           """Save state to JSON file atomically (thread-safe)."""
           with self._lock:
               self._state = state
               self.state_file.parent.mkdir(parents=True, exist_ok=True)

               # Atomic write via temp file + rename
               content = state.model_dump_json(indent=2)
               temp_file = self.state_file.with_suffix(".tmp")
               temp_file.write_text(content)
               temp_file.rename(self.state_file)

       def update(self, **kwargs) -> WorkflowState:
           """Update specific fields atomically (thread-safe).

           Auto-loads state from disk if not already loaded.
           """
           with self._lock:
               if not self._state:
                   if self.state_file.exists():
                       content = self.state_file.read_text()
                       data = json.loads(content)
                       self._state = WorkflowState.model_validate(data)
                   if not self._state:
                       raise ValueError("No state loaded and no state file exists")

               self._state = self._state.model_copy(update=kwargs)

               # Atomic save
               self.state_file.parent.mkdir(parents=True, exist_ok=True)
               content = self._state.model_dump_json(indent=2)
               temp_file = self.state_file.with_suffix(".tmp")
               temp_file.write_text(content)
               temp_file.rename(self.state_file)
               return self._state

       def claim_task(self, worker_id: str) -> Task | None:
           """Atomically claim next available task for worker.

           Council Fix (Check-Then-Act Race): This method performs find, update,
           and save in ONE critical section. No load-then-update pattern.

           Args:
               worker_id: Unique identifier for the claiming worker

           Returns:
               The claimed Task (now RUNNING), or None if no tasks available
           """
           with self._lock:
               # Load fresh state
               if not self.state_file.exists():
                   return None
               content = self.state_file.read_text()
               data = json.loads(content)
               self._state = WorkflowState.model_validate(data)

               # Check for existing task owned by this worker (idempotency)
               for task_id, task in self._state.tasks.items():
                   if task.status == TaskStatus.RUNNING and task.claimed_by == worker_id:
                       if not task.is_timed_out():
                           return task  # Return existing, don't modify

               # Find claimable task
               task_id = self._state.get_claimable_task()
               if not task_id:
                   return None

               # Update task atomically
               task = self._state.tasks[task_id]
               now = datetime.now(timezone.utc).isoformat()
               updated_task = task.model_copy(update={
                   "status": TaskStatus.RUNNING,
                   "started_at": now,
                   "claimed_by": worker_id,
               })
               self._state.tasks[task_id] = updated_task

               # Save atomically
               self.state_file.parent.mkdir(parents=True, exist_ok=True)
               content = self._state.model_dump_json(indent=2)
               temp_file = self.state_file.with_suffix(".tmp")
               temp_file.write_text(content)
               temp_file.rename(self.state_file)

               return updated_task

       def complete_task(self, task_id: str, worker_id: str) -> Task | None:
           """Atomically mark task as completed.

           Council Fix (Check-Then-Act Race): Validates ownership and updates
           in ONE critical section.

           Args:
               task_id: ID of task to complete
               worker_id: Worker claiming completion (must own the task)

           Returns:
               The completed Task, or None if validation fails
           """
           with self._lock:
               # Load fresh state
               if not self.state_file.exists():
                   return None
               content = self.state_file.read_text()
               data = json.loads(content)
               self._state = WorkflowState.model_validate(data)

               if task_id not in self._state.tasks:
                   return None

               task = self._state.tasks[task_id]

               # Validate ownership
               if task.claimed_by != worker_id:
                   return None

               # Update task atomically
               now = datetime.now(timezone.utc).isoformat()
               updated_task = task.model_copy(update={
                   "status": TaskStatus.COMPLETED,
                   "completed_at": now,
               })
               self._state.tasks[task_id] = updated_task

               # Save atomically
               self.state_file.parent.mkdir(parents=True, exist_ok=True)
               content = self._state.model_dump_json(indent=2)
               temp_file = self.state_file.with_suffix(".tmp")
               temp_file.write_text(content)
               temp_file.rename(self.state_file)

               return updated_task
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_state.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(state): migrate to JSON, add atomic claim_task/complete_task methods"
   ```

---

### Task 4: Daemon Handlers with Lock Convoy Fix (daemon.py)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/daemon.py`
- Modify: `tests/harness/test_daemon.py`

**Council Fixes Applied:**
- **Lock Convoy:** Release state lock BEFORE acquiring trajectory lock
- **Lost Ack:** `task_claim` requires `worker_id` and returns existing task for retries
- **Missing Signal:** `exec` handler decodes negative return codes to signal names in logs and response
- **Check-Then-Act Race:** Handlers call atomic `state_manager.claim_task()` / `complete_task()` methods

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_daemon.py (add to existing file)

   import time
   from datetime import datetime, timezone, timedelta
   from harness.state import Task, TaskStatus, WorkflowState


   # Add these fixtures if not present
   @pytest.fixture
   def daemon_with_state(socket_path, worktree):
       """Daemon with state manager and trajectory logger."""
       from harness.daemon import HarnessDaemon
       from harness.state import StateManager, WorkflowState, Task, TaskStatus

       manager = StateManager(worktree)
       manager.save(
           WorkflowState(
               workflow="execute-plan",
               plan="/plan.md",
               worktree=str(worktree),
               base_sha="abc123",
               tasks={},
           )
       )

       daemon = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.1)

       yield daemon

       daemon.shutdown()
       server_thread.join(timeout=1)


   def test_handle_task_claim_returns_claimable(daemon_with_state, socket_path):
       """task_claim returns first claimable task."""
       from harness.state import Task, TaskStatus

       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First task", status=TaskStatus.PENDING, dependencies=[]),
               "task-2": Task(id="task-2", description="Second task", status=TaskStatus.PENDING, dependencies=["task-1"]),
           }
       )

       response = send_command(socket_path, {"command": "task_claim", "worker_id": "worker-test"})

       assert response["status"] == "ok"
       assert response["data"]["task_id"] == "task-1"
       assert response["data"]["description"] == "First task"


   def test_handle_task_claim_requires_worker_id(daemon_with_state, socket_path):
       """task_claim requires worker_id parameter (Council: Lost Ack fix)."""
       response = send_command(socket_path, {"command": "task_claim"})

       assert response["status"] == "error"
       assert "worker_id" in response["message"].lower()


   def test_handle_task_claim_idempotency(daemon_with_state, socket_path):
       """task_claim returns same task for same worker (Council: Lost Ack fix)."""
       from harness.state import Task, TaskStatus

       now = datetime.now(timezone.utc).isoformat()
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Already claimed",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   claimed_by="worker-123",
                   started_at=now,
               ),
               "task-2": Task(id="task-2", description="Pending", status=TaskStatus.PENDING, dependencies=[]),
           }
       )

       # Same worker should get their existing task back
       response = send_command(socket_path, {"command": "task_claim", "worker_id": "worker-123"})

       assert response["status"] == "ok"
       assert response["data"]["task_id"] == "task-1"
       assert response["data"]["is_retry"] is True


   def test_handle_task_claim_marks_running(daemon_with_state, socket_path):
       """task_claim marks claimed task as RUNNING with claimed_by."""
       from harness.state import Task, TaskStatus

       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First", status=TaskStatus.PENDING, dependencies=[]),
           }
       )

       send_command(socket_path, {"command": "task_claim", "worker_id": "worker-new"})

       state = daemon_with_state.state_manager.load()
       assert state.tasks["task-1"].status == TaskStatus.RUNNING
       assert state.tasks["task-1"].claimed_by == "worker-new"


   def test_handle_task_claim_reclaims_timed_out(daemon_with_state, socket_path):
       """task_claim reclaims a timed-out zombie task."""
       from harness.state import Task, TaskStatus

       old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Zombie task",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   started_at=old_time,
                   timeout_seconds=3600,
                   claimed_by="dead-worker",
               ),
           }
       )

       response = send_command(socket_path, {"command": "task_claim", "worker_id": "new-worker"})

       assert response["status"] == "ok"
       assert response["data"]["task_id"] == "task-1"
       assert response["data"]["is_reclaim"] is True

       # Verify claimed_by was updated
       state = daemon_with_state.state_manager.load()
       assert state.tasks["task-1"].claimed_by == "new-worker"


   def test_handle_task_complete_marks_completed(daemon_with_state, socket_path):
       """task_complete marks task as COMPLETED."""
       from harness.state import Task, TaskStatus

       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="First",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   claimed_by="worker-123",
               ),
           }
       )

       response = send_command(socket_path, {
           "command": "task_complete",
           "task_id": "task-1",
           "worker_id": "worker-123",
       })

       assert response["status"] == "ok"
       state = daemon_with_state.state_manager.load()
       assert state.tasks["task-1"].status == TaskStatus.COMPLETED


   def test_handle_task_complete_validates_ownership(daemon_with_state, socket_path):
       """task_complete validates worker owns the task (Council: Lost Ack fix)."""
       from harness.state import Task, TaskStatus

       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="First",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   claimed_by="worker-123",
               ),
           }
       )

       # Different worker tries to complete
       response = send_command(socket_path, {
           "command": "task_complete",
           "task_id": "task-1",
           "worker_id": "worker-other",
       })

       assert response["status"] == "error"
       assert "ownership" in response["message"].lower() or "claimed" in response["message"].lower()


   def test_task_claim_logs_trajectory_after_state_update(daemon_with_state, socket_path, worktree):
       """task_claim releases state lock before logging (Council: Lock Convoy fix)."""
       from harness.state import Task, TaskStatus

       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First", status=TaskStatus.PENDING, dependencies=[]),
           }
       )

       send_command(socket_path, {"command": "task_claim", "worker_id": "worker-test"})

       # Verify trajectory was written
       trajectory_file = worktree / ".claude" / "trajectory.jsonl"
       assert trajectory_file.exists()

       import json
       lines = trajectory_file.read_text().strip().split("\n")
       last_event = json.loads(lines[-1])
       assert last_event["event"] == "claim"
       assert last_event["task_id"] == "task-1"


   def test_exec_decodes_signal_on_negative_returncode(daemon_with_state, socket_path, worktree):
       """exec handler decodes negative return codes to signal names (Council: Missing Signal fix)."""
       # Run a command that will be killed by signal (simulate with shell)
       # Note: This test uses a mock to verify signal decoding logic
       response = send_command(socket_path, {
           "command": "exec",
           "args": ["sh", "-c", "kill -9 $$"],  # Process kills itself with SIGKILL
           "cwd": str(worktree),
       })

       # Verify signal_name is in response
       assert response["status"] == "ok"
       data = response["data"]
       # Return code should be -9 (SIGKILL)
       assert data["returncode"] == -9 or data.get("signal_name") == "SIGKILL"

       # Verify trajectory log includes signal_name
       trajectory_file = worktree / ".claude" / "trajectory.jsonl"
       import json
       lines = trajectory_file.read_text().strip().split("\n")
       exec_events = [json.loads(l) for l in lines if json.loads(l).get("event") == "exec"]
       assert len(exec_events) > 0
       last_exec = exec_events[-1]
       if last_exec["returncode"] < 0:
           assert "signal_name" in last_exec
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "task_claim or task_complete or exec_decodes"
   ```
   Expected: FAIL (handlers not defined)

3. **Implement MINIMAL code:**

   Update `src/harness/daemon.py`:
   ```python
   # Add imports at top
   import time
   from datetime import datetime, timezone
   from .state import StateManager, TaskStatus
   from .trajectory import TrajectoryLogger
   from .runtime import create_runtime, Runtime, decode_signal

   # Update HarnessDaemon.__init__
   def __init__(self, socket_path: str, worktree_root: str):
       self.socket_path = socket_path
       self.worktree_root = Path(worktree_root)
       self.state_manager = StateManager(self.worktree_root)
       self.trajectory_logger = TrajectoryLogger(self.worktree_root)
       self.runtime = create_runtime()
       self._lock_fd = None
       # ... rest of __init__ unchanged ...

   # Add to handlers dict in dispatch()
   handlers = {
       "get_state": self._handle_get_state,
       "update_state": self._handle_update_state,
       "git": self._handle_git,
       "ping": self._handle_ping,
       "shutdown": self._handle_shutdown,
       "task_claim": self._handle_task_claim,
       "task_complete": self._handle_task_complete,
       "exec": self._handle_exec,
   }

   # Add handler methods
   def _handle_task_claim(self, request: dict, server: "HarnessDaemon") -> dict:
       """Claim next available task from the DAG.

       Council Fixes:
       - Check-Then-Act Race: Uses atomic state_manager.claim_task()
       - Lock Convoy: State lock released before logging to trajectory
       """
       worker_id = request.get("worker_id")
       if not worker_id:
           return {"status": "error", "message": "worker_id required"}

       # Council Fix (Check-Then-Act Race): Use atomic claim_task method
       # This performs find, update, save in ONE critical section
       task = server.state_manager.claim_task(worker_id)

       if not task:
           return {"status": "ok", "data": None}

       # Council Fix (Lock Convoy): State lock already released, now log trajectory
       server.trajectory_logger.log({
           "event": "claim",
           "task_id": task.id,
           "worker_id": worker_id,
           "timestamp": time.time(),
       })

       return {"status": "ok", "data": {
           "task_id": task.id,
           "description": task.description,
       }}

   def _handle_task_complete(self, request: dict, server: "HarnessDaemon") -> dict:
       """Mark a task as completed.

       Council Fix (Check-Then-Act Race): Uses atomic state_manager.complete_task().
       """
       task_id = request.get("task_id")
       worker_id = request.get("worker_id")

       if not task_id:
           return {"status": "error", "message": "task_id required"}
       if not worker_id:
           return {"status": "error", "message": "worker_id required"}

       # Council Fix (Check-Then-Act Race): Use atomic complete_task method
       # This handles find, ownership validation, and update in one critical section
       task = server.state_manager.complete_task(task_id, worker_id)

       if not task:
           return {"status": "error", "message": f"Task {task_id} not found or not owned by {worker_id}"}

       # Council Fix (Lock Convoy): Log AFTER state lock is released
       server.trajectory_logger.log({
           "event": "complete",
           "task_id": task_id,
           "worker_id": worker_id,
           "timestamp": time.time(),
       })

       return {"status": "ok", "data": {"task_id": task_id}}

   def _handle_exec(self, request: dict, server: "HarnessDaemon") -> dict:
       """Execute command through safe runtime.

       Council Fix (Missing Signal): Includes signal_name in log and response
       when command is killed by signal (negative return code).
       """
       args = request.get("args", [])
       cwd = request.get("cwd")
       env = request.get("env")
       timeout = request.get("timeout", 60)

       if not args:
           return {"status": "error", "message": "args required"}
       if not cwd:
           return {"status": "error", "message": "cwd required"}

       try:
           result = server.runtime.execute(
               args=args,
               cwd=cwd,
               timeout=timeout,
               env=env,
           )

           # Council Fix (Missing Signal): Decode negative return codes
           signal_name = decode_signal(result.returncode)

           log_entry = {
               "event": "exec",
               "args": args,
               "cwd": cwd,
               "returncode": result.returncode,
               "timestamp": time.time(),
           }
           # Add signal_name if process was killed by signal
           if signal_name:
               log_entry["signal_name"] = signal_name

           server.trajectory_logger.log(log_entry)

           response_data = {
               "returncode": result.returncode,
               "stdout": result.stdout,
               "stderr": result.stderr,
           }
           # Include signal info for LLM agent understanding
           if signal_name:
               response_data["signal_name"] = signal_name

           return {"status": "ok", "data": response_data}
       except subprocess.TimeoutExpired:
           return {"status": "error", "message": f"Command timed out after {timeout}s"}
       except Exception as e:
           return {"status": "error", "message": str(e)}
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(daemon): add task_claim/complete handlers with worker_id idempotency and lock convoy fix"
   ```

---

### Task 5: Client with Worker ID (client.py)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/client.py`
- Modify: `tests/harness/test_client.py`

**Council Fixes Applied:**
- **Lost Ack:** Generate stable `WORKER_ID` on module load, pass with every request

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_client.py (add to existing file)

   import json
   import os
   import subprocess
   import pytest


   class TestWorkerID:
       """Tests for worker ID generation (Council: Lost Ack fix)."""

       def test_worker_id_generated_on_import(self):
           """Client generates WORKER_ID constant on module load."""
           from harness import client

           assert hasattr(client, "WORKER_ID")
           assert client.WORKER_ID is not None
           assert len(client.WORKER_ID) > 0

       def test_worker_id_is_stable(self):
           """WORKER_ID is stable within same process."""
           from harness import client

           id1 = client.WORKER_ID
           id2 = client.WORKER_ID
           assert id1 == id2


   class TestTaskCommands:
       """Tests for task claim/complete commands."""

       def test_task_claim_returns_json(self, harness_cli, daemon_process):
           """harness task claim returns claimable task JSON."""
           result = subprocess.run(
               [harness_cli, "task", "claim"],
               capture_output=True,
               text=True,
           )

           assert result.returncode == 0
           data = json.loads(result.stdout)
           assert data is None or "task_id" in data

       def test_task_complete_requires_id(self, harness_cli, daemon_process):
           """harness task complete requires --id flag."""
           result = subprocess.run(
               [harness_cli, "task", "complete"],
               capture_output=True,
               text=True,
           )

           assert result.returncode != 0


   class TestExecCommand:
       """Tests for harness exec command."""

       def test_exec_runs_command(self, harness_cli, daemon_process, tmp_path):
           """harness exec -- echo hello runs command through runtime."""
           result = subprocess.run(
               [harness_cli, "exec", "--cwd", str(tmp_path), "--", "echo", "hello"],
               capture_output=True,
               text=True,
           )

           assert result.returncode == 0
           output = json.loads(result.stdout)
           assert output["returncode"] == 0
           assert "hello" in output["stdout"]

       def test_exec_with_env(self, harness_cli, daemon_process, tmp_path):
           """harness exec -e VAR=value passes env vars."""
           result = subprocess.run(
               [harness_cli, "exec", "--cwd", str(tmp_path),
                "-e", "MY_VAR=secret",
                "--", "sh", "-c", "echo $MY_VAR"],
               capture_output=True,
               text=True,
           )

           assert result.returncode == 0
           output = json.loads(result.stdout)
           assert "secret" in output["stdout"]
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_client.py -v -k "worker_id or task or exec"
   ```
   Expected: FAIL (WORKER_ID not defined, commands not implemented)

3. **Implement MINIMAL code:**

   Update `src/harness/client.py`:
   ```python
   # Add near top of file, after imports
   import uuid

   # Council Fix (Lost Ack): Generate stable worker ID on module load
   # This survives reconnects/retries within the same process
   WORKER_ID = f"worker-{uuid.uuid4().hex[:12]}"

   # Add to argument parser (in main())
   # Task commands
   task_parser = subparsers.add_parser("task", help="Task management commands")
   task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)

   task_claim = task_subparsers.add_parser("claim", help="Claim next available task")

   task_complete = task_subparsers.add_parser("complete", help="Mark task as completed")
   task_complete.add_argument("--id", required=True, help="Task ID to complete")

   # Exec command
   exec_parser = subparsers.add_parser(
       "exec",
       help="Execute command through safe runtime"
   )
   exec_parser.add_argument("--cwd", required=True, help="Working directory")
   exec_parser.add_argument(
       "-e", "--env",
       action="append",
       dest="env_vars",
       help="Environment variable (VAR=value), can be repeated"
   )
   exec_parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")
   exec_parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute")

   # Add command dispatch in main()
   elif args.command == "task":
       if args.task_command == "claim":
           _cmd_task_claim(socket_path, worktree_root)
       elif args.task_command == "complete":
           _cmd_task_complete(socket_path, worktree_root, args.id)
   elif args.command == "exec":
       exec_args = args.command
       if exec_args and exec_args[0] == "--":
           exec_args = exec_args[1:]
       _cmd_exec(socket_path, worktree_root, args.cwd, exec_args, args.env_vars, args.timeout)

   # Add command handlers
   def _cmd_task_claim(socket_path: str, worktree_root: str):
       """Claim next available task from daemon."""
       response = send_rpc(
           socket_path,
           {"command": "task_claim", "worker_id": WORKER_ID},
           worktree_root
       )

       if response.get("status") == "error":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)

       data = response.get("data")
       print(json.dumps(data))


   def _cmd_task_complete(socket_path: str, worktree_root: str, task_id: str):
       """Mark task as completed."""
       response = send_rpc(
           socket_path,
           {"command": "task_complete", "task_id": task_id, "worker_id": WORKER_ID},
           worktree_root,
       )

       if response.get("status") == "error":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)

       print(json.dumps(response.get("data")))


   def _cmd_exec(socket_path: str, worktree_root: str, cwd: str, command: list, env_vars: list | None, timeout: int):
       """Execute command through safe runtime."""
       if not command:
           print("Error: No command specified", file=sys.stderr)
           sys.exit(1)

       # Parse env vars
       env = {}
       for env_var in (env_vars or []):
           if "=" in env_var:
               key, value = env_var.split("=", 1)
               env[key] = value

       response = send_rpc(
           socket_path,
           {
               "command": "exec",
               "args": command,
               "cwd": cwd,
               "env": env if env else None,
               "timeout": timeout,
           },
           worktree_root,
       )

       if response.get("status") == "error":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)

       data = response.get("data")
       print(json.dumps(data))
       sys.exit(data.get("returncode", 0))
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_client.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): add WORKER_ID constant, task claim/complete, and exec commands"
   ```

---

### Task 6: Git Module Delegation (git.py)

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/git.py`
- Modify: `tests/harness/test_git.py`

**Goal:** Delegate git.py execution to runtime.py with `exclusive=True`. Remove GLOBAL_GIT_LOCK.

**Council Fix Applied:** Global Lock Performance Suicide - Git operations MUST use `exclusive=True` because they modify shared .git directory. Non-git commands run lock-free.

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_git.py (replace existing)

   import subprocess
   import pytest


   def test_safe_git_exec_uses_runtime(tmp_path):
       """safe_git_exec delegates to LocalRuntime."""
       from harness.git import safe_git_exec

       # Create a git repo
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)

       result = safe_git_exec(["status"], cwd=str(tmp_path))

       assert result.returncode == 0
       assert "On branch" in result.stdout or "No commits yet" in result.stdout


   def test_safe_commit_uses_runtime(tmp_path):
       """safe_commit delegates to LocalRuntime."""
       from harness.git import safe_commit

       # Create a git repo with a file
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
       subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path)
       subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path)
       (tmp_path / "test.txt").write_text("hello")
       subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)

       result = safe_commit("test commit", cwd=str(tmp_path))

       assert result.returncode == 0


   def test_global_git_lock_removed():
       """GLOBAL_GIT_LOCK should be removed (use GLOBAL_EXEC_LOCK from runtime)."""
       import harness.git as git_module

       assert not hasattr(git_module, "GLOBAL_GIT_LOCK")


   def test_uses_global_exec_lock():
       """git.py should use GLOBAL_EXEC_LOCK from runtime.py."""
       from harness.runtime import GLOBAL_EXEC_LOCK
       import threading

       assert isinstance(GLOBAL_EXEC_LOCK, type(threading.Lock()))


   def test_git_uses_exclusive_locking(tmp_path, monkeypatch):
       """Council Fix (Global Lock): Git operations must use exclusive=True."""
       from harness.git import safe_git_exec
       from harness import runtime

       # Track calls to execute
       calls = []
       original_execute = runtime.LocalRuntime.execute

       def tracking_execute(self, args, cwd, timeout=60, env=None, exclusive=False):
           calls.append({"args": args, "exclusive": exclusive})
           return original_execute(self, args, cwd, timeout, env, exclusive)

       monkeypatch.setattr(runtime.LocalRuntime, "execute", tracking_execute)

       # Create git repo and run git command
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
       safe_git_exec(["status"], cwd=str(tmp_path))

       # Verify exclusive=True was passed
       assert len(calls) == 1
       assert calls[0]["exclusive"] is True, "Git commands MUST use exclusive=True"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_git.py -v
   ```
   Expected: FAIL (GLOBAL_GIT_LOCK still exists)

3. **Implement MINIMAL code:**

   Replace `src/harness/git.py`:
   ```python
   # src/harness/git.py
   """Git operations using safe runtime execution.

   All execution delegated to runtime.py for unified sandboxing.
   GLOBAL_GIT_LOCK removed - runtime.py provides GLOBAL_EXEC_LOCK.
   """

   from __future__ import annotations

   import subprocess
   from .runtime import LocalRuntime


   # Use singleton LocalRuntime for git operations
   _runtime = LocalRuntime()


   def safe_git_exec(
       args: list[str],
       cwd: str,
       timeout: int = 60,
   ) -> subprocess.CompletedProcess:
       """Execute git command through safe runtime.

       Council Fix (Global Lock): Uses exclusive=True because git commands
       modify the shared .git directory and must be serialized.

       Args:
           args: Git command arguments (without 'git' prefix)
           cwd: Working directory

       Returns:
           CompletedProcess with stdout, stderr, returncode
       """
       # Council Fix: exclusive=True for git (modifies .git directory)
       return _runtime.execute(["git"] + args, cwd=cwd, timeout=timeout, exclusive=True)


   def safe_commit(message: str, cwd: str) -> subprocess.CompletedProcess:
       """Atomic add + commit operation.

       Council Fix (Global Lock): Uses exclusive=True because these are git
       commands that modify the shared .git directory.
       """
       # Stage all changes (exclusive=True)
       add_result = _runtime.execute(["git", "add", "-A"], cwd=cwd, exclusive=True)
       if add_result.returncode != 0:
           return add_result

       # Commit (exclusive=True)
       return _runtime.execute(["git", "commit", "-m", message], cwd=cwd, exclusive=True)


   def get_head_sha(cwd: str) -> str | None:
       """Get current HEAD commit SHA."""
       result = safe_git_exec(["rev-parse", "HEAD"], cwd=cwd)
       if result.returncode == 0:
           return result.stdout.strip()
       return None
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_git.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "refactor(git): delegate to runtime.py, remove GLOBAL_GIT_LOCK"
   ```

---

### Task 7: Integration Tests

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_integration.py (add to existing file)

   import json
   import subprocess
   import time
   import pytest


   @pytest.fixture
   def workflow_with_tasks(daemon_process, harness_cli, tmp_worktree):
       """Setup workflow state with DAG tasks."""
       from harness.state import StateManager, WorkflowState, Task, TaskStatus

       manager = StateManager(tmp_worktree)
       manager.save(
           WorkflowState(
               workflow="execute-plan",
               plan="test.md",
               worktree=str(tmp_worktree),
               base_sha="abc123",
               tasks={
                   "task-1": Task(id="task-1", description="First task", status=TaskStatus.PENDING, dependencies=[]),
                   "task-2": Task(id="task-2", description="Second task", status=TaskStatus.PENDING, dependencies=["task-1"]),
                   "task-3": Task(id="task-3", description="Third task", status=TaskStatus.PENDING, dependencies=["task-1"]),
               },
           )
       )
       return tmp_worktree


   def test_full_task_workflow(harness_cli, workflow_with_tasks):
       """End-to-end: claim, complete, verify DAG progression."""
       # Claim first task
       claim1 = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           cwd=workflow_with_tasks,
       )
       assert claim1.returncode == 0
       data1 = json.loads(claim1.stdout)
       assert data1["task_id"] == "task-1"

       # Complete first task
       complete1 = subprocess.run(
           [harness_cli, "task", "complete", "--id", "task-1"],
           capture_output=True,
           text=True,
           cwd=workflow_with_tasks,
       )
       assert complete1.returncode == 0

       # Now task-2 and task-3 should be claimable (both depend on task-1)
       claim2 = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           cwd=workflow_with_tasks,
       )
       assert claim2.returncode == 0
       data2 = json.loads(claim2.stdout)
       assert data2["task_id"] in ["task-2", "task-3"]


   def test_dag_dependency_enforcement(harness_cli, workflow_with_tasks):
       """DAG enforces task dependencies: can't claim blocked task."""
       # Claim task-1
       claim1 = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           cwd=workflow_with_tasks,
       )
       data1 = json.loads(claim1.stdout)
       assert data1["task_id"] == "task-1"

       # Second claim should return None (task-2/3 blocked, task-1 already claimed)
       claim2 = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           cwd=workflow_with_tasks,
       )
       # Should return the same task (idempotency) or None
       data2 = json.loads(claim2.stdout)
       # With same worker, should get same task back (idempotency)
       assert data2 is None or data2["task_id"] == "task-1"


   def test_trajectory_logging(harness_cli, workflow_with_tasks):
       """Trajectory log captures claim and complete events."""
       # Claim a task
       subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           cwd=workflow_with_tasks,
       )

       # Check trajectory file
       trajectory_file = workflow_with_tasks / ".claude" / "trajectory.jsonl"
       assert trajectory_file.exists()

       lines = trajectory_file.read_text().strip().split("\n")
       events = [json.loads(line) for line in lines if line]

       # Should have at least a claim event
       assert any(e["event"] == "claim" for e in events)


   def test_json_state_persistence(harness_cli, workflow_with_tasks):
       """State is persisted as JSON (Council: Markdown Database fix)."""
       state_file = workflow_with_tasks / ".claude" / "dev-workflow-state.json"
       assert state_file.exists()

       content = state_file.read_text()
       data = json.loads(content)
       assert "tasks" in data
       assert "task-1" in data["tasks"]
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_integration.py -v -k "task_workflow or dag or trajectory or json_state"
   ```
   Expected: FAIL (fixtures need updates for new schema)

3. **Implement MINIMAL code:**

   Update fixtures as needed to support the new JSON state format and task schema.

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_integration.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add end-to-end tests for task workflow with DAG"
   ```

---

### Task 8: Code Review

**Effort:** simple (3-10 tool calls)

**Files:**
- All modified files from Tasks 1-7

**Instructions:**

1. Review all changes since base commit
2. Check for:
   - **Council Fixes Applied:**
     - JSON state (no frontmatter methods)
     - Lock convoy (state and trajectory have separate locks)
     - Worker ID idempotency (claimed_by field, WORKER_ID constant)
     - UID mapping (DockerRuntime --user flag)
   - Thread safety (locks used correctly)
   - Error handling (graceful failures)
   - Code style (consistent with existing patterns)
   - Test coverage (all new code tested)
3. Final verification:
   ```bash
   uv run pytest tests/harness/ -v
   uv run ruff check src/harness/
   ```

---

## Summary

| Task | Effort | Files | Commit Message | Council Fix |
|------|--------|-------|----------------|-------------|
| 1. Runtime Abstraction | standard | runtime.py (NEW), test_runtime.py | `feat(runtime): add runtime.py with UID mapping and signal decoding` | Root Escape, Blind Execution, Missing Signal |
| 2. Trajectory Logger | standard | trajectory.py (NEW), test_trajectory.py | `feat(trajectory): add trajectory.py with separate lock` | Lock Convoy (prep) |
| 3. JSON State Schema | complex | state.py, test_state.py | `feat(state): migrate to JSON, add worker_id tracking` | Markdown Database, Lost Ack, Zombie |
| 4. Daemon Handlers | standard | daemon.py, test_daemon.py | `feat(daemon): add handlers with lock convoy fix and signal logging` | Lock Convoy, Lost Ack, Missing Signal |
| 5. Client Commands | standard | client.py, test_client.py | `feat(client): add WORKER_ID and task commands` | Lost Ack |
| 6. Git Delegation | simple | git.py, test_git.py | `refactor(git): delegate to runtime.py` | - |
| 7. Integration Tests | standard | test_integration.py | `test(integration): add DAG workflow tests` | - |
| 8. Code Review | simple | all | (no commit, review only) | - |

**Total estimated tool calls:** 80-110

---

## Council Fixes Verification Checklist

Before marking the plan as complete, verify:

- [ ] **JSON State:** `StateManager` uses `.json` file, no `_parse_frontmatter` or `_to_frontmatter` methods
- [ ] **Lock Convoy:** State update and trajectory write are in separate critical sections (state lock released before trajectory.log())
- [ ] **Lost Ack:** `task_claim` requires `worker_id`, returns existing task for same worker, `Task.claimed_by` tracks ownership
- [ ] **Root Escape:** `DockerRuntime` passes `--user UID:GID` by default, configurable via `map_uid` parameter
- [ ] **Missing Signal:** `decode_signal()` in runtime.py, `_handle_exec` includes `signal_name` in log and response for negative return codes
- [ ] **Global Lock Suicide:** `LocalRuntime.execute()` defaults to `exclusive=False`; git.py passes `exclusive=True` for all git operations
- [ ] **Check-Then-Act Race:** `StateManager.claim_task()` and `complete_task()` are atomic methods that perform find-validate-update in one critical section; daemon handlers use these instead of load-then-update pattern

---

## Key Differences from Original Plan

| Aspect | Original Plan | Council Revision |
|--------|---------------|------------------|
| State Format | Markdown frontmatter | JSON (`model_dump_json()`) |
| State File | `dev-workflow-state.local.md` | `dev-workflow-state.json` |
| Locking | Single state lock for everything | Separate locks for state and trajectory |
| Task Claim | No worker tracking | `worker_id` required, idempotent returns |
| Docker | No UID mapping | `--user $(id -u):$(id -g)` by default |
| Trajectory | Same lock as state | Separate lock, written after state update |
| Signal Handling | Raw negative return codes | Decoded to signal names (SIGKILL, SIGSEGV) |
| Execute Lock | Always lock on execute() | `exclusive=False` by default, `True` only for git |
| State Operations | Load-then-update pattern | Atomic `claim_task()` / `complete_task()` methods |
