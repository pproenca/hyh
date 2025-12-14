# Harness Architecture Implementation Plan

> **For Claude:** `/dev-workflow:execute-plan` for batch checkpoints, or `Skill("dev-workflow:subagent-driven-development")` for autonomous execution.
>
> **Methodology:** All tasks use `dev-workflow:test-driven-development` skill (RED -> GREEN -> REFACTOR).

**Goal:** Replace Bash-based state management with a persistent Python 3.13t daemon ("harness-d") that provides thread-safe state access, eliminates race conditions via global git mutex, and communicates over Unix Domain Sockets.

**Architecture:** Python daemon using `socketserver.ThreadingUnixStreamServer` (no asyncio - leverages Python 3.13t free-threading for true parallelism). Pydantic validation happens ONLY in daemon. "Dumb" CLI client imports nothing but stdlib (`sys`, `json`, `socket`, `os`, `subprocess`). Client auto-spawns daemon on connection failure with `fcntl.flock` to prevent races.

**Tech Stack:** Python 3.13t (free-threading), Pydantic v2, socketserver.ThreadingUnixStreamServer, threading.Lock, fcntl.flock, pytest

---

## Architecture Review Notes (The Council)

**Rejected patterns:**
- `asyncio` - defeats free-threading purpose, adds complexity for blocking subprocess calls
- Heavy client imports - Pydantic import adds ~200ms latency to every hook
- `nohup` + sleep loop for daemon startup - race conditions, fragile

**Approved patterns:**
- `socketserver.ThreadingMixIn` - each client gets real OS thread, runs in parallel
- Dumb client with auto-spawn - zero-latency, stdlib only, spawns daemon on demand
- `fcntl.flock` for socket locking - guarantees single daemon per worktree

---

## Task Grouping Strategy

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent Python modules, no file overlap |
| Group 2 | 3 | Threaded daemon imports state + git, depends on Group 1 |
| Group 3 | 4 | Dumb client with auto-spawn, depends on daemon protocol |
| Group 4 | 5, 6 | Independent hook shims, no file overlap |
| Group 5 | 7 | Integration with skill, final verification |

---

### Task 1: Create State Models with Pydantic

**Effort:** standard (10-15 tool calls)

**Files:**

- Create: `harness/state.py`
- Create: `harness/__init__.py`
- Test: `tests/harness/test_state.py`

**Steps:**

1. Write failing test for WorkflowState model:
   ```python
   # tests/harness/test_state.py
   import pytest
   from harness.state import WorkflowState, PendingHandoff, StateManager

   def test_workflow_state_validation():
       state = WorkflowState(
           workflow="subagent",
           plan="/path/to/plan.md",
           current_task=3,
           total_tasks=10,
           worktree="/path/to/worktree",
           base_sha="abc123def",
           last_commit="def456abc",
           parallel_mode=True,
       )
       assert state.current_task == 3
       assert state.workflow == "subagent"

   def test_workflow_state_rejects_invalid_workflow():
       with pytest.raises(ValueError):
           WorkflowState(
               workflow="invalid",
               plan="/path/to/plan.md",
               current_task=0,
               total_tasks=5,
               worktree="/path",
               base_sha="abc",
           )

   def test_pending_handoff_model():
       handoff = PendingHandoff(mode="sequential", plan="/path/to/plan.md")
       assert handoff.mode == "sequential"
   ```

2. Run: `pytest tests/harness/test_state.py -v`
   Expected: FAIL (module not found)

3. Implement state models:
   ```python
   # harness/__init__.py
   """Harness: Thread-safe state management for dev-workflow."""

   # harness/state.py
   from pydantic import BaseModel, Field
   from typing import Literal
   from pathlib import Path
   import re

   class WorkflowState(BaseModel):
       """State for an active workflow execution."""
       workflow: Literal["execute-plan", "subagent"] = Field(
           ..., description="Execution mode"
       )
       plan: str = Field(..., description="Absolute path to plan file")
       current_task: int = Field(ge=0, description="Last completed task (0=not started)")
       total_tasks: int = Field(gt=0, description="Total tasks in plan")
       worktree: str = Field(..., description="Absolute path to worktree")
       base_sha: str = Field(..., description="Base commit SHA before workflow")
       last_commit: str | None = Field(None, description="Last commit SHA")
       current_group: int = Field(1, ge=1, description="Current parallel group")
       total_groups: int = Field(1, ge=1, description="Total parallel groups")
       parallel_mode: bool = Field(True, description="Enable parallel execution")
       batch_size: int = Field(5, ge=0, description="Tasks per batch (0=unbatched)")
       retry_count: int = Field(0, ge=0, le=2, description="Retries for current task")
       failed_tasks: str = Field("", description="Comma-separated failed task numbers")
       enabled: bool = Field(True, description="Workflow active")

   class PendingHandoff(BaseModel):
       """Handoff file for session resume."""
       mode: Literal["sequential", "subagent"]
       plan: str

   class StateManager:
       """Manages workflow state with file persistence."""

       def __init__(self, worktree_root: Path):
           self.worktree_root = Path(worktree_root)
           self.state_file = self.worktree_root / ".claude" / "dev-workflow-state.local.md"
           self._state: WorkflowState | None = None

       def load(self) -> WorkflowState | None:
           """Load state from disk."""
           if not self.state_file.exists():
               return None

           content = self.state_file.read_text()
           frontmatter = self._parse_frontmatter(content)
           if not frontmatter:
               return None

           self._state = WorkflowState(**frontmatter)
           return self._state

       def save(self, state: WorkflowState) -> None:
           """Save state to disk atomically."""
           self._state = state
           self.state_file.parent.mkdir(parents=True, exist_ok=True)

           content = self._to_frontmatter(state)
           temp_file = self.state_file.with_suffix(".tmp")
           temp_file.write_text(content)
           temp_file.rename(self.state_file)

       def update(self, **kwargs) -> WorkflowState:
           """Update specific fields atomically."""
           if not self._state:
               raise ValueError("No state loaded")
           self._state = self._state.model_copy(update=kwargs)
           self.save(self._state)
           return self._state

       def _parse_frontmatter(self, content: str) -> dict | None:
           """Parse YAML frontmatter from markdown."""
           match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
           if not match:
               return None

           result = {}
           for line in match.group(1).split("\n"):
               if ":" in line:
                   key, value = line.split(":", 1)
                   key = key.strip()
                   value = value.strip().strip('"').strip("'")
                   # Type coercion
                   if value.lower() == "true":
                       value = True
                   elif value.lower() == "false":
                       value = False
                   elif value.isdigit():
                       value = int(value)
                   result[key] = value
           return result

       def _to_frontmatter(self, state: WorkflowState) -> str:
           """Convert state to markdown frontmatter."""
           lines = ["---"]
           for key, value in state.model_dump().items():
               if isinstance(value, bool):
                   value = str(value).lower()
               lines.append(f"{key}: {value}")
           lines.append("---")
           lines.append("")
           return "\n".join(lines)
   ```

4. Run: `pytest tests/harness/test_state.py -v`
   Expected: PASS

5. Add test for StateManager persistence:
   ```python
   # Add to tests/harness/test_state.py
   def test_state_manager_save_and_load(tmp_path):
       manager = StateManager(tmp_path)
       state = WorkflowState(
           workflow="execute-plan",
           plan="/path/to/plan.md",
           current_task=0,
           total_tasks=5,
           worktree=str(tmp_path),
           base_sha="abc123",
       )
       manager.save(state)

       # Load in new manager instance
       manager2 = StateManager(tmp_path)
       loaded = manager2.load()
       assert loaded is not None
       assert loaded.current_task == 0
       assert loaded.total_tasks == 5

   def test_state_manager_update(tmp_path):
       manager = StateManager(tmp_path)
       state = WorkflowState(
           workflow="subagent",
           plan="/p.md",
           current_task=0,
           total_tasks=3,
           worktree=str(tmp_path),
           base_sha="abc",
       )
       manager.save(state)

       updated = manager.update(current_task=1, last_commit="def456")
       assert updated.current_task == 1
       assert updated.last_commit == "def456"

       # Verify persisted
       loaded = StateManager(tmp_path).load()
       assert loaded.current_task == 1
   ```

6. Run: `pytest tests/harness/test_state.py -v`
   Expected: PASS

7. Commit: `git add -A && git commit -m "feat(harness): add Pydantic state models and StateManager"`

---

### Task 2: Implement Git Mutex Module

**Effort:** standard (10-15 tool calls)

**Files:**

- Create: `harness/git.py`
- Test: `tests/harness/test_git.py`

**Steps:**

1. Write failing test for global git lock:
   ```python
   # tests/harness/test_git.py
   import pytest
   import threading
   import time
   from unittest.mock import patch, MagicMock

   def test_git_execute_returns_result():
       """Git execute should return subprocess result."""
       from harness.git import safe_git_exec

       with patch("harness.git.subprocess.run") as mock_run:
           mock_run.return_value = MagicMock(
               returncode=0,
               stdout="abc123\n",
               stderr="",
           )
           result = safe_git_exec(["rev-parse", "HEAD"], cwd="/tmp")
           assert result.returncode == 0
           assert "abc123" in result.stdout

   def test_global_git_lock_serializes_operations():
       """Multiple git operations should not overlap (Python 3.13t free-threading)."""
       from harness.git import safe_git_exec, GLOBAL_GIT_LOCK

       execution_order = []

       def mock_git(*args, **kwargs):
           thread_name = threading.current_thread().name
           execution_order.append(f"start-{thread_name}")
           time.sleep(0.05)  # Simulate git operation
           execution_order.append(f"end-{thread_name}")
           return MagicMock(returncode=0, stdout="", stderr="")

       with patch("harness.git.subprocess.run", side_effect=mock_git):
           threads = []
           for i in range(3):
               t = threading.Thread(
                   target=lambda: safe_git_exec(["status"], cwd="/tmp"),
                   name=f"thread-{i}",
               )
               threads.append(t)

           for t in threads:
               t.start()
           for t in threads:
               t.join()

       # Verify serialization: each start followed by its end before next start
       for i in range(0, len(execution_order), 2):
           start = execution_order[i]
           end = execution_order[i + 1]
           thread_name = start.replace("start-", "")
           assert end == f"end-{thread_name}", f"Operations overlapped! Order: {execution_order}"
   ```

2. Run: `pytest tests/harness/test_git.py -v`
   Expected: FAIL (module not found)

3. Implement git mutex (simplified, no asyncio):
   ```python
   # harness/git.py
   """
   Thread-safe git operations with global mutex.

   In Python 3.13t (free-threading), this lock protects .git/index
   across ALL parallel threads without GIL contention.
   """
   import subprocess
   import threading
   from typing import List

   # Global mutex for ALL git operations across all threads
   # In Python 3.13t, threads run truly parallel - this lock is essential
   GLOBAL_GIT_LOCK = threading.Lock()

   def safe_git_exec(
       args: List[str],
       cwd: str,
       timeout: int = 60,
   ) -> subprocess.CompletedProcess:
       """
       Execute git command with global mutex protection.

       Blocking call is fine because we're in a ThreadingMixIn server.
       Other clients are handled by other threads while we wait.

       Args:
           args: Git command arguments (without 'git' prefix)
           cwd: Working directory for git command
           timeout: Command timeout in seconds

       Returns:
           CompletedProcess with returncode, stdout, stderr
       """
       with GLOBAL_GIT_LOCK:
           return subprocess.run(
               ["git"] + args,
               cwd=cwd,
               capture_output=True,
               text=True,
               timeout=timeout,
           )

   def safe_commit(cwd: str, message: str) -> subprocess.CompletedProcess:
       """Atomic add + commit operation under single lock acquisition."""
       with GLOBAL_GIT_LOCK:
           # Stage all changes
           add_result = subprocess.run(
               ["git", "add", "-A"],
               cwd=cwd,
               capture_output=True,
               text=True,
           )
           if add_result.returncode != 0:
               return add_result

           # Commit
           return subprocess.run(
               ["git", "commit", "-m", message],
               cwd=cwd,
               capture_output=True,
               text=True,
           )

   def get_head_sha(cwd: str) -> str | None:
       """Get current HEAD commit SHA."""
       result = safe_git_exec(["rev-parse", "HEAD"], cwd=cwd)
       if result.returncode == 0:
           return result.stdout.strip()
       return None
   ```

4. Run: `pytest tests/harness/test_git.py -v`
   Expected: PASS

5. Add test for safe_commit:
   ```python
   # Add to tests/harness/test_git.py
   def test_safe_commit_atomic(tmp_path):
       """safe_commit should be atomic add + commit."""
       import subprocess

       # Initialize git repo
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
       subprocess.run(
           ["git", "config", "user.email", "test@test.com"],
           cwd=tmp_path, capture_output=True
       )
       subprocess.run(
           ["git", "config", "user.name", "Test"],
           cwd=tmp_path, capture_output=True
       )

       # Create a file
       (tmp_path / "test.txt").write_text("hello")

       from harness.git import safe_commit, get_head_sha

       result = safe_commit(str(tmp_path), "test commit")
       assert result.returncode == 0

       sha = get_head_sha(str(tmp_path))
       assert sha is not None
       assert len(sha) == 40
   ```

6. Run: `pytest tests/harness/test_git.py -v`
   Expected: PASS

7. Commit: `git add -A && git commit -m "feat(harness): add thread-safe git mutex with GLOBAL_GIT_LOCK"`

---

### Task 3: Implement Threaded Daemon (No asyncio)

**Effort:** complex (15-25 tool calls)

**Files:**

- Create: `harness/daemon.py`
- Modify: `harness/__init__.py`
- Test: `tests/harness/test_daemon.py`

**Steps:**

1. Write failing test for threaded daemon:
   ```python
   # tests/harness/test_daemon.py
   import pytest
   import json
   import socket
   import threading
   import time
   import subprocess
   from pathlib import Path

   @pytest.fixture
   def socket_path(tmp_path):
       return str(tmp_path / "harness.sock")

   @pytest.fixture
   def worktree(tmp_path):
       """Create a mock worktree with state."""
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
       subprocess.run(
           ["git", "config", "user.email", "test@test.com"],
           cwd=tmp_path, capture_output=True
       )
       subprocess.run(
           ["git", "config", "user.name", "Test"],
           cwd=tmp_path, capture_output=True
       )
       (tmp_path / "file.txt").write_text("content")
       subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
       subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True)
       return tmp_path

   def send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
       """Send command to daemon and get response."""
       sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
       sock.settimeout(timeout)
       sock.connect(socket_path)
       sock.sendall(json.dumps(command).encode() + b"\n")
       response = b""
       while True:
           chunk = sock.recv(4096)
           if not chunk:
               break
           response += chunk
           if b"\n" in response:
               break
       sock.close()
       return json.loads(response.decode().strip())

   def test_daemon_get_state(socket_path, worktree):
       from harness.daemon import HarnessDaemon
       from harness.state import WorkflowState, StateManager

       # Create state file
       manager = StateManager(worktree)
       manager.save(WorkflowState(
           workflow="subagent",
           plan="/plan.md",
           current_task=2,
           total_tasks=5,
           worktree=str(worktree),
           base_sha="abc123",
       ))

       daemon = HarnessDaemon(socket_path, str(worktree))

       # Start daemon in background thread
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.1)  # Let it start

       try:
           response = send_command(socket_path, {"command": "get_state"})
           assert response["status"] == "ok"
           assert response["data"]["current_task"] == 2
           assert response["data"]["total_tasks"] == 5
       finally:
           daemon.shutdown()
           server_thread.join(timeout=1)

   def test_daemon_update_state(socket_path, worktree):
       from harness.daemon import HarnessDaemon
       from harness.state import WorkflowState, StateManager

       manager = StateManager(worktree)
       manager.save(WorkflowState(
           workflow="execute-plan",
           plan="/plan.md",
           current_task=0,
           total_tasks=3,
           worktree=str(worktree),
           base_sha="abc",
       ))

       daemon = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.1)

       try:
           response = send_command(socket_path, {
               "command": "update_state",
               "updates": {"current_task": 1, "last_commit": "def456"},
           })
           assert response["status"] == "ok"

           # Verify persisted
           loaded = StateManager(worktree).load()
           assert loaded.current_task == 1
           assert loaded.last_commit == "def456"
       finally:
           daemon.shutdown()
           server_thread.join(timeout=1)

   def test_daemon_git_operations(socket_path, worktree):
       from harness.daemon import HarnessDaemon

       daemon = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.1)

       try:
           response = send_command(socket_path, {
               "command": "git",
               "args": ["rev-parse", "HEAD"],
               "cwd": str(worktree),
           })
           assert response["status"] == "ok"
           assert response["data"]["returncode"] == 0
           assert len(response["data"]["stdout"].strip()) == 40
       finally:
           daemon.shutdown()
           server_thread.join(timeout=1)

   def test_daemon_parallel_clients(socket_path, worktree):
       """Verify daemon handles parallel clients (Python 3.13t threading)."""
       from harness.daemon import HarnessDaemon
       from harness.state import WorkflowState, StateManager

       manager = StateManager(worktree)
       manager.save(WorkflowState(
           workflow="subagent",
           plan="/plan.md",
           current_task=0,
           total_tasks=10,
           worktree=str(worktree),
           base_sha="abc",
       ))

       daemon = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.1)

       results = []
       errors = []

       def client_request(client_id):
           try:
               resp = send_command(socket_path, {"command": "get_state"})
               results.append((client_id, resp["status"]))
           except Exception as e:
               errors.append((client_id, str(e)))

       try:
           # Launch 5 parallel clients
           threads = [
               threading.Thread(target=client_request, args=(i,))
               for i in range(5)
           ]
           for t in threads:
               t.start()
           for t in threads:
               t.join()

           assert len(errors) == 0, f"Errors: {errors}"
           assert len(results) == 5
           assert all(status == "ok" for _, status in results)
       finally:
           daemon.shutdown()
           server_thread.join(timeout=1)
   ```

2. Run: `pytest tests/harness/test_daemon.py -v`
   Expected: FAIL (module not found)

3. Implement threaded daemon (NO asyncio):
   ```python
   # harness/daemon.py
   """
   Harness Daemon - Thread-safe state management server.

   Uses socketserver.ThreadingUnixStreamServer for true parallelism
   in Python 3.13t (free-threading). No asyncio.

   Each client connection gets a real OS thread that runs in parallel.
   Pydantic validation happens here - the client is deliberately "dumb".
   """
   import fcntl
   import json
   import os
   import signal
   import socketserver
   import sys
   import threading
   from pathlib import Path

   from .state import StateManager, WorkflowState
   from .git import safe_git_exec, safe_commit, get_head_sha

   class HarnessHandler(socketserver.StreamRequestHandler):
       """
       Handle a single client connection.

       In Python 3.13t, this runs in a parallel thread without GIL contention.
       CPU-heavy Pydantic validation happens here, not in the client.
       """

       def handle(self):
           try:
               line = self.rfile.readline()
               if not line:
                   return

               request = json.loads(line.decode())
               response = self.dispatch(request)
               self.wfile.write(json.dumps(response).encode() + b"\n")
           except Exception as e:
               error_response = {"status": "error", "message": str(e)}
               self.wfile.write(json.dumps(error_response).encode() + b"\n")

       def dispatch(self, request: dict) -> dict:
           """Route command to handler."""
           command = request.get("command")
           server = self.server  # type: HarnessDaemon

           handlers = {
               "get_state": self._handle_get_state,
               "update_state": self._handle_update_state,
               "git": self._handle_git,
               "ping": self._handle_ping,
               "shutdown": self._handle_shutdown,
           }

           handler = handlers.get(command)
           if not handler:
               return {"status": "error", "message": f"Unknown command: {command}"}

           return handler(request, server)

       def _handle_get_state(self, request: dict, server: "HarnessDaemon") -> dict:
           state = server.state_manager.load()
           if state is None:
               return {"status": "ok", "data": None}
           return {"status": "ok", "data": state.model_dump()}

       def _handle_update_state(self, request: dict, server: "HarnessDaemon") -> dict:
           updates = request.get("updates", {})
           if not updates:
               return {"status": "error", "message": "No updates provided"}
           try:
               # Pydantic validation happens here (CPU-heavy, parallel thread)
               updated = server.state_manager.update(**updates)
               return {"status": "ok", "data": updated.model_dump()}
           except Exception as e:
               return {"status": "error", "message": str(e)}

       def _handle_git(self, request: dict, server: "HarnessDaemon") -> dict:
           args = request.get("args", [])
           cwd = request.get("cwd", str(server.worktree_root))
           result = safe_git_exec(args, cwd)
           return {
               "status": "ok",
               "data": {
                   "returncode": result.returncode,
                   "stdout": result.stdout,
                   "stderr": result.stderr,
               },
           }

       def _handle_ping(self, request: dict, server: "HarnessDaemon") -> dict:
           return {"status": "ok", "data": {"running": True, "pid": os.getpid()}}

       def _handle_shutdown(self, request: dict, server: "HarnessDaemon") -> dict:
           # Schedule shutdown in separate thread to allow response
           threading.Thread(target=server.shutdown, daemon=True).start()
           return {"status": "ok", "data": {"shutdown": True}}


   class HarnessDaemon(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
       """
       Threaded Unix socket server for workflow state management.

       ThreadingMixIn + Python 3.13t = true parallel thread execution.
       """
       daemon_threads = True  # Auto-kill threads on exit
       allow_reuse_address = True

       def __init__(self, socket_path: str, worktree_root: str):
           self.socket_path = socket_path
           self.worktree_root = Path(worktree_root)
           self.state_manager = StateManager(self.worktree_root)
           self._lock_fd = None

           # Acquire exclusive lock to prevent multiple daemons
           self._acquire_lock()

           # Remove stale socket
           if os.path.exists(socket_path):
               os.unlink(socket_path)

           super().__init__(socket_path, HarnessHandler)

           # Set socket permissions (user only)
           os.chmod(socket_path, 0o600)

           # Load initial state
           self.state_manager.load()

       def _acquire_lock(self):
           """Acquire exclusive lock on socket path (idempotency)."""
           lock_path = self.socket_path + ".lock"
           self._lock_fd = open(lock_path, "w")
           try:
               fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
           except BlockingIOError:
               self._lock_fd.close()
               raise RuntimeError("Another daemon is already running")

       def server_close(self):
           """Clean up on shutdown."""
           super().server_close()
           if os.path.exists(self.socket_path):
               os.unlink(self.socket_path)
           if self._lock_fd:
               fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
               self._lock_fd.close()


   def run_daemon(socket_path: str, worktree_root: str) -> None:
       """Entry point for running daemon as subprocess."""
       daemon = HarnessDaemon(socket_path, worktree_root)

       def handle_sigterm(*args):
           daemon.shutdown()

       signal.signal(signal.SIGTERM, handle_sigterm)
       signal.signal(signal.SIGINT, handle_sigterm)

       try:
           daemon.serve_forever()
       finally:
           daemon.server_close()


   if __name__ == "__main__":
       if len(sys.argv) != 3:
           print("Usage: python -m harness.daemon <socket_path> <worktree_root>")
           sys.exit(1)
       run_daemon(sys.argv[1], sys.argv[2])
   ```

4. Run: `pytest tests/harness/test_daemon.py -v`
   Expected: PASS

5. Add test for fcntl lock (single daemon guarantee):
   ```python
   # Add to tests/harness/test_daemon.py
   def test_daemon_single_instance_lock(socket_path, worktree):
       """fcntl.flock should prevent multiple daemons."""
       from harness.daemon import HarnessDaemon

       daemon1 = HarnessDaemon(socket_path, str(worktree))
       server_thread = threading.Thread(target=daemon1.serve_forever)
       server_thread.daemon = True
       server_thread.start()
       time.sleep(0.1)

       try:
           # Second daemon should fail to acquire lock
           with pytest.raises(RuntimeError, match="Another daemon"):
               HarnessDaemon(socket_path, str(worktree))
       finally:
           daemon1.shutdown()
           server_thread.join(timeout=1)
   ```

6. Run: `pytest tests/harness/test_daemon.py -v`
   Expected: PASS

7. Commit: `git add -A && git commit -m "feat(harness): add threaded daemon with socketserver.ThreadingMixIn"`

---

### Task 4: Implement "Dumb" Client with Auto-Spawn

**Effort:** standard (10-15 tool calls)

**Files:**

- Create: `harness/client.py`
- Test: `tests/harness/test_client.py`

**Steps:**

1. Write failing test for dumb client:
   ```python
   # tests/harness/test_client.py
   """
   Tests for the "dumb" client.

   The client MUST NOT import pydantic or harness.state.
   It only uses stdlib: sys, json, socket, os, subprocess, time, argparse.
   """
   import pytest
   import subprocess
   import time
   import sys
   from pathlib import Path

   def test_client_has_no_heavy_imports():
       """Client must only import stdlib modules."""
       # Import the module and check its imports
       import harness.client as client_module

       # Get all imported modules
       import_names = set()
       for name, obj in vars(client_module).items():
           if hasattr(obj, "__module__"):
               import_names.add(obj.__module__)

       # These are forbidden (heavy imports)
       forbidden = {"pydantic", "harness.state"}
       violations = import_names & forbidden
       assert not violations, f"Client has forbidden imports: {violations}"

   @pytest.fixture
   def worktree_with_daemon(tmp_path):
       """Create worktree and let client auto-spawn daemon."""
       # Initialize git repo
       subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
       subprocess.run(
           ["git", "config", "user.email", "test@test.com"],
           cwd=tmp_path, capture_output=True
       )
       subprocess.run(
           ["git", "config", "user.name", "Test"],
           cwd=tmp_path, capture_output=True
       )
       (tmp_path / "file.txt").write_text("content")
       subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
       subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True)

       # Create state file
       state_dir = tmp_path / ".claude"
       state_dir.mkdir()
       state_file = state_dir / "dev-workflow-state.local.md"
       state_file.write_text("""---
   workflow: subagent
   plan: /plan.md
   current_task: 1
   total_tasks: 5
   worktree: {worktree}
   base_sha: abc123
   enabled: true
   ---
   """.format(worktree=str(tmp_path)))

       socket_path = str(tmp_path / "harness.sock")

       yield {"socket": socket_path, "worktree": tmp_path}

       # Cleanup: kill any spawned daemon
       subprocess.run(["pkill", "-f", f"harness.daemon.*{socket_path}"], capture_output=True)

   def test_client_auto_spawns_daemon(worktree_with_daemon):
       """Client should spawn daemon on ConnectionRefused."""
       from harness.client import send_rpc

       socket_path = worktree_with_daemon["socket"]
       worktree = worktree_with_daemon["worktree"]

       # No daemon running yet - client should auto-spawn
       response = send_rpc(
           socket_path,
           {"command": "ping"},
           worktree_root=str(worktree),
       )

       assert response["status"] == "ok"
       assert response["data"]["running"] is True

   def test_client_get_state(worktree_with_daemon):
       from harness.client import send_rpc

       socket_path = worktree_with_daemon["socket"]
       worktree = worktree_with_daemon["worktree"]

       response = send_rpc(
           socket_path,
           {"command": "get_state"},
           worktree_root=str(worktree),
       )

       assert response["status"] == "ok"
       assert response["data"]["current_task"] == 1
       assert response["data"]["total_tasks"] == 5

   def test_client_git_command(worktree_with_daemon):
       from harness.client import send_rpc

       socket_path = worktree_with_daemon["socket"]
       worktree = worktree_with_daemon["worktree"]

       response = send_rpc(
           socket_path,
           {"command": "git", "args": ["status"], "cwd": str(worktree)},
           worktree_root=str(worktree),
       )

       assert response["status"] == "ok"
       assert response["data"]["returncode"] == 0

   def test_spawn_daemon_detects_crash(tmp_path):
       """spawn_daemon should detect immediate crashes (zombie detection)."""
       from harness.client import spawn_daemon

       socket_path = str(tmp_path / "harness.sock")
       # Pass non-existent worktree to trigger crash
       nonexistent = str(tmp_path / "does-not-exist")

       with pytest.raises(RuntimeError, match="crashed"):
           spawn_daemon(nonexistent, socket_path)
   ```

2. Run: `pytest tests/harness/test_client.py -v`
   Expected: FAIL (module not found)

3. Implement dumb client (STDLIB ONLY - no pydantic):
   ```python
   # harness/client.py
   """
   Harness CLI Client - "Dumb" client with auto-spawn.

   CRITICAL: This module MUST NOT import pydantic or harness.state.
   Every import adds ~200ms latency to git hooks.

   Only stdlib allowed: sys, json, socket, os, subprocess, time, argparse
   """
   import argparse
   import json
   import os
   import socket
   import subprocess
   import sys
   import time

   # Default socket path
   def get_socket_path() -> str:
       return f"/tmp/harness-{os.getenv('USER', 'default')}.sock"

   def spawn_daemon(worktree_root: str, socket_path: str) -> None:
       """
       Spawn daemon as detached subprocess.

       Uses start_new_session=True to fully detach from parent.
       The daemon will acquire fcntl lock to prevent duplicates.
       Checks process status during wait to detect immediate crashes.

       Timeout is configurable via HARNESS_TIMEOUT env var (default 5s).
       This accounts for slow CI environments where Pydantic import may take time.
       """
       proc = subprocess.Popen(
           [sys.executable, "-m", "harness.daemon", socket_path, worktree_root],
           start_new_session=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.PIPE,
           stdin=subprocess.DEVNULL,
       )

       # Wait for socket to appear (default 5 seconds for CI reliability)
       # Configurable via HARNESS_TIMEOUT env var
       timeout_seconds = int(os.getenv("HARNESS_TIMEOUT", "5"))
       iterations = timeout_seconds * 10  # 0.1s per iteration

       # Check if process died during startup (zombie detection)
       for _ in range(iterations):
           if proc.poll() is not None:
               # Daemon died immediately - get error output
               _, stderr = proc.communicate()
               raise RuntimeError(
                   f"Daemon crashed on startup (exit {proc.returncode}): "
                   f"{stderr.decode().strip()}"
               )

           if os.path.exists(socket_path):
               time.sleep(0.05)  # Extra delay for server to start accepting
               return
           time.sleep(0.1)

       # Timeout - check one more time if process died
       if proc.poll() is not None:
           _, stderr = proc.communicate()
           raise RuntimeError(
               f"Daemon crashed (exit {proc.returncode}): {stderr.decode().strip()}"
           )

       raise RuntimeError(
           f"Daemon failed to start (timeout {timeout_seconds}s waiting for socket)"
       )

   def send_rpc(
       socket_path: str,
       request: dict,
       worktree_root: str | None = None,
       timeout: float = 5.0,
       max_retries: int = 1,
   ) -> dict:
       """
       Send RPC request to daemon.

       If connection fails, auto-spawns daemon and retries.
       """
       for attempt in range(max_retries + 1):
           sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
           sock.settimeout(timeout)

           try:
               sock.connect(socket_path)
               sock.sendall(json.dumps(request).encode() + b"\n")

               # Read response
               response = b""
               while True:
                   chunk = sock.recv(4096)
                   if not chunk:
                       break
                   response += chunk
                   if b"\n" in response:
                       break

               return json.loads(response.decode().strip())

           except (FileNotFoundError, ConnectionRefusedError):
               if attempt < max_retries and worktree_root:
                   # Auto-spawn daemon
                   spawn_daemon(worktree_root, socket_path)
                   continue
               raise
           finally:
               sock.close()

   def _get_git_root() -> str:
       """Get git worktree root."""
       result = subprocess.run(
           ["git", "rev-parse", "--show-toplevel"],
           capture_output=True,
           text=True,
       )
       if result.returncode == 0:
           return result.stdout.strip()
       return os.getcwd()

   # NOTE: No _coerce_value function!
   # Type coercion is the Daemon's job via Pydantic schemas.
   # The client passes raw strings to avoid data corruption.
   # (e.g., branch name "true" should not become bool(True))

   def main():
       """CLI entry point using argparse."""
       parser = argparse.ArgumentParser(
           prog="harness",
           description="Thread-safe state management for dev-workflow"
       )
       subparsers = parser.add_subparsers(dest="command", required=True)

       # ping
       subparsers.add_parser("ping", help="Check if daemon is running")

       # get-state
       subparsers.add_parser("get-state", help="Get current workflow state")

       # update-state
       upd = subparsers.add_parser("update-state", help="Update workflow state fields")
       upd.add_argument(
           "--field", nargs=2, action="append", dest="fields",
           metavar=("KEY", "VALUE"), help="Field to update (repeatable)"
       )

       # git -- <args>
       git = subparsers.add_parser(
           "git",
           help="Execute git command with mutex (Usage: harness git -- <args>)",
           description="Run git commands through the daemon's global mutex. "
                       "The -- separator is REQUIRED to prevent flag confusion.",
           usage="harness git -- <git-command> [git-args...]",
       )
       git.add_argument(
           "git_args", nargs=argparse.REMAINDER,
           help="Git arguments after -- separator (e.g., harness git -- commit -m 'msg')"
       )

       # session-start
       subparsers.add_parser("session-start", help="Handle SessionStart hook")

       # check-state
       subparsers.add_parser("check-state", help="Handle Stop hook")

       # check-commit
       subparsers.add_parser("check-commit", help="Handle SubagentStop hook")

       # shutdown
       subparsers.add_parser("shutdown", help="Shutdown daemon")

       args = parser.parse_args()

       socket_path = os.getenv("HARNESS_SOCKET", get_socket_path())
       worktree_root = os.getenv("HARNESS_WORKTREE") or _get_git_root()

       # Route commands
       if args.command == "ping":
           _cmd_ping(socket_path, worktree_root)
       elif args.command == "get-state":
           _cmd_get_state(socket_path, worktree_root)
       elif args.command == "update-state":
           _cmd_update_state(socket_path, worktree_root, args.fields or [])
       elif args.command == "git":
           # Strip leading -- separator if present
           git_args = args.git_args
           if git_args and git_args[0] == "--":
               git_args = git_args[1:]
           _cmd_git(socket_path, worktree_root, git_args)
       elif args.command == "session-start":
           _cmd_session_start(socket_path, worktree_root)
       elif args.command == "check-state":
           _cmd_check_state(socket_path, worktree_root)
       elif args.command == "check-commit":
           _cmd_check_commit(socket_path, worktree_root)
       elif args.command == "shutdown":
           _cmd_shutdown(socket_path, worktree_root)

   def _cmd_ping(socket_path: str, worktree_root: str):
       try:
           response = send_rpc(socket_path, {"command": "ping"}, worktree_root)
           if response.get("status") == "ok":
               print("ok")
           else:
               print("error", file=sys.stderr)
               sys.exit(1)
       except (FileNotFoundError, ConnectionRefusedError):
           print("not running")
           sys.exit(1)

   def _cmd_get_state(socket_path: str, worktree_root: str):
       response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
       if response["status"] != "ok":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)
       if response["data"] is None:
           print("No active workflow")
           sys.exit(1)
       print(json.dumps(response["data"], indent=2))

   def _cmd_update_state(socket_path: str, worktree_root: str, fields: list):
       """Update state fields. fields is list of [key, value] pairs from argparse.

       NOTE: All values are passed as raw strings to the daemon.
       Pydantic handles type coercion based on the schema.
       """
       updates = {key: value for key, value in fields}  # Raw strings, no coercion

       response = send_rpc(
           socket_path,
           {"command": "update_state", "updates": updates},
           worktree_root,
       )
       if response["status"] != "ok":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)
       print(f"Updated: current_task={response['data'].get('current_task')}")

   def _cmd_git(socket_path: str, worktree_root: str, git_args: list):
       """Execute git command through daemon mutex."""
       cwd = os.getcwd()
       response = send_rpc(
           socket_path,
           {"command": "git", "args": git_args, "cwd": cwd},
           worktree_root,
       )
       if response["status"] != "ok":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)
       data = response["data"]
       print(data["stdout"], end="")
       if data["stderr"]:
           print(data["stderr"], file=sys.stderr, end="")
       sys.exit(data["returncode"])

   def _cmd_session_start(socket_path: str, worktree_root: str):
       try:
           response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
       except (FileNotFoundError, ConnectionRefusedError):
           print("{}")
           return

       if response["status"] != "ok" or response["data"] is None:
           print("{}")
           return

       state = response["data"]
       output = {
           "hookSpecificOutput": {
               "hookEventName": "SessionStart",
               "additionalContext": (
                   f"Resuming {state['workflow']}: "
                   f"task {state['current_task']}/{state['total_tasks']}"
               ),
           }
       }
       print(json.dumps(output))

   def _cmd_check_state(socket_path: str, worktree_root: str):
       try:
           response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
       except (FileNotFoundError, ConnectionRefusedError):
           print("allow")
           return

       if response["status"] != "ok" or response["data"] is None:
           print("allow")
           return

       state = response["data"]
       if state.get("enabled") and state["current_task"] < state["total_tasks"]:
           print(f"deny: Workflow in progress ({state['current_task']}/{state['total_tasks']})")
           sys.exit(1)
       print("allow")

   def _cmd_check_commit(socket_path: str, worktree_root: str):
       try:
           response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
       except (FileNotFoundError, ConnectionRefusedError):
           print("allow")
           return

       if response["status"] != "ok" or response["data"] is None:
           print("allow")
           return

       state = response["data"]

       # Get current HEAD via daemon (mutex-protected)
       git_response = send_rpc(
           socket_path,
           {"command": "git", "args": ["rev-parse", "HEAD"], "cwd": os.getcwd()},
           worktree_root,
       )
       if git_response["status"] != "ok":
           print("allow")  # Fail open
           return

       current_head = git_response["data"]["stdout"].strip()
       last_commit = state.get("last_commit")

       if last_commit and current_head == last_commit:
           print(f"deny: No new commit since {last_commit[:7]}")
           sys.exit(1)
       print("allow")

   def _cmd_shutdown(socket_path: str, worktree_root: str):
       try:
           response = send_rpc(socket_path, {"command": "shutdown"}, None)
           print("Shutdown requested")
       except (FileNotFoundError, ConnectionRefusedError):
           print("Daemon not running")

   if __name__ == "__main__":
       main()
   ```

4. Run: `pytest tests/harness/test_client.py -v`
   Expected: PASS

5. Add `__main__.py` entry point:
   ```python
   # harness/__main__.py
   """Entry point for `python -m harness`."""
   from harness.client import main

   if __name__ == "__main__":
       main()
   ```

6. Run: `pytest tests/harness/test_client.py -v`
   Expected: PASS

7. Commit: `git add -A && git commit -m "feat(harness): add dumb client with auto-spawn (stdlib only)"`

---

### Task 5: Replace Hook Shims

**Effort:** simple (3-10 tool calls)

**Files:**

- Modify: `hooks/session-start.sh`
- Modify: `hooks/check-state-on-stop.sh`
- Modify: `hooks/check-commit-on-subagent-stop.sh`

**Steps:**

1. Replace session-start.sh:
   ```bash
   #!/bin/bash
   # Session start hook - delegates to harness client
   # Client auto-spawns daemon if needed (no start-daemon.sh required)

   exec python3 -m harness session-start
   ```

2. Replace check-state-on-stop.sh:
   ```bash
   #!/bin/bash
   # Stop hook - verify workflow state before allowing stop

   exec python3 -m harness check-state
   ```

3. Replace check-commit-on-subagent-stop.sh:
   ```bash
   #!/bin/bash
   # Subagent stop hook - verify commit was made

   exec python3 -m harness check-commit
   ```

4. Update hooks.json to use new commands (if paths changed)

5. Test hooks manually:
   ```bash
   # In a test worktree
   python3 -m harness session-start
   # Should output JSON or {}

   python3 -m harness check-state
   # Should output "allow" or "deny: ..."
   ```

6. Commit: `git add -A && git commit -m "feat(hooks): replace all hooks with harness client calls"`

---

### Task 6: Deprecate hook-helpers.sh

**Effort:** simple (3-10 tool calls)

**Files:**

- Modify: `scripts/hook-helpers.sh`

**Steps:**

1. Add deprecation notice to hook-helpers.sh:
   ```bash
   #!/bin/bash
   # DEPRECATED: This file is deprecated in favor of harness daemon.
   # All functionality has been moved to harness/state.py and harness/git.py.
   #
   # Migration:
   #   frontmatter_get -> harness get-state | jq '.field'
   #   frontmatter_set -> harness update-state --field key value
   #   git operations  -> harness git <args>
   #
   # This file is kept for backwards compatibility with external tools.

   echo "WARNING: hook-helpers.sh is deprecated. Use 'harness' CLI instead." >&2

   # ... keep existing functions for backwards compat ...
   ```

2. Verify no internal code still sources hook-helpers.sh directly

3. Commit: `git add -A && git commit -m "chore: deprecate hook-helpers.sh in favor of harness"`

---

### Task 7: Update Subagent-Driven-Development Skill

**Effort:** complex (15-25 tool calls)

**Files:**

- Modify: `skills/subagent-driven-development/SKILL.md`

**Steps:**

1. Read current skill file to understand existing patterns

2. Update skill instructions to use harness:

   Replace direct git commands:
   ```markdown
   # Before
   git add -A && git commit -m "..."

   # After (note the -- separator before git arguments)
   harness git -- add -A && harness git -- commit -m "..."
   ```

   Replace state reading:
   ```markdown
   # Before
   source hook-helpers.sh
   CURRENT=$(frontmatter_get "$STATE_FILE" "current_task" "0")

   # After
   harness get-state  # Returns JSON
   ```

   Replace state updates:
   ```markdown
   # Before
   frontmatter_set "$STATE_FILE" "current_task" "$NEXT"

   # After
   harness update-state --field current_task "$NEXT"
   ```

3. Add new section explaining mutex protection and `--` separator:
   ```markdown
   ## Git Safety (Harness Mutex)

   All git operations MUST go through harness with `--` separator:
   - `harness git -- add -A` - stages all changes
   - `harness git -- commit -m "feat: description"` - commits with global mutex
   - `harness git -- status` - checks working tree
   - `harness git -- diff --cached` - view staged changes

   **IMPORTANT:** The `--` separator is REQUIRED to prevent git flags from
   being confused with harness flags. Always use: `harness git -- <git-command>`

   The harness daemon serializes ALL git operations across ALL parallel
   subagents using `threading.Lock()` in Python 3.13t. This eliminates
   `index.lock` race conditions completely.

   **Why this works:** In Python 3.13t (free-threading/no-GIL), the daemon
   handles each subagent in a separate OS thread. The global git lock ensures
   only one git operation runs at a time, while other daemon operations
   (state reads, pings) run in parallel.
   ```

4. Verify skill file syntax is valid markdown

5. Commit: `git add -A && git commit -m "feat(skill): update subagent-driven-development to use harness"`

6. Run integration test:
   - Create test plan with 3 parallel tasks
   - Execute with skill
   - Verify no `index.lock` errors
   - Verify state updates are atomic

7. Commit: `git add -A && git commit -m "test: verify harness integration with subagent skill"`

---

## Verification Checklist

After completing all tasks:

1. [ ] Client imports ONLY stdlib (`sys`, `json`, `socket`, `os`, `subprocess`, `time`, `argparse`)
2. [ ] Daemon uses `socketserver.ThreadingMixIn` (no asyncio)
3. [ ] `fcntl.flock` prevents multiple daemon instances
4. [ ] Client auto-spawns daemon on `ConnectionRefused`
5. [ ] Client detects zombie daemon (crash on startup)
6. [ ] `harness ping` returns "ok" when daemon running
7. [ ] `harness get-state` returns valid JSON
8. [ ] `harness git -- status` serializes properly (no index.lock)
9. [ ] All hooks delegate to `python3 -m harness <command>`
10. [ ] Subagent skill uses `harness git -- <args>` pattern
11. [ ] All tests pass: `pytest tests/harness/ -v`

## Performance Targets

| Operation | Target | Rationale |
|-----------|--------|-----------|
| `harness ping` (cold) | < 500ms | Daemon spawn + connect |
| `harness ping` (warm) | < 10ms | Socket round-trip only |
| `harness get-state` | < 15ms | Socket + JSON parse |
| `harness git status` | < 100ms | Mutex acquire + subprocess |
