# tests/harness/test_git.py
"""Tests for git operations delegating to runtime.py."""

import subprocess
from unittest.mock import patch, MagicMock


def test_safe_git_exec_uses_runtime():
    """safe_git_exec should delegate to LocalRuntime."""
    from harness.git import safe_git_exec

    with patch("harness.git._runtime") as mock_runtime:
        mock_runtime.execute.return_value = MagicMock(
            returncode=0,
            stdout="abc123\n",
            stderr="",
        )
        result = safe_git_exec(["rev-parse", "HEAD"], cwd="/tmp")

        # Verify delegation to runtime
        mock_runtime.execute.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            cwd="/tmp",
            timeout=60,
            exclusive=True,
        )
        assert result.returncode == 0
        assert result.stdout == "abc123\n"


def test_safe_commit_uses_runtime():
    """safe_commit should delegate to LocalRuntime."""
    from harness.git import safe_commit

    with patch("harness.git._runtime") as mock_runtime:
        # Mock successful add
        add_result = MagicMock(returncode=0, stdout="", stderr="")
        # Mock successful commit
        commit_result = MagicMock(returncode=0, stdout="", stderr="")
        mock_runtime.execute.side_effect = [add_result, commit_result]

        result = safe_commit(cwd="/tmp", message="test commit")

        # Verify both git add and git commit were called with exclusive=True
        assert mock_runtime.execute.call_count == 2
        calls = mock_runtime.execute.call_args_list

        # First call: git add -A
        assert calls[0][0][0] == ["git", "add", "-A"]
        assert calls[0][1]["cwd"] == "/tmp"
        assert calls[0][1]["exclusive"] is True

        # Second call: git commit -m
        assert calls[1][0][0] == ["git", "commit", "-m", "test commit"]
        assert calls[1][1]["cwd"] == "/tmp"
        assert calls[1][1]["exclusive"] is True

        assert result.returncode == 0


def test_global_git_lock_removed():
    """git module should NOT have GLOBAL_GIT_LOCK."""
    import harness.git

    # GLOBAL_GIT_LOCK should not exist
    assert not hasattr(harness.git, "GLOBAL_GIT_LOCK"), (
        "GLOBAL_GIT_LOCK should be removed from git.py"
    )


def test_uses_global_exec_lock():
    """Import GLOBAL_EXEC_LOCK from runtime."""
    from harness.runtime import GLOBAL_EXEC_LOCK

    # Just verify it exists and is a Lock
    import threading
    assert isinstance(GLOBAL_EXEC_LOCK, threading.Lock)


def test_git_uses_exclusive_locking(monkeypatch):
    """Git operations must pass exclusive=True."""
    from harness.git import safe_git_exec, safe_commit

    # Track calls to runtime.execute
    execute_calls = []

    def mock_execute(command, cwd=None, timeout=None, exclusive=False):
        execute_calls.append({
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
            "exclusive": exclusive,
        })
        return MagicMock(returncode=0, stdout="", stderr="")

    # Patch the runtime singleton
    with patch("harness.git._runtime") as mock_runtime:
        mock_runtime.execute = mock_execute

        # Test safe_git_exec
        safe_git_exec(["status"], cwd="/tmp")
        assert execute_calls[-1]["exclusive"] is True, (
            "safe_git_exec must use exclusive=True"
        )

        # Reset and test safe_commit
        execute_calls.clear()
        safe_commit(cwd="/tmp", message="test")

        # Both add and commit should use exclusive=True
        assert len(execute_calls) == 2
        assert execute_calls[0]["exclusive"] is True, (
            "git add must use exclusive=True"
        )
        assert execute_calls[1]["exclusive"] is True, (
            "git commit must use exclusive=True"
        )


def test_safe_commit_atomic_integration(tmp_path):
    """safe_commit should work end-to-end (integration test)."""
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True
    )

    # Create a file
    (tmp_path / "test.txt").write_text("hello")

    from harness.git import safe_commit, get_head_sha

    result = safe_commit(str(tmp_path), "test commit")
    assert result.returncode == 0

    sha = get_head_sha(str(tmp_path))
    assert sha is not None
    assert len(sha) == 40
