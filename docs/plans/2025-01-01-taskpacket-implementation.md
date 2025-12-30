# TaskPacket Architecture Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-01-01-taskpacket-implementation.md` to implement task-by-task.

**Goal:** Implement complete TaskPacket architecture with orchestration layer, enforcement hooks, and artifact validation - enabling 90% performance improvement via hierarchical task distribution.

**Architecture:** Three layers:
1. **Data Layer** - TaskPacket struct and XML parser (Tasks 1-3)
2. **Enforcement Layer** - Artifact validation, Stop hooks, re-injection (Tasks 4-6)
3. **Orchestration Layer** - Custom agents, hook configs (Tasks 7-8)

**Tech Stack:** Python 3.13+, msgspec for serialization, xml.etree.ElementTree for XML parsing, pytest for testing.

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent structs in plan.py |
| Group 2 | 3 | XML parser depends on TaskPacket |
| Group 3 | 4 | Daemon changes depend on XML parser |
| Group 4 | 5, 6 | Task verify and remind commands (parallel) |
| Group 5 | 7 | Hook configuration depends on CLI commands |
| Group 6 | 8 | Custom agents depend on hooks |
| Group 7 | 9 | Code Review |

---

### Task 1: Add AgentModel Enum and TaskPacket Struct

**Files:**
- Modify: `src/hyh/plan.py:1-10` (add imports)
- Modify: `src/hyh/plan.py:85-91` (add after PlanTaskDefinition)
- Test: `tests/hyh/test_plan.py`

**Step 1: Write failing test for AgentModel enum** (2 min)

```python
# tests/hyh/test_plan.py - add at end of file

def test_agent_model_enum_values():
    """AgentModel enum has haiku, sonnet, opus values."""
    from hyh.plan import AgentModel

    assert AgentModel.HAIKU.value == "haiku"
    assert AgentModel.SONNET.value == "sonnet"
    assert AgentModel.OPUS.value == "opus"
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_agent_model_enum_values -v
```

Expected: FAIL with `ImportError: cannot import name 'AgentModel' from 'hyh.plan'`

**Step 3: Implement AgentModel enum** (2 min)

Add to `src/hyh/plan.py` after the imports section (around line 6):

```python
from enum import Enum


class AgentModel(str, Enum):
    """Model tier for agent tasks.

    Usage guidance:
    - HAIKU: Quick verification, lint checks, simple file operations
    - SONNET: Standard implementation, most tasks (default)
    - OPUS: Complex architectural decisions, synthesis, planning
    """

    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_agent_model_enum_values -v
```

Expected: PASS (1 passed)

**Step 5: Write failing test for TaskPacket struct** (3 min)

```python
# tests/hyh/test_plan.py - add after previous test

def test_task_packet_struct_defaults():
    """TaskPacket has correct default values."""
    from hyh.plan import AgentModel, TaskPacket

    packet = TaskPacket(
        id="T001",
        description="Test task",
        instructions="Do the thing",
        success_criteria="Tests pass",
    )

    assert packet.id == "T001"
    assert packet.description == "Test task"
    assert packet.role is None
    assert packet.model == AgentModel.SONNET  # default
    assert packet.files_in_scope == ()
    assert packet.files_out_of_scope == ()
    assert packet.input_context == ""
    assert packet.output_contract == ""
    assert packet.instructions == "Do the thing"
    assert packet.constraints == ""
    assert packet.tools == ()
    assert packet.verification_commands == ()
    assert packet.success_criteria == "Tests pass"
    assert packet.artifacts_to_read == ()
    assert packet.artifacts_to_write == ()


def test_task_packet_struct_full():
    """TaskPacket accepts all fields."""
    from hyh.plan import AgentModel, TaskPacket

    packet = TaskPacket(
        id="T001",
        description="Create token service",
        role="implementer",
        model=AgentModel.OPUS,
        files_in_scope=("src/auth/token.py", "tests/auth/test_token.py"),
        files_out_of_scope=("src/auth/session.py",),
        input_context="User credentials schema",
        output_contract="TokenService with generate()",
        instructions="1. Write test\n2. Implement",
        constraints="Use existing jwt library",
        tools=("Read", "Edit", "Bash"),
        verification_commands=("pytest tests/auth/", "ruff check"),
        success_criteria="All tests pass",
        artifacts_to_read=(),
        artifacts_to_write=(".claude/artifacts/T001-api.md",),
    )

    assert packet.role == "implementer"
    assert packet.model == AgentModel.OPUS
    assert len(packet.files_in_scope) == 2
    assert len(packet.tools) == 3
```

**Step 6: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_task_packet_struct_defaults tests/hyh/test_plan.py::test_task_packet_struct_full -v
```

Expected: FAIL with `ImportError: cannot import name 'TaskPacket' from 'hyh.plan'`

**Step 7: Implement TaskPacket struct** (5 min)

Add to `src/hyh/plan.py` after AgentModel (around line 18):

```python
class TaskPacket(Struct, frozen=True, forbid_unknown_fields=True, omit_defaults=True):
    """Complete work packet for an agent. Agent receives ONLY this."""

    # Required fields
    id: str
    description: str
    instructions: str
    success_criteria: str

    # Optional identity
    role: str | None = None
    model: AgentModel = AgentModel.SONNET

    # Scope boundaries
    files_in_scope: tuple[str, ...] = ()
    files_out_of_scope: tuple[str, ...] = ()

    # Interface contract
    input_context: str = ""
    output_contract: str = ""

    # Implementation
    constraints: str = ""

    # Tool permissions
    tools: tuple[str, ...] = ()

    # Verification
    verification_commands: tuple[str, ...] = ()

    # Artifacts
    artifacts_to_read: tuple[str, ...] = ()
    artifacts_to_write: tuple[str, ...] = ()
```

**Step 8: Run tests to verify they pass** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_task_packet_struct_defaults tests/hyh/test_plan.py::test_task_packet_struct_full -v
```

Expected: PASS (2 passed)

**Step 9: Commit** (30 sec)

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "$(cat <<'EOF'
feat(plan): add AgentModel enum and TaskPacket struct

TaskPacket is a self-contained work packet that agents receive via RPC.
Includes scope boundaries, interface contracts, verification commands,
and artifact declarations. AgentModel provides guidance on when to use
haiku/sonnet/opus.
EOF
)"
```

---

### Task 2: Add XMLPlanDefinition Struct

**Files:**
- Modify: `src/hyh/plan.py` (add after TaskPacket)
- Test: `tests/hyh/test_plan.py`

**Step 1: Write failing test for XMLPlanDefinition** (2 min)

```python
# tests/hyh/test_plan.py - add after TaskPacket tests

def test_xml_plan_definition_struct():
    """XMLPlanDefinition holds goal, tasks, and dependencies."""
    from hyh.plan import TaskPacket, XMLPlanDefinition

    packet = TaskPacket(
        id="T001",
        description="Test",
        instructions="Do it",
        success_criteria="Done",
    )

    plan = XMLPlanDefinition(
        goal="Test goal",
        tasks={"T001": packet},
        dependencies={"T002": ("T001",)},
    )

    assert plan.goal == "Test goal"
    assert "T001" in plan.tasks
    assert plan.dependencies["T002"] == ("T001",)
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_xml_plan_definition_struct -v
```

Expected: FAIL with `ImportError: cannot import name 'XMLPlanDefinition' from 'hyh.plan'`

**Step 3: Implement XMLPlanDefinition struct** (3 min)

Add to `src/hyh/plan.py` after TaskPacket:

```python
class XMLPlanDefinition(Struct, frozen=True, forbid_unknown_fields=True):
    """Plan parsed from XML format with TaskPackets."""

    goal: str
    tasks: dict[str, TaskPacket]
    dependencies: dict[str, tuple[str, ...]] = {}

    def to_workflow_state(self) -> WorkflowState:
        """Convert to WorkflowState for daemon execution."""
        from .state import Task, TaskStatus, WorkflowState

        state_tasks = {}
        for tid, packet in self.tasks.items():
            state_tasks[tid] = Task(
                id=tid,
                description=packet.description,
                status=TaskStatus.PENDING,
                dependencies=self.dependencies.get(tid, ()),
                instructions=packet.instructions,
                role=packet.role,
                model=packet.model.value if packet.model else None,
                files_in_scope=packet.files_in_scope,
                files_out_of_scope=packet.files_out_of_scope,
                input_context=packet.input_context,
                output_contract=packet.output_contract,
                constraints=packet.constraints,
                tools=packet.tools,
                verification_commands=packet.verification_commands,
                success_criteria=packet.success_criteria,
                artifacts_to_read=packet.artifacts_to_read,
                artifacts_to_write=packet.artifacts_to_write,
            )
        return WorkflowState(tasks=state_tasks)

    def validate_dag(self) -> None:
        """Validate task dependencies form a valid DAG."""
        from .state import detect_cycle

        for task_id, deps in self.dependencies.items():
            if task_id not in self.tasks:
                raise ValueError(f"Dependency declared for unknown task: {task_id}")
            for dep in deps:
                if dep not in self.tasks:
                    raise ValueError(f"Missing dependency: {dep} (in {task_id})")

        graph = {tid: self.dependencies.get(tid, ()) for tid in self.tasks}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Cycle detected at {cycle_node}")
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_xml_plan_definition_struct -v
```

Expected: PASS (1 passed)

**Step 5: Commit** (30 sec)

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "$(cat <<'EOF'
feat(plan): add XMLPlanDefinition struct

Holds TaskPackets with explicit dependency graph. Converts to
WorkflowState with all extended fields for daemon execution.
EOF
)"
```

---

### Task 3: Implement XML Plan Parser

**Files:**
- Modify: `src/hyh/plan.py` (add parse_xml_plan function)
- Modify: `src/hyh/plan.py:199-225` (update parse_plan_content)
- Test: `tests/hyh/test_plan.py`

**Step 1: Write failing test for parse_xml_plan** (3 min)

```python
# tests/hyh/test_plan.py - add after XMLPlanDefinition tests

def test_parse_xml_plan_full():
    """parse_xml_plan parses complete XML plan with all fields."""
    from hyh.plan import AgentModel, parse_xml_plan

    xml_content = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Implement authentication">
  <dependencies>
    <dep from="T002" to="T001"/>
  </dependencies>

  <task id="T001" role="implementer" model="opus">
    <description>Create token service</description>
    <tools>Read, Edit, Bash</tools>
    <scope>
      <include>src/auth/token.py</include>
      <include>tests/auth/test_token.py</include>
      <exclude>src/auth/session.py</exclude>
    </scope>
    <interface>
      <input>User credentials</input>
      <output>JWT token</output>
    </interface>
    <instructions><![CDATA[
1. Write failing test
2. Implement
    ]]></instructions>
    <constraints>Use existing jwt library</constraints>
    <verification>
      <command>pytest tests/auth/</command>
      <command>ruff check src/auth/</command>
    </verification>
    <success>All tests pass</success>
    <artifacts>
      <write>.claude/artifacts/T001-api.md</write>
    </artifacts>
  </task>

  <task id="T002" role="reviewer" model="haiku">
    <description>Review token service</description>
    <tools>Read, Grep</tools>
    <scope>
      <include>src/auth/token.py</include>
    </scope>
    <instructions>Review the implementation</instructions>
    <success>Report written</success>
    <artifacts>
      <read>.claude/artifacts/T001-api.md</read>
      <write>.claude/artifacts/T002-review.md</write>
    </artifacts>
  </task>
</plan>
"""

    plan = parse_xml_plan(xml_content)

    assert plan.goal == "Implement authentication"
    assert plan.dependencies["T002"] == ("T001",)

    t001 = plan.tasks["T001"]
    assert t001.role == "implementer"
    assert t001.model == AgentModel.OPUS
    assert t001.files_in_scope == ("src/auth/token.py", "tests/auth/test_token.py")
    assert t001.tools == ("Read", "Edit", "Bash")
    assert t001.verification_commands == ("pytest tests/auth/", "ruff check src/auth/")
    assert t001.artifacts_to_write == (".claude/artifacts/T001-api.md",)

    t002 = plan.tasks["T002"]
    assert t002.model == AgentModel.HAIKU
    assert t002.artifacts_to_read == (".claude/artifacts/T001-api.md",)
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_parse_xml_plan_full -v
```

Expected: FAIL with `ImportError: cannot import name 'parse_xml_plan' from 'hyh.plan'`

**Step 3: Implement parse_xml_plan** (5 min)

Add to `src/hyh/plan.py` after XMLPlanDefinition:

```python
import xml.etree.ElementTree as ET


def parse_xml_plan(content: str) -> XMLPlanDefinition:
    """Parse XML plan format into XMLPlanDefinition with TaskPackets."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML: {e}") from e

    if root.tag != "plan":
        raise ValueError(f"Root element must be 'plan', got '{root.tag}'")

    goal = root.get("goal", "Goal not specified")

    # Parse dependencies
    dependencies: dict[str, tuple[str, ...]] = {}
    deps_elem = root.find("dependencies")
    if deps_elem is not None:
        for dep in deps_elem.findall("dep"):
            from_task = dep.get("from")
            to_tasks = dep.get("to", "")
            if from_task and to_tasks:
                dependencies[from_task] = tuple(t.strip() for t in to_tasks.split(","))

    # Parse tasks
    tasks: dict[str, TaskPacket] = {}
    for task_elem in root.findall(".//task"):
        task_id = task_elem.get("id")
        if not task_id:
            raise ValueError("Task element missing 'id' attribute")
        _validate_task_id(task_id)

        model_str = task_elem.get("model", "sonnet")
        try:
            model = AgentModel(model_str)
        except ValueError:
            raise ValueError(f"Invalid model '{model_str}' for task {task_id}")

        def get_text(tag: str, default: str = "") -> str:
            elem = task_elem.find(tag)
            return (elem.text or "").strip() if elem is not None else default

        # Parse scope
        scope_elem = task_elem.find("scope")
        files_in_scope: tuple[str, ...] = ()
        files_out_of_scope: tuple[str, ...] = ()
        if scope_elem is not None:
            files_in_scope = tuple(
                (e.text or "").strip() for e in scope_elem.findall("include") if e.text
            )
            files_out_of_scope = tuple(
                (e.text or "").strip() for e in scope_elem.findall("exclude") if e.text
            )

        # Parse interface
        interface_elem = task_elem.find("interface")
        input_context = ""
        output_contract = ""
        if interface_elem is not None:
            input_elem = interface_elem.find("input")
            output_elem = interface_elem.find("output")
            input_context = (input_elem.text or "").strip() if input_elem is not None else ""
            output_contract = (output_elem.text or "").strip() if output_elem is not None else ""

        # Parse tools
        tools_elem = task_elem.find("tools")
        tools: tuple[str, ...] = ()
        if tools_elem is not None and tools_elem.text:
            tools = tuple(t.strip() for t in tools_elem.text.split(",") if t.strip())

        # Parse verification commands
        verification_elem = task_elem.find("verification")
        verification_commands: tuple[str, ...] = ()
        if verification_elem is not None:
            verification_commands = tuple(
                (e.text or "").strip() for e in verification_elem.findall("command") if e.text
            )

        # Parse artifacts
        artifacts_elem = task_elem.find("artifacts")
        artifacts_to_read: tuple[str, ...] = ()
        artifacts_to_write: tuple[str, ...] = ()
        if artifacts_elem is not None:
            artifacts_to_read = tuple(
                (e.text or "").strip() for e in artifacts_elem.findall("read") if e.text
            )
            artifacts_to_write = tuple(
                (e.text or "").strip() for e in artifacts_elem.findall("write") if e.text
            )

        description = get_text("description")
        instructions = get_text("instructions")
        success_criteria = get_text("success")

        if not description:
            raise ValueError(f"Task {task_id} missing <description>")
        if not instructions:
            raise ValueError(f"Task {task_id} missing <instructions>")
        if not success_criteria:
            raise ValueError(f"Task {task_id} missing <success>")

        tasks[task_id] = TaskPacket(
            id=task_id,
            description=description,
            role=task_elem.get("role"),
            model=model,
            files_in_scope=files_in_scope,
            files_out_of_scope=files_out_of_scope,
            input_context=input_context,
            output_contract=output_contract,
            instructions=instructions,
            constraints=get_text("constraints"),
            tools=tools,
            verification_commands=verification_commands,
            success_criteria=success_criteria,
            artifacts_to_read=artifacts_to_read,
            artifacts_to_write=artifacts_to_write,
        )

    if not tasks:
        raise ValueError("No valid tasks found in XML plan")

    return XMLPlanDefinition(goal=goal, tasks=tasks, dependencies=dependencies)
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_parse_xml_plan_full -v
```

Expected: PASS (1 passed)

**Step 5: Update parse_plan_content for XML detection** (2 min)

In `src/hyh/plan.py`, update `parse_plan_content` to add XML detection:

```python
def parse_plan_content(content: str) -> PlanDefinition | XMLPlanDefinition:
    if not content or not content.strip():
        raise ValueError("No valid plan found: content is empty or whitespace-only")

    # Format 0: XML plan (new primary format)
    stripped = content.strip()
    if stripped.startswith("<?xml") or stripped.startswith("<plan"):
        xml_plan = parse_xml_plan(content)
        if not xml_plan.tasks:
            raise ValueError("No valid plan found: no tasks defined in XML plan")
        xml_plan.validate_dag()
        return xml_plan

    # ... rest unchanged (markdown and speckit formats)
```

**Step 6: Commit** (30 sec)

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "$(cat <<'EOF'
feat(plan): add XML plan parser

parse_xml_plan() parses XML format into XMLPlanDefinition with
TaskPackets. parse_plan_content() auto-detects XML format.
EOF
)"
```

---

### Task 4: Extend Task Struct and Update Daemon

**Files:**
- Modify: `src/hyh/state.py` (extend Task struct)
- Test: `tests/hyh/test_state.py`
- Test: `tests/hyh/test_daemon.py`

**Step 1: Write failing test for extended Task fields** (2 min)

```python
# tests/hyh/test_state.py - add at end

def test_task_extended_fields():
    """Task supports TaskPacket-like extended fields."""
    from hyh.state import Task

    task = Task(
        id="T001",
        description="Test task",
        files_in_scope=("src/a.py",),
        tools=("Read", "Edit"),
        verification_commands=("pytest",),
        success_criteria="Tests pass",
        artifacts_to_write=(".claude/artifacts/T001.md",),
        model="sonnet",
    )

    assert task.files_in_scope == ("src/a.py",)
    assert task.tools == ("Read", "Edit")
    assert task.model == "sonnet"
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_state.py::test_task_extended_fields -v
```

Expected: FAIL with `TypeError: ... unexpected keyword argument 'files_in_scope'`

**Step 3: Extend Task struct** (3 min)

In `src/hyh/state.py`, add fields to Task class after `role`:

```python
class Task(Struct, frozen=True, forbid_unknown_fields=True):
    id: str
    description: str

    status: TaskStatus = TaskStatus.PENDING
    dependencies: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None
    claimed_by: str | None = None
    timeout_seconds: TimeoutSeconds = 600
    instructions: str | None = None
    role: str | None = None

    # TaskPacket extended fields
    model: str | None = None
    files_in_scope: tuple[str, ...] = ()
    files_out_of_scope: tuple[str, ...] = ()
    input_context: str = ""
    output_contract: str = ""
    constraints: str = ""
    tools: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    success_criteria: str = ""
    artifacts_to_read: tuple[str, ...] = ()
    artifacts_to_write: tuple[str, ...] = ()

    _clock: ClassVar[Callable[[], datetime]] = lambda: datetime.now(UTC)
    # ... rest unchanged
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_state.py::test_task_extended_fields -v
```

Expected: PASS (1 passed)

**Step 5: Commit** (30 sec)

```bash
git add src/hyh/state.py tests/hyh/test_state.py
git commit -m "$(cat <<'EOF'
feat(state): extend Task struct with TaskPacket fields

Adds scope, tools, verification_commands, success_criteria,
and artifacts fields to Task for full TaskPacket support.
EOF
)"
```

---

### Task 5: Add Task Verify Command (Artifact Validation)

**Files:**
- Modify: `src/hyh/daemon.py` (add TaskVerifyRequest and handler)
- Modify: `src/hyh/client.py` (add CLI command)
- Test: `tests/hyh/test_client.py`

**Step 1: Write failing test for task verify** (3 min)

```python
# tests/hyh/test_client.py - add at end

def test_task_verify_checks_artifacts(daemon_manager, worktree, socket_path):
    """task verify checks verification commands and artifacts."""
    from hyh.client import send_rpc

    xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test">
  <task id="T001">
    <description>Test task</description>
    <instructions>Do it</instructions>
    <success>Tests pass</success>
    <artifacts>
      <write>.claude/artifacts/T001.md</write>
    </artifacts>
  </task>
</plan>
"""

    with daemon_manager(worktree, socket_path):
        send_rpc(socket_path, {"command": "plan_import", "content": xml_plan}, str(worktree))
        send_rpc(socket_path, {"command": "task_claim", "worker_id": "w1"}, str(worktree))

        # Verify should fail - artifact not written yet
        response = send_rpc(
            socket_path,
            {"command": "task_verify", "task_id": "T001"},
            str(worktree),
        )

        assert response["status"] == "ok"
        data = response["data"]
        assert data["verified"] is False
        assert "T001.md" in data["missing_artifacts"][0]

        # Write the artifact
        artifact_dir = worktree / ".claude" / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "T001.md").write_text("# API\n")

        # Verify should pass now
        response = send_rpc(
            socket_path,
            {"command": "task_verify", "task_id": "T001"},
            str(worktree),
        )

        assert response["data"]["verified"] is True
        assert response["data"]["missing_artifacts"] == []
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_client.py::test_task_verify_checks_artifacts -v
```

Expected: FAIL with unknown command error

**Step 3: Add TaskVerifyRequest to daemon** (2 min)

In `src/hyh/daemon.py`, add after other request types:

```python
class TaskVerifyRequest(Struct, tag="task_verify", tag_field="command"):
    """Request to verify task completion criteria."""

    task_id: str
```

**Step 4: Add handler for task_verify** (5 min)

In `src/hyh/daemon.py`, add handler to DaemonServer class:

```python
def _handle_task_verify(
    self, request: TaskVerifyRequest, server: DaemonServer
) -> Ok | Err:
    """Verify task completion: artifacts exist, success criteria checkable."""
    state = server.state_manager.load()
    if state is None:
        return Err(message="No active workflow")

    task = state.tasks.get(request.task_id)
    if task is None:
        return Err(message=f"Task not found: {request.task_id}")

    # Check artifacts_to_write exist
    missing_artifacts = []
    for artifact_path in task.artifacts_to_write:
        full_path = server.worktree_root / artifact_path
        if not full_path.exists():
            missing_artifacts.append(artifact_path)

    # Check artifacts_to_read exist (should have been written by deps)
    missing_inputs = []
    for artifact_path in task.artifacts_to_read:
        full_path = server.worktree_root / artifact_path
        if not full_path.exists():
            missing_inputs.append(artifact_path)

    verified = len(missing_artifacts) == 0 and len(missing_inputs) == 0

    return Ok(data={
        "verified": verified,
        "task_id": request.task_id,
        "missing_artifacts": missing_artifacts,
        "missing_inputs": missing_inputs,
        "success_criteria": task.success_criteria,
        "verification_commands": list(task.verification_commands),
    })
```

**Step 5: Add to Request union and match statement** (2 min)

Add `TaskVerifyRequest` to the Request union type and add case to match statement:

```python
case TaskVerifyRequest():
    result = self._handle_task_verify(request, server)
```

**Step 6: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_client.py::test_task_verify_checks_artifacts -v
```

Expected: PASS (1 passed)

**Step 7: Add CLI command** (2 min)

In `src/hyh/client.py`:

```python
# Add subparser
task_verify = task_subparsers.add_parser("verify", help="Verify task completion criteria")
task_verify.add_argument("--id", required=True, help="Task ID to verify")

# Add to match statement
case "verify":
    _cmd_task_verify(socket_path, worktree_root, args.id)

# Add handler
def _cmd_task_verify(socket_path: str, worktree_root: str, task_id: str) -> None:
    response = send_rpc(
        socket_path,
        {"command": "task_verify", "task_id": task_id},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)

    data = response["data"]
    if data["verified"]:
        print(f"Task {task_id}: VERIFIED")
        print(f"Success criteria: {data['success_criteria']}")
    else:
        print(f"Task {task_id}: NOT VERIFIED")
        if data["missing_artifacts"]:
            print(f"Missing artifacts: {', '.join(data['missing_artifacts'])}")
        if data["missing_inputs"]:
            print(f"Missing inputs: {', '.join(data['missing_inputs'])}")
        sys.exit(1)
```

**Step 8: Commit** (30 sec)

```bash
git add src/hyh/daemon.py src/hyh/client.py tests/hyh/test_client.py
git commit -m "$(cat <<'EOF'
feat(cli): add task verify command for artifact validation

Checks that artifacts_to_write exist before allowing task completion.
Used by Stop/SubagentStop hooks to prevent premature termination.
EOF
)"
```

---

### Task 6: Add Task Remind Command (Re-injection Pattern)

**Files:**
- Modify: `src/hyh/daemon.py` (add TaskRemindRequest and handler)
- Modify: `src/hyh/client.py` (add CLI command)
- Test: `tests/hyh/test_client.py`

**Step 1: Write failing test for task remind** (2 min)

```python
# tests/hyh/test_client.py - add at end

def test_task_remind_returns_checklist(daemon_manager, worktree, socket_path):
    """task remind returns task checklist for re-injection."""
    from hyh.client import send_rpc

    xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test">
  <task id="T001">
    <description>Test task</description>
    <instructions>1. Write test\n2. Implement</instructions>
    <success>All tests pass</success>
    <verification>
      <command>pytest tests/</command>
    </verification>
    <artifacts>
      <write>.claude/artifacts/T001.md</write>
    </artifacts>
  </task>
</plan>
"""

    with daemon_manager(worktree, socket_path):
        send_rpc(socket_path, {"command": "plan_import", "content": xml_plan}, str(worktree))
        send_rpc(socket_path, {"command": "task_claim", "worker_id": "w1"}, str(worktree))

        response = send_rpc(
            socket_path,
            {"command": "task_remind", "task_id": "T001"},
            str(worktree),
        )

        assert response["status"] == "ok"
        reminder = response["data"]["reminder"]
        assert "Test task" in reminder
        assert "pytest tests/" in reminder
        assert "T001.md" in reminder
        assert "All tests pass" in reminder
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_client.py::test_task_remind_returns_checklist -v
```

Expected: FAIL with unknown command error

**Step 3: Add TaskRemindRequest and handler** (3 min)

In `src/hyh/daemon.py`:

```python
class TaskRemindRequest(Struct, tag="task_remind", tag_field="command"):
    """Request task reminder for re-injection."""

    task_id: str


def _handle_task_remind(
    self, request: TaskRemindRequest, server: DaemonServer
) -> Ok | Err:
    """Generate task reminder for re-injection pattern."""
    state = server.state_manager.load()
    if state is None:
        return Err(message="No active workflow")

    task = state.tasks.get(request.task_id)
    if task is None:
        return Err(message=f"Task not found: {request.task_id}")

    # Build reminder text
    lines = [
        f"## Reminder: Task {task.id}",
        "",
        f"**Objective:** {task.description}",
        "",
        "**Your task is NOT complete until:**",
    ]

    for cmd in task.verification_commands:
        lines.append(f"- [ ] Run `{cmd}` and verify it passes")

    for artifact in task.artifacts_to_write:
        lines.append(f"- [ ] Write artifact: `{artifact}`")

    if task.success_criteria:
        lines.append(f"- [ ] Verify: {task.success_criteria}")

    lines.append("")
    lines.append("**Do NOT stop until ALL items are checked.**")

    return Ok(data={
        "task_id": task.id,
        "reminder": "\n".join(lines),
    })
```

**Step 4: Add to Request union and match statement** (1 min)

**Step 5: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_client.py::test_task_remind_returns_checklist -v
```

Expected: PASS (1 passed)

**Step 6: Add CLI command** (2 min)

```python
# Add subparser
task_remind = task_subparsers.add_parser("remind", help="Get task checklist for re-injection")
task_remind.add_argument("--id", required=True, help="Task ID")

# Add handler
def _cmd_task_remind(socket_path: str, worktree_root: str, task_id: str) -> None:
    response = send_rpc(
        socket_path,
        {"command": "task_remind", "task_id": task_id},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(response["data"]["reminder"])
```

**Step 7: Commit** (30 sec)

```bash
git add src/hyh/daemon.py src/hyh/client.py tests/hyh/test_client.py
git commit -m "$(cat <<'EOF'
feat(cli): add task remind command for re-injection pattern

Generates task checklist for periodic re-injection to prevent
Claude from forgetting incomplete items during long tasks.
EOF
)"
```

---

### Task 7: Create Hook Configuration

**Files:**
- Create: `src/hyh/templates/settings-template.json`
- Modify: `src/hyh/init.py` (copy settings template)
- Test: `tests/hyh/test_init.py`

**Step 1: Create settings template** (5 min)

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "uvx hyh session-start",
        "timeout": 5
      }]
    }],

    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "prompt",
        "prompt": "You just modified code. Have you run the relevant tests to verify your changes? If not, run them now before proceeding."
      }]
    }],

    "SubagentStop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Before completing, verify:\n1. Have you run ALL verification_commands from your TaskPacket?\n2. Do ALL tests pass?\n3. Have you written ALL artifacts_to_write?\n\nIf any answer is NO, continue working. Do NOT stop."
      }]
    }],

    "Stop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Before stopping, confirm:\n1. All success_criteria met?\n2. All verification_commands pass?\n3. All artifacts_to_write created?\n4. Called 'hyh task complete' if applicable?\n\nIf any condition fails, continue working."
      }]
    }],

    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "uvx hyh context-preserve",
        "timeout": 10
      }]
    }]
  }
}
```

**Step 2: Update init.py to copy settings** (3 min)

Update `src/hyh/init.py` to include settings template in initialization.

**Step 3: Commit** (30 sec)

```bash
git add src/hyh/templates/settings-template.json src/hyh/init.py
git commit -m "$(cat <<'EOF'
feat(init): add Claude Code hook configuration template

Includes Stop/SubagentStop hooks for preventing premature termination,
PostToolUse for continuous verification prompts, and PreCompact for
context preservation.
EOF
)"
```

---

### Task 8: Create Custom Agent Definitions

**Files:**
- Create: `src/hyh/templates/agents/orchestrator.md`
- Create: `src/hyh/templates/agents/implementer.md`
- Create: `src/hyh/templates/agents/reviewer.md`
- Modify: `src/hyh/init.py` (copy agent templates)

**Step 1: Create orchestrator agent** (5 min)

```markdown
---
name: orchestrator
description: Principal engineer that decomposes specs and coordinates implementation
tools: Read, Grep, Glob, Task
model: opus
---

You are the orchestrator (IC7 principal engineer). You coordinate, you do NOT implement.

<workflow>
1. EXPLORE: Read relevant codebase files, identify patterns and integration points
2. PLAN: Create XML plan with TaskPackets for each component
3. IMPORT: Run `uvx hyh plan import --file plan.xml`
4. SPAWN: For each claimable task, use Task tool with appropriate subagent
5. MONITOR: Check task completion via `uvx hyh task verify --id <id>`
6. VERIFY: Spawn reviewer tasks after implementation
7. SYNTHESIZE: Create PR when all tasks pass
</workflow>

<scaling_rules>
Scale effort to complexity:
- Trivial (< 10 LOC): Direct guidance, no subagent
- Small (10-50 LOC): 1 subagent + verification
- Medium (50-200 LOC): 2-3 subagents with staged deps
- Large (200+ LOC): 5+ subagents, milestone-based

For each task you spawn, include:
1. Single objective
2. files_in_scope / files_out_of_scope
3. Interface contract (inputs/outputs)
4. verification_commands
5. success_criteria
6. artifacts_to_write
</scaling_rules>

<task_packet_template>
<task id="TX" role="implementer|reviewer" model="haiku|sonnet|opus">
  <description>Single clear objective</description>
  <tools>Read, Edit, Bash, Grep</tools>
  <scope>
    <include>exact/file/paths.py</include>
    <exclude>files/to/avoid.py</exclude>
  </scope>
  <interface>
    <input>What this task receives</input>
    <output>What this task produces</output>
  </interface>
  <instructions>TDD steps...</instructions>
  <constraints>What NOT to do</constraints>
  <verification>
    <command>pytest path/</command>
  </verification>
  <success>Measurable criteria</success>
  <artifacts>
    <read>from/previous.md</read>
    <write>for/next.md</write>
  </artifacts>
</task>
</task_packet_template>
```

**Step 2: Create implementer agent** (3 min)

```markdown
---
name: implementer
description: Feature implementation with strict TDD
tools: Read, Edit, Bash, Grep, Glob
model: sonnet
---

You execute implementation tasks following strict TDD.

<task_packet>
$TASK_PACKET
</task_packet>

CRITICAL RULES:
1. Stay within files_in_scope - do NOT touch files in files_out_of_scope
2. Follow TDD: test first -> fail -> implement -> pass
3. Run ALL verification_commands before claiming done
4. Write ALL artifacts_to_write before completing
5. Call `uvx hyh task complete --id $TASK_ID` when done

Your context is ISOLATED. You don't know about other tasks.
Focus ONLY on your TaskPacket objectives.

<anti_overfitting>
Write general-purpose solutions. Do NOT:
- Hard-code values for specific test inputs
- Create solutions that only work for test cases
- Create helper scripts or workarounds
</anti_overfitting>
```

**Step 3: Create reviewer agent** (2 min)

```markdown
---
name: reviewer
description: Code review and verification (read-only)
tools: Read, Grep, Glob, Bash
model: haiku
---

You verify implementations WITHOUT modifying code.

<task_packet>
$TASK_PACKET
</task_packet>

Review checklist:
1. Hard-coded values that only satisfy tests?
2. Missing edge case handling?
3. Security vulnerabilities?
4. Pattern consistency with codebase?
5. Interface contracts honored?

Report format:
<verification>
  <status>PASS|FAIL</status>
  <issues>
    <issue severity="high|medium|low">Description</issue>
  </issues>
</verification>

Write report to artifacts_to_write path.
```

**Step 4: Commit** (30 sec)

```bash
git add src/hyh/templates/agents/
git commit -m "$(cat <<'EOF'
feat(agents): add orchestrator, implementer, reviewer agent definitions

Orchestrator coordinates with scaling rules. Implementer follows TDD
with anti-overfitting guards. Reviewer does read-only verification.
EOF
)"
```

---

### Task 9: Code Review

**Files:**
- All modified files from Tasks 1-8

**Step 1: Review all changes** (5 min)

```bash
git diff master..HEAD --stat
git log master..HEAD --oneline
```

**Step 2: Run full test suite** (2 min)

```bash
make check
```

Expected: All checks pass

**Step 3: Verify enforcement layer** (3 min)

Check that:
- [ ] `task verify` validates artifacts exist
- [ ] `task remind` generates re-injection checklist
- [ ] Hook templates include Stop/SubagentStop prompts
- [ ] Agent definitions include scaling rules and anti-overfitting

**Step 4: Create summary** (2 min)

Document implementation and confirm all layers complete:
- Data Layer: TaskPacket, XMLPlanDefinition, XML parser
- Enforcement Layer: task verify, task remind, hook configs
- Orchestration Layer: orchestrator, implementer, reviewer agents

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATION LAYER                          │
│   .claude/agents/orchestrator.md (scaling rules, task spawning) │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ENFORCEMENT LAYER                            │
│   Stop hooks → task verify → artifact validation                │
│   SubagentStop → completion criteria check                      │
│   PostToolUse → continuous test prompts                         │
│   PreCompact → context-preserve                                 │
│   Re-injection → task remind                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DATA LAYER                                   │
│   TaskPacket (scope, tools, verification, artifacts)            │
│   XMLPlanDefinition (tasks + dependencies)                      │
│   Extended Task struct in daemon                                │
└─────────────────────────────────────────────────────────────────┘
```

This architecture addresses:
- **Premature termination**: Stop/SubagentStop hooks enforce completion criteria
- **Artifact coordination**: task verify validates artifacts before completion
- **Re-injection**: task remind provides checklist for long tasks
- **Context efficiency**: TaskPacket isolation, PreCompact preservation
- **Scaling**: Orchestrator with embedded scaling rules
