import contextlib
import fcntl
import os
import signal as signal_module
import socketserver
import subprocess
import sys
import threading
import time
from io import TextIOWrapper
from pathlib import Path
from types import FrameType
from typing import Annotated, Final, Literal

import msgspec
from msgspec import Meta, Struct, field

from .acp import ACPEmitter
from .git import safe_git_exec
from .plan import parse_plan_content
from .registry import ProjectRegistry
from .runtime import Runtime, create_runtime, decode_signal
from .state import WorkflowStateStore
from .trajectory import TrajectoryLogger

TRUNCATE_LIMIT: Final[int] = 4096


# -- Request Types (Tagged Union) --


class GetStateRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="get_state", tag_field="command"
):
    pass


class StatusRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="status", tag_field="command"
):
    event_count: int = 10


class UpdateStateRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="update_state", tag_field="command"
):
    updates: dict[str, object]


class GitRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="git", tag_field="command"):
    args: list[str] = field(default_factory=list)
    cwd: str | None = None


class PingRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="ping", tag_field="command"):
    pass


class ShutdownRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="shutdown", tag_field="command"
):
    pass


class TaskClaimRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="task_claim", tag_field="command"
):
    worker_id: str

    def __post_init__(self) -> None:
        if not self.worker_id or not self.worker_id.strip():
            raise msgspec.ValidationError("worker_id cannot be empty or whitespace")


class TaskCompleteRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="task_complete", tag_field="command"
):
    task_id: str
    worker_id: str
    force: bool = False

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_id.strip():
            raise msgspec.ValidationError("task_id cannot be empty or whitespace")
        if not self.worker_id or not self.worker_id.strip():
            raise msgspec.ValidationError("worker_id cannot be empty or whitespace")


class ExecRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="exec", tag_field="command"):
    args: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: Annotated[float, Meta(gt=0)] | None = None
    exclusive: bool = False


class PlanImportRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="plan_import", tag_field="command"
):
    content: str

    def __post_init__(self) -> None:
        if not self.content or not self.content.strip():
            raise msgspec.ValidationError("content cannot be empty or whitespace")


class PlanResetRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="plan_reset", tag_field="command"
):
    pass


class ContextPreserveRequest(
    Struct, forbid_unknown_fields=True, frozen=True, tag="context_preserve", tag_field="command"
):
    """Request to preserve context for PreCompact hook."""

    pass


type Request = (
    GetStateRequest
    | StatusRequest
    | UpdateStateRequest
    | GitRequest
    | PingRequest
    | ShutdownRequest
    | TaskClaimRequest
    | TaskCompleteRequest
    | ExecRequest
    | PlanImportRequest
    | PlanResetRequest
    | ContextPreserveRequest
)


# -- Response Types (Result ADT) --


class Ok(Struct, forbid_unknown_fields=True, frozen=True, tag="ok", tag_field="status"):
    """Success response wrapper."""

    data: object


class Err(Struct, forbid_unknown_fields=True, frozen=True, tag="error", tag_field="status"):
    """Error response wrapper."""

    message: str


type Result = Ok | Err


# -- Response Data Types --


class GetStateData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for get_state."""

    state: dict[str, object] | None


class GitData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for git commands."""

    returncode: int
    stdout: str
    stderr: str


class PingData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for ping."""

    running: Literal[True]
    pid: int


class ShutdownData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for shutdown."""

    shutdown: Literal[True]


class PlanResetData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for plan_reset."""

    message: str


class StatusSummary(Struct, forbid_unknown_fields=True, frozen=True):
    """Summary counts for status response."""

    total: int
    completed: int
    running: int
    pending: int
    failed: int


class StatusData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for status command."""

    active: bool
    summary: StatusSummary
    tasks: dict[str, object]
    events: list[dict[str, object]]
    active_workers: list[str]


class UpdateStateData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for update_state."""

    state: dict[str, object]


class TaskClaimData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for task_claim."""

    task: dict[str, object] | None
    is_retry: bool = False
    is_reclaim: bool = False


class TaskCompleteData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for task_complete."""

    task_id: str


class ExecData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for exec commands."""

    returncode: int
    stdout: str
    stderr: str
    signal_name: str | None = None


class PlanImportData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for plan_import."""

    goal: str
    task_count: int


class ContextPreserveData(Struct, forbid_unknown_fields=True, frozen=True):
    """Response data for context_preserve."""

    path: str | None = None
    completed: int = 0
    total: int = 0
    message: str | None = None


class HarnessHandler(socketserver.StreamRequestHandler):
    server: HarnessDaemon

    def handle(self) -> None:
        try:
            line = self.rfile.readline()
            if not line:
                return

            response_bytes = self.dispatch(line.strip())
            self.wfile.write(response_bytes + b"\n")
        except Exception as e:
            error_response = msgspec.json.encode(Err(message=str(e)))
            self.wfile.write(error_response + b"\n")

    def dispatch(self, raw: bytes) -> bytes:
        """Dispatch typed request to handler methods.

        Args:
            raw: Raw JSON bytes from client

        Returns:
            JSON-encoded Result (Ok or Err)
        """
        try:
            request = msgspec.json.decode(raw, type=Request)
        except (msgspec.ValidationError, msgspec.DecodeError) as e:
            return msgspec.json.encode(Err(message=f"Invalid request: {e}"))

        server = self.server

        # Pattern match on typed request union - all handlers return Result
        match request:
            case GetStateRequest():
                result = self._handle_get_state(request, server)
            case StatusRequest():
                result = self._handle_status(request, server)
            case UpdateStateRequest():
                result = self._handle_update_state(request, server)
            case GitRequest():
                result = self._handle_git(request, server)
            case PingRequest():
                result = self._handle_ping(request, server)
            case ShutdownRequest():
                result = self._handle_shutdown(request, server)
            case TaskClaimRequest():
                result = self._handle_task_claim(request, server)
            case TaskCompleteRequest():
                result = self._handle_task_complete(request, server)
            case ExecRequest():
                result = self._handle_exec(request, server)
            case PlanImportRequest():
                result = self._handle_plan_import(request, server)
            case PlanResetRequest():
                result = self._handle_plan_reset(request, server)
            case ContextPreserveRequest():
                result = self._handle_context_preserve(request, server)

        return msgspec.json.encode(result)

    def _handle_get_state(self, _request: GetStateRequest, server: HarnessDaemon) -> Result:
        state = server.state_manager.load()
        if state is None:
            return Ok(data=GetStateData(state=None))
        return Ok(data=GetStateData(state=msgspec.to_builtins(state)))

    def _handle_status(self, request: StatusRequest, server: HarnessDaemon) -> Result:
        from .state import TaskStatus

        state = server.state_manager.load()

        if state is None:
            return Ok(
                data=StatusData(
                    active=False,
                    summary=StatusSummary(
                        total=0,
                        completed=0,
                        running=0,
                        pending=0,
                        failed=0,
                    ),
                    tasks={},
                    events=[],
                    active_workers=[],
                )
            )

        tasks = state.tasks

        completed = running = pending = failed = 0
        active_workers: set[str] = set()

        for task in tasks.values():
            match task.status:
                case TaskStatus.COMPLETED:
                    completed += 1
                case TaskStatus.RUNNING:
                    running += 1
                    if task.claimed_by:
                        active_workers.add(task.claimed_by)
                case TaskStatus.PENDING:
                    pending += 1
                case TaskStatus.FAILED:
                    failed += 1

        summary = StatusSummary(
            total=len(tasks),
            completed=completed,
            running=running,
            pending=pending,
            failed=failed,
        )

        events = server.trajectory_logger.tail(n=request.event_count)

        return Ok(
            data=StatusData(
                active=True,
                summary=summary,
                tasks={tid: msgspec.to_builtins(t) for tid, t in tasks.items()},
                events=events,
                active_workers=list(active_workers),
            )
        )

    def _handle_update_state(self, request: UpdateStateRequest, server: HarnessDaemon) -> Result:
        updates = request.updates
        if not updates:
            return Err(message="No updates provided")
        try:
            updated = server.state_manager.update(**updates)
            return Ok(data=UpdateStateData(state=msgspec.to_builtins(updated)))
        except Exception as e:
            return Err(message=str(e))

    def _handle_git(self, request: GitRequest, server: HarnessDaemon) -> Result:
        args = request.args
        cwd = request.cwd if request.cwd else str(server.worktree_root)
        result = safe_git_exec(args, cwd)
        return Ok(
            data=GitData(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )

    def _handle_ping(self, _request: PingRequest, _server: HarnessDaemon) -> Result:
        return Ok(data=PingData(running=True, pid=os.getpid()))

    def _handle_shutdown(self, _request: ShutdownRequest, server: HarnessDaemon) -> Result:
        threading.Thread(target=server.shutdown, daemon=True).start()
        return Ok(data=ShutdownData(shutdown=True))

    def _handle_task_claim(self, request: TaskClaimRequest, server: HarnessDaemon) -> Result:
        worker_id = request.worker_id
        if not worker_id:
            return Err(message="worker_id is required")

        try:
            claim_result = server.state_manager.claim_task(worker_id)

            if not claim_result.task:
                return Ok(data=TaskClaimData(task=None))

            task = claim_result.task

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

            return Ok(
                data=TaskClaimData(
                    task=msgspec.to_builtins(task),
                    is_retry=claim_result.is_retry,
                    is_reclaim=claim_result.is_reclaim,
                )
            )
        except Exception as e:
            return Err(message=str(e))

    def _handle_task_complete(self, request: TaskCompleteRequest, server: HarnessDaemon) -> Result:
        task_id = request.task_id
        worker_id = request.worker_id
        force = request.force

        if not task_id:
            return Err(message="task_id is required")
        if not worker_id:
            return Err(message="worker_id is required")

        try:
            server.state_manager.complete_task(task_id, worker_id, force=force)

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

            return Ok(data=TaskCompleteData(task_id=task_id))
        except Exception as e:
            return Err(message=str(e))

    def _handle_exec(self, request: ExecRequest, server: HarnessDaemon) -> Result:
        args = request.args
        cwd = request.cwd
        env = request.env
        timeout = request.timeout
        exclusive = request.exclusive

        if not args:
            return Err(message="args is required")

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

            return Ok(
                data=ExecData(
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    signal_name=signal_name,
                )
            )
        except subprocess.TimeoutExpired as e:
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
            return Ok(
                data=ExecData(
                    returncode=-15,
                    stdout=e.stdout.decode() if e.stdout else "",
                    stderr=e.stderr.decode() if e.stderr else "",
                    signal_name=signal_name,
                )
            )
        except Exception as e:
            return Err(message=str(e))

    def _handle_plan_import(self, request: PlanImportRequest, server: HarnessDaemon) -> Result:
        content = request.content
        if not content:
            return Err(message="content required")

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

            return Ok(data=PlanImportData(goal=plan.goal, task_count=len(plan.tasks)))
        except ValueError as e:
            msg = str(e)
            if "No valid plan found" in msg:
                msg += ". Run 'hyh plan template' to see the required format."
            return Err(message=msg)

    def _handle_plan_reset(self, _request: PlanResetRequest, server: HarnessDaemon) -> Result:
        server.state_manager.reset()

        server.trajectory_logger.log({"event_type": "plan_reset"})
        if server.acp_emitter:
            server.acp_emitter.emit({"event_type": "plan_reset"})

        return Ok(data=PlanResetData(message="Workflow state cleared"))

    def _handle_context_preserve(
        self, _request: ContextPreserveRequest, server: HarnessDaemon
    ) -> Ok | Err:
        """Write current workflow state to .claude/progress.txt for PreCompact."""
        from .state import TaskStatus

        state = server.state_manager.load()
        if state is None:
            return Ok(data=ContextPreserveData(message="No active workflow"))

        # Build progress summary
        tasks = state.tasks
        total = len(tasks)
        completed = sum(1 for t in tasks.values() if t.status == TaskStatus.COMPLETED)
        running = [t.id for t in tasks.values() if t.status == TaskStatus.RUNNING]
        pending = [t.id for t in tasks.values() if t.status == TaskStatus.PENDING]

        lines = [
            "## Current State",
            f"- Progress: {completed}/{total} tasks completed",
            f"- Running: {', '.join(running) if running else 'None'}",
            f"- Pending: {', '.join(pending[:5])}{'...' if len(pending) > 5 else ''}",
            "",
            "## Completed Tasks",
        ]

        for task in tasks.values():
            if task.status == TaskStatus.COMPLETED:
                lines.append(f"- {task.id}: {task.description}")

        # Write to progress file
        progress_dir = server.worktree_root / ".claude"
        progress_dir.mkdir(parents=True, exist_ok=True)
        progress_file = progress_dir / "progress.txt"
        progress_file.write_text("\n".join(lines))

        return Ok(
            data=ContextPreserveData(path=str(progress_file), completed=completed, total=total)
        )


class HarnessDaemon(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    socket_path: str
    worktree_root: Path
    state_manager: WorkflowStateStore
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
        self.state_manager = WorkflowStateStore(self.worktree_root)
        self.trajectory_logger = TrajectoryLogger(
            self.worktree_root / ".claude" / "trajectory.jsonl"
        )
        self.acp_emitter = acp_emitter

        registry = ProjectRegistry()
        registry.register(self.worktree_root)
        self.runtime = create_runtime()
        self.runtime.check_capabilities()
        self._lock_fd = None

        self._acquire_lock()

        if Path(socket_path).exists():
            Path(socket_path).unlink()

        old_umask = os.umask(0o077)
        try:
            super().__init__(socket_path, HarnessHandler)
        finally:
            os.umask(old_umask)

        Path(socket_path).chmod(0o600)

        self.state_manager.load()

    def _acquire_lock(self) -> None:
        self._lock_path = self.socket_path + ".lock"
        lock_path = Path(self._lock_path)
        self._lock_fd = lock_path.open("w")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            self._lock_fd.close()
            raise RuntimeError("Another daemon is already running") from err

    def server_close(self) -> None:
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
    daemon = HarnessDaemon(socket_path, worktree_root)

    def handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
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
        print("Usage: python -m hyh.daemon <socket_path> <worktree_root>")
        sys.exit(1)
    run_daemon(sys.argv[1], sys.argv[2])
