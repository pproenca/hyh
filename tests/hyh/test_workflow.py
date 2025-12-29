"""Tests for workflow state management."""

from pathlib import Path


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
