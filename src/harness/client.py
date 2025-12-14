# src/harness/client.py
"""
Harness CLI Client - "Dumb" client with auto-spawn.

CRITICAL: This module MUST NOT import pydantic or harness.state.
Every import adds ~200ms latency to git hooks.

Only stdlib allowed: sys, json, socket, os, subprocess, time, argparse
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time


# Default socket path
def get_socket_path() -> str:
    return f"/tmp/harness-{os.getenv('USER', 'default')}.sock"


def spawn_daemon(worktree_root: str, socket_path: str) -> None:
    """
    Spawn daemon as detached subprocess.

    Uses start_new_session=True to fully detach from parent.
    The daemon will acquire fcntl lock to prevent duplicates.
    Checks process status during wait to detect immediate crashes.

    Timeout is configurable via HARNESS_TIMEOUT env var (default 5s).
    This accounts for slow CI environments where Pydantic import may take time.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "harness.daemon", socket_path, worktree_root],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )

    # Wait for socket to appear (default 5 seconds for CI reliability)
    # Configurable via HARNESS_TIMEOUT env var
    timeout_seconds = int(os.getenv("HARNESS_TIMEOUT", "5"))
    iterations = timeout_seconds * 10  # 0.1s per iteration

    # Check if process died during startup (zombie detection)
    for _ in range(iterations):
        if proc.poll() is not None:
            # Daemon died immediately - get error output
            _, stderr = proc.communicate()
            raise RuntimeError(
                f"Daemon crashed on startup (exit {proc.returncode}): "
                f"{stderr.decode().strip()}"
            )

        if os.path.exists(socket_path):
            time.sleep(0.05)  # Extra delay for server to start accepting
            return
        time.sleep(0.1)

    # Timeout - check one more time if process died
    if proc.poll() is not None:
        _, stderr = proc.communicate()
        raise RuntimeError(
            f"Daemon crashed (exit {proc.returncode}): {stderr.decode().strip()}"
        )

    raise RuntimeError(
        f"Daemon failed to start (timeout {timeout_seconds}s waiting for socket)"
    )


def send_rpc(
    socket_path: str,
    request: dict,
    worktree_root: str | None = None,
    timeout: float = 5.0,
    max_retries: int = 1,
) -> dict:
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

            return json.loads(response.decode().strip())

        except (FileNotFoundError, ConnectionRefusedError):
            if attempt < max_retries and worktree_root:
                # Auto-spawn daemon
                spawn_daemon(worktree_root, socket_path)
                continue
            raise
        finally:
            sock.close()


def _get_git_root() -> str:
    """Get git worktree root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return os.getcwd()


# NOTE: No _coerce_value function!
# Type coercion is the Daemon's job via Pydantic schemas.
# The client passes raw strings to avoid data corruption.
# (e.g., branch name "true" should not become bool(True))


def main():
    """CLI entry point using argparse."""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Thread-safe state management for dev-workflow"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ping
    subparsers.add_parser("ping", help="Check if daemon is running")

    # get-state
    subparsers.add_parser("get-state", help="Get current workflow state")

    # update-state
    upd = subparsers.add_parser("update-state", help="Update workflow state fields")
    upd.add_argument(
        "--field", nargs=2, action="append", dest="fields",
        metavar=("KEY", "VALUE"), help="Field to update (repeatable)"
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
        "git_args", nargs=argparse.REMAINDER,
        help="Git arguments after -- separator (e.g., harness git -- commit -m 'msg')"
    )

    # session-start
    subparsers.add_parser("session-start", help="Handle SessionStart hook")

    # check-state
    subparsers.add_parser("check-state", help="Handle Stop hook")

    # check-commit
    subparsers.add_parser("check-commit", help="Handle SubagentStop hook")

    # shutdown
    subparsers.add_parser("shutdown", help="Shutdown daemon")

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
    elif args.command == "session-start":
        _cmd_session_start(socket_path, worktree_root)
    elif args.command == "check-state":
        _cmd_check_state(socket_path, worktree_root)
    elif args.command == "check-commit":
        _cmd_check_commit(socket_path, worktree_root)
    elif args.command == "shutdown":
        _cmd_shutdown(socket_path, worktree_root)


def _cmd_ping(socket_path: str, worktree_root: str):
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


def _cmd_get_state(socket_path: str, worktree_root: str):
    response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
    if response["status"] != "ok":
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)
    if response["data"] is None:
        print("No active workflow")
        sys.exit(1)
    print(json.dumps(response["data"], indent=2))


def _cmd_update_state(socket_path: str, worktree_root: str, fields: list):
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


def _cmd_git(socket_path: str, worktree_root: str, git_args: list):
    """Execute git command through daemon mutex."""
    cwd = os.getcwd()
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


def _cmd_session_start(socket_path: str, worktree_root: str):
    try:
        response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
    except (FileNotFoundError, ConnectionRefusedError):
        print("{}")
        return

    if response["status"] != "ok" or response["data"] is None:
        print("{}")
        return

    state = response["data"]
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                f"Resuming {state['workflow']}: "
                f"task {state['current_task']}/{state['total_tasks']}"
            ),
        }
    }
    print(json.dumps(output))


def _cmd_check_state(socket_path: str, worktree_root: str):
    try:
        response = send_rpc(socket_path, {"command": "get_state"}, worktree_root)
    except (FileNotFoundError, ConnectionRefusedError):
        print("allow")
        return

    if response["status"] != "ok" or response["data"] is None:
        print("allow")
        return

    state = response["data"]
    if state.get("enabled") and state["current_task"] < state["total_tasks"]:
        print(f"deny: Workflow in progress ({state['current_task']}/{state['total_tasks']})")
        sys.exit(1)
    print("allow")


def _cmd_check_commit(socket_path: str, worktree_root: str):
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
        {"command": "git", "args": ["rev-parse", "HEAD"], "cwd": os.getcwd()},
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


def _cmd_shutdown(socket_path: str, worktree_root: str):
    try:
        response = send_rpc(socket_path, {"command": "shutdown"}, None)
        print("Shutdown requested")
    except (FileNotFoundError, ConnectionRefusedError):
        print("Daemon not running")


if __name__ == "__main__":
    main()
