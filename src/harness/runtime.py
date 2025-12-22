# src/harness/runtime.py
"""
Runtime abstraction for executing commands in local or Docker environments.

This module provides:
- Signal decoding for negative return codes
- Path mapping between host and container filesystems
- LocalRuntime for executing commands directly on the host
- DockerRuntime for executing commands inside Docker containers
- Factory function for creating runtime instances from environment variables
"""

import os
import signal
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final, Protocol

# Global execution lock for exclusive operations
GLOBAL_EXEC_LOCK: Final[threading.Lock] = threading.Lock()


def decode_signal(returncode: int) -> str | None:
    """
    Decode negative return codes to signal names.

    Args:
        returncode: Process return code (negative indicates signal)

    Returns:
        Signal name (e.g., "SIGTERM") or None if not a signal

    Examples:
        decode_signal(-15) -> "SIGTERM"
        decode_signal(-9) -> "SIGKILL"
        decode_signal(0) -> None
        decode_signal(1) -> None
    """
    if returncode >= 0:
        return None

    # Convert negative return code to positive signal number
    sig_num = abs(returncode)

    # Try to find the signal name
    try:
        sig = signal.Signals(sig_num)
        return sig.name
    except ValueError:
        # Unknown signal - return generic name
        return f"SIG{sig_num}"


@dataclass(slots=True, frozen=True)
class ExecutionResult:
    """Immutable result of executing a command.

    Thread-safe by design: frozen dataclass prevents mutation after creation.
    """

    returncode: int
    stdout: str
    stderr: str


class PathMapper(ABC):
    """Abstract base class for mapping paths between host and runtime environments."""

    __slots__ = ()

    @abstractmethod
    def to_runtime(self, host_path: str) -> str:
        """Map a host path to a runtime path."""
        ...

    @abstractmethod
    def to_host(self, runtime_path: str) -> str:
        """Map a runtime path to a host path."""
        ...


class IdentityMapper(PathMapper):
    """Path mapper that returns paths unchanged."""

    __slots__ = ()

    def to_runtime(self, host_path: str) -> str:
        return host_path

    def to_host(self, runtime_path: str) -> str:
        return runtime_path


class VolumeMapper(PathMapper):
    """Path mapper for Docker volume mounts."""

    __slots__ = ("container_root", "host_root")

    def __init__(self, host_root: str, container_root: str) -> None:
        self.host_root = host_root.rstrip("/")
        self.container_root = container_root.rstrip("/")

    def _normalize_and_validate(self, path: str, root: str) -> str | None:
        """Normalize path and validate it stays within root.

        Returns the relative path if valid, None if path escapes root.
        """
        # Normalize to resolve .. and . components
        normalized = os.path.normpath(path)

        # Check if normalized path is exactly root or starts with root/
        if normalized == root:
            return ""
        if normalized.startswith(root + "/"):
            return normalized[len(root) :]
        return None

    def to_runtime(self, host_path: str) -> str:
        """Map a host path to a container path.

        Normalizes the path to prevent traversal attacks.
        Returns the original path unchanged if it doesn't match host_root.
        """
        relative = self._normalize_and_validate(host_path, self.host_root)
        if relative is not None:
            return self.container_root + relative
        return host_path

    def to_host(self, runtime_path: str) -> str:
        """Map a container path to a host path.

        Normalizes the path to prevent traversal attacks.
        Returns the original path unchanged if it doesn't match container_root.
        """
        relative = self._normalize_and_validate(runtime_path, self.container_root)
        if relative is not None:
            return self.host_root + relative
        return runtime_path


class Runtime(Protocol):
    """Protocol for runtime execution engines."""

    def execute(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        exclusive: bool = False,
    ) -> ExecutionResult:
        """
        Execute a command.

        Args:
            command: Command and arguments to execute
            cwd: Working directory for the command
            env: Environment variables to pass to the command
            timeout: Timeout in seconds (None for no timeout)
            exclusive: If True, acquire GLOBAL_EXEC_LOCK before executing

        Returns:
            ExecutionResult with returncode, stdout, stderr
        """
        ...

    def check_capabilities(self) -> None:
        """
        Verify required tools are available.

        Raises:
            RuntimeError: If required tools are not available
        """
        ...


class LocalRuntime:
    """Runtime for executing commands directly on the host system."""

    __slots__ = ()

    def execute(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        exclusive: bool = False,
    ) -> ExecutionResult:
        """Execute a command locally.

        Args:
            command: Command and arguments to execute
            cwd: Working directory for the command
            env: Environment variables to pass to the command (merged with os.environ)
            timeout: Timeout in seconds (None for no timeout)
            exclusive: If True, acquire GLOBAL_EXEC_LOCK before executing

        Returns:
            ExecutionResult with returncode, stdout, stderr
        """

        def _execute() -> ExecutionResult:
            # Merge env with os.environ only if env is provided (walrus operator)
            exec_env = {**os.environ, **env} if env else None

            result = subprocess.run(
                command,
                cwd=cwd,
                env=exec_env,
                timeout=timeout,
                capture_output=True,
                text=True,
            )

            return ExecutionResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )

        if exclusive:
            with GLOBAL_EXEC_LOCK:
                return _execute()
        return _execute()

    def check_capabilities(self) -> None:
        """
        Verify git is available.

        Raises:
            RuntimeError: If git is not found in PATH
        """
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError("git not found in PATH")


class DockerRuntime:
    """Runtime for executing commands inside a Docker container."""

    __slots__ = ("container_id", "path_mapper")

    def __init__(self, container_id: str, path_mapper: PathMapper) -> None:
        self.container_id = container_id
        self.path_mapper = path_mapper

    def check_capabilities(self) -> None:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Docker not available: {result.stderr}")

    def execute(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        exclusive: bool = False,
    ) -> ExecutionResult:
        """
        Execute a command inside the Docker container.

        Args:
            command: Command and arguments to execute
            cwd: Working directory for the command (host path)
            env: Environment variables to pass to the command
            timeout: Timeout in seconds (None for no timeout)
            exclusive: If True, acquire GLOBAL_EXEC_LOCK before executing

        Returns:
            ExecutionResult with returncode, stdout, stderr
        """

        def _execute() -> ExecutionResult:
            docker_cmd = ["docker", "exec"]

            # Add UID:GID mapping to prevent root escape
            uid = os.getuid()
            gid = os.getgid()
            docker_cmd.extend(["--user", f"{uid}:{gid}"])

            if env:
                for key, value in env.items():
                    docker_cmd.extend(["-e", f"{key}={value}"])

            if cwd:
                container_cwd = self.path_mapper.to_runtime(cwd)
                docker_cmd.extend(["-w", container_cwd])

            docker_cmd.append(self.container_id)
            docker_cmd.extend(command)

            result = subprocess.run(
                docker_cmd,
                timeout=timeout,
                capture_output=True,
                text=True,
            )

            return ExecutionResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )

        if exclusive:
            with GLOBAL_EXEC_LOCK:
                return _execute()
        return _execute()


def create_runtime() -> LocalRuntime | DockerRuntime:
    """
    Create a runtime instance based on environment variables.

    Environment variables:
        HARNESS_CONTAINER_ID: Docker container ID (if set, creates DockerRuntime)
        HARNESS_HOST_ROOT: Host root directory for volume mapping
        HARNESS_CONTAINER_ROOT: Container root directory for volume mapping

    Returns:
        LocalRuntime if HARNESS_CONTAINER_ID is not set
        DockerRuntime if HARNESS_CONTAINER_ID is set
    """
    container_id = os.environ.get("HARNESS_CONTAINER_ID")

    if container_id:
        host_root = os.environ.get("HARNESS_HOST_ROOT")
        container_root = os.environ.get("HARNESS_CONTAINER_ROOT")

        path_mapper: PathMapper
        if host_root and container_root:
            path_mapper = VolumeMapper(host_root, container_root)
        else:
            path_mapper = IdentityMapper()

        return DockerRuntime(container_id, path_mapper)
    return LocalRuntime()
