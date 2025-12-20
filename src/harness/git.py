# src/harness/git.py
"""
Thread-safe git operations delegating to runtime.py.

Git operations use exclusive=True to protect .git/index
across ALL parallel threads without GIL contention.
"""

from .runtime import ExecutionResult, LocalRuntime

# Singleton runtime instance
_runtime = LocalRuntime()

# Dangerous git options that could enable command injection
# These options can execute arbitrary commands or expose sensitive data
_DANGEROUS_OPTIONS = frozenset(
    {
        "-c",  # Can set core.pager, core.editor, etc. to arbitrary commands
        "--config",  # Same as -c
        "--upload-pack",  # Executes specified program
        "--exec",  # Executes command
        "-u",  # Short for --upload-pack in some contexts
        "--receive-pack",  # Executes specified program
    }
)

# Dangerous option prefixes (options that take values with =)
_DANGEROUS_PREFIXES = (
    "-c=",
    "--config=",
    "--upload-pack=",
    "--exec=",
    "--receive-pack=",
)


def _validate_git_args(args: list[str]) -> None:
    """Validate git arguments to prevent command injection.

    Raises:
        ValueError: If dangerous options are detected
    """
    for arg in args:
        # Check exact matches
        if arg in _DANGEROUS_OPTIONS:
            raise ValueError(
                f"Dangerous git option '{arg}' is not allowed. "
                "This option could enable command injection."
            )
        # Check prefixes (e.g., -c=value, --upload-pack=cmd)
        for prefix in _DANGEROUS_PREFIXES:
            if arg.startswith(prefix):
                raise ValueError(
                    f"Dangerous git option '{prefix.rstrip('=')}' is not allowed. "
                    "This option could enable command injection."
                )


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

    Raises:
        ValueError: If dangerous git options are detected
    """
    _validate_git_args(args)

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
