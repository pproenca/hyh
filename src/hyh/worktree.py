"""Git worktree management (DHH-style).

Pattern: ../project--branch as sibling directories.
See: https://gist.github.com/dhh/18575558fc5ee10f15b6cd3e108ed844
"""

import subprocess
from pathlib import Path

from msgspec import Struct


class WorktreeResult(Struct, frozen=True, forbid_unknown_fields=True):
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
