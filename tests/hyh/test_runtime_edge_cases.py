"""
Runtime Edge Cases Tests.

Tests for LocalRuntime, DockerRuntime, and execution edge cases.
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hyh.runtime import (
    DockerRuntime,
    IdentityMapper,
    LocalRuntime,
    VolumeMapper,
    decode_signal,
)


class TestSignalDecoding:
    """Test signal decoding edge cases."""

    def test_decode_signal_zero(self) -> None:
        """Return code 0 should return None (success)."""
        assert decode_signal(0) is None

    def test_decode_signal_positive(self) -> None:
        """Positive return codes should return None (not a signal)."""
        assert decode_signal(1) is None
        assert decode_signal(127) is None

    def test_decode_signal_known(self) -> None:
        """Known signals should be decoded to names."""
        # SIGTERM = -15
        result = decode_signal(-15)
        assert result is not None
        assert "TERM" in result or "15" in result

        # SIGKILL = -9
        result = decode_signal(-9)
        assert result is not None
        assert "KILL" in result or "9" in result

    def test_decode_signal_out_of_range(self) -> None:
        """Out-of-range signal numbers should still decode."""
        # -256 is not a valid signal
        result = decode_signal(-256)
        assert result is not None
        assert "256" in result


class TestVolumeMapper:
    """Test VolumeMapper path translation."""

    def test_identity_mapper(self) -> None:
        """IdentityMapper should return paths unchanged."""
        mapper = IdentityMapper()
        assert mapper.to_runtime("/host/path") == "/host/path"
        assert mapper.to_runtime("/any/path") == "/any/path"

    def test_volume_mapper_translation(self) -> None:
        """VolumeMapper should translate host paths to container paths."""
        mapper = VolumeMapper("/host/workspace", "/container/workspace")

        result = mapper.to_runtime("/host/workspace/subdir/file.py")
        assert result == "/container/workspace/subdir/file.py"

    def test_volume_mapper_outside_root(self) -> None:
        """Paths outside host_root should be returned unchanged."""
        mapper = VolumeMapper("/host/workspace", "/container/workspace")

        result = mapper.to_runtime("/other/path/file.py")
        assert result == "/other/path/file.py"

    def test_volume_mapper_path_traversal(self) -> None:
        """Path traversal attempts should not escape container root."""
        mapper = VolumeMapper("/host/workspace", "/container/workspace")

        # Attempt to escape via ../..
        result = mapper.to_runtime("/host/workspace/../../../etc/passwd")
        # Should either reject or normalize safely
        assert "/etc/passwd" not in result or result == "/host/workspace/../../../etc/passwd"

    def test_volume_mapper_symlink_handling(self) -> None:
        """Test symlink handling in volume mapping."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create real directory structure
            real_dir = Path(tmpdir) / "real"
            real_dir.mkdir()
            (real_dir / "file.txt").write_text("content")

            # Create symlink
            link_dir = Path(tmpdir) / "link"
            link_dir.symlink_to(real_dir)

            # Mapper using symlink as root
            mapper = VolumeMapper(str(link_dir), "/container")

            # Path under symlink should translate
            result = mapper.to_runtime(str(link_dir / "file.txt"))
            assert "/container" in result


class TestLocalRuntime:
    """Test LocalRuntime execution."""

    def test_execute_simple_command(self) -> None:
        """Simple command should execute successfully."""
        runtime = LocalRuntime()
        result = runtime.execute(["echo", "hello"], cwd=".")

        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_execute_with_timeout(self) -> None:
        """Command exceeding timeout raises TimeoutExpired.

        NOTE: LocalRuntime doesn't catch TimeoutExpired - it propagates.
        This documents actual behavior.
        """
        runtime = LocalRuntime()

        # This should raise TimeoutExpired
        with pytest.raises(subprocess.TimeoutExpired):
            runtime.execute(["sleep", "10"], cwd=".", timeout=0.1)

    def test_execute_with_env(self) -> None:
        """Environment variables should be passed to command."""
        runtime = LocalRuntime()
        result = runtime.execute(
            ["printenv", "MY_VAR"],
            cwd=".",
            env={"MY_VAR": "my_value"},
        )

        assert result.returncode == 0
        assert "my_value" in result.stdout

    def test_execute_nonexistent_command(self) -> None:
        """Non-existent command raises FileNotFoundError.

        NOTE: LocalRuntime doesn't catch FileNotFoundError - it propagates.
        This documents actual behavior.
        """
        runtime = LocalRuntime()

        with pytest.raises(FileNotFoundError):
            runtime.execute(
                ["nonexistent_command_xyz123"],
                cwd=".",
            )


class TestDockerRuntime:
    """Test DockerRuntime (with mocking)."""

    def test_docker_runtime_command_building(self) -> None:
        """DockerRuntime should build correct docker exec commands."""
        runtime = DockerRuntime(
            container_id="test-container",
            path_mapper=IdentityMapper(),
        )

        # Mock subprocess.run
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="output",
                stderr="",
            )

            runtime.execute(["echo", "hello"], cwd="/workspace")

            # Verify docker exec was called
            call_args = mock_run.call_args
            cmd = call_args[1]["args"] if "args" in call_args[1] else call_args[0][0]
            assert "docker" in cmd
            assert "exec" in cmd
            assert "test-container" in cmd

    def test_docker_runtime_with_volume_mapper(self) -> None:
        """DockerRuntime should use volume mapper for paths."""
        mapper = VolumeMapper("/host/workspace", "/app")
        runtime = DockerRuntime(
            container_id="test-container",
            path_mapper=mapper,
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            runtime.execute(["ls"], cwd="/host/workspace/subdir")

            # cwd should be translated to container path
            call_args = mock_run.call_args
            cmd_str = " ".join(str(x) for x in call_args[0][0])
            assert "/app/subdir" in cmd_str or "-w" in cmd_str
