# Strangler Fig Plan Import Implementation

**Goal:** Enable the Harness daemon to ingest LLM-generated plans from Markdown files with embedded JSON, converting them to the internal DAG schema for task execution.

**Architecture:** Add a `plan.py` module that parses Markdown containing JSON plan blocks, validates the DAG, and converts to `WorkflowState`. Expose via `harness plan import` and `harness plan template` client commands routed to a new daemon handler.

---

## Task Overview

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent: plan.py models vs test fixtures |
| Group 2 | 3, 4 | Both touch plan.py: parser + integration |
| Group 3 | 5, 6 | Client commands: template + import |
| Group 4 | 7 | Daemon handler depends on client commands |
| Group 5 | 8 | Integration test depends on all above |

---

### Task 1: Plan Module - Pydantic Models

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
   from harness.plan import PlanTaskDefinition, PlanDefinition


   def test_plan_task_definition_basic():
       """PlanTaskDefinition should validate required fields."""
       task = PlanTaskDefinition(description="Implement feature X")
       assert task.description == "Implement feature X"
       assert task.dependencies == []
       assert task.timeout_seconds == 600


   def test_plan_task_definition_with_dependencies():
       """PlanTaskDefinition should accept dependencies list."""
       task = PlanTaskDefinition(
           description="Build on feature X",
           dependencies=["task-1", "task-2"],
           timeout_seconds=1200,
       )
       assert task.dependencies == ["task-1", "task-2"]
       assert task.timeout_seconds == 1200


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
   """

   from pydantic import BaseModel, Field


   class PlanTaskDefinition(BaseModel):
       """User-facing task definition (simpler than internal Task)."""

       description: str = Field(..., description="Task description")
       dependencies: list[str] = Field(default_factory=list, description="Task IDs this depends on")
       timeout_seconds: int = Field(600, description="Timeout for task execution")


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
   git add -A && git commit -m "feat(plan): add PlanDefinition and PlanTaskDefinition models"
   ```

---

### Task 2: Plan Module - DAG Validation

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/plan.py:15-25`
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
   pytest tests/harness/test_plan.py::test_plan_definition_validate_dag_no_cycle -v
   pytest tests/harness/test_plan.py::test_plan_definition_validate_dag_detects_cycle -v
   pytest tests/harness/test_plan.py::test_plan_definition_validate_dag_missing_dependency -v
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

### Task 3: Plan Module - Markdown Parser

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/plan.py:30-60`
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
   # Add to src/harness/plan.py
   import json
   import re


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

### Task 4: Plan Module - Convert to WorkflowState

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/plan.py:60-90`
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
           )
       return WorkflowState(tasks=tasks)
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_plan.py -v -k "to_workflow_state"
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): add to_workflow_state conversion method"
   ```

---

### Task 5: Client Command - Plan Template

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/client.py:350-365`
- Test: `tests/harness/test_client.py` (new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_client.py (or create new file)
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
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_client.py::test_plan_template_outputs_json_schema -v
   ```
   Expected: FAIL (command doesn't exist)

3. **Implement MINIMAL code:**
   ```python
   # Add to src/harness/client.py in main() after line ~350

   # plan subcommand with template and import
   plan_parser = subparsers.add_parser("plan", help="Plan management commands")
   plan_subparsers = plan_parser.add_subparsers(dest="plan_command", required=True)
   plan_subparsers.add_parser("template", help="Output JSON schema template")

   # Add to command routing after line ~400
   elif args.command == "plan":
       if args.plan_command == "template":
           _cmd_plan_template()

   # Add handler function
   def _cmd_plan_template() -> None:
       """Output JSON schema template for plan files."""
       template = {
           "goal": "Describe the overall goal here",
           "tasks": {
               "task-1": {
                   "description": "First task description",
                   "dependencies": [],
                   "timeout_seconds": 600
               },
               "task-2": {
                   "description": "Second task that depends on first",
                   "dependencies": ["task-1"],
                   "timeout_seconds": 600
               }
           }
       }
       print(json.dumps(template, indent=2))
   ```

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_client.py::test_plan_template_outputs_json_schema -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): add 'harness plan template' command"
   ```

---

### Task 6: Client Command - Plan Import

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/client.py:355-380`
- Test: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_client.py

   def test_plan_import_sends_rpc(tmp_path, socket_path, worktree):
       """harness plan import should send plan_import RPC to daemon."""
       # Create plan file
       plan_file = tmp_path / "plan.md"
       plan_file.write_text('''
   # Test Plan

   ```json
   {
     "goal": "Test import",
     "tasks": {
       "task-1": {"description": "First task"}
     }
   }
   ```
   ''')

       # This test requires daemon running - use DaemonManager
       from tests.harness.conftest import DaemonManager

       with DaemonManager(socket_path, worktree):
           result = subprocess.run(
               [sys.executable, "-m", "harness.client", "plan", "import", "--file", str(plan_file)],
               capture_output=True,
               text=True,
               env={**os.environ, "HARNESS_SOCKET": socket_path, "HARNESS_WORKTREE": str(worktree)},
           )
           assert result.returncode == 0
           assert "imported" in result.stdout.lower() or "success" in result.stdout.lower()


   def test_plan_import_file_not_found():
       """harness plan import should error if file doesn't exist."""
       result = subprocess.run(
           [sys.executable, "-m", "harness.client", "plan", "import", "--file", "/nonexistent/plan.md"],
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

   # Add to command routing
   elif args.plan_command == "import":
       _cmd_plan_import(socket_path, worktree_root, args.file)

   # Add handler function
   def _cmd_plan_import(socket_path: str, worktree_root: str, file_path: str) -> None:
       """Import plan from markdown file."""
       # Read file locally (client responsibility - no Pydantic)
       path = Path(file_path)
       if not path.exists():
           print(f"Error: File not found: {file_path}", file=sys.stderr)
           sys.exit(1)

       content = path.read_text()

       # Send to daemon for parsing and validation
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
   (Note: RPC test requires daemon handler - will pass after Task 7)

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): add 'harness plan import' command"
   ```

---

### Task 7: Daemon Handler - Plan Import

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/daemon.py:67-80` (handlers dict)
- Modify: `src/harness/daemon.py:295-330` (new handler method)
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
   pytest tests/harness/test_daemon.py::test_plan_import_handler_parses_and_saves -v
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
           # Parse markdown to plan (Pydantic validation happens here)
           plan = parse_markdown_to_plan(content)

           # Convert to WorkflowState
           state = plan.to_workflow_state()

           # Save state (validates DAG again for safety)
           server.state_manager.save(state)

           # Log to trajectory
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
   git add -A && git commit -m "feat(daemon): add plan_import handler for markdown plan ingestion"
   ```

---

### Task 8: Integration Test - Full Flow

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_integration.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   # Add to tests/harness/test_integration.py

   def test_plan_import_then_claim_tasks(socket_path, worktree):
       """Full flow: import plan -> claim tasks -> complete tasks."""
       import subprocess
       import sys
       import os

       # Start daemon
       from harness.client import spawn_daemon
       spawn_daemon(str(worktree), socket_path)

       try:
           # Create plan file
           plan_file = worktree / "plan.md"
           plan_file.write_text('''
   # Integration Test Plan

   ```json
   {
     "goal": "Test full flow",
     "tasks": {
       "task-1": {"description": "First task"},
       "task-2": {"description": "Second task", "dependencies": ["task-1"]}
     }
   }
   ```
   ''')

           env = {**os.environ, "HARNESS_SOCKET": socket_path, "HARNESS_WORKTREE": str(worktree)}

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
           import json
           data = json.loads(result.stdout)
           assert data["task"]["id"] == "task-1"  # No deps, claimable first

           # Complete first task
           result = subprocess.run(
               [sys.executable, "-m", "harness.client", "task", "complete", "--id", "task-1"],
               capture_output=True,
               text=True,
               env=env,
           )
           assert result.returncode == 0

           # Claim second task (should now be available)
           result = subprocess.run(
               [sys.executable, "-m", "harness.client", "task", "claim"],
               capture_output=True,
               text=True,
               env=env,
           )
           assert result.returncode == 0
           data = json.loads(result.stdout)
           assert data["task"]["id"] == "task-2"  # Now claimable

       finally:
           from tests.harness.conftest import cleanup_daemon_subprocess
           cleanup_daemon_subprocess(socket_path)
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_integration.py::test_plan_import_then_claim_tasks -v
   ```
   Expected: FAIL until all previous tasks are complete

3. **Implement:** No new code - this test validates the integration of all previous tasks.

4. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_integration.py::test_plan_import_then_claim_tasks -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "test(integration): add plan import -> task claim flow test"
   ```

---

### Task 9: Code Review

**Effort:** standard (10-15 tool calls)

**Files:**
- All modified files from Tasks 1-8

**Instructions:**

1. Use dev-workflow:code-reviewer agent to review all changes:
   ```bash
   git diff main..HEAD
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

- [ ] `harness plan template` outputs valid JSON schema
- [ ] `harness plan import --file plan.md` parses JSON from Markdown
- [ ] Cycle detection rejects invalid DAGs
- [ ] Missing dependency detection works
- [ ] Imported plan converts to WorkflowState correctly
- [ ] Tasks can be claimed after import
- [ ] Dependency ordering enforced (task-2 waits for task-1)
- [ ] All existing tests pass
- [ ] No Pydantic imports in client.py

## Post-Plan Next Steps

After this plan is complete:
1. Update dev-workflow plugin prompts to use `harness plan template` and `harness plan import`
2. Create bash shim (hook-helpers.sh) in dev-workflow plugin repo
3. Update agent prompts to use `harness task claim/complete` loop
