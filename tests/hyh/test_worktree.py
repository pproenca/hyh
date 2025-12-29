"""Tests for git worktree management (DHH-style)."""

import subprocess
from pathlib import Path


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
