# Harness v2.5: Minimum Viable Bridge

**Goal:** Enable "Orchestrator Injection" - the Orchestrator writes plans with embedded instructions, the Daemon extracts and delivers them to agents on `task claim`.

**YAGNI Constraints Applied:**
- No `context_files` (agents have `ls`/`grep`)
- No Markdown parsing beyond regex JSON extraction
- Fire-and-forget ACP (no retry queues, no reconnect logic)

---

## Task Overview

| Task | Description | Effort |
|------|-------------|--------|
| 1 | Add `instructions`, `role` to Task model | simple |
| 2 | Create plan.py (regex: `r"```(?:json)?\s*(\{.*?\})\s*```"` + DAG validation) | simple |
| 3 | Create ACP emitter (queue-based non-blocking dispatch) | simple |
| 4 | Wire client commands + daemon handler | standard |

**Execution:** Sequential (1 → 2 → 3 → 4)

---

### Task 1: The "Empty Shell" Model

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/state.py:29-56`
- Test: `tests/harness/test_state.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_state.py after test_task_claimed_by_field

   def test_task_instructions_field():
       """Task should have instructions field for orchestrator injection."""
       task = Task(
           id="task-1",
           description="Test task",
           status=TaskStatus.PENDING,
           dependencies=[],
           instructions="Step 1: Read the file. Step 2: Modify the function.",
       )
       assert task.instructions == "Step 1: Read the file. Step 2: Modify the function."


   def test_task_role_field():
       """Task should have role field for agent specialization."""
       task = Task(
           id="task-1",
           description="Test task",
           status=TaskStatus.PENDING,
           dependencies=[],
           role="frontend",
       )
       assert task.role == "frontend"


   def test_task_injection_fields_default_to_none():
       """Injection fields should default to None for backwards compat."""
       task = Task(
           id="task-1",
           description="Test task",
           status=TaskStatus.PENDING,
           dependencies=[],
       )
       assert task.instructions is None
       assert task.role is None
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_state.py -v -k "instructions or role"
   ```
   Expected: FAIL (fields don't exist)

3. **Implement MINIMAL code:**
   ```python
   # Modify Task class in src/harness/state.py (add after timeout_seconds)

   class Task(BaseModel):
       """Individual task in workflow DAG."""

       id: str = Field(..., description="Unique task identifier")
       description: str = Field(..., description="Task description")
       status: TaskStatus = Field(..., description="Current task status")
       dependencies: list[str] = Field(..., description="Task IDs that must complete first")
       started_at: datetime | None = Field(None, description="Task start timestamp")
       completed_at: datetime | None = Field(None, description="Task completion timestamp")
       claimed_by: str | None = Field(None, description="Worker ID that claimed this task")
       timeout_seconds: int = Field(600, description="Timeout for task execution")
       # Orchestrator Injection (v2.5)
       instructions: str | None = Field(None, description="Detailed prompt for agent")
       role: str | None = Field(None, description="Agent role: frontend, backend, etc.")
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_state.py -v -k "instructions or role"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(state): add instructions and role fields to Task model"
   ```

---

### Task 2: The "Fuzzy" Compiler

**Effort:** simple (3-10 tool calls)

**Files:**
- Create: `src/harness/plan.py`
- Test: `tests/harness/test_plan.py`

**Mandatory Regex Pattern:**
```python
re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
```
- Handles `json` tag or no tag
- Captures only the JSON object `{...}`
- Ignores thinking tokens before/after the block

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_plan.py
   """Tests for plan JSON extraction and DAG validation."""

   import pytest

   from harness.plan import PlanDefinition, PlanTaskDefinition, parse_plan_content


   def test_plan_task_definition_basic():
       """PlanTaskDefinition should validate required fields."""
       task = PlanTaskDefinition(description="Implement feature X")
       assert task.description == "Implement feature X"
       assert task.dependencies == []
       assert task.instructions is None
       assert task.role is None


   def test_plan_task_definition_with_injection():
       """PlanTaskDefinition should accept injection fields."""
       task = PlanTaskDefinition(
           description="Build component",
           instructions="Use React hooks pattern",
           role="frontend",
       )
       assert task.instructions == "Use React hooks pattern"
       assert task.role == "frontend"


   def test_parse_plan_content_extracts_json():
       """parse_plan_content should extract JSON from markdown chatter."""
       content = '''
   Here is the implementation plan for your feature.

   I will break this down into tasks:

   ```json
   {
     "goal": "Implement auth",
     "tasks": {
       "task-1": {"description": "Add login endpoint"},
       "task-2": {"description": "Add logout", "dependencies": ["task-1"]}
     }
   }
   ```

   Let me know if you want changes.
   '''
       plan = parse_plan_content(content)
       assert plan.goal == "Implement auth"
       assert len(plan.tasks) == 2
       assert plan.tasks["task-2"].dependencies == ["task-1"]


   def test_parse_plan_content_no_json_raises():
       """parse_plan_content should raise if no JSON block found."""
       content = "Here is the plan: do task 1, then task 2."
       with pytest.raises(ValueError, match="[Nn]o JSON"):
           parse_plan_content(content)


   def test_parse_plan_content_invalid_json_raises():
       """parse_plan_content should raise for malformed JSON."""
       content = '''
   ```json
   {goal: "broken}
   ```
   '''
       with pytest.raises(ValueError, match="[Ii]nvalid JSON"):
           parse_plan_content(content)


   def test_plan_validate_dag_rejects_cycle():
       """validate_dag should reject A -> B -> A cycles."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "a": PlanTaskDefinition(description="A", dependencies=["b"]),
               "b": PlanTaskDefinition(description="B", dependencies=["a"]),
           },
       )
       with pytest.raises(ValueError, match="[Cc]ycle"):
           plan.validate_dag()


   def test_plan_validate_dag_rejects_missing_dep():
       """validate_dag should reject references to non-existent tasks."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "a": PlanTaskDefinition(description="A", dependencies=["ghost"]),
           },
       )
       with pytest.raises(ValueError, match="[Mm]issing"):
           plan.validate_dag()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_plan.py -v
   ```
   Expected: FAIL (module doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # src/harness/plan.py
   """
   Plan extraction from LLM output.

   The Orchestrator emits markdown with thinking tokens, followed by a JSON block.
   We extract the JSON, validate the DAG, and convert to WorkflowState.
   """

   import json
   import re

   from pydantic import BaseModel, Field

   from .state import Task, TaskStatus, WorkflowState


   class PlanTaskDefinition(BaseModel):
       """User-facing task definition."""

       description: str
       dependencies: list[str] = Field(default_factory=list)
       timeout_seconds: int = 600
       instructions: str | None = None
       role: str | None = None


   class PlanDefinition(BaseModel):
       """User-facing plan with goal and tasks."""

       goal: str
       tasks: dict[str, PlanTaskDefinition]

       def validate_dag(self) -> None:
           """Reject cycles and missing dependencies."""
           # Check deps exist
           for task_id, task in self.tasks.items():
               for dep in task.dependencies:
                   if dep not in self.tasks:
                       raise ValueError(f"Missing dependency: {dep} (in {task_id})")

           # DFS cycle detection
           visited: set[str] = set()
           path: set[str] = set()

           def visit(node: str) -> None:
               if node in path:
                   raise ValueError(f"Cycle detected at {node}")
               if node in visited:
                   return
               visited.add(node)
               path.add(node)
               for dep in self.tasks[node].dependencies:
                   visit(dep)
               path.remove(node)

           for task_id in self.tasks:
               visit(task_id)

       def to_workflow_state(self) -> WorkflowState:
           """Convert to internal WorkflowState."""
           tasks = {
               tid: Task(
                   id=tid,
                   description=t.description,
                   status=TaskStatus.PENDING,
                   dependencies=t.dependencies,
                   timeout_seconds=t.timeout_seconds,
                   instructions=t.instructions,
                   role=t.role,
               )
               for tid, t in self.tasks.items()
           }
           return WorkflowState(tasks=tasks)


   def parse_plan_content(content: str) -> PlanDefinition:
       """Extract JSON plan from LLM output.

       Finds the first ```json block, parses it, validates the DAG.
       Everything outside the JSON block is ignored (thinking tokens, markdown).
       """
       # Forgiving wrapper: handles ```json or plain ```
       # Strict on payload: must be valid JSON object
       match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
       if not match:
           raise ValueError("No JSON plan block found")

       try:
           data = json.loads(match.group(1))
       except json.JSONDecodeError as e:
           raise ValueError(f"Invalid JSON: {e}") from e

       plan = PlanDefinition(**data)
       plan.validate_dag()
       return plan
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_plan.py -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): add JSON extractor with DAG validation"
   ```

---

### Task 3: The ACP Emitter

**Effort:** simple (3-10 tool calls)

**Files:**
- Create: `src/harness/acp.py`
- Test: `tests/harness/test_acp.py`

**Non-Blocking Contract (MANDATORY):**
- `emit()` MUST be strictly non-blocking (< 1ms)
- Use `queue.Queue` with background `threading.Thread` for dispatch
- Daemon handlers call `emit()` which pushes to queue and returns immediately
- Background thread drains queue and sends over socket
- If connection fails, log once to stderr and disable (no retries, no crashes)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_acp.py
   """Tests for ACP fire-and-forget emitter."""

   import json
   import socket
   import threading
   import time

   import pytest

   from harness.acp import ACPEmitter


   @pytest.fixture
   def mock_server():
       """Ephemeral TCP server to receive ACP messages."""
       received: list[str] = []
       server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
       server.bind(("127.0.0.1", 0))
       server.listen(1)
       port = server.getsockname()[1]

       def accept():
           try:
               conn, _ = server.accept()
               conn.settimeout(1.0)
               while True:
                   data = conn.recv(4096)
                   if not data:
                       break
                   received.append(data.decode())
               conn.close()
           except (OSError, socket.timeout):
               pass

       t = threading.Thread(target=accept, daemon=True)
       t.start()
       yield {"port": port, "received": received}
       server.close()


   def test_emitter_sends_json(mock_server):
       """ACPEmitter should send JSON lines."""
       emitter = ACPEmitter(port=mock_server["port"])
       emitter.emit({"event": "test", "data": 123})
       time.sleep(0.1)
       emitter.close()

       assert len(mock_server["received"]) == 1
       msg = json.loads(mock_server["received"][0].strip())
       assert msg["event"] == "test"


   def test_emitter_graceful_on_no_server():
       """ACPEmitter should not crash if server unavailable."""
       emitter = ACPEmitter(port=59999)  # Nothing listening
       # Should not raise
       emitter.emit({"event": "test"})
       emitter.close()


   def test_emitter_logs_once_on_failure(capsys):
       """ACPEmitter should log connection failure once, not spam."""
       emitter = ACPEmitter(port=59999)
       emitter.emit({"event": "1"})
       emitter.emit({"event": "2"})
       emitter.emit({"event": "3"})
       emitter.close()

       captured = capsys.readouterr()
       # Should only see one warning, not three
       assert captured.err.count("ACP") <= 1
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_acp.py -v
   ```
   Expected: FAIL (module doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # src/harness/acp.py
   """
   ACP Emitter - Queue-based non-blocking telemetry to Claude Code.

   Design:
   - emit() pushes to queue and returns immediately (< 1ms)
   - Background thread drains queue and sends over socket
   - If connect fails, log once to stderr and disable
   - Daemon never stalls on socket operations
   """

   import json
   import queue
   import socket
   import sys
   import threading
   from typing import Any


   class ACPEmitter:
       """Queue-based non-blocking telemetry emitter."""

       def __init__(self, host: str = "127.0.0.1", port: int = 9100) -> None:
           self._host = host
           self._port = port
           self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
           self._disabled = False
           self._warned = False
           self._thread = threading.Thread(target=self._worker, daemon=True)
           self._thread.start()

       def emit(self, entry: dict[str, Any]) -> None:
           """Push to queue and return immediately. Strictly non-blocking."""
           if not self._disabled:
               self._queue.put_nowait(entry)

       def _worker(self) -> None:
           """Background thread: drain queue, send over socket."""
           sock: socket.socket | None = None
           while True:
               entry = self._queue.get()
               if entry is None:  # Shutdown signal
                   break
               if self._disabled:
                   continue

               # Lazy connect
               if sock is None:
                   try:
                       sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                       sock.settimeout(2.0)
                       sock.connect((self._host, self._port))
                   except OSError:
                       self._disabled = True
                       sock = None
                       if not self._warned:
                           print(f"ACP: Claude Code not available on port {self._port}", file=sys.stderr)
                           self._warned = True
                       continue

               # Send
               try:
                   msg = json.dumps(entry) + "\n"
                   sock.sendall(msg.encode())
               except OSError:
                   self._disabled = True
                   try:
                       sock.close()
                   except OSError:
                       pass
                   sock = None

           # Cleanup on shutdown
           if sock:
               try:
                   sock.close()
               except OSError:
                   pass

       def close(self) -> None:
           """Clean shutdown - signal worker thread to exit."""
           self._queue.put(None)
           self._thread.join(timeout=1.0)
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_acp.py -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(acp): add fire-and-forget telemetry emitter"
   ```

---

### Task 4: The Wiring

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/client.py` (add plan commands)
- Modify: `src/harness/daemon.py` (add handler + ACP integration)
- Test: `tests/harness/test_daemon.py`, `tests/harness/test_integration.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_daemon.py

   def test_plan_import_handler(daemon_manager):
       """plan_import should parse JSON and seed state."""
       daemon, _ = daemon_manager
       from tests.harness.conftest import send_command

       content = '''
   Here is the plan:

   ```json
   {
     "goal": "Test",
     "tasks": {
       "task-1": {"description": "First"},
       "task-2": {"description": "Second", "dependencies": ["task-1"]}
     }
   }
   ```
   '''
       resp = send_command(daemon.socket_path, {"command": "plan_import", "content": content})
       assert resp["status"] == "ok"
       assert resp["data"]["task_count"] == 2

       # Verify state
       state = send_command(daemon.socket_path, {"command": "get_state"})
       assert "task-1" in state["data"]["tasks"]


   def test_plan_import_preserves_injection(daemon_manager):
       """plan_import should preserve instructions and role."""
       daemon, _ = daemon_manager
       from tests.harness.conftest import send_command

       content = '''
   ```json
   {
     "goal": "Test",
     "tasks": {
       "task-1": {
         "description": "Do it",
         "instructions": "Step by step guide here",
         "role": "backend"
       }
     }
   }
   ```
   '''
       send_command(daemon.socket_path, {"command": "plan_import", "content": content})
       state = send_command(daemon.socket_path, {"command": "get_state"})
       task = state["data"]["tasks"]["task-1"]
       assert task["instructions"] == "Step by step guide here"
       assert task["role"] == "backend"


   def test_plan_import_rejects_cycle(daemon_manager):
       """plan_import should reject cyclic dependencies."""
       daemon, _ = daemon_manager
       from tests.harness.conftest import send_command

       content = '''
   ```json
   {
     "goal": "Bad",
     "tasks": {
       "a": {"description": "A", "dependencies": ["b"]},
       "b": {"description": "B", "dependencies": ["a"]}
     }
   }
   ```
   '''
       resp = send_command(daemon.socket_path, {"command": "plan_import", "content": content})
       assert resp["status"] == "error"
       assert "cycle" in resp["message"].lower()
   ```

   ```python
   # Add to tests/harness/test_integration.py

   def test_plan_import_then_claim_with_injection(socket_path, worktree):
       """Full flow: import plan -> claim -> verify injection fields."""
       import json
       import os
       import subprocess
       import sys

       from harness.client import spawn_daemon
       from tests.harness.conftest import cleanup_daemon_subprocess

       spawn_daemon(str(worktree), socket_path)

       try:
           plan_file = worktree / "plan.md"
           plan_file.write_text('''
   ```json
   {
     "goal": "E2E Test",
     "tasks": {
       "task-1": {
         "description": "First task",
         "instructions": "Do this carefully",
         "role": "backend"
       },
       "task-2": {
         "description": "Second task",
         "dependencies": ["task-1"]
       }
     }
   }
   ```
   ''')

           env = {**os.environ, "HARNESS_SOCKET": socket_path, "HARNESS_WORKTREE": str(worktree)}

           # Import
           r = subprocess.run(
               [sys.executable, "-m", "harness.client", "plan", "import", "--file", str(plan_file)],
               capture_output=True, text=True, env=env,
           )
           assert r.returncode == 0, r.stderr

           # Claim
           r = subprocess.run(
               [sys.executable, "-m", "harness.client", "task", "claim"],
               capture_output=True, text=True, env=env,
           )
           assert r.returncode == 0
           data = json.loads(r.stdout)
           assert data["task"]["instructions"] == "Do this carefully"
           assert data["task"]["role"] == "backend"

       finally:
           cleanup_daemon_subprocess(socket_path)
   ```

   ```python
   # Add to tests/harness/test_client.py

   def test_plan_import_file_not_found():
       """harness plan import should error on missing file."""
       import subprocess
       import sys

       r = subprocess.run(
           [sys.executable, "-m", "harness.client", "plan", "import", "--file", "/ghost.md"],
           capture_output=True, text=True,
       )
       assert r.returncode != 0
       assert "not found" in r.stderr.lower()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_daemon.py -v -k "plan_import"
   pytest tests/harness/test_client.py -v -k "plan_import"
   ```
   Expected: FAIL (command doesn't exist)

3. **Implement MINIMAL code:**

   **client.py additions:**
   ```python
   # Add to argparse section (after existing subparsers)
   plan_parser = subparsers.add_parser("plan", help="Plan management")
   plan_sub = plan_parser.add_subparsers(dest="plan_command", required=True)
   plan_import_parser = plan_sub.add_parser("import", help="Import plan from file")
   plan_import_parser.add_argument("--file", required=True, help="Plan file path")

   # Add to command routing
   elif args.command == "plan":
       if args.plan_command == "import":
           _cmd_plan_import(socket_path, worktree_root, args.file)

   # Add handler
   def _cmd_plan_import(socket_path: str, worktree_root: str, file_path: str) -> None:
       """Import plan from file."""
       path = Path(file_path)
       if not path.exists():
           print(f"Error: File not found: {file_path}", file=sys.stderr)
           sys.exit(1)

       content = path.read_text()
       response = send_rpc(
           socket_path,
           {"command": "plan_import", "content": content},
           worktree_root,
       )
       if response["status"] != "ok":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)
       print(f"Plan imported ({response['data']['task_count']} tasks)")
   ```

   **daemon.py additions:**
   ```python
   # Add imports
   from .acp import ACPEmitter
   from .plan import parse_plan_content

   # Add to handlers dict
   "plan_import": self._handle_plan_import,

   # Add handler method
   def _handle_plan_import(self, request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
       """Import plan from LLM output."""
       content = request.get("content")
       if not content:
           return {"status": "error", "message": "content required"}

       try:
           plan = parse_plan_content(content)
           state = plan.to_workflow_state()
           server.state_manager.save(state)

           server.trajectory_logger.log({
               "event_type": "plan_import",
               "goal": plan.goal,
               "task_count": len(plan.tasks),
           })
           server.acp_emitter.emit({
               "event_type": "plan_import",
               "goal": plan.goal,
               "task_count": len(plan.tasks),
           })

           return {"status": "ok", "data": {"goal": plan.goal, "task_count": len(plan.tasks)}}
       except ValueError as e:
           return {"status": "error", "message": str(e)}

   # Add to HarnessDaemon.__init__ (after trajectory_logger)
   self.acp_emitter = ACPEmitter()

   # Add ACP emit calls to existing handlers:
   # In _handle_task_claim, after trajectory_logger.log():
   server.acp_emitter.emit({"event_type": "task_claim", "task_id": task.id, "worker_id": worker_id})

   # In _handle_task_complete:
   server.acp_emitter.emit({"event_type": "task_complete", "task_id": task_id})
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/ -v -k "plan_import"
   pytest tests/harness/test_integration.py -v -k "injection"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat: wire plan import command with ACP telemetry"
   ```

---

## Validation Checklist

- [ ] `Task` model has `instructions` and `role` fields (both optional)
- [ ] `parse_plan_content` extracts JSON from markdown with regex
- [ ] DAG validation rejects cycles and missing deps
- [ ] `ACPEmitter` logs once on connection failure, then silences
- [ ] `harness plan import --file x.md` imports plan to daemon
- [ ] `harness task claim` returns task with injection fields
- [ ] All existing tests pass
- [ ] No `context_files` anywhere (YAGNI)
- [ ] No Pydantic in client.py

---

## Architecture

```
Orchestrator (Claude)
        │
        │ "Here is the plan: ```json {...} ```"
        ▼
┌───────────────────┐
│ harness plan      │
│ import --file x   │
└────────┬──────────┘
         │ content
         ▼
┌───────────────────────────────────────┐
│            Daemon                      │
│  ┌─────────────┐   ┌────────────────┐ │
│  │ plan.py     │   │ ACPEmitter     │ │
│  │ (regex JSON)│   │ (fire-forget)  │ │
│  └──────┬──────┘   └───────┬────────┘ │
│         │                   │          │
│         ▼                   ▼          │
│  ┌─────────────┐   ┌────────────────┐ │
│  │ StateManager│   │ Claude Code    │ │
│  │ (JSON DAG)  │   │ :9100          │ │
│  └─────────────┘   └────────────────┘ │
└───────────────────────────────────────┘
         │
         │ harness task claim
         ▼
┌───────────────────┐
│ Agent             │
│ (gets: id, desc,  │
│  instructions,    │
│  role)            │
└───────────────────┘
```
