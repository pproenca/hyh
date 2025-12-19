# `harness status` Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-19-harness-status-impl.md` to implement task-by-task.

**Goal:** Add a `harness status` CLI command that displays workflow progress, task states, worker assignments, and recent events.

**Architecture:** New RPC handler in daemon aggregates state + trajectory data. Client formats output with progress bar, task table, and event log. Supports `--json` for machine output and `--watch` for auto-refresh.

**Tech Stack:** Python 3.13t, stdlib only in client, Pydantic in daemon, pytest for testing.

---

## Task 1: Add `status` RPC Handler to Daemon

**Files:**
- Modify: `src/harness/daemon.py:69-83` (handler registry)
- Modify: `src/harness/daemon.py` (add `_handle_status` method after `_handle_get_state`)
- Test: `tests/harness/test_daemon.py`

**Step 1: Write the failing test** (3 min)

Add test to `tests/harness/test_daemon.py`:

```python
def test_handle_status_returns_workflow_summary(daemon_with_state: Any, socket_path: Path) -> None:
    """Status command returns workflow summary with task counts."""
    daemon, worktree = daemon_with_state

    response = send_command(socket_path, {"command": "status"})

    assert response["status"] == "ok"
    data = response["data"]
    assert "summary" in data
    assert data["summary"]["total"] == 3
    assert data["summary"]["completed"] >= 0
    assert data["summary"]["running"] >= 0
    assert data["summary"]["pending"] >= 0
    assert "tasks" in data
    assert "events" in data
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_daemon.py::test_handle_status_returns_workflow_summary -v
```

Expected: FAIL with `KeyError: 'status'` (unknown command)

**Step 3: Register handler in daemon** (2 min)

In `src/harness/daemon.py`, add to handlers dict (around line 69-83):

```python
"status": self._handle_status,
```

**Step 4: Implement `_handle_status` method** (5 min)

Add after `_handle_get_state` method in `HarnessHandler` class:

```python
def _handle_status(
    self, request: dict[str, Any], server: HarnessDaemon
) -> dict[str, Any]:
    """Return workflow status summary with task counts and recent events."""
    state = server.state_manager.load()

    if state is None:
        return {
            "status": "ok",
            "data": {
                "active": False,
                "summary": {"total": 0, "completed": 0, "running": 0, "pending": 0, "failed": 0},
                "tasks": {},
                "events": [],
                "active_workers": [],
            },
        }

    # Compute summary counts
    tasks = state.tasks
    summary = {
        "total": len(tasks),
        "completed": sum(1 for t in tasks.values() if t.status == TaskStatus.COMPLETED),
        "running": sum(1 for t in tasks.values() if t.status == TaskStatus.RUNNING),
        "pending": sum(1 for t in tasks.values() if t.status == TaskStatus.PENDING),
        "failed": sum(1 for t in tasks.values() if t.status == TaskStatus.FAILED),
    }

    # Get active workers from running tasks
    active_workers = list({t.claimed_by for t in tasks.values() if t.status == TaskStatus.RUNNING and t.claimed_by})

    # Get recent events from trajectory
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
```

**Step 5: Add import if needed** (30 sec)

Ensure `TaskStatus` is imported at top of `daemon.py`:

```python
from harness.state import StateManager, TaskStatus
```

**Step 6: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_daemon.py::test_handle_status_returns_workflow_summary -v
```

Expected: PASS

**Step 7: Commit** (30 sec)

```bash
git add src/harness/daemon.py tests/harness/test_daemon.py
git commit -m "feat(daemon): add status RPC handler"
```

---

## Task 2: Add `status` Client Command with Formatting

**Files:**
- Modify: `src/harness/client.py:266-356` (argparse registration)
- Modify: `src/harness/client.py:363-410` (command dispatch)
- Modify: `src/harness/client.py` (add `_cmd_status` function)

**Step 1: Add argparse registration** (2 min)

In `src/harness/client.py`, find the subparsers section (around line 266-356) and add:

```python
status_parser = subparsers.add_parser("status", help="Show workflow status and recent events")
status_parser.add_argument("--json", action="store_true", help="Output raw JSON")
status_parser.add_argument("--watch", nargs="?", const=2, type=int, metavar="SECONDS", help="Auto-refresh (default: 2s)")
```

**Step 2: Add command dispatch** (1 min)

In the command dispatch block (around line 363-410), add:

```python
elif args.command == "status":
    _cmd_status(socket_path, worktree_root, json_output=args.json, watch_interval=args.watch)
```

**Step 3: Implement `_cmd_status` function** (10 min)

Add the status command function (before `main()`):

```python
def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s" if secs else f"{mins}m"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m" if mins else f"{hours}h"


def _format_relative_time(iso_timestamp: str) -> str:
    """Format ISO timestamp as relative time (e.g., '2m ago')."""
    from datetime import datetime, timezone

    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = (now - dt).total_seconds()

    if delta < 60:
        return f"{int(delta)}s ago"
    elif delta < 3600:
        return f"{int(delta // 60)}m ago"
    else:
        return f"{int(delta // 3600)}h ago"


def _cmd_status(socket_path: str, worktree_root: str, json_output: bool = False, watch_interval: int | None = None) -> None:
    """Display workflow status with progress, tasks, and recent events."""
    import time

    def render_once() -> bool:
        """Render status once. Returns True if workflow is active."""
        try:
            response = send_rpc(socket_path, {"command": "status"}, worktree_root)
        except (FileNotFoundError, ConnectionRefusedError):
            print("Daemon not running. Start with: harness ping")
            return False

        if response["status"] != "ok":
            print(f"Error: {response.get('message', 'Unknown error')}", file=sys.stderr)
            return False

        data = response["data"]

        if json_output:
            print(json.dumps(data, indent=2))
            return data.get("active", False)

        if not data.get("active"):
            print("No active workflow")
            return False

        summary = data["summary"]
        tasks = data["tasks"]
        events = data["events"]
        workers = data.get("active_workers", [])

        # Header
        print("=" * 65)
        print(" HARNESS STATUS")
        print("=" * 65)
        print()

        # Progress bar (16 chars)
        total = summary["total"]
        completed = summary["completed"]
        if total > 0:
            filled = int((completed / total) * 16)
            bar = "█" * filled + "░" * (16 - filled)
            pct = int((completed / total) * 100)
            print(f" Progress: {bar} {completed}/{total} tasks ({pct}%)")
        else:
            print(" Progress: No tasks")

        # Workers
        running = summary["running"]
        idle = len(workers) - running if workers else 0
        print(f" Workers:  {len(workers)} active" + (f", {idle} idle" if idle > 0 else ""))
        print()

        # Tasks table
        print("-" * 65)
        print(" TASKS")
        print("-" * 65)

        # Sort tasks by ID (numeric if possible)
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

            # Status icon
            icons = {"completed": "✓", "running": "⟳", "pending": "○", "failed": "✗"}
            icon = icons.get(status, "?")

            # Time info
            time_info = ""
            if status == "completed" and task.get("completed_at"):
                time_info = _format_relative_time(task["completed_at"])
            elif status == "running" and task.get("started_at"):
                from datetime import datetime, timezone
                started = datetime.fromisoformat(task["started_at"].replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                time_info = _format_duration(elapsed)

            # Blocking deps for pending tasks
            if status == "pending" and task.get("dependencies"):
                incomplete = [d for d in task["dependencies"] if tasks.get(d, {}).get("status") != "completed"]
                if incomplete:
                    time_info = f"blocked by {','.join(incomplete[:3])}"

            worker_col = worker[:10] if worker else ""
            print(f" {icon}  {tid:3}  {desc:40}  {worker_col:10}  {status:9}  {time_info}")

        print()

        # Recent events
        if events:
            print("-" * 65)
            print(" RECENT EVENTS")
            print("-" * 65)
            for evt in events[-5:]:
                ts = evt.get("timestamp", "")
                if ts:
                    from datetime import datetime, timezone
                    try:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
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

    # Watch mode: loop with refresh
    if watch_interval is not None:
        try:
            while True:
                # Clear screen
                print("\033[2J\033[H", end="")
                active = render_once()
                if not active:
                    break
                time.sleep(watch_interval)
        except KeyboardInterrupt:
            print("\nStopped watching.")
    else:
        render_once()
```

**Step 4: Run full test suite** (30 sec)

```bash
pytest tests/harness/ -v -k "status or client"
```

Expected: All tests pass

**Step 5: Test manually** (1 min)

```bash
# Start daemon
python -m harness ping

# Check status (should show "No active workflow")
python -m harness status

# Check JSON output
python -m harness status --json
```

**Step 6: Commit** (30 sec)

```bash
git add src/harness/client.py
git commit -m "feat(client): add status command with formatting"
```

---

## Task 3: Add Edge Case Tests

**Files:**
- Modify: `tests/harness/test_daemon.py`

**Step 1: Test no active workflow** (3 min)

```python
def test_handle_status_no_workflow(daemon_manager: Any, socket_path: Path, worktree: Path) -> None:
    """Status returns inactive when no workflow exists."""
    with daemon_manager(worktree):
        response = send_command(socket_path, {"command": "status"})

    assert response["status"] == "ok"
    assert response["data"]["active"] is False
    assert response["data"]["summary"]["total"] == 0
```

**Step 2: Test with running tasks** (3 min)

```python
def test_handle_status_with_running_task(daemon_with_state: Any, socket_path: Path) -> None:
    """Status shows active workers for running tasks."""
    daemon, worktree = daemon_with_state

    # Claim a task first
    claim_response = send_command(socket_path, {"command": "task_claim", "worker_id": "test-worker"})
    assert claim_response["status"] == "ok"

    response = send_command(socket_path, {"command": "status"})

    assert response["status"] == "ok"
    assert response["data"]["summary"]["running"] >= 1
    assert "test-worker" in response["data"]["active_workers"]
```

**Step 3: Run tests** (30 sec)

```bash
pytest tests/harness/test_daemon.py -v -k "status"
```

Expected: All status tests pass

**Step 4: Commit** (30 sec)

```bash
git add tests/harness/test_daemon.py
git commit -m "test(daemon): add edge case tests for status handler"
```

---

## Task 4: Code Review

**Files:** All modified files from Tasks 1-3

**Step 1: Review changes**

```bash
git diff main..HEAD --stat
git log --oneline main..HEAD
```

**Step 2: Run full test suite**

```bash
make check
```

**Step 3: Manual verification**

Test all command variants:
```bash
python -m harness status
python -m harness status --json
python -m harness status --watch 1
```

---

## Parallel Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1 | Daemon handler must exist before client can test |
| Group 2 | 2 | Client depends on daemon handler |
| Group 3 | 3 | Edge case tests after core implementation |
| Group 4 | 4 | Code review after all implementation |

All tasks are sequential due to dependencies.
