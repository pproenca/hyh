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

from .state import StateManager
from .git import safe_git_exec


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
