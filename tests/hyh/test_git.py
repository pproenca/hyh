# tests/hyh/test_git.py
"""Tests for git operations delegating to runtime.py."""

import subprocess
from unittest.mock import MagicMock, patch


def test_safe_git_exec_uses_runtime():
    """safe_git_exec should delegate to LocalRuntime."""
    from hyh.git import safe_git_exec

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.return_value = MagicMock(
            returncode=0,
            stdout="abc123\n",
            stderr="",
        )
        result = safe_git_exec(["rev-parse", "HEAD"], cwd="/tmp")

        mock_runtime.execute.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            cwd="/tmp",
            timeout=60,
            exclusive=True,
        )
        assert result.returncode == 0
        assert result.stdout == "abc123\n"


def test_safe_commit_uses_runtime():
    """safe_commit should delegate to LocalRuntime with external locking."""
    from hyh.git import safe_commit

    with patch("hyh.git._runtime") as mock_runtime:
        add_result = MagicMock(returncode=0, stdout="", stderr="")

        commit_result = MagicMock(returncode=0, stdout="", stderr="")
        mock_runtime.execute.side_effect = [add_result, commit_result]

        result = safe_commit(cwd="/tmp", message="test commit")

        assert mock_runtime.execute.call_count == 2
        calls = mock_runtime.execute.call_args_list

        # First call: git add -A (exclusive=False since lock is held externally)
        assert calls[0][0][0] == ["git", "add", "-A"]
        assert calls[0][1]["cwd"] == "/tmp"
        assert calls[0][1]["exclusive"] is False

        # Second call: git commit -m (exclusive=False since lock is held externally)
        assert calls[1][0][0] == ["git", "commit", "-m", "test commit"]
        assert calls[1][1]["cwd"] == "/tmp"
        assert calls[1][1]["exclusive"] is False

        assert result.returncode == 0


def test_global_git_lock_removed():
    """git module should NOT have GLOBAL_GIT_LOCK."""
    import hyh.git

    # GLOBAL_GIT_LOCK should not exist
    assert not hasattr(hyh.git, "GLOBAL_GIT_LOCK"), "GLOBAL_GIT_LOCK should be removed from git.py"


def test_uses_global_exec_lock():
    """Import GLOBAL_EXEC_LOCK from runtime."""

    import threading

    from hyh.runtime import GLOBAL_EXEC_LOCK

    assert isinstance(GLOBAL_EXEC_LOCK, type(threading.Lock()))


def test_git_uses_exclusive_locking():
    """Git write operations should use exclusive locking.

    Read operations can run in parallel; write operations must be serialized.
    """
    from hyh.git import safe_commit, safe_git_exec

    execute_calls: list[dict[str, object]] = []

    def mock_execute(
        command: list[str],
        cwd: str | None = None,
        timeout: int | None = None,
        exclusive: bool = False,
    ) -> MagicMock:
        execute_calls.append({"command": command, "exclusive": exclusive})
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.side_effect = mock_execute
        # Write operation: safe_commit holds lock externally
        safe_commit(cwd="/tmp", message="Test commit")
        # safe_commit should have made git add and git commit calls
        assert len(execute_calls) >= 2

        execute_calls.clear()

        # Read operation with explicit read_only=True
        safe_git_exec(["status"], cwd="/tmp", read_only=True)
        assert execute_calls[-1]["exclusive"] is False, "read_only=True should skip exclusive lock"

        # Default behavior (read_only=False) still locks
        safe_git_exec(["status"], cwd="/tmp")
        assert execute_calls[-1]["exclusive"] is True, (
            "Default read_only=False should use exclusive lock"
        )


def test_safe_commit_atomic_integration(tmp_path):
    """safe_commit should work end-to-end (integration test)."""

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

    (tmp_path / "test.txt").write_text("hello")

    from hyh.git import get_head_sha, safe_commit

    result = safe_commit(str(tmp_path), "test commit")
    assert result.returncode == 0

    sha = get_head_sha(str(tmp_path))
    assert sha is not None
    assert len(sha) == 40


def test_get_head_sha_handles_failure():
    """Verify get_head_sha returns None on failure."""
    from hyh.git import get_head_sha

    mock_result = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repository")

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.return_value = mock_result
        sha = get_head_sha("/tmp/not-a-repo")

    assert sha is None


def test_safe_commit_stages_and_commits():
    """Verify safe_commit calls git add and git commit."""
    from hyh.git import safe_commit

    calls: list[list[str]] = []

    def mock_execute(
        args: list[str],
        cwd: str,
        timeout: int | None = None,
        exclusive: bool = False,
    ) -> MagicMock:
        calls.append(args)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.side_effect = mock_execute
        safe_commit("/tmp/repo", "test commit message")

    assert ["git", "add", "-A"] in calls
    assert any("commit" in call and "-m" in call for call in calls)


def test_get_head_sha_returns_commit_hash():
    """Verify get_head_sha extracts commit hash from git output."""
    from hyh.git import get_head_sha

    mock_result = MagicMock(returncode=0, stdout="abc123def456\n", stderr="")

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.return_value = mock_result
        sha = get_head_sha("/tmp/repo")

    assert sha == "abc123def456"


def test_safe_git_exec_raises_on_failure():
    """Verify git failures are properly reported."""
    from hyh.git import safe_git_exec

    mock_result = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repository")

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.return_value = mock_result
        result = safe_git_exec(["status"], "/tmp")

    assert result.returncode == 128
    assert "not a git repository" in result.stderr


def test_safe_commit_is_atomic_single_lock():
    """safe_commit must hold lock for ENTIRE add+commit sequence (no race window).

    The fix is to hold GLOBAL_EXEC_LOCK externally and call execute with exclusive=False.
    This ensures no other thread can interleave between add and commit.
    """
    from hyh.git import safe_commit
    from hyh.runtime import GLOBAL_EXEC_LOCK

    execute_calls = []
    lock_held_during_calls = []

    def tracking_execute(command, cwd=None, timeout=None, exclusive=False):
        # Record if lock is held when execute is called
        # Try to acquire non-blocking - if it fails, lock is held
        lock_is_held = not GLOBAL_EXEC_LOCK.acquire(blocking=False)
        if not lock_is_held:
            GLOBAL_EXEC_LOCK.release()
        lock_held_during_calls.append(lock_is_held)
        execute_calls.append({"command": command, "exclusive": exclusive})
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.side_effect = tracking_execute

        safe_commit("/tmp", "test commit")

    assert len(execute_calls) == 2, f"Expected 2 execute calls, got {len(execute_calls)}"

    # Both calls should NOT use exclusive=True (lock is held externally)
    for call in execute_calls:
        assert call["exclusive"] is False, (
            f"Call {call} used exclusive=True, but lock should be held externally"
        )

    # Both calls should have seen the lock as held
    assert all(lock_held_during_calls), (
        f"Lock was not held during all calls: {lock_held_during_calls}. "
        f"This creates a race window between git add and git commit."
    )


def test_safe_git_exec_read_only_skips_lock():
    """safe_git_exec with read_only=True should NOT acquire GLOBAL_EXEC_LOCK.

    Bug: All git commands use exclusive=True, serializing parallel reads.
    Fix: Add read_only parameter, only lock on write operations.
    """
    from hyh.git import safe_git_exec

    execute_calls: list[dict[str, object]] = []

    def mock_execute(command: list[str], cwd: str, timeout: int, exclusive: bool) -> MagicMock:
        execute_calls.append({"command": command, "exclusive": exclusive})
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("hyh.git._runtime") as mock_runtime:
        mock_runtime.execute.side_effect = mock_execute
        # Read-only command should NOT use exclusive lock
        safe_git_exec(["status"], cwd="/tmp", read_only=True)
        assert execute_calls[-1]["exclusive"] is False, (
            "read_only=True should pass exclusive=False to skip GLOBAL_EXEC_LOCK"
        )

        # Write command should still use exclusive lock
        safe_git_exec(["commit", "-m", "test"], cwd="/tmp", read_only=False)
        assert execute_calls[-1]["exclusive"] is True, (
            "read_only=False (default) should pass exclusive=True"
        )
