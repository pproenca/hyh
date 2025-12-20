# Multi-Project Isolation Design

**Date:** 2025-12-20
**Status:** Ready for implementation

## Problem Statement

Harness currently uses a single socket per user (`/tmp/harness-{user}.sock`), limiting it to one project at a time. Users need to:
- Run multiple projects concurrently without conflicts
- Check status of different projects through the CLI
- Have multiple Claude sessions within the same project without concurrency issues

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Project identifier | Git worktree root path | Natural boundary, already used for state |
| Socket location | `~/.harness/sockets/{hash}.sock` | Central location, avoids /tmp pollution |
| Hash length | 16 chars (64 bits) | 4B projects before 50% collision |
| Project detection | Auto from cwd + `--project` override | Ergonomic default with escape hatch |
| Status scope | Current project (default), `--all` for all | Familiar pattern, minimal surprise |
| Multi-session | Single daemon, multiple clients | Existing DAG model handles concurrency |
| Registry | Lazy population on daemon spawn | No explicit init step needed |
| Migration | Clean break | New paths only, old daemons work until restart |

## Architecture

### Directory Structure

```
~/.harness/
├── sockets/           # Per-project sockets and locks
│   ├── a1b2c3d4e5f6g7h8.sock
│   ├── a1b2c3d4e5f6g7h8.lock
│   ├── i9j0k1l2m3n4o5p6.sock
│   └── i9j0k1l2m3n4o5p6.lock
└── registry.json      # Project path → hash mapping
```

### Socket Path Resolution

```python
def get_socket_path(worktree: Path | None = None) -> str:
    """Resolve socket path for worktree (auto-detect if None)."""
    if worktree is None:
        worktree = _get_git_worktree_root()  # from cwd

    harness_dir = Path.home() / ".harness" / "sockets"
    harness_dir.mkdir(parents=True, exist_ok=True)

    # Deterministic 16-char hash of absolute path
    path_hash = hashlib.sha256(str(worktree).encode()).hexdigest()[:16]
    return str(harness_dir / f"{path_hash}.sock")
```

### Registry Schema

**File:** `~/.harness/registry.json`

```json
{
  "projects": {
    "a1b2c3d4e5f6g7h8": {
      "path": "/Users/pedro/Projects/harness",
      "last_active": "2025-12-20T10:30:00Z"
    }
  }
}
```

### Concurrency Model

**Within a project:**
- Single daemon per worktree (enforced by file lock at `{hash}.lock`)
- Multiple clients connect with unique `worker_id`
- DAG-based task claiming prevents conflicts (existing behavior)

**Across projects:**
- Completely isolated - separate daemons, separate sockets, separate state
- No shared state, no coordination needed

## CLI Changes

| Command | Behavior |
|---------|----------|
| `harness status` | Show status for current project (auto-detected from cwd) |
| `harness status --all` | List all registered projects with their status |
| `harness status --project /path` | Query specific project by path |
| `harness shutdown` | Stop daemon for current project |
| `harness shutdown --all` | Stop all running daemons |

### Error Handling

**Not inside a git worktree:**
```
$ cd /tmp && harness status
Error: Not inside a git worktree. Use --project /path/to/repo or run from within a project.
```

**Stale registry entry:**
```
$ harness status --all
Projects:
  /Users/pedro/Projects/harness    [running] 3/5 tasks
  /Users/pedro/Projects/deleted    [stale - path not found]
```

**Daemon crash recovery:**
- Client attempts connect, gets ECONNREFUSED
- Client removes stale socket, spawns new daemon
- Same as current behavior, just project-scoped

## Files to Modify

| File | Changes |
|------|---------|
| `client.py` | New `get_socket_path(worktree)`, `--project` flag, `--all` flag |
| `daemon.py` | Accept worktree from client spawn, register with registry |
| New: `registry.py` | Registry class: load, save, register, list, prune |

**No changes needed:**
- `state.py` - Already worktree-scoped
- `trajectory.py` - Already worktree-scoped
- `runtime.py` - Unchanged
- `git.py` - Unchanged

## State Isolation

State remains in each worktree at `.claude/dev-workflow-state.json`. Already project-local, no changes needed.

## Security Considerations

- Socket permissions: `0o600` (user-only)
- Lock files prevent race conditions on daemon spawn
- Registry uses atomic write pattern (tmp-fsync-rename)
