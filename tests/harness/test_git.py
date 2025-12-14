# tests/harness/test_git.py
"""Tests for thread-safe git operations with global mutex."""
import pytest
import threading
import time
from unittest.mock import patch, MagicMock


def test_git_execute_returns_result():
    """Git execute should return subprocess result."""
    from harness.git import safe_git_exec

    with patch("harness.git.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="abc123\n",
            stderr="",
        )
        result = safe_git_exec(["rev-parse", "HEAD"], cwd="/tmp")
        assert result.returncode == 0
        assert "abc123" in result.stdout


def test_global_git_lock_serializes_operations():
    """Multiple git operations should not overlap (Python 3.13t free-threading)."""
    from harness.git import safe_git_exec, GLOBAL_GIT_LOCK

    execution_order = []

    def mock_git(*args, **kwargs):
        thread_name = threading.current_thread().name
        execution_order.append(f"start-{thread_name}")
        time.sleep(0.05)  # Simulate git operation
        execution_order.append(f"end-{thread_name}")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.git.subprocess.run", side_effect=mock_git):
        threads = []
        for i in range(3):
            t = threading.Thread(
                target=lambda: safe_git_exec(["status"], cwd="/tmp"),
                name=f"thread-{i}",
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # Verify serialization: each start followed by its end before next start
    for i in range(0, len(execution_order), 2):
        start = execution_order[i]
        end = execution_order[i + 1]
        thread_name = start.replace("start-", "")
        assert end == f"end-{thread_name}", f"Operations overlapped! Order: {execution_order}"


def test_safe_commit_atomic(tmp_path):
    """safe_commit should be atomic add + commit."""
    import subprocess

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True
    )

    # Create a file
    (tmp_path / "test.txt").write_text("hello")

    from harness.git import safe_commit, get_head_sha

    result = safe_commit(str(tmp_path), "test commit")
    assert result.returncode == 0

    sha = get_head_sha(str(tmp_path))
    assert sha is not None
    assert len(sha) == 40
