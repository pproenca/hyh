# src/hyh/demo.py
"""Interactive demo of hyh features."""

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# ANSI color constants
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
MAGENTA = "\033[0;35m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"  # No Color

SAMPLE_WORKFLOW_JSON = """{
  "tasks": {
    "setup": {
      "id": "setup",
      "description": "Set up project scaffolding",
      "status": "pending",
      "dependencies": [],
      "started_at": null,
      "completed_at": null,
      "claimed_by": null,
      "timeout_seconds": 600,
      "instructions": "Initialize project structure with src/ and tests/ directories",
      "role": null
    },
    "backend": {
      "id": "backend",
      "description": "Implement backend API",
      "status": "pending",
      "dependencies": ["setup"],
      "started_at": null,
      "completed_at": null,
      "claimed_by": null,
      "timeout_seconds": 600,
      "instructions": "Create REST endpoints with JSON responses",
      "role": "backend"
    },
    "frontend": {
      "id": "frontend",
      "description": "Implement frontend UI",
      "status": "pending",
      "dependencies": ["setup"],
      "started_at": null,
      "completed_at": null,
      "claimed_by": null,
      "timeout_seconds": 600,
      "instructions": "Build React components with TypeScript",
      "role": "frontend"
    },
    "integration": {
      "id": "integration",
      "description": "Integration testing",
      "status": "pending",
      "dependencies": ["backend", "frontend"],
      "started_at": null,
      "completed_at": null,
      "claimed_by": null,
      "timeout_seconds": 600,
      "instructions": null,
      "role": null
    },
    "deploy": {
      "id": "deploy",
      "description": "Deploy to production",
      "status": "pending",
      "dependencies": ["integration"],
      "started_at": null,
      "completed_at": null,
      "claimed_by": null,
      "timeout_seconds": 600,
      "instructions": null,
      "role": null
    }
  }
}"""

SAMPLE_LLM_PLAN = """I'll create a plan for building the API:

**Goal:** Build REST API with authentication

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
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
"""


def print_header(title: str) -> None:
    """Print a section header with magenta borders."""
    print()
    print(
        f"{MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}"
    )
    print(f"{BOLD}{MAGENTA}  {title}{NC}")
    print(
        f"{MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}"
    )
    print()


def print_step(text: str) -> None:
    """Print a step indicator with cyan arrow."""
    print(f"{CYAN}▶ {BOLD}{text}{NC}")


def print_info(text: str) -> None:
    """Print dimmed info text, indented."""
    print(f"{DIM}  {text}{NC}")


def print_success(text: str) -> None:
    """Print success message with green checkmark."""
    print(f"{GREEN}✓ {text}{NC}")


def print_command(cmd: str) -> None:
    """Print a command that will be executed."""
    print(f"{YELLOW}  $ {cmd}{NC}")


def print_explanation(text: str) -> None:
    """Print an explanation with info icon."""
    print(f"{BLUE}  \N{INFORMATION SOURCE} {text}{NC}")


def wait_for_user() -> None:
    """Wait for user to press Enter."""
    print()
    print(f"{DIM}  Press Enter to continue...{NC}")
    input()


def run_command(cmd: str) -> None:
    """Print and execute a command, showing indented output."""
    print_command(cmd)
    print()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)  # noqa: S602
    for line in (result.stdout + result.stderr).splitlines():
        print(f"    {line}")
    print()


def cleanup(demo_dir: Path) -> None:
    """Clean up demo environment."""
    print()
    print_step("Cleaning up demo environment...")

    # Shutdown daemon if running
    with contextlib.suppress(Exception):
        subprocess.run(["hyh", "shutdown"], capture_output=True, timeout=5)  # noqa: S607

    # Remove demo directory
    shutil.rmtree(demo_dir, ignore_errors=True)

    print_success("Demo environment cleaned up")
    print()


def step_01_intro() -> None:
    """Show welcome message and project overview."""
    print("\033c", end="")  # Clear screen
    print_header("Welcome to hyh (hold your horses)")

    print(f"  {BOLD}hyh{NC} is a thread-safe state management daemon for dev workflows.")
    print()
    print("  It solves three problems:")
    print()
    print(f"    {GREEN}1.{NC} {BOLD}Task Coordination{NC} - Workers claim/complete tasks from DAG")
    print(f"    {GREEN}2.{NC} {BOLD}Git Safety{NC} - Mutex prevents .git/index corruption")
    print(f"    {GREEN}3.{NC} {BOLD}Crash Recovery{NC} - Atomic writes survive power failures")
    print()
    print(f"  {DIM}Architecture: Dumb client (stdlib only) + Smart daemon (msgspec validation){NC}")
    print(f"  {DIM}Runtime: Python 3.13 free-threaded (true parallelism, no GIL){NC}")

    wait_for_user()


def step_02_setup(demo_dir: Path) -> None:
    """Set up demo environment with git repo and sample workflow."""
    print_header("Step 1: Setting Up the Demo Environment")

    print_step("Creating isolated demo directory")
    print_info("We'll use a temporary directory so we don't touch your real workflows")
    print()

    state_dir = demo_dir / ".claude"
    state_dir.mkdir(parents=True)

    # Initialize git repo
    subprocess.run(["git", "init", "--quiet"], cwd=demo_dir, check=True)  # noqa: S607
    (demo_dir / "README.md").write_text("# Demo\n")
    subprocess.run(["git", "add", "README.md"], cwd=demo_dir, check=True)  # noqa: S607
    subprocess.run(
        ["git", "commit", "-m", "Initial commit", "--quiet"],  # noqa: S607
        cwd=demo_dir,
        check=True,
    )

    print_success(f"Created demo git repo at: {demo_dir}")
    print()

    print_step("Creating a sample workflow with task dependencies")
    print_info("This creates a DAG (Directed Acyclic Graph) of tasks")
    print()

    (state_dir / "dev-workflow-state.json").write_text(SAMPLE_WORKFLOW_JSON)

    # Change to demo directory
    os.chdir(demo_dir)

    print(f"  {BOLD}Task DAG:{NC}")
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

    print_success("Workflow state created")
    print_explanation("Tasks can only run when ALL their dependencies are completed")

    wait_for_user()


def step_03_worker_identity() -> None:
    """Demonstrate worker identity."""
    print_header("Step 2: Worker Identity")

    print_step("Each worker has a stable identity")
    print_info("Worker IDs persist across CLI invocations using atomic writes")
    print()

    run_command("hyh worker-id")

    print_explanation("This ID is used for task ownership (lease renewal)")
    print_explanation("Multiple invocations return the same ID")

    wait_for_user()


def step_04_plan_import(demo_dir: Path) -> None:
    """Demonstrate plan import from LLM output."""
    print_header("Step 3: Importing Plans from LLM Output")

    print_step("LLM orchestrators emit plans in structured Markdown format")
    print_info("The 'plan import' command parses and validates the DAG")
    print()

    # Create sample plan file
    plan_file = demo_dir / "llm-output.md"
    plan_file.write_text(SAMPLE_LLM_PLAN)

    print(f"  {BOLD}Sample LLM output file:{NC}")
    print()
    run_command(f"cat '{plan_file}'")

    print_step("Import the plan")
    print()
    run_command(f"hyh plan import --file '{plan_file}'")

    print_step("View the imported state")
    print()
    jq_filter = (
        ".tasks | to_entries[] | {id: .key, status: .value.status, deps: .value.dependencies}"
    )
    run_command(f"hyh get-state | jq '{jq_filter}'")

    print_explanation("Dependencies are inferred from Task Groups (Group N depends on Group N-1)")
    print_explanation("Task instructions come from the Markdown body under each ### Task header")
    print()

    print_step("Get the plan template (shows format documentation)")
    print()
    run_command("hyh plan template | head -50")

    print_explanation("Use 'plan template' to see the full Markdown format for LLM prompting")

    wait_for_user()


def step_05_basic_commands() -> None:
    """Demonstrate basic daemon commands."""
    print_header("Step 4: Basic Daemon Commands")

    print_step("Ping the daemon")
    print_info("The daemon auto-spawns on first command if not running")
    print()

    run_command("hyh ping")

    print_explanation("The daemon is now running as a background process")
    print_explanation("It listens on a Unix socket for client requests")

    wait_for_user()

    print_step("View the current workflow state")
    print()

    run_command("hyh get-state | jq . | head -40")

    print_explanation("All 3 tasks are 'pending' - none have been claimed yet")
    print_explanation("Only 'setup-db' is claimable (it has no dependencies)")

    wait_for_user()


def step_06_status_dashboard() -> None:
    """Demonstrate status dashboard."""
    print_header("Step 5: Status Dashboard")

    print_step("View workflow status at a glance")
    print_info("The 'status' command provides a real-time dashboard")
    print()

    run_command("hyh status")

    print_explanation("Progress bar shows completion percentage")
    print_explanation("Task table shows status, worker, and blocking dependencies")
    print_explanation("Recent events show what happened and when")

    wait_for_user()

    print_step("Machine-readable output for scripting")
    print()

    run_command("hyh status --json | jq '.summary'")

    print_explanation("Use --json for CI/CD integration")
    print_explanation("Use --watch for live updates (e.g., hyh status --watch 2)")

    wait_for_user()


def step_07_task_workflow() -> None:
    """Demonstrate task claiming and completion."""
    print_header("Step 6: Task Claiming and Completion")

    print_step("Claim the first available task")
    print_info("Each worker gets a unique ID and claims tasks atomically")
    print()

    run_command("hyh task claim")

    print_explanation("We got 'setup-db' - the only task with no dependencies")
    print_explanation("The task is now 'running' and locked to our worker ID")

    wait_for_user()

    print_step("Try to claim again (idempotency)")
    print_info("Claiming again returns the same task - lease renewal pattern")
    print()

    run_command("hyh task claim")

    print_explanation("Same task returned - this is intentional!")
    print_explanation("It renews the lease timestamp, preventing task theft on retries")

    wait_for_user()

    print_step("Complete the setup-db task")
    print()

    run_command("hyh task complete --id setup-db")

    print_success("Task completed!")
    print()

    print_step("What tasks are claimable now?")
    print()

    jq_filter = (
        r"'.tasks as $tasks | $tasks | to_entries[] | .key as $tid | "
        r".value.status as $status | .value.dependencies as $deps | "
        r'(if $status == "pending" and ([$deps[] | $tasks[.].status] | '
        r'all(. == "completed")) then " <- CLAIMABLE" else "" end) as $marker | '
        r'"\($tid): \($status)\($marker)"'
        "'"
    )
    run_command(f"hyh get-state | jq -r {jq_filter}")

    print_explanation("'auth-endpoints' is now claimable (depends on completed 'setup-db')")
    print_explanation("'api-tests' is still blocked (depends on 'auth-endpoints')")

    wait_for_user()

    print_step("Complete the remaining tasks")
    print()

    # Complete remaining tasks programmatically
    for _ in range(2):
        print_command("hyh task claim")
        result = subprocess.run(
            ["hyh", "task", "claim"],  # noqa: S607
            capture_output=True,
            text=True,
        )
        claim_data = json.loads(result.stdout)
        task_id = claim_data.get("task", {}).get("id", "unknown")
        print(f"    Claimed: {task_id}")
        print()

        print_command(f"hyh task complete --id {task_id}")
        subprocess.run(  # noqa: S603
            ["hyh", "task", "complete", "--id", task_id],  # noqa: S607
            capture_output=True,
        )
        print()

    print_success("All tasks completed!")

    wait_for_user()

    print_step("Final state")
    print()

    run_command("hyh get-state | jq -r '.tasks | to_entries[] | \"\\(.key): \\(.value.status)\"'")

    print_explanation("Every task is now 'completed' - workflow finished!")

    wait_for_user()


def step_08_git_mutex() -> None:
    """Demonstrate git operations with mutex."""
    print_header("Step 7: Git Operations with Mutex")

    print_step("The problem: parallel git operations corrupt .git/index")
    print_info("Two workers running 'git add' simultaneously = data loss")
    print()

    print_step("The solution: hyh git -- <command>")
    print_info("All git operations go through a global mutex")
    print()

    # Create a demo file
    Path("demo.txt").write_text("demo content\n")

    run_command("hyh git -- add demo.txt")
    run_command("hyh git -- status")
    run_command("hyh git -- commit -m 'Add demo file'")

    print_explanation("Each git command acquires an exclusive lock")
    print_explanation("Parallel workers block until the lock is free")
    print_explanation("Result: safe git operations, no corruption")

    wait_for_user()


def step_09_hooks(demo_dir: Path) -> None:
    """Demonstrate Claude Code hook integration."""
    print_header("Step 8: Claude Code Hook Integration")

    print_step("hyh provides hooks for Claude Code plugins")
    print_info("Three hooks: session-start, check-state, check-commit")
    print()

    print(f"  {BOLD}1. SessionStart Hook{NC} - Shows workflow progress on session resume")
    print()
    run_command("hyh session-start | jq .")

    print_explanation("This output gets injected into Claude's context at session start")
    print()

    print_step("2. Stop Hook (check-state)")
    print_info("Prevents ending session while workflow is incomplete")
    print()

    # Reset to fresh workflow with pending task
    state_dir = demo_dir / ".claude"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "dev-workflow-state.json").write_text("""{
  "tasks": {
    "incomplete-task": {
      "id": "incomplete-task",
      "description": "This task is not done",
      "status": "pending",
      "dependencies": []
    }
  }
}""")

    print(f"  {DIM}Created workflow with 1 pending task{NC}")
    print()
    run_command("hyh check-state || true")

    print_explanation("Exit code 1 + 'deny' = Claude Code blocks the session end")
    print()

    # Complete the task
    subprocess.run(["hyh", "task", "claim"], capture_output=True)  # noqa: S607
    subprocess.run(
        ["hyh", "task", "complete", "--id", "incomplete-task"],  # noqa: S607
        capture_output=True,
    )
    print(f"  {DIM}Task completed...{NC}")
    print()

    run_command("hyh check-state")

    print_explanation("Exit code 0 + 'allow' = Session can end")
    print()

    print_step("3. SubagentStop Hook (check-commit)")
    print_info("Requires agents to make git commits after work")
    print()

    # Set up last_commit in state
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
    )
    current_head = result.stdout.strip()
    subprocess.run(  # noqa: S603
        ["hyh", "update-state", "--field", "last_commit", current_head],  # noqa: S607
        capture_output=True,
    )

    run_command("hyh check-commit || true")

    print_explanation("If HEAD matches last_commit, agent hasn't committed new work")
    print_explanation("Useful to ensure code changes are persisted")

    wait_for_user()


def step_10_multi_project(demo_dir: Path) -> None:
    """Demonstrate multi-project isolation."""
    print_header("Step 9: Multi-Project Isolation")

    print_step("Each project gets an isolated daemon")
    print_info("Socket paths are hashed from the git worktree root")
    print()

    print_explanation("This demo project has its own daemon socket at:")
    print()

    # Calculate socket hash
    socket_hash = hashlib.sha256(str(demo_dir).encode()).hexdigest()[:12]
    print(f"  {DIM}~/.hyh/sockets/{socket_hash}.sock{NC}")
    print()

    print_step("View all registered projects")
    print()

    run_command("hyh status --all")

    print_explanation("Multiple hyh daemons can run simultaneously")
    print_explanation("Use --project <path> to target a specific project")

    wait_for_user()


def step_11_exec(demo_dir: Path) -> None:
    """Demonstrate command execution and observability."""
    print_header("Step 10: Command Execution and Observability")

    print_step("Execute arbitrary commands")
    print_info("The 'exec' command runs any shell command through the daemon")
    print()

    run_command("hyh exec -- echo 'Hello from hyh!'")
    run_command("hyh exec -- python3 -c 'print(2 + 2)'")

    print_explanation("Commands can optionally acquire the exclusive lock (--exclusive)")
    print_explanation("Useful for operations that need serialization")

    wait_for_user()

    print_step("View the trajectory log")
    print_info("Every operation is logged to .claude/trajectory.jsonl")
    print()

    state_dir = demo_dir / ".claude"
    trajectory_file = state_dir / "trajectory.jsonl"
    if trajectory_file.exists():
        run_command(f"cat '{trajectory_file}' | jq -s '.[0:3]' | head -60")
    else:
        print(f"  {DIM}(No trajectory log yet){NC}")
        print()

    print_explanation("JSONL format: append-only, crash-safe")
    print_explanation("O(1) tail retrieval - reads from end of file")
    print_explanation("Each event has timestamp, duration, reason for debugging")

    wait_for_user()


def step_12_state_update() -> None:
    """Demonstrate direct state updates."""
    print_header("Step 11: Direct State Updates")

    print_step("Update state fields directly")
    print_info("Useful for orchestration metadata")
    print()

    run_command("hyh update-state --field current_phase 'deployment' --field parallel_workers 3")
    run_command("hyh get-state | jq 'del(.tasks)'")

    print_explanation("State updates are atomic and validated by msgspec")
    print_explanation("Unknown fields are allowed for flexibility")

    wait_for_user()


def step_13_architecture() -> None:
    """Show architecture overview."""
    print_header("Step 12: Architecture Overview")

    print(f"  {BOLD}Client-Daemon Split{NC}")
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

    print(f"  {BOLD}Lock Hierarchy (Deadlock Prevention){NC}")
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
    print(f"  {DIM}Release-then-Log Pattern: Release state lock BEFORE logging{NC}")
    print(f"  {DIM}This prevents lock convoy (threads waiting on I/O){NC}")
    print()

    wait_for_user()

    print(f"  {BOLD}Atomic Persistence Pattern{NC}")
    print()
    print("    ┌─────────────────────────────────────────────────────────────┐")
    print("    │  1. Write to state.json.tmp                                 │")
    print("    │  2. fsync() - ensure bytes hit disk                         │")
    print("    │  3. rename(tmp, state.json) - POSIX atomic operation        │")
    print("    └─────────────────────────────────────────────────────────────┘")
    print()
    print(f"  {DIM}If power fails during write: tmp file is corrupt, original intact{NC}")
    print(f"  {DIM}If power fails during rename: atomic, so either old or new state{NC}")
    print()

    wait_for_user()


def step_14_recap() -> None:
    """Show command recap."""
    print_header("Recap: Key Commands")

    print(f"  {BOLD}Daemon Control{NC}")
    print(f"    {YELLOW}hyh ping{NC}              Check if daemon is running")
    print(f"    {YELLOW}hyh shutdown{NC}          Stop the daemon")
    print()
    print(f"  {BOLD}Worker Identity{NC}")
    print(f"    {YELLOW}hyh worker-id{NC}         Print stable worker ID")
    print()
    print(f"  {BOLD}Plan Management{NC}")
    print(f"    {YELLOW}hyh plan import --file{NC}  Import LLM-generated plan")
    print(f"    {YELLOW}hyh plan template{NC}       Show Markdown plan format")
    print(f"    {YELLOW}hyh plan reset{NC}          Clear workflow state")
    print()
    print(f"  {BOLD}Status Dashboard{NC}")
    print(f"    {YELLOW}hyh status{NC}            Show workflow dashboard")
    print(f"    {YELLOW}hyh status --json{NC}     Machine-readable output")
    print(f"    {YELLOW}hyh status --watch{NC}    Auto-refresh mode")
    print(f"    {YELLOW}hyh status --all{NC}      List all projects")
    print()
    print(f"  {BOLD}State Management{NC}")
    print(f"    {YELLOW}hyh get-state{NC}         Get current workflow state")
    print(f"    {YELLOW}hyh update-state{NC}      Update state fields")
    print()
    print(f"  {BOLD}Task Workflow{NC}")
    print(f"    {YELLOW}hyh task claim{NC}        Claim next available task")
    print(f"    {YELLOW}hyh task complete{NC}     Mark task as completed")
    print()
    print(f"  {BOLD}Command Execution{NC}")
    print(f"    {YELLOW}hyh git -- <cmd>{NC}      Git with mutex")
    print(f"    {YELLOW}hyh exec -- <cmd>{NC}     Arbitrary command")
    print()
    print(f"  {BOLD}Hook Integration{NC}")
    print(f"    {YELLOW}hyh session-start{NC}     SessionStart hook output")
    print(f"    {YELLOW}hyh check-state{NC}       Stop hook (deny if incomplete)")
    print(f"    {YELLOW}hyh check-commit{NC}      SubagentStop hook (deny if no commit)")
    print()

    wait_for_user()


def step_15_next_steps() -> None:
    """Show next steps and wrap up."""
    print_header("Next Steps")

    print(f"  {BOLD}1. Explore the codebase{NC}")
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
    print(f"  {BOLD}2. Run the tests{NC}")
    print("     make test                           # All tests (30s timeout)")
    print("     make test-fast                      # No timeout (faster iteration)")
    print("     make check                          # lint + typecheck + test")
    print()
    print(f"  {BOLD}3. Read the architecture docs{NC}")
    print("     docs/plans/                         # Design documents")
    print()
    print(f"  {BOLD}4. Try parallel workers{NC}")
    print("     Open multiple terminals and run 'hyh task claim'")
    print("     Watch them coordinate via the shared state")
    print()

    print_header("Demo Complete!")

    print("  Thanks for taking the tour!")
    print()
    print(f"  {DIM}Demo directory will be cleaned up on exit.{NC}")
    print()


def _run_all_steps(demo_dir: Path) -> None:
    """Run all demo steps."""
    step_01_intro()
    step_02_setup(demo_dir)
    step_03_worker_identity()
    step_04_plan_import(demo_dir)
    step_05_basic_commands()
    step_06_status_dashboard()
    step_07_task_workflow()
    step_08_git_mutex()
    step_09_hooks(demo_dir)
    step_10_multi_project(demo_dir)
    step_11_exec(demo_dir)
    step_12_state_update()
    step_13_architecture()
    step_14_recap()
    step_15_next_steps()


def run() -> None:
    """Run the interactive demo."""
    original_cwd = Path.cwd()
    demo_dir = Path(tempfile.mkdtemp())

    try:
        _run_all_steps(demo_dir)
    finally:
        os.chdir(original_cwd)
        cleanup(demo_dir)
