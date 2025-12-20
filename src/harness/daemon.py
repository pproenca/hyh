# src/harness/daemon.py
"""
Harness Daemon - Thread-safe state management server.

Uses socketserver.ThreadingUnixStreamServer for true parallelism
in Python 3.13t (free-threading). No asyncio.

Each client connection gets a real OS thread that runs in parallel.
Pydantic validation happens here - the client is deliberately "dumb".
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal as signal_module
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from io import TextIOWrapper
from pathlib import Path
from types import FrameType
from typing import Any

from .acp import ACPEmitter
from .git import safe_git_exec
from .plan import parse_plan_content
from .registry import ProjectRegistry
from .runtime import Runtime, create_runtime, decode_signal
from .state import StateManager
from .trajectory import TrajectoryLogger

# Truncation limit for trajectory logs - agents need enough context to debug
TRUNCATE_LIMIT = 4096


class HarnessHandler(socketserver.StreamRequestHandler):
    """
    Handle a single client connection.

    In Python 3.13t, this runs in a parallel thread without GIL contention.
    CPU-heavy Pydantic validation happens here, not in the client.
    """

    server: HarnessDaemon

    def handle(self) -> None:
        try:
            line = self.rfile.readline()
            if not line:
                return

            request: dict[str, Any] = json.loads(line.decode())
            response = self.dispatch(request)
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except Exception as e:
            error_response = {"status": "error", "message": str(e)}
            self.wfile.write(json.dumps(error_response).encode() + b"\n")

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Route command to handler."""
        command = request.get("command")
        server = self.server

        handlers: dict[
            str,
            Callable[[dict[str, Any], HarnessDaemon], dict[str, Any]],
        ] = {
            "get_state": self._handle_get_state,
            "status": self._handle_status,
            "update_state": self._handle_update_state,
            "git": self._handle_git,
            "ping": self._handle_ping,
            "shutdown": self._handle_shutdown,
            "task_claim": self._handle_task_claim,
            "task_complete": self._handle_task_complete,
            "exec": self._handle_exec,
            "plan_import": self._handle_plan_import,
            "plan_reset": self._handle_plan_reset,
        }

        if command is None:
            return {"status": "error", "message": "Missing command"}

        handler = handlers.get(command)
        if not handler:
            return {"status": "error", "message": f"Unknown command: {command}"}

        return handler(request, server)

    def _handle_get_state(self, _request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
        state = server.state_manager.load()
        if state is None:
            return {"status": "ok", "data": None}
        return {"status": "ok", "data": state.model_dump(mode="json")}

    def _handle_status(self, request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
        """Return workflow status summary with task counts and recent events."""
        from .state import TaskStatus

        state = server.state_manager.load()

        if state is None:
            return {
                "status": "ok",
                "data": {
                    "active": False,
                    "summary": {
                        "total": 0,
                        "completed": 0,
                        "running": 0,
                        "pending": 0,
                        "failed": 0,
                    },
                    "tasks": {},
                    "events": [],
                    "active_workers": [],
                },
            }

        tasks = state.tasks
        summary = {
            "total": len(tasks),
            "completed": sum(1 for t in tasks.values() if t.status == TaskStatus.COMPLETED),
            "running": sum(1 for t in tasks.values() if t.status == TaskStatus.RUNNING),
            "pending": sum(1 for t in tasks.values() if t.status == TaskStatus.PENDING),
            "failed": sum(1 for t in tasks.values() if t.status == TaskStatus.FAILED),
        }

        active_workers = list(
            {
                t.claimed_by
                for t in tasks.values()
                if t.status == TaskStatus.RUNNING and t.claimed_by
            }
        )

        events = server.trajectory_logger.tail(n=request.get("event_count", 10))

        return {
            "status": "ok",
            "data": {
                "active": True,
                "summary": summary,
                "tasks": {tid: t.model_dump(mode="json") for tid, t in tasks.items()},
                "events": events,
                "active_workers": active_workers,
            },
        }

    def _handle_update_state(
        self, request: dict[str, Any], server: HarnessDaemon
    ) -> dict[str, Any]:
        updates = request.get("updates", {})
        if not updates:
            return {"status": "error", "message": "No updates provided"}
        try:
            # Pydantic validation happens here (CPU-heavy, parallel thread)
            updated = server.state_manager.update(**updates)
            return {"status": "ok", "data": updated.model_dump(mode="json")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_git(self, request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
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

    def _handle_ping(self, _request: dict[str, Any], _server: HarnessDaemon) -> dict[str, Any]:
        return {"status": "ok", "data": {"running": True, "pid": os.getpid()}}

    def _handle_shutdown(self, _request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
        # Schedule shutdown in separate thread to allow response
        threading.Thread(target=server.shutdown, daemon=True).start()
        return {"status": "ok", "data": {"shutdown": True}}

    def _handle_task_claim(self, request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
        """Claim a task for a worker (atomic operation with idempotency)."""
        worker_id = request.get("worker_id")
        if not worker_id:
            return {"status": "error", "message": "worker_id is required"}

        try:
            # Atomically claim task with is_retry/is_reclaim computed inside lock
            claim_result = server.state_manager.claim_task(worker_id)

            if not claim_result.task:
                return {"status": "ok", "data": {"task": None}}

            task = claim_result.task

            # Log to trajectory AFTER state lock is released to avoid holding the lock during I/O
            server.trajectory_logger.log(
                {
                    "event_type": "task_claim",
                    "task_id": task.id,
                    "worker_id": worker_id,
                    "is_retry": claim_result.is_retry,
                    "is_reclaim": claim_result.is_reclaim,
                }
            )
            if server.acp_emitter:
                server.acp_emitter.emit(
                    {
                        "event_type": "task_claim",
                        "task_id": task.id,
                        "worker_id": worker_id,
                    }
                )

            return {
                "status": "ok",
                "data": {
                    "task": task.model_dump(mode="json"),
                    "is_retry": claim_result.is_retry,
                    "is_reclaim": claim_result.is_reclaim,
                },
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_task_complete(
        self, request: dict[str, Any], server: HarnessDaemon
    ) -> dict[str, Any]:
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

            # Log to trajectory AFTER state lock is released to avoid holding the lock during I/O
            server.trajectory_logger.log(
                {
                    "event_type": "task_complete",
                    "task_id": task_id,
                    "worker_id": worker_id,
                }
            )
            if server.acp_emitter:
                server.acp_emitter.emit(
                    {
                        "event_type": "task_complete",
                        "task_id": task_id,
                    }
                )

            return {"status": "ok", "data": {"task_id": task_id}}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_exec(self, request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
        """Execute a command using the runtime."""
        args = request.get("args", [])
        cwd = request.get("cwd")
        env = request.get("env")
        timeout = request.get("timeout")
        exclusive = request.get("exclusive", False)

        if not args:
            return {"status": "error", "message": "args is required"}

        try:
            start_time = time.monotonic()
            result = server.runtime.execute(
                command=args,
                cwd=cwd,
                env=env,
                timeout=timeout,
                exclusive=exclusive,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            signal_name = decode_signal(result.returncode) if result.returncode < 0 else None

            server.trajectory_logger.log(
                {
                    "event_type": "exec",
                    "args": args,
                    "returncode": result.returncode,
                    "signal_name": signal_name,
                    "stdout": result.stdout[:TRUNCATE_LIMIT] if result.stdout else "",
                    "stderr": result.stderr[:TRUNCATE_LIMIT] if result.stderr else "",
                    "duration_ms": duration_ms,
                }
            )

            response_data: dict[str, Any] = {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            if signal_name:
                response_data["signal_name"] = signal_name

            return {"status": "ok", "data": response_data}
        except subprocess.TimeoutExpired as e:
            # Timeout is a normal case - return success with timeout info

            duration_ms = int((time.monotonic() - start_time) * 1000)
            signal_name = "SIGTERM"
            server.trajectory_logger.log(
                {
                    "event_type": "exec",
                    "args": args,
                    "returncode": -15,
                    "signal_name": signal_name,
                    "timeout": True,
                    "duration_ms": duration_ms,
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

    def _handle_plan_import(self, request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
        """Import plan from LLM output."""
        content = request.get("content")
        if not content:
            return {"status": "error", "message": "content required"}

        try:
            plan = parse_plan_content(content)
            state = plan.to_workflow_state()
            server.state_manager.save(state)

            server.trajectory_logger.log(
                {
                    "event_type": "plan_import",
                    "goal": plan.goal,
                    "task_count": len(plan.tasks),
                }
            )
            if server.acp_emitter:
                server.acp_emitter.emit(
                    {
                        "event_type": "plan_import",
                        "goal": plan.goal,
                        "task_count": len(plan.tasks),
                    }
                )

            return {"status": "ok", "data": {"goal": plan.goal, "task_count": len(plan.tasks)}}
        except ValueError as e:
            msg = str(e)
            if "No valid plan found" in msg:
                msg += ". Run 'harness plan template' to see the required format."
            return {"status": "error", "message": msg}

    def _handle_plan_reset(self, _request: dict[str, Any], server: HarnessDaemon) -> dict[str, Any]:
        """Reset workflow state (clear all tasks)."""
        server.state_manager.reset()

        server.trajectory_logger.log({"event_type": "plan_reset"})
        if server.acp_emitter:
            server.acp_emitter.emit({"event_type": "plan_reset"})

        return {"status": "ok", "data": {"message": "Workflow state cleared"}}


class HarnessDaemon(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """
    Threaded Unix socket server for workflow state management.

    ThreadingMixIn + Python 3.13t = true parallel thread execution.
    """

    daemon_threads = True
    allow_reuse_address = True

    socket_path: str
    worktree_root: Path
    state_manager: StateManager
    trajectory_logger: TrajectoryLogger
    acp_emitter: ACPEmitter | None
    runtime: Runtime
    _lock_fd: TextIOWrapper | None
    _lock_path: str

    def __init__(
        self,
        socket_path: str,
        worktree_root: str,
        *,
        acp_emitter: ACPEmitter | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.worktree_root = Path(worktree_root)
        self.state_manager = StateManager(self.worktree_root)
        self.trajectory_logger = TrajectoryLogger(
            self.worktree_root / ".claude" / "trajectory.jsonl"
        )
        self.acp_emitter = acp_emitter

        registry = ProjectRegistry()
        registry.register(self.worktree_root)
        self.runtime = create_runtime()
        self.runtime.check_capabilities()  # Fail fast if dependencies unavailable
        self._lock_fd = None

        # Acquire exclusive lock to prevent multiple daemons
        self._acquire_lock()

        if Path(socket_path).exists():
            Path(socket_path).unlink()

        # Create socket with restrictive permissions from the start
        old_umask = os.umask(0o077)
        try:
            super().__init__(socket_path, HarnessHandler)
        finally:
            os.umask(old_umask)

        Path(socket_path).chmod(0o600)

        self.state_manager.load()

    def _acquire_lock(self) -> None:
        """Acquire exclusive lock on socket path (idempotency)."""
        self._lock_path = self.socket_path + ".lock"
        lock_path = Path(self._lock_path)
        self._lock_fd = lock_path.open("w")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            self._lock_fd.close()
            raise RuntimeError("Another daemon is already running") from err

    def server_close(self) -> None:
        """Clean up on shutdown."""
        super().server_close()
        if self.acp_emitter:
            self.acp_emitter.close()
        socket_path = Path(self.socket_path)
        if socket_path.exists():
            socket_path.unlink()
        if self._lock_fd:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()

            lock_path = Path(self._lock_path)
            with contextlib.suppress(OSError):
                lock_path.unlink(missing_ok=True)


def run_daemon(socket_path: str, worktree_root: str) -> None:
    """Entry point for running daemon as subprocess."""
    daemon = HarnessDaemon(socket_path, worktree_root)

    def handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
        # shutdown() must be called from a different thread than serve_forever()
        threading.Thread(target=daemon.shutdown, daemon=True).start()

    signal_module.signal(signal_module.SIGTERM, handle_sigterm)
    signal_module.signal(signal_module.SIGINT, handle_sigterm)

    try:
        daemon.serve_forever()
    finally:
        daemon.server_close()
        sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m harness.daemon <socket_path> <worktree_root>")
        sys.exit(1)
    run_daemon(sys.argv[1], sys.argv[2])
