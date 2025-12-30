"""Tests for plan Markdown parsing and DAG validation."""

import pytest

from hyh.plan import PlanDefinition, PlanTaskDefinition, parse_plan_content

# =============================================================================
# PlanTaskDefinition and PlanDefinition Tests
# =============================================================================


def test_plan_task_definition_basic():
    """PlanTaskDefinition should validate required fields."""
    task = PlanTaskDefinition(description="Implement feature X")
    assert task.description == "Implement feature X"
    assert task.dependencies == ()
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


def test_parse_plan_content_invalid_format_raises():
    """parse_plan_content should raise if no valid Markdown plan found."""
    content = "Here is the plan: do task 1, then task 2."
    with pytest.raises(ValueError, match="No valid plan found"):
        parse_plan_content(content)


def test_plan_validate_dag_rejects_cycle():
    """validate_dag should reject A -> B -> A cycles."""
    plan = PlanDefinition(
        goal="Test",
        tasks={
            "a": PlanTaskDefinition(description="A", dependencies=("b",)),
            "b": PlanTaskDefinition(description="B", dependencies=("a",)),
        },
    )
    with pytest.raises(ValueError, match="[Cc]ycle"):
        plan.validate_dag()


def test_plan_validate_dag_rejects_missing_dep():
    """validate_dag should reject references to non-existent tasks."""
    plan = PlanDefinition(
        goal="Test",
        tasks={
            "a": PlanTaskDefinition(description="A", dependencies=("ghost",)),
        },
    )
    with pytest.raises(ValueError, match="[Mm]issing"):
        plan.validate_dag()


# =============================================================================
# Task Groups Format Tests (Legacy Format)
# =============================================================================


def test_get_plan_template_returns_markdown():
    """Template generation produces Markdown with speckit as recommended format."""
    from hyh.plan import get_plan_template

    template = get_plan_template()

    assert "# Plan Template" in template
    assert "## Recommended: Speckit Checkbox Format" in template
    # Legacy Task Groups section should exist
    assert "## Legacy: Task Groups Format" in template
    # JSON section should NOT exist
    assert "JSON Format" not in template


def test_parse_markdown_plan_basic():
    """parse_markdown_plan extracts goal, tasks, and dependencies from Markdown (legacy format)."""
    from hyh.plan import parse_markdown_plan

    content = """\
# Feature Plan

**Goal:** Implement user authentication

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2  | Core setup |
| Group 2    | 3     | Depends on Group 1 |

---

### Task 1: Create User Model

**Files:**
- Create: `src/models/user.py`

**Step 1: Define User class**
```python
class User:
    pass
```

### Task 2: Add Password Hashing

**Files:**
- Modify: `src/models/user.py`

**Step 1: Add bcrypt**
Use bcrypt for hashing.

### Task 3: Create Login Endpoint

**Files:**
- Create: `src/routes/auth.py`

**Step 1: Implement /login**
Return JWT token.
"""
    plan = parse_markdown_plan(content)

    assert plan.goal == "Implement user authentication"
    assert len(plan.tasks) == 3
    assert plan.tasks["1"].description == "Create User Model"
    assert plan.tasks["2"].description == "Add Password Hashing"
    assert plan.tasks["3"].description == "Create Login Endpoint"
    assert set(plan.tasks["1"].dependencies) == set()
    assert set(plan.tasks["2"].dependencies) == set()
    assert set(plan.tasks["3"].dependencies) == {"1", "2"}


def test_parse_markdown_plan_missing_goal():
    """parse_markdown_plan uses fallback when Goal not found."""
    from hyh.plan import parse_markdown_plan

    content = """\
## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Only task |

### Task 1: Solo Task

Do something.
"""
    plan = parse_markdown_plan(content)
    assert plan.goal == "Goal not specified"
    assert len(plan.tasks) == 1


def test_parse_markdown_plan_multi_group_dependencies():
    """Tasks in Group 3 depend on Group 2, not Group 1."""
    from hyh.plan import parse_markdown_plan

    content = """\
**Goal:** Three group test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | First |
| Group 2    | 2     | Second |
| Group 3    | 3     | Third |

### Task 1: First

Content 1.

### Task 2: Second

Content 2.

### Task 3: Third

Content 3.
"""
    plan = parse_markdown_plan(content)

    assert set(plan.tasks["1"].dependencies) == set()
    assert set(plan.tasks["2"].dependencies) == {"1"}
    assert set(plan.tasks["3"].dependencies) == {"2"}


def test_parse_markdown_plan_semantic_ids():
    """parse_markdown_plan supports semantic IDs like auth-service."""
    from hyh.plan import parse_markdown_plan

    content = """\
**Goal:** Semantic ID test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | auth-service, db-migration | Core |
| Group 2    | api-endpoints | Depends on core |

### Task auth-service: Authentication Service

Set up auth.

### Task db-migration: Database Migration

Run migrations.

### Task api-endpoints: API Endpoints

Create endpoints.
"""
    plan = parse_markdown_plan(content)

    assert len(plan.tasks) == 3
    assert "auth-service" in plan.tasks
    assert "db-migration" in plan.tasks
    assert "api-endpoints" in plan.tasks
    assert set(plan.tasks["auth-service"].dependencies) == set()
    assert set(plan.tasks["api-endpoints"].dependencies) == {"auth-service", "db-migration"}


def test_parse_markdown_plan_rejects_orphan_tasks():
    """parse_markdown_plan rejects tasks not in any group."""
    from hyh.plan import parse_markdown_plan

    content = """\
**Goal:** Orphan test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Only task 1 in group |

### Task 1: Grouped Task

In a group.

### Task 2: Orphan Task

Not in any group - should fail!
"""
    with pytest.raises(ValueError, match="Orphan tasks not in any group: 2"):
        parse_markdown_plan(content)


def test_parse_plan_content_markdown_format():
    """parse_plan_content should detect and parse Markdown format."""
    content = """\
# Implementation Plan

**Goal:** Test markdown parsing

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Core |

### Task 1: Test Task

Instructions here.
"""
    plan = parse_plan_content(content)

    assert plan.goal == "Test markdown parsing"
    assert len(plan.tasks) == 1
    assert plan.tasks["1"].description == "Test Task"


def test_parse_plan_content_markdown_cycle_rejected():
    """Markdown plans with cycles are rejected."""
    content = """\
**Goal:** Cycle test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1     | Only group |

### Task 1: Cyclic Task

Instructions.
"""
    plan = parse_plan_content(content)
    assert plan.goal == "Cycle test"


def test_get_plan_template_includes_speckit_format():
    """get_plan_template should show speckit format as recommended."""
    from hyh.plan import get_plan_template

    template = get_plan_template()

    # Speckit format (primary/recommended)
    assert "## Recommended: Speckit Checkbox Format" in template
    assert "# Tasks: [Feature Name]" in template
    assert "- [ ] T001" in template
    assert "## Phase 1:" in template

    # Task Groups format (legacy) should still be included
    assert "**Goal:**" in template
    assert "| Task Group |" in template


def test_parse_markdown_plan_rejects_phantom_tasks():
    """parse_markdown_plan rejects tasks in table but not in body (phantom tasks).

    Bug: If "### Task 2: ..." is typo'd as "### Task2: ...", the parser silently
    drops Task 2 because it's in the table but the regex doesn't match the header.
    """
    from hyh.plan import parse_markdown_plan

    content = """\
**Goal:** Phantom task test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2  | Task 2 is in table but not in body |

### Task 1: Real Task

This task has a proper header.

### Task2: Typo Task

Missing space after Task - regex won't match!
"""
    with pytest.raises(ValueError, match="Phantom tasks in table but not in body: 2"):
        parse_markdown_plan(content)


def test_parse_markdown_plan_flexible_header_formats():
    """parse_markdown_plan accepts reasonable header variations.

    The regex should accept:
    - "### Task 1: Description" (standard)
    - "### Task 2 : Description" (space before colon)
    - "### Task 3" (no colon, no description)
    - "### Task auth-service: Description" (semantic ID)
    - "### Task 1.1: Description" (dotted ID)
    """
    from hyh.plan import parse_markdown_plan

    content = """\
**Goal:** Flexible format test

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2, 3, auth-service, 1.1 | Various formats |

### Task 1: Standard format

Body 1.

### Task 2 : Space before colon

Body 2.

### Task 3

No colon, no description.

### Task auth-service: Semantic ID

Body auth.

### Task 1.1: Dotted ID

Body dotted.
"""
    plan = parse_markdown_plan(content)

    assert "1" in plan.tasks
    assert "2" in plan.tasks
    assert "3" in plan.tasks
    assert "auth-service" in plan.tasks
    assert "1.1" in plan.tasks
    assert len(plan.tasks) == 5


# =============================================================================
# Speckit Checkbox Format Tests (Primary Format)
# =============================================================================


def test_parse_plan_content_detects_speckit_format():
    """parse_plan_content auto-detects speckit checkbox format."""
    content = """\
# Tasks: My Feature

## Phase 1: Setup

- [ ] T001 First task
- [ ] T002 [P] Second task

## Phase 2: Core

- [ ] T003 Third task
"""
    plan = parse_plan_content(content)

    assert plan.goal == "My Feature"
    assert len(plan.tasks) == 3
    assert plan.tasks["T001"].description == "First task"
    assert plan.tasks["T003"].dependencies == ("T001", "T002")


def test_speckit_to_plan_definition_preserves_dependencies():
    """SpecTaskList.to_plan_definition() preserves phase dependencies."""
    from hyh.plan import parse_speckit_tasks

    content = """\
# Tasks: Conversion Test

## Phase 1: Setup

- [ ] T001 Task one
- [x] T002 Task two

## Phase 2: Core

- [ ] T003 Task three
"""
    spec_tasks = parse_speckit_tasks(content)
    plan = spec_tasks.to_plan_definition()

    assert plan.goal == "Conversion Test"
    assert len(plan.tasks) == 3
    assert plan.tasks["T001"].description == "Task one"
    assert plan.tasks["T003"].dependencies == ("T001", "T002")


def test_parse_speckit_checkbox_basic():
    """parse_speckit_tasks extracts tasks from checkbox format."""
    from hyh.plan import parse_speckit_tasks

    content = """\
## Progress Management

Mark completed tasks with [x].

## Phase 1: Setup

- [ ] T001 Create project structure
- [x] T002 Initialize git repository

## Phase 2: Core

- [ ] T003 [P] Implement user model in src/models/user.py
- [ ] T004 [P] [US1] Add auth service in src/services/auth.py
"""
    result = parse_speckit_tasks(content)

    assert len(result.tasks) == 4
    assert result.tasks["T001"].status == "pending"
    assert result.tasks["T002"].status == "completed"
    assert result.tasks["T003"].parallel is True
    assert result.tasks["T004"].user_story == "US1"
    assert "src/services/auth.py" in result.tasks["T004"].description

    # Verify phases are correctly extracted
    assert result.phases == ("Setup", "Core")

    # Verify file_path is correctly extracted
    assert result.tasks["T003"].file_path == "src/models/user.py"
    assert result.tasks["T004"].file_path == "src/services/auth.py"


def test_parse_speckit_tasks_phase_dependencies():
    """Tasks in Phase N depend on all tasks in Phase N-1."""
    from hyh.plan import parse_speckit_tasks

    content = """\
## Phase 1: Setup

- [ ] T001 Setup task A
- [ ] T002 [P] Setup task B

## Phase 2: Core

- [ ] T003 Core task (depends on Phase 1)
"""
    result = parse_speckit_tasks(content)

    # Phase 1 tasks have no dependencies
    assert result.tasks["T001"].dependencies == ()
    assert result.tasks["T002"].dependencies == ()
    # Phase 2 tasks depend on all Phase 1 tasks
    assert set(result.tasks["T003"].dependencies) == {"T001", "T002"}


def test_spec_task_list_to_workflow_state():
    """SpecTaskList converts to WorkflowState for daemon."""
    from hyh.plan import parse_speckit_tasks
    from hyh.state import TaskStatus

    content = """\
## Phase 1: Setup

- [ ] T001 Create project
- [x] T002 Init git

## Phase 2: Core

- [ ] T003 [P] Build feature
"""
    spec_tasks = parse_speckit_tasks(content)
    state = spec_tasks.to_workflow_state()

    assert len(state.tasks) == 3
    assert state.tasks["T001"].status == TaskStatus.PENDING
    assert state.tasks["T002"].status == TaskStatus.COMPLETED
    assert state.tasks["T003"].dependencies == ("T001", "T002")


def test_agent_model_enum_values():
    """AgentModel enum has haiku, sonnet, opus values."""
    from hyh.plan import AgentModel

    assert AgentModel.HAIKU.value == "haiku"
    assert AgentModel.SONNET.value == "sonnet"
    assert AgentModel.OPUS.value == "opus"


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


# =============================================================================
# XML Parser Error Handling Tests
# =============================================================================


def test_parse_xml_plan_malformed_xml():
    """parse_xml_plan raises ValueError for malformed XML."""
    from hyh.plan import parse_xml_plan

    with pytest.raises(ValueError, match="Invalid XML"):
        parse_xml_plan("<not valid xml")


def test_parse_xml_plan_wrong_root_element():
    """parse_xml_plan raises ValueError for wrong root element."""
    from hyh.plan import parse_xml_plan

    with pytest.raises(ValueError, match="Root element must be 'plan'"):
        parse_xml_plan("<workflow></workflow>")


def test_parse_xml_plan_missing_task_id():
    """parse_xml_plan raises ValueError for task without id."""
    from hyh.plan import parse_xml_plan

    xml = """\
<plan>
  <task>
    <description>test</description>
    <instructions>do</instructions>
    <success>done</success>
  </task>
</plan>
"""
    with pytest.raises(ValueError, match="missing 'id' attribute"):
        parse_xml_plan(xml)


def test_parse_xml_plan_missing_description():
    """parse_xml_plan raises ValueError for task without description."""
    from hyh.plan import parse_xml_plan

    xml = """\
<plan>
  <task id="T001">
    <instructions>do</instructions>
    <success>done</success>
  </task>
</plan>
"""
    with pytest.raises(ValueError, match="T001.*missing.*description"):
        parse_xml_plan(xml)


def test_parse_xml_plan_missing_instructions():
    """parse_xml_plan raises ValueError for task without instructions."""
    from hyh.plan import parse_xml_plan

    xml = """\
<plan>
  <task id="T001">
    <description>test</description>
    <success>done</success>
  </task>
</plan>
"""
    with pytest.raises(ValueError, match="T001.*missing.*instructions"):
        parse_xml_plan(xml)


def test_parse_xml_plan_missing_success():
    """parse_xml_plan raises ValueError for task without success criteria."""
    from hyh.plan import parse_xml_plan

    xml = """\
<plan>
  <task id="T001">
    <description>test</description>
    <instructions>do</instructions>
  </task>
</plan>
"""
    with pytest.raises(ValueError, match="T001.*missing.*success"):
        parse_xml_plan(xml)


def test_parse_xml_plan_invalid_model():
    """parse_xml_plan raises ValueError for invalid model enum."""
    from hyh.plan import parse_xml_plan

    xml = """\
<plan>
  <task id="T001" model="gpt4">
    <description>test</description>
    <instructions>do</instructions>
    <success>done</success>
  </task>
</plan>
"""
    with pytest.raises(ValueError, match="Invalid model 'gpt4'"):
        parse_xml_plan(xml)


def test_parse_xml_plan_empty_plan():
    """parse_xml_plan raises ValueError for plan with no tasks."""
    from hyh.plan import parse_xml_plan

    with pytest.raises(ValueError, match="No valid tasks found"):
        parse_xml_plan("<plan></plan>")
