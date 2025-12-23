# Design: `hyh demo` Command

Port the interactive demo from `demo.sh` to a native Python command.

## Goal

Provide an educational onboarding experience for new developers to understand hyh's features through an interactive, step-by-step walkthrough.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| UI approach | Plain ANSI colors | Matches bash script, no dependencies, works everywhere |
| Code location | Separate `demo.py` module | Keeps demo content separate from CLI infrastructure |
| Isolation | Temp directory | Zero risk of touching user's real workflow state |
| Scope | All 12 steps | Full parity with bash script |

## Module Structure

```
src/hyh/
├── client.py      # Add demo subparser + _cmd_demo()
└── demo.py        # New file with all demo logic
```

### demo.py Structure

```python
# ANSI color constants
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
MAGENTA = "\033[0;35m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# Output helpers
def print_header(title: str) -> None: ...
def print_step(text: str) -> None: ...
def print_info(text: str) -> None: ...
def print_success(text: str) -> None: ...
def print_command(cmd: str) -> None: ...
def print_explanation(text: str) -> None: ...
def wait_for_user() -> None: ...
def run_command(cmd: str) -> None: ...

# Demo steps
def step_01_setup(demo_dir: Path) -> None: ...
def step_02_worker_identity() -> None: ...
# ... through step_12

# Main entry point
def run() -> None: ...
```

## Client Integration

```python
# In client.py

from hyh import demo

# Add subparser
subparsers.add_parser("demo", help="Interactive tour of hyh features")

# Add case in match statement
case "demo":
    demo.run()
```

## Output Formatting

| Function | Output | Symbol |
|----------|--------|--------|
| `print_header(title)` | Magenta box with borders | ━━━ |
| `print_step(text)` | Cyan bold text | ▶ |
| `print_info(text)` | Dimmed indented text | |
| `print_success(text)` | Green text | ✓ |
| `print_command(cmd)` | Yellow indented | $ |
| `print_explanation(text)` | Blue text | ℹ |
| `wait_for_user()` | Prompt for Enter | |
| `run_command(cmd)` | Execute and show output | |

### run_command Implementation

```python
def run_command(cmd: str) -> None:
    print_command(cmd)
    print()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    for line in (result.stdout + result.stderr).splitlines():
        print(f"    {line}")
    print()
```

## Environment Setup & Cleanup

### Setup

```python
def step_01_setup(demo_dir: Path) -> None:
    state_dir = demo_dir / ".claude"
    state_dir.mkdir(parents=True)

    # Initialize git repo
    subprocess.run(["git", "init", "--quiet"], cwd=demo_dir, check=True)
    (demo_dir / "README.md").write_text("# Demo\n")
    subprocess.run(["git", "add", "README.md"], cwd=demo_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit", "--quiet"],
        cwd=demo_dir, check=True
    )

    # Create sample workflow state
    (state_dir / "dev-workflow-state.json").write_text(SAMPLE_WORKFLOW_JSON)

    os.chdir(demo_dir)
```

### Cleanup

```python
def cleanup(demo_dir: Path) -> None:
    print_step("Cleaning up demo environment...")

    try:
        subprocess.run(["hyh", "shutdown"], capture_output=True, timeout=5)
    except Exception:
        pass

    shutil.rmtree(demo_dir, ignore_errors=True)
    print_success("Demo environment cleaned up")
```

### Signal Handling

```python
def run() -> None:
    original_cwd = os.getcwd()
    demo_dir = Path(tempfile.mkdtemp())
    try:
        # run steps
    finally:
        os.chdir(original_cwd)
        cleanup(demo_dir)
```

## Demo Steps

| Step | Function | Actions |
|------|----------|---------|
| 1 | `step_01_setup()` | Create temp dir, git init, sample workflow, DAG ASCII |
| 2 | `step_02_worker_identity()` | `hyh worker-id`, explain lease renewal |
| 3 | `step_03_plan_import()` | Sample LLM markdown, `hyh plan import` |
| 4 | `step_04_basic_commands()` | `hyh ping`, `hyh get-state` |
| 5 | `step_05_status_dashboard()` | `hyh status`, `hyh status --json` |
| 6 | `step_06_task_workflow()` | Claim/complete tasks, show progression |
| 7 | `step_07_git_mutex()` | `hyh git -- add/commit`, explain locking |
| 8 | `step_08_hooks()` | `session-start`, `check-state`, `check-commit` |
| 9 | `step_09_multi_project()` | Socket path hashing, `hyh status --all` |
| 10 | `step_10_exec()` | `hyh exec`, show trajectory.jsonl |
| 11 | `step_11_state_update()` | `hyh update-state --field` |
| 12 | `step_12_architecture()` | ASCII diagrams |

## Testing

```python
# tests/hyh/test_demo.py

def test_demo_output_helpers():
    """Test formatting functions produce expected ANSI output."""

def test_demo_cleanup_on_interrupt():
    """Verify temp directory is cleaned up on KeyboardInterrupt."""

def test_demo_runs_in_isolation():
    """Verify demo doesn't touch cwd's .claude/ directory."""
```

**Tested:** Output helpers, cleanup behavior, isolation guarantees.

**Not tested:** Full e2e run (interactive), step content (static strings).

## Files Changed

| File | Change |
|------|--------|
| `src/hyh/demo.py` | New - all demo logic |
| `src/hyh/client.py` | Add demo subparser + case |
| `tests/hyh/test_demo.py` | New - unit tests |
| `demo.sh` | Add deprecation note |

## Out of Scope

- Modular sections (`--section basics`)
- Non-interactive mode
- Rich/Textual TUI
