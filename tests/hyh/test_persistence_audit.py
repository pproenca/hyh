"""
Red Team Security Audit: Persistence and Atomicity Vulnerabilities.

These tests target file I/O bugs, atomicity issues, and persistence edge cases.

Tests focus on:
- Atomic file write races
- Directory fsync for crash safety
- Symlink attacks on socket/lock files
- Cache invalidation on external modifications
- File permission issues
"""

import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore


class TestAtomicFileWriteRace:
    """HIGH: Atomic file write race (state.py:221-230).

    Vulnerability: After renaming temp file, directory is not fsync'd,
    which could lead to data loss on crash.
    """

    def test_fsync_is_called_during_save(self) -> None:
        """Verify fsync is called for crash safety."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            fsync_called = []
            original_fsync = os.fsync

            def tracking_fsync(fd: int) -> None:
                fsync_called.append(fd)
                return original_fsync(fd)

            with patch("os.fsync", tracking_fsync):
                state = WorkflowState(
                    tasks={
                        "t1": Task(
                            id="t1", description="x", status=TaskStatus.PENDING, dependencies=[]
                        )
                    }
                )
                manager.save(state)

            # Verify at least one fsync was called (for the file)
            assert len(fsync_called) >= 1, "No fsync calls detected - crash safety compromised"

    def test_concurrent_save_no_corruption(self) -> None:
        """Concurrent saves should not corrupt state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            initial = WorkflowState(
                tasks={
                    "t1": Task(
                        id="t1", description="initial", status=TaskStatus.PENDING, dependencies=[]
                    )
                }
            )
            manager.save(initial)

            errors: list[tuple[int, Exception]] = []

            def save_state(version: int) -> None:
                try:
                    state = WorkflowState(
                        tasks={
                            "t1": Task(
                                id="t1",
                                description=f"v{version}",
                                status=TaskStatus.PENDING,
                                dependencies=[],
                            )
                        }
                    )
                    manager.save(state)
                except Exception as e:
                    errors.append((version, e))

            threads = [threading.Thread(target=save_state, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # File should be valid JSON
            content = manager.state_file.read_text()
            data = json.loads(content)  # Should not raise
            assert "tasks" in data
            assert "t1" in data["tasks"]

            # No errors during concurrent saves
            assert len(errors) == 0, f"Errors during concurrent save: {errors}"


class TestSymlinkAttacks:
    """MEDIUM: Symlink attacks on socket/lock files.

    Vulnerability: TOCTOU between checking if path exists and creating it.
    """

    def test_document_socket_symlink_vulnerability(self) -> None:
        """Document the symlink attack surface.

        Attack scenario:
        1. Attacker predicts socket path (based on worktree hash)
        2. Creates symlink at that path pointing to a sensitive file
        3. Daemon writes to socket, actually writes to sensitive file

        This test documents the vulnerability exists.
        Defense: Use O_NOFOLLOW or check for symlink before operations.
        """
        from hyh.client import get_socket_path

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree = Path(tmpdir) / "repo"
            worktree.mkdir()

            socket_path = get_socket_path(worktree)

            # This is predictable based on worktree hash
            # An attacker could pre-create a symlink here
            assert socket_path.endswith(".sock")

    def test_document_lock_file_vulnerability(self) -> None:
        """Document the lock file symlink attack surface.

        The lock file is {socket_path}.lock - same vulnerability applies.
        """
        from hyh.client import get_socket_path

        with tempfile.TemporaryDirectory() as tmpdir:
            worktree = Path(tmpdir) / "repo"
            worktree.mkdir()

            socket_path = get_socket_path(worktree)
            lock_path = socket_path + ".lock"

            # Lock file is also predictable
            assert lock_path.endswith(".lock")


class TestCacheInvalidation:
    """MEDIUM: Cache invalidation issues (state.py:195-219).

    Vulnerability: External file modifications are not detected.
    """

    def test_external_modification_cache_behavior(self) -> None:
        """Document cache behavior when file is externally modified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            state1 = WorkflowState(
                tasks={
                    "t1": Task(
                        id="t1", description="original", status=TaskStatus.PENDING, dependencies=[]
                    )
                }
            )
            manager.save(state1)

            loaded = manager.load()
            assert loaded is not None
            assert loaded.tasks["t1"].description == "original"

            manager.state_file.write_text(
                json.dumps(
                    {
                        "tasks": {
                            "t1": {
                                "id": "t1",
                                "description": "externally modified",
                                "status": "pending",
                                "dependencies": [],
                                "timeout_seconds": 600,
                            }
                        }
                    }
                )
            )

            # Load again - should see external changes
            loaded2 = manager.load()
            assert loaded2 is not None
            # This documents current behavior - load() re-reads the file
            assert loaded2.tasks["t1"].description == "externally modified"

    def test_claim_uses_cached_state(self) -> None:
        """Document that claim_task uses cached state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            state = WorkflowState(
                tasks={
                    "t1": Task(
                        id="t1", description="v1", status=TaskStatus.PENDING, dependencies=[]
                    )
                }
            )
            manager.save(state)
            manager.load()

            # Claim should use cached state
            result = manager.claim_task("worker-1")
            assert result.task is not None
            assert result.task.id == "t1"


class TestTrajectoryFilePermissions:
    """MEDIUM: Trajectory file permissions (trajectory.py:51).

    Vulnerability: Trajectory file created with 0o644 (world-readable).
    """

    def test_trajectory_file_created_with_permissions(self) -> None:
        """Check trajectory file permissions."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            traj_file = Path(tmpdir) / "trajectory.jsonl"
            logger = TrajectoryLogger(traj_file)

            logger.log({"event": "test"})

            file_stat = traj_file.stat()
            mode = stat.S_IMODE(file_stat.st_mode)

            # Document current behavior: 0o644 is world-readable
            # If trajectory contains sensitive data, this is a vulnerability
            # Ideal would be 0o600 or 0o640
            assert mode == 0o644, f"Unexpected permissions: {oct(mode)}"

    def test_trajectory_directory_permissions(self) -> None:
        """Check trajectory directory permissions."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            traj_file = Path(tmpdir) / "subdir" / "trajectory.jsonl"
            logger = TrajectoryLogger(traj_file)

            logger.log({"event": "test"})

            # Check parent directory exists
            assert traj_file.parent.exists()


class TestUnboundedJSONParsing:
    """MEDIUM: Unbounded JSON parsing (daemon.py:54-63).

    Vulnerability: No size limit on incoming JSON requests.
    """

    def test_document_unbounded_request_vulnerability(self) -> None:
        """Document that requests have no size limit.

        The vulnerability:
        def handle(self) -> None:
            line = self.rfile.readline()  # No size limit!
            request = json.loads(line)    # Parses entire request

        Attack: Send a multi-GB JSON line to exhaust server memory.

        Mitigation: Use readline(MAX_REQUEST_SIZE).
        """
        # This is a documentation test
        # Actual testing would require setting up a daemon
        pass


class TestUTF8Truncation:
    """MEDIUM: Unsafe UTF-8 truncation (daemon.py:307-308).

    Vulnerability: Truncating output at arbitrary position breaks UTF-8.
    """

    def test_utf8_truncation_at_byte_boundary(self) -> None:
        """Truncating at byte boundary can break multi-byte characters.

        Japanese "日" is 3 bytes: E6 97 A5
        Truncating at byte 1 or 2 creates invalid UTF-8.
        """
        test_string = "日本語" * 1000  # Multi-byte characters
        encoded = test_string.encode("utf-8")

        truncated = encoded[:10]  # Likely breaks a character

        # This shows the vulnerability - truncation may break UTF-8 encoding
        # Decoding with errors='replace' prevents crash but loses data
        # Suggested fix: Use text[:LIMIT] not bytes[:LIMIT]
        truncated.decode("utf-8", errors="replace")

    def test_proper_utf8_truncation(self) -> None:
        """Demonstrate proper UTF-8 truncation at character boundary."""
        test_string = "日本語テスト"

        # Proper truncation: limit characters, not bytes
        char_limit = 4
        properly_truncated = test_string[:char_limit]

        # Should always be valid
        assert len(properly_truncated) <= char_limit
        assert properly_truncated.encode("utf-8").decode("utf-8") == properly_truncated


class TestPartialJSONRecovery:
    """Tests for handling corrupt/partial JSON files."""

    def test_partial_json_raises_clear_error(self) -> None:
        """StateManager should raise clear error for partial JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))
            state_file = manager.state_file
            state_file.parent.mkdir(parents=True, exist_ok=True)

            state_file.write_text('{"tasks": {"t1":')

            # Load should raise clear error, not crash
            with pytest.raises((json.JSONDecodeError, ValueError)):
                manager.load()

    def test_empty_file_handling(self) -> None:
        """StateManager should handle empty file gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))
            state_file = manager.state_file
            state_file.parent.mkdir(parents=True, exist_ok=True)

            state_file.write_text("")

            # Should raise or return empty state
            with pytest.raises((json.JSONDecodeError, ValueError)):
                manager.load()


class TestStateFilePermissionDenied:
    """Tests for permission error handling.

    Note: Permission tests are platform-dependent and may behave differently
    on macOS vs Linux vs Windows. The atomic write pattern uses a temp file
    followed by rename, which may succeed even with restrictive permissions
    on some systems.
    """

    def test_save_documents_atomic_write_pattern(self) -> None:
        """Document that StateManager uses atomic write pattern.

        The atomic write pattern (write temp + rename) is crash-safe but
        has different permission semantics than direct file writes.

        On some systems:
        - Can't write to read-only file BUT can write temp + rename over it
        - Can't create files in read-only dir (expected failure)

        This test documents the expected behavior.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            state = WorkflowState(
                tasks={
                    "t1": Task(id="t1", description="x", status=TaskStatus.PENDING, dependencies=[])
                }
            )
            manager.save(state)

            # Verify the atomic write pattern is used
            # (state_file should exist and be valid JSON after save)
            assert manager.state_file.exists()
            loaded = manager.load()
            assert loaded is not None
            assert "t1" in loaded.tasks

    def test_nonexistent_directory_creates_parents(self) -> None:
        """StateManager should create parent directories if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_path = Path(tmpdir) / "a" / "b" / "c"
            manager = WorkflowStateStore(deep_path)

            state = WorkflowState(
                tasks={
                    "t1": Task(id="t1", description="x", status=TaskStatus.PENDING, dependencies=[])
                }
            )
            manager.save(state)

            assert manager.state_file.exists()
            assert manager.state_file.parent.exists()
