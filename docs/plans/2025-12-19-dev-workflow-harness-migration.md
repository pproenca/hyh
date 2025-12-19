# Dev-Workflow Plugin → Harness Migration Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-19-dev-workflow-harness-migration.md` to implement task-by-task.

**Goal:** Eliminate all markdown-based state management from dev-workflow plugin, making harness the single source of truth for workflow state.

**Architecture:** Replace `.claude/dev-workflow-state.local.md` frontmatter approach with harness daemon JSON-RPC calls. The plugin already has partial harness integration—this migration completes it.

**Tech Stack:** Bash shell scripts, harness CLI, jq for JSON parsing.

---

## Current State Analysis

### Dual State Management (Broken)

```
session-start.sh
├─ PRIMARY: harness ping + harness get-state ✓
└─ FALLBACK: get_state_file() + frontmatter_get() ✗ REMOVE

abandon.md
├─ get_state_file() ✗
├─ frontmatter_get() ✗
└─ delete_state_file() ✗ MISSING: harness call 🔴 BUG

execute-plan.md
└─ harness_import_plan() + harness_get_progress() ✓ OK

stop.sh
├─ get_state_file() ✗
└─ frontmatter_get() ✗ REMOVE ENTIRE FILE
```

### Functions Inventory

**REMOVE (9 functions):**
- `frontmatter_get()` - lines 10-31
- `frontmatter_set()` - lines 36-57
- `get_state_file()` - lines 63-67
- `create_state_file()` - lines 73-92
- `delete_state_file()` - lines 96-100
- `get_task_content()` - lines 239-264
- `harness_is_workflow_active()` - lines 284-292 (unused, redundant with harness_get_progress)

**KEEP (12 functions):**
- Plan parsing: `get_task_numbers()`, `get_next_task_number()`, `get_task_files()`, `group_tasks_by_dependency()`, `get_max_parallel_from_groups()`
- Harness integration: `harness_import_plan()`, `harness_get_progress()`, `harness_claim_task()`, `harness_complete_task()`

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1 | Critical bug fix (independent) |
| Group 2 | 2, 3 | Hook updates (can parallelize) |
| Group 3 | 4 | Function cleanup (depends on hooks) |
| Group 4 | 5 | Documentation (last) |

---

### Task 1: Fix abandon.md Bug (Add harness reset)

**Effort:** simple (5 tool calls)

**Files:**
- Modify: `~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/commands/abandon.md`

**Problem:** The abandon command only deletes the markdown state file but does NOT clear harness state. Users who abandon then resume see stale harness tasks.

**Step 1: Read the current abandon.md**

```bash
cat ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/commands/abandon.md
```

**Step 2: Replace the bash code block**

The current implementation uses:
```bash
STATE_FILE="$(get_state_file)"
# ... frontmatter operations
delete_state_file
```

Replace with harness-native approach:
```bash
# Check for active workflow in harness
PROGRESS=$(harness_get_progress 2>/dev/null) || {
  echo "No active workflow to abandon."
  exit 0
}

TOTAL=$(echo "$PROGRESS" | jq -r '.total // 0')
if [[ "$TOTAL" -eq 0 ]]; then
  echo "No active workflow to abandon."
  exit 0
fi

# Show current progress
COMPLETED=$(echo "$PROGRESS" | jq -r '.completed // 0')
PENDING=$(echo "$PROGRESS" | jq -r '.pending // 0')
RUNNING=$(echo "$PROGRESS" | jq -r '.running // 0')

echo "Abandoning workflow:"
echo "  - $COMPLETED completed"
echo "  - $RUNNING running"
echo "  - $PENDING pending"
echo ""

# Clear harness state (reset all tasks)
harness plan reset

# Delete legacy state file if present (cleanup)
STATE_FILE="$(git rev-parse --show-toplevel 2>/dev/null)/.claude/dev-workflow-state.local.md"
[[ -f "$STATE_FILE" ]] && rm -f "$STATE_FILE"

echo "Workflow abandoned. Ready for new work."
```

**Step 3: Test the fix**

```bash
# Start a workflow
harness plan import --file test-plan.md

# Verify it exists
harness get-state | jq '.tasks | length'  # Should be > 0

# Run abandon
/dev-workflow:abandon

# Verify cleared
harness get-state | jq '.tasks | length'  # Should be 0
```

**Step 4: Commit**

```bash
git add ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/commands/abandon.md
git commit -m "fix(abandon): add harness reset to clear daemon state"
```

---

### Task 2: Remove Markdown Fallback from session-start.sh

**Effort:** standard (8 tool calls)

**Files:**
- Modify: `~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/hooks/session-start.sh`

**Problem:** Lines 35-60 contain a fallback path that uses the old markdown state file. With harness as the single backend, this fallback creates confusion and dual-path bugs.

**Step 1: Read the current hook**

```bash
cat ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/hooks/session-start.sh
```

**Step 2: Identify the sections**

The hook should have:
- Lines 1-10: Shebang, source helpers
- Lines 12-32: Harness path (KEEP)
- Lines 35-60: Markdown fallback (DELETE)

**Step 3: Remove the fallback block**

Delete the entire section that starts with `# Fallback to markdown state` and ends before the closing.

The final hook should ONLY use harness:

```bash
#!/usr/bin/env bash
# SessionStart hook - Check for active workflow via harness

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../scripts/hook-helpers.sh"

# Check harness daemon
if ! command -v harness &>/dev/null; then
  exit 0  # Harness not installed, no workflow possible
fi

# Check for active workflow
PROGRESS=$(harness_get_progress 2>/dev/null) || exit 0

TOTAL=$(echo "$PROGRESS" | jq -r '.total // 0')
[[ "$TOTAL" -eq 0 ]] && exit 0

# Display workflow status
COMPLETED=$(echo "$PROGRESS" | jq -r '.completed // 0')
PENDING=$(echo "$PROGRESS" | jq -r '.pending // 0')
RUNNING=$(echo "$PROGRESS" | jq -r '.running // 0')

cat << EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "<system-context>\n**ACTIVE WORKFLOW DETECTED**\n\nProgress: $COMPLETED/$TOTAL tasks completed\n- Pending: $PENDING\n- Running: $RUNNING\n\nCommands:\n- /dev-workflow:resume - Continue execution\n- /dev-workflow:abandon - Discard workflow state\n</system-context>"
  }
}
EOF
```

**Step 4: Test the hook**

```bash
# With active workflow
harness plan import --file test-plan.md
~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/hooks/session-start.sh
# Should output JSON with workflow status

# Without workflow
harness update-state --reset
~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/hooks/session-start.sh
# Should output nothing (exit 0)
```

**Step 5: Commit**

```bash
git add ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/hooks/session-start.sh
git commit -m "refactor(session-start): remove markdown state fallback, harness-only"
```

---

### Task 3: Delete or Rewrite stop.sh Hook

**Effort:** simple (3 tool calls)

**Files:**
- Delete: `~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/hooks/stop.sh`
- OR Modify to use harness

**Decision:** DELETE the hook entirely.

**Rationale:**
1. Harness persists state atomically—no risk of data loss on session end
2. Users can check `/dev-workflow:resume` in new session
3. Stop hook adds latency without value

**Step 1: Remove the hook**

```bash
rm ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/hooks/stop.sh
```

**Step 2: Update hooks.md if it references stop.sh**

```bash
grep -l "stop.sh" ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/*.md
# Edit any files that reference it
```

**Step 3: Commit**

```bash
git add -A ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/
git commit -m "refactor(hooks): remove stop.sh, harness handles persistence"
```

---

### Task 4: Clean hook-helpers.sh (Remove 9 Functions)

**Effort:** complex (15 tool calls)

**Files:**
- Modify: `~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/scripts/hook-helpers.sh`

**Step 1: Read the file and identify line ranges**

```bash
grep -n "^frontmatter_get\|^frontmatter_set\|^get_state_file\|^create_state_file\|^delete_state_file\|^get_task_content\|^harness_is_workflow_active" ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/scripts/hook-helpers.sh
```

**Step 2: Remove functions in order (highest line number first to preserve offsets)**

Functions to remove with approximate line ranges:
1. `harness_is_workflow_active()` - ~284-292
2. `get_task_content()` - ~239-264
3. `delete_state_file()` - ~96-100
4. `create_state_file()` - ~73-92
5. `get_state_file()` - ~63-67
6. `frontmatter_set()` - ~36-57
7. `frontmatter_get()` - ~10-31

**Step 3: Update file header comment**

Remove any references to "frontmatter" or "state file" in the header.

**Step 4: Verify remaining functions work**

```bash
# Source the file and test each kept function
source ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/scripts/hook-helpers.sh

# Test plan parsing
get_task_numbers "/path/to/test-plan.md"
group_tasks_by_dependency "/path/to/test-plan.md"

# Test harness functions
harness_get_progress
harness_import_plan "/path/to/test-plan.md"
```

**Step 5: Run existing tests**

```bash
cd ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/tests
bats hook-helpers.bats 2>&1 | head -50
```

Note: Some tests for removed functions will fail. Those test cases should also be removed.

**Step 6: Remove tests for deleted functions**

```bash
# Find tests that reference removed functions
grep -n "frontmatter_get\|frontmatter_set\|get_state_file\|create_state_file\|delete_state_file\|get_task_content\|harness_is_workflow_active" ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/tests/hook-helpers.bats
```

Delete those test blocks.

**Step 7: Run tests again**

```bash
bats hook-helpers.bats
# All remaining tests should pass
```

**Step 8: Commit**

```bash
git add ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/scripts/hook-helpers.sh
git add ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/tests/hook-helpers.bats
git commit -m "refactor(hook-helpers): remove 9 legacy state functions, harness-only"
```

---

### Task 5: Update Documentation (SKILL.md)

**Effort:** standard (6 tool calls)

**Files:**
- Modify: `~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/skills/getting-started/SKILL.md`

**Step 1: Find outdated references**

```bash
grep -n "dev-workflow-state\|frontmatter\|create_state_file\|delete_state_file" ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/skills/getting-started/SKILL.md
```

**Step 2: Update the State Persistence section (lines ~313-352)**

Replace markdown state description with harness description:

```markdown
## State Persistence

Workflow state is managed by the **harness daemon** via Unix socket RPC.

**State Location:** Managed by harness (not a user-editable file)

**View State:**
```bash
harness get-state | jq '.tasks'
```

**Clear State:**
```bash
harness update-state --reset
```

**Persistence Guarantees:**
- Atomic writes (crash-safe)
- DAG-based task dependencies
- Automatic timeout and reclaim for stuck tasks

**Recovery:** If Claude Code crashes mid-workflow, run `/dev-workflow:resume` in a new session. Harness tracks exactly which task was in progress.
```

**Step 3: Update any code examples that show state file**

Replace:
```bash
# OLD
cat .claude/dev-workflow-state.local.md
```

With:
```bash
# NEW
harness get-state | jq '.'
```

**Step 4: Update README.md if needed**

```bash
grep -n "dev-workflow-state\|state.local" ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/README.md
```

**Step 5: Commit**

```bash
git add ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/skills/getting-started/SKILL.md
git add ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/README.md
git commit -m "docs: update state persistence to reflect harness-only backend"
```

---

### Task 6: Code Review

Run code review on all changes:

1. Verify no remaining references to markdown state
2. Test full workflow lifecycle: execute → resume → abandon
3. Ensure harness daemon starts correctly
4. Verify error handling when harness is unavailable

---

## Verification

After all tasks complete, run the full verification:

```bash
# 1. Check no markdown state references remain
grep -r "dev-workflow-state\|frontmatter_get\|frontmatter_set" ~/.claude/plugins/cache/pproenca/dev-workflow/a8af331049d5/
# Should return nothing (or only in changelogs/history)

# 2. Test workflow lifecycle
harness plan import --file test-plan.md
harness get-state | jq '.tasks | length'  # Should be > 0
harness task claim | jq '.task.id'        # Should return task ID
harness task complete --id <task-id>
# Repeat until all done

# 3. Test abandon
harness plan import --file test-plan.md
/dev-workflow:abandon
harness get-state | jq '.tasks | length'  # Should be 0

# 4. Test resume
harness plan import --file test-plan.md
harness task claim  # Start a task
# Simulate crash (new session)
/dev-workflow:resume  # Should detect in-progress task
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Harness daemon not running | `ensure-harness.sh` script spawns daemon |
| Existing workflows with .claude/dev-workflow-state.local.md | Abandon cleans up legacy file |
| External scripts depend on state file | Breaking change, document in CHANGELOG |
| Plugin cache invalidated | Changes apply to new plugin versions only |
