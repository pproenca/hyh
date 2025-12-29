"""Tests for bundled templates."""

from importlib.resources import files


def test_templates_exist():
    """Bundled templates are accessible."""
    templates = files("hyh") / "templates"

    assert (templates / "spec-template.md").is_file()
    assert (templates / "plan-template.md").is_file()
    assert (templates / "tasks-template.md").is_file()
    assert (templates / "checklist-template.md").is_file()


def test_spec_template_has_required_sections():
    """Spec template contains required sections."""
    content = (files("hyh") / "templates" / "spec-template.md").read_text()

    assert "## User Scenarios" in content or "User Scenarios" in content
    assert "## Requirements" in content or "Requirements" in content
    assert "## Success Criteria" in content or "Success Criteria" in content


def test_plan_template_has_required_sections():
    """Plan template contains required sections."""
    content = (files("hyh") / "templates" / "plan-template.md").read_text()

    assert "## Overview" in content
    assert "## Technical Context" in content
    assert "## Project Structure" in content


def test_tasks_template_has_required_sections():
    """Tasks template contains required sections."""
    content = (files("hyh") / "templates" / "tasks-template.md").read_text()

    assert "## Phase" in content or "Phase" in content
    assert "## Dependencies" in content or "Dependencies" in content


def test_checklist_template_has_required_sections():
    """Checklist template contains required sections."""
    content = (files("hyh") / "templates" / "checklist-template.md").read_text()

    assert "## Notes" in content or "Notes" in content
    assert "- [ ]" in content  # Has checkbox items
