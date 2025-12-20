# src/harness/git.py
"""
Thread-safe git operations delegating to runtime.py.

Git operations use exclusive=True to protect .git/index
across ALL parallel threads without GIL contention.
"""

from .runtime import ExecutionResult, LocalRuntime

# Singleton runtime instance
_runtime = LocalRuntime()


def safe_git_exec(
    args: list[str],
    cwd: str,
    timeout: int = 60,
    read_only: bool = False,
) -> ExecutionResult:
    """
    Execute git command with optional exclusive locking via runtime.

    Blocking call is fine because we're in a ThreadingMixIn server.
    Other clients are handled by other threads while we wait.

    Args:
        args: Git command arguments (without 'git' prefix)
        cwd: Working directory for git command
        timeout: Command timeout in seconds
        read_only: If True, skip GLOBAL_EXEC_LOCK (for parallel reads)

    Returns:
        ExecutionResult with returncode, stdout, stderr
    """
    return _runtime.execute(
        ["git", *args],
        cwd=cwd,
        timeout=timeout,
        exclusive=not read_only,
    )


def safe_commit(cwd: str, message: str) -> ExecutionResult:
    """Atomic add + commit operation with exclusive locking.

    Holds lock for ENTIRE sequence to prevent race conditions where
    another thread could modify staging between add and commit.
    """
    from .runtime import GLOBAL_EXEC_LOCK

    with GLOBAL_EXEC_LOCK:
        # Stage all changes (no exclusive flag - we already hold the lock)
        add_result = _runtime.execute(
            ["git", "add", "-A"],
            cwd=cwd,
            exclusive=False,
        )
        if add_result.returncode != 0:
            return add_result

        # Commit (no exclusive flag - we already hold the lock)
        return _runtime.execute(
            ["git", "commit", "-m", message],
            cwd=cwd,
            exclusive=False,
        )


def get_head_sha(cwd: str) -> str | None:
    """Get current HEAD commit SHA."""
    result = safe_git_exec(["rev-parse", "HEAD"], cwd=cwd, read_only=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None
