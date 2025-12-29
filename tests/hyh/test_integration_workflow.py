"""Integration test for full hyh workflow."""

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
