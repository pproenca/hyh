# Harness v2.0: The Pull Engine Implementation Plan

**Goal:** Transform the Harness from a "Task Counter" to an Autonomous Research Kernel with runtime abstraction, trajectory logging, DAG-based state, and a pull protocol.

**Architecture:** Minimal changes approach - extend existing patterns, colocate related code. LocalRuntime/DockerRuntime in git.py, TrajectoryLogger in state.py, Task/DAG fields in WorkflowState. Clean break from v1.0 schema (no backward compatibility). 5 modified files, 0 new files.

---

## Architectural Fixes (Council Review)

This plan addresses four critical issues identified during architecture review:

| Issue | Problem | Fix |
|-------|---------|-----|
| **I/O Suicide** | `tail()` reads entire file into RAM | Efficient reverse-seek implementation |
| **Path Mismatch** | DockerRuntime passes host paths to container | `mount_map` for host→container translation |
| **Zombie Deadlock** | Crashed workers leave tasks RUNNING forever | Task timeout + reclaim in `get_claimable_task` |
| **Secret-Less Agent** | No env var passing to Runtime | Add `env` parameter to `execute()` |

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent modules (runtime + trajectory), no file overlap |
| Group 2 | 3 | State schema depends on Task model design from Group 1 |
| Group 3 | 4, 5 | Daemon handlers + client commands, sequential (daemon before client) |
| Group 4 | 6 | Integration tests span all modules |

---

### Task 1: Runtime Abstraction (git.py)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/git.py`
- Test: `tests/harness/test_git.py`

**Architectural Fixes Applied:**
- **Secret-Less Agent:** Add `env` parameter to `execute()` for API keys
- **Path Mismatch:** Add `mount_map` to DockerRuntime for host→container path translation

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_git.py
   import os
   import subprocess
   import pytest


   def test_local_runtime_execute_success(tmp_path):
       """LocalRuntime executes command and returns CompletedProcess."""
       from harness.git import LocalRuntime

       runtime = LocalRuntime()
       result = runtime.execute(["echo", "hello"], cwd=str(tmp_path))

       assert result.returncode == 0
       assert result.stdout.strip() == "hello"


   def test_local_runtime_execute_failure(tmp_path):
       """LocalRuntime returns non-zero for failed commands."""
       from harness.git import LocalRuntime

       runtime = LocalRuntime()
       result = runtime.execute(["false"], cwd=str(tmp_path))

       assert result.returncode != 0


   def test_local_runtime_timeout(tmp_path):
       """LocalRuntime raises TimeoutExpired for slow commands."""
       from harness.git import LocalRuntime

       runtime = LocalRuntime()

       with pytest.raises(subprocess.TimeoutExpired):
           runtime.execute(["sleep", "10"], cwd=str(tmp_path), timeout=0.1)


   def test_local_runtime_env_vars(tmp_path):
       """LocalRuntime passes env vars to subprocess."""
       from harness.git import LocalRuntime

       runtime = LocalRuntime()
       result = runtime.execute(
           ["sh", "-c", "echo $TEST_API_KEY"],
           cwd=str(tmp_path),
           env={"TEST_API_KEY": "secret123"},
       )

       assert result.returncode == 0
       assert "secret123" in result.stdout


   def test_local_runtime_env_merges_with_os_environ(tmp_path):
       """LocalRuntime merges custom env with os.environ (PATH needed)."""
       from harness.git import LocalRuntime

       runtime = LocalRuntime()
       # Without PATH merge, 'echo' wouldn't be found
       result = runtime.execute(
           ["echo", "works"],
           cwd=str(tmp_path),
           env={"CUSTOM_VAR": "value"},
       )

       assert result.returncode == 0
       assert "works" in result.stdout


   def test_docker_runtime_execute(tmp_path, mocker):
       """DockerRuntime calls docker exec with correct args."""
       from harness.git import DockerRuntime

       mock_run = mocker.patch("harness.git.subprocess.run")
       mock_run.return_value = subprocess.CompletedProcess(
           args=[], returncode=0, stdout="output", stderr=""
       )

       runtime = DockerRuntime(container="test-container")
       result = runtime.execute(["pytest", "tests/"], cwd="/app")

       mock_run.assert_called_once()
       call_args = mock_run.call_args[0][0]
       assert "docker" in call_args
       assert "exec" in call_args
       assert "-w" in call_args
       assert "test-container" in call_args


   def test_docker_runtime_path_mapping(mocker):
       """DockerRuntime translates host paths to container paths via mount_map."""
       from harness.git import DockerRuntime

       mock_run = mocker.patch("harness.git.subprocess.run")
       mock_run.return_value = subprocess.CompletedProcess(
           args=[], returncode=0, stdout="", stderr=""
       )

       runtime = DockerRuntime(
           container="test-container",
           mount_map={"/Users/dev/project": "/app"},
       )
       runtime.execute(["ls"], cwd="/Users/dev/project/src")

       call_args = mock_run.call_args[0][0]
       # Should translate /Users/dev/project/src -> /app/src
       assert "/app/src" in call_args
       assert "/Users/dev/project" not in " ".join(call_args)


   def test_docker_runtime_env_vars(mocker):
       """DockerRuntime passes env vars via -e flags."""
       from harness.git import DockerRuntime

       mock_run = mocker.patch("harness.git.subprocess.run")
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
       # Should have -e API_KEY=secret -e DEBUG=1
       assert "-e" in call_args
       env_args = []
       for i, arg in enumerate(call_args):
           if arg == "-e" and i + 1 < len(call_args):
               env_args.append(call_args[i + 1])
       assert "API_KEY=secret" in env_args
       assert "DEBUG=1" in env_args


   def test_runtime_protocol():
       """Both runtimes implement same interface."""
       from harness.git import LocalRuntime, DockerRuntime

       for runtime_cls in [LocalRuntime, DockerRuntime]:
           assert hasattr(runtime_cls, "execute")
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_git.py -v -k "runtime"
   ```
   Expected: FAIL (LocalRuntime, DockerRuntime not defined)

3. **Implement MINIMAL code:**

   In `src/harness/git.py`, add after GLOBAL_GIT_LOCK:
   ```python
   import os
   from typing import Protocol


   class Runtime(Protocol):
       """Protocol for command execution runtimes."""

       def execute(
           self,
           args: list[str],
           cwd: str,
           timeout: int = 60,
           env: dict[str, str] | None = None,
       ) -> subprocess.CompletedProcess:
           """Execute command and return result."""
           ...


   class LocalRuntime:
       """Execute commands locally with global lock."""

       def execute(
           self,
           args: list[str],
           cwd: str,
           timeout: int = 60,
           env: dict[str, str] | None = None,
       ) -> subprocess.CompletedProcess:
           # Merge custom env with os.environ (need PATH, etc.)
           merged_env = {**os.environ, **(env or {})}

           with GLOBAL_GIT_LOCK:
               return subprocess.run(
                   args,
                   cwd=cwd,
                   capture_output=True,
                   text=True,
                   timeout=timeout,
                   env=merged_env,
               )


   class DockerRuntime:
       """Execute commands in existing Docker container."""

       def __init__(
           self,
           container: str,
           mount_map: dict[str, str] | None = None,
       ):
           self.container = container
           # mount_map: {"/host/path": "/container/path"}
           self.mount_map = mount_map or {}

       def _translate_path(self, host_path: str) -> str:
           """Translate host path to container path using mount_map."""
           for host_prefix, container_prefix in self.mount_map.items():
               if host_path.startswith(host_prefix):
                   return host_path.replace(host_prefix, container_prefix, 1)
           return host_path

       def execute(
           self,
           args: list[str],
           cwd: str,
           timeout: int = 60,
           env: dict[str, str] | None = None,
       ) -> subprocess.CompletedProcess:
           container_cwd = self._translate_path(cwd)

           # Build docker exec command
           cmd = ["docker", "exec"]

           # Add env vars via -e flags
           for key, value in (env or {}).items():
               cmd.extend(["-e", f"{key}={value}"])

           cmd.extend(["-w", container_cwd, self.container])
           cmd.extend(args)

           with GLOBAL_GIT_LOCK:
               return subprocess.run(
                   cmd,
                   capture_output=True,
                   text=True,
                   timeout=timeout,
               )
   ```

   Update `safe_git_exec` to use LocalRuntime internally:
   ```python
   _local_runtime = LocalRuntime()

   def safe_git_exec(args: list[str], cwd: str) -> subprocess.CompletedProcess:
       """Execute git command with global lock (legacy wrapper)."""
       return _local_runtime.execute(["git"] + args, cwd)
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_git.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(runtime): add LocalRuntime and DockerRuntime with env and path mapping"
   ```

---

### Task 2: Trajectory Logger (state.py)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/state.py`
- Test: `tests/harness/test_state.py`

**Architectural Fixes Applied:**
- **I/O Suicide:** Implement efficient reverse-seek `tail()` that reads from end of file, NOT entire file into RAM

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_state.py (add to existing file)

   import json
   import time
   import threading
   from concurrent.futures import ThreadPoolExecutor


   def test_trajectory_logger_creates_file(tmp_path):
       """TrajectoryLogger creates .claude/trajectory.jsonl on first log."""
       from harness.state import TrajectoryLogger

       logger = TrajectoryLogger(tmp_path)
       logger.log({"event": "test", "data": 123})

       log_file = tmp_path / ".claude" / "trajectory.jsonl"
       assert log_file.exists()


   def test_trajectory_logger_appends_jsonl(tmp_path):
       """TrajectoryLogger appends JSON lines, not replaces."""
       from harness.state import TrajectoryLogger

       logger = TrajectoryLogger(tmp_path)
       logger.log({"event": "first"})
       logger.log({"event": "second"})

       log_file = tmp_path / ".claude" / "trajectory.jsonl"
       lines = log_file.read_text().strip().split("\n")

       assert len(lines) == 2
       assert json.loads(lines[0])["event"] == "first"
       assert json.loads(lines[1])["event"] == "second"


   def test_trajectory_logger_thread_safe(tmp_path):
       """TrajectoryLogger handles concurrent writes without corruption."""
       from harness.state import TrajectoryLogger

       logger = TrajectoryLogger(tmp_path)

       def write_event(i):
           logger.log({"event": "concurrent", "id": i})

       with ThreadPoolExecutor(max_workers=10) as executor:
           list(executor.map(write_event, range(100)))

       log_file = tmp_path / ".claude" / "trajectory.jsonl"
       lines = log_file.read_text().strip().split("\n")

       assert len(lines) == 100
       # Each line should be valid JSON
       for line in lines:
           parsed = json.loads(line)
           assert parsed["event"] == "concurrent"


   def test_trajectory_logger_tail(tmp_path):
       """TrajectoryLogger.tail(n) returns last n events."""
       from harness.state import TrajectoryLogger

       logger = TrajectoryLogger(tmp_path)
       for i in range(10):
           logger.log({"event": "test", "id": i})

       tail = logger.tail(3)

       assert len(tail) == 3
       assert tail[0]["id"] == 7
       assert tail[1]["id"] == 8
       assert tail[2]["id"] == 9


   def test_trajectory_logger_tail_empty_file(tmp_path):
       """TrajectoryLogger.tail() handles empty/missing file."""
       from harness.state import TrajectoryLogger

       logger = TrajectoryLogger(tmp_path)

       # File doesn't exist yet
       assert logger.tail(5) == []


   def test_trajectory_logger_tail_fewer_than_n(tmp_path):
       """TrajectoryLogger.tail(n) returns all if fewer than n entries."""
       from harness.state import TrajectoryLogger

       logger = TrajectoryLogger(tmp_path)
       logger.log({"event": "one"})
       logger.log({"event": "two"})

       tail = logger.tail(10)

       assert len(tail) == 2
       assert tail[0]["event"] == "one"
       assert tail[1]["event"] == "two"


   def test_trajectory_tail_large_file_performance(tmp_path):
       """tail(n) on large file completes fast without reading entire file."""
       from harness.state import TrajectoryLogger

       logger = TrajectoryLogger(tmp_path)

       # Create ~1MB log file (10000 entries, ~100 bytes each)
       for i in range(10000):
           logger.log({"event": "bulk", "id": i, "padding": "x" * 50})

       # Verify file is substantial
       log_file = tmp_path / ".claude" / "trajectory.jsonl"
       file_size = log_file.stat().st_size
       assert file_size > 500_000  # At least 500KB

       # tail(5) should complete in < 50ms (not read whole file)
       start = time.perf_counter()
       tail = logger.tail(5)
       elapsed = time.perf_counter() - start

       assert len(tail) == 5
       assert tail[-1]["id"] == 9999
       # Must complete quickly - reading whole file would take much longer
       assert elapsed < 0.05, f"tail() took {elapsed:.3f}s, should be < 50ms"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_state.py -v -k "trajectory"
   ```
   Expected: FAIL (TrajectoryLogger not defined)

3. **Implement MINIMAL code:**

   In `src/harness/state.py`, add after imports:
   ```python
   class TrajectoryLogger:
       """JSONL append-only log at .claude/trajectory.jsonl.

       Uses efficient reverse-seek for tail() to avoid O(N) memory on large files.
       """

       # Block size for reverse reading (4KB is good for SSD)
       _BLOCK_SIZE = 4096

       def __init__(self, worktree_root: Path):
           self.log_file = worktree_root / ".claude" / "trajectory.jsonl"
           self._lock = threading.Lock()

       def log(self, event: dict) -> None:
           """Append event to trajectory (thread-safe)."""
           with self._lock:
               self.log_file.parent.mkdir(parents=True, exist_ok=True)
               with self.log_file.open("a") as f:
                   f.write(json.dumps(event) + "\n")

       def tail(self, n: int) -> list[dict]:
           """Return last n events using efficient reverse-seek.

           Reads from end of file in blocks, avoiding O(N) memory usage.
           """
           if not self.log_file.exists():
               return []

           with self._lock:
               return self._tail_reverse_seek(n)

       def _tail_reverse_seek(self, n: int) -> list[dict]:
           """Read last n lines by seeking from end of file."""
           lines = []
           remaining_bytes = b""

           with self.log_file.open("rb") as f:
               # Get file size
               f.seek(0, 2)  # Seek to end
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

                   # Add complete lines (in reverse order, will reverse at end)
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

           # Parse JSON
           return [json.loads(line.decode()) for line in lines if line.strip()]
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_state.py -v
   ```
   Expected: PASS (all tests green, including performance test)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(trajectory): add TrajectoryLogger with efficient reverse-seek tail"
   ```

---

### Task 3: DAG-Based State Schema (state.py)

**Effort:** complex (15-25 tool calls)

**Files:**
- Modify: `src/harness/state.py`
- Test: `tests/harness/test_state.py`

**Architectural Fixes Applied:**
- **Zombie Deadlock:** Add `timeout_seconds` to Task model and detect timed-out RUNNING tasks as reclaimable in `get_claimable_task()`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_state.py (add/update)

   from datetime import datetime, timezone
   from harness.state import Task, TaskStatus, WorkflowState


   def test_task_model_validation():
       """Task model validates required fields."""
       task = Task(
           id="task-1",
           description="Setup environment",
           status=TaskStatus.PENDING,
           dependencies=[],
       )

       assert task.id == "task-1"
       assert task.status == TaskStatus.PENDING


   def test_task_model_has_timeout():
       """Task model has timeout_seconds field with default."""
       task = Task(
           id="task-1",
           description="Test",
           status=TaskStatus.PENDING,
           dependencies=[],
       )

       # Default timeout is 10 minutes (600 seconds)
       assert task.timeout_seconds == 600


   def test_task_model_custom_timeout():
       """Task model accepts custom timeout."""
       task = Task(
           id="task-1",
           description="Long task",
           status=TaskStatus.PENDING,
           dependencies=[],
           timeout_seconds=3600,  # 1 hour
       )

       assert task.timeout_seconds == 3600


   def test_workflow_state_v2_schema():
       """WorkflowState v2 has tasks dict and dag."""
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


   def test_get_claimable_task_no_deps():
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


   def test_get_claimable_task_with_deps():
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
       assert claimable == "task-2"  # task-1 done, task-3 blocked by task-2


   def test_get_claimable_task_multiple_deps():
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
       assert claimable == "task-2"  # task-3 blocked until task-2 done


   def test_get_claimable_task_none_available():
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
                   started_at=now,  # Just started, not timed out
               ),
               "task-2": Task(id="task-2", description="B", status=TaskStatus.PENDING, dependencies=["task-1"]),
           },
       )

       claimable = state.get_claimable_task()
       assert claimable is None


   def test_get_claimable_task_all_completed():
       """get_claimable_task returns None when all tasks done."""
       state = WorkflowState(
           workflow="execute-plan",
           plan="test.md",
           worktree="/tmp",
           base_sha="abc",
           tasks={
               "task-1": Task(id="task-1", description="A", status=TaskStatus.COMPLETED, dependencies=[]),
           },
       )

       claimable = state.get_claimable_task()
       assert claimable is None


   def test_get_claimable_task_reclaims_timed_out():
       """get_claimable_task returns timed-out RUNNING task as claimable."""
       # Task started 2 hours ago with 1 hour timeout - should be reclaimable
       from datetime import timedelta
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
                   timeout_seconds=3600,  # 1 hour timeout
               ),
           },
       )

       claimable = state.get_claimable_task()
       assert claimable == "task-1"  # Timed out, can be reclaimed


   def test_get_claimable_task_running_not_timed_out():
       """get_claimable_task skips RUNNING task that hasn't timed out."""
       # Task started 30 minutes ago with 1 hour timeout - NOT reclaimable
       from datetime import timedelta
       recent_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

       state = WorkflowState(
           workflow="execute-plan",
           plan="test.md",
           worktree="/tmp",
           base_sha="abc",
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Active task",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   started_at=recent_time,
                   timeout_seconds=3600,  # 1 hour timeout
               ),
               "task-2": Task(id="task-2", description="B", status=TaskStatus.PENDING, dependencies=["task-1"]),
           },
       )

       claimable = state.get_claimable_task()
       assert claimable is None  # task-1 still running, task-2 blocked


   def test_task_is_timed_out_method():
       """Task.is_timed_out() correctly identifies expired tasks."""
       from datetime import timedelta

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
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_state.py -v -k "task or claimable"
   ```
   Expected: FAIL (Task, TaskStatus not defined)

3. **Implement MINIMAL code:**

   In `src/harness/state.py`, update imports and add models:
   ```python
   from datetime import datetime, timezone
   from enum import Enum
   from pydantic import BaseModel, Field


   class TaskStatus(str, Enum):
       """Task execution status."""
       PENDING = "pending"
       RUNNING = "running"
       COMPLETED = "completed"
       FAILED = "failed"


   class Task(BaseModel):
       """Individual task in a workflow plan."""
       id: str
       description: str
       status: TaskStatus = TaskStatus.PENDING
       dependencies: list[str] = Field(default_factory=list)
       started_at: str | None = None
       completed_at: str | None = None
       # Default 10 minute timeout for zombie detection
       timeout_seconds: int = 600

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
               # Ensure timezone-aware comparison
               if started.tzinfo is None:
                   started = started.replace(tzinfo=timezone.utc)
               now = datetime.now(timezone.utc)
               elapsed = (now - started).total_seconds()
               return elapsed > self.timeout_seconds
           except (ValueError, TypeError):
               return False
   ```

   Update WorkflowState class (replace old task counter fields):
   ```python
   class WorkflowState(BaseModel):
       """Workflow state with DAG-based task tracking."""
       workflow: Literal["execute-plan", "subagent"]
       plan: str
       worktree: str
       base_sha: str
       last_commit: str | None = None

       # v2.0: DAG-based task tracking
       tasks: dict[str, Task] = Field(default_factory=dict)

       # Keep for compatibility during transition
       enabled: bool = True

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
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_state.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(state): add Task model with timeout and zombie detection in get_claimable_task"
   ```

---

### Task 4: Daemon Claim/Complete Handlers (daemon.py)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/daemon.py`
- Test: `tests/harness/test_daemon.py`

**Architectural Fixes Applied:**
- **Zombie Deadlock:** Claim handler detects timed-out tasks (via `get_claimable_task`) and logs "reclaim" event with retry_count

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_daemon.py (add to existing file)

   import time
   from datetime import datetime, timezone, timedelta
   from harness.state import Task, TaskStatus, WorkflowState


   def test_handle_task_claim_returns_claimable(daemon_with_state, send_command):
       """task_claim returns first claimable task."""
       # Setup state with tasks
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First task", status=TaskStatus.PENDING, dependencies=[]),
               "task-2": Task(id="task-2", description="Second task", status=TaskStatus.PENDING, dependencies=["task-1"]),
           }
       )

       response = send_command({"command": "task_claim"})

       assert response["status"] == "ok"
       assert response["data"]["task_id"] == "task-1"
       assert response["data"]["description"] == "First task"


   def test_handle_task_claim_marks_running(daemon_with_state, send_command):
       """task_claim marks claimed task as running."""
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First", status=TaskStatus.PENDING, dependencies=[]),
           }
       )

       send_command({"command": "task_claim"})

       state = daemon_with_state.state_manager.load()
       assert state.tasks["task-1"].status == TaskStatus.RUNNING


   def test_handle_task_claim_none_available(daemon_with_state, send_command):
       """task_claim returns None when no tasks claimable (active task not timed out)."""
       now = datetime.now(timezone.utc).isoformat()
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="First",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   started_at=now,
                   timeout_seconds=3600,
               ),
           }
       )

       response = send_command({"command": "task_claim"})

       assert response["status"] == "ok"
       assert response["data"] is None


   def test_handle_task_claim_reclaims_timed_out(daemon_with_state, send_command):
       """task_claim reclaims a timed-out zombie task."""
       old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Zombie task",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   started_at=old_time,
                   timeout_seconds=3600,  # 1 hour timeout, started 2 hours ago
               ),
           }
       )

       response = send_command({"command": "task_claim"})

       assert response["status"] == "ok"
       assert response["data"]["task_id"] == "task-1"
       assert response["data"]["is_reclaim"] is True

       # Verify started_at was reset
       state = daemon_with_state.state_manager.load()
       new_started = datetime.fromisoformat(state.tasks["task-1"].started_at)
       assert new_started > datetime.fromisoformat(old_time)


   def test_handle_task_complete_marks_completed(daemon_with_state, send_command):
       """task_complete marks task as completed."""
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First", status=TaskStatus.RUNNING, dependencies=[]),
           }
       )

       response = send_command({"command": "task_complete", "task_id": "task-1"})

       assert response["status"] == "ok"
       state = daemon_with_state.state_manager.load()
       assert state.tasks["task-1"].status == TaskStatus.COMPLETED


   def test_handle_task_complete_invalid_id(daemon_with_state, send_command):
       """task_complete returns error for unknown task."""
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First", status=TaskStatus.RUNNING, dependencies=[]),
           }
       )

       response = send_command({"command": "task_complete", "task_id": "task-999"})

       assert response["status"] == "error"
       assert "not found" in response["message"].lower()


   def test_task_claim_logs_trajectory(daemon_with_state, send_command, tmp_path):
       """task_claim logs event to trajectory."""
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(id="task-1", description="First", status=TaskStatus.PENDING, dependencies=[]),
           }
       )

       send_command({"command": "task_claim"})

       trajectory = daemon_with_state.trajectory_logger.tail(1)
       assert len(trajectory) == 1
       assert trajectory[0]["event"] == "claim"
       assert trajectory[0]["task_id"] == "task-1"


   def test_task_reclaim_logs_trajectory_with_retry_count(daemon_with_state, send_command):
       """Reclaiming a timed-out task logs 'reclaim' event with retry_count."""
       old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
       daemon_with_state.state_manager.update(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Zombie",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   started_at=old_time,
                   timeout_seconds=3600,
               ),
           }
       )

       send_command({"command": "task_claim"})

       trajectory = daemon_with_state.trajectory_logger.tail(1)
       assert trajectory[0]["event"] == "reclaim"
       assert trajectory[0]["retry_count"] == 1
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v -k "task_claim or task_complete"
   ```
   Expected: FAIL (handlers not defined)

3. **Implement MINIMAL code:**

   In `src/harness/daemon.py`, add to imports:
   ```python
   import time
   from datetime import datetime, timezone
   from harness.state import TrajectoryLogger, TaskStatus
   ```

   Update HarnessDaemon.__init__ to add trajectory logger:
   ```python
   def __init__(self, worktree_root: Path, ...):
       ...
       self.trajectory_logger = TrajectoryLogger(worktree_root)
   ```

   Add handlers to dispatch dict:
   ```python
   handlers = {
       ...
       "task_claim": self._handle_task_claim,
       "task_complete": self._handle_task_complete,
   }
   ```

   Add handler methods:
   ```python
   def _handle_task_claim(self, request: dict, server: "HarnessDaemon") -> dict:
       """Claim next available task from the DAG.

       Handles both fresh claims and reclaims of timed-out zombie tasks.
       """
       state = server.state_manager.load()
       if not state:
           return {"status": "error", "message": "No workflow active"}

       task_id = state.get_claimable_task()
       if not task_id:
           return {"status": "ok", "data": None}

       task = state.tasks[task_id]

       # Detect if this is a reclaim (zombie recovery)
       is_reclaim = task.status == TaskStatus.RUNNING and task.is_timed_out()

       # Calculate retry count for reclaims
       retry_count = 0
       if is_reclaim:
           # Count previous reclaim events for this task
           trajectory = server.trajectory_logger.tail(100)
           retry_count = sum(
               1 for e in trajectory
               if e.get("task_id") == task_id and e.get("event") in ("claim", "reclaim")
           )

       # Mark as running with fresh timestamp
       now = datetime.now(timezone.utc).isoformat()
       updated_task = task.model_copy(update={
           "status": TaskStatus.RUNNING,
           "started_at": now,
       })
       updated_tasks = {**state.tasks, task_id: updated_task}
       server.state_manager.update(tasks=updated_tasks)

       # Log trajectory (different event type for reclaims)
       event_type = "reclaim" if is_reclaim else "claim"
       event = {
           "event": event_type,
           "task_id": task_id,
           "timestamp": time.time(),
       }
       if is_reclaim:
           event["retry_count"] = retry_count
       server.trajectory_logger.log(event)

       return {"status": "ok", "data": {
           "task_id": task_id,
           "description": task.description,
           "is_reclaim": is_reclaim,
       }}

   def _handle_task_complete(self, request: dict, server: "HarnessDaemon") -> dict:
       """Mark a task as completed."""
       task_id = request.get("task_id")
       if not task_id:
           return {"status": "error", "message": "task_id required"}

       state = server.state_manager.load()
       if not state:
           return {"status": "error", "message": "No workflow active"}

       if task_id not in state.tasks:
           return {"status": "error", "message": f"Task {task_id} not found"}

       # Mark as completed
       task = state.tasks[task_id]
       now = datetime.now(timezone.utc).isoformat()
       updated_task = task.model_copy(update={
           "status": TaskStatus.COMPLETED,
           "completed_at": now,
       })
       updated_tasks = {**state.tasks, task_id: updated_task}
       server.state_manager.update(tasks=updated_tasks)

       # Log trajectory
       server.trajectory_logger.log({
           "event": "complete",
           "task_id": task_id,
           "timestamp": time.time(),
       })

       return {"status": "ok", "data": {"task_id": task_id}}
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_daemon.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(daemon): add task_claim and task_complete handlers with zombie reclaim"
   ```

---

### Task 5: Client Task Commands (client.py)

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/client.py`
- Test: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_client.py (add to existing file)

   import json
   import subprocess


   def test_task_claim_command(harness_cli, daemon_process):
       """harness task claim returns claimable task JSON."""
       # Setup: daemon already has state with tasks
       result = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
       )

       assert result.returncode == 0
       data = json.loads(result.stdout)
       assert "task_id" in data or data is None


   def test_task_complete_command(harness_cli, daemon_process):
       """harness task complete --id <id> marks task done."""
       # First claim a task
       claim_result = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
       )
       if claim_result.returncode == 0 and claim_result.stdout.strip() != "null":
           data = json.loads(claim_result.stdout)
           task_id = data["task_id"]

           # Complete it
           result = subprocess.run(
               [harness_cli, "task", "complete", "--id", task_id],
               capture_output=True,
               text=True,
           )

           assert result.returncode == 0


   def test_task_claim_no_daemon(harness_cli, tmp_path, monkeypatch):
       """harness task claim auto-spawns daemon."""
       # Point to non-existent socket (fresh env)
       monkeypatch.setenv("HARNESS_SOCKET", str(tmp_path / "test.sock"))

       # Should either spawn daemon or fail gracefully
       result = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           timeout=10,
       )

       # Accept either success (spawned) or error (no state)
       assert result.returncode in [0, 1]
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_client.py -v -k "task"
   ```
   Expected: FAIL (task subcommand not defined)

3. **Implement MINIMAL code:**

   In `src/harness/client.py`, add to argument parser:
   ```python
   # Task commands
   task_parser = subparsers.add_parser("task", help="Task management commands")
   task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)

   # harness task claim
   task_claim = task_subparsers.add_parser("claim", help="Claim next available task")

   # harness task complete
   task_complete = task_subparsers.add_parser("complete", help="Mark task as completed")
   task_complete.add_argument("--id", required=True, help="Task ID to complete")
   ```

   Add command handlers:
   ```python
   def _cmd_task_claim(args, socket_path: str, worktree_root: str) -> int:
       """Claim next available task from daemon."""
       response = send_rpc(socket_path, {"command": "task_claim"}, worktree_root)

       if response.get("status") == "error":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           return 1

       data = response.get("data")
       print(json.dumps(data))
       return 0


   def _cmd_task_complete(args, socket_path: str, worktree_root: str) -> int:
       """Mark task as completed."""
       response = send_rpc(
           socket_path,
           {"command": "task_complete", "task_id": args.id},
           worktree_root,
       )

       if response.get("status") == "error":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           return 1

       print(json.dumps(response.get("data")))
       return 0
   ```

   Add dispatch in main:
   ```python
   elif args.command == "task":
       if args.task_command == "claim":
           return _cmd_task_claim(args, socket_path, worktree_root)
       elif args.task_command == "complete":
           return _cmd_task_complete(args, socket_path, worktree_root)
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_client.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): add task claim and task complete commands"
   ```

---

### Task 6: Integration Tests

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


   def test_full_task_workflow(harness_cli, tmp_worktree, daemon_process):
       """End-to-end: init workflow with tasks, claim, complete."""
       # Initialize workflow with tasks (via update-state or init command)
       init_result = subprocess.run(
           [
               harness_cli, "update-state",
               "--field", "workflow", "execute-plan",
               "--field", "plan", "test.md",
           ],
           capture_output=True,
           text=True,
           cwd=tmp_worktree,
       )
       assert init_result.returncode == 0

       # TODO: Add tasks via new init-tasks command or direct state update

       # Claim task
       claim_result = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           cwd=tmp_worktree,
       )

       if claim_result.stdout.strip() != "null":
           data = json.loads(claim_result.stdout)
           task_id = data["task_id"]

           # Complete task
           complete_result = subprocess.run(
               [harness_cli, "task", "complete", "--id", task_id],
               capture_output=True,
               text=True,
               cwd=tmp_worktree,
           )
           assert complete_result.returncode == 0


   def test_dag_dependency_enforcement(harness_cli, tmp_worktree, daemon_process):
       """DAG enforces task dependencies: can't claim blocked task."""
       # Setup tasks where task-2 depends on task-1
       # ... setup code ...

       # First claim should return task-1 (no deps)
       claim1 = subprocess.run(
           [harness_cli, "task", "claim"],
           capture_output=True,
           text=True,
           cwd=tmp_worktree,
       )

       if claim1.stdout.strip() != "null":
           data1 = json.loads(claim1.stdout)
           assert data1["task_id"] == "task-1"

           # Second claim should return None (task-2 blocked)
           claim2 = subprocess.run(
               [harness_cli, "task", "claim"],
               capture_output=True,
               text=True,
               cwd=tmp_worktree,
           )
           # task-2 is blocked until task-1 completes
           assert claim2.stdout.strip() == "null"


   def test_trajectory_logging(harness_cli, tmp_worktree, daemon_process):
       """Trajectory log captures claim and complete events."""
       # Perform claim and complete
       # ...

       # Check trajectory file
       trajectory_file = tmp_worktree / ".claude" / "trajectory.jsonl"
       if trajectory_file.exists():
           lines = trajectory_file.read_text().strip().split("\n")
           events = [json.loads(line) for line in lines if line]

           event_types = [e["event"] for e in events]
           assert "claim" in event_types or "complete" in event_types
   ```

2. **Run test, verify FAILURE:**
   ```bash
   uv run pytest tests/harness/test_integration.py -v -k "task_workflow or dag or trajectory"
   ```
   Expected: FAIL (tests need actual implementation context)

3. **Implement MINIMAL code:**

   Update test fixtures to setup proper task state:
   ```python
   @pytest.fixture
   def workflow_with_tasks(daemon_process, harness_cli, tmp_worktree):
       """Setup workflow state with DAG tasks."""
       # Use daemon's state manager to inject tasks
       # This may require a new init-tasks command or direct file setup
       ...
   ```

4. **Run test, verify PASS:**
   ```bash
   uv run pytest tests/harness/test_integration.py -v
   ```
   Expected: PASS (all tests green)

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add end-to-end tests for task workflow"
   ```

---

### Task 7: Code Review

**Effort:** simple (3-10 tool calls)

**Files:**
- All modified files from Tasks 1-6

**Instructions:**

1. Review all changes since base commit
2. Check for:
   - Thread safety (locks used correctly)
   - Error handling (graceful failures)
   - Code style (consistent with existing patterns)
   - Test coverage (all new code tested)
3. Address any issues found
4. Final verification:
   ```bash
   uv run pytest tests/harness/ -v
   uv run ruff check src/harness/
   ```

---

## Summary

| Task | Effort | Files | Commit Message | Arch Fix |
|------|--------|-------|----------------|----------|
| 1. Runtime Abstraction | standard | git.py, test_git.py | `feat(runtime): add LocalRuntime and DockerRuntime with env and path mapping` | Secret-Less, Path Mismatch |
| 2. Trajectory Logger | standard | state.py, test_state.py | `feat(trajectory): add TrajectoryLogger with efficient reverse-seek tail` | I/O Suicide |
| 3. DAG-Based State | complex | state.py, test_state.py | `feat(state): add Task model with timeout and zombie detection in get_claimable_task` | Zombie Deadlock |
| 4. Daemon Handlers | standard | daemon.py, test_daemon.py | `feat(daemon): add task_claim and task_complete handlers with zombie reclaim` | Zombie Deadlock |
| 5. Client Commands | standard | client.py, test_client.py | `feat(client): add task claim and task complete commands` | - |
| 6. Integration Tests | standard | test_integration.py | `test(integration): add end-to-end tests for task workflow` | - |
| 7. Code Review | simple | all | (no commit, review only) | - |

**Total estimated tool calls:** 75-100

---

## Architectural Fixes Summary

| Issue | Task(s) | Solution |
|-------|---------|----------|
| **I/O Suicide** | Task 2 | Efficient reverse-seek `tail()` reads from end in 4KB blocks |
| **Path Mismatch** | Task 1 | `DockerRuntime.mount_map` translates host→container paths |
| **Zombie Deadlock** | Task 3, 4 | `Task.timeout_seconds` + `is_timed_out()` + reclaim logic |
| **Secret-Less Agent** | Task 1 | `env` parameter on `execute()`, merged with os.environ for LocalRuntime, -e flags for DockerRuntime |
