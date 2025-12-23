# tests/hyh/test_runtime.py
"""
Tests for runtime abstraction with UID mapping.

Tests cover:
- Signal decoding (negative return codes to signal names)
- PathMapper (IdentityMapper, VolumeMapper)
- LocalRuntime (execute, timeout, env, no_lock_by_default, exclusive_acquires_lock)
- DockerRuntime (docker exec, path mapping, env via -e, UID mapping)
- RuntimeFactory (create_runtime from env vars)
"""

import os
import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest

from tests.hyh.conftest import wait_until


class TestSignalDecoding:
    """Test decode_signal() helper function."""

    def test_negative_sigterm(self):
        """Negative return code -15 should decode to SIGTERM."""
        from hyh.runtime import decode_signal

        assert decode_signal(-15) == "SIGTERM"

    def test_negative_sigkill(self):
        """Negative return code -9 should decode to SIGKILL."""
        from hyh.runtime import decode_signal

        assert decode_signal(-9) == "SIGKILL"

    def test_negative_sigint(self):
        """Negative return code -2 should decode to SIGINT."""
        from hyh.runtime import decode_signal

        assert decode_signal(-2) == "SIGINT"

    def test_positive_code_returns_none(self):
        """Positive return codes should return None."""
        from hyh.runtime import decode_signal

        assert decode_signal(0) is None
        assert decode_signal(1) is None
        assert decode_signal(127) is None

    def test_zero_returns_none(self):
        """Zero return code should return None."""
        from hyh.runtime import decode_signal

        assert decode_signal(0) is None

    def test_unknown_signal_returns_generic(self):
        """Unknown signal numbers should return SIG<N>."""
        from hyh.runtime import decode_signal

        # -99 is unlikely to be a real signal
        result = decode_signal(-99)
        assert result is not None
        assert result == "SIG99" or "99" in result


class TestPathMapper:
    """Test PathMapper ABC and implementations."""

    def test_path_mapper_cannot_instantiate(self):
        """PathMapper is abstract - cannot instantiate."""
        from hyh.runtime import PathMapper

        with pytest.raises(TypeError):
            PathMapper()

    def test_identity_mapper_returns_same_path(self):
        """IdentityMapper should return the same path."""
        from hyh.runtime import IdentityMapper

        mapper = IdentityMapper()
        assert mapper.to_runtime("/host/path/file.txt") == "/host/path/file.txt"
        assert mapper.to_host("/runtime/path/file.txt") == "/runtime/path/file.txt"

    def test_volume_mapper_maps_host_to_container(self):
        """VolumeMapper should map host paths to container paths."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/workspace", container_root="/workspace")

        assert mapper.to_runtime("/host/workspace/src/file.py") == "/workspace/src/file.py"
        assert mapper.to_runtime("/host/workspace/README.md") == "/workspace/README.md"

    def test_volume_mapper_maps_container_to_host(self):
        """VolumeMapper should map container paths to host paths."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/workspace", container_root="/workspace")

        assert mapper.to_host("/workspace/src/file.py") == "/host/workspace/src/file.py"
        assert mapper.to_host("/workspace/README.md") == "/host/workspace/README.md"

    def test_volume_mapper_handles_trailing_slashes(self):
        """VolumeMapper should handle trailing slashes correctly."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/workspace/", container_root="/workspace/")

        assert mapper.to_runtime("/host/workspace/file.txt") == "/workspace/file.txt"
        assert mapper.to_host("/workspace/file.txt") == "/host/workspace/file.txt"

    def test_volume_mapper_preserves_absolute_paths(self):
        """VolumeMapper should preserve absolute paths."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/workspace", container_root="/workspace")

        result = mapper.to_runtime("/host/workspace/nested/deep/file.txt")
        assert result == "/workspace/nested/deep/file.txt"

    def test_volume_mapper_path_outside_host_root(self):
        """VolumeMapper should pass through paths outside mapped host root."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper("/host/workspace", "/container/workspace")

        assert mapper.to_runtime("/other/path") == "/other/path"

    def test_volume_mapper_path_outside_container_root(self):
        """VolumeMapper should pass through paths outside mapped container root."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper("/host/workspace", "/container/workspace")

        assert mapper.to_host("/other/container/path") == "/other/container/path"

    def test_volume_mapper_path_with_similar_prefix_not_mapped(self):
        """VolumeMapper should NOT map paths that share prefix but aren't children.

        Bug: /host/work maps /host/workspace incorrectly due to startswith() check.
        The path /host/workspace starts with /host/work, but is NOT a child of it.
        """
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/work", container_root="/container/work")

        # /host/workspace is NOT a child of /host/work - should pass through unchanged
        assert mapper.to_runtime("/host/workspace") == "/host/workspace"
        assert mapper.to_runtime("/host/working") == "/host/working"
        assert mapper.to_runtime("/host/work-extra") == "/host/work-extra"

        # But actual children SHOULD be mapped
        assert mapper.to_runtime("/host/work/file.txt") == "/container/work/file.txt"
        assert mapper.to_runtime("/host/work") == "/container/work"

    def test_volume_mapper_container_path_with_similar_prefix_not_mapped(self):
        """VolumeMapper to_host should NOT map paths that share prefix but aren't children."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/work", container_root="/container/work")

        # /container/workspace is NOT a child of /container/work
        assert mapper.to_host("/container/workspace") == "/container/workspace"
        assert mapper.to_host("/container/working") == "/container/working"

        # But actual children SHOULD be mapped
        assert mapper.to_host("/container/work/file.txt") == "/host/work/file.txt"


class TestLocalRuntime:
    """Test LocalRuntime execution."""

    def test_execute_simple_command(self):
        """LocalRuntime should execute simple commands."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()
        result = runtime.execute(["echo", "hello"])

        assert result.returncode == 0
        assert result.stdout.strip() == "hello"
        assert result.stderr == ""

    def test_execute_command_with_env(self):
        """LocalRuntime should pass environment variables."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()
        result = runtime.execute(["sh", "-c", "echo $TEST_VAR"], env={"TEST_VAR": "test_value"})

        assert result.returncode == 0
        assert result.stdout.strip() == "test_value"

    def test_execute_command_with_timeout(self):
        """LocalRuntime should timeout long-running commands."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()

        with pytest.raises(subprocess.TimeoutExpired):
            runtime.execute(["sleep", "10"], timeout=0.1)

    def test_execute_failing_command(self):
        """LocalRuntime should return non-zero for failing commands."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()
        result = runtime.execute(["false"])

        assert result.returncode != 0

    def test_execute_with_cwd(self):
        """LocalRuntime should execute commands in specified cwd."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()
        result = runtime.execute(["pwd"], cwd="/tmp")

        assert result.returncode == 0
        assert "/tmp" in result.stdout

    def test_no_lock_by_default(self):
        """LocalRuntime should NOT acquire lock by default."""
        from hyh.runtime import GLOBAL_EXEC_LOCK, LocalRuntime

        runtime = LocalRuntime()

        with GLOBAL_EXEC_LOCK:
            # If execute() tries to acquire the lock, this will deadlock/timeout
            # We use a thread to test this
            result_container = {}

            def run_command():
                result = runtime.execute(["echo", "test"], timeout=1.0)
                result_container["result"] = result

            thread = threading.Thread(target=run_command)
            thread.start()
            thread.join(timeout=2.0)

            # Thread should complete without blocking
            assert not thread.is_alive(), "Command blocked on lock when exclusive=False"
            assert result_container["result"].returncode == 0

    def test_exclusive_acquires_lock(self):
        """LocalRuntime with exclusive=True should acquire lock."""
        from hyh.runtime import GLOBAL_EXEC_LOCK, LocalRuntime

        runtime = LocalRuntime()

        GLOBAL_EXEC_LOCK.acquire()

        try:
            result_container = {}

            def run_command():
                result = runtime.execute(["echo", "test"], exclusive=True, timeout=5.0)
                result_container["result"] = result

            thread = threading.Thread(target=run_command)
            thread.start()

            # Wait for thread to start and block on lock
            wait_until(
                lambda: thread.is_alive(),
                timeout=1.0,
                message="Thread should start",
            )

            # Thread should be blocked waiting for lock (still alive because it's waiting)
            assert thread.is_alive(), "Command should block waiting for lock"

            GLOBAL_EXEC_LOCK.release()

            # Thread should now complete
            thread.join(timeout=2.0)
            assert not thread.is_alive(), "Command should complete after lock release"
            assert result_container["result"].returncode == 0
        finally:
            # Ensure lock is released even if test fails
            if GLOBAL_EXEC_LOCK.locked():
                GLOBAL_EXEC_LOCK.release()

    def test_execute_captures_stderr(self):
        """LocalRuntime should capture stderr."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()
        result = runtime.execute(["sh", "-c", "echo error >&2"])

        assert result.returncode == 0
        assert result.stderr.strip() == "error"


class TestDockerRuntime:
    """Test DockerRuntime execution with UID mapping."""

    @patch("subprocess.run")
    def test_execute_uses_docker_exec(self, mock_run):
        """DockerRuntime should use docker exec."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        runtime = DockerRuntime(container_id="test-container", path_mapper=IdentityMapper())
        runtime.execute(["echo", "hello"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "docker"
        assert args[1] == "exec"
        assert "test-container" in args

    @patch("subprocess.run")
    def test_execute_includes_uid_gid_mapping(self, mock_run):
        """DockerRuntime should pass --user $(id -u):$(id -g)."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        runtime = DockerRuntime(container_id="test-container", path_mapper=IdentityMapper())
        runtime.execute(["echo", "hello"])

        args = mock_run.call_args[0][0]
        assert "--user" in args
        user_index = args.index("--user")
        # Should be in format "UID:GID"
        user_value = args[user_index + 1]
        assert ":" in user_value
        # Should contain numeric values
        uid, gid = user_value.split(":")
        assert uid.isdigit()
        assert gid.isdigit()

    @patch("subprocess.run")
    def test_execute_passes_env_variables(self, mock_run):
        """DockerRuntime should pass env vars with -e flags."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        runtime = DockerRuntime(container_id="test-container", path_mapper=IdentityMapper())
        runtime.execute(["echo", "test"], env={"API_KEY": "secret123", "DEBUG": "true"})

        args = mock_run.call_args[0][0]
        assert "-e" in args

        env_vars = {}
        for i, arg in enumerate(args):
            if arg == "-e" and i + 1 < len(args):
                env_pair = args[i + 1]
                if "=" in env_pair:
                    key, value = env_pair.split("=", 1)
                    env_vars[key] = value

        assert env_vars.get("API_KEY") == "secret123"
        assert env_vars.get("DEBUG") == "true"

    @patch("subprocess.run")
    def test_execute_maps_cwd_path(self, mock_run):
        """DockerRuntime should map cwd using PathMapper."""
        from hyh.runtime import DockerRuntime, VolumeMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        mapper = VolumeMapper(host_root="/host/workspace", container_root="/workspace")
        runtime = DockerRuntime(container_id="test-container", path_mapper=mapper)
        runtime.execute(["pwd"], cwd="/host/workspace/src")

        args = mock_run.call_args[0][0]
        assert "-w" in args
        w_index = args.index("-w")
        assert args[w_index + 1] == "/workspace/src"

    @patch("subprocess.run")
    def test_execute_with_timeout(self, mock_run):
        """DockerRuntime should pass timeout to subprocess.run."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        runtime = DockerRuntime(container_id="test-container", path_mapper=IdentityMapper())
        runtime.execute(["sleep", "1"], timeout=5.0)

        assert mock_run.call_args[1]["timeout"] == 5.0

    @patch("subprocess.run")
    def test_execute_command_structure(self, mock_run):
        """DockerRuntime should construct correct docker exec command."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        runtime = DockerRuntime(container_id="test-container", path_mapper=IdentityMapper())
        runtime.execute(["python", "script.py", "--arg", "value"])

        args = mock_run.call_args[0][0]

        # Basic structure: docker exec [flags] container command
        assert args[0] == "docker"
        assert args[1] == "exec"
        assert "test-container" in args

        # The actual command should be at the end
        assert "python" in args
        assert "script.py" in args
        assert "--arg" in args
        assert "value" in args

    @patch("subprocess.run")
    def test_docker_runtime_exclusive_lock_acquired(self, mock_run):
        """DockerRuntime should acquire GLOBAL_EXEC_LOCK when exclusive=True."""
        import threading

        from hyh.runtime import GLOBAL_EXEC_LOCK, DockerRuntime, VolumeMapper

        mapper = VolumeMapper("/host", "/container")
        runtime = DockerRuntime("test-container", mapper)

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        GLOBAL_EXEC_LOCK.acquire()

        try:
            result_container = {}

            def run_command():
                result = runtime.execute(["echo", "test"], exclusive=True)
                result_container["result"] = result

            thread = threading.Thread(target=run_command)
            thread.start()

            # Wait for thread to start and block on lock
            wait_until(
                lambda: thread.is_alive(),
                timeout=1.0,
                message="Thread should start",
            )

            # Thread should be blocked waiting for lock (still alive because it's waiting)
            assert thread.is_alive(), "Command should block waiting for lock when exclusive=True"

            GLOBAL_EXEC_LOCK.release()

            # Thread should now complete
            thread.join(timeout=2.0)
            assert not thread.is_alive(), "Command should complete after lock release"
            assert result_container["result"].returncode == 0
        finally:
            # Ensure lock is released even if test fails
            if GLOBAL_EXEC_LOCK.locked():
                GLOBAL_EXEC_LOCK.release()


class TestRuntimeFactory:
    """Test create_runtime() factory function."""

    def test_create_local_runtime_by_default(self):
        """create_runtime() should return LocalRuntime by default."""
        from hyh.runtime import LocalRuntime, create_runtime

        runtime = create_runtime()
        assert isinstance(runtime, LocalRuntime)

    @patch.dict(os.environ, {"HYH_CONTAINER_ID": "test-container"})
    def test_create_docker_runtime_from_env(self):
        """create_runtime() should return DockerRuntime when env var set."""
        from hyh.runtime import DockerRuntime, create_runtime

        runtime = create_runtime()
        assert isinstance(runtime, DockerRuntime)

    @patch.dict(os.environ, {"HYH_CONTAINER_ID": "test-container"})
    def test_create_docker_runtime_uses_container_id(self):
        """DockerRuntime from factory should use container ID from env."""
        from hyh.runtime import DockerRuntime, create_runtime

        runtime = create_runtime()
        assert isinstance(runtime, DockerRuntime)

        assert runtime.container_id == "test-container"

    @patch.dict(
        os.environ,
        {
            "HYH_CONTAINER_ID": "test-container",
            "HYH_HOST_ROOT": "/host/workspace",
            "HYH_CONTAINER_ROOT": "/workspace",
        },
    )
    def test_create_docker_runtime_with_volume_mapping(self):
        """DockerRuntime from factory should use VolumeMapper when paths provided."""
        from hyh.runtime import DockerRuntime, VolumeMapper, create_runtime

        runtime = create_runtime()
        assert isinstance(runtime, DockerRuntime)
        assert isinstance(runtime.path_mapper, VolumeMapper)

    @patch.dict(os.environ, {"HYH_CONTAINER_ID": "test-container"}, clear=True)
    def test_create_docker_runtime_with_identity_mapper_default(self):
        """DockerRuntime should use IdentityMapper when no paths in env."""
        from hyh.runtime import DockerRuntime, IdentityMapper, create_runtime

        os.environ.pop("HYH_HOST_ROOT", None)
        os.environ.pop("HYH_CONTAINER_ROOT", None)

        runtime = create_runtime()
        assert isinstance(runtime, DockerRuntime)
        assert isinstance(runtime.path_mapper, IdentityMapper)

    @patch.dict(os.environ, {}, clear=True)
    def test_create_local_runtime_when_no_env_vars(self):
        """create_runtime() should return LocalRuntime when no env vars set."""
        from hyh.runtime import LocalRuntime, create_runtime

        runtime = create_runtime()
        assert isinstance(runtime, LocalRuntime)


class TestRuntimeCapabilityCheck:
    """Test check_capabilities() method on Runtime implementations."""

    def test_local_runtime_has_check_capabilities(self):
        """LocalRuntime should have check_capabilities method."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()
        assert hasattr(runtime, "check_capabilities")
        assert callable(runtime.check_capabilities)

    def test_local_runtime_check_capabilities_verifies_git(self):
        """LocalRuntime.check_capabilities should verify git is available."""
        from hyh.runtime import LocalRuntime

        runtime = LocalRuntime()
        # Should not raise if git is installed (it is in test env)
        runtime.check_capabilities()

    @patch("subprocess.run")
    def test_local_runtime_check_capabilities_raises_on_missing_git(self, mock_run):
        """LocalRuntime.check_capabilities should raise if git not found."""
        from hyh.runtime import LocalRuntime

        mock_run.return_value = MagicMock(returncode=1)

        runtime = LocalRuntime()
        with pytest.raises(RuntimeError, match="git"):
            runtime.check_capabilities()

    @patch("subprocess.run")
    def test_docker_runtime_has_check_capabilities(self, mock_run):
        """DockerRuntime should have check_capabilities method."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runtime = DockerRuntime("test-container", IdentityMapper())
        assert hasattr(runtime, "check_capabilities")
        assert callable(runtime.check_capabilities)

    @patch("subprocess.run")
    def test_docker_runtime_check_capabilities_verifies_docker(self, mock_run):
        """DockerRuntime.check_capabilities should run docker info."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runtime = DockerRuntime("test-container", IdentityMapper())
        runtime.check_capabilities()

        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("docker" in str(c) and "info" in str(c) for c in calls)

    @patch("subprocess.run")
    def test_docker_runtime_check_capabilities_raises_on_docker_failure(self, mock_run):
        """DockerRuntime.check_capabilities should raise if docker not running."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=1, stderr="Cannot connect to Docker daemon")
        runtime = DockerRuntime("test-container", IdentityMapper())

        with pytest.raises(RuntimeError, match="[Dd]ocker"):
            runtime.check_capabilities()
