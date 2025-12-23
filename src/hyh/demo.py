# src/hyh/demo.py
"""Interactive demo of hyh features."""

from __future__ import annotations

import contextlib
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
    print(f"  {DIM}Runtime: Python 3.13t free-threaded (true parallelism, no GIL){NC}")

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


def _run_all_steps(demo_dir: Path) -> None:
    """Run all demo steps."""
    step_01_intro()
    step_02_setup(demo_dir)


def run() -> None:
    """Run the interactive demo."""
    original_cwd = Path.cwd()
    demo_dir = Path(tempfile.mkdtemp())

    try:
        _run_all_steps(demo_dir)
    finally:
        os.chdir(original_cwd)
        cleanup(demo_dir)
