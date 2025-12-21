# tests/harness/conftest.py
"""
Shared pytest fixtures for harness tests.

Centralizes daemon management to ensure proper resource cleanup
and eliminate ResourceWarning issues.
"""

import json
import os
import socket as socket_module
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_git_config(monkeypatch):
    """Isolate tests from user's global git config.

    Prevents GPG signing, custom hooks, and other user config
    from affecting test execution.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


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
    timeout = 5.0
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        current = {t for t in threading.enumerate() if not t.daemon and t.is_alive()}
        new_threads = current - before
        # Filter out the main thread
        new_threads = {t for t in new_threads if t.name != "MainThread"}
        if not new_threads:
            break
        time.sleep(0.05)
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
    sock_path = f"/tmp/harness-test-{short_id}.sock"
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
        from harness.daemon import HarnessDaemon

        self.daemon = HarnessDaemon(self.socket_path, str(self.worktree))
        self.server_thread = threading.Thread(target=self.daemon.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()
        time.sleep(0.1)  # Let daemon fully start
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

    # Wait for graceful shutdown - check socket disappears
    for _ in range(int(wait_time * 10)):  # 10 iterations per second
        if not os.path.exists(socket_path):
            break
        time.sleep(0.1)

    # If socket still exists, use pkill with SIGTERM
    if os.path.exists(socket_path):
        subprocess.run(
            ["pkill", "-TERM", "-f", f"harness.daemon.*{socket_path}"],
            capture_output=True,
        )
        # Wait for SIGTERM to take effect
        for _ in range(10):  # 1 second max
            if not os.path.exists(socket_path):
                break
            time.sleep(0.1)

    # Last resort - SIGKILL
    if os.path.exists(socket_path):
        subprocess.run(
            ["pkill", "-KILL", "-f", f"harness.daemon.*{socket_path}"],
            capture_output=True,
        )
        time.sleep(0.3)

    # Clean up socket files
    import contextlib

    if os.path.exists(socket_path):
        with contextlib.suppress(OSError):
            os.unlink(socket_path)
    lock_path = socket_path + ".lock"
    if os.path.exists(lock_path):
        with contextlib.suppress(OSError):
            os.unlink(lock_path)
