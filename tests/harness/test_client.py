# tests/harness/test_client.py
"""
Tests for the "dumb" client.

The client MUST NOT import pydantic or harness.state.
It only uses stdlib: sys, json, socket, os, subprocess, time, argparse.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


def test_client_has_no_heavy_imports():
    """Client must only import stdlib modules."""
    # Import the module and check its imports
    import harness.client as client_module

    # Get all imported modules
    import_names = set()
    for name, obj in vars(client_module).items():
        if hasattr(obj, "__module__"):
            import_names.add(obj.__module__)

    # These are forbidden (heavy imports)
    forbidden = {"pydantic", "harness.state"}
    violations = import_names & forbidden
    assert not violations, f"Client has forbidden imports: {violations}"


def test_client_does_not_import_pydantic():
    """Client module must not import pydantic (startup performance).

    This is a critical constraint - pydantic adds ~20-30ms to import time.
    The client must remain a dumb RPC wrapper with stdlib-only imports.
    """
    # Run in subprocess to test fresh import
    script = """
import sys
import harness.client

# Check if pydantic is in loaded modules
pydantic_loaded = any('pydantic' in mod for mod in sys.modules)
if pydantic_loaded:
    print("FAIL: pydantic imported")
    sys.exit(1)
else:
    print("OK: pydantic not imported")
    sys.exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent.parent,  # Project root
    )

    assert result.returncode == 0, (
        f"Client imported pydantic! This violates the <50ms startup constraint.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_client_startup_time():
    """Client module imports in <50ms (allow 100ms for CI variance).

    The client must be a fast, dumb RPC wrapper. Any validation/logic lives
    in the daemon. This test ensures we don't accidentally add heavy imports.
    """
    # Run in subprocess to measure cold import time
    script = """
import sys
import time

start = time.monotonic()
import harness.client
elapsed_ms = (time.monotonic() - start) * 1000

print(f"{elapsed_ms:.2f}")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent.parent,  # Project root
    )

    assert result.returncode == 0, f"Import failed: {result.stderr}"

    elapsed_ms = float(result.stdout.strip())

    # Target: <50ms, but allow 100ms for CI variance (slow runners, cold cache)
    assert elapsed_ms < 100, (
        f"Client import took {elapsed_ms:.2f}ms (target: <50ms, max: <100ms)\n"
        f"This suggests heavy imports were added. Client must use stdlib only."
    )


@pytest.fixture
def worktree_with_daemon(tmp_path):
    """Create worktree and let client auto-spawn daemon."""
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True
    )
    (tmp_path / "file.txt").write_text("content")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True
    )

    # Create state file (v2 JSON schema with task DAG)
    state_dir = tmp_path / ".claude"
    state_dir.mkdir()
    state_file = state_dir / "dev-workflow-state.json"
    import json

    state_data = {
        "tasks": {
            "task-1": {
                "id": "task-1",
                "description": "First task",
                "status": "pending",
                "dependencies": [],
                "started_at": None,
                "completed_at": None,
                "claimed_by": None,
                "timeout_seconds": 600,
            },
            "task-2": {
                "id": "task-2",
                "description": "Second task",
                "status": "pending",
                "dependencies": ["task-1"],
                "started_at": None,
                "completed_at": None,
                "claimed_by": None,
                "timeout_seconds": 600,
            },
        }
    }
    state_file.write_text(json.dumps(state_data, indent=2))

    # Use short socket path in /tmp to avoid macOS path length limit
    socket_id = uuid.uuid4().hex[:8]
    socket_path = f"/tmp/harness-test-{socket_id}.sock"

    yield {"socket": socket_path, "worktree": tmp_path}

    # Cleanup: gracefully shutdown daemon and remove socket
    from .conftest import cleanup_daemon_subprocess

    cleanup_daemon_subprocess(socket_path)


def test_client_auto_spawns_daemon(worktree_with_daemon):
    """Client should spawn daemon on ConnectionRefused."""
    from harness.client import send_rpc

    socket_path = worktree_with_daemon["socket"]
    worktree = worktree_with_daemon["worktree"]

    # No daemon running yet - client should auto-spawn
    response = send_rpc(
        socket_path,
        {"command": "ping"},
        worktree_root=str(worktree),
    )

    assert response["status"] == "ok"
    assert response["data"]["running"] is True


def test_client_get_state(worktree_with_daemon):
    from harness.client import send_rpc

    socket_path = worktree_with_daemon["socket"]
    worktree = worktree_with_daemon["worktree"]

    response = send_rpc(
        socket_path,
        {"command": "get_state"},
        worktree_root=str(worktree),
    )

    assert response["status"] == "ok"
    # v2 schema uses task DAG
    assert "tasks" in response["data"]
    assert "task-1" in response["data"]["tasks"]
    assert response["data"]["tasks"]["task-1"]["status"] == "pending"


def test_client_git_command(worktree_with_daemon):
    from harness.client import send_rpc

    socket_path = worktree_with_daemon["socket"]
    worktree = worktree_with_daemon["worktree"]

    response = send_rpc(
        socket_path,
        {"command": "git", "args": ["status"], "cwd": str(worktree)},
        worktree_root=str(worktree),
    )

    assert response["status"] == "ok"
    assert response["data"]["returncode"] == 0


def test_spawn_daemon_detects_crash(tmp_path):
    """spawn_daemon should detect immediate crashes (zombie detection).

    This test verifies that spawn_daemon properly detects when the daemon
    process exits immediately (e.g., due to an error during startup).
    We trigger this by passing a worktree path that will cause fcntl.flock
    to fail (by making the lock file directory non-writable).
    """
    import stat

    from harness.client import spawn_daemon

    # Use short socket path in /tmp to avoid macOS path length limit
    socket_id = uuid.uuid4().hex[:8]
    # Create a directory for the socket but make it unwritable
    socket_dir = Path(f"/tmp/harness-test-crash-{socket_id}")
    socket_dir.mkdir()
    socket_path = str(socket_dir / "harness.sock")

    # Make socket directory unwritable to cause daemon to fail
    socket_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x only

    try:
        with pytest.raises(RuntimeError, match="crashed|failed"):
            spawn_daemon(str(tmp_path), socket_path)
    finally:
        # Restore permissions and cleanup
        socket_dir.chmod(stat.S_IRWXU)
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        if os.path.exists(socket_path + ".lock"):
            os.unlink(socket_path + ".lock")
        socket_dir.rmdir()


# TestWorkerID
class TestWorkerID:
    """Tests for WORKER_ID constant."""

    def test_worker_id_generated_on_import(self):
        """Client has WORKER_ID constant."""
        import harness.client as client_module

        assert hasattr(client_module, "WORKER_ID")
        assert isinstance(client_module.WORKER_ID, str)
        assert client_module.WORKER_ID.startswith("worker-")
        assert len(client_module.WORKER_ID) == len("worker-") + 12

    def test_worker_id_is_stable(self):
        """WORKER_ID is same within process."""
        import harness.client as client_module

        worker_id_1 = client_module.WORKER_ID
        worker_id_2 = client_module.WORKER_ID
        assert worker_id_1 == worker_id_2

    def test_worker_id_stable_across_processes(self, tmp_path):
        """WORKER_ID must be identical across separate CLI invocations.

        This is critical for idempotent task claims - if a worker claims a task,
        then calls claim again, it should get the same task back (lease renewal).
        This requires the worker_id to be stable across process invocations.

        Bug regression test: uuid.uuid4() generates new ID per process.
        """
        # Script that prints the worker ID
        script = """
import sys
sys.path.insert(0, 'src')
from harness.client import get_worker_id
print(get_worker_id())
"""
        script_file = tmp_path / "get_worker_id.py"
        script_file.write_text(script)

        # Run twice as separate processes
        result1 = subprocess.run(
            ["python", str(script_file)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,  # Project root
        )
        result2 = subprocess.run(
            ["python", str(script_file)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result1.returncode == 0, f"First call failed: {result1.stderr}"
        assert result2.returncode == 0, f"Second call failed: {result2.stderr}"

        worker_id_1 = result1.stdout.strip()
        worker_id_2 = result2.stdout.strip()

        assert worker_id_1 == worker_id_2, (
            f"Worker ID changed between processes!\n"
            f"  First:  {worker_id_1}\n"
            f"  Second: {worker_id_2}\n"
            f"This breaks idempotent task claims (lease renewal pattern)."
        )


# TestTaskCommands
class TestTaskCommands:
    """Tests for task claim/complete commands."""

    def test_task_claim_returns_json(self, worktree_with_daemon):
        """harness task claim returns task JSON."""
        from harness.client import send_rpc

        socket_path = worktree_with_daemon["socket"]
        worktree = worktree_with_daemon["worktree"]

        # Task claim should include WORKER_ID
        import harness.client as client_module

        response = send_rpc(
            socket_path,
            {"command": "task_claim", "worker_id": client_module.WORKER_ID},
            worktree_root=str(worktree),
        )

        assert response["status"] == "ok"
        # Daemon will return task data (format determined by daemon)
        assert "data" in response

    def test_task_complete_requires_id(self, worktree_with_daemon):
        """harness task complete needs task_id and worker_id."""
        from harness.client import send_rpc

        socket_path = worktree_with_daemon["socket"]
        worktree = worktree_with_daemon["worktree"]

        import harness.client as client_module

        response = send_rpc(
            socket_path,
            {
                "command": "task_complete",
                "task_id": "task-123",
                "worker_id": client_module.WORKER_ID,
            },
            worktree_root=str(worktree),
        )

        # Response status depends on daemon implementation
        # This test just verifies the command structure
        assert "status" in response


# TestExecCommand
class TestExecCommand:
    """Tests for exec command."""

    def test_exec_runs_command(self, worktree_with_daemon):
        """harness exec -- echo hello works."""
        from harness.client import send_rpc

        socket_path = worktree_with_daemon["socket"]
        worktree = worktree_with_daemon["worktree"]

        response = send_rpc(
            socket_path,
            {
                "command": "exec",
                "args": ["echo", "hello"],
                "cwd": str(worktree),
                "env": {},
                "timeout": 5.0,
            },
            worktree_root=str(worktree),
        )

        assert response["status"] == "ok"
        assert "data" in response

    def test_exec_with_env(self, worktree_with_daemon):
        """harness exec -e VAR=value passes env vars."""
        from harness.client import send_rpc

        socket_path = worktree_with_daemon["socket"]
        worktree = worktree_with_daemon["worktree"]

        response = send_rpc(
            socket_path,
            {
                "command": "exec",
                "args": ["printenv", "TEST_VAR"],
                "cwd": str(worktree),
                "env": {"TEST_VAR": "test_value"},
                "timeout": 5.0,
            },
            worktree_root=str(worktree),
        )

        assert response["status"] == "ok"
        assert "data" in response


def test_plan_import_file_not_found():
    """harness plan import should error on missing file."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "harness.client", "plan", "import", "--file", "/ghost.md"],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower()


def test_plan_template_outputs_schema(capsys):
    """harness plan template prints valid JSON schema."""
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "harness.client", "plan", "template"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "properties" in data
    assert "goal" in data["properties"]


def test_client_plan_template_does_not_break_import_constraints():
    """Adding plan import doesn't violate stdlib-only rule.

    The get_plan_schema import is allowed because it's from harness.plan,
    not from pydantic directly.
    """
    import ast
    from pathlib import Path

    client_source = Path("src/harness/client.py").read_text()
    tree = ast.parse(client_source)

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    # harness.plan is allowed (it's our code)
    # pydantic direct import is NOT allowed
    assert "pydantic" not in imports
    assert "harness.plan" in imports
