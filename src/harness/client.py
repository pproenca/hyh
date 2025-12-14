# src/harness/client.py
"""
Harness CLI Client - "Dumb" client with auto-spawn.

CRITICAL: This module MUST NOT import pydantic or harness.state.
Every import adds ~200ms latency to git hooks.

Only stdlib allowed: sys, json, socket, os, subprocess, time, argparse, pathlib
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


def get_worker_id() -> str:
    """Get stable worker ID, persisting across CLI invocations using Atomic Write."""
    # Allow override via env var (for testing or custom locations)
    worker_id_path = os.getenv("HARNESS_WORKER_ID_FILE")
    if worker_id_path:
        worker_id_file = Path(worker_id_path)
    else:
        # Use XDG_RUNTIME_DIR if available (more secure), fallback to /tmp
        runtime_dir = os.getenv("XDG_RUNTIME_DIR", "/tmp")
        username = os.getenv("USER", "default")
        worker_id_file = Path(f"{runtime_dir}/harness-worker-{username}.id")

    # Try to read existing worker ID
    if worker_id_file.exists():
        try:
            worker_id = worker_id_file.read_text().strip()
            # Basic validation to prevent using corrupt data
            # Format: "worker-" (7) + 12 hex chars = 19 total
            if worker_id.startswith("worker-") and len(worker_id) == 19:
                return worker_id
        except OSError:
            pass  # Fall through to regenerate

    # Generate new ID
    worker_id = f"worker-{uuid.uuid4().hex[:12]}"

    # ATOMIC WRITE PATTERN (Council Requirement)
    tmp_file = worker_id_file.with_suffix(".tmp")
    try:
        # Create with 600 permissions (User Read/Write ONLY)
        # Using open with low-level flags to ensure permissions from creation
        fd = os.open(str(tmp_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(worker_id)
            f.flush()
            os.fsync(f.fileno())  # Durability

        # Atomic rename
        tmp_file.rename(worker_id_file)
    except OSError:
        # If persistence fails (e.g. read-only FS), return ephemeral ID
        # This keeps the client usable even if stateful restart is broken
        pass

    return worker_id


# Generate stable WORKER_ID on module load (Council Fix: Lost Ack)
WORKER_ID = get_worker_id()


# Default socket path - /tmp is the standard Unix socket location
def get_socket_path() -> str:
    runtime_dir = os.getenv("XDG_RUNTIME_DIR", "/tmp")
    return f"{runtime_dir}/harness-{os.getenv('USER', 'default')}.sock"


def spawn_daemon(worktree_root: str, socket_path: str) -> None:
    """
    Spawn daemon as a fully detached process using double-fork.

    This ensures no Popen object keeps a reference to the daemon process,
    avoiding ResourceWarning when the daemon continues running after spawn.

    The daemon will acquire fcntl lock to prevent duplicates.
    Timeout is configurable via HARNESS_TIMEOUT env var (default 5s).
    """
    import contextlib
    import tempfile

    # Create temporary files for stderr and status communication
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".stderr") as stderr_file:
        stderr_path = stderr_file.name

    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".status") as status_file:
        status_path = status_file.name

    try:
        # First fork - create intermediate child
        pid = os.fork()
        if pid == 0:
            # Intermediate child process
            try:
                # Create new session to detach from terminal
                os.setsid()

                # Second fork - create daemon (grandchild)
                daemon_pid = os.fork()
                if daemon_pid == 0:
                    # Daemon process (grandchild)
                    try:
                        # Redirect stdio
                        null_fd = os.open("/dev/null", os.O_RDWR)
                        os.dup2(null_fd, 0)  # stdin
                        os.dup2(null_fd, 1)  # stdout
                        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                        os.dup2(stderr_fd, 2)  # stderr
                        if null_fd > 2:
                            os.close(null_fd)
                        if stderr_fd > 2:
                            os.close(stderr_fd)

                        # Execute daemon (S606 is expected - we need execv for daemonization)
                        os.execv(  # noqa: S606
                            sys.executable,
                            [
                                sys.executable,
                                "-m",
                                "harness.daemon",
                                socket_path,
                                worktree_root,
                            ],
                        )
                    except Exception as e:
                        # Write error to stderr file (best effort, ignore failures)
                        with contextlib.suppress(Exception):
                            Path(stderr_path).write_text(str(e))
                        os._exit(1)
                else:
                    # Intermediate child - write daemon PID to status file and exit
                    Path(status_path).write_text(str(daemon_pid))
                    os._exit(0)
            except Exception:
                os._exit(1)
        else:
            # Parent process - wait for intermediate child
            _, status = os.waitpid(pid, 0)
            if status != 0:
                raise RuntimeError("Failed to fork daemon process")

        # Read daemon PID from status file
        daemon_pid_str = Path(status_path).read_text().strip()
        if not daemon_pid_str:
            raise RuntimeError("Failed to get daemon PID")
        daemon_pid = int(daemon_pid_str)

        # Wait for socket to appear (default 5 seconds for CI reliability)
        try:
            timeout_seconds = int(os.getenv("HARNESS_TIMEOUT", "5"))
        except (ValueError, TypeError):
            timeout_seconds = 5
        iterations = timeout_seconds * 10  # 0.1s per iteration

        # Check if daemon started successfully
        for _ in range(iterations):
            # Check if daemon crashed (process no longer exists)
            try:
                os.kill(daemon_pid, 0)  # Signal 0 checks if process exists
            except OSError as err:
                # Process died - read error from stderr file
                stderr_content = ""
                with contextlib.suppress(Exception):
                    stderr_content = Path(stderr_path).read_text().strip()
                raise RuntimeError(f"Daemon crashed on startup: {stderr_content}") from err

            if Path(socket_path).exists():
                time.sleep(0.05)  # Extra delay for server to start accepting
                return
            time.sleep(0.1)

        # Timeout - check if daemon is still running
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
        # Clean up temporary files
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
    """
    Send RPC request to daemon.

    If connection fails, auto-spawns daemon and retries.
    """
    for attempt in range(max_retries + 1):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        try:
            sock.connect(socket_path)
            sock.sendall(json.dumps(request).encode() + b"\n")

            # Read response
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

        except (FileNotFoundError, ConnectionRefusedError):
            if attempt < max_retries and worktree_root:
                # Auto-spawn daemon
                spawn_daemon(worktree_root, socket_path)
                continue
            raise
        finally:
            sock.close()

    # This should never be reached due to the raise in the except block
    raise RuntimeError("send_rpc failed after all retries")


def _get_git_root() -> str:
    """Get git worktree root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return str(Path.cwd())


# NOTE: No _coerce_value function!
# Type coercion is the Daemon's job via Pydantic schemas.
# The client passes raw strings to avoid data corruption.
# (e.g., branch name "true" should not become bool(True))


def main() -> None:
    """CLI entry point using argparse."""
    parser = argparse.ArgumentParser(
        prog="harness", description="Thread-safe state management for dev-workflow"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ping
    subparsers.add_parser("ping", help="Check if daemon is running")

    # get-state
    subparsers.add_parser("get-state", help="Get current workflow state")

    # update-state
    upd = subparsers.add_parser("update-state", help="Update workflow state fields")
    upd.add_argument(
        "--field",
        nargs=2,
        action="append",
        dest="fields",
        metavar=("KEY", "VALUE"),
        help="Field to update (repeatable)",
    )

    # git -- <args>
    git = subparsers.add_parser(
        "git",
        help="Execute git command with mutex (Usage: harness git -- <args>)",
        description="Run git commands through the daemon's global mutex. "
        "The -- separator is REQUIRED to prevent flag confusion.",
        usage="harness git -- <git-command> [git-args...]",
    )
    git.add_argument(
        "git_args",
        nargs=argparse.REMAINDER,
        help="Git arguments after -- separator (e.g., harness git -- commit -m 'msg')",
    )

    # task subcommand with claim and complete
    task = subparsers.add_parser("task", help="Task management commands")
    task_subparsers = task.add_subparsers(dest="task_command", required=True)
    task_subparsers.add_parser("claim", help="Claim next available task")
    task_complete = task_subparsers.add_parser("complete", help="Mark task as complete")
    task_complete.add_argument("--id", required=True, help="Task ID to complete")

    # plan subcommand
    plan_parser = subparsers.add_parser("plan", help="Plan management")
    plan_sub = plan_parser.add_subparsers(dest="plan_command", required=True)
    plan_import_parser = plan_sub.add_parser("import", help="Import plan from file")
    plan_import_parser.add_argument("--file", required=True, help="Plan file path")

    # exec command
    exec_parser = subparsers.add_parser(
        "exec",
        help="Execute command with mutex (Usage: harness exec -- <command>)",
        description="Run arbitrary commands through the daemon's global mutex.",
        usage="harness exec [--cwd DIR] [-e VAR=value] [--timeout SEC] -- <command> [args...]",
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

    # session-start
    subparsers.add_parser("session-start", help="Handle SessionStart hook")

    # check-state
    subparsers.add_parser("check-state", help="Handle Stop hook")

    # check-commit
    subparsers.add_parser("check-commit", help="Handle SubagentStop hook")

    # shutdown
    subparsers.add_parser("shutdown", help="Shutdown daemon")

    # worker-id
    subparsers.add_parser("worker-id", help="Print stable worker ID")

    args = parser.parse_args()

    socket_path = os.getenv("HARNESS_SOCKET", get_socket_path())
    worktree_root = os.getenv("HARNESS_WORKTREE") or _get_git_root()

    # Route commands
    if args.command == "ping":
        _cmd_ping(socket_path, worktree_root)
    elif args.command == "get-state":
        _cmd_get_state(socket_path, worktree_root)
    elif args.command == "update-state":
        _cmd_update_state(socket_path, worktree_root, args.fields or [])
    elif args.command == "git":
        # Strip leading -- separator if present
        git_args = args.git_args
        if git_args and git_args[0] == "--":
            git_args = git_args[1:]
        _cmd_git(socket_path, worktree_root, git_args)
    elif args.command == "task":
        if args.task_command == "claim":
            _cmd_task_claim(socket_path, worktree_root)
        elif args.task_command == "complete":
            _cmd_task_complete(socket_path, worktree_root, args.id)
    elif args.command == "plan":
        if args.plan_command == "import":
            _cmd_plan_import(socket_path, worktree_root, args.file)
    elif args.command == "exec":
        # Strip leading -- separator if present
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
    elif args.command == "session-start":
        _cmd_session_start(socket_path, worktree_root)
    elif args.command == "check-state":
        _cmd_check_state(socket_path, worktree_root)
    elif args.command == "check-commit":
        _cmd_check_commit(socket_path, worktree_root)
    elif args.command == "shutdown":
        _cmd_shutdown(socket_path, worktree_root)
    elif args.command == "worker-id":
        _cmd_worker_id()


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
    if response["data"] is None:
        print("No active workflow")
        sys.exit(1)
    print(json.dumps(response["data"], indent=2))


def _cmd_update_state(socket_path: str, worktree_root: str, fields: list[list[str]]) -> None:
    """Update state fields. fields is list of [key, value] pairs from argparse.

    NOTE: All values are passed as raw strings to the daemon.
    Pydantic handles type coercion based on the schema.
    """
    updates = {key: value for key, value in fields}  # Raw strings, no coercion

    response = send_rpc(
        socket_path,
        {"command": "update_state", "updates": updates},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(f"Updated: current_task={response['data'].get('current_task')}")


def _cmd_git(socket_path: str, worktree_root: str, git_args: list[str]) -> None:
    """Execute git command through daemon mutex."""
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

    if response["status"] != "ok" or response["data"] is None:
        print("{}")
        return

    state = response["data"]
    tasks = state.get("tasks", {})
    if not tasks:
        print("{}")
        return

    # Calculate progress from task DAG
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

    if response["status"] != "ok" or response["data"] is None:
        print("allow")
        return

    state = response["data"]
    tasks = state.get("tasks", {})
    if not tasks:
        print("allow")
        return

    # Calculate progress from task DAG
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

    if response["status"] != "ok" or response["data"] is None:
        print("allow")
        return

    state = response["data"]

    # Get current HEAD via daemon (mutex-protected)
    git_response = send_rpc(
        socket_path,
        {"command": "git", "args": ["rev-parse", "HEAD"], "cwd": str(Path.cwd())},
        worktree_root,
    )
    if git_response["status"] != "ok":
        print("allow")  # Fail open
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


def _cmd_task_claim(socket_path: str, worktree_root: str) -> None:
    """Claim next available task."""
    response = send_rpc(
        socket_path,
        {"command": "task_claim", "worker_id": WORKER_ID},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(response["data"], indent=2))


def _cmd_task_complete(socket_path: str, worktree_root: str, task_id: str) -> None:
    """Mark task as complete."""
    response = send_rpc(
        socket_path,
        {"command": "task_complete", "task_id": task_id, "worker_id": WORKER_ID},
        worktree_root,
    )
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    print(f"Task {task_id} completed")


def _cmd_exec(
    socket_path: str,
    worktree_root: str,
    command_args: list[str],
    cwd: str | None,
    env_vars: list[str],
    timeout: float,
) -> None:
    """Execute command through daemon mutex."""
    # Parse environment variables
    env: dict[str, str] = {}
    for env_var in env_vars:
        if "=" in env_var:
            key, value = env_var.split("=", 1)
            env[key] = value
        else:
            print(f"Error: Invalid env var format: {env_var}", file=sys.stderr)
            sys.exit(1)

    # Use current directory if not specified
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
    """Print stable worker ID."""
    print(get_worker_id())


def _cmd_plan_import(socket_path: str, worktree_root: str, file_path: str) -> None:
    """Import plan from file."""
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


if __name__ == "__main__":
    main()
