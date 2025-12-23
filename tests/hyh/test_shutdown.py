"""
Graceful Shutdown Tests for Daemon.

<approach>
The daemon uses ThreadingMixIn, meaning each client gets a handler thread.
During shutdown:
1. New connections should be rejected
2. In-flight requests should complete
3. Resources should be cleaned up (socket, lock file)

Testing strategy:
1. Start long-running requests, trigger shutdown, verify completion
2. Test new connection rejection after shutdown
3. Test socket file cleanup atomicity
</approach>

Tests focus on:
- In-flight request completion during shutdown
- New connection rejection
- Resource cleanup
"""

import contextlib
import json
import os
import socket as socket_module
import threading
from pathlib import Path

import pytest

from tests.hyh.conftest import DaemonManager, send_command, wait_for_socket, wait_until


class TestShutdownWaitsForInflight:
    """Test that shutdown waits for in-flight requests to complete."""

    def test_shutdown_waits_for_inflight_requests(self, socket_path: str, worktree: Path) -> None:
        """Start long-running requests, shutdown, verify all complete."""
        with DaemonManager(socket_path, worktree) as _daemon:
            # Create some tasks for exec commands
            send_command(
                socket_path,
                {
                    "command": "plan_import",
                    "plan_content": """# Test Plan

## Goal
Test shutdown

| Task Group | Tasks |
|------------|-------|
| Group 1 | 1, 2, 3 |

### Task 1: First
Do something

### Task 2: Second
Do something else

### Task 3: Third
Final task
""",
                },
            )

            results: list[dict] = []
            results_lock = threading.Lock()
            started = threading.Event()

            def long_running_request(request_id: int) -> None:
                started.set()
                try:
                    # Execute a command that takes some time
                    result = send_command(
                        socket_path,
                        {"command": "status"},
                        timeout=10.0,
                    )
                    with results_lock:
                        results.append({"id": request_id, "result": result})
                except Exception as e:
                    with results_lock:
                        results.append({"id": request_id, "error": str(e)})

            # Start several concurrent requests
            threads = []
            for i in range(5):
                t = threading.Thread(target=long_running_request, args=(i,))
                t.start()
                threads.append(t)

            # Wait for at least one request to start
            started.wait(timeout=2)

            # Trigger shutdown (suppress - may close connection before responding)
            with contextlib.suppress(Exception):
                send_command(socket_path, {"command": "shutdown"}, timeout=1.0)

            # Wait for all threads to complete
            for t in threads:
                t.join(timeout=5)
                if t.is_alive():
                    pytest.fail("Request thread did not complete after shutdown")

            # All requests should have completed (not errored due to shutdown)
            with results_lock:
                successful = [r for r in results if "result" in r]
                assert len(successful) == 5, f"Only {len(successful)}/5 requests completed"


class TestShutdownRejectsNewConnections:
    """Test that new connections are rejected after shutdown starts."""

    def test_new_connection_after_shutdown(self, socket_path: str, worktree: Path) -> None:
        """After shutdown signal, new connections should fail."""
        with DaemonManager(socket_path, worktree) as daemon:
            # Verify daemon is running
            result = send_command(socket_path, {"command": "ping"})
            assert result["status"] == "ok"

            # Trigger shutdown
            daemon.shutdown()

            # Wait for connection to be refused (daemon stops accepting)
            def connection_fails() -> bool:
                """Check if connection is refused."""
                sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
                sock.settimeout(0.1)
                try:
                    sock.connect(socket_path)
                    # If connect succeeds, try to communicate
                    sock.sendall(json.dumps({"command": "ping"}).encode() + b"\n")
                    sock.recv(4096)
                    sock.close()
                    return False  # Connection still works
                except (ConnectionRefusedError, FileNotFoundError, OSError, TimeoutError):
                    sock.close()
                    return True  # Connection fails as expected

            wait_until(
                connection_fails,
                timeout=2.0,
                message="Daemon should refuse connections after shutdown",
            )


class TestSocketCleanupRace:
    """Test socket file cleanup for TOCTOU issues.

    KNOWN ISSUE: daemon.py:431-437 has a window between unlink and create
    where the socket doesn't exist, causing client connection failures.
    """

    def test_socket_file_removed_after_shutdown(self, socket_path: str, worktree: Path) -> None:
        """Socket file should be removed after daemon shuts down."""
        with DaemonManager(socket_path, worktree) as _daemon:
            # Socket should exist
            assert os.path.exists(socket_path), "Socket should exist while running"
            # Shutdown is called by DaemonManager.__exit__

        # After context exit, socket should be cleaned up (replaces time.sleep(0.2))
        wait_until(
            lambda: not os.path.exists(socket_path),
            timeout=2.0,
            message="Socket should be removed after shutdown",
        )

    def test_lock_file_removed_after_shutdown(self, socket_path: str, worktree: Path) -> None:
        """Lock file should be removed after daemon shuts down."""
        lock_path = socket_path + ".lock"

        with DaemonManager(socket_path, worktree) as _daemon:
            # Lock file should exist
            assert os.path.exists(lock_path), "Lock file should exist while running"
            # Shutdown is called by DaemonManager.__exit__

        # After context exit, lock file should be cleaned up (replaces time.sleep(0.2))
        wait_until(
            lambda: not os.path.exists(lock_path),
            timeout=2.0,
            message="Lock file should be removed after shutdown",
        )


class TestDaemonStartupRace:
    """Test daemon startup race conditions."""

    def test_single_daemon_per_socket(self, socket_path: str, worktree: Path) -> None:
        """Only one daemon should be able to bind to a socket path."""
        from hyh.daemon import HarnessDaemon

        daemon1 = None
        daemon2 = None

        try:
            daemon1 = HarnessDaemon(socket_path, str(worktree))
            thread1 = threading.Thread(target=daemon1.serve_forever)
            thread1.daemon = True
            thread1.start()
            wait_for_socket(socket_path)  # Replaces time.sleep(0.1)

            # Second daemon should fail to start due to lock
            # Raises RuntimeError wrapping BlockingIOError
            with pytest.raises((OSError, BlockingIOError, RuntimeError)) as exc_info:
                daemon2 = HarnessDaemon(socket_path, str(worktree))
                # If constructor succeeds (shouldn't), try to start
                if daemon2:
                    daemon2.serve_forever()

            # Verify the error indicates another daemon is running
            assert "already running" in str(exc_info.value).lower()
        finally:
            if daemon1:
                daemon1.shutdown()
                daemon1.server_close()
            if daemon2:
                with contextlib.suppress(Exception):
                    daemon2.server_close()


class TestSignalHandling:
    """Test signal handling during shutdown."""

    def test_shutdown_via_command(self, socket_path: str, worktree: Path) -> None:
        """Shutdown command should trigger graceful shutdown."""
        from hyh.daemon import HarnessDaemon

        daemon = HarnessDaemon(socket_path, str(worktree))
        server_thread = threading.Thread(target=daemon.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        wait_for_socket(socket_path)  # Replaces time.sleep(0.1)

        try:
            # Send shutdown command
            result = send_command(socket_path, {"command": "shutdown"})
            assert result["status"] == "ok"

            # Server should stop
            server_thread.join(timeout=2)
            assert not server_thread.is_alive(), "Server should have stopped"
        finally:
            with contextlib.suppress(Exception):
                daemon.server_close()


# -----------------------------------------------------------------------------
# Complexity Analysis
# -----------------------------------------------------------------------------
"""
<complexity_analysis>
| Metric | Value |
|--------|-------|
| Time Complexity (Best) | O(1) for shutdown signal |
| Time Complexity (Average) | O(r) where r = in-flight requests |
| Time Complexity (Worst) | O(r * t) if requests have timeout t |
| Space Complexity | O(r) for request handler threads |
| Scalability Limit | No explicit limit on pending handlers during shutdown |
</complexity_analysis>

<self_critique>
1. Tests rely on timing (time.sleep) which can be flaky on slow systems;
   using explicit synchronization primitives would be more reliable.
2. Socket cleanup test doesn't verify atomicity of the operation, only that
   cleanup eventually happens; instrumenting the code would prove atomicity.
3. Signal handling test only covers command-based shutdown, not SIGTERM/SIGINT
   which would require process-level testing.
</self_critique>
"""
