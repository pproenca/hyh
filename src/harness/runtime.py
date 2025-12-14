# src/harness/runtime.py
"""
Runtime abstraction for executing commands in local or Docker environments.

This module provides:
- Signal decoding for negative return codes
- Path mapping between host and container filesystems
- LocalRuntime for executing commands directly on the host
- DockerRuntime for executing commands inside Docker containers
- Factory function for creating runtime instances from environment variables

Council Fixes Applied:
- Root Escape: DockerRuntime passes --user $(id -u):$(id -g) to docker exec
- Blind Execution: Add env parameter to execute() for API keys
- Missing Signal: Add decode_signal() helper to translate negative return codes to signal names
- Global Lock Suicide: Add exclusive: bool = False parameter - only acquire lock when True
"""

from __future__ import annotations

import subprocess
import threading
import signal
import os
from abc import ABC, abstractmethod
from typing import Protocol, Dict, Optional
from dataclasses import dataclass


# Global execution lock for exclusive operations
GLOBAL_EXEC_LOCK = threading.Lock()


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


@dataclass
class ExecutionResult:
    """Result of executing a command."""
    returncode: int
    stdout: str
    stderr: str


class PathMapper(ABC):
    """Abstract base class for mapping paths between host and runtime environments."""

    @abstractmethod
    def to_runtime(self, host_path: str) -> str:
        """Map a host path to a runtime path."""
        pass

    @abstractmethod
    def to_host(self, runtime_path: str) -> str:
        """Map a runtime path to a host path."""
        pass


class IdentityMapper(PathMapper):
    """Path mapper that returns paths unchanged."""

    def to_runtime(self, host_path: str) -> str:
        """Return the same path."""
        return host_path

    def to_host(self, runtime_path: str) -> str:
        """Return the same path."""
        return runtime_path


class VolumeMapper(PathMapper):
    """Path mapper for Docker volume mounts."""

    def __init__(self, host_root: str, container_root: str):
        """
        Initialize VolumeMapper.

        Args:
            host_root: Root directory on the host (e.g., /host/workspace)
            container_root: Root directory in the container (e.g., /workspace)
        """
        # Normalize paths by removing trailing slashes
        self.host_root = host_root.rstrip("/")
        self.container_root = container_root.rstrip("/")

    def to_runtime(self, host_path: str) -> str:
        """Map a host path to a container path."""
        if host_path.startswith(self.host_root):
            # Replace host root with container root
            relative_path = host_path[len(self.host_root):]
            return self.container_root + relative_path
        return host_path

    def to_host(self, runtime_path: str) -> str:
        """Map a container path to a host path."""
        if runtime_path.startswith(self.container_root):
            # Replace container root with host root
            relative_path = runtime_path[len(self.container_root):]
            return self.host_root + relative_path
        return runtime_path


class Runtime(Protocol):
    """Protocol for runtime execution engines."""

    def execute(
        self,
        command: list[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
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

    def execute(
        self,
        command: list[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        exclusive: bool = False,
    ) -> ExecutionResult:
        """
        Execute a command locally.

        Args:
            command: Command and arguments to execute
            cwd: Working directory for the command
            env: Environment variables to pass to the command
            timeout: Timeout in seconds (None for no timeout)
            exclusive: If True, acquire GLOBAL_EXEC_LOCK before executing

        Returns:
            ExecutionResult with returncode, stdout, stderr
        """
        def _execute():
            # Build environment (merge with current environment if provided)
            exec_env = os.environ.copy() if env else None
            if env:
                exec_env.update(env)

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
        else:
            return _execute()


class DockerRuntime:
    """Runtime for executing commands inside a Docker container."""

    def __init__(self, container_id: str, path_mapper: PathMapper):
        """
        Initialize DockerRuntime.

        Args:
            container_id: Docker container ID or name
            path_mapper: PathMapper for translating host/container paths
        """
        self.container_id = container_id
        self.path_mapper = path_mapper

    def check_capabilities(self) -> None:
        """Verify Docker daemon is running and accessible."""
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Docker not available: {result.stderr}")

    def execute(
        self,
        command: list[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
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
        def _execute():
            # Build docker exec command
            docker_cmd = ["docker", "exec"]

            # Add UID:GID mapping to prevent root escape
            uid = os.getuid()
            gid = os.getgid()
            docker_cmd.extend(["--user", f"{uid}:{gid}"])

            # Add environment variables
            if env:
                for key, value in env.items():
                    docker_cmd.extend(["-e", f"{key}={value}"])

            # Add working directory (map to container path)
            if cwd:
                container_cwd = self.path_mapper.to_runtime(cwd)
                docker_cmd.extend(["-w", container_cwd])

            # Add container ID
            docker_cmd.append(self.container_id)

            # Add the actual command
            docker_cmd.extend(command)

            # Execute
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
        else:
            return _execute()


def create_runtime() -> Runtime:
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
        # Create DockerRuntime with appropriate path mapper
        host_root = os.environ.get("HARNESS_HOST_ROOT")
        container_root = os.environ.get("HARNESS_CONTAINER_ROOT")

        if host_root and container_root:
            path_mapper = VolumeMapper(host_root, container_root)
        else:
            path_mapper = IdentityMapper()

        return DockerRuntime(container_id, path_mapper)
    else:
        return LocalRuntime()
