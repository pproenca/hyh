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
