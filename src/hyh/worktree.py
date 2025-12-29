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
