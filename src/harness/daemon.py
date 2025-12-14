# src/harness/daemon.py
"""
Harness Daemon - Thread-safe state management server.

Uses socketserver.ThreadingUnixStreamServer for true parallelism
in Python 3.13t (free-threading). No asyncio.

Each client connection gets a real OS thread that runs in parallel.
Pydantic validation happens here - the client is deliberately "dumb".
"""

import fcntl
import json
import os
import signal
import socketserver
import sys
import threading
from pathlib import Path

from .state import StateManager, TaskStatus
from .git import safe_git_exec
from .trajectory import TrajectoryLogger
from .runtime import create_runtime, decode_signal


class HarnessHandler(socketserver.StreamRequestHandler):
    """
    Handle a single client connection.

    In Python 3.13t, this runs in a parallel thread without GIL contention.
    CPU-heavy Pydantic validation happens here, not in the client.
    """

    def handle(self):
        try:
            line = self.rfile.readline()
            if not line:
                return

            request = json.loads(line.decode())
            response = self.dispatch(request)
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except Exception as e:
            error_response = {"status": "error", "message": str(e)}
            self.wfile.write(json.dumps(error_response).encode() + b"\n")

    def dispatch(self, request: dict) -> dict:
        """Route command to handler."""
        command = request.get("command")
        server = self.server  # type: HarnessDaemon

        handlers = {
            "get_state": self._handle_get_state,
            "update_state": self._handle_update_state,
            "git": self._handle_git,
            "ping": self._handle_ping,
            "shutdown": self._handle_shutdown,
            "task_claim": self._handle_task_claim,
            "task_complete": self._handle_task_complete,
            "exec": self._handle_exec,
        }

        handler = handlers.get(command)
        if not handler:
            return {"status": "error", "message": f"Unknown command: {command}"}

        return handler(request, server)

    def _handle_get_state(self, request: dict, server: "HarnessDaemon") -> dict:
        state = server.state_manager.load()
        if state is None:
            return {"status": "ok", "data": None}
        return {"status": "ok", "data": state.model_dump()}

    def _handle_update_state(self, request: dict, server: "HarnessDaemon") -> dict:
        updates = request.get("updates", {})
        if not updates:
            return {"status": "error", "message": "No updates provided"}
        try:
            # Pydantic validation happens here (CPU-heavy, parallel thread)
            updated = server.state_manager.update(**updates)
            return {"status": "ok", "data": updated.model_dump()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_git(self, request: dict, server: "HarnessDaemon") -> dict:
        args = request.get("args", [])
        cwd = request.get("cwd", str(server.worktree_root))
        result = safe_git_exec(args, cwd)
        return {
            "status": "ok",
            "data": {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        }

    def _handle_ping(self, request: dict, server: "HarnessDaemon") -> dict:
        return {"status": "ok", "data": {"running": True, "pid": os.getpid()}}

    def _handle_shutdown(self, request: dict, server: "HarnessDaemon") -> dict:
        # Schedule shutdown in separate thread to allow response
        threading.Thread(target=server.shutdown, daemon=True).start()
        return {"status": "ok", "data": {"shutdown": True}}

    def _handle_task_claim(self, request: dict, server: "HarnessDaemon") -> dict:
        """Claim a task for a worker (atomic operation with idempotency)."""
        worker_id = request.get("worker_id")
        if not worker_id:
            return {"status": "error", "message": "worker_id is required"}

        try:
            # Check state BEFORE claiming to determine retry/reclaim flags
            state_before = server.state_manager.load()
            existing_task_id = None
            task_was_timed_out = False

            if state_before:
                # Check if worker already has a running task (for retry detection)
                for task in state_before.tasks.values():
                    if (
                        task.claimed_by == worker_id
                        and task.status == TaskStatus.RUNNING
                    ):
                        existing_task_id = task.id
                        break

                # Check if the claimable task is a timed-out task
                claimable = state_before.get_claimable_task()
                if claimable and claimable.status == TaskStatus.RUNNING and claimable.is_timed_out():
                    task_was_timed_out = True

            # Atomically claim task (state lock released inside this call)
            task = server.state_manager.claim_task(worker_id)

            if not task:
                return {"status": "ok", "data": {"task": None}}

            # Determine flags
            is_retry = existing_task_id == task.id
            is_reclaim = task_was_timed_out and not is_retry

            # Log to trajectory AFTER state lock is released (lock convoy fix)
            server.trajectory_logger.log(
                {
                    "event_type": "task_claim",
                    "task_id": task.id,
                    "worker_id": worker_id,
                    "is_retry": is_retry,
                    "is_reclaim": is_reclaim,
                }
            )

            return {
                "status": "ok",
                "data": {
                    "task": task.model_dump(mode='json'),
                    "is_retry": is_retry,
                    "is_reclaim": is_reclaim,
                },
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_task_complete(self, request: dict, server: "HarnessDaemon") -> dict:
        """Complete a task with ownership validation."""
        task_id = request.get("task_id")
        worker_id = request.get("worker_id")

        if not task_id:
            return {"status": "error", "message": "task_id is required"}
        if not worker_id:
            return {"status": "error", "message": "worker_id is required"}

        try:
            # Atomically complete task (validates ownership, state lock released inside)
            server.state_manager.complete_task(task_id, worker_id)

            # Log to trajectory AFTER state lock is released (lock convoy fix)
            server.trajectory_logger.log(
                {
                    "event_type": "task_complete",
                    "task_id": task_id,
                    "worker_id": worker_id,
                }
            )

            return {"status": "ok", "data": {"task_id": task_id}}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_exec(self, request: dict, server: "HarnessDaemon") -> dict:
        """Execute a command using the runtime."""
        import subprocess

        cmd = request.get("cmd", [])
        cwd = request.get("cwd")
        env = request.get("env")
        timeout = request.get("timeout")
        exclusive = request.get("exclusive", False)

        if not cmd:
            return {"status": "error", "message": "cmd is required"}

        try:
            # Execute command
            result = server.runtime.execute(
                command=cmd,
                cwd=cwd,
                env=env,
                timeout=timeout,
                exclusive=exclusive,
            )

            # Decode signal if returncode is negative
            signal_name = decode_signal(result.returncode) if result.returncode < 0 else None

            # Log to trajectory
            server.trajectory_logger.log(
                {
                    "event_type": "exec",
                    "cmd": cmd,
                    "returncode": result.returncode,
                    "signal_name": signal_name,
                    "stdout": result.stdout[:200] if result.stdout else "",  # Truncate for log
                    "stderr": result.stderr[:200] if result.stderr else "",
                }
            )

            response_data = {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            if signal_name:
                response_data["signal_name"] = signal_name

            return {"status": "ok", "data": response_data}
        except subprocess.TimeoutExpired as e:
            # Timeout is a normal case - return success with timeout info
            # Process was killed with SIGTERM (-15)
            signal_name = "SIGTERM"
            server.trajectory_logger.log(
                {
                    "event_type": "exec",
                    "cmd": cmd,
                    "returncode": -15,
                    "signal_name": signal_name,
                    "timeout": True,
                }
            )
            return {
                "status": "ok",
                "data": {
                    "returncode": -15,
                    "stdout": e.stdout.decode() if e.stdout else "",
                    "stderr": e.stderr.decode() if e.stderr else "",
                    "signal_name": signal_name,
                },
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


class HarnessDaemon(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """
    Threaded Unix socket server for workflow state management.

    ThreadingMixIn + Python 3.13t = true parallel thread execution.
    """

    daemon_threads = True  # Auto-kill threads on exit
    allow_reuse_address = True

    def __init__(self, socket_path: str, worktree_root: str):
        self.socket_path = socket_path
        self.worktree_root = Path(worktree_root)
        self.state_manager = StateManager(self.worktree_root)
        self.trajectory_logger = TrajectoryLogger(
            self.worktree_root / ".claude" / "trajectory.jsonl"
        )
        self.runtime = create_runtime()
        self._lock_fd = None

        # Acquire exclusive lock to prevent multiple daemons
        self._acquire_lock()

        # Remove stale socket
        if os.path.exists(socket_path):
            os.unlink(socket_path)

        # Create socket with restrictive permissions from the start
        old_umask = os.umask(0o077)
        try:
            super().__init__(socket_path, HarnessHandler)
        finally:
            os.umask(old_umask)

        # Ensure socket permissions (user only)
        os.chmod(socket_path, 0o600)

        # Load initial state
        self.state_manager.load()

    def _acquire_lock(self):
        """Acquire exclusive lock on socket path (idempotency)."""
        self._lock_path = self.socket_path + ".lock"
        self._lock_fd = open(self._lock_path, "w")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_fd.close()
            raise RuntimeError("Another daemon is already running")

    def server_close(self):
        """Clean up on shutdown."""
        super().server_close()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        if self._lock_fd:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
            # Clean up lock file
            if hasattr(self, "_lock_path") and os.path.exists(self._lock_path):
                try:
                    os.unlink(self._lock_path)
                except OSError:
                    pass  # Best effort cleanup


def run_daemon(socket_path: str, worktree_root: str) -> None:
    """Entry point for running daemon as subprocess."""
    daemon = HarnessDaemon(socket_path, worktree_root)

    def handle_sigterm(*args):
        daemon.shutdown()

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    try:
        daemon.serve_forever()
    finally:
        daemon.server_close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m harness.daemon <socket_path> <worktree_root>")
        sys.exit(1)
    run_daemon(sys.argv[1], sys.argv[2])
