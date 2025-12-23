# Demo Command Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-23-demo-command.md` to implement task-by-task.

**Goal:** Port the interactive `demo.sh` bash script to a Python-based `hyh demo` command that walks users through hyh features.

**Architecture:** Create a standalone `src/hyh/demo.py` module with the demo logic, lazily imported from client.py to preserve <50ms startup time. The demo runs in an isolated temp directory, spawning its own daemon, and uses subprocess calls to `hyh` to demonstrate each feature. Interactive "press Enter" prompts guide users through 12 steps.

**Tech Stack:** Python stdlib only (tempfile, subprocess, shutil, os), ANSI escape codes for colors, no external dependencies.

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1 | Core demo module with output helpers |
| Group 2 | 2 | Demo steps 1-4 (setup, worker-id, plan import, daemon basics) |
| Group 3 | 3 | Demo steps 5-8 (status, tasks, git mutex, hooks) |
| Group 4 | 4 | Demo steps 9-12 (multi-project, exec, state, architecture recap) |
| Group 5 | 5 | CLI integration in client.py |
| Group 6 | 6 | Test coverage |
| Group 7 | 7 | Code review |

---

### Task 1: Create demo.py module with output helpers and setup

**Files:**
- Create: `src/hyh/demo.py`
- Test: `tests/hyh/test_demo.py`

**Step 1: Write failing test for output helpers** (2-5 min)

```python
# tests/hyh/test_demo.py
"""Tests for hyh demo command."""

from io import StringIO

import pytest


def test_demo_colors_defined():
    """Demo module should define ANSI color constants."""
    from hyh.demo import Colors

    assert hasattr(Colors, "RED")
    assert hasattr(Colors, "GREEN")
    assert hasattr(Colors, "YELLOW")
    assert hasattr(Colors, "BLUE")
    assert hasattr(Colors, "MAGENTA")
    assert hasattr(Colors, "CYAN")
    assert hasattr(Colors, "BOLD")
    assert hasattr(Colors, "DIM")
    assert hasattr(Colors, "NC")


def test_print_header_formats_correctly(capsys):
    """print_header should produce bordered section header."""
    from hyh.demo import print_header

    print_header("Test Section")
    captured = capsys.readouterr()
    assert "Test Section" in captured.out
    assert "━" in captured.out  # Box drawing character


def test_print_step_formats_correctly(capsys):
    """print_step should format with arrow prefix."""
    from hyh.demo import print_step

    print_step("Doing something")
    captured = capsys.readouterr()
    assert "▶" in captured.out
    assert "Doing something" in captured.out
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_demo.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hyh.demo'`

**Step 3: Write minimal demo.py with output helpers** (5 min)

```python
# src/hyh/demo.py
"""Interactive demo for hyh CLI features."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class Colors:
    """ANSI escape codes for terminal colors."""

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    MAGENTA = "\033[0;35m"
    CYAN = "\033[0;36m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    NC = "\033[0m"  # No Color


def print_header(title: str) -> None:
    """Print a section header with box drawing."""
    c = Colors
    print()
    print(f"{c.MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{c.NC}")
    print(f"{c.BOLD}{c.MAGENTA}  {title}{c.NC}")
    print(f"{c.MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{c.NC}")
    print()


def print_step(text: str) -> None:
    """Print a step indicator."""
    print(f"{Colors.CYAN}▶ {Colors.BOLD}{text}{Colors.NC}")


def print_info(text: str) -> None:
    """Print informational text (dimmed)."""
    print(f"{Colors.DIM}  {text}{Colors.NC}")


def print_success(text: str) -> None:
    """Print success message with checkmark."""
    print(f"{Colors.GREEN}✓ {text}{Colors.NC}")


def print_command(cmd: str) -> None:
    """Print a command being executed."""
    print(f"{Colors.YELLOW}  $ {cmd}{Colors.NC}")


def print_explanation(text: str) -> None:
    """Print an explanation with info icon."""
    print(f"{Colors.BLUE}  ℹ {text}{Colors.NC}")


def wait_for_user() -> None:
    """Wait for user to press Enter to continue."""
    print()
    print(f"{Colors.DIM}  Press Enter to continue...{Colors.NC}")
    input()


def run_command(cmd: str, cwd: str | Path | None = None) -> str:
    """Run a shell command and return output, also printing it."""
    print_command(cmd)
    print()
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    output = result.stdout + result.stderr
    for line in output.splitlines():
        print(f"    {line}")
    print()
    return output
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_demo.py -v
```

Expected: PASS (3 passed)

**Step 5: Commit** (30 sec)

```bash
git add src/hyh/demo.py tests/hyh/test_demo.py
git commit -m "feat(demo): add demo module with output helpers"
```

---

### Task 2: Implement demo steps 1-4 (setup, worker-id, plan import, daemon)

**Files:**
- Modify: `src/hyh/demo.py`
- Test: `tests/hyh/test_demo.py`

**Step 1: Write failing test for DemoRunner class** (2-5 min)

```python
# Add to tests/hyh/test_demo.py

def test_demo_runner_creates_temp_directory():
    """DemoRunner should create isolated temp directory on enter."""
    from hyh.demo import DemoRunner

    with DemoRunner() as runner:
        assert runner.demo_dir.exists()
        assert runner.state_dir.exists()
        assert (runner.demo_dir / ".git").exists()


def test_demo_runner_cleans_up_on_exit():
    """DemoRunner should clean up temp directory on exit."""
    from hyh.demo import DemoRunner

    with DemoRunner() as runner:
        demo_dir = runner.demo_dir
    assert not demo_dir.exists()
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_demo.py::test_demo_runner_creates_temp_directory -v
```

Expected: FAIL with `ImportError: cannot import name 'DemoRunner'`

**Step 3: Implement DemoRunner context manager** (5 min)

Add to `src/hyh/demo.py`:

```python
class DemoRunner:
    """Context manager for running the interactive demo."""

    def __init__(self) -> None:
        self.demo_dir: Path = Path()
        self.state_dir: Path = Path()
        self._original_cwd: Path = Path()

    def __enter__(self) -> DemoRunner:
        """Set up isolated demo environment."""
        self.demo_dir = Path(tempfile.mkdtemp(prefix="hyh-demo-"))
        self.state_dir = self.demo_dir / ".claude"
        self.state_dir.mkdir()

        self._original_cwd = Path.cwd()
        os.chdir(self.demo_dir)

        # Initialize git repo
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.demo_dir,
            check=True,
        )
        (self.demo_dir / "README.md").write_text("# Demo\n")
        subprocess.run(
            ["git", "add", "README.md"],
            cwd=self.demo_dir,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit", "--quiet"],
            cwd=self.demo_dir,
            check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
                 "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.com"},
        )

        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: object) -> None:
        """Clean up demo environment."""
        os.chdir(self._original_cwd)

        # Shutdown daemon if running
        subprocess.run(
            ["hyh", "shutdown"],
            capture_output=True,
            cwd=self.demo_dir,
        )

        shutil.rmtree(self.demo_dir, ignore_errors=True)

    def run_hyh(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run hyh command in demo directory."""
        return subprocess.run(
            ["hyh", *args],
            capture_output=True,
            text=True,
            cwd=self.demo_dir,
        )
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_demo.py::test_demo_runner_creates_temp_directory tests/hyh/test_demo.py::test_demo_runner_cleans_up_on_exit -v
```

Expected: PASS (2 passed)

**Step 5: Implement demo steps 1-4** (5 min)

Add to `src/hyh/demo.py`:

```python
    def step_intro(self) -> None:
        """Step 0: Welcome and intro."""
        print("\033[2J\033[H", end="")  # Clear screen
        print_header("Welcome to Harness")

        c = Colors
        print(f"  {c.BOLD}Harness{c.NC} is a thread-safe state management daemon for dev workflows.")
        print()
        print("  It solves three problems:")
        print()
        print(f"    {c.GREEN}1.{c.NC} {c.BOLD}Task Coordination{c.NC} - Multiple workers claim/complete tasks from a DAG")
        print(f"    {c.GREEN}2.{c.NC} {c.BOLD}Git Safety{c.NC} - Mutex prevents parallel git operations corrupting .git/index")
        print(f"    {c.GREEN}3.{c.NC} {c.BOLD}Crash Recovery{c.NC} - Atomic writes ensure state survives power failures")
        print()
        print(f"  {c.DIM}Architecture: Dumb client (stdlib only) + Smart daemon (msgspec validation){c.NC}")
        print(f"  {c.DIM}Runtime: Python 3.13t free-threaded (true parallelism, no GIL){c.NC}")

        wait_for_user()

    def step_setup(self) -> None:
        """Step 1: Set up demo environment."""
        print_header("Step 1: Setting Up the Demo Environment")

        print_step("Creating isolated demo directory")
        print_info("We'll use a temporary directory so we don't touch your real workflows")
        print()
        print_success(f"Created demo git repo at: {self.demo_dir}")
        print()

        print_step("Creating a sample workflow with task dependencies")
        print_info("This creates a DAG (Directed Acyclic Graph) of tasks")
        print()

        # Create sample workflow state
        state = '''{
  "tasks": {
    "setup": {"id": "setup", "description": "Set up project scaffolding", "status": "pending", "dependencies": []},
    "backend": {"id": "backend", "description": "Implement backend API", "status": "pending", "dependencies": ["setup"]},
    "frontend": {"id": "frontend", "description": "Implement frontend UI", "status": "pending", "dependencies": ["setup"]},
    "integration": {"id": "integration", "description": "Integration testing", "status": "pending", "dependencies": ["backend", "frontend"]},
    "deploy": {"id": "deploy", "description": "Deploy to production", "status": "pending", "dependencies": ["integration"]}
  }
}'''
        (self.state_dir / "dev-workflow-state.json").write_text(state)

        self._print_dag()
        print_success("Workflow state created")
        print_explanation("Tasks can only run when ALL their dependencies are completed")

        wait_for_user()

    def _print_dag(self) -> None:
        """Print the task DAG diagram."""
        print(f"  {Colors.BOLD}Task DAG:{Colors.NC}")
        print()
        print("                    ┌─────────┐")
        print("                    │  setup  │")
        print("                    └────┬────┘")
        print("                         │")
        print("              ┌──────────┴──────────┐")
        print("              ▼                     ▼")
        print("        ┌─────────┐           ┌──────────┐")
        print("        │ backend │           │ frontend │")
        print("        └────┬────┘           └────┬─────┘")
        print("              │                     │")
        print("              └──────────┬──────────┘")
        print("                         ▼")
        print("                  ┌─────────────┐")
        print("                  │ integration │")
        print("                  └──────┬──────┘")
        print("                         │")
        print("                         ▼")
        print("                    ┌────────┐")
        print("                    │ deploy │")
        print("                    └────────┘")
        print()

    def step_worker_identity(self) -> None:
        """Step 2: Worker identity."""
        print_header("Step 2: Worker Identity")

        print_step("Each worker has a stable identity")
        print_info("Worker IDs persist across CLI invocations using atomic writes")
        print()

        run_command("hyh worker-id", cwd=self.demo_dir)

        print_explanation("This ID is used for task ownership (lease renewal)")
        print_explanation("Multiple invocations return the same ID")

        wait_for_user()

    def step_plan_import(self) -> None:
        """Step 3: Plan import from LLM output."""
        print_header("Step 3: Importing Plans from LLM Output")

        print_step("LLM orchestrators emit plans in structured Markdown format")
        print_info("The 'plan import' command parses and validates the DAG")
        print()

        # Create sample LLM plan
        plan_content = '''I'll create a plan for building the API:

**Goal:** Build REST API with authentication

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------||
| Group 1    | setup-db | Core infrastructure |
| Group 2    | auth-endpoints | Depends on DB |
| Group 3    | api-tests | Integration tests |

---

### Task setup-db: Initialize database schema

Create tables for users and sessions using SQLAlchemy.

### Task auth-endpoints: Implement login/logout endpoints

Use JWT tokens with 24h expiry. Create /login and /logout routes.

### Task api-tests: Write integration tests

Test full authentication flow with pytest.
'''
        plan_file = self.demo_dir / "llm-output.md"
        plan_file.write_text(plan_content)

        print(f"  {Colors.BOLD}Sample LLM output file:{Colors.NC}")
        print()
        run_command(f"cat '{plan_file}'", cwd=self.demo_dir)

        print_step("Import the plan")
        print()
        run_command(f"hyh plan import --file '{plan_file}'", cwd=self.demo_dir)

        print_step("View the imported state")
        print()
        run_command("hyh get-state | jq '.tasks | to_entries[] | {id: .key, status: .value.status, deps: .value.dependencies}'", cwd=self.demo_dir)

        print_explanation("Dependencies are inferred from Task Groups (Group N depends on Group N-1)")
        print_explanation("Task instructions come from the Markdown body under each ### Task header")
        print()

        print_step("Get the plan template (shows format documentation)")
        print()
        run_command("hyh plan template | head -50", cwd=self.demo_dir)

        print_explanation("Use 'plan template' to see the full Markdown format for LLM prompting")

        wait_for_user()

    def step_daemon_basics(self) -> None:
        """Step 4: Basic daemon commands."""
        print_header("Step 4: Basic Daemon Commands")

        print_step("Ping the daemon")
        print_info("The daemon auto-spawns on first command if not running")
        print()

        run_command("hyh ping", cwd=self.demo_dir)

        print_explanation("The daemon is now running as a background process")
        print_explanation("It listens on a Unix socket for client requests")

        wait_for_user()

        print_step("View the current workflow state")
        print()

        run_command("hyh get-state | jq . | head -40", cwd=self.demo_dir)

        print_explanation("All 3 tasks are 'pending' - none have been claimed yet")
        print_explanation("Only 'setup-db' is claimable (it has no dependencies)")

        wait_for_user()
```

**Step 6: Run tests** (30 sec)

```bash
pytest tests/hyh/test_demo.py -v
```

Expected: PASS

**Step 7: Commit** (30 sec)

```bash
git add src/hyh/demo.py tests/hyh/test_demo.py
git commit -m "feat(demo): add steps 1-4 (setup, worker-id, plan import, daemon)"
```

---

### Task 3: Implement demo steps 5-8 (status, tasks, git mutex, hooks)

**Files:**
- Modify: `src/hyh/demo.py`

**Step 1: Implement step 5 - status dashboard** (3 min)

Add to `DemoRunner` class in `src/hyh/demo.py`:

```python
    def step_status_dashboard(self) -> None:
        """Step 5: Status dashboard."""
        print_header("Step 5: Status Dashboard")

        print_step("View workflow status at a glance")
        print_info("The 'status' command provides a real-time dashboard")
        print()

        run_command("hyh status", cwd=self.demo_dir)

        print_explanation("Progress bar shows completion percentage")
        print_explanation("Task table shows status, worker, and blocking dependencies")
        print_explanation("Recent events show what happened and when")

        wait_for_user()

        print_step("Machine-readable output for scripting")
        print()

        run_command("hyh status --json | jq '.summary'", cwd=self.demo_dir)

        print_explanation("Use --json for CI/CD integration")
        print_explanation("Use --watch for live updates (e.g., hyh status --watch 2)")

        wait_for_user()
```

**Step 2: Implement step 6 - task workflow** (5 min)

```python
    def step_task_workflow(self) -> None:
        """Step 6: Task claiming and completion."""
        print_header("Step 6: Task Claiming and Completion")

        print_step("Claim the first available task")
        print_info("Each worker gets a unique ID and claims tasks atomically")
        print()

        run_command("hyh task claim", cwd=self.demo_dir)

        print_explanation("We got 'setup-db' - the only task with no dependencies")
        print_explanation("The task is now 'running' and locked to our worker ID")

        wait_for_user()

        print_step("Try to claim again (idempotency)")
        print_info("Claiming again returns the same task - lease renewal pattern")
        print()

        run_command("hyh task claim", cwd=self.demo_dir)

        print_explanation("Same task returned - this is intentional!")
        print_explanation("It renews the lease timestamp, preventing task theft on retries")

        wait_for_user()

        print_step("Complete the setup-db task")
        print()

        run_command("hyh task complete --id setup-db", cwd=self.demo_dir)

        print_success("Task completed!")
        print()

        print_step("What tasks are claimable now?")
        print()

        run_command(
            "hyh get-state | jq -r '"
            ".tasks as $tasks | "
            "$tasks | to_entries[] | "
            ".key as $tid | "
            ".value.status as $status | "
            ".value.dependencies as $deps | "
            "(if $status == \"pending\" and ([$deps[] | $tasks[.].status] | all(. == \"completed\")) then \" <- CLAIMABLE\" else \"\" end) as $marker | "
            "\"\\($tid): \\($status)\\($marker)\"'",
            cwd=self.demo_dir,
        )

        print_explanation("'auth-endpoints' is now claimable (depends on completed 'setup-db')")
        print_explanation("'api-tests' is still blocked (depends on 'auth-endpoints')")

        wait_for_user()

        print_step("Complete the remaining tasks")
        print()

        # Claim and complete remaining tasks
        for task_id in ["auth-endpoints", "api-tests"]:
            result = self.run_hyh("task", "claim")
            print_command("hyh task claim")
            import json
            try:
                data = json.loads(result.stdout)
                claimed_id = data.get("task", {}).get("id", "unknown")
                print(f"    Claimed: {claimed_id}")
            except json.JSONDecodeError:
                print(f"    {result.stdout}")
            print()

            print_command(f"hyh task complete --id {task_id}")
            self.run_hyh("task", "complete", "--id", task_id)
            print()

        print_success("All tasks completed!")

        wait_for_user()

        print_step("Final state")
        print()

        run_command("hyh get-state | jq -r '.tasks | to_entries[] | \"\\(.key): \\(.value.status)\"'", cwd=self.demo_dir)

        print_explanation("Every task is now 'completed' - workflow finished!")

        wait_for_user()
```

**Step 3: Implement step 7 - git mutex** (3 min)

```python
    def step_git_mutex(self) -> None:
        """Step 7: Git operations with mutex."""
        print_header("Step 7: Git Operations with Mutex")

        print_step("The problem: parallel git operations corrupt .git/index")
        print_info("Two workers running 'git add' simultaneously = data loss")
        print()

        print_step("The solution: hyh git -- <command>")
        print_info("All git operations go through a global mutex")
        print()

        (self.demo_dir / "demo.txt").write_text("demo content\n")

        run_command("hyh git -- add demo.txt", cwd=self.demo_dir)
        run_command("hyh git -- status", cwd=self.demo_dir)
        run_command("hyh git -- commit -m 'Add demo file'", cwd=self.demo_dir)

        print_explanation("Each git command acquires an exclusive lock")
        print_explanation("Parallel workers block until the lock is free")
        print_explanation("Result: safe git operations, no corruption")

        wait_for_user()
```

**Step 4: Implement step 8 - hook integration** (5 min)

```python
    def step_hook_integration(self) -> None:
        """Step 8: Claude Code hook integration."""
        print_header("Step 8: Claude Code Hook Integration")

        print_step("Harness provides hooks for Claude Code plugins")
        print_info("Three hooks: session-start, check-state, check-commit")
        print()

        c = Colors
        print(f"  {c.BOLD}1. SessionStart Hook{c.NC} - Shows workflow progress on session resume")
        print()
        run_command("hyh session-start | jq .", cwd=self.demo_dir)

        print_explanation("This output gets injected into Claude's context at session start")
        print()

        print_step("2. Stop Hook (check-state)")
        print_info("Prevents ending session while workflow is incomplete")
        print()

        # Create incomplete workflow
        incomplete_state = '''{
  "tasks": {
    "incomplete-task": {"id": "incomplete-task", "description": "This task is not done", "status": "pending", "dependencies": []}
  }
}'''
        (self.state_dir / "dev-workflow-state.json").write_text(incomplete_state)

        print(f"  {c.DIM}Created workflow with 1 pending task{c.NC}")
        print()
        run_command("hyh check-state || true", cwd=self.demo_dir)

        print_explanation("Exit code 1 + 'deny' = Claude Code blocks the session end")
        print()

        # Complete the task
        self.run_hyh("task", "claim")
        self.run_hyh("task", "complete", "--id", "incomplete-task")
        print(f"  {c.DIM}Task completed...{c.NC}")
        print()

        run_command("hyh check-state", cwd=self.demo_dir)

        print_explanation("Exit code 0 + 'allow' = Session can end")
        print()

        print_step("3. SubagentStop Hook (check-commit)")
        print_info("Requires agents to make git commits after work")
        print()

        # Set up last_commit in state
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=self.demo_dir,
        )
        current_head = result.stdout.strip()
        self.run_hyh("update-state", "--field", "last_commit", current_head)

        run_command("hyh check-commit || true", cwd=self.demo_dir)

        print_explanation("If HEAD matches last_commit, agent hasn't committed new work")
        print_explanation("Useful to ensure code changes are persisted")

        wait_for_user()
```

**Step 5: Run existing tests** (30 sec)

```bash
pytest tests/hyh/test_demo.py -v
```

Expected: PASS

**Step 6: Commit** (30 sec)

```bash
git add src/hyh/demo.py
git commit -m "feat(demo): add steps 5-8 (status, tasks, git mutex, hooks)"
```

---

### Task 4: Implement demo steps 9-12 (multi-project, exec, state, architecture)

**Files:**
- Modify: `src/hyh/demo.py`

**Step 1: Implement step 9 - multi-project isolation** (3 min)

Add to `DemoRunner` class:

```python
    def step_multi_project(self) -> None:
        """Step 9: Multi-project isolation."""
        print_header("Step 9: Multi-Project Isolation")

        print_step("Each project gets an isolated daemon")
        print_info("Socket paths are hashed from the git worktree root")
        print()

        import hashlib
        socket_hash = hashlib.sha256(str(self.demo_dir).encode()).hexdigest()[:12]

        print_explanation("This demo project has its own daemon socket at:")
        print()
        print(f"  {Colors.DIM}~/.hyh/sockets/{socket_hash}.sock{Colors.NC}")
        print()

        print_step("View all registered projects")
        print()

        run_command("hyh status --all", cwd=self.demo_dir)

        print_explanation("Multiple hyh daemons can run simultaneously")
        print_explanation("Use --project <path> to target a specific project")

        wait_for_user()
```

**Step 2: Implement step 10 - exec and trajectory** (3 min)

```python
    def step_exec_trajectory(self) -> None:
        """Step 10: Command execution and observability."""
        print_header("Step 10: Command Execution and Observability")

        print_step("Execute arbitrary commands")
        print_info("The 'exec' command runs any shell command through the daemon")
        print()

        run_command("hyh exec -- echo 'Hello from hyh!'", cwd=self.demo_dir)
        run_command("hyh exec -- python3 -c 'print(2 + 2)'", cwd=self.demo_dir)

        print_explanation("Commands can optionally acquire the exclusive lock (--exclusive)")
        print_explanation("Useful for operations that need serialization")

        wait_for_user()

        print_step("View the trajectory log")
        print_info("Every operation is logged to .claude/trajectory.jsonl")
        print()

        trajectory_file = self.state_dir / "trajectory.jsonl"
        if trajectory_file.exists():
            run_command(f"cat '{trajectory_file}' | jq -s '.[0:3]' | head -60", cwd=self.demo_dir)
        else:
            print(f"  {Colors.DIM}(Trajectory file not created in demo){Colors.NC}")
            print()

        print_explanation("JSONL format: append-only, crash-safe")
        print_explanation("O(1) tail retrieval - reads from end of file")
        print_explanation("Each event has timestamp, duration, reason for debugging")

        wait_for_user()
```

**Step 3: Implement step 11 - state updates** (2 min)

```python
    def step_state_updates(self) -> None:
        """Step 11: Direct state updates."""
        print_header("Step 11: Direct State Updates")

        print_step("Update state fields directly")
        print_info("Useful for orchestration metadata")
        print()

        run_command("hyh update-state --field current_phase 'deployment' --field parallel_workers 3", cwd=self.demo_dir)
        run_command("hyh get-state | jq 'del(.tasks)'", cwd=self.demo_dir)

        print_explanation("State updates are atomic and validated by msgspec")
        print_explanation("Unknown fields are allowed for flexibility")

        wait_for_user()
```

**Step 4: Implement step 12 - architecture overview and recap** (5 min)

```python
    def step_architecture(self) -> None:
        """Step 12: Architecture overview."""
        print_header("Step 12: Architecture Overview")

        c = Colors
        print(f"  {c.BOLD}Client-Daemon Split{c.NC}")
        print()
        print("    ┌──────────────────────────────────────────────────────────────────┐")
        print("    │                        CLIENT (client.py)                        │")
        print("    │  • Imports ONLY stdlib (sys, json, socket, argparse)             │")
        print("    │  • <50ms startup time                                            │")
        print("    │  • Zero validation logic                                         │")
        print("    │  • Hash-based socket path for multi-project isolation            │")
        print("    └──────────────────────────────────────────────────────────────────┘")
        print("                                   │")
        print("                           Unix Domain Socket")
        print("                                   │")
        print("                                   ▼")
        print("    ┌──────────────────────────────────────────────────────────────────┐")
        print("    │                        DAEMON (daemon.py)                        │")
        print("    │  • ThreadingMixIn for parallel request handling                  │")
        print("    │  • msgspec validation at the boundary                            │")
        print("    │  • StateManager with thread-safe locking                         │")
        print("    │  • TrajectoryLogger for observability                            │")
        print("    │  • Runtime abstraction (Local or Docker)                         │")
        print("    └──────────────────────────────────────────────────────────────────┘")
        print()

        wait_for_user()

        print(f"  {c.BOLD}Lock Hierarchy (Deadlock Prevention){c.NC}")
        print()
        print("    Acquire locks in this order ONLY:")
        print()
        print("    ┌───────────────────────────────────────┐")
        print("    │  1. StateManager._lock     (highest)  │  Protects DAG state")
        print("    ├───────────────────────────────────────┤")
        print("    │  2. TrajectoryLogger._lock            │  Protects event log")
        print("    ├───────────────────────────────────────┤")
        print("    │  3. GLOBAL_EXEC_LOCK       (lowest)   │  Protects git index")
        print("    └───────────────────────────────────────┘")
        print()
        print(f"  {c.DIM}Release-then-Log Pattern: Release state lock BEFORE logging{c.NC}")
        print(f"  {c.DIM}This prevents lock convoy (threads waiting on I/O){c.NC}")
        print()

        wait_for_user()

        print(f"  {c.BOLD}Atomic Persistence Pattern{c.NC}")
        print()
        print("    ┌─────────────────────────────────────────────────────────────┐")
        print("    │  1. Write to state.json.tmp                                 │")
        print("    │  2. fsync() - ensure bytes hit disk                         │")
        print("    │  3. rename(tmp, state.json) - POSIX atomic operation        │")
        print("    └─────────────────────────────────────────────────────────────┘")
        print()
        print(f"  {c.DIM}If power fails during write: tmp file is corrupt, original intact{c.NC}")
        print(f"  {c.DIM}If power fails during rename: atomic, so either old or new state{c.NC}")
        print()

        wait_for_user()

    def step_recap(self) -> None:
        """Print key commands recap."""
        print_header("Recap: Key Commands")

        c = Colors
        print(f"  {c.BOLD}Daemon Control{c.NC}")
        print(f"    {c.YELLOW}hyh ping{c.NC}              Check if daemon is running")
        print(f"    {c.YELLOW}hyh shutdown{c.NC}          Stop the daemon")
        print()
        print(f"  {c.BOLD}Worker Identity{c.NC}")
        print(f"    {c.YELLOW}hyh worker-id{c.NC}         Print stable worker ID")
        print()
        print(f"  {c.BOLD}Plan Management{c.NC}")
        print(f"    {c.YELLOW}hyh plan import --file{c.NC}  Import LLM-generated plan")
        print(f"    {c.YELLOW}hyh plan template{c.NC}       Show Markdown plan format")
        print(f"    {c.YELLOW}hyh plan reset{c.NC}          Clear workflow state")
        print()
        print(f"  {c.BOLD}Status Dashboard{c.NC}")
        print(f"    {c.YELLOW}hyh status{c.NC}            Show workflow dashboard")
        print(f"    {c.YELLOW}hyh status --json{c.NC}     Machine-readable output")
        print(f"    {c.YELLOW}hyh status --watch{c.NC}    Auto-refresh mode")
        print(f"    {c.YELLOW}hyh status --all{c.NC}      List all projects")
        print()
        print(f"  {c.BOLD}State Management{c.NC}")
        print(f"    {c.YELLOW}hyh get-state{c.NC}         Get current workflow state")
        print(f"    {c.YELLOW}hyh update-state{c.NC}      Update state fields")
        print()
        print(f"  {c.BOLD}Task Workflow{c.NC}")
        print(f"    {c.YELLOW}hyh task claim{c.NC}        Claim next available task")
        print(f"    {c.YELLOW}hyh task complete{c.NC}     Mark task as completed")
        print()
        print(f"  {c.BOLD}Command Execution{c.NC}")
        print(f"    {c.YELLOW}hyh git -- <cmd>{c.NC}      Git with mutex")
        print(f"    {c.YELLOW}hyh exec -- <cmd>{c.NC}     Arbitrary command")
        print()
        print(f"  {c.BOLD}Hook Integration{c.NC}")
        print(f"    {c.YELLOW}hyh session-start{c.NC}     SessionStart hook output")
        print(f"    {c.YELLOW}hyh check-state{c.NC}       Stop hook (deny if incomplete)")
        print(f"    {c.YELLOW}hyh check-commit{c.NC}      SubagentStop hook (deny if no commit)")
        print()

        wait_for_user()

    def step_next_steps(self) -> None:
        """Print next steps."""
        print_header("Next Steps")

        c = Colors
        print(f"  {c.BOLD}1. Explore the codebase{c.NC}")
        print("     src/hyh/client.py    - Dumb CLI client")
        print("     src/hyh/daemon.py    - ThreadingMixIn server")
        print("     src/hyh/state.py     - msgspec models + StateManager")
        print("     src/hyh/trajectory.py - JSONL logging")
        print("     src/hyh/runtime.py   - Local/Docker execution")
        print("     src/hyh/plan.py      - Markdown plan parser → WorkflowState")
        print("     src/hyh/git.py       - Git operations via runtime")
        print("     src/hyh/acp.py       - Background event emitter")
        print("     src/hyh/registry.py  - Multi-project registry")
        print()
        print(f"  {c.BOLD}2. Run the tests{c.NC}")
        print("     make test                           # All tests (30s timeout)")
        print("     make test-fast                      # No timeout (faster iteration)")
        print("     make check                          # lint + typecheck + test")
        print()
        print(f"  {c.BOLD}3. Read the architecture docs{c.NC}")
        print("     docs/plans/                         # Design documents")
        print()
        print(f"  {c.BOLD}4. Try parallel workers{c.NC}")
        print("     Open multiple terminals and run 'hyh task claim'")
        print("     Watch them coordinate via the shared state")
        print()

        print_header("Demo Complete!")

        print("  Thanks for taking the tour!")
        print()
        print(f"  {c.DIM}Demo directory will be cleaned up on exit.{c.NC}")
        print()
```

**Step 5: Add main run method** (2 min)

```python
    def run(self) -> None:
        """Run the full interactive demo."""
        self.step_intro()
        self.step_setup()
        self.step_worker_identity()
        self.step_plan_import()
        self.step_daemon_basics()
        self.step_status_dashboard()
        self.step_task_workflow()
        self.step_git_mutex()
        self.step_hook_integration()
        self.step_multi_project()
        self.step_exec_trajectory()
        self.step_state_updates()
        self.step_architecture()
        self.step_recap()
        self.step_next_steps()


def run_demo() -> None:
    """Entry point for the demo command."""
    # Check for jq
    if shutil.which("jq") is None:
        print(f"{Colors.RED}ERROR: jq is required but not installed.{Colors.NC}")
        print(f"{Colors.DIM}Install with: brew install jq (macOS) or apt install jq (Linux){Colors.NC}")
        sys.exit(1)

    with DemoRunner() as runner:
        runner.run()
```

**Step 6: Run tests** (30 sec)

```bash
pytest tests/hyh/test_demo.py -v
```

Expected: PASS

**Step 7: Commit** (30 sec)

```bash
git add src/hyh/demo.py
git commit -m "feat(demo): add steps 9-12 and run_demo entry point"
```

---

### Task 5: Add CLI integration in client.py

**Files:**
- Modify: `src/hyh/client.py`

**Step 1: Write failing test for demo command** (2 min)

Add to `tests/hyh/test_demo.py`:

```python
def test_demo_command_registered():
    """hyh demo should be a valid command."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "hyh.client", "demo", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "demo" in result.stdout.lower() or "interactive" in result.stdout.lower()
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/hyh/test_demo.py::test_demo_command_registered -v
```

Expected: FAIL with error about invalid command

**Step 3: Add demo command to client.py** (3 min)

Add parser registration (after line 534, before `args = parser.parse_args()`):

```python
    subparsers.add_parser("demo", help="Interactive tour of hyh features")
```

Add command handler function (before `if __name__ == "__main__":`):

```python
def _cmd_demo() -> None:
    from hyh.demo import run_demo

    run_demo()
```

Add case to match statement (after the status case):

```python
        case "demo":
            _cmd_demo()
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
pytest tests/hyh/test_demo.py::test_demo_command_registered -v
```

Expected: PASS

**Step 5: Commit** (30 sec)

```bash
git add src/hyh/client.py tests/hyh/test_demo.py
git commit -m "feat(demo): register demo command in CLI"
```

---

### Task 6: Add comprehensive test coverage

**Files:**
- Modify: `tests/hyh/test_demo.py`

**Step 1: Add test for jq dependency check** (2 min)

```python
def test_run_demo_requires_jq(monkeypatch):
    """run_demo should exit if jq is not available."""
    import shutil
    from hyh.demo import run_demo

    # Patch shutil.which to return None for jq
    original_which = shutil.which

    def mock_which(cmd):
        if cmd == "jq":
            return None
        return original_which(cmd)

    monkeypatch.setattr(shutil, "which", mock_which)

    with pytest.raises(SystemExit) as exc_info:
        run_demo()
    assert exc_info.value.code == 1
```

**Step 2: Add test for run_command output** (2 min)

```python
def test_run_command_returns_output(capsys, tmp_path, monkeypatch):
    """run_command should execute command and return output."""
    from hyh.demo import run_command

    monkeypatch.chdir(tmp_path)
    output = run_command("echo 'test output'", cwd=tmp_path)
    assert "test output" in output


def test_demo_runner_runs_hyh_commands():
    """DemoRunner.run_hyh should execute hyh commands."""
    from hyh.demo import DemoRunner

    with DemoRunner() as runner:
        result = runner.run_hyh("--help")
        assert result.returncode == 0
        assert "hyh" in result.stdout.lower()
```

**Step 3: Run all tests** (30 sec)

```bash
pytest tests/hyh/test_demo.py -v
```

Expected: PASS

**Step 4: Run full test suite** (30 sec)

```bash
make test
```

Expected: PASS

**Step 5: Commit** (30 sec)

```bash
git add tests/hyh/test_demo.py
git commit -m "test(demo): add comprehensive test coverage"
```

---

### Task 7: Code Review

Run code review to verify implementation quality, test coverage, and adherence to project conventions.
