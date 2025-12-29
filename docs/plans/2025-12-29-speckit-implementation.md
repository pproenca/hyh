# Speckit Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform hyh into a spec-driven development workflow tool with Claude Code plugin integration and DHH-style git worktree management.

**Architecture:** Add three new modules (worktree.py, workflow.py, init.py) alongside updated plan.py parser. Bundle test-prompt templates and Claude Code plugin files. New CLI commands delegate to these modules.

**Tech Stack:** Python 3.13+, msgspec for structs, pytest for TDD, Claude Code plugin system (markdown commands, JSON hooks)

---

## Task 1: Add Speckit Checkbox Parser

**Files:**

- Modify: `src/hyh/plan.py`
- Test: `tests/hyh/test_plan.py`

**Step 1: Write the failing test for basic checkbox parsing**

Add to `tests/hyh/test_plan.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/hyh/test_plan.py::test_parse_speckit_checkbox_basic -v`
Expected: FAIL with "cannot import name 'parse_speckit_tasks'"

**Step 3: Write minimal implementation**

Add to `src/hyh/plan.py`:

```python
import re
from typing import Final

from msgspec import Struct


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


_CHECKBOX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^- \[([ xX])\] (T\d+)(?: \[P\])?(?: \[([A-Z]+\d+)\])? (.+)$"
)

_PHASE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^## Phase \d+: (.+)$"
)


def parse_speckit_tasks(content: str) -> SpecTaskList:
    """Parse speckit checkbox format into task list.

    Format:
    ## Phase N: Phase Name
    - [ ] T001 [P] [US1] Description with path/to/file.py
    - [x] T002 Completed task

    Markers:
    - [ ] = pending, [x] = completed
    - [P] = parallel (can run concurrently)
    - [US1] = user story reference
    """
    tasks: dict[str, SpecTaskDefinition] = {}
    phases: list[str] = []
    current_phase: str | None = None

    for line in content.split("\n"):
        # Check for phase header
        phase_match = _PHASE_PATTERN.match(line.strip())
        if phase_match:
            current_phase = phase_match.group(1)
            phases.append(current_phase)
            continue

        # Check for task checkbox
        checkbox_match = _CHECKBOX_PATTERN.match(line.strip())
        if checkbox_match:
            check, task_id, user_story, description = checkbox_match.groups()

            # Detect parallel marker
            parallel = "[P]" in line

            # Extract file path from description if present
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

    return SpecTaskList(tasks=tasks, phases=tuple(phases))
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/hyh/test_plan.py::test_parse_speckit_checkbox_basic -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "feat(plan): add speckit checkbox format parser"
```

---

## Task 2: Add Phase Dependencies to Speckit Parser

**Files:**

- Modify: `src/hyh/plan.py`
- Test: `tests/hyh/test_plan.py`

**Step 1: Write the failing test for phase dependencies**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/hyh/test_plan.py::test_parse_speckit_tasks_phase_dependencies -v`
Expected: FAIL (dependencies are empty)

**Step 3: Update implementation to track phase dependencies**

Update `parse_speckit_tasks` in `src/hyh/plan.py`:

```python
def parse_speckit_tasks(content: str) -> SpecTaskList:
    """Parse speckit checkbox format into task list with phase dependencies."""
    tasks: dict[str, SpecTaskDefinition] = {}
    phases: list[str] = []
    current_phase: str | None = None
    phase_tasks: dict[str, list[str]] = {}  # phase_name -> [task_ids]

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

            if current_phase:
                phase_tasks[current_phase].append(task_id)

    # Build dependencies: Phase N depends on Phase N-1
    for i, phase in enumerate(phases):
        if i == 0:
            continue  # First phase has no dependencies
        prev_phase = phases[i - 1]
        prev_task_ids = tuple(phase_tasks.get(prev_phase, []))

        for task_id in phase_tasks.get(phase, []):
            old_task = tasks[task_id]
            tasks[task_id] = SpecTaskDefinition(
                description=old_task.description,
                status=old_task.status,
                parallel=old_task.parallel,
                user_story=old_task.user_story,
                phase=old_task.phase,
                file_path=old_task.file_path,
                dependencies=prev_task_ids,
            )

    return SpecTaskList(tasks=tasks, phases=tuple(phases))
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/hyh/test_plan.py::test_parse_speckit_tasks_phase_dependencies -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "feat(plan): add phase-based dependencies to speckit parser"
```

---

## Task 3: Convert SpecTaskList to WorkflowState

**Files:**

- Modify: `src/hyh/plan.py`
- Test: `tests/hyh/test_plan.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/hyh/test_plan.py::test_spec_task_list_to_workflow_state -v`
Expected: FAIL with "SpecTaskList has no attribute 'to_workflow_state'"

**Step 3: Add to_workflow_state method**

Add to `SpecTaskList` in `src/hyh/plan.py`:

```python
from .state import Task, TaskStatus, WorkflowState


class SpecTaskList(Struct, frozen=True, forbid_unknown_fields=True):
    """Parsed speckit tasks.md content."""

    tasks: dict[str, SpecTaskDefinition]
    phases: tuple[str, ...]

    def to_workflow_state(self) -> WorkflowState:
        """Convert to WorkflowState for daemon execution."""
        from .state import Task, TaskStatus, WorkflowState

        tasks = {}
        for tid, spec_task in self.tasks.items():
            status = (
                TaskStatus.COMPLETED
                if spec_task.status == "completed"
                else TaskStatus.PENDING
            )
            tasks[tid] = Task(
                id=tid,
                description=spec_task.description,
                status=status,
                dependencies=spec_task.dependencies,
            )
        return WorkflowState(tasks=tasks)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/hyh/test_plan.py::test_spec_task_list_to_workflow_state -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/plan.py tests/hyh/test_plan.py
git commit -m "feat(plan): add SpecTaskList.to_workflow_state() conversion"
```

---

## Task 4: Create worktree.py Module

**Files:**

- Create: `src/hyh/worktree.py`
- Create: `tests/hyh/test_worktree.py`

**Step 1: Write the failing test for worktree creation**

Create `tests/hyh/test_worktree.py`:

```python
"""Tests for git worktree management (DHH-style)."""

import subprocess
from pathlib import Path

import pytest


def test_create_worktree_dhh_style(tmp_path: Path):
    """create_worktree creates sibling directory with branch."""
    from hyh.worktree import create_worktree

    # Setup: create a git repo
    main_repo = tmp_path / "myproject"
    main_repo.mkdir()
    subprocess.run(["git", "init"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    (main_repo / "README.md").write_text("# Project")
    subprocess.run(["git", "add", "-A"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )

    # Act
    result = create_worktree(main_repo, "42-user-auth")

    # Assert
    expected_path = tmp_path / "myproject--42-user-auth"
    assert result.worktree_path == expected_path
    assert expected_path.exists()
    assert (expected_path / "README.md").exists()

    # Verify branch was created
    branch_result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=expected_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert branch_result.stdout.strip() == "42-user-auth"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/hyh/test_worktree.py::test_create_worktree_dhh_style -v`
Expected: FAIL with "No module named 'hyh.worktree'"

**Step 3: Create minimal worktree.py**

Create `src/hyh/worktree.py`:

```python
"""Git worktree management (DHH-style).

Pattern: ../project--branch as sibling directories.
See: https://gist.github.com/dhh/18575558fc5ee10f15b6cd3e108ed844
"""

import subprocess
from pathlib import Path
from typing import Final

from msgspec import Struct


class WorktreeResult(Struct, frozen=True):
    """Result of worktree creation."""

    worktree_path: Path
    branch_name: str
    main_repo: Path


def create_worktree(main_repo: Path, branch_name: str) -> WorktreeResult:
    """Create a worktree with DHH-style naming.

    Creates: ../{repo_name}--{branch_name}/
    Branch: {branch_name}

    Args:
        main_repo: Path to the main repository.
        branch_name: Name for both branch and worktree suffix.

    Returns:
        WorktreeResult with paths.

    Raises:
        subprocess.CalledProcessError: If git commands fail.
    """
    main_repo = Path(main_repo).resolve()
    repo_name = main_repo.name
    worktree_path = main_repo.parent / f"{repo_name}--{branch_name}"

    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )

    return WorktreeResult(
        worktree_path=worktree_path,
        branch_name=branch_name,
        main_repo=main_repo,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/hyh/test_worktree.py::test_create_worktree_dhh_style -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/worktree.py tests/hyh/test_worktree.py
git commit -m "feat(worktree): add DHH-style git worktree creation"
```

---

## Task 5: Add Worktree List and Switch Functions

**Files:**

- Modify: `src/hyh/worktree.py`
- Modify: `tests/hyh/test_worktree.py`

**Step 1: Write tests for list and switch**

Add to `tests/hyh/test_worktree.py`:

```python
def test_list_worktrees(tmp_path: Path):
    """list_worktrees returns all DHH-style worktrees."""
    from hyh.worktree import create_worktree, list_worktrees

    # Setup main repo
    main_repo = tmp_path / "myproject"
    main_repo.mkdir()
    subprocess.run(["git", "init"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    (main_repo / "README.md").write_text("# Project")
    subprocess.run(["git", "add", "-A"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )

    # Create two worktrees
    create_worktree(main_repo, "42-feature-a")
    create_worktree(main_repo, "43-feature-b")

    # Act
    worktrees = list_worktrees(main_repo)

    # Assert
    assert len(worktrees) == 2
    branches = {wt.branch_name for wt in worktrees}
    assert branches == {"42-feature-a", "43-feature-b"}


def test_get_worktree_for_branch(tmp_path: Path):
    """get_worktree returns path for a specific branch."""
    from hyh.worktree import create_worktree, get_worktree

    # Setup
    main_repo = tmp_path / "myproject"
    main_repo.mkdir()
    subprocess.run(["git", "init"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    (main_repo / "README.md").write_text("# Project")
    subprocess.run(["git", "add", "-A"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    create_worktree(main_repo, "42-user-auth")

    # Act
    result = get_worktree(main_repo, "42-user-auth")

    # Assert
    assert result is not None
    assert result.branch_name == "42-user-auth"
    assert result.worktree_path == tmp_path / "myproject--42-user-auth"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/hyh/test_worktree.py -v -k "list_worktrees or get_worktree"`
Expected: FAIL with "cannot import name 'list_worktrees'"

**Step 3: Add list_worktrees and get_worktree**

Add to `src/hyh/worktree.py`:

```python
def list_worktrees(main_repo: Path) -> list[WorktreeResult]:
    """List all DHH-style worktrees for a repository.

    Args:
        main_repo: Path to the main repository.

    Returns:
        List of WorktreeResult for each worktree.
    """
    main_repo = Path(main_repo).resolve()
    repo_name = main_repo.name
    prefix = f"{repo_name}--"

    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=main_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    worktrees: list[WorktreeResult] = []
    current_path: Path | None = None
    current_branch: str | None = None

    for line in result.stdout.split("\n"):
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
        elif line.startswith("branch refs/heads/"):
            current_branch = line.replace("branch refs/heads/", "")
        elif line == "" and current_path and current_branch:
            # Filter to DHH-style worktrees only
            if current_path.name.startswith(prefix):
                worktrees.append(
                    WorktreeResult(
                        worktree_path=current_path,
                        branch_name=current_branch,
                        main_repo=main_repo,
                    )
                )
            current_path = None
            current_branch = None

    return worktrees


def get_worktree(main_repo: Path, branch_name: str) -> WorktreeResult | None:
    """Get worktree for a specific branch.

    Args:
        main_repo: Path to the main repository.
        branch_name: Branch name to find.

    Returns:
        WorktreeResult if found, None otherwise.
    """
    worktrees = list_worktrees(main_repo)
    for wt in worktrees:
        if wt.branch_name == branch_name:
            return wt
    return None
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/hyh/test_worktree.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/worktree.py tests/hyh/test_worktree.py
git commit -m "feat(worktree): add list_worktrees and get_worktree functions"
```

---

## Task 6: Add Worktree CLI Commands

**Files:**

- Modify: `src/hyh/client.py`
- Test: `tests/hyh/test_worktree.py`

**Step 1: Write test for CLI worktree create**

Add to `tests/hyh/test_worktree.py`:

```python
def test_cli_worktree_create(tmp_path: Path, monkeypatch):
    """hyh worktree create creates worktree via CLI."""
    import sys
    from io import StringIO

    # Setup main repo
    main_repo = tmp_path / "myproject"
    main_repo.mkdir()
    subprocess.run(["git", "init"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )
    (main_repo / "README.md").write_text("# Project")
    subprocess.run(["git", "add", "-A"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=main_repo,
        capture_output=True,
        check=True,
    )

    # Mock cwd to main_repo
    monkeypatch.chdir(main_repo)
    monkeypatch.setenv("HYH_WORKTREE", str(main_repo))

    # Run CLI
    from hyh.client import main

    monkeypatch.setattr(sys, "argv", ["hyh", "worktree", "create", "42-feature"])

    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    main()

    # Verify
    expected_path = tmp_path / "myproject--42-feature"
    assert expected_path.exists()
    assert "Created" in stdout.getvalue() or "42-feature" in stdout.getvalue()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/hyh/test_worktree.py::test_cli_worktree_create -v`
Expected: FAIL with argument error (worktree subcommand doesn't exist)

**Step 3: Add worktree CLI commands to client.py**

Add to `src/hyh/client.py` in the argparse setup section:

```python
# Add after the existing subparsers

worktree_parser = subparsers.add_parser("worktree", help="Git worktree management")
worktree_sub = worktree_parser.add_subparsers(dest="worktree_command", required=True)

worktree_create = worktree_sub.add_parser("create", help="Create a new worktree")
worktree_create.add_argument("branch", help="Branch name (e.g., 42-user-auth)")

worktree_sub.add_parser("list", help="List all worktrees")

worktree_switch = worktree_sub.add_parser("switch", help="Show path to switch to worktree")
worktree_switch.add_argument("branch", help="Branch name to switch to")
```

Add the command handlers:

```python
def _cmd_worktree_create(branch: str) -> None:
    from hyh.worktree import create_worktree

    main_repo = Path(_get_git_root())
    result = create_worktree(main_repo, branch)
    print(f"Created worktree: {result.worktree_path}")
    print(f"Branch: {result.branch_name}")
    print(f"\nTo switch: cd {result.worktree_path}")


def _cmd_worktree_list() -> None:
    from hyh.worktree import list_worktrees

    main_repo = Path(_get_git_root())
    worktrees = list_worktrees(main_repo)

    if not worktrees:
        print("No worktrees found.")
        return

    print("Worktrees:")
    for wt in worktrees:
        print(f"  {wt.branch_name}: {wt.worktree_path}")


def _cmd_worktree_switch(branch: str) -> None:
    from hyh.worktree import get_worktree

    main_repo = Path(_get_git_root())
    wt = get_worktree(main_repo, branch)

    if wt is None:
        print(f"Worktree not found: {branch}", file=sys.stderr)
        sys.exit(1)

    print(f"cd {wt.worktree_path}")
```

Add to the match statement in `main()`:

```python
case "worktree":
    match args.worktree_command:
        case "create":
            _cmd_worktree_create(args.branch)
        case "list":
            _cmd_worktree_list()
        case "switch":
            _cmd_worktree_switch(args.branch)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/hyh/test_worktree.py::test_cli_worktree_create -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/client.py tests/hyh/test_worktree.py
git commit -m "feat(cli): add worktree create/list/switch commands"
```

---

## Task 7: Create Workflow State Module

**Files:**

- Create: `src/hyh/workflow.py`
- Create: `tests/hyh/test_workflow.py`

**Step 1: Write test for workflow state detection**

Create `tests/hyh/test_workflow.py`:

```python
"""Tests for workflow state management."""

from pathlib import Path

import pytest


def test_detect_workflow_phase_no_spec(tmp_path: Path):
    """detect_phase returns 'none' when no spec exists."""
    from hyh.workflow import detect_phase

    result = detect_phase(tmp_path)
    assert result.phase == "none"
    assert result.next_action == "specify"


def test_detect_workflow_phase_has_spec(tmp_path: Path):
    """detect_phase returns 'specify' when spec exists but no plan."""
    from hyh.workflow import detect_phase

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    (specs_dir / "spec.md").write_text("# Spec")

    result = detect_phase(tmp_path)
    assert result.phase == "specify"
    assert result.next_action == "plan"


def test_detect_workflow_phase_has_tasks(tmp_path: Path):
    """detect_phase returns 'plan' when tasks exist but not complete."""
    from hyh.workflow import detect_phase

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    (specs_dir / "spec.md").write_text("# Spec")
    (specs_dir / "plan.md").write_text("# Plan")
    (specs_dir / "tasks.md").write_text("""\
## Phase 1: Setup

- [ ] T001 Create project
- [ ] T002 Init git
""")

    result = detect_phase(tmp_path)
    assert result.phase == "plan"
    assert result.next_action == "implement"


def test_detect_workflow_phase_all_complete(tmp_path: Path):
    """detect_phase returns 'implement' when all tasks complete."""
    from hyh.workflow import detect_phase

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    (specs_dir / "spec.md").write_text("# Spec")
    (specs_dir / "plan.md").write_text("# Plan")
    (specs_dir / "tasks.md").write_text("""\
## Phase 1: Setup

- [x] T001 Create project
- [x] T002 Init git
""")

    result = detect_phase(tmp_path)
    assert result.phase == "complete"
    assert result.next_action is None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/hyh/test_workflow.py -v`
Expected: FAIL with "No module named 'hyh.workflow'"

**Step 3: Create workflow.py**

Create `src/hyh/workflow.py`:

```python
"""Workflow state detection and management."""

from pathlib import Path

from msgspec import Struct

from .plan import parse_speckit_tasks


class WorkflowPhase(Struct, frozen=True):
    """Current workflow phase and suggested next action."""

    phase: str  # "none", "specify", "plan", "implement", "complete"
    next_action: str | None  # "specify", "plan", "implement", None
    spec_exists: bool = False
    plan_exists: bool = False
    tasks_total: int = 0
    tasks_complete: int = 0


def detect_phase(worktree: Path) -> WorkflowPhase:
    """Detect current workflow phase based on artifacts.

    Args:
        worktree: Path to worktree root.

    Returns:
        WorkflowPhase with current state and suggested action.
    """
    worktree = Path(worktree)
    specs_dir = worktree / "specs"

    spec_path = specs_dir / "spec.md"
    plan_path = specs_dir / "plan.md"
    tasks_path = specs_dir / "tasks.md"

    spec_exists = spec_path.exists()
    plan_exists = plan_path.exists()
    tasks_total = 0
    tasks_complete = 0

    # No spec = nothing started
    if not spec_exists:
        return WorkflowPhase(
            phase="none",
            next_action="specify",
            spec_exists=False,
            plan_exists=False,
        )

    # Has spec but no plan
    if not plan_exists:
        return WorkflowPhase(
            phase="specify",
            next_action="plan",
            spec_exists=True,
            plan_exists=False,
        )

    # Has plan, check tasks
    if tasks_path.exists():
        content = tasks_path.read_text()
        task_list = parse_speckit_tasks(content)
        tasks_total = len(task_list.tasks)
        tasks_complete = sum(
            1 for t in task_list.tasks.values() if t.status == "completed"
        )

        if tasks_complete >= tasks_total and tasks_total > 0:
            return WorkflowPhase(
                phase="complete",
                next_action=None,
                spec_exists=True,
                plan_exists=True,
                tasks_total=tasks_total,
                tasks_complete=tasks_complete,
            )

    return WorkflowPhase(
        phase="plan",
        next_action="implement",
        spec_exists=True,
        plan_exists=True,
        tasks_total=tasks_total,
        tasks_complete=tasks_complete,
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/hyh/test_workflow.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/workflow.py tests/hyh/test_workflow.py
git commit -m "feat(workflow): add phase detection from artifacts"
```

---

## Task 8: Add Workflow CLI Commands

**Files:**

- Modify: `src/hyh/client.py`
- Modify: `tests/hyh/test_workflow.py`

**Step 1: Write test for workflow status CLI**

Add to `tests/hyh/test_workflow.py`:

```python
def test_cli_workflow_status(tmp_path: Path, monkeypatch):
    """hyh workflow status shows current phase."""
    import sys
    from io import StringIO

    # Setup with spec only
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    (specs_dir / "spec.md").write_text("# Spec")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HYH_WORKTREE", str(tmp_path))

    from hyh.client import main

    monkeypatch.setattr(sys, "argv", ["hyh", "workflow", "status"])

    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    main()

    output = stdout.getvalue()
    assert "specify" in output.lower() or "plan" in output.lower()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/hyh/test_workflow.py::test_cli_workflow_status -v`
Expected: FAIL (workflow subcommand doesn't exist)

**Step 3: Add workflow CLI commands**

Add to `src/hyh/client.py`:

```python
# Add to argparse setup
workflow_parser = subparsers.add_parser("workflow", help="Workflow state management")
workflow_sub = workflow_parser.add_subparsers(dest="workflow_command", required=True)

workflow_sub.add_parser("status", help="Show current workflow phase")
workflow_status = workflow_sub.add_parser("status", help="Show current workflow phase")
workflow_status.add_argument("--json", action="store_true", help="Output JSON")
workflow_status.add_argument("--quiet", action="store_true", help="Minimal output")
```

Add handlers:

```python
def _cmd_workflow_status(json_output: bool = False, quiet: bool = False) -> None:
    import json as json_module

    from hyh.workflow import detect_phase

    worktree = Path(_get_git_root())
    phase = detect_phase(worktree)

    if json_output:
        print(
            json_module.dumps(
                {
                    "phase": phase.phase,
                    "next_action": phase.next_action,
                    "spec_exists": phase.spec_exists,
                    "plan_exists": phase.plan_exists,
                    "tasks_total": phase.tasks_total,
                    "tasks_complete": phase.tasks_complete,
                }
            )
        )
        return

    if quiet:
        if phase.next_action:
            print(f"Next: /hyh {phase.next_action}")
        else:
            print("Complete")
        return

    print("=" * 50)
    print(" WORKFLOW STATUS")
    print("=" * 50)
    print()
    print(f" Phase:      {phase.phase}")
    print(f" Spec:       {'yes' if phase.spec_exists else 'no'}")
    print(f" Plan:       {'yes' if phase.plan_exists else 'no'}")

    if phase.tasks_total > 0:
        pct = int((phase.tasks_complete / phase.tasks_total) * 100)
        print(f" Tasks:      {phase.tasks_complete}/{phase.tasks_total} ({pct}%)")

    print()
    if phase.next_action:
        print(f" Next:       /hyh {phase.next_action}")
    else:
        print(" Status:     All tasks complete!")
    print()
```

Add to match statement:

```python
case "workflow":
    match args.workflow_command:
        case "status":
            _cmd_workflow_status(
                json_output=getattr(args, "json", False),
                quiet=getattr(args, "quiet", False),
            )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/hyh/test_workflow.py::test_cli_workflow_status -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/client.py tests/hyh/test_workflow.py
git commit -m "feat(cli): add workflow status command"
```

---

## Task 9: Bundle Templates from test-prompt

**Files:**

- Create: `src/hyh/templates/spec-template.md`
- Create: `src/hyh/templates/plan-template.md`
- Create: `src/hyh/templates/tasks-template.md`
- Create: `src/hyh/templates/checklist-template.md`
- Modify: `pyproject.toml`

**Step 1: Copy templates from test-prompt**

```bash
mkdir -p src/hyh/templates
cp test-prompt/.specify/templates/spec-template.md src/hyh/templates/
cp test-prompt/.specify/templates/plan-template.md src/hyh/templates/
cp test-prompt/.specify/templates/tasks-template.md src/hyh/templates/
cp test-prompt/.specify/templates/checklist-template.md src/hyh/templates/
```

**Step 2: Verify templates are included in package**

Add to `pyproject.toml` if needed for including non-Python files:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/hyh"]

[tool.hatch.build.targets.wheel.force-include]
"src/hyh/templates" = "hyh/templates"
```

**Step 3: Write test to verify templates are accessible**

Create `tests/hyh/test_templates.py`:

```python
"""Tests for bundled templates."""

from pathlib import Path

import pytest


def test_templates_exist():
    """Bundled templates are accessible."""
    from importlib.resources import files

    templates = files("hyh") / "templates"

    assert (templates / "spec-template.md").is_file()
    assert (templates / "plan-template.md").is_file()
    assert (templates / "tasks-template.md").is_file()
    assert (templates / "checklist-template.md").is_file()


def test_spec_template_has_required_sections():
    """Spec template contains required sections."""
    from importlib.resources import files

    content = (files("hyh") / "templates" / "spec-template.md").read_text()

    assert "## User Scenarios" in content or "User Scenarios" in content
    assert "## Requirements" in content or "Requirements" in content
    assert "## Success Criteria" in content or "Success Criteria" in content
```

**Step 4: Run tests**

Run: `pytest tests/hyh/test_templates.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hyh/templates/ pyproject.toml tests/hyh/test_templates.py
git commit -m "feat: bundle speckit templates with package"
```

---

## Task 10: Create Plugin Files Structure

**Files:**

- Create: `src/hyh/plugin/plugin.json`
- Create: `src/hyh/plugin/commands/hyh.md`
- Create: `src/hyh/plugin/commands/help.md`
- Create: `src/hyh/plugin/hooks/hooks.json`
- Create: `src/hyh/plugin/skills/spec-driven-dev.md`

**Step 1: Create plugin.json**

Create `src/hyh/plugin/plugin.json`:

```json
{
  "name": "hyh",
  "description": "Hold Your Horses - spec-driven development workflow",
  "version": "0.2.0",
  "author": {
    "name": "Pedro Proenca",
    "email": "pedro@10xengs.com"
  },
  "repository": "https://github.com/pproenca/hyh",
  "license": "MIT",
  "commands": ["./commands/"],
  "skills": ["./skills/"],
  "hooks": "./hooks/hooks.json"
}
```

**Step 2: Create main command file**

Create `src/hyh/plugin/commands/hyh.md`:

```markdown
---
description: Spec-driven development workflow - specify, plan, implement
argument-hint: [specify|plan|implement|status] [args]
allowed-tools: Bash(hyh:*), Bash(git:*), Read, Write, Edit, Glob, Grep
---

# hyh - Spec-Driven Development

Route based on $ARGUMENTS:

## If $ARGUMENTS starts with "specify"

Extract the feature description after "specify". Then:

1. Generate a slug from the description (2-4 words, kebab-case)
2. Get next feature number: `hyh workflow status --json` and increment
3. Create worktree: `hyh worktree create {N}-{slug}`
4. Load spec template and fill with user's description
5. Ask up to 5 clarifying questions (one at a time) for [NEEDS CLARIFICATION] markers
6. Write finalized spec to `specs/spec.md`
7. Report: "Spec complete. Run `/hyh plan` to continue."

## If $ARGUMENTS starts with "plan"

1. Verify `specs/spec.md` exists
2. Load spec and constitution (if `.hyh/constitution.md` exists)
3. Generate `specs/research.md` (resolve technical unknowns)
4. Generate `specs/plan.md` (architecture, tech stack)
5. Generate `specs/data-model.md` if entities involved
6. Generate `specs/tasks.md` in speckit checkbox format
7. Generate `specs/checklists/requirements.md`
8. Run consistency analysis
9. Import tasks: `hyh plan import --file specs/tasks.md`
10. Report: "Plan complete. Run `/hyh implement` to continue."

## If $ARGUMENTS starts with "implement"

1. Run: `hyh workflow status` to verify tasks exist
2. Check checklists pass (or ask to proceed)
3. Loop:
   a. `hyh task claim` → get next task
   b. If no task: done
   c. Execute task per instructions
   d. `hyh task complete --id {id}`
   e. Update specs/tasks.md with [x]
4. Report completion

## If $ARGUMENTS is empty or "status"

Run: `hyh workflow status`

Based on result, suggest next action:
- No spec? → "Start with: /hyh specify <your feature idea>"
- Has spec, no plan? → "Continue with: /hyh plan"
- Has tasks? → "Continue with: /hyh implement"
- All complete? → "All done! Ready to merge."
```

**Step 3: Create help command**

Create `src/hyh/plugin/commands/help.md`:

```markdown
---
description: Show hyh commands and current workflow state
---

# hyh Help

Display available commands and current state:

1. Run: `hyh workflow status`
2. Show this help:

## Commands

| Command | Description |
|---------|-------------|
| `/hyh specify <idea>` | Start new feature - creates worktree, generates spec |
| `/hyh plan` | Generate design artifacts and tasks from spec |
| `/hyh implement` | Execute tasks with daemon coordination |
| `/hyh status` | Show current workflow phase and progress |

## Workflow

```
specify → plan → implement → merge
   ↓        ↓         ↓
spec.md  tasks.md  working code
```

## Worktree Commands

| Command | Description |
|---------|-------------|
| `hyh worktree create <slug>` | Create new feature worktree |
| `hyh worktree list` | List all feature worktrees |
| `hyh worktree switch <slug>` | Show path to switch to worktree |
```

**Step 4: Create hooks.json**

Create `src/hyh/plugin/hooks/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "hyh workflow status --quiet"
          }
        ]
      }
    ]
  }
}
```

**Step 5: Create skill file**

Create `src/hyh/plugin/skills/spec-driven-dev.md`:

```markdown
---
name: spec-driven-development
description: Use when implementing features - follow the specify → plan → implement workflow
---

# Spec-Driven Development

When implementing any non-trivial feature, use the hyh workflow:

## 1. Specify First

Before writing code, create a specification:
- Run `/hyh specify <your idea>`
- Answer clarifying questions
- Review the generated spec.md

## 2. Plan Before Implementing

Generate design artifacts:
- Run `/hyh plan`
- Review tasks.md for the work breakdown
- Check checklists pass

## 3. Implement with Tracking

Execute tasks systematically:
- Run `/hyh implement`
- Tasks are tracked via daemon
- Progress is visible with `/hyh status`

## Why This Matters

- Specs catch misunderstandings early
- Plans break work into manageable pieces
- Tracking ensures nothing is forgotten
- Worktrees keep main branch clean
```

**Step 6: Commit**

```bash
git add src/hyh/plugin/
git commit -m "feat: add Claude Code plugin files"
```

---

## Task 11: Add hyh init Command

**Files:**

- Create: `src/hyh/init.py`
- Modify: `src/hyh/client.py`
- Create: `tests/hyh/test_init.py`

**Step 1: Write test for init command**

Create `tests/hyh/test_init.py`:

```python
"""Tests for hyh init command."""

from pathlib import Path

import pytest


def test_init_creates_plugin_directory(tmp_path: Path, monkeypatch):
    """hyh init creates .claude/plugins/hyh/ structure."""
    from hyh.init import init_project

    result = init_project(tmp_path)

    plugin_dir = tmp_path / ".claude" / "plugins" / "hyh"
    assert plugin_dir.exists()
    assert (plugin_dir / "plugin.json").exists()
    assert (plugin_dir / "commands" / "hyh.md").exists()
    assert (plugin_dir / "hooks" / "hooks.json").exists()


def test_init_creates_hyh_directory(tmp_path: Path):
    """hyh init creates .hyh/ with config and templates."""
    from hyh.init import init_project

    result = init_project(tmp_path)

    hyh_dir = tmp_path / ".hyh"
    assert hyh_dir.exists()
    assert (hyh_dir / "config.json").exists()
    assert (hyh_dir / "templates" / "spec-template.md").exists()


def test_init_config_has_required_fields(tmp_path: Path):
    """Config file has main_branch and next_feature_number."""
    import json

    from hyh.init import init_project

    init_project(tmp_path)

    config = json.loads((tmp_path / ".hyh" / "config.json").read_text())
    assert "main_branch" in config
    assert "next_feature_number" in config
    assert config["next_feature_number"] == 1
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/hyh/test_init.py -v`
Expected: FAIL with "No module named 'hyh.init'"

**Step 3: Create init.py**

Create `src/hyh/init.py`:

```python
"""Project initialization for hyh."""

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

from msgspec import Struct


class InitResult(Struct, frozen=True):
    """Result of project initialization."""

    project_root: Path
    plugin_dir: Path
    hyh_dir: Path
    main_branch: str


def _get_main_branch(project_root: Path) -> str:
    """Detect main branch name."""
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]

    # Fallback: check if main or master exists
    for branch in ["main", "master"]:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=project_root,
            capture_output=True,
        )
        if result.returncode == 0:
            return branch

    return "main"  # Default


def init_project(project_root: Path) -> InitResult:
    """Initialize hyh in a project.

    Creates:
    - .claude/plugins/hyh/ with plugin files
    - .hyh/ with config and templates

    Args:
        project_root: Path to project root.

    Returns:
        InitResult with created paths.
    """
    project_root = Path(project_root).resolve()

    # Create plugin directory
    plugin_dir = project_root / ".claude" / "plugins" / "hyh"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Copy plugin files from package
    plugin_source = files("hyh") / "plugin"

    # Copy plugin.json
    (plugin_dir / "plugin.json").write_text(
        (plugin_source / "plugin.json").read_text()
    )

    # Copy commands
    commands_dir = plugin_dir / "commands"
    commands_dir.mkdir(exist_ok=True)
    for cmd_file in ["hyh.md", "help.md"]:
        src = plugin_source / "commands" / cmd_file
        if src.is_file():
            (commands_dir / cmd_file).write_text(src.read_text())

    # Copy hooks
    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "hooks.json").write_text(
        (plugin_source / "hooks" / "hooks.json").read_text()
    )

    # Copy skills
    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    skills_src = plugin_source / "skills" / "spec-driven-dev.md"
    if skills_src.is_file():
        (skills_dir / "spec-driven-dev.md").write_text(skills_src.read_text())

    # Create .hyh directory
    hyh_dir = project_root / ".hyh"
    hyh_dir.mkdir(exist_ok=True)

    # Detect main branch
    main_branch = _get_main_branch(project_root)

    # Create config
    config = {
        "main_branch": main_branch,
        "next_feature_number": 1,
    }
    (hyh_dir / "config.json").write_text(json.dumps(config, indent=2))

    # Copy templates
    templates_dir = hyh_dir / "templates"
    templates_dir.mkdir(exist_ok=True)

    templates_source = files("hyh") / "templates"
    for template in ["spec-template.md", "plan-template.md", "tasks-template.md", "checklist-template.md"]:
        src = templates_source / template
        if src.is_file():
            (templates_dir / template).write_text(src.read_text())

    return InitResult(
        project_root=project_root,
        plugin_dir=plugin_dir,
        hyh_dir=hyh_dir,
        main_branch=main_branch,
    )
```

**Step 4: Add CLI command**

Add to `src/hyh/client.py`:

```python
# Add to argparse
subparsers.add_parser("init", help="Initialize hyh in current project")
```

Add handler:

```python
def _cmd_init() -> None:
    from hyh.init import init_project

    project_root = Path(_get_git_root())
    result = init_project(project_root)

    print("hyh initialized!")
    print()
    print(f"Plugin:    {result.plugin_dir}")
    print(f"Config:    {result.hyh_dir}")
    print(f"Branch:    {result.main_branch}")
    print()
    print("Next steps:")
    print("  1. Commit the .claude/ and .hyh/ directories")
    print("  2. In Claude Code, run: /hyh specify <your feature idea>")
```

Add to match:

```python
case "init":
    _cmd_init()
```

**Step 5: Run tests and commit**

Run: `pytest tests/hyh/test_init.py -v`
Expected: PASS

```bash
git add src/hyh/init.py src/hyh/client.py tests/hyh/test_init.py
git commit -m "feat: add hyh init command for project setup"
```

---

## Task 12: Integration Test - Full Workflow

**Files:**

- Create: `tests/hyh/test_integration_workflow.py`

**Step 1: Write integration test**

Create `tests/hyh/test_integration_workflow.py`:

```python
"""Integration test for full hyh workflow."""

import json
import subprocess
from pathlib import Path

import pytest


@pytest.mark.slow
def test_full_workflow_specify_to_implement(tmp_path: Path):
    """Test complete workflow from init through task execution."""
    # 1. Create git repo
    main_repo = tmp_path / "myproject"
    main_repo.mkdir()
    subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=main_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=main_repo,
        check=True,
        capture_output=True,
    )
    (main_repo / "README.md").write_text("# Project")
    subprocess.run(["git", "add", "-A"], cwd=main_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=main_repo,
        check=True,
        capture_output=True,
    )

    # 2. Init hyh
    from hyh.init import init_project

    init_project(main_repo)
    assert (main_repo / ".claude" / "plugins" / "hyh" / "plugin.json").exists()
    assert (main_repo / ".hyh" / "config.json").exists()

    # 3. Create worktree
    from hyh.worktree import create_worktree

    wt_result = create_worktree(main_repo, "1-test-feature")
    worktree = wt_result.worktree_path
    assert worktree.exists()

    # 4. Check workflow status (should be "none")
    from hyh.workflow import detect_phase

    phase = detect_phase(worktree)
    assert phase.phase == "none"
    assert phase.next_action == "specify"

    # 5. Create spec manually (simulating /hyh specify)
    specs_dir = worktree / "specs"
    specs_dir.mkdir()
    (specs_dir / "spec.md").write_text("# Test Feature Spec")

    phase = detect_phase(worktree)
    assert phase.phase == "specify"
    assert phase.next_action == "plan"

    # 6. Create plan and tasks (simulating /hyh plan)
    (specs_dir / "plan.md").write_text("# Implementation Plan")
    (specs_dir / "tasks.md").write_text("""\
## Phase 1: Setup

- [ ] T001 Create project structure
- [ ] T002 [P] Initialize configuration

## Phase 2: Core

- [ ] T003 Implement main feature
""")

    phase = detect_phase(worktree)
    assert phase.phase == "plan"
    assert phase.next_action == "implement"
    assert phase.tasks_total == 3
    assert phase.tasks_complete == 0

    # 7. Parse tasks and verify structure
    from hyh.plan import parse_speckit_tasks

    tasks = parse_speckit_tasks((specs_dir / "tasks.md").read_text())
    assert len(tasks.tasks) == 3
    assert tasks.tasks["T001"].phase == "Setup"
    assert tasks.tasks["T003"].dependencies == ("T001", "T002")

    # 8. Convert to workflow state
    state = tasks.to_workflow_state()
    assert len(state.tasks) == 3

    # 9. Simulate completion
    (specs_dir / "tasks.md").write_text("""\
## Phase 1: Setup

- [x] T001 Create project structure
- [x] T002 [P] Initialize configuration

## Phase 2: Core

- [x] T003 Implement main feature
""")

    phase = detect_phase(worktree)
    assert phase.phase == "complete"
    assert phase.next_action is None
```

**Step 2: Run integration test**

Run: `pytest tests/hyh/test_integration_workflow.py -v -m slow`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/hyh/test_integration_workflow.py
git commit -m "test: add full workflow integration test"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Speckit checkbox parser | plan.py |
| 2 | Phase dependencies | plan.py |
| 3 | SpecTaskList → WorkflowState | plan.py |
| 4 | worktree.py module | worktree.py |
| 5 | List/switch worktrees | worktree.py |
| 6 | Worktree CLI commands | client.py |
| 7 | Workflow state module | workflow.py |
| 8 | Workflow CLI commands | client.py |
| 9 | Bundle templates | templates/, pyproject.toml |
| 10 | Plugin files | plugin/ |
| 11 | hyh init command | init.py, client.py |
| 12 | Integration test | test_integration_workflow.py |

---

**Plan complete and saved to `docs/plans/2025-12-29-speckit-implementation.md`.**

**Two execution options:**

1. **Subagent-Driven (this session)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. **Parallel Session (separate)** - Open new Claude Code session in worktree, use `superpowers:executing-plans` for batch execution with checkpoints

**Which approach?**
