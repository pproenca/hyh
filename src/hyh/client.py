import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from hyh import demo


def get_worker_id() -> str:
    worker_id_path = os.getenv("HYH_WORKER_ID_FILE")
    if worker_id_path:
        worker_id_file = Path(worker_id_path)
    else:
        runtime_dir = os.getenv("XDG_RUNTIME_DIR", "/tmp")
        username = os.getenv("USER", "default")
        worker_id_file = Path(f"{runtime_dir}/hyh-worker-{username}.id")

    if worker_id_file.exists():
        try:
            worker_id = worker_id_file.read_text().strip()

            if worker_id.startswith("worker-") and len(worker_id) == 19:
                return worker_id
        except OSError:
            pass

    worker_id = f"worker-{uuid.uuid4().hex[:12]}"

    tmp_file = worker_id_file.with_suffix(".tmp")
    try:
        fd = os.open(str(tmp_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(worker_id)
            f.flush()
            os.fsync(f.fileno())

        tmp_file.rename(worker_id_file)
    except OSError:
        pass

    return worker_id


WORKER_ID: Final[str] = get_worker_id()


def get_socket_path(worktree: Path | None = None) -> str:
    env_socket = os.getenv("HYH_SOCKET")
    if env_socket:
        return env_socket

    if worktree is None:
        worktree = Path.cwd()
    worktree = Path(worktree).resolve()

    hyh_dir = Path.home() / ".hyh" / "sockets"
    hyh_dir.mkdir(parents=True, exist_ok=True)

    path_hash = hashlib.sha256(str(worktree).encode()).hexdigest()[:16]
    return str(hyh_dir / f"{path_hash}.sock")


def spawn_daemon(worktree_root: str, socket_path: str) -> None:
    import contextlib
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".stderr") as stderr_file:
        stderr_path = stderr_file.name

    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".status") as status_file:
        status_path = status_file.name

    try:
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()

                daemon_pid = os.fork()
                if daemon_pid == 0:
                    try:
                        null_fd = os.open("/dev/null", os.O_RDWR)
                        os.dup2(null_fd, 0)
                        os.dup2(null_fd, 1)
                        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                        os.dup2(stderr_fd, 2)
                        if null_fd > 2:
                            os.close(null_fd)
                        if stderr_fd > 2:
                            os.close(stderr_fd)

                        os.execv(  # noqa: S606
                            sys.executable,
                            [
                                sys.executable,
                                "-m",
                                "hyh.daemon",
                                socket_path,
                                worktree_root,
                            ],
                        )
                    except Exception as e:
                        with contextlib.suppress(Exception):
                            Path(stderr_path).write_text(str(e))
                        os._exit(1)
                else:
                    Path(status_path).write_text(str(daemon_pid))
                    os._exit(0)
            except Exception:
                os._exit(1)
        else:
            _, status = os.waitpid(pid, 0)
            if status != 0:
                raise RuntimeError("Failed to fork daemon process")

        daemon_pid_str = Path(status_path).read_text().strip()
        if not daemon_pid_str:
            raise RuntimeError("Failed to get daemon PID")
        daemon_pid = int(daemon_pid_str)

        try:
            timeout_seconds = int(os.getenv("HYH_TIMEOUT", "5"))
        except (ValueError, TypeError):
            timeout_seconds = 5
        iterations = timeout_seconds * 10

        for _ in range(iterations):
            try:
                os.kill(daemon_pid, 0)
            except OSError as err:
                stderr_content = ""
                with contextlib.suppress(Exception):
                    stderr_content = Path(stderr_path).read_text().strip()
                raise RuntimeError(f"Daemon crashed on startup: {stderr_content}") from err

            if Path(socket_path).exists():
                time.sleep(0.05)
                return
            time.sleep(0.1)

        try:
            os.kill(daemon_pid, 0)
        except OSError as err:
            stderr_content = ""
            with contextlib.suppress(Exception):
                stderr_content = Path(stderr_path).read_text().strip()
            raise RuntimeError(f"Daemon crashed: {stderr_content}") from err

        raise RuntimeError(
            f"Daemon failed to start (timeout {timeout_seconds}s waiting for socket)"
        )
    finally:
        for path in [stderr_path, status_path]:
            with contextlib.suppress(OSError):
                Path(path).unlink(missing_ok=True)


def send_rpc(
    socket_path: str,
    request: dict[str, Any],
    worktree_root: str | None = None,
    timeout: float = 5.0,
    max_retries: int = 1,
) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        try:
            sock.connect(socket_path)
            sock.sendall(json.dumps(request).encode() + b"\n")

            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\n" in response:
                    break

            result: dict[str, Any] = json.loads(response.decode().strip())
            return result

        except (FileNotFoundError, ConnectionRefusedError, BrokenPipeError, OSError):
            if attempt < max_retries and worktree_root:
                spawn_daemon(worktree_root, socket_path)
                continue
            raise
        finally:
            sock.close()

    raise RuntimeError("send_rpc failed after all retries")


def _get_git_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return str(Path.cwd())


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s" if secs else f"{mins}m"
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _format_relative_time(iso_timestamp: str) -> str:
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    now = datetime.now(UTC)
    delta = (now - dt).total_seconds()

    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h ago"


def _cmd_status_all() -> int:
    from hyh.registry import ProjectRegistry

    registry = ProjectRegistry()
    projects = registry.list_projects()

    if not projects:
        print("No projects registered.")
        return 0

    # Filter out stale entries (paths that no longer exist)
    valid_projects = {
        hash_id: info for hash_id, info in projects.items() if Path(info["path"]).exists()
    }

    if not valid_projects:
        print("No projects registered.")
        return 0

    print("Projects:")
    for hash_id, info in valid_projects.items():
        path = info["path"]
        sock_path = str(Path.home() / ".hyh" / "sockets" / f"{hash_id}.sock")
        status = "[running]" if Path(sock_path).exists() else "[stopped]"
        print(f"  {path}  {status}")

    return 0


def _cmd_status(args: argparse.Namespace, socket_path: str, worktree_root: str) -> None:
    if getattr(args, "all", False):
        _cmd_status_all()
        return

    json_output = args.json
    watch_interval = args.watch

    def render_once() -> bool:
        try:
            response = send_rpc(socket_path, {"command": "status"}, worktree_root)
        except FileNotFoundError:
            if json_output:
                print(json.dumps({"daemon": False, "active": False}))
            else:
                print("=" * 50)
                print(" HARNESS STATUS")
                print("=" * 50)
                print()
                print(" Daemon:   not running")
                print(" Workflow: none")
                print()
                print(" Start daemon with: hyh ping")
                print()
            return False
        except ConnectionRefusedError:
            if json_output:
                print(json.dumps({"daemon": False, "active": False, "error": "connection_refused"}))
            else:
                print("=" * 50)
                print(" HARNESS STATUS")
                print("=" * 50)
                print()
                print(" Daemon:   not responding (stale socket?)")
                print(" Workflow: unknown")
                print()
                print(" Try: hyh ping")
                print()
            return False

        if response["status"] != "ok":
            print(f"Error: {response.get('message', 'Unknown error')}", file=sys.stderr)
            return False

        data = response["data"]

        if json_output:
            data["daemon"] = True
            print(json.dumps(data, indent=2))
            return bool(data.get("active", False))

        if not data.get("active"):
            print("=" * 50)
            print(" HARNESS STATUS")
            print("=" * 50)
            print()
            print(" Daemon:   running")
            print(" Workflow: none")
            print()
            print(" Import a plan with: hyh plan-import <file>")
            print()
            return False

        summary = data["summary"]
        tasks = data["tasks"]
        events = data["events"]
        workers = data.get("active_workers", [])

        print("=" * 65)
        print(" HARNESS STATUS")
        print("=" * 65)
        print()

        total = summary["total"]
        completed = summary["completed"]
        if total > 0:
            filled = int((completed / total) * 16)
            bar = "█" * filled + "░" * (16 - filled)
            pct = int((completed / total) * 100)
            print(f" Progress: {bar} {completed}/{total} tasks ({pct}%)")
        else:
            print(" Progress: No tasks")

        running = summary["running"]
        idle = len(workers) - running if workers else 0
        print(f" Workers:  {len(workers)} active" + (f", {idle} idle" if idle > 0 else ""))
        print()

        print("-" * 65)
        print(" TASKS")
        print("-" * 65)

        def task_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
            tid = item[0]
            try:
                return (0, str(int(tid)).zfill(10))
            except ValueError:
                return (1, tid)

        for tid, task in sorted(tasks.items(), key=task_sort_key):
            status = task["status"]
            desc = task.get("description", "")[:40]
            worker = task.get("claimed_by", "")

            icons = {"completed": "✓", "running": "⟳", "pending": "○", "failed": "✗"}
            icon = icons.get(status, "?")

            time_info = ""
            if status == "completed" and task.get("completed_at"):
                time_info = _format_relative_time(task["completed_at"])
            elif status == "running" and task.get("started_at"):
                started = datetime.fromisoformat(task["started_at"].replace("Z", "+00:00"))
                elapsed = (datetime.now(UTC) - started).total_seconds()
                time_info = _format_duration(elapsed)

            if status == "pending" and task.get("dependencies"):
                incomplete = [
                    d for d in task["dependencies"] if tasks.get(d, {}).get("status") != "completed"
                ]
                if incomplete:
                    time_info = f"blocked by {','.join(incomplete[:3])}"

            worker_col = worker[:10] if worker else ""
            print(f" {icon}  {tid:3}  {desc:40}  {worker_col:10}  {status:9}  {time_info}")

        print()

        if events:
            print("-" * 65)
            print(" RECENT EVENTS")
            print("-" * 65)
            for evt in events[-5:]:
                ts = evt.get("timestamp", "")
                if ts:
                    try:
                        dt = datetime.fromtimestamp(ts, tz=UTC)
                        ts_str = dt.strftime("%H:%M:%S")
                    except (ValueError, TypeError):
                        ts_str = str(ts)[:8]
                else:
                    ts_str = "        "

                evt_type = evt.get("event_type", evt.get("event", ""))[:15]
                task_id = evt.get("task_id", "")
                worker_id = evt.get("worker_id", "")[:10]
                extra = evt.get("success", "")
                if extra is True:
                    extra = "success"
                elif extra is False:
                    extra = "failed"
                else:
                    extra = ""

                print(f" {ts_str}  {evt_type:15}  #{task_id:3}  {worker_id:10}  {extra}")

        print()
        return True

    if watch_interval is not None:
        try:
            while True:
                print("\033[2J\033[H", end="")
                active = render_once()
                if not active:
                    break
                time.sleep(watch_interval)
        except KeyboardInterrupt:
            print("\nStopped watching.")
    else:
        render_once()
    return


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hyh", description="Thread-safe state management for dev-workflow"
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Path to project worktree (default: auto-detect from cwd)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ping", help="Check if daemon is running")

    subparsers.add_parser("get-state", help="Get current workflow state")

    upd = subparsers.add_parser("update-state", help="Update workflow state fields")
    upd.add_argument(
        "--field",
        nargs=2,
        action="append",
        dest="fields",
        metavar=("KEY", "VALUE"),
        help="Field to update (repeatable)",
    )

    git = subparsers.add_parser(
        "git",
        help="Execute git command with mutex (Usage: hyh git -- <args>)",
        description="Run git commands through the daemon's global mutex. "
        "The -- separator is REQUIRED to prevent flag confusion.",
        usage="hyh git -- <git-command> [git-args...]",
    )
    git.add_argument(
        "git_args",
        nargs=argparse.REMAINDER,
        help="Git arguments after -- separator (e.g., hyh git -- commit -m 'msg')",
    )

    task = subparsers.add_parser("task", help="Task management commands")
    task_subparsers = task.add_subparsers(dest="task_command", required=True)
    task_subparsers.add_parser("claim", help="Claim next available task")
    task_complete = task_subparsers.add_parser("complete", help="Mark task as complete")
    task_complete.add_argument("--id", required=True, help="Task ID to complete")
    task_complete.add_argument(
        "--force",
        action="store_true",
        help="Complete task regardless of ownership (for recovery)",
    )

    plan_parser = subparsers.add_parser("plan", help="Plan management")
    plan_sub = plan_parser.add_subparsers(dest="plan_command", required=True)
    plan_import_parser = plan_sub.add_parser("import", help="Import plan from file")
    plan_import_parser.add_argument("--file", required=True, help="Plan file path")
    plan_sub.add_parser("template", help="Print Markdown template for plan format")
    plan_sub.add_parser("reset", help="Clear all workflow state")

    exec_parser = subparsers.add_parser(
        "exec",
        help="Execute command with mutex (Usage: hyh exec -- <command>)",
        description="Run arbitrary commands through the daemon's global mutex.",
        usage="hyh exec [--cwd DIR] [-e VAR=value] [--timeout SEC] -- <command> [args...]",
    )
    exec_parser.add_argument("--cwd", help="Working directory for command")
    exec_parser.add_argument(
        "-e",
        "--env",
        action="append",
        dest="env_vars",
        help="Environment variable (repeatable, format: VAR=value)",
    )
    exec_parser.add_argument(
        "--timeout", type=float, default=5.0, help="Command timeout in seconds"
    )
    exec_parser.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="Command and arguments after -- separator",
    )

    subparsers.add_parser("session-start", help="Handle SessionStart hook")

    subparsers.add_parser("check-state", help="Handle Stop hook")

    subparsers.add_parser("check-commit", help="Handle SubagentStop hook")

    subparsers.add_parser("shutdown", help="Shutdown daemon")

    subparsers.add_parser("worker-id", help="Print stable worker ID")

    subparsers.add_parser("context-preserve", help="Write workflow state to progress file")

    subparsers.add_parser("demo", help="Interactive tour of hyh features")

    subparsers.add_parser("init", help="Initialize hyh in current project")

    status_parser = subparsers.add_parser("status", help="Show workflow status and recent events")
    status_parser.add_argument("--json", action="store_true", help="Output raw JSON")
    status_parser.add_argument(
        "--watch",
        nargs="?",
        const=2,
        type=int,
        metavar="SECONDS",
        help="Auto-refresh (default: 2s)",
    )
    status_parser.add_argument(
        "--all",
        action="store_true",
        help="List all registered projects",
    )

    worktree_parser = subparsers.add_parser("worktree", help="Git worktree management")
    worktree_sub = worktree_parser.add_subparsers(dest="worktree_command", required=True)

    worktree_create = worktree_sub.add_parser("create", help="Create a new worktree")
    worktree_create.add_argument("branch", help="Branch name (e.g., 42-user-auth)")

    worktree_sub.add_parser("list", help="List all worktrees")

    worktree_switch = worktree_sub.add_parser("switch", help="Show path to switch to worktree")
    worktree_switch.add_argument("branch", help="Branch name to switch to")

    workflow_parser = subparsers.add_parser("workflow", help="Workflow state management")
    workflow_sub = workflow_parser.add_subparsers(dest="workflow_command", required=True)

    workflow_status = workflow_sub.add_parser("status", help="Show current workflow phase")
    workflow_status.add_argument("--json", action="store_true", help="Output JSON")
    workflow_status.add_argument("--quiet", action="store_true", help="Minimal output")

    args = parser.parse_args()

    if args.project:
        worktree_root = str(Path(args.project).resolve())
    else:
        worktree_root = os.getenv("HYH_WORKTREE") or _get_git_root()
    socket_path = get_socket_path(Path(worktree_root))

    match args.command:
        case "ping":
            _cmd_ping(socket_path, worktree_root)
        case "get-state":
            _cmd_get_state(socket_path, worktree_root)
        case "update-state":
            _cmd_update_state(socket_path, worktree_root, args.fields or [])
        case "git":
            git_args = args.git_args
            if git_args and git_args[0] == "--":
                git_args = git_args[1:]
            _cmd_git(socket_path, worktree_root, git_args)
        case "task":
            match args.task_command:
                case "claim":
                    _cmd_task_claim(socket_path, worktree_root)
                case "complete":
                    _cmd_task_complete(socket_path, worktree_root, args.id, args.force)
        case "plan":
            match args.plan_command:
                case "import":
                    _cmd_plan_import(socket_path, worktree_root, args.file)
                case "template":
                    _cmd_plan_template()
                case "reset":
                    _cmd_plan_reset(socket_path, worktree_root)
        case "exec":
            command_args = args.command_args
            if command_args and command_args[0] == "--":
                command_args = command_args[1:]
            _cmd_exec(
                socket_path,
                worktree_root,
                command_args,
                args.cwd,
                args.env_vars or [],
                args.timeout,
            )
        case "session-start":
            _cmd_session_start(socket_path, worktree_root)
        case "check-state":
            _cmd_check_state(socket_path, worktree_root)
        case "check-commit":
            _cmd_check_commit(socket_path, worktree_root)
        case "shutdown":
            _cmd_shutdown(socket_path, worktree_root)
        case "worker-id":
            _cmd_worker_id()
        case "context-preserve":
            _cmd_context_preserve(socket_path, worktree_root)
        case "demo":
            demo.run()
        case "init":
            _cmd_init()
        case "status":
            _cmd_status(args, socket_path, worktree_root)
        case "worktree":
            match args.worktree_command:
                case "create":
                    _cmd_worktree_create(args.branch)
                case "list":
                    _cmd_worktree_list()
                case "switch":
                    _cmd_worktree_switch(args.branch)
        case "workflow":
            match args.workflow_command:
                case "status":
                    _cmd_workflow_status(
                        json_output=getattr(args, "json", False),
                        quiet=getattr(args, "quiet", False),
                    )


def _cmd_ping(socket_path: str, worktree_root: str) -> None:
    try:
        response = send_rpc(socket_path, {"command": "ping"}, worktree_root)
        if response.get("status") == "ok":
            print("ok")
        else:
            print("error", file=sys.stderr)
            sys.exit(1)
    except (FileNotFoundError, ConnectionRefusedError):
        print("not running")
        sys.exit(1)


def _cmd_get_state(socket_path: str, worktree_root: str) -> None:
    response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    if response["data"]["state"] is None:
        print("No active workflow")
        sys.exit(1)
    print(json.dumps(response["data"]["state"], indent=2))


def _cmd_update_state(socket_path: str, worktree_root: str, fields: list[list[str]]) -> None:
    updates = {key: value for key, value in fields}

    response = send_rpc(
        socket_path,
        {"command": "update_state", "updates": updates},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(f"Updated: current_task={response['data']['state'].get('current_task')}")


def _cmd_git(socket_path: str, worktree_root: str, git_args: list[str]) -> None:
    cwd = str(Path.cwd())
    response = send_rpc(
        socket_path,
        {"command": "git", "args": git_args, "cwd": cwd},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    data = response["data"]
    print(data["stdout"], end="")
    if data["stderr"]:
        print(data["stderr"], file=sys.stderr, end="")
    sys.exit(data["returncode"])


def _cmd_session_start(socket_path: str, worktree_root: str) -> None:
    try:
        response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
    except (FileNotFoundError, ConnectionRefusedError):
        print("{}")
        return

    if response["status"] != "ok" or response["data"]["state"] is None:
        print("{}")
        return

    state = response["data"]["state"]
    tasks = state.get("tasks", {})
    if not tasks:
        print("{}")
        return

    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks.values() if t.get("status") == "completed")

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (f"Resuming workflow: task {completed_tasks}/{total_tasks}"),
        }
    }
    print(json.dumps(output))


def _cmd_check_state(socket_path: str, worktree_root: str) -> None:
    try:
        response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
    except (FileNotFoundError, ConnectionRefusedError):
        print("allow")
        return

    if response["status"] != "ok" or response["data"]["state"] is None:
        print("allow")
        return

    state = response["data"]["state"]
    tasks = state.get("tasks", {})
    if not tasks:
        print("allow")
        return

    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks.values() if t.get("status") == "completed")

    if completed_tasks < total_tasks:
        print(f"deny: Workflow in progress ({completed_tasks}/{total_tasks})")
        sys.exit(1)
    print("allow")


def _cmd_check_commit(socket_path: str, worktree_root: str) -> None:
    try:
        response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
    except (FileNotFoundError, ConnectionRefusedError):
        print("allow")
        return

    if response["status"] != "ok" or response["data"]["state"] is None:
        print("allow")
        return

    state = response["data"]["state"]

    git_response = send_rpc(
        socket_path,
        {"command": "git", "args": ["rev-parse", "HEAD"], "cwd": str(Path.cwd())},
        worktree_root,
    )
    if git_response["status"] != "ok":
        print("allow")
        return

    current_head = git_response["data"]["stdout"].strip()
    last_commit = state.get("last_commit")

    if last_commit and current_head == last_commit:
        print(f"deny: No new commit since {last_commit[:7]}")
        sys.exit(1)
    print("allow")


def _cmd_shutdown(socket_path: str, _worktree_root: str) -> None:
    try:
        send_rpc(socket_path, {"command": "shutdown"}, None)
        print("Shutdown requested")
    except (FileNotFoundError, ConnectionRefusedError):
        print("Daemon not running")
    except json.JSONDecodeError:
        # Daemon may shut down before sending response - this is expected
        print("Shutdown requested")


def _cmd_task_claim(socket_path: str, worktree_root: str) -> None:
    response = send_rpc(
        socket_path,
        {"command": "task_claim", "worker_id": WORKER_ID},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(response["data"], indent=2))


def _cmd_task_complete(
    socket_path: str, worktree_root: str, task_id: str, force: bool = False
) -> None:
    response = send_rpc(
        socket_path,
        {
            "command": "task_complete",
            "task_id": task_id,
            "worker_id": WORKER_ID,
            "force": force,
        },
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(f"Task {task_id} completed" + (" (forced)" if force else ""))


def _cmd_exec(
    socket_path: str,
    worktree_root: str,
    command_args: list[str],
    cwd: str | None,
    env_vars: list[str],
    timeout: float,
) -> None:
    env: dict[str, str] = {}
    for env_var in env_vars:
        if "=" in env_var:
            key, value = env_var.split("=", 1)
            env[key] = value
        else:
            print(f"Error: Invalid env var format: {env_var}", file=sys.stderr)
            sys.exit(1)

    if cwd is None:
        cwd = str(Path.cwd())

    response = send_rpc(
        socket_path,
        {
            "command": "exec",
            "args": command_args,
            "cwd": cwd,
            "env": env,
            "timeout": timeout,
        },
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    data = response["data"]
    print(data["stdout"], end="")
    if data["stderr"]:
        print(data["stderr"], file=sys.stderr, end="")
    sys.exit(data["returncode"])


def _cmd_worker_id() -> None:
    print(get_worker_id())


def _cmd_context_preserve(socket_path: str, worktree_root: str) -> None:
    response = send_rpc(socket_path, {"command": "context_preserve"}, worktree_root)
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    data = response["data"]
    if data.get("path"):
        print(f"Progress saved to {data['path']}")
        print(f"Completed: {data['completed']}/{data['total']} tasks")
    else:
        print(data.get("message", "Done"))


def _cmd_init() -> None:
    from hyh.init import init_project

    project_root = Path(_get_git_root())
    result = init_project(project_root)

    print("hyh initialized!")
    print()
    print(f"Plugin:    {result.plugin_dir}")
    print(f"Config:    {result.hyh_dir}")
    print(f"Branch:    {result.main_branch}")
    print()
    print("Next steps:")
    print("  1. Commit the .claude/ and .hyh/ directories")
    print("  2. In Claude Code, run: /hyh specify <your feature idea>")


def _cmd_plan_import(socket_path: str, worktree_root: str, file_path: str) -> None:
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_text()
    response = send_rpc(
        socket_path,
        {"command": "plan_import", "content": content},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(f"Plan imported ({response['data']['task_count']} tasks)")


def _cmd_plan_template() -> None:
    from hyh.plan import get_plan_template

    print(get_plan_template())


def _cmd_plan_reset(socket_path: str, worktree_root: str) -> None:
    response = send_rpc(
        socket_path,
        {"command": "plan_reset"},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print("Workflow state cleared")


def _cmd_worktree_create(branch: str) -> None:
    from hyh.worktree import create_worktree

    main_repo = Path(_get_git_root())
    result = create_worktree(main_repo, branch)
    print(f"Created worktree: {result.worktree_path}")
    print(f"Branch: {result.branch_name}")
    print(f"\nTo switch: cd {result.worktree_path}")


def _cmd_worktree_list() -> None:
    from hyh.worktree import list_worktrees

    main_repo = Path(_get_git_root())
    worktrees = list_worktrees(main_repo)

    if not worktrees:
        print("No worktrees found.")
        return

    print("Worktrees:")
    for wt in worktrees:
        print(f"  {wt.branch_name}: {wt.worktree_path}")


def _cmd_worktree_switch(branch: str) -> None:
    from hyh.worktree import get_worktree

    main_repo = Path(_get_git_root())
    wt = get_worktree(main_repo, branch)

    if wt is None:
        print(f"Worktree not found: {branch}", file=sys.stderr)
        sys.exit(1)

    assert wt is not None  # Type narrowing for ty
    print(f"cd {wt.worktree_path}")


def _cmd_workflow_status(json_output: bool = False, quiet: bool = False) -> None:
    import json as json_module

    from hyh.workflow import detect_phase

    worktree = Path(_get_git_root())
    phase = detect_phase(worktree)

    if json_output:
        print(
            json_module.dumps(
                {
                    "phase": phase.phase,
                    "next_action": phase.next_action,
                    "spec_exists": phase.spec_exists,
                    "plan_exists": phase.plan_exists,
                    "tasks_total": phase.tasks_total,
                    "tasks_complete": phase.tasks_complete,
                }
            )
        )
        return

    if quiet:
        if phase.next_action:
            print(f"Next: /hyh {phase.next_action}")
        else:
            print("Complete")
        return

    print("=" * 50)
    print(" WORKFLOW STATUS")
    print("=" * 50)
    print()
    print(f" Phase:      {phase.phase}")
    print(f" Spec:       {'yes' if phase.spec_exists else 'no'}")
    print(f" Plan:       {'yes' if phase.plan_exists else 'no'}")

    if phase.tasks_total > 0:
        pct = int((phase.tasks_complete / phase.tasks_total) * 100)
        print(f" Tasks:      {phase.tasks_complete}/{phase.tasks_total} ({pct}%)")

    print()
    if phase.next_action:
        print(f" Next:       /hyh {phase.next_action}")
    else:
        print(" Status:     All tasks complete!")
    print()


if __name__ == "__main__":
    main()
