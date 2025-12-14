# src/harness/git.py
"""
Thread-safe git operations with global mutex.

In Python 3.13t (free-threading), this lock protects .git/index
across ALL parallel threads without GIL contention.
"""

import subprocess
import threading
from typing import List

# Global mutex for ALL git operations across all threads
# In Python 3.13t, threads run truly parallel - this lock is essential
GLOBAL_GIT_LOCK = threading.Lock()


def safe_git_exec(
    args: List[str],
    cwd: str,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """
    Execute git command with global mutex protection.

    Blocking call is fine because we're in a ThreadingMixIn server.
    Other clients are handled by other threads while we wait.

    Args:
        args: Git command arguments (without 'git' prefix)
        cwd: Working directory for git command
        timeout: Command timeout in seconds

    Returns:
        CompletedProcess with returncode, stdout, stderr
    """
    with GLOBAL_GIT_LOCK:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )


def safe_commit(cwd: str, message: str) -> subprocess.CompletedProcess:
    """Atomic add + commit operation under single lock acquisition."""
    with GLOBAL_GIT_LOCK:
        # Stage all changes
        add_result = subprocess.run(
            ["git", "add", "-A"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if add_result.returncode != 0:
            return add_result

        # Commit
        return subprocess.run(
            ["git", "commit", "-m", message],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )


def get_head_sha(cwd: str) -> str | None:
    """Get current HEAD commit SHA."""
    result = safe_git_exec(["rev-parse", "HEAD"], cwd=cwd)
    if result.returncode == 0:
        return result.stdout.strip()
    return None
