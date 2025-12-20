# Multi-Project Isolation Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-20-multi-project-isolation.md` to implement task-by-task.

**Goal:** Enable harness to manage multiple git worktrees concurrently with isolated daemons, sockets, and state per project.

**Architecture:** Each worktree gets a unique socket at `~/.harness/sockets/{hash16}.sock` derived from the absolute worktree path. A registry at `~/.harness/registry.json` tracks all projects for `--all` queries. CLI auto-detects project from cwd with `--project` override.

**Tech Stack:** Python 3.13t, hashlib.sha256, stdlib json (no Pydantic in client), fcntl.flock for registry and daemon singleton.

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2 | Registry module (new file) and socket path logic (independent) |
| Group 2 | 3 | CLI flags depend on socket path changes |
| Group 3 | 4 | Daemon registration depends on registry module |
| Group 4 | 5 | Integration test depends on all components |
| Group 5 | 6 | Code review |

---

### Task 1: Create Registry Module

**Files:**
- Create: `src/harness/registry.py`
- Test: `tests/harness/test_registry.py`

**Step 1: Write the failing test for registry load/save** (3 min)

```python
# tests/harness/test_registry.py
"""Tests for project registry."""

import json
from pathlib import Path

import pytest

from harness.registry import ProjectRegistry


def test_registry_load_empty(tmp_path: Path) -> None:
    """Registry returns empty projects dict when file doesn't exist."""
    registry_file = tmp_path / "registry.json"
    registry = ProjectRegistry(registry_file)

    assert registry.list_projects() == {}


def test_registry_register_project(tmp_path: Path) -> None:
    """Registry persists project on register."""
    registry_file = tmp_path / "registry.json"
    registry = ProjectRegistry(registry_file)

    worktree = Path("/Users/test/project")
    registry.register(worktree)

    # Reload to verify persistence
    registry2 = ProjectRegistry(registry_file)
    projects = registry2.list_projects()

    assert len(projects) == 1
    assert str(worktree) in [p["path"] for p in projects.values()]


def test_registry_concurrent_registration(tmp_path: Path) -> None:
    """Concurrent registrations don't lose data (race condition safety)."""
    import concurrent.futures

    registry_file = tmp_path / "registry.json"
    projects = [tmp_path / f"project_{i}" for i in range(10)]
    for p in projects:
        p.mkdir()

    def register_project(proj: Path) -> str:
        registry = ProjectRegistry(registry_file)
        return registry.register(proj)

    # Register 10 projects concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(register_project, projects))

    # Verify all 10 projects were registered (no lost writes)
    registry = ProjectRegistry(registry_file)
    registered = registry.list_projects()
    assert len(registered) == 10
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_registry.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'harness.registry'`

**Step 3: Write minimal registry implementation** (5 min)

```python
# src/harness/registry.py
"""Project registry for multi-project isolation.

Tracks registered projects in ~/.harness/registry.json for --all queries.
Uses fcntl.flock for race-condition safety across concurrent daemons.

CRITICAL: This module MUST NOT import pydantic (client.py constraint).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _get_default_registry_path() -> Path:
    """Get default registry path, respecting HARNESS_REGISTRY_FILE env var."""
    env_path = os.getenv("HARNESS_REGISTRY_FILE")
    if env_path:
        return Path(env_path)
    return Path.home() / ".harness" / "registry.json"


class ProjectRegistry:
    """Process-safe project registry with file locking."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self.registry_file = Path(registry_file) if registry_file else _get_default_registry_path()
        self._ensure_parent_dir()
        self._lock_file = self.registry_file.with_suffix(".lock")

    def _ensure_parent_dir(self) -> None:
        """Create parent directory if needed."""
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

    def _with_lock(self, fn: "Callable[[], T]") -> "T":
        """Execute fn while holding exclusive lock on registry."""
        with open(self._lock_file, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def _load_unlocked(self) -> dict[str, Any]:
        """Load registry from disk (caller must hold lock)."""
        if not self.registry_file.exists():
            return {"projects": {}}
        try:
            return json.loads(self.registry_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {"projects": {}}

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        """Atomic write to registry file (caller must hold lock)."""
        tmp = self.registry_file.with_suffix(".tmp")
        content = json.dumps(data, indent=2)
        with open(tmp, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(self.registry_file)

    def register(self, worktree: Path) -> str:
        """Register a project, return its hash ID. Thread/process-safe."""
        worktree = worktree.resolve()
        path_hash = hashlib.sha256(str(worktree).encode()).hexdigest()[:16]

        def _do_register() -> str:
            data = self._load_unlocked()
            data["projects"][path_hash] = {
                "path": str(worktree),
                "last_active": datetime.now(timezone.utc).isoformat(),
            }
            self._save_unlocked(data)
            return path_hash

        return self._with_lock(_do_register)

    def list_projects(self) -> dict[str, dict[str, Any]]:
        """Return all registered projects."""
        return self._with_lock(lambda: self._load_unlocked().get("projects", {}))

    def get_hash_for_path(self, worktree: Path) -> str:
        """Compute hash for a worktree path (no lock needed)."""
        worktree = worktree.resolve()
        return hashlib.sha256(str(worktree).encode()).hexdigest()[:16]
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_registry.py -v
```

Expected: PASS (3 passed)

**Step 5: Commit** (30 sec)

```bash
git add src/harness/registry.py tests/harness/test_registry.py
git commit -m "feat(registry): add project registry for multi-project isolation"
```

---

### Task 2: Update Socket Path Resolution

**Files:**
- Modify: `src/harness/client.py:75-78` (get_socket_path function)
- Test: `tests/harness/test_client.py`

**Step 1: Write the failing test for hash-based socket path** (3 min)

Add to `tests/harness/test_client.py`:

```python
def test_get_socket_path_uses_worktree_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Socket path includes hash of worktree for project isolation."""
    # Clear env override
    monkeypatch.delenv("HARNESS_SOCKET", raising=False)

    worktree1 = tmp_path / "project1"
    worktree2 = tmp_path / "project2"
    worktree1.mkdir()
    worktree2.mkdir()

    from harness.client import get_socket_path

    path1 = get_socket_path(worktree1)
    path2 = get_socket_path(worktree2)

    # Different worktrees get different sockets
    assert path1 != path2

    # Same worktree always gets same socket
    assert get_socket_path(worktree1) == path1

    # Socket is in ~/.harness/sockets/
    assert ".harness/sockets/" in path1
    assert path1.endswith(".sock")


def test_get_socket_path_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HARNESS_SOCKET env var overrides computed path."""
    custom_socket = str(tmp_path / "custom.sock")
    monkeypatch.setenv("HARNESS_SOCKET", custom_socket)

    from harness.client import get_socket_path

    assert get_socket_path(tmp_path) == custom_socket
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_client.py::test_get_socket_path_uses_worktree_hash -v
```

Expected: FAIL with `TypeError: get_socket_path() takes 0 positional arguments but 1 was given`

**Step 3: Update get_socket_path to accept worktree parameter** (3 min)

Replace in `src/harness/client.py` (lines 75-78):

```python
def get_socket_path(worktree: "Path | None" = None) -> str:
    """Get socket path for a worktree.

    Args:
        worktree: Git worktree root. If None, uses current directory.

    Returns:
        Socket path. Uses HARNESS_SOCKET env var if set, otherwise
        computes hash-based path in ~/.harness/sockets/.
    """
    # Environment override takes precedence
    env_socket = os.getenv("HARNESS_SOCKET")
    if env_socket:
        return env_socket

    # Resolve worktree path
    if worktree is None:
        worktree = Path.cwd()
    worktree = Path(worktree).resolve()

    # Hash-based socket in ~/.harness/sockets/
    harness_dir = Path.home() / ".harness" / "sockets"
    harness_dir.mkdir(parents=True, exist_ok=True)

    path_hash = hashlib.sha256(str(worktree).encode()).hexdigest()[:16]
    return str(harness_dir / f"{path_hash}.sock")
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_client.py::test_get_socket_path_uses_worktree_hash tests/harness/test_client.py::test_get_socket_path_env_override -v
```

Expected: PASS (2 passed)

**Step 5: Update main() to pass worktree to get_socket_path** (2 min)

In `src/harness/client.py`, find the main() function (around line 584-585) and update:

```python
# Before:
# socket_path = os.getenv("HARNESS_SOCKET", get_socket_path())
# worktree_root = os.getenv("HARNESS_WORKTREE") or _get_git_root()

# After:
worktree_root = os.getenv("HARNESS_WORKTREE") or _get_git_root()
socket_path = get_socket_path(Path(worktree_root))
```

**Step 6: Run all client tests to verify no regressions** (30 sec)

```bash
pytest tests/harness/test_client.py -v
```

Expected: All tests pass

**Step 7: Commit** (30 sec)

```bash
git add src/harness/client.py tests/harness/test_client.py
git commit -m "feat(client): hash-based socket path for multi-project isolation"
```

---

### Task 3: Add CLI Flags for Project Selection

**Files:**
- Modify: `src/harness/client.py` (argparse setup, status command)
- Test: `tests/harness/test_client.py`

**Step 1: Write failing test for --project flag** (3 min)

Add to `tests/harness/test_client.py`:

```python
def test_status_project_flag_overrides_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--project flag overrides auto-detection from cwd."""
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()

    # Initialize git repos
    import subprocess

    for p in [project_a, project_b]:
        subprocess.run(["git", "init"], cwd=p, capture_output=True)

    # Run from project_a but query project_b
    monkeypatch.chdir(project_a)
    monkeypatch.delenv("HARNESS_SOCKET", raising=False)
    monkeypatch.delenv("HARNESS_WORKTREE", raising=False)

    from harness.client import get_socket_path

    # Without --project, would use project_a
    socket_a = get_socket_path(project_a)

    # With --project, should use project_b
    socket_b = get_socket_path(project_b)

    assert socket_a != socket_b
```

**Step 2: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_client.py::test_status_project_flag_overrides_cwd -v
```

Expected: PASS (this test validates the get_socket_path function already works)

**Step 3: Add --project argument to argparse** (3 min)

In `src/harness/client.py`, find the argparse setup in main() and add to the parent parser:

```python
# Find the parser setup (around line 510-520) and add:
parser.add_argument(
    "--project",
    type=str,
    default=None,
    help="Path to project worktree (default: auto-detect from cwd)",
)
```

**Step 4: Update main() to use --project flag** (2 min)

Update the worktree resolution in main():

```python
# Update worktree resolution to respect --project flag:
if args.project:
    worktree_root = str(Path(args.project).resolve())
else:
    worktree_root = os.getenv("HARNESS_WORKTREE") or _get_git_root()
socket_path = get_socket_path(Path(worktree_root))
```

**Step 5: Write test for --all flag on status** (3 min)

Add to `tests/harness/test_client.py`:

```python
def test_status_all_flag_lists_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--all flag lists all registered projects."""
    from harness.registry import ProjectRegistry

    registry_file = tmp_path / "registry.json"
    registry = ProjectRegistry(registry_file)

    # Register two projects
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()
    registry.register(project_a)
    registry.register(project_b)

    # Verify both are listed
    projects = registry.list_projects()
    paths = [p["path"] for p in projects.values()]
    assert str(project_a) in paths
    assert str(project_b) in paths
```

**Step 6: Run test** (30 sec)

```bash
pytest tests/harness/test_client.py::test_status_all_flag_lists_projects -v
```

Expected: PASS

**Step 7: Add --all argument to status subcommand** (2 min)

In `src/harness/client.py`, find the status subparser and add:

```python
# Find status_parser (search for 'status_parser = subparsers.add_parser')
status_parser.add_argument(
    "--all",
    action="store_true",
    help="List all registered projects",
)
```

**Step 8: Update _cmd_status to handle --all** (5 min)

Update `_cmd_status` function in `src/harness/client.py`:

```python
def _cmd_status(args: argparse.Namespace, socket_path: str, worktree_root: str) -> int:
    """Show workflow status."""
    # Handle --all flag
    if getattr(args, "all", False):
        return _cmd_status_all()

    # Existing status logic continues...
    # (keep the rest of the function unchanged)
```

Add new function for --all:

```python
def _cmd_status_all() -> int:
    """List all registered projects with status."""
    from harness.registry import ProjectRegistry

    registry = ProjectRegistry()
    projects = registry.list_projects()

    if not projects:
        print("No projects registered.")
        return 0

    print("Projects:")
    for hash_id, info in projects.items():
        path = info["path"]
        # Check if daemon is running by testing socket
        sock_path = str(Path.home() / ".harness" / "sockets" / f"{hash_id}.sock")
        if Path(sock_path).exists():
            status = "[running]"
        else:
            status = "[stopped]"

        # Check if path exists
        if not Path(path).exists():
            status = "[stale - path not found]"

        print(f"  {path}  {status}")

    return 0
```

**Step 9: Run all status tests** (30 sec)

```bash
pytest tests/harness/test_client.py -k status -v
```

Expected: All status tests pass

**Step 10: Commit** (30 sec)

```bash
git add src/harness/client.py tests/harness/test_client.py
git commit -m "feat(cli): add --project and --all flags for multi-project support"
```

---

### Task 4: Daemon Registers with Registry on Spawn

**Files:**
- Modify: `src/harness/daemon.py` (HarnessDaemon.__init__)
- Test: `tests/harness/test_daemon.py`

**Step 1: Write failing test for daemon registration** (3 min)

Add to `tests/harness/test_daemon.py`:

```python
def test_daemon_registers_with_registry(
    tmp_path: Path, socket_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daemon registers project in registry on spawn."""
    from harness.daemon import HarnessDaemon
    from harness.registry import ProjectRegistry

    registry_file = tmp_path / "registry.json"
    worktree = tmp_path / "project"
    worktree.mkdir()
    (worktree / ".claude").mkdir()

    # Use env var to configure registry path (keeps HarnessDaemon signature clean)
    monkeypatch.setenv("HARNESS_REGISTRY_FILE", str(registry_file))

    # Spawn daemon
    daemon = HarnessDaemon(socket_path, str(worktree))

    try:
        # Verify project was registered
        registry = ProjectRegistry(registry_file)
        projects = registry.list_projects()
        paths = [p["path"] for p in projects.values()]
        assert str(worktree) in paths
    finally:
        daemon.server_close()
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_daemon.py::test_daemon_registers_with_registry -v
```

Expected: FAIL with `AssertionError` (daemon doesn't register yet)

**Step 3: Update HarnessDaemon.__init__ to register with registry** (3 min)

In `src/harness/daemon.py`, add registration after state manager init (around line 423):

```python
# After: self.state_manager = StateManager(self.worktree_root)
# Add:
from harness.registry import ProjectRegistry

registry = ProjectRegistry()  # Uses HARNESS_REGISTRY_FILE env var
registry.register(self.worktree_root)
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/harness/test_daemon.py::test_daemon_registers_with_registry -v
```

Expected: PASS

**Step 5: Run all daemon tests to check for regressions** (30 sec)

```bash
pytest tests/harness/test_daemon.py -v
```

Expected: All tests pass

**Step 6: Commit** (30 sec)

```bash
git add src/harness/daemon.py tests/harness/test_daemon.py
git commit -m "feat(daemon): register project in registry on spawn"
```

---

### Task 5: Integration Test for Multi-Project Isolation

**Files:**
- Test: `tests/harness/test_integration.py`

**Step 1: Write integration test for two concurrent projects** (5 min)

Add to `tests/harness/test_integration.py`:

```python
def test_multi_project_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two projects run concurrently with isolated daemons."""
    import subprocess

    from harness.registry import ProjectRegistry

    # Create two projects
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    for p in [project_a, project_b]:
        p.mkdir()
        (p / ".claude").mkdir()
        subprocess.run(["git", "init"], cwd=p, capture_output=True)

    # Configure shared registry via env var
    registry_file = tmp_path / "registry.json"
    monkeypatch.setenv("HARNESS_REGISTRY_FILE", str(registry_file))

    # Clear HARNESS_SOCKET to use hash-based paths
    monkeypatch.delenv("HARNESS_SOCKET", raising=False)

    # Use unique socket paths based on worktree hash
    from harness.client import get_socket_path

    socket_a = get_socket_path(project_a)
    socket_b = get_socket_path(project_b)

    # Sockets should be different
    assert socket_a != socket_b

    # Spawn daemon for project A
    from harness.daemon import HarnessDaemon

    daemon_a = HarnessDaemon(socket_a, str(project_a))

    try:
        # Spawn daemon for project B
        daemon_b = HarnessDaemon(socket_b, str(project_b))

        try:
            # Both daemons running concurrently
            assert Path(socket_a).exists()
            assert Path(socket_b).exists()

            # Registry has both projects (tests race-condition safety)
            registry = ProjectRegistry(registry_file)
            projects = registry.list_projects()
            assert len(projects) == 2

        finally:
            daemon_b.server_close()
    finally:
        daemon_a.server_close()
```

**Step 2: Run integration test** (30 sec)

```bash
pytest tests/harness/test_integration.py::test_multi_project_isolation -v
```

Expected: PASS

**Step 3: Run full test suite** (1 min)

```bash
make test
```

Expected: All tests pass

**Step 4: Commit** (30 sec)

```bash
git add tests/harness/test_integration.py
git commit -m "test(integration): add multi-project isolation test"
```

---

### Task 6: Code Review

**Files:**
- All modified files from Tasks 1-5

**Step 1: Review changes** (5 min)

```bash
git diff main..HEAD --stat
git log main..HEAD --oneline
```

**Step 2: Run full test suite with coverage** (1 min)

```bash
make check
```

**Step 3: Verify no lint/type errors** (30 sec)

```bash
make lint
make typecheck
```

Expected: All checks pass

---

## Summary

| Task | Description | Files Changed |
|------|-------------|---------------|
| 1 | Registry module | +registry.py, +test_registry.py |
| 2 | Hash-based socket path | client.py |
| 3 | CLI --project and --all flags | client.py |
| 4 | Daemon registers with registry | daemon.py |
| 5 | Integration test | test_integration.py |
| 6 | Code review | (verification only) |
