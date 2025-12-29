"""Project initialization for hyh."""

import json
import subprocess
from importlib.resources import files
from pathlib import Path

from msgspec import Struct


class InitResult(Struct, frozen=True, forbid_unknown_fields=True):
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
    (plugin_dir / "plugin.json").write_text((plugin_source / "plugin.json").read_text())

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
    (hooks_dir / "hooks.json").write_text((plugin_source / "hooks" / "hooks.json").read_text())

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
    for template in [
        "spec-template.md",
        "plan-template.md",
        "tasks-template.md",
        "checklist-template.md",
    ]:
        src = templates_source / template
        if src.is_file():
            (templates_dir / template).write_text(src.read_text())

    return InitResult(
        project_root=project_root,
        plugin_dir=plugin_dir,
        hyh_dir=hyh_dir,
        main_branch=main_branch,
    )
