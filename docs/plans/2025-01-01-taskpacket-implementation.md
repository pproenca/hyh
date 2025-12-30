# TaskPacket Architecture Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-01-01-taskpacket-implementation.md` to implement task-by-task.

**Goal:** Implement TaskPacket architecture enabling agents to receive self-contained work packets via RPC, eliminating the need to load entire plans.

**Architecture:** Extend plan.py with TaskPacket struct and XML parser. Modify daemon RPC to return full TaskPacket on task claim. Add context preservation command for PreCompact hooks.

**Tech Stack:** Python 3.13+, msgspec for serialization, xml.etree.ElementTree for XML parsing, pytest for testing.

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Independent structs in plan.py, no file overlap with daemon |
| Group 2 | 3 | XML parser depends on TaskPacket struct from Task 1 |
| Group 3 | 4 | Daemon changes depend on TaskPacket and XML parser |
| Group 4 | 5 | CLI changes depend on daemon RPC |
| Group 5 | 6 | Code Review |

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
    """Model tier for agent tasks."""

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
and artifact declarations.
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
    from hyh.plan import AgentModel, TaskPacket, XMLPlanDefinition

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

**Step 3: Implement XMLPlanDefinition struct** (2 min)

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
            )
        return WorkflowState(tasks=state_tasks)

    def validate_dag(self) -> None:
        """Validate task dependencies form a valid DAG."""
        from .state import detect_cycle

        # Check all dependencies exist
        for task_id, deps in self.dependencies.items():
            if task_id not in self.tasks:
                raise ValueError(f"Dependency declared for unknown task: {task_id}")
            for dep in deps:
                if dep not in self.tasks:
                    raise ValueError(f"Missing dependency: {dep} (in {task_id})")

        # Check for cycles
        graph = {tid: self.dependencies.get(tid, ()) for tid in self.tasks}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Cycle detected at {cycle_node}")
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_xml_plan_definition_struct -v
```

Expected: PASS (1 passed)

**Step 5: Write failing test for to_workflow_state** (2 min)

```python
# tests/hyh/test_plan.py - add after previous test

def test_xml_plan_definition_to_workflow_state():
    """XMLPlanDefinition converts to WorkflowState correctly."""
    from hyh.plan import TaskPacket, XMLPlanDefinition
    from hyh.state import TaskStatus

    packet1 = TaskPacket(
        id="T001",
        description="First task",
        instructions="Do first",
        success_criteria="Done",
        role="implementer",
    )
    packet2 = TaskPacket(
        id="T002",
        description="Second task",
        instructions="Do second",
        success_criteria="Done",
    )

    plan = XMLPlanDefinition(
        goal="Test goal",
        tasks={"T001": packet1, "T002": packet2},
        dependencies={"T002": ("T001",)},
    )

    state = plan.to_workflow_state()

    assert len(state.tasks) == 2
    assert state.tasks["T001"].description == "First task"
    assert state.tasks["T001"].role == "implementer"
    assert state.tasks["T001"].status == TaskStatus.PENDING
    assert state.tasks["T002"].dependencies == ("T001",)
```

**Step 6: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_xml_plan_definition_to_workflow_state -v
```

Expected: PASS (1 passed) - implementation already included

**Step 7: Write failing test for validate_dag** (2 min)

```python
# tests/hyh/test_plan.py - add after previous test

def test_xml_plan_definition_validate_dag_missing_dep():
    """validate_dag raises on missing dependency."""
    import pytest

    from hyh.plan import TaskPacket, XMLPlanDefinition

    packet = TaskPacket(
        id="T001",
        description="Task",
        instructions="Do it",
        success_criteria="Done",
    )

    plan = XMLPlanDefinition(
        goal="Test",
        tasks={"T001": packet},
        dependencies={"T001": ("T999",)},  # T999 doesn't exist
    )

    with pytest.raises(ValueError, match="Missing dependency: T999"):
        plan.validate_dag()
```

**Step 8: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_xml_plan_definition_validate_dag_missing_dep -v
```

Expected: PASS (1 passed)

**Step 9: Commit** (30 sec)

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "$(cat <<'EOF'
feat(plan): add XMLPlanDefinition struct

Holds TaskPackets with explicit dependency graph. Converts to
WorkflowState for daemon execution. Validates DAG integrity.
EOF
)"
```

---

### Task 3: Implement XML Plan Parser

**Files:**
- Modify: `src/hyh/plan.py` (add parse_xml_plan function)
- Modify: `src/hyh/plan.py:199-225` (update parse_plan_content)
- Test: `tests/hyh/test_plan.py`

**Step 1: Write failing test for parse_xml_plan basic case** (3 min)

```python
# tests/hyh/test_plan.py - add after previous tests

def test_parse_xml_plan_basic():
    """parse_xml_plan parses minimal XML plan."""
    from hyh.plan import AgentModel, parse_xml_plan

    xml_content = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test feature">
  <task id="T001" role="implementer" model="sonnet">
    <description>Create service</description>
    <instructions>Write the code</instructions>
    <success>Tests pass</success>
  </task>
</plan>
"""

    plan = parse_xml_plan(xml_content)

    assert plan.goal == "Test feature"
    assert "T001" in plan.tasks
    assert plan.tasks["T001"].description == "Create service"
    assert plan.tasks["T001"].role == "implementer"
    assert plan.tasks["T001"].model == AgentModel.SONNET
    assert plan.tasks["T001"].instructions == "Write the code"
    assert plan.tasks["T001"].success_criteria == "Tests pass"
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_parse_xml_plan_basic -v
```

Expected: FAIL with `ImportError: cannot import name 'parse_xml_plan' from 'hyh.plan'`

**Step 3: Implement basic parse_xml_plan** (5 min)

Add to `src/hyh/plan.py` after XMLPlanDefinition:

```python
import xml.etree.ElementTree as ET


def parse_xml_plan(content: str) -> XMLPlanDefinition:
    """Parse XML plan format into XMLPlanDefinition with TaskPackets.

    Args:
        content: XML string containing plan definition

    Returns:
        XMLPlanDefinition with TaskPackets and dependencies

    Raises:
        ValueError: If XML is malformed or required fields are missing
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML: {e}") from e

    if root.tag != "plan":
        raise ValueError(f"Root element must be 'plan', got '{root.tag}'")

    goal = root.get("goal", "Goal not specified")

    # Parse dependencies section
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

        # Get model enum
        model_str = task_elem.get("model", "sonnet")
        try:
            model = AgentModel(model_str)
        except ValueError:
            raise ValueError(f"Invalid model '{model_str}' for task {task_id}")

        # Helper to get element text
        def get_text(tag: str, default: str = "") -> str:
            elem = task_elem.find(tag)
            return (elem.text or "").strip() if elem is not None else default

        # Helper to get tuple of texts
        def get_texts(parent_tag: str, child_tag: str) -> tuple[str, ...]:
            parent = task_elem.find(parent_tag)
            if parent is None:
                return ()
            return tuple(
                (e.text or "").strip()
                for e in parent.findall(child_tag)
                if e.text
            )

        # Parse scope
        scope_elem = task_elem.find("scope")
        files_in_scope: tuple[str, ...] = ()
        files_out_of_scope: tuple[str, ...] = ()
        if scope_elem is not None:
            files_in_scope = tuple(
                (e.text or "").strip()
                for e in scope_elem.findall("include")
                if e.text
            )
            files_out_of_scope = tuple(
                (e.text or "").strip()
                for e in scope_elem.findall("exclude")
                if e.text
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

        # Parse tools (comma-separated or individual elements)
        tools_elem = task_elem.find("tools")
        tools: tuple[str, ...] = ()
        if tools_elem is not None and tools_elem.text:
            tools = tuple(t.strip() for t in tools_elem.text.split(",") if t.strip())

        # Parse verification commands
        verification_commands = get_texts("verification", "command")

        # Parse artifacts
        artifacts_elem = task_elem.find("artifacts")
        artifacts_to_read: tuple[str, ...] = ()
        artifacts_to_write: tuple[str, ...] = ()
        if artifacts_elem is not None:
            artifacts_to_read = tuple(
                (e.text or "").strip()
                for e in artifacts_elem.findall("read")
                if e.text
            )
            artifacts_to_write = tuple(
                (e.text or "").strip()
                for e in artifacts_elem.findall("write")
                if e.text
            )

        # Get required fields
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
pytest tests/hyh/test_plan.py::test_parse_xml_plan_basic -v
```

Expected: PASS (1 passed)

**Step 5: Write failing test for full XML plan** (3 min)

```python
# tests/hyh/test_plan.py - add after previous test

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

    # Check goal
    assert plan.goal == "Implement authentication"

    # Check dependencies
    assert plan.dependencies["T002"] == ("T001",)

    # Check T001
    t001 = plan.tasks["T001"]
    assert t001.role == "implementer"
    assert t001.model == AgentModel.OPUS
    assert t001.files_in_scope == ("src/auth/token.py", "tests/auth/test_token.py")
    assert t001.files_out_of_scope == ("src/auth/session.py",)
    assert t001.input_context == "User credentials"
    assert t001.output_contract == "JWT token"
    assert "Write failing test" in t001.instructions
    assert t001.constraints == "Use existing jwt library"
    assert t001.tools == ("Read", "Edit", "Bash")
    assert t001.verification_commands == ("pytest tests/auth/", "ruff check src/auth/")
    assert t001.artifacts_to_write == (".claude/artifacts/T001-api.md",)

    # Check T002
    t002 = plan.tasks["T002"]
    assert t002.model == AgentModel.HAIKU
    assert t002.artifacts_to_read == (".claude/artifacts/T001-api.md",)
```

**Step 6: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_parse_xml_plan_full -v
```

Expected: PASS (1 passed)

**Step 7: Write failing test for parse_plan_content XML detection** (2 min)

```python
# tests/hyh/test_plan.py - add after previous test

def test_parse_plan_content_detects_xml():
    """parse_plan_content detects and parses XML format."""
    from hyh.plan import parse_plan_content

    xml_content = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test">
  <task id="T001">
    <description>Task one</description>
    <instructions>Do it</instructions>
    <success>Done</success>
  </task>
</plan>
"""

    plan = parse_plan_content(xml_content)

    assert plan.goal == "Test"
    assert "T001" in plan.tasks
```

**Step 8: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_parse_plan_content_detects_xml -v
```

Expected: FAIL with `ValueError: No valid plan found`

**Step 9: Update parse_plan_content for XML detection** (2 min)

In `src/hyh/plan.py`, update `parse_plan_content` function (around line 199) to add XML detection before the speckit check:

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

    # Format 1: Task Groups markdown (legacy)
    if "**Goal:**" in content and "| Task Group |" in content:
        plan = parse_markdown_plan(content)
        if not plan.tasks:
            raise ValueError("No valid plan found: no tasks defined in plan")
        plan.validate_dag()
        return plan

    # Format 2: Speckit checkbox format
    if _CHECKBOX_PATTERN.search(content):
        spec_tasks = parse_speckit_tasks(content)
        if not spec_tasks.tasks:
            raise ValueError("No valid plan found: no tasks defined in speckit format")
        plan = spec_tasks.to_plan_definition()
        plan.validate_dag()
        return plan

    raise ValueError(
        "No valid plan found. Supported formats:\n"
        "  1. XML: <?xml ...> or <plan goal=\"...\"> (recommended)\n"
        "  2. Speckit: - [ ] T001 checkbox tasks\n"
        "  3. Task Groups: **Goal:** + | Task Group | table (legacy)\n"
        "Run 'hyh plan template' for format reference."
    )
```

**Step 10: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_plan.py::test_parse_plan_content_detects_xml -v
```

Expected: PASS (1 passed)

**Step 11: Commit** (30 sec)

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "$(cat <<'EOF'
feat(plan): add XML plan parser

parse_xml_plan() parses XML format into XMLPlanDefinition with
TaskPackets. parse_plan_content() now auto-detects XML format.
Supports all TaskPacket fields including scope, tools, artifacts.
EOF
)"
```

---

### Task 4: Update Daemon to Return TaskPacket on Claim

**Files:**
- Modify: `src/hyh/daemon.py` (update TaskClaimData and handler)
- Modify: `src/hyh/state.py` (extend Task struct)
- Test: `tests/hyh/test_daemon.py`

**Step 1: Write failing test for extended Task fields** (3 min)

```python
# tests/hyh/test_state.py - add at end of file

def test_task_extended_fields():
    """Task supports TaskPacket-like extended fields."""
    from hyh.state import Task

    task = Task(
        id="T001",
        description="Test task",
        files_in_scope=("src/a.py", "src/b.py"),
        files_out_of_scope=("src/c.py",),
        input_context="Input data",
        output_contract="Output spec",
        constraints="No new deps",
        tools=("Read", "Edit"),
        verification_commands=("pytest",),
        success_criteria="Tests pass",
        artifacts_to_read=(),
        artifacts_to_write=(".claude/artifacts/T001.md",),
        model="sonnet",
    )

    assert task.files_in_scope == ("src/a.py", "src/b.py")
    assert task.tools == ("Read", "Edit")
    assert task.model == "sonnet"
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_state.py::test_task_extended_fields -v
```

Expected: FAIL with `TypeError: ... unexpected keyword argument 'files_in_scope'`

**Step 3: Extend Task struct with TaskPacket fields** (3 min)

In `src/hyh/state.py`, update the Task class (around line 67):

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

**Step 5: Update XMLPlanDefinition.to_workflow_state to include extended fields** (3 min)

Update `to_workflow_state` method in `src/hyh/plan.py` XMLPlanDefinition class:

```python
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
```

**Step 6: Write test for full TaskPacket in claim response** (3 min)

```python
# tests/hyh/test_daemon.py - add at end of file

def test_task_claim_returns_extended_fields(daemon_manager, worktree, socket_path):
    """task_claim returns full TaskPacket fields."""
    import json
    import socket

    from hyh.client import send_rpc

    # Import XML plan with full fields
    xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test">
  <task id="T001" role="implementer" model="opus">
    <description>Test task</description>
    <tools>Read, Edit</tools>
    <scope>
      <include>src/a.py</include>
    </scope>
    <instructions>Do the thing</instructions>
    <constraints>No new deps</constraints>
    <verification>
      <command>pytest</command>
    </verification>
    <success>Tests pass</success>
    <artifacts>
      <write>.claude/out.md</write>
    </artifacts>
  </task>
</plan>
"""

    with daemon_manager(worktree, socket_path):
        # Import plan
        send_rpc(socket_path, {"command": "plan_import", "content": xml_plan}, str(worktree))

        # Claim task
        response = send_rpc(
            socket_path,
            {"command": "task_claim", "worker_id": "test-worker"},
            str(worktree),
        )

        assert response["status"] == "ok"
        task = response["data"]["task"]

        # Verify extended fields are present
        assert task["role"] == "implementer"
        assert task["model"] == "opus"
        assert task["files_in_scope"] == ["src/a.py"]
        assert task["tools"] == ["Read", "Edit"]
        assert task["constraints"] == "No new deps"
        assert task["verification_commands"] == ["pytest"]
        assert task["success_criteria"] == "Tests pass"
        assert task["artifacts_to_write"] == [".claude/out.md"]
```

**Step 7: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_daemon.py::test_task_claim_returns_extended_fields -v
```

Expected: PASS (1 passed) - msgspec serializes all fields automatically

**Step 8: Commit** (30 sec)

```bash
git add src/hyh/state.py src/hyh/plan.py tests/hyh/test_state.py tests/hyh/test_daemon.py
git commit -m "$(cat <<'EOF'
feat(daemon): return full TaskPacket fields on task claim

Extended Task struct with all TaskPacket fields (scope, tools,
artifacts, etc). XMLPlanDefinition.to_workflow_state populates
all fields. task_claim now returns complete work packet.
EOF
)"
```

---

### Task 5: Add Context Preserve Command

**Files:**
- Modify: `src/hyh/client.py` (add context-preserve command)
- Modify: `src/hyh/daemon.py` (add handler)
- Test: `tests/hyh/test_client.py`

**Step 1: Write failing test for context_preserve RPC** (2 min)

```python
# tests/hyh/test_client.py - add at end of file

def test_context_preserve_writes_progress_file(daemon_manager, worktree, socket_path):
    """context_preserve command writes .claude/progress.txt."""
    from hyh.client import send_rpc

    # Import a plan first
    xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test feature">
  <task id="T001">
    <description>First task</description>
    <instructions>Do it</instructions>
    <success>Done</success>
  </task>
  <task id="T002">
    <description>Second task</description>
    <instructions>Do it too</instructions>
    <success>Done</success>
  </task>
</plan>
"""

    with daemon_manager(worktree, socket_path):
        send_rpc(socket_path, {"command": "plan_import", "content": xml_plan}, str(worktree))

        # Claim and complete T001
        send_rpc(socket_path, {"command": "task_claim", "worker_id": "w1"}, str(worktree))
        send_rpc(
            socket_path,
            {"command": "task_complete", "task_id": "T001", "worker_id": "w1"},
            str(worktree),
        )

        # Call context_preserve
        response = send_rpc(socket_path, {"command": "context_preserve"}, str(worktree))

        assert response["status"] == "ok"

        # Check progress file exists
        progress_file = worktree / ".claude" / "progress.txt"
        assert progress_file.exists()

        content = progress_file.read_text()
        assert "T001" in content
        assert "completed" in content.lower() or "Completed" in content
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_client.py::test_context_preserve_writes_progress_file -v
```

Expected: FAIL with error about unknown command

**Step 3: Add ContextPreserveRequest to daemon** (2 min)

In `src/hyh/daemon.py`, add after other request types (around line 120):

```python
class ContextPreserveRequest(Struct, tag="context_preserve", tag_field="command"):
    """Request to preserve context for PreCompact hook."""

    pass
```

**Step 4: Add handler for context_preserve** (5 min)

In `src/hyh/daemon.py`, add handler method to DaemonServer class:

```python
def _handle_context_preserve(
    self, _request: ContextPreserveRequest, server: DaemonServer
) -> Ok | Err:
    """Write current workflow state to .claude/progress.txt for PreCompact."""
    state = server.state_manager.load()
    if state is None:
        return Ok(data={"message": "No active workflow"})

    # Build progress summary
    tasks = state.tasks
    total = len(tasks)
    completed = sum(1 for t in tasks.values() if t.status == TaskStatus.COMPLETED)
    running = [t.id for t in tasks.values() if t.status == TaskStatus.RUNNING]
    pending = [t.id for t in tasks.values() if t.status == TaskStatus.PENDING]

    lines = [
        "## Current State",
        f"- Progress: {completed}/{total} tasks completed",
        f"- Running: {', '.join(running) if running else 'None'}",
        f"- Pending: {', '.join(pending[:5])}{'...' if len(pending) > 5 else ''}",
        "",
        "## Completed Tasks",
    ]

    for task in tasks.values():
        if task.status == TaskStatus.COMPLETED:
            lines.append(f"- {task.id}: {task.description}")

    # Write to progress file
    progress_dir = server.worktree_root / ".claude"
    progress_dir.mkdir(parents=True, exist_ok=True)
    progress_file = progress_dir / "progress.txt"
    progress_file.write_text("\n".join(lines))

    return Ok(data={"path": str(progress_file), "completed": completed, "total": total})
```

**Step 5: Add to request union and match statement** (2 min)

In `src/hyh/daemon.py`:

1. Add to Request union type (around line 130):
```python
Request: TypeAlias = (
    PingRequest
    | GetStateRequest
    | UpdateStateRequest
    | GitRequest
    | TaskClaimRequest
    | TaskCompleteRequest
    | PlanImportRequest
    | PlanResetRequest
    | ExecRequest
    | ShutdownRequest
    | StatusRequest
    | ContextPreserveRequest  # Add this
)
```

2. Add to match statement in handle_request (around line 290):
```python
case ContextPreserveRequest():
    result = self._handle_context_preserve(request, server)
```

**Step 6: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_client.py::test_context_preserve_writes_progress_file -v
```

Expected: PASS (1 passed)

**Step 7: Add CLI command for context preserve** (2 min)

In `src/hyh/client.py`, add subparser (around line 527):

```python
subparsers.add_parser("context-preserve", help="Write workflow state to progress file")
```

Add case to match statement (around line 645):

```python
case "context-preserve":
    _cmd_context_preserve(socket_path, worktree_root)
```

Add handler function:

```python
def _cmd_context_preserve(socket_path: str, worktree_root: str) -> None:
    response = send_rpc(socket_path, {"command": "context_preserve"}, worktree_root)
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    data = response["data"]
    if "path" in data:
        print(f"Progress saved to {data['path']}")
        print(f"Completed: {data['completed']}/{data['total']} tasks")
    else:
        print(data.get("message", "Done"))
```

**Step 8: Run all tests to verify nothing broke** (30 sec)

```bash
pytest tests/hyh/ -v --tb=short
```

Expected: All tests pass

**Step 9: Commit** (30 sec)

```bash
git add src/hyh/daemon.py src/hyh/client.py tests/hyh/test_client.py
git commit -m "$(cat <<'EOF'
feat(cli): add context-preserve command for PreCompact hook

Writes workflow progress to .claude/progress.txt for context
preservation during compaction. Shows completed/pending tasks
for session resumption.
EOF
)"
```

---

### Task 6: Code Review

**Files:**
- All modified files from Tasks 1-5

**Step 1: Review all changes** (5 min)

```bash
git diff master..HEAD --stat
git log master..HEAD --oneline
```

**Step 2: Run full test suite** (2 min)

```bash
make check
```

Expected: All checks pass (lint, typecheck, test)

**Step 3: Review for anti-patterns** (3 min)

Check for:
- [ ] Hard-coded values
- [ ] Missing edge cases
- [ ] Security issues
- [ ] Pattern consistency

**Step 4: Create summary** (2 min)

Document what was implemented and any follow-up items.

---

## Follow-Up Items (Not in This Plan)

1. **Custom agent definitions** - Create `.claude/agents/implementer.md` and `reviewer.md`
2. **Hook configurations** - Update `.claude/settings.json` with prompt-based hooks
3. **Plan template update** - Update `hyh plan template` to show XML format
4. **Documentation** - Update README with new XML plan format
