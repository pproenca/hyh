#!/usr/bin/env bash
# ============================================================================
# DEPRECATED: This script has been ported to Python.
# Use `hyh demo` instead for the interactive tour.
# This file is kept for reference only.
# ============================================================================
#
# Hyh Demo Script
# An interactive tour for developers to understand the project
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# Demo state directory (isolated from real workflows)
DEMO_DIR=$(mktemp -d)
DEMO_STATE_DIR="$DEMO_DIR/.claude"

print_header() {
    echo ""
    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${MAGENTA}  $1${NC}"
    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

print_step() {
    echo -e "${CYAN}▶ ${BOLD}$1${NC}"
}

print_info() {
    echo -e "${DIM}  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_command() {
    echo -e "${YELLOW}  \$ $1${NC}"
}

print_output() {
    echo -e "${DIM}$1${NC}"
}

print_explanation() {
    echo -e "${BLUE}  ℹ $1${NC}"
}

wait_for_user() {
    echo ""
    echo -e "${DIM}  Press Enter to continue...${NC}"
    read -r
}

run_command() {
    local cmd="$1"
    print_command "$cmd"
    echo ""
    eval "$cmd" 2>&1 | sed 's/^/    /'
    echo ""
}

cleanup() {
    echo ""
    print_step "Cleaning up demo environment..."

    # Shutdown daemon if running
    if hyh ping >/dev/null 2>&1; then
        hyh shutdown >/dev/null 2>&1 || true
    fi

    # Remove demo directory
    rm -rf "$DEMO_DIR"

    print_success "Demo environment cleaned up"
    echo ""
}

trap cleanup EXIT

# Get the directory where this script lives (project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure jq is installed
if ! command -v jq &> /dev/null; then
    echo -e "${RED}ERROR: jq is required but not installed.${NC}"
    echo -e "${DIM}Install with: brew install jq (macOS) or apt install jq (Linux)${NC}"
    exit 1
fi

# Ensure hyh is installed
if ! command -v hyh &> /dev/null; then
    echo -e "${CYAN}▶ ${BOLD}Installing hyh...${NC}"
    if command -v uv &> /dev/null; then
        uv pip install -e "$SCRIPT_DIR" --quiet
    else
        pip install -e "$SCRIPT_DIR" --quiet
    fi

    # Add .venv/bin to PATH if it exists (uv installs there)
    if [ -d "$SCRIPT_DIR/.venv/bin" ]; then
        export PATH="$SCRIPT_DIR/.venv/bin:$PATH"
    fi

    if ! command -v hyh &> /dev/null; then
        echo -e "${RED}ERROR: Failed to install hyh. Please run: uv pip install -e .${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ hyh installed${NC}"
fi

# ============================================================================
# INTRO
# ============================================================================

clear
print_header "Welcome to Harness"

echo -e "  ${BOLD}Harness${NC} is a thread-safe state management daemon for dev workflows."
echo ""
echo "  It solves three problems:"
echo ""
echo -e "    ${GREEN}1.${NC} ${BOLD}Task Coordination${NC} - Multiple workers claim/complete tasks from a DAG"
echo -e "    ${GREEN}2.${NC} ${BOLD}Git Safety${NC} - Mutex prevents parallel git operations corrupting .git/index"
echo -e "    ${GREEN}3.${NC} ${BOLD}Crash Recovery${NC} - Atomic writes ensure state survives power failures"
echo ""
echo -e "  ${DIM}Architecture: Dumb client (stdlib only) + Smart daemon (msgspec validation)${NC}"
echo -e "  ${DIM}Runtime: Python 3.13t free-threaded (true parallelism, no GIL)${NC}"

wait_for_user

# ============================================================================
# SETUP
# ============================================================================

print_header "Step 1: Setting Up the Demo Environment"

print_step "Creating isolated demo directory"
print_info "We'll use a temporary directory so we don't touch your real workflows"
echo ""

mkdir -p "$DEMO_STATE_DIR"
cd "$DEMO_DIR"
git init --quiet
echo "# Demo" > README.md
git add README.md
git commit -m "Initial commit" --quiet

print_success "Created demo git repo at: $DEMO_DIR"
echo ""

print_step "Creating a sample workflow with task dependencies"
print_info "This creates a DAG (Directed Acyclic Graph) of tasks"
echo ""

cat > "$DEMO_STATE_DIR/dev-workflow-state.json" << 'EOF'
{
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
}
EOF

echo -e "  ${BOLD}Task DAG:${NC}"
echo ""
echo "                    ┌─────────┐"
echo "                    │  setup  │"
echo "                    └────┬────┘"
echo "                         │"
echo "              ┌──────────┴──────────┐"
echo "              ▼                     ▼"
echo "        ┌─────────┐           ┌──────────┐"
echo "        │ backend │           │ frontend │"
echo "        └────┬────┘           └────┬─────┘"
echo "              │                     │"
echo "              └──────────┬──────────┘"
echo "                         ▼"
echo "                  ┌─────────────┐"
echo "                  │ integration │"
echo "                  └──────┬──────┘"
echo "                         │"
echo "                         ▼"
echo "                    ┌────────┐"
echo "                    │ deploy │"
echo "                    └────────┘"
echo ""

print_success "Workflow state created"
print_explanation "Tasks can only run when ALL their dependencies are completed"

wait_for_user

# ============================================================================
# WORKER IDENTITY
# ============================================================================

print_header "Step 2: Worker Identity"

print_step "Each worker has a stable identity"
print_info "Worker IDs persist across CLI invocations using atomic writes"
echo ""

run_command "hyh worker-id"

print_explanation "This ID is used for task ownership (lease renewal)"
print_explanation "Multiple invocations return the same ID"

wait_for_user

# ============================================================================
# PLAN IMPORT
# ============================================================================

print_header "Step 3: Importing Plans from LLM Output"

print_step "LLM orchestrators emit plans in structured Markdown format"
print_info "The 'plan import' command parses and validates the DAG"
echo ""

# Create a sample LLM-style plan file
cat > "$DEMO_DIR/llm-output.md" << 'PLANEOF'
I'll create a plan for building the API:

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
PLANEOF

echo -e "  ${BOLD}Sample LLM output file:${NC}"
echo ""
run_command "cat '$DEMO_DIR/llm-output.md'"

print_step "Import the plan"
echo ""

run_command "hyh plan import --file '$DEMO_DIR/llm-output.md'"

print_step "View the imported state"
echo ""

run_command "hyh get-state | jq '.tasks | to_entries[] | {id: .key, status: .value.status, deps: .value.dependencies}'"

print_explanation "Dependencies are inferred from Task Groups (Group N depends on Group N-1)"
print_explanation "Task instructions come from the Markdown body under each ### Task header"
echo ""

print_step "Get the plan template (shows format documentation)"
echo ""

run_command "hyh plan template | head -50"

print_explanation "Use 'plan template' to see the full Markdown format for LLM prompting"

wait_for_user

# ============================================================================
# BASIC COMMANDS
# ============================================================================

print_header "Step 4: Basic Daemon Commands"

print_step "Ping the daemon"
print_info "The daemon auto-spawns on first command if not running"
echo ""

run_command "hyh ping"

print_explanation "The daemon is now running as a background process"
print_explanation "It listens on a Unix socket for client requests"

wait_for_user

print_step "View the current workflow state"
echo ""

run_command "hyh get-state | jq . | head -40"

print_explanation "All 3 tasks are 'pending' - none have been claimed yet"
print_explanation "Only 'setup-db' is claimable (it has no dependencies)"

wait_for_user

# ============================================================================
# STATUS DASHBOARD
# ============================================================================

print_header "Step 5: Status Dashboard"

print_step "View workflow status at a glance"
print_info "The 'status' command provides a real-time dashboard"
echo ""

run_command "hyh status"

print_explanation "Progress bar shows completion percentage"
print_explanation "Task table shows status, worker, and blocking dependencies"
print_explanation "Recent events show what happened and when"

wait_for_user

print_step "Machine-readable output for scripting"
echo ""

run_command "hyh status --json | jq '.summary'"

print_explanation "Use --json for CI/CD integration"
print_explanation "Use --watch for live updates (e.g., hyh status --watch 2)"

wait_for_user

# ============================================================================
# TASK WORKFLOW
# ============================================================================

print_header "Step 6: Task Claiming and Completion"

print_step "Claim the first available task"
print_info "Each worker gets a unique ID and claims tasks atomically"
echo ""

run_command "hyh task claim"

print_explanation "We got 'setup-db' - the only task with no dependencies"
print_explanation "The task is now 'running' and locked to our worker ID"

wait_for_user

print_step "Try to claim again (idempotency)"
print_info "Claiming again returns the same task - lease renewal pattern"
echo ""

run_command "hyh task claim"

print_explanation "Same task returned - this is intentional!"
print_explanation "It renews the lease timestamp, preventing task theft on retries"

wait_for_user

print_step "Complete the setup-db task"
echo ""

run_command "hyh task complete --id setup-db"

print_success "Task completed!"
echo ""

print_step "What tasks are claimable now?"
echo ""

run_command "hyh get-state | jq -r '
  .tasks as \$tasks |
  \$tasks | to_entries[] |
  .key as \$tid |
  .value.status as \$status |
  .value.dependencies as \$deps |
  (if \$status == \"pending\" and ([\$deps[] | \$tasks[.].status] | all(. == \"completed\")) then \" <- CLAIMABLE\" else \"\" end) as \$marker |
  \"\(\$tid): \(\$status)\(\$marker)\"
'"

print_explanation "'auth-endpoints' is now claimable (depends on completed 'setup-db')"
print_explanation "'api-tests' is still blocked (depends on 'auth-endpoints')"

wait_for_user

print_step "Complete the remaining tasks"
echo ""

# Claim and complete auth-endpoints
echo -e "${YELLOW}  \$ hyh task claim${NC}"
CLAIM_RESULT=$(hyh task claim)
TASK_ID=$(echo "$CLAIM_RESULT" | jq -r '.task.id')
echo "    Claimed: $TASK_ID"
echo ""

echo -e "${YELLOW}  \$ hyh task complete --id $TASK_ID${NC}"
hyh task complete --id "$TASK_ID"
echo ""

# Claim and complete api-tests
echo -e "${YELLOW}  \$ hyh task claim${NC}"
CLAIM_RESULT=$(hyh task claim)
TASK_ID=$(echo "$CLAIM_RESULT" | jq -r '.task.id')
echo "    Claimed: $TASK_ID"
echo ""

echo -e "${YELLOW}  \$ hyh task complete --id $TASK_ID${NC}"
hyh task complete --id "$TASK_ID"
echo ""

print_success "All tasks completed!"

wait_for_user

print_step "Final state"
echo ""

run_command "hyh get-state | jq -r '.tasks | to_entries[] | \"\\(.key): \\(.value.status)\"'"

print_explanation "Every task is now 'completed' - workflow finished!"

wait_for_user

# ============================================================================
# GIT MUTEX
# ============================================================================

print_header "Step 7: Git Operations with Mutex"

print_step "The problem: parallel git operations corrupt .git/index"
print_info "Two workers running 'git add' simultaneously = data loss"
echo ""

print_step "The solution: hyh git -- <command>"
print_info "All git operations go through a global mutex"
echo ""

echo "demo content" > demo.txt

run_command "hyh git -- add demo.txt"
run_command "hyh git -- status"
run_command "hyh git -- commit -m 'Add demo file'"

print_explanation "Each git command acquires an exclusive lock"
print_explanation "Parallel workers block until the lock is free"
print_explanation "Result: safe git operations, no corruption"

wait_for_user

# ============================================================================
# HOOK INTEGRATION
# ============================================================================

print_header "Step 8: Claude Code Hook Integration"

print_step "Harness provides hooks for Claude Code plugins"
print_info "Three hooks: session-start, check-state, check-commit"
echo ""

echo -e "  ${BOLD}1. SessionStart Hook${NC} - Shows workflow progress on session resume"
echo ""
run_command "hyh session-start | jq ."

print_explanation "This output gets injected into Claude's context at session start"
echo ""

print_step "2. Stop Hook (check-state)"
print_info "Prevents ending session while workflow is incomplete"
echo ""

# First, reset to a fresh workflow with pending tasks
cat > "$DEMO_STATE_DIR/dev-workflow-state.json" << 'EOF'
{
  "tasks": {
    "incomplete-task": {
      "id": "incomplete-task",
      "description": "This task is not done",
      "status": "pending",
      "dependencies": []
    }
  }
}
EOF

echo -e "  ${DIM}Created workflow with 1 pending task${NC}"
echo ""
run_command "hyh check-state || true"

print_explanation "Exit code 1 + 'deny' = Claude Code blocks the session end"
echo ""

# Now complete the task
hyh task claim >/dev/null
hyh task complete --id incomplete-task >/dev/null
echo -e "  ${DIM}Task completed...${NC}"
echo ""

run_command "hyh check-state"

print_explanation "Exit code 0 + 'allow' = Session can end"
echo ""

print_step "3. SubagentStop Hook (check-commit)"
print_info "Requires agents to make git commits after work"
echo ""

# Set up last_commit in state
CURRENT_HEAD=$(git rev-parse HEAD)
hyh update-state --field last_commit "$CURRENT_HEAD" >/dev/null

run_command "hyh check-commit || true"

print_explanation "If HEAD matches last_commit, agent hasn't committed new work"
print_explanation "Useful to ensure code changes are persisted"

wait_for_user

# ============================================================================
# MULTI-PROJECT ISOLATION
# ============================================================================

print_header "Step 9: Multi-Project Isolation"

print_step "Each project gets an isolated daemon"
print_info "Socket paths are hashed from the git worktree root"
echo ""

print_explanation "This demo project has its own daemon socket at:"
echo ""
SOCKET_HASH=$(echo -n "$DEMO_DIR" | shasum -a 256 | head -c 12)
echo -e "  ${DIM}~/.hyh/sockets/${SOCKET_HASH}.sock${NC}"
echo ""

print_step "View all registered projects"
echo ""

run_command "hyh status --all"

print_explanation "Multiple hyh daemons can run simultaneously"
print_explanation "Use --project <path> to target a specific project"

wait_for_user

# ============================================================================
# EXEC & TRAJECTORY
# ============================================================================

print_header "Step 10: Command Execution and Observability"

print_step "Execute arbitrary commands"
print_info "The 'exec' command runs any shell command through the daemon"
echo ""

run_command "hyh exec -- echo 'Hello from hyh!'"
run_command "hyh exec -- python3 -c 'print(2 + 2)'"

print_explanation "Commands can optionally acquire the exclusive lock (--exclusive)"
print_explanation "Useful for operations that need serialization"

wait_for_user

print_step "View the trajectory log"
print_info "Every operation is logged to .claude/trajectory.jsonl"
echo ""

run_command "cat '$DEMO_STATE_DIR/trajectory.jsonl' | jq -s '.[0:3]' | head -60"

print_explanation "JSONL format: append-only, crash-safe"
print_explanation "O(1) tail retrieval - reads from end of file"
print_explanation "Each event has timestamp, duration, reason for debugging"

wait_for_user

# ============================================================================
# STATE UPDATE
# ============================================================================

print_header "Step 11: Direct State Updates"

print_step "Update state fields directly"
print_info "Useful for orchestration metadata"
echo ""

run_command "hyh update-state --field current_phase 'deployment' --field parallel_workers 3"
run_command "hyh get-state | jq 'del(.tasks)'"

print_explanation "State updates are atomic and validated by msgspec"
print_explanation "Unknown fields are allowed for flexibility"

wait_for_user

# ============================================================================
# ARCHITECTURE
# ============================================================================

print_header "Step 12: Architecture Overview"

echo -e "  ${BOLD}Client-Daemon Split${NC}"
echo ""
echo "    ┌──────────────────────────────────────────────────────────────────┐"
echo "    │                        CLIENT (client.py)                        │"
echo "    │  • Imports ONLY stdlib (sys, json, socket, argparse)             │"
echo "    │  • <50ms startup time                                            │"
echo "    │  • Zero validation logic                                         │"
echo "    │  • Hash-based socket path for multi-project isolation            │"
echo "    └──────────────────────────────────────────────────────────────────┘"
echo "                                   │"
echo "                           Unix Domain Socket"
echo "                                   │"
echo "                                   ▼"
echo "    ┌──────────────────────────────────────────────────────────────────┐"
echo "    │                        DAEMON (daemon.py)                        │"
echo "    │  • ThreadingMixIn for parallel request handling                  │"
echo "    │  • msgspec validation at the boundary                            │"
echo "    │  • StateManager with thread-safe locking                         │"
echo "    │  • TrajectoryLogger for observability                            │"
echo "    │  • Runtime abstraction (Local or Docker)                         │"
echo "    └──────────────────────────────────────────────────────────────────┘"
echo ""

wait_for_user

echo -e "  ${BOLD}Lock Hierarchy (Deadlock Prevention)${NC}"
echo ""
echo "    Acquire locks in this order ONLY:"
echo ""
echo "    ┌───────────────────────────────────────┐"
echo "    │  1. StateManager._lock     (highest)  │  Protects DAG state"
echo "    ├───────────────────────────────────────┤"
echo "    │  2. TrajectoryLogger._lock            │  Protects event log"
echo "    ├───────────────────────────────────────┤"
echo "    │  3. GLOBAL_EXEC_LOCK       (lowest)   │  Protects git index"
echo "    └───────────────────────────────────────┘"
echo ""
echo -e "  ${DIM}Release-then-Log Pattern: Release state lock BEFORE logging${NC}"
echo -e "  ${DIM}This prevents lock convoy (threads waiting on I/O)${NC}"
echo ""

wait_for_user

echo -e "  ${BOLD}Atomic Persistence Pattern${NC}"
echo ""
echo "    ┌─────────────────────────────────────────────────────────────┐"
echo "    │  1. Write to state.json.tmp                                 │"
echo "    │  2. fsync() - ensure bytes hit disk                         │"
echo "    │  3. rename(tmp, state.json) - POSIX atomic operation        │"
echo "    └─────────────────────────────────────────────────────────────┘"
echo ""
echo -e "  ${DIM}If power fails during write: tmp file is corrupt, original intact${NC}"
echo -e "  ${DIM}If power fails during rename: atomic, so either old or new state${NC}"
echo ""

wait_for_user

# ============================================================================
# RECAP
# ============================================================================

print_header "Recap: Key Commands"

echo -e "  ${BOLD}Daemon Control${NC}"
echo -e "    ${YELLOW}hyh ping${NC}              Check if daemon is running"
echo -e "    ${YELLOW}hyh shutdown${NC}          Stop the daemon"
echo ""
echo -e "  ${BOLD}Worker Identity${NC}"
echo -e "    ${YELLOW}hyh worker-id${NC}         Print stable worker ID"
echo ""
echo -e "  ${BOLD}Plan Management${NC}"
echo -e "    ${YELLOW}hyh plan import --file${NC}  Import LLM-generated plan"
echo -e "    ${YELLOW}hyh plan template${NC}       Show Markdown plan format"
echo -e "    ${YELLOW}hyh plan reset${NC}          Clear workflow state"
echo ""
echo -e "  ${BOLD}Status Dashboard${NC}"
echo -e "    ${YELLOW}hyh status${NC}            Show workflow dashboard"
echo -e "    ${YELLOW}hyh status --json${NC}     Machine-readable output"
echo -e "    ${YELLOW}hyh status --watch${NC}    Auto-refresh mode"
echo -e "    ${YELLOW}hyh status --all${NC}      List all projects"
echo ""
echo -e "  ${BOLD}State Management${NC}"
echo -e "    ${YELLOW}hyh get-state${NC}         Get current workflow state"
echo -e "    ${YELLOW}hyh update-state${NC}      Update state fields"
echo ""
echo -e "  ${BOLD}Task Workflow${NC}"
echo -e "    ${YELLOW}hyh task claim${NC}        Claim next available task"
echo -e "    ${YELLOW}hyh task complete${NC}     Mark task as completed"
echo ""
echo -e "  ${BOLD}Command Execution${NC}"
echo -e "    ${YELLOW}hyh git -- <cmd>${NC}      Git with mutex"
echo -e "    ${YELLOW}hyh exec -- <cmd>${NC}     Arbitrary command"
echo ""
echo -e "  ${BOLD}Hook Integration${NC}"
echo -e "    ${YELLOW}hyh session-start${NC}     SessionStart hook output"
echo -e "    ${YELLOW}hyh check-state${NC}       Stop hook (deny if incomplete)"
echo -e "    ${YELLOW}hyh check-commit${NC}      SubagentStop hook (deny if no commit)"
echo ""

wait_for_user

# ============================================================================
# NEXT STEPS
# ============================================================================

print_header "Next Steps"

echo -e "  ${BOLD}1. Explore the codebase${NC}"
echo "     src/hyh/client.py    - Dumb CLI client"
echo "     src/hyh/daemon.py    - ThreadingMixIn server"
echo "     src/hyh/state.py     - msgspec models + StateManager"
echo "     src/hyh/trajectory.py - JSONL logging"
echo "     src/hyh/runtime.py   - Local/Docker execution"
echo "     src/hyh/plan.py      - Markdown plan parser → WorkflowState"
echo "     src/hyh/git.py       - Git operations via runtime"
echo "     src/hyh/acp.py       - Background event emitter"
echo "     src/hyh/registry.py  - Multi-project registry"
echo ""
echo -e "  ${BOLD}2. Run the tests${NC}"
echo "     make test                           # All tests (30s timeout)"
echo "     make test-fast                      # No timeout (faster iteration)"
echo "     make check                          # lint + typecheck + test"
echo ""
echo -e "  ${BOLD}3. Read the architecture docs${NC}"
echo "     docs/plans/                         # Design documents"
echo ""
echo -e "  ${BOLD}4. Try parallel workers${NC}"
echo "     Open multiple terminals and run 'hyh task claim'"
echo "     Watch them coordinate via the shared state"
echo ""

print_header "Demo Complete!"

echo -e "  Thanks for taking the tour!"
echo ""
echo -e "  ${DIM}Demo directory will be cleaned up on exit.${NC}"
echo ""
