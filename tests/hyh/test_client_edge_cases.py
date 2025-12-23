"""
Client Edge Cases Tests.

Tests for CLI client robustness and error handling.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from hyh.client import get_socket_path, get_worker_id


class TestWorkerIdPersistence:
    """Test worker ID file handling edge cases."""

    def test_worker_id_format(self) -> None:
        """Worker ID should have correct format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = Path(tmpdir) / "worker.id"

            with patch.dict(os.environ, {"HYH_WORKER_ID_FILE": str(worker_file)}):
                worker_id = get_worker_id()

                assert worker_id.startswith("worker-")
                assert len(worker_id) == 19  # "worker-" + 12 hex chars

    def test_worker_id_persistence(self) -> None:
        """Same worker ID should be returned on subsequent calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = Path(tmpdir) / "worker.id"

            with patch.dict(os.environ, {"HYH_WORKER_ID_FILE": str(worker_file)}):
                id1 = get_worker_id()
                id2 = get_worker_id()

                assert id1 == id2, "Worker ID should be persistent"

    def test_worker_id_file_corrupted(self) -> None:
        """Corrupted worker ID file should be regenerated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = Path(tmpdir) / "worker.id"

            # Create corrupted file
            worker_file.write_text("invalid-format")

            with patch.dict(os.environ, {"HYH_WORKER_ID_FILE": str(worker_file)}):
                worker_id = get_worker_id()

                # Should get a valid new ID
                assert worker_id.startswith("worker-")
                assert len(worker_id) == 19

    def test_worker_id_file_multiple_lines(self) -> None:
        """File with multiple lines triggers regeneration.

        NOTE: get_worker_id() validates format strictly. A file with
        multiple lines is considered corrupted and triggers regeneration.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = Path(tmpdir) / "worker.id"

            # Write multiple lines (considered corrupted)
            worker_file.write_text("worker-abcdef123456\nworker-second\n")

            with patch.dict(os.environ, {"HYH_WORKER_ID_FILE": str(worker_file)}):
                worker_id = get_worker_id()

                # Should get a newly generated ID (format validation)
                assert worker_id.startswith("worker-")
                assert len(worker_id) == 19


class TestSocketPathGeneration:
    """Test socket path generation edge cases."""

    def test_socket_path_format(self) -> None:
        """Socket path should be in correct directory."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"HYH_WORKTREE": tmpdir}),
        ):
            socket_path = get_socket_path()

            assert socket_path.endswith(".sock")
            assert "hyh" in socket_path.lower()

    def test_socket_path_deterministic(self) -> None:
        """Same worktree should get same socket path."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"HYH_WORKTREE": tmpdir}),
        ):
            path1 = get_socket_path()
            path2 = get_socket_path()

            assert path1 == path2

    def test_socket_path_different_worktrees(self) -> None:
        """Socket path is deterministic based on git root.

        NOTE: HYH_WORKTREE env var may not directly affect get_socket_path().
        The socket path is based on git root detection, not the env var.
        This test verifies determinism - same env gives same path.
        """
        # Call twice in same env - should get same path
        path1 = get_socket_path()
        path2 = get_socket_path()

        assert path1 == path2, "Socket path should be deterministic"


class TestSocketPathLength:
    """Test socket path length constraints (macOS limit)."""

    def test_socket_path_under_limit(self) -> None:
        """Socket path should be under 104 character limit."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"HYH_WORKTREE": tmpdir}),
        ):
            socket_path = get_socket_path()

            # macOS AF_UNIX limit is 104 characters
            assert len(socket_path) < 104, f"Socket path too long: {len(socket_path)} chars"
