import re
from enum import Enum
from typing import Final
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from msgspec import Struct

from .state import Task, TaskStatus, WorkflowState, detect_cycle


class AgentModel(str, Enum):
    """Model tier for agent tasks."""

    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


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


_SAFE_TASK_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$")


def _get_element_text(elem: Element, tag: str, default: str = "") -> str:
    """Get text content of a child element, stripped."""
    child = elem.find(tag)
    return (child.text or "").strip() if child is not None else default


def _get_child_texts(elem: Element, parent_tag: str, child_tag: str) -> tuple[str, ...]:
    """Get tuple of text contents from nested child elements."""
    parent = elem.find(parent_tag)
    if parent is None:
        return ()
    return tuple((e.text or "").strip() for e in parent.findall(child_tag) if e.text)


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
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as e:
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
        except ValueError as e:
            raise ValueError(f"Invalid model '{model_str}' for task {task_id}") from e

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

        # Parse tools (comma-separated or individual elements)
        tools_elem = task_elem.find("tools")
        tools: tuple[str, ...] = ()
        if tools_elem is not None and tools_elem.text:
            tools = tuple(t.strip() for t in tools_elem.text.split(",") if t.strip())

        # Parse verification commands
        verification_commands = _get_child_texts(task_elem, "verification", "command")

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

        # Get required fields
        description = _get_element_text(task_elem, "description")
        instructions = _get_element_text(task_elem, "instructions")
        success_criteria = _get_element_text(task_elem, "success")

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
            constraints=_get_element_text(task_elem, "constraints"),
            tools=tools,
            verification_commands=verification_commands,
            success_criteria=success_criteria,
            artifacts_to_read=artifacts_to_read,
            artifacts_to_write=artifacts_to_write,
        )

    if not tasks:
        raise ValueError("No valid tasks found in XML plan")

    return XMLPlanDefinition(goal=goal, tasks=tasks, dependencies=dependencies)


_CHECKBOX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^- \[([ xX])\] (T\d+)(?: \[P\])?(?: \[([A-Z]+\d+)\])? (.+)$",
    re.MULTILINE,
)

_PHASE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^## Phase \d+: (.+)$")

# Extract goal from "# Tasks: Feature Name" or "# Feature Name" headers
_TITLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^# (?:Tasks:\s*)?(.+)$", re.MULTILINE)


def _validate_task_id(task_id: str) -> None:
    if not task_id:
        raise ValueError("Task ID cannot be empty")

    if not _SAFE_TASK_ID_PATTERN.match(task_id):
        raise ValueError(
            f"Invalid task ID '{task_id}': Task IDs must start with alphanumeric "
            "and contain only letters, digits, hyphens, underscores, and dots. "
            "Special characters like $, `, ;, |, etc. are not allowed."
        )


class _TaskData(Struct, frozen=True, forbid_unknown_fields=True):
    description: str
    instructions: str
    dependencies: tuple[str, ...]


class SpecTaskDefinition(Struct, frozen=True, forbid_unknown_fields=True, omit_defaults=True):
    """Task definition from speckit checkbox format."""

    description: str
    status: str = "pending"  # "pending" or "completed"
    parallel: bool = False
    user_story: str | None = None
    phase: str | None = None
    file_path: str | None = None
    dependencies: tuple[str, ...] = ()


class SpecTaskList(Struct, frozen=True, forbid_unknown_fields=True):
    """Parsed speckit tasks.md content."""

    tasks: dict[str, SpecTaskDefinition]
    phases: tuple[str, ...]
    goal: str = "Implementation tasks"

    def to_plan_definition(self) -> PlanDefinition:
        """Convert to PlanDefinition for daemon import."""
        plan_tasks = {}
        for tid, spec_task in self.tasks.items():
            plan_tasks[tid] = PlanTaskDefinition(
                description=spec_task.description,
                dependencies=spec_task.dependencies,
                timeout_seconds=600,
                instructions=None,
                role=None,
            )
        return PlanDefinition(goal=self.goal, tasks=plan_tasks)

    def to_workflow_state(self) -> WorkflowState:
        """Convert to WorkflowState for daemon execution."""
        tasks = {}
        for tid, spec_task in self.tasks.items():
            status = TaskStatus.COMPLETED if spec_task.status == "completed" else TaskStatus.PENDING
            tasks[tid] = Task(
                id=tid,
                description=spec_task.description,
                status=status,
                dependencies=spec_task.dependencies,
            )
        return WorkflowState(tasks=tasks)


class PlanTaskDefinition(Struct, frozen=True, forbid_unknown_fields=True, omit_defaults=True):
    description: str
    dependencies: tuple[str, ...] = ()
    timeout_seconds: int = 600
    instructions: str | None = None
    role: str | None = None


class PlanDefinition(Struct, frozen=True, forbid_unknown_fields=True):
    goal: str
    tasks: dict[str, PlanTaskDefinition]

    def validate_dag(self) -> None:
        for task_id, task in self.tasks.items():
            for dep in task.dependencies:
                if dep not in self.tasks:
                    raise ValueError(f"Missing dependency: {dep} (in {task_id})")

        graph = {task_id: task.dependencies for task_id, task in self.tasks.items()}
        if cycle_node := detect_cycle(graph):
            raise ValueError(f"Cycle detected at {cycle_node}")

    def to_workflow_state(self) -> WorkflowState:
        tasks = {
            tid: Task(
                id=tid,
                description=t.description,
                status=TaskStatus.PENDING,
                dependencies=t.dependencies,
                started_at=None,
                completed_at=None,
                claimed_by=None,
                timeout_seconds=t.timeout_seconds,
                instructions=t.instructions,
                role=t.role,
            )
            for tid, t in self.tasks.items()
        }
        return WorkflowState(tasks=tasks)


def parse_markdown_plan(content: str) -> PlanDefinition:
    goal_match = re.search(r"\*\*Goal:\*\*\s*(.+)", content)
    goal = goal_match.group(1).strip() if goal_match else "Goal not specified"

    group_pattern = r"\|\s*Group\s*(\d+)\s*\|\s*([\w\-\.,\s]+)\s*\|"
    groups: dict[int, list[str]] = {}

    for match in re.finditer(group_pattern, content):
        group_id = int(match.group(1))
        task_ids = [t.strip() for t in match.group(2).split(",") if t.strip()]
        for tid in task_ids:
            _validate_task_id(tid)
        groups[group_id] = task_ids

    task_pattern = r"^### Task\s+([\w\-\.]+)\s*(?::\s*(.*))?$"
    parts = re.split(task_pattern, content, flags=re.MULTILINE)

    tasks_data: dict[str, _TaskData] = {}

    for i in range(1, len(parts), 3):
        if i + 2 > len(parts):
            break
        t_id = parts[i].strip()
        t_desc = (parts[i + 1] or "").strip()
        t_body = parts[i + 2].strip()

        _validate_task_id(t_id)

        tasks_data[t_id] = _TaskData(
            description=t_desc if t_desc else f"Task {t_id}",
            instructions=t_body,
            dependencies=(),
        )

    # Build dependency mapping: task_id -> tuple of dependency task_ids
    task_dependencies: dict[str, tuple[str, ...]] = {}
    sorted_group_ids = sorted(groups.keys())
    for i, group_id in enumerate(sorted_group_ids):
        if i > 0:
            prev_group_id = sorted_group_ids[i - 1]
            prev_tasks = tuple(groups[prev_group_id])
            for t_id in groups[group_id]:
                task_dependencies[t_id] = prev_tasks

    all_grouped_tasks = {t for tasks in groups.values() for t in tasks}

    orphan_tasks = set(tasks_data.keys()) - all_grouped_tasks
    if orphan_tasks:
        raise ValueError(
            f"Orphan tasks not in any group: {', '.join(sorted(orphan_tasks))}. "
            "Add them to the Task Groups table."
        )

    phantom_tasks = all_grouped_tasks - set(tasks_data.keys())
    if phantom_tasks:
        raise ValueError(
            f"Phantom tasks in table but not in body: {', '.join(sorted(phantom_tasks))}. "
            "Check for typos in ### Task headers (missing space, wrong ID)."
        )

    final_tasks = {}
    for t_id, t_data in tasks_data.items():
        final_tasks[t_id] = PlanTaskDefinition(
            description=t_data.description,
            instructions=t_data.instructions,
            dependencies=task_dependencies.get(t_id, ()),
            timeout_seconds=600,
            role=None,
        )

    return PlanDefinition(goal=goal, tasks=final_tasks)


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
        '  1. XML: <?xml ...> or <plan goal="..."> (recommended)\n'
        "  2. Speckit: - [ ] T001 checkbox tasks\n"
        "  3. Task Groups: **Goal:** + | Task Group | table (legacy)\n"
        "Run 'hyh plan template' for format reference."
    )


def get_plan_template() -> str:
    return """\
# Plan Template

## Recommended: Speckit Checkbox Format

```markdown
# Tasks: [Feature Name]

## Phase 1: Setup

- [ ] T001 Create project structure
- [ ] T002 [P] Initialize configuration

## Phase 2: Core

- [ ] T003 Implement main feature
- [ ] T004 [P] [US1] Add user model in src/models/user.py

## Phase 3: Tests

- [ ] T005 Add integration tests
```

**Format:**
- `- [ ]` = pending, `- [x]` = completed
- `[P]` = can run in parallel (no file conflicts)
- `[US1]` = user story reference (optional)
- Task IDs: T001, T002, etc.
- Phase N tasks depend on ALL Phase N-1 tasks

---

## Legacy: Task Groups Format

```markdown
**Goal:** One sentence description

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1    | 1, 2  | Core (parallel) |
| Group 2    | 3     | Depends on 1 |

### Task 1: Create User Model

**Files:**
- Create: `src/models/user.py`

**Step 1: Write failing test**
...
```

**Dependency Rules (both formats):**
- Tasks in Group/Phase N depend on ALL tasks in Group/Phase N-1
- Tasks within the same group/phase are independent (can run in parallel)
"""


def parse_speckit_tasks(content: str) -> SpecTaskList:
    """Parse speckit checkbox format into task list.

    Format:
    # Tasks: Feature Name  (optional, used as goal)
    ## Phase N: Phase Name
    - [ ] T001 [P] [US1] Description with path/to/file.py
    - [x] T002 Completed task

    Markers:
    - [ ] = pending, [x] = completed
    - [P] = parallel (can run concurrently)
    - [US1] = user story reference

    Dependencies:
    - Tasks in Phase N automatically depend on ALL tasks in Phase N-1
    """
    # Extract goal from first # heading (e.g., "# Tasks: Feature Name" -> "Feature Name")
    title_match = _TITLE_PATTERN.search(content)
    goal = title_match.group(1).strip() if title_match else "Implementation tasks"

    tasks: dict[str, SpecTaskDefinition] = {}
    phases: list[str] = []
    phase_tasks: dict[str, list[str]] = {}
    current_phase: str | None = None

    for line in content.split("\n"):
        phase_match = _PHASE_PATTERN.match(line.strip())
        if phase_match:
            current_phase = phase_match.group(1)
            phases.append(current_phase)
            phase_tasks[current_phase] = []
            continue

        checkbox_match = _CHECKBOX_PATTERN.match(line.strip())
        if checkbox_match:
            check, task_id, user_story, description = checkbox_match.groups()
            parallel = "[P]" in line

            file_path = None
            path_match = re.search(r"(\S+\.\w+)$", description)
            if path_match:
                file_path = path_match.group(1)

            tasks[task_id] = SpecTaskDefinition(
                description=description.strip(),
                status="completed" if check.lower() == "x" else "pending",
                parallel=parallel,
                user_story=user_story,
                phase=current_phase,
                file_path=file_path,
            )

            # Track which tasks belong to which phase
            if current_phase is not None:
                phase_tasks[current_phase].append(task_id)

    # Set phase-based dependencies: tasks in Phase N depend on ALL tasks in Phase N-1
    for i, phase in enumerate(phases):
        if i > 0:
            prev_phase = phases[i - 1]
            prev_phase_task_ids = tuple(phase_tasks[prev_phase])
            for task_id in phase_tasks[phase]:
                # Replace task with updated dependencies
                old_task = tasks[task_id]
                tasks[task_id] = SpecTaskDefinition(
                    description=old_task.description,
                    status=old_task.status,
                    parallel=old_task.parallel,
                    user_story=old_task.user_story,
                    phase=old_task.phase,
                    file_path=old_task.file_path,
                    dependencies=prev_phase_task_ids,
                )

    return SpecTaskList(tasks=tasks, phases=tuple(phases), goal=goal)
