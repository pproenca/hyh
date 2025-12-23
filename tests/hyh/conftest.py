# tests/hyh/conftest.py
"""
Shared pytest fixtures for hyh tests.

Centralizes daemon management to ensure proper resource cleanup
and eliminate ResourceWarning issues.
"""

import asyncio
import json
import os
import socket as socket_module
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import Verbosity, settings

# =============================================================================
# Hypothesis Profiles - CI vs Local Development
# =============================================================================

# CI profile: relaxed deadlines for slower CI runners
settings.register_profile(
    "ci",
    max_examples=100,
    deadline=5000,  # 5 seconds (default is 200ms)
    verbosity=Verbosity.normal,
)

# Local dev profile: faster feedback
settings.register_profile(
    "default",
    max_examples=100,
    deadline=1000,  # 1 second
)

# Auto-select profile based on CI environment variable
if os.environ.get("CI"):
    settings.load_profile("ci")
else:
    settings.load_profile("default")

# =============================================================================
# Free-Threading Compatibility
# =============================================================================


@pytest.fixture(autouse=True)
def aggressive_thread_switching(request):
    """Force frequent context switches to expose races on GIL-enabled builds.

    On free-threaded Python (3.13t/3.14t), this is a no-op since there's no GIL.
    On standard Python, setting switch interval to 1 microsecond forces the GIL
    to release frequently, making race conditions more likely to manifest.

    SKIPPED for 'slow' tests (complexity benchmarks) which need stable timing.

    See: https://py-free-threading.github.io/testing/
    """
    # Skip for slow tests - complexity benchmarks need stable timing
    if request.node.get_closest_marker("slow"):
        yield
        return

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.000001)  # 1 microsecond
    yield
    sys.setswitchinterval(old_interval)


# =============================================================================
# Test Utilities - Condition-based waiting (replaces raw time.sleep polling)
# =============================================================================


def wait_until(
    condition: Callable[[], bool],
    timeout: float = 5.0,
    poll_interval: float = 0.01,
    message: str = "Condition not met",
) -> None:
    """Wait until condition is true. Replaces raw time.sleep polling.

    Args:
        condition: Callable that returns True when wait should end.
        timeout: Maximum time to wait in seconds.
        poll_interval: Time between condition checks.
        message: Error message if timeout is reached.

    Raises:
        TimeoutError: If condition not met within timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(poll_interval)
    raise TimeoutError(message)


def wait_for_socket(socket_path: str | Path, timeout: float = 2.0) -> None:
    """Wait for socket file to exist.

    Args:
        socket_path: Path to Unix socket file.
        timeout: Maximum time to wait.

    Raises:
        TimeoutError: If socket not created within timeout.
    """
    wait_until(
        lambda: Path(socket_path).exists(),
        timeout=timeout,
        message=f"Socket {socket_path} not created within {timeout}s",
    )


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def isolate_git_config(monkeypatch):
    """Isolate tests from user's global git config.

    Prevents GPG signing, custom hooks, and other user config
    from affecting test execution.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


@pytest.fixture(autouse=True)
def isolate_project_registry(tmp_path, monkeypatch):
    """Isolate tests from the global project registry.

    Prevents tests from polluting ~/.hyh/registry.json with
    temp directory paths that become stale after pytest cleanup.
    """
    registry_file = tmp_path / "test-registry.json"
    monkeypatch.setenv("HYH_REGISTRY_FILE", str(registry_file))


@pytest.fixture(autouse=True)
def thread_isolation():
    """Ensure no threads leak between tests.

    With Python 3.14t (free-threaded), thread timing is different and
    tests can pollute each other if threads aren't properly cleaned up.

    This fixture:
    1. Records active threads before the test
    2. After the test, waits for any new non-daemon threads to finish
    3. Warns if threads are still running after timeout
    """
    import warnings

    # Record threads before test (excluding daemon threads)
    before = {t for t in threading.enumerate() if not t.daemon and t.is_alive()}

    yield

    # Wait for new threads to finish
    timeout = 2.0  # Reduced from 5.0
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        current = {t for t in threading.enumerate() if not t.daemon and t.is_alive()}
        new_threads = current - before
        # Filter out the main thread
        new_threads = {t for t in new_threads if t.name != "MainThread"}
        if not new_threads:
            break
        time.sleep(0.01)  # More frequent polling (was 0.05)
    else:
        # Timeout - warn about stray threads
        current = {t for t in threading.enumerate() if not t.daemon and t.is_alive()}
        new_threads = current - before
        new_threads = {t for t in new_threads if t.name != "MainThread"}
        if new_threads:
            thread_names = [t.name for t in new_threads]
            warnings.warn(
                f"Test left {len(new_threads)} threads running: {thread_names}",
                category=pytest.PytestWarning,
                stacklevel=2,
            )


@pytest.fixture
def socket_path(tmp_path):
    """Generate a short socket path in /tmp to avoid macOS AF_UNIX path length limit."""
    short_id = uuid.uuid4().hex[:8]
    sock_path = f"/tmp/hyh-test-{short_id}.sock"
    yield sock_path
    # Cleanup socket and lock file
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    lock_path = sock_path + ".lock"
    if os.path.exists(lock_path):
        os.unlink(lock_path)


@pytest.fixture
def worktree(tmp_path):
    """Create a mock worktree with git repo."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    (tmp_path / "file.txt").write_text("content")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True
    )
    return tmp_path


def send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
    """Send command to daemon and get response."""
    sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        sock.sendall(json.dumps(command).encode() + b"\n")
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\n" in response:
                break
        return json.loads(response.decode().strip())
    finally:
        sock.close()


async def async_send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
    """Async version of send_command using asyncio streams.

    Use this in async tests for non-blocking socket communication
    with the daemon.

    Args:
        socket_path: Path to Unix socket.
        command: Command dict to send.
        timeout: Timeout in seconds.

    Returns:
        Response dict from daemon.

    Raises:
        asyncio.TimeoutError: If connection or response times out.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(socket_path),
        timeout=timeout,
    )
    try:
        writer.write(json.dumps(command).encode() + b"\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return json.loads(response.decode().strip())
    finally:
        writer.close()
        await writer.wait_closed()


def send_command_with_retry(socket_path: str, cmd: dict, max_retries: int = 3) -> dict:
    """Send command to daemon with retry on connection refused."""
    for attempt in range(max_retries):
        sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        sock.settimeout(10.0)
        try:
            sock.connect(socket_path)
            sock.sendall(json.dumps(cmd).encode() + b"\n")
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\n" in response:
                    break
            return json.loads(response.decode().strip())
        except ConnectionRefusedError:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
                continue
            raise
        finally:
            sock.close()
    raise ConnectionRefusedError("Failed to connect after retries")


class DaemonManager:
    """Context manager for daemon lifecycle with proper resource cleanup."""

    def __init__(self, socket_path: str, worktree: Path):
        self.socket_path = socket_path
        self.worktree = worktree
        self.daemon = None
        self.server_thread = None

    def __enter__(self):
        from hyh.daemon import HarnessDaemon

        self.daemon = HarnessDaemon(self.socket_path, str(self.worktree))
        self.server_thread = threading.Thread(target=self.daemon.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()
        wait_for_socket(self.socket_path, timeout=2.0)  # Condition-based (was time.sleep(0.1))
        return self.daemon

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.daemon:
            self.daemon.shutdown()
            # CRITICAL: server_close() releases the lock file and socket
            self.daemon.server_close()
        if self.server_thread:
            self.server_thread.join(timeout=2)
        return False


@pytest.fixture
def daemon_manager(socket_path, worktree):
    """Fixture providing a daemon manager with proper cleanup."""
    manager = DaemonManager(socket_path, worktree)
    with manager as daemon:
        yield daemon, worktree
    # Cleanup happens automatically via context manager


def cleanup_daemon_subprocess(socket_path: str, wait_time: float = 1.0) -> None:
    """Gracefully cleanup a daemon subprocess.

    1. Tries graceful shutdown via socket command
    2. Waits for socket to disappear (process exited)
    3. Falls back to pkill + SIGKILL if needed
    4. Cleans up socket files
    """
    import contextlib

    # Try graceful shutdown first
    sock = None
    try:
        sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(socket_path)
        sock.sendall(json.dumps({"command": "shutdown"}).encode() + b"\n")
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        pass
    finally:
        if sock is not None:
            sock.close()

    # Wait for graceful shutdown - use condition-based wait
    def socket_gone() -> bool:
        return not os.path.exists(socket_path)

    # Wait with suppressed timeout - falls through to SIGTERM if not done
    with contextlib.suppress(TimeoutError):
        wait_until(socket_gone, timeout=wait_time, poll_interval=0.05)

    # If socket still exists, use pkill with SIGTERM
    if os.path.exists(socket_path):
        subprocess.run(
            ["pkill", "-TERM", "-f", f"hyh.daemon.*{socket_path}"],
            capture_output=True,
        )
        # Wait for SIGTERM to take effect
        with contextlib.suppress(TimeoutError):
            wait_until(socket_gone, timeout=1.0, poll_interval=0.05)

    # Last resort - SIGKILL
    if os.path.exists(socket_path):
        subprocess.run(
            ["pkill", "-KILL", "-f", f"hyh.daemon.*{socket_path}"],
            capture_output=True,
        )
        with contextlib.suppress(TimeoutError):
            wait_until(socket_gone, timeout=0.5, poll_interval=0.05)

    # Clean up socket files
    if os.path.exists(socket_path):
        with contextlib.suppress(OSError):
            os.unlink(socket_path)
    lock_path = socket_path + ".lock"
    if os.path.exists(lock_path):
        with contextlib.suppress(OSError):
            os.unlink(lock_path)


# =============================================================================
# Task Clock Reset Fixture (for time-machine compatibility)
# =============================================================================


@pytest.fixture(autouse=True)
def reset_task_clock():
    """Reset Task clock after each test to ensure isolation.

    When tests use Task.set_clock() for time mocking, this ensures
    the clock is reset to the default datetime.now(UTC) after each test.
    """
    yield
    # Import here to avoid circular imports at module load time
    from hyh.state import Task

    Task.reset_clock()


# =============================================================================
# Session-scoped Git Template (reduces subprocess overhead)
# =============================================================================


@pytest.fixture(scope="session")
def git_template_dir(tmp_path_factory) -> Path:
    """Create a template git repo once per session.

    This saves ~3 subprocess calls per test by reusing the initialized
    git config across all tests.
    """
    template = tmp_path_factory.mktemp("git_template")
    subprocess.run(["git", "init"], cwd=template, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=template,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=template,
        capture_output=True,
        check=True,
    )
    return template


@pytest.fixture
def fast_worktree(tmp_path: Path, git_template_dir: Path) -> Path:
    """Fast worktree by copying pre-initialized git template.

    Only requires 2 subprocess calls (add + commit) instead of 5.
    Use this fixture for tests that need a git repo but don't need
    specific git history.
    """
    import shutil

    worktree = tmp_path / "worktree"
    shutil.copytree(git_template_dir, worktree)
    (worktree / "file.txt").write_text("content")
    subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=worktree,
        capture_output=True,
        check=True,
    )
    return worktree


@pytest.fixture(scope="session")
def worker_id(request: pytest.FixtureRequest) -> str:
    """Return xdist worker id or 'master' for non-parallel runs.

    Useful for creating worker-specific resources when running
    tests in parallel with pytest-xdist.
    """
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"
