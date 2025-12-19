# Design: `harness status` Command

## Overview

A CLI command to give developers visibility into workflow progress, agent workloads, and recent events.

## Use Cases

1. **Real-time monitoring** - Watch agents work live
2. **Progress check** - Quick snapshot of completion status
3. **Debugging** - Understand stalls, view recent events/errors

## Command Syntax

```bash
harness status              # Human-readable output
harness status --json       # Machine-readable JSON
harness status --watch      # Auto-refresh every 2s
harness status --watch 5    # Custom interval (seconds)
```

## Output Format

```
═══════════════════════════════════════════════════════════════
 HARNESS STATUS
═══════════════════════════════════════════════════════════════

 Progress: ████████░░░░░░░░ 3/8 tasks (37%)
 Workers:  2 active, 1 idle
 Elapsed:  4m 23s

───────────────────────────────────────────────────────────────
 TASKS
───────────────────────────────────────────────────────────────
 ✓  1  Setup project structure                    completed  2m ago
 ✓  2  Create database models                     completed  1m ago
 ⟳  3  Implement API endpoints         worker-a   running    45s
 ⟳  4  Add authentication              worker-b   running    30s
 ○  5  Write unit tests                           pending    blocked by 3,4
 ○  6  Integration tests                          pending    blocked by 5
 ○  7  Documentation                              pending
 ○  8  Code review                                pending    blocked by 3-7

───────────────────────────────────────────────────────────────
 RECENT EVENTS (last 5)
───────────────────────────────────────────────────────────────
 12:34:45  task_claimed   #3  worker-a
 12:34:42  task_claimed   #4  worker-b
 12:34:30  task_completed #2  worker-a   success
 12:32:15  task_completed #1  worker-b   success
 12:30:00  workflow_started
```

## Implementation

### Files to Modify

| File | Change |
|------|--------|
| `src/harness/daemon.py` | Add `handle_status()` RPC handler |
| `src/harness/client.py` | Add `_cmd_status()` with formatter |

### Data Flow

```
harness status
    → client._cmd_status()
    → send_rpc({"command": "status"})
    → daemon.handle_status()
        → state = StateManager.get_state()
        → events = TrajectoryLogger.tail(5)
        → workers = derive from tasks.claimed_by
    → return JSON response
    → client formats to stdout
```

### RPC Response Schema

```json
{
  "status": "ok",
  "data": {
    "workflow": {
      "tasks": {...},
      "started_at": "2025-12-19T12:30:00Z"
    },
    "events": [
      {"event": "task_claimed", "task_id": "3", "worker_id": "worker-a", "ts": 1703...}
    ],
    "summary": {
      "total": 8,
      "completed": 3,
      "running": 2,
      "pending": 3,
      "active_workers": ["worker-a", "worker-b"]
    }
  }
}
```

### Client Formatting Logic

1. **Progress bar**: `completed / total` ratio mapped to 16 chars
2. **Task status icons**: `✓` completed, `⟳` running, `○` pending, `✗` failed
3. **Relative time**: Convert timestamps to "2m ago", "45s" format
4. **Blocking deps**: For pending tasks, show which incomplete deps block them
5. **Workers**: Extract unique `claimed_by` from running tasks

### Flag Behavior

**`--json`**
- Output raw daemon response
- No formatting, suitable for `jq` piping

**`--watch [interval]`**
- Default interval: 2 seconds
- Clear screen + reprint on each refresh
- Handle `KeyboardInterrupt` gracefully
- Show "Last updated: HH:MM:SS" in header

### Edge Cases

| Scenario | Behavior |
|----------|----------|
| No active workflow | "No active workflow" message |
| Daemon not running | "Daemon not running. Start with: harness ping" |
| All tasks completed | Show 100% progress, no running workers |
| Task timeout | Mark as `⚠ timeout` in status |

## Constraints

- **Client stdlib only**: No rich/textual dependencies (per project rules)
- **Unicode safe**: Use ASCII fallback if terminal doesn't support Unicode
- **O(1) events**: Use trajectory.tail(), not full file read
