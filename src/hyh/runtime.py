import os
import signal
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Final, Protocol

from msgspec import Struct

GLOBAL_EXEC_LOCK: Final[threading.Lock] = threading.Lock()


def decode_signal(returncode: int) -> str | None:
    if returncode >= 0:
        return None

    sig_num = abs(returncode)

    try:
        sig = signal.Signals(sig_num)
        return sig.name
    except ValueError:
        return f"SIG{sig_num}"


class ExecutionResult(Struct, frozen=True, forbid_unknown_fields=True):
    returncode: int
    stdout: str
    stderr: str


class PathMapper(ABC):
    __slots__ = ()

    @abstractmethod
    def to_runtime(self, host_path: str) -> str: ...

    @abstractmethod
    def to_host(self, runtime_path: str) -> str: ...


class IdentityMapper(PathMapper):
    __slots__ = ()

    def to_runtime(self, host_path: str) -> str:
        return host_path

    def to_host(self, runtime_path: str) -> str:
        return runtime_path


class VolumeMapper(PathMapper):
    __slots__ = ("container_root", "host_root")

    def __init__(self, host_root: str, container_root: str) -> None:
        self.host_root = host_root.rstrip("/")
        self.container_root = container_root.rstrip("/")

    def _normalize_and_validate(self, path: str, root: str) -> str | None:
        normalized = os.path.normpath(path)

        if normalized == root:
            return ""
        if normalized.startswith(root + "/"):
            return normalized[len(root) :]
        return None

    def to_runtime(self, host_path: str) -> str:
        relative = self._normalize_and_validate(host_path, self.host_root)
        if relative is not None:
            return self.container_root + relative
        return host_path

    def to_host(self, runtime_path: str) -> str:
        relative = self._normalize_and_validate(runtime_path, self.container_root)
        if relative is not None:
            return self.host_root + relative
        return runtime_path


class Runtime(Protocol):
    def execute(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        exclusive: bool = False,
    ) -> ExecutionResult: ...

    def check_capabilities(self) -> None: ...


class LocalRuntime:
    __slots__ = ()

    def execute(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        exclusive: bool = False,
    ) -> ExecutionResult:
        def _execute() -> ExecutionResult:
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
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError("git not found in PATH")


class DockerRuntime:
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
        def _execute() -> ExecutionResult:
            docker_cmd = ["docker", "exec"]

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
    container_id = os.environ.get("HYH_CONTAINER_ID")

    if container_id:
        host_root = os.environ.get("HYH_HOST_ROOT")
        container_root = os.environ.get("HYH_CONTAINER_ROOT")

        path_mapper: PathMapper
        if host_root and container_root:
            path_mapper = VolumeMapper(host_root, container_root)
        else:
            path_mapper = IdentityMapper()

        return DockerRuntime(container_id, path_mapper)
    return LocalRuntime()
