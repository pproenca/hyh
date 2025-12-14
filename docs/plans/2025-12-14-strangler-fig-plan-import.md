# Harness v2.5: The "Connected" Kernel

**Goal:** Upgrade Harness from a "Passive Database" to an "Active Relay" - enabling plan ingestion from LLM-generated Markdown with "Orchestrator Injection" support, plus real-time telemetry streaming via ACP (Agent Communication Protocol).

**Architecture:**
1. **"Empty Shell" Agent** - Task model gains `instructions`, `role`, `context_files` fields for orchestrator injection
2. **ACP Side-Channel** - Synchronous WebSocket client streams trajectory events to Claude Code (`ws://localhost:9100`)
3. **Plan as Compiler** - Markdown parser extracts JSON plans, validates DAG, seeds daemon state

---

## Task Overview

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent: state.py Task enhancement vs plan.py models |
| Group 2 | 3 | DAG validation (depends on models) |
| Group 3 | 4, 5 | Independent: Markdown parser vs WorkflowState conversion |
| Group 4 | 6 | ACP module (independent) |
| Group 5 | 7, 8 | Client commands: template + import |
| Group 6 | 9, 10 | Daemon handlers: plan_import + ACP integration |
| Group 7 | 11 | Integration test |
| Group 8 | 12 | Code review |

---

### Task 1: Enhanced Task Model - Orchestrator Injection Fields

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


   def test_task_context_files_field():
       """Task should have context_files field for file preloading."""
       task = Task(
           id="task-1",
           description="Test task",
           status=TaskStatus.PENDING,
           dependencies=[],
           context_files=["src/main.py", "tests/test_main.py"],
       )
       assert task.context_files == ["src/main.py", "tests/test_main.py"]


   def test_task_injection_fields_default_to_none_or_empty():
       """Injection fields should default to None/empty for backwards compat."""
       task = Task(
           id="task-1",
           description="Test task",
           status=TaskStatus.PENDING,
           dependencies=[],
       )
       assert task.instructions is None
       assert task.role is None
       assert task.context_files == []
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_state.py -v -k "instructions or role or context_files"
   ```
   Expected: FAIL (fields don't exist)

3. **Implement MINIMAL code:**
   ```python
   # Modify Task class in src/harness/state.py (add after timeout_seconds field)

   class Task(BaseModel):
       """Individual task in workflow DAG."""

       id: str = Field(..., description="Unique task identifier")
       description: str = Field(..., description="Task description")
       status: TaskStatus = Field(..., description="Current task status")
       dependencies: list[str] = Field(..., description="List of task IDs that must complete first")
       started_at: datetime | None = Field(None, description="Task start timestamp")
       completed_at: datetime | None = Field(None, description="Task completion timestamp")
       claimed_by: str | None = Field(None, description="Worker ID that claimed this task")
       timeout_seconds: int = Field(600, description="Timeout for task execution")
       # Orchestrator Injection fields (v2.5)
       instructions: str | None = Field(None, description="Detailed prompt for the agent")
       role: str | None = Field(None, description="Agent role: frontend, security, etc.")
       context_files: list[str] = Field(default_factory=list, description="Files to preload")
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_state.py -v -k "instructions or role or context_files"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(state): add orchestrator injection fields to Task model"
   ```

---

### Task 2: Plan Module - Pydantic Models with Injection Fields

**Effort:** simple (3-10 tool calls)

**Files:**
- Create: `src/harness/plan.py`
- Test: `tests/harness/test_plan.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_plan.py
   """Tests for plan parsing and conversion."""

   import pytest

   from harness.plan import PlanDefinition, PlanTaskDefinition


   def test_plan_task_definition_basic():
       """PlanTaskDefinition should validate required fields."""
       task = PlanTaskDefinition(description="Implement feature X")
       assert task.description == "Implement feature X"
       assert task.dependencies == []
       assert task.timeout_seconds == 600
       assert task.instructions is None
       assert task.role is None
       assert task.context_files == []


   def test_plan_task_definition_with_injection_fields():
       """PlanTaskDefinition should accept orchestrator injection fields."""
       task = PlanTaskDefinition(
           description="Build frontend component",
           dependencies=["task-1"],
           instructions="Use React hooks. Follow existing patterns in src/components/.",
           role="frontend",
           context_files=["src/components/Button.tsx"],
       )
       assert task.instructions == "Use React hooks. Follow existing patterns in src/components/."
       assert task.role == "frontend"
       assert task.context_files == ["src/components/Button.tsx"]


   def test_plan_definition_basic():
       """PlanDefinition should validate goal and tasks dict."""
       plan = PlanDefinition(
           goal="Implement auth system",
           tasks={
               "task-1": PlanTaskDefinition(description="Add login"),
               "task-2": PlanTaskDefinition(
                   description="Add logout",
                   dependencies=["task-1"],
               ),
           },
       )
       assert plan.goal == "Implement auth system"
       assert len(plan.tasks) == 2
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_plan.py -v
   ```
   Expected: FAIL (ImportError - module doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # src/harness/plan.py
   """
   Plan parsing and conversion for LLM-generated Markdown plans.

   Converts "fuzzy" Markdown/JSON plans to strict WorkflowState DAG.
   Supports "Orchestrator Injection" via instructions, role, and context_files.
   """

   from pydantic import BaseModel, Field


   class PlanTaskDefinition(BaseModel):
       """User-facing task definition with orchestrator injection support."""

       description: str = Field(..., description="Task description")
       dependencies: list[str] = Field(default_factory=list, description="Task IDs this depends on")
       timeout_seconds: int = Field(600, description="Timeout for task execution")
       # Orchestrator Injection fields
       instructions: str | None = Field(None, description="Detailed prompt for the agent")
       role: str | None = Field(None, description="Agent role: frontend, security, etc.")
       context_files: list[str] = Field(default_factory=list, description="Files to preload")


   class PlanDefinition(BaseModel):
       """User-facing plan definition with goal and tasks."""

       goal: str = Field(..., description="Overall goal of the plan")
       tasks: dict[str, PlanTaskDefinition] = Field(..., description="Task ID -> definition mapping")
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_plan.py -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): add PlanDefinition with orchestrator injection fields"
   ```

---

### Task 3: Plan Module - DAG Validation

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/plan.py:25-50`
- Test: `tests/harness/test_plan.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_plan.py

   def test_plan_definition_validate_dag_no_cycle():
       """validate_dag should pass for valid DAG."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "task-1": PlanTaskDefinition(description="First"),
               "task-2": PlanTaskDefinition(description="Second", dependencies=["task-1"]),
               "task-3": PlanTaskDefinition(description="Third", dependencies=["task-1", "task-2"]),
           },
       )
       # Should not raise
       plan.validate_dag()


   def test_plan_definition_validate_dag_detects_cycle():
       """validate_dag should raise ValueError for A -> B -> A cycle."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "task-a": PlanTaskDefinition(description="A", dependencies=["task-b"]),
               "task-b": PlanTaskDefinition(description="B", dependencies=["task-a"]),
           },
       )
       with pytest.raises(ValueError, match="[Cc]ycle"):
           plan.validate_dag()


   def test_plan_definition_validate_dag_missing_dependency():
       """validate_dag should raise ValueError for missing dependency."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "task-1": PlanTaskDefinition(description="First", dependencies=["nonexistent"]),
           },
       )
       with pytest.raises(ValueError, match="[Mm]issing dependency"):
           plan.validate_dag()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_plan.py -v -k "validate_dag"
   ```
   Expected: FAIL (AttributeError - validate_dag doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # Add to PlanDefinition class in src/harness/plan.py

   def validate_dag(self) -> None:
       """Ensure no circular dependencies and all deps exist.

       Raises:
           ValueError: If a cycle is detected or dependency is missing.
       """
       # Check all dependencies exist
       for task_id, task in self.tasks.items():
           for dep in task.dependencies:
               if dep not in self.tasks:
                   raise ValueError(f"Missing dependency: {dep} (referenced by {task_id})")

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
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_plan.py -v -k "validate_dag"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): add DAG validation with cycle and missing dep detection"
   ```

---

### Task 4: Plan Module - Markdown Parser

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/plan.py:50-90`
- Test: `tests/harness/test_plan.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_plan.py

   def test_parse_markdown_extracts_json_block():
       """parse_markdown_to_plan should extract JSON from Markdown."""
       content = '''
   # My Plan

   Some description text.

   ```json
   {
     "goal": "Implement feature",
     "tasks": {
       "task-1": {"description": "First task"},
       "task-2": {"description": "Second task", "dependencies": ["task-1"]}
     }
   }
   ```

   More text after.
   '''
       from harness.plan import parse_markdown_to_plan

       plan = parse_markdown_to_plan(content)
       assert plan.goal == "Implement feature"
       assert len(plan.tasks) == 2
       assert plan.tasks["task-2"].dependencies == ["task-1"]


   def test_parse_markdown_with_injection_fields():
       """parse_markdown_to_plan should parse orchestrator injection fields."""
       content = '''
   ```json
   {
     "goal": "Build UI",
     "tasks": {
       "task-1": {
         "description": "Create component",
         "instructions": "Use React hooks pattern",
         "role": "frontend",
         "context_files": ["src/App.tsx"]
       }
     }
   }
   ```
   '''
       from harness.plan import parse_markdown_to_plan

       plan = parse_markdown_to_plan(content)
       assert plan.tasks["task-1"].instructions == "Use React hooks pattern"
       assert plan.tasks["task-1"].role == "frontend"
       assert plan.tasks["task-1"].context_files == ["src/App.tsx"]


   def test_parse_markdown_no_json_block_raises():
       """parse_markdown_to_plan should raise ValueError if no JSON block."""
       content = "# Plan\n\nNo JSON here."
       from harness.plan import parse_markdown_to_plan

       with pytest.raises(ValueError, match="[Nn]o JSON"):
           parse_markdown_to_plan(content)


   def test_parse_markdown_invalid_json_raises():
       """parse_markdown_to_plan should raise ValueError for invalid JSON."""
       content = '''
   ```json
   {invalid json}
   ```
   '''
       from harness.plan import parse_markdown_to_plan

       with pytest.raises(ValueError, match="[Ii]nvalid JSON"):
           parse_markdown_to_plan(content)


   def test_parse_markdown_validates_dag():
       """parse_markdown_to_plan should validate DAG after parsing."""
       content = '''
   ```json
   {
     "goal": "Test",
     "tasks": {
       "task-a": {"description": "A", "dependencies": ["task-b"]},
       "task-b": {"description": "B", "dependencies": ["task-a"]}
     }
   }
   ```
   '''
       from harness.plan import parse_markdown_to_plan

       with pytest.raises(ValueError, match="[Cc]ycle"):
           parse_markdown_to_plan(content)
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_plan.py -v -k "parse_markdown"
   ```
   Expected: FAIL (ImportError - parse_markdown_to_plan doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # Add imports at top of src/harness/plan.py
   import json
   import re

   # Add function after PlanDefinition class
   def parse_markdown_to_plan(content: str) -> PlanDefinition:
       """Extract JSON plan block from Markdown content.

       Args:
           content: Markdown content with embedded ```json block.

       Returns:
           Validated PlanDefinition.

       Raises:
           ValueError: If no JSON block found, JSON is invalid, or DAG has cycle.
       """
       # Look for ```json ... ``` block
       match = re.search(r"```json\s*\n(.*?)\n\s*```", content, re.DOTALL)
       if not match:
           raise ValueError("No JSON plan block found in content")

       json_str = match.group(1)
       try:
           data = json.loads(json_str)
       except json.JSONDecodeError as e:
           raise ValueError(f"Invalid JSON in plan block: {e}") from e

       try:
           plan = PlanDefinition(**data)
       except Exception as e:
           raise ValueError(f"Invalid plan schema: {e}") from e

       plan.validate_dag()
       return plan
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_plan.py -v -k "parse_markdown"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): add Markdown parser for JSON plan blocks"
   ```

---

### Task 5: Plan Module - Convert to WorkflowState

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/plan.py:90-120`
- Test: `tests/harness/test_plan.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_plan.py
   from harness.state import TaskStatus, WorkflowState


   def test_plan_to_workflow_state_converts_all_tasks():
       """to_workflow_state should convert all tasks to internal Task model."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "task-1": PlanTaskDefinition(description="First"),
               "task-2": PlanTaskDefinition(
                   description="Second",
                   dependencies=["task-1"],
                   timeout_seconds=1200,
               ),
           },
       )
       state = plan.to_workflow_state()

       assert isinstance(state, WorkflowState)
       assert len(state.tasks) == 2
       assert state.tasks["task-1"].id == "task-1"
       assert state.tasks["task-1"].description == "First"
       assert state.tasks["task-1"].status == TaskStatus.PENDING
       assert state.tasks["task-1"].dependencies == []
       assert state.tasks["task-2"].dependencies == ["task-1"]
       assert state.tasks["task-2"].timeout_seconds == 1200


   def test_plan_to_workflow_state_preserves_injection_fields():
       """to_workflow_state should preserve orchestrator injection fields."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "task-1": PlanTaskDefinition(
                   description="Frontend task",
                   instructions="Use hooks pattern",
                   role="frontend",
                   context_files=["src/App.tsx"],
               ),
           },
       )
       state = plan.to_workflow_state()

       assert state.tasks["task-1"].instructions == "Use hooks pattern"
       assert state.tasks["task-1"].role == "frontend"
       assert state.tasks["task-1"].context_files == ["src/App.tsx"]


   def test_plan_to_workflow_state_sets_pending_status():
       """to_workflow_state should set all tasks to PENDING status."""
       plan = PlanDefinition(
           goal="Test",
           tasks={
               "task-1": PlanTaskDefinition(description="First"),
           },
       )
       state = plan.to_workflow_state()

       assert state.tasks["task-1"].status == TaskStatus.PENDING
       assert state.tasks["task-1"].started_at is None
       assert state.tasks["task-1"].completed_at is None
       assert state.tasks["task-1"].claimed_by is None
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_plan.py -v -k "to_workflow_state"
   ```
   Expected: FAIL (AttributeError - to_workflow_state doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # Add import at top of src/harness/plan.py
   from .state import Task, TaskStatus, WorkflowState

   # Add method to PlanDefinition class
   def to_workflow_state(self) -> WorkflowState:
       """Convert plan to internal WorkflowState for execution.

       Returns:
           WorkflowState with all tasks in PENDING status.
       """
       tasks: dict[str, Task] = {}
       for task_id, task_def in self.tasks.items():
           tasks[task_id] = Task(
               id=task_id,
               description=task_def.description,
               status=TaskStatus.PENDING,
               dependencies=task_def.dependencies,
               timeout_seconds=task_def.timeout_seconds,
               instructions=task_def.instructions,
               role=task_def.role,
               context_files=task_def.context_files,
           )
       return WorkflowState(tasks=tasks)
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_plan.py -v -k "to_workflow_state"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): add to_workflow_state with injection field support"
   ```

---

### Task 6: ACP Relay Module

**Effort:** standard (10-15 tool calls)

**Files:**
- Create: `src/harness/acp.py`
- Test: `tests/harness/test_acp.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # tests/harness/test_acp.py
   """Tests for ACP (Agent Communication Protocol) relay module."""

   import json
   import socket
   import threading
   import time

   import pytest

   from harness.acp import ACPClient


   @pytest.fixture
   def mock_acp_server():
       """Create a mock WebSocket-like server for testing."""
       messages_received = []
       server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
       server_socket.bind(("127.0.0.1", 0))  # Random port
       server_socket.listen(1)
       port = server_socket.getsockname()[1]

       def accept_connections():
           try:
               conn, _ = server_socket.accept()
               conn.settimeout(2.0)
               while True:
                   try:
                       data = conn.recv(4096)
                       if not data:
                           break
                       messages_received.append(data.decode())
                   except socket.timeout:
                       break
               conn.close()
           except OSError:
               pass

       thread = threading.Thread(target=accept_connections, daemon=True)
       thread.start()

       yield {"port": port, "messages": messages_received}

       server_socket.close()


   def test_acp_client_connects(mock_acp_server):
       """ACPClient should connect to specified port."""
       client = ACPClient(port=mock_acp_server["port"])
       assert client.connect() is True
       client.close()


   def test_acp_client_send_log(mock_acp_server):
       """ACPClient should send log entries as JSON."""
       client = ACPClient(port=mock_acp_server["port"])
       client.connect()

       log_entry = {"event_type": "task_claim", "task_id": "task-1"}
       client.send_log(log_entry)
       time.sleep(0.1)  # Allow message to be received

       client.close()

       assert len(mock_acp_server["messages"]) >= 1
       # Verify JSON format
       received = json.loads(mock_acp_server["messages"][0].strip())
       assert received["event_type"] == "task_claim"


   def test_acp_client_graceful_failure():
       """ACPClient should handle connection failure gracefully."""
       client = ACPClient(port=59999)  # Unlikely to be in use
       # Should not raise, just return False
       assert client.connect() is False
       # send_log should be no-op when not connected
       client.send_log({"event": "test"})  # Should not raise


   def test_acp_client_reconnect_on_failure(mock_acp_server):
       """ACPClient should attempt reconnect on send failure."""
       client = ACPClient(port=mock_acp_server["port"], reconnect=True)
       client.connect()
       client.close()  # Simulate disconnect

       # Should attempt reconnect on next send (may fail, but shouldn't crash)
       client.send_log({"event": "test"})
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_acp.py -v
   ```
   Expected: FAIL (ImportError - module doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # src/harness/acp.py
   """
   ACP (Agent Communication Protocol) Relay for Claude Code connectivity.

   Provides real-time telemetry streaming via synchronous WebSocket-like
   connection. Uses plain sockets for simplicity (no websocket-client dep).

   Thread-safe: Can be called from multiple daemon handler threads.
   """

   import json
   import socket
   import threading
   from typing import Any


   class ACPClient:
       """Synchronous client for ACP telemetry relay.

       Connects to Claude Code's local port for real-time event streaming.
       Designed for use in threaded daemon environment (no asyncio).
       """

       def __init__(
           self,
           host: str = "127.0.0.1",
           port: int = 9100,
           reconnect: bool = True,
       ) -> None:
           self.host = host
           self.port = port
           self.reconnect = reconnect
           self._socket: socket.socket | None = None
           self._lock = threading.Lock()
           self._connected = False

       def connect(self) -> bool:
           """Establish connection to ACP endpoint.

           Returns:
               True if connected, False on failure.
           """
           with self._lock:
               if self._connected:
                   return True

               try:
                   self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                   self._socket.settimeout(5.0)
                   self._socket.connect((self.host, self.port))
                   self._connected = True
                   return True
               except (OSError, socket.error):
                   self._socket = None
                   self._connected = False
                   return False

       def send_log(self, entry: dict[str, Any]) -> bool:
           """Send log entry to ACP endpoint.

           Thread-safe. Attempts reconnect on failure if enabled.

           Args:
               entry: Log entry dict to send as JSON.

           Returns:
               True if sent successfully, False otherwise.
           """
           with self._lock:
               if not self._connected:
                   if self.reconnect:
                       # Release lock during reconnect attempt
                       pass
                   else:
                       return False

           # Attempt reconnect outside lock if needed
           if not self._connected and self.reconnect:
               self.connect()

           with self._lock:
               if not self._connected or not self._socket:
                   return False

               try:
                   message = json.dumps(entry) + "\n"
                   self._socket.sendall(message.encode())
                   return True
               except (OSError, socket.error):
                   self._connected = False
                   self._socket = None
                   return False

       def close(self) -> None:
           """Close connection."""
           with self._lock:
               if self._socket:
                   try:
                       self._socket.close()
                   except OSError:
                       pass
                   self._socket = None
               self._connected = False

       @property
       def is_connected(self) -> bool:
           """Check if currently connected."""
           with self._lock:
               return self._connected
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_acp.py -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(acp): add ACP relay client for Claude Code telemetry"
   ```

---

### Task 7: Client Command - Plan Template

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/client.py:350-380`
- Test: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_client.py
   import json
   import subprocess
   import sys


   def test_plan_template_outputs_json_schema():
       """harness plan template should output valid JSON schema example."""
       result = subprocess.run(
           [sys.executable, "-m", "harness.client", "plan", "template"],
           capture_output=True,
           text=True,
       )
       assert result.returncode == 0
       # Should be valid JSON
       data = json.loads(result.stdout)
       assert "goal" in data
       assert "tasks" in data
       # Should have example tasks
       assert len(data["tasks"]) >= 1


   def test_plan_template_includes_injection_fields():
       """harness plan template should include orchestrator injection fields."""
       result = subprocess.run(
           [sys.executable, "-m", "harness.client", "plan", "template"],
           capture_output=True,
           text=True,
       )
       data = json.loads(result.stdout)
       # At least one task should show injection fields
       task = list(data["tasks"].values())[0]
       assert "instructions" in task
       assert "role" in task
       assert "context_files" in task
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_client.py -v -k "plan_template"
   ```
   Expected: FAIL (command doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # Add to src/harness/client.py in main() after existing subparsers (~line 350)

   # plan subcommand with template and import
   plan_parser = subparsers.add_parser("plan", help="Plan management commands")
   plan_subparsers = plan_parser.add_subparsers(dest="plan_command", required=True)
   plan_subparsers.add_parser("template", help="Output JSON schema template")

   # Add to command routing after existing elif blocks (~line 400)
   elif args.command == "plan":
       if args.plan_command == "template":
           _cmd_plan_template()

   # Add handler function at end of file
   def _cmd_plan_template() -> None:
       """Output JSON schema template for plan files."""
       template = {
           "goal": "Describe the overall goal here",
           "tasks": {
               "task-1": {
                   "description": "First task description",
                   "dependencies": [],
                   "timeout_seconds": 600,
                   "instructions": "Detailed instructions for the agent (optional)",
                   "role": "general",
                   "context_files": []
               },
               "task-2": {
                   "description": "Second task that depends on first",
                   "dependencies": ["task-1"],
                   "timeout_seconds": 600,
                   "instructions": None,
                   "role": None,
                   "context_files": ["src/relevant_file.py"]
               }
           }
       }
       print(json.dumps(template, indent=2))
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_client.py -v -k "plan_template"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): add 'harness plan template' with injection fields"
   ```

---

### Task 8: Client Command - Plan Import

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/client.py:355-390`
- Test: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_client.py

   def test_plan_import_file_not_found():
       """harness plan import should error if file doesn't exist."""
       result = subprocess.run(
           [sys.executable, "-m", "harness.client", "plan", "import", "--file", "/nonexistent.md"],
           capture_output=True,
           text=True,
       )
       assert result.returncode != 0
       assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_client.py::test_plan_import_file_not_found -v
   ```
   Expected: FAIL (command doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # Add to src/harness/client.py plan subparsers section
   plan_import = plan_subparsers.add_parser("import", help="Import plan from markdown file")
   plan_import.add_argument("--file", required=True, help="Path to plan markdown file")

   # Add to command routing in plan section
   elif args.plan_command == "import":
       _cmd_plan_import(socket_path, worktree_root, args.file)

   # Add handler function
   def _cmd_plan_import(socket_path: str, worktree_root: str, file_path: str) -> None:
       """Import plan from markdown file."""
       path = Path(file_path)
       if not path.exists():
           print(f"Error: File not found: {file_path}", file=sys.stderr)
           sys.exit(1)

       content = path.read_text()

       response = send_rpc(
           socket_path,
           {"command": "plan_import", "content": content, "file_path": file_path},
           worktree_root,
       )
       if response["status"] != "ok":
           print(f"Error: {response.get('message')}", file=sys.stderr)
           sys.exit(1)

       task_count = response["data"].get("task_count", 0)
       print(f"Plan imported successfully ({task_count} tasks)")
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_client.py::test_plan_import_file_not_found -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): add 'harness plan import' command"
   ```

---

### Task 9: Daemon Handler - Plan Import

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/daemon.py:30-35` (imports)
- Modify: `src/harness/daemon.py:67-80` (handlers dict)
- Modify: `src/harness/daemon.py:295-340` (new handler)
- Test: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_daemon.py

   def test_plan_import_handler_parses_and_saves(daemon_manager):
       """plan_import handler should parse markdown and save to state."""
       daemon, worktree = daemon_manager

       content = '''
   # Test Plan

   ```json
   {
     "goal": "Test import",
     "tasks": {
       "task-1": {"description": "First task"},
       "task-2": {"description": "Second task", "dependencies": ["task-1"]}
     }
   }
   ```
   '''

       from tests.harness.conftest import send_command

       response = send_command(
           daemon.socket_path,
           {"command": "plan_import", "content": content, "file_path": "test.md"},
       )

       assert response["status"] == "ok"
       assert response["data"]["task_count"] == 2

       # Verify state was saved
       state_response = send_command(daemon.socket_path, {"command": "get_state"})
       assert state_response["status"] == "ok"
       assert "task-1" in state_response["data"]["tasks"]
       assert "task-2" in state_response["data"]["tasks"]


   def test_plan_import_handler_preserves_injection_fields(daemon_manager):
       """plan_import handler should preserve orchestrator injection fields."""
       daemon, _ = daemon_manager

       content = '''
   ```json
   {
     "goal": "Test injection",
     "tasks": {
       "task-1": {
         "description": "Frontend task",
         "instructions": "Use React hooks",
         "role": "frontend",
         "context_files": ["src/App.tsx"]
       }
     }
   }
   ```
   '''

       from tests.harness.conftest import send_command

       response = send_command(
           daemon.socket_path,
           {"command": "plan_import", "content": content, "file_path": "test.md"},
       )
       assert response["status"] == "ok"

       # Verify injection fields in state
       state_response = send_command(daemon.socket_path, {"command": "get_state"})
       task = state_response["data"]["tasks"]["task-1"]
       assert task["instructions"] == "Use React hooks"
       assert task["role"] == "frontend"
       assert task["context_files"] == ["src/App.tsx"]


   def test_plan_import_handler_rejects_cycle(daemon_manager):
       """plan_import handler should reject plans with cycles."""
       daemon, _ = daemon_manager

       content = '''
   ```json
   {
     "goal": "Test",
     "tasks": {
       "task-a": {"description": "A", "dependencies": ["task-b"]},
       "task-b": {"description": "B", "dependencies": ["task-a"]}
     }
   }
   ```
   '''

       from tests.harness.conftest import send_command

       response = send_command(
           daemon.socket_path,
           {"command": "plan_import", "content": content, "file_path": "test.md"},
       )

       assert response["status"] == "error"
       assert "cycle" in response["message"].lower()
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_daemon.py -v -k "plan_import"
   ```
   Expected: FAIL (Unknown command: plan_import)

3. **Implement MINIMAL code:**
   ```python
   # Add import at top of src/harness/daemon.py
   from .plan import parse_markdown_to_plan

   # Add to handlers dict in HarnessHandler.dispatch()
   "plan_import": self._handle_plan_import,

   # Add handler method to HarnessHandler class
   def _handle_plan_import(
       self, request: dict[str, Any], server: HarnessDaemon
   ) -> dict[str, Any]:
       """Import plan from markdown content."""
       content = request.get("content")
       file_path = request.get("file_path", "<unknown>")

       if not content:
           return {"status": "error", "message": "content is required"}

       try:
           plan = parse_markdown_to_plan(content)
           state = plan.to_workflow_state()
           server.state_manager.save(state)

           server.trajectory_logger.log(
               {
                   "event_type": "plan_import",
                   "file_path": file_path,
                   "goal": plan.goal,
                   "task_count": len(plan.tasks),
               }
           )

           return {
               "status": "ok",
               "data": {
                   "goal": plan.goal,
                   "task_count": len(plan.tasks),
                   "task_ids": list(plan.tasks.keys()),
               },
           }
       except ValueError as e:
           return {"status": "error", "message": str(e)}
       except Exception as e:
           return {"status": "error", "message": f"Failed to import plan: {e}"}
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_daemon.py -v -k "plan_import"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(daemon): add plan_import handler with injection support"
   ```

---

### Task 10: Daemon ACP Integration

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/daemon.py:297-325` (HarnessDaemon.__init__)
- Modify: `src/harness/daemon.py:170-180` (trajectory logging hooks)
- Test: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_daemon.py
   import os


   def test_daemon_initializes_acp_client(daemon_manager):
       """Daemon should initialize ACP client for telemetry relay."""
       daemon, _ = daemon_manager
       assert hasattr(daemon, "acp_client")
       from harness.acp import ACPClient

       assert isinstance(daemon.acp_client, ACPClient)


   def test_daemon_acp_disabled_by_env(socket_path, worktree):
       """Daemon should not connect ACP if HARNESS_ACP_DISABLED=1."""
       import os

       os.environ["HARNESS_ACP_DISABLED"] = "1"
       try:
           from tests.harness.conftest import DaemonManager

           with DaemonManager(socket_path, worktree) as daemon:
               # ACP client should exist but not be connected
               assert hasattr(daemon, "acp_client")
               assert not daemon.acp_client.is_connected
       finally:
           del os.environ["HARNESS_ACP_DISABLED"]
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_daemon.py -v -k "acp"
   ```
   Expected: FAIL (daemon doesn't have acp_client attribute)

3. **Implement MINIMAL code:**
   ```python
   # Add import at top of src/harness/daemon.py
   from .acp import ACPClient

   # Add to HarnessDaemon class attributes (after runtime)
   acp_client: ACPClient

   # Add to HarnessDaemon.__init__ (after runtime initialization)
   # ACP telemetry relay (optional - graceful degradation)
   self.acp_client = ACPClient(
       port=int(os.getenv("HARNESS_ACP_PORT", "9100")),
       reconnect=True,
   )
   if not os.getenv("HARNESS_ACP_DISABLED"):
       self.acp_client.connect()  # Non-blocking, graceful failure

   # Modify trajectory logging calls to also send to ACP
   # In _handle_task_claim, after trajectory_logger.log():
   server.acp_client.send_log(
       {
           "event_type": "task_claim",
           "task_id": task.id,
           "worker_id": worker_id,
           "is_retry": is_retry,
           "is_reclaim": is_reclaim,
       }
   )

   # Similarly for _handle_task_complete and _handle_exec
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_daemon.py -v -k "acp"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(daemon): integrate ACP client for telemetry relay"
   ```

---

### Task 11: Integration Test - Full Flow

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_integration.py

   def test_plan_import_then_claim_tasks_with_injection(socket_path, worktree):
       """Full flow: import plan with injection -> claim tasks -> verify injection."""
       import json
       import os
       import subprocess
       import sys

       from harness.client import spawn_daemon

       spawn_daemon(str(worktree), socket_path)

       try:
           plan_file = worktree / "plan.md"
           plan_file.write_text('''
   # Integration Test Plan

   ```json
   {
     "goal": "Test injection flow",
     "tasks": {
       "task-1": {
         "description": "First task",
         "instructions": "Follow these steps carefully",
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

           env = {
               **os.environ,
               "HARNESS_SOCKET": socket_path,
               "HARNESS_WORKTREE": str(worktree),
               "HARNESS_ACP_DISABLED": "1",  # Disable ACP for test
           }

           # Import plan
           result = subprocess.run(
               [sys.executable, "-m", "harness.client", "plan", "import", "--file", str(plan_file)],
               capture_output=True,
               text=True,
               env=env,
           )
           assert result.returncode == 0, f"Import failed: {result.stderr}"

           # Claim first task
           result = subprocess.run(
               [sys.executable, "-m", "harness.client", "task", "claim"],
               capture_output=True,
               text=True,
               env=env,
           )
           assert result.returncode == 0
           data = json.loads(result.stdout)
           task = data["task"]

           # Verify injection fields are present
           assert task["id"] == "task-1"
           assert task["instructions"] == "Follow these steps carefully"
           assert task["role"] == "backend"

           # Complete first task
           result = subprocess.run(
               [sys.executable, "-m", "harness.client", "task", "complete", "--id", "task-1"],
               capture_output=True,
               text=True,
               env=env,
           )
           assert result.returncode == 0

           # Claim second task
           result = subprocess.run(
               [sys.executable, "-m", "harness.client", "task", "claim"],
               capture_output=True,
               text=True,
               env=env,
           )
           assert result.returncode == 0
           data = json.loads(result.stdout)
           assert data["task"]["id"] == "task-2"

       finally:
           from tests.harness.conftest import cleanup_daemon_subprocess

           cleanup_daemon_subprocess(socket_path)
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_plan_import_then_claim_tasks_with_injection -v
   ```
   Expected: FAIL until all previous tasks are complete

3. **Implement:** No new code - this test validates the integration.

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_integration.py::test_plan_import_then_claim_tasks_with_injection -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add plan import with injection flow test"
   ```

---

### Task 12: Code Review

**Effort:** standard (10-15 tool calls)

**Files:**
- All modified files from Tasks 1-11

**Instructions:**

1. Use dev-workflow:code-reviewer agent to review all changes:
   ```bash
   git diff master..HEAD
   ```

2. Address any feedback using dev-workflow:receiving-code-review skill

3. Run full test suite:
   ```bash
   pytest tests/harness/ -v
   ```

4. Verify no regressions

---

## Validation Checklist

Before merging, verify:

- [ ] Task model has `instructions`, `role`, `context_files` fields
- [ ] `harness plan template` outputs schema with injection fields
- [ ] `harness plan import --file plan.md` parses JSON and preserves injection fields
- [ ] Cycle detection rejects invalid DAGs
- [ ] Missing dependency detection works
- [ ] ACP client initializes in daemon (graceful failure if port unavailable)
- [ ] ACP telemetry is disabled via `HARNESS_ACP_DISABLED=1`
- [ ] Tasks can be claimed after import with injection fields intact
- [ ] Dependency ordering enforced
- [ ] All existing tests pass
- [ ] No Pydantic imports in client.py

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│                     Claude Code                              │
│                    (ws://localhost:9100)                     │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ ACP Telemetry
                              │ (JSONL events)
┌─────────────────────────────────────────────────────────────┐
│                      Harness Daemon                          │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │ StateManager│  │ ACPClient    │  │ TrajectoryLogger    │ │
│  │ (JSON DAG)  │  │ (WebSocket)  │  │ (JSONL append)      │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
│         ▲                                                    │
│         │ plan_import                                        │
│  ┌──────┴──────┐                                            │
│  │  plan.py    │◄── parse_markdown_to_plan()                │
│  │  (Fuzzy     │                                            │
│  │  Compiler)  │                                            │
│  └─────────────┘                                            │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ Unix Socket RPC
┌─────────────────────────────────────────────────────────────┐
│                      Harness Client                          │
│  harness plan import --file plan.md                          │
│  harness task claim                                          │
│  harness task complete --id task-1                           │
└─────────────────────────────────────────────────────────────┘
```

## Post-Plan Next Steps

After this plan is complete:
1. Update dev-workflow plugin prompts to use new commands
2. Implement ACP protocol handshake (MCP-compatible framing)
3. Add `harness plan status` command for DAG visualization
4. Create bash shim (hook-helpers.sh) in dev-workflow plugin repo
