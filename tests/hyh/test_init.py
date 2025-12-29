"""Tests for hyh init command."""

from pathlib import Path


def test_init_creates_plugin_directory(tmp_path: Path):
    """hyh init creates .claude/plugins/hyh/ structure."""
    from hyh.init import init_project

    init_project(tmp_path)

    plugin_dir = tmp_path / ".claude" / "plugins" / "hyh"
    assert plugin_dir.exists()
    assert (plugin_dir / "plugin.json").exists()
    assert (plugin_dir / "commands" / "hyh.md").exists()
    assert (plugin_dir / "hooks" / "hooks.json").exists()


def test_init_creates_hyh_directory(tmp_path: Path):
    """hyh init creates .hyh/ with config and templates."""
    from hyh.init import init_project

    init_project(tmp_path)

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


def test_init_returns_init_result(tmp_path: Path):
    """init_project returns InitResult with correct paths."""
    from hyh.init import InitResult, init_project

    result = init_project(tmp_path)

    assert isinstance(result, InitResult)
    assert result.project_root == tmp_path.resolve()
    assert result.plugin_dir == tmp_path / ".claude" / "plugins" / "hyh"
    assert result.hyh_dir == tmp_path / ".hyh"


def test_init_copies_all_templates(tmp_path: Path):
    """init_project copies all template files."""
    from hyh.init import init_project

    init_project(tmp_path)

    templates_dir = tmp_path / ".hyh" / "templates"
    assert (templates_dir / "spec-template.md").exists()
    assert (templates_dir / "plan-template.md").exists()
    assert (templates_dir / "tasks-template.md").exists()
    assert (templates_dir / "checklist-template.md").exists()


def test_init_copies_skills(tmp_path: Path):
    """init_project copies skills directory."""
    from hyh.init import init_project

    init_project(tmp_path)

    skills_dir = tmp_path / ".claude" / "plugins" / "hyh" / "skills"
    assert skills_dir.exists()
    assert (skills_dir / "spec-driven-dev.md").exists()


def test_init_idempotent(tmp_path: Path):
    """Running init twice does not fail."""
    from hyh.init import init_project

    result1 = init_project(tmp_path)
    result2 = init_project(tmp_path)

    assert result1.plugin_dir == result2.plugin_dir
    assert result1.hyh_dir == result2.hyh_dir


def test_init_detects_main_branch_from_git(tmp_path: Path):
    """init_project detects main branch when in git repo."""
    import subprocess

    from hyh.init import init_project

    # Initialize a git repo with 'master' branch
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    # Create initial commit to establish branch
    (tmp_path / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        capture_output=True,
    )

    result = init_project(tmp_path)

    # Should detect master (default git branch name)
    assert result.main_branch in ("main", "master")
