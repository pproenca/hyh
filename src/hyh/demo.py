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


def _run_all_steps(demo_dir: Path) -> None:
    """Run all demo steps. Placeholder for now."""
    pass


def run() -> None:
    """Run the interactive demo."""
    original_cwd = Path.cwd()
    demo_dir = Path(tempfile.mkdtemp())

    try:
        _run_all_steps(demo_dir)
    finally:
        os.chdir(original_cwd)
        cleanup(demo_dir)
