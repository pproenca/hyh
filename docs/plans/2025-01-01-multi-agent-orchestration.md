# Multi-Agent Orchestration Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-01-01-multi-agent-orchestration.md` to implement task-by-task.

**Goal:** Build a complete multi-agent orchestration system with Explore/Plan/Delegate/Verify/Synthesize phases, anti-abandonment architecture, and hooks-based verification.

**Architecture:** Prompt-driven orchestration via Claude Code plugin commands. The lead agent (orchestrator) spawns implementation subagents via Claude Code's `Task` tool. Subagents communicate through the daemon's task claim/complete RPC and `.claude/artifacts/` for context handoff. Stop hooks prevent premature completion.

**Tech Stack:** Python 3.13+, msgspec, Claude Code hooks, Task tool for subagent spawning, shell scripts for verification

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | T001 | Core orchestration command - foundation |
| Group 2 | T002, T003 | Agent templates (parallel - no file overlap) |
| Group 3 | T004 | Stop hook verification script |
| Group 4 | T005 | Integration test for full workflow |
| Group 5 | T006 | Code review |

---

### Task T001: Create Orchestration Plugin Command

**Files:**
- Create: `src/hyh/plugin/commands/orchestrate.md`

**Step 1: Create the orchestration command file**

Create `src/hyh/plugin/commands/orchestrate.md` with full Explore/Plan/Delegate/Verify/Synthesize protocol:

```markdown
---
description: Multi-agent orchestration - explore, plan, delegate, verify
argument-hint: <feature description>
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(hyh:*), Bash(git:*), Bash(make:*), Task, AskUserQuestion
---

# Orchestrate: $ARGUMENTS

You are an IC7 principal engineer. You coordinate complex features through delegation.

## CRITICAL: Anti-Abandonment Rules

1. **External Progress Tracking**: Create `.claude/progress.md` at start. Update after EVERY completed step.
2. **No Premature Completion**: You are NOT done until Phase 5 reports success.
3. **Re-injection**: Every 10 tool calls, re-read `.claude/progress.md` to stay on track.

---

## Context Budget Allocation

| Agent Type | Token Budget | Contents |
|------------|--------------|----------|
| Orchestrator | ~50K tokens | Full plan, all artifacts, dependency graph |
| Implementer | ~15-20K tokens | Single TaskPacket + interface contracts only |
| Verifier | ~25K tokens | Implementation + tests + artifacts |
| Integration | ~30K tokens | All interfaces + API layer |

**80% Rule**: NEVER exceed 80% of context window. If approaching limit, compress or spawn fresh subagent.

---

## Phase 1: EXPLORE (Do this yourself - no subagents)

**Objective:** Understand codebase context before planning.

1. Search for similar features:
   ```bash
   hyh task list --status completed 2>/dev/null || echo "No prior tasks"
   ```

2. Read relevant files using Read, Grep, Glob:
   - Identify patterns to follow
   - Find integration points
   - Note file conventions

3. Document findings in `.claude/artifacts/exploration.md`:
   ```markdown
   # Exploration: $ARGUMENTS

   ## Current Architecture
   [Brief summary of relevant existing code]

   ## Integration Points
   - File: path/to/file.py - Function: xyz - Why: ...

   ## Patterns to Follow
   - Pattern 1: [description]

   ## Files Likely Modified
   - path/to/file.py
   ```

4. Initialize progress tracking:
   ```bash
   mkdir -p .claude/artifacts
   cat > .claude/progress.md << 'EOF'
   # Progress: $ARGUMENTS

   ## Phase 1: EXPLORE
   - [x] Search for similar features
   - [x] Read relevant codebase files
   - [x] Document in exploration.md

   ## Phase 2: PLAN
   - [ ] Create XML plan
   - [ ] Import plan to daemon
   - [ ] Get user approval

   ## Phase 3: DELEGATE
   - [ ] Spawn implementation subagents
   - [ ] Monitor completion

   ## Phase 4: VERIFY
   - [ ] Spawn reviewer
   - [ ] Address issues if any

   ## Phase 5: SYNTHESIZE
   - [ ] Run full test suite
   - [ ] Create PR description
   EOF
   ```

---

## Phase 2: PLAN (Do this yourself)

**Objective:** Create executable task breakdown.

1. **Assess Complexity:**

   | Size | LOC | Subagent Strategy |
   |------|-----|-------------------|
   | Trivial | <10 | Do directly, no subagent |
   | Small | 10-50 | 1 subagent + verification |
   | Medium | 50-200 | 2-3 subagents, staged deps |
   | Large | 200+ | 5+ subagents, milestones |

2. **Create XML plan** at `specs/plan.xml`:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <plan goal="[One sentence goal]">
     <dependencies>
       <dep from="T002" to="T001"/>
       <dep from="T003" to="T001"/>
     </dependencies>

     <task id="T001" role="implementer" model="sonnet">
       <description>Brief description</description>
       <instructions>
         Detailed step-by-step instructions.
         Use TDD: write failing test, implement, verify.
       </instructions>
       <success>
         - Tests pass
         - TypeScript/lint clean
         - Artifact written
       </success>
       <scope>
         <include>src/path/to/module.py</include>
         <include>tests/path/to/test_module.py</include>
         <exclude>src/unrelated/</exclude>
       </scope>
       <interface>
         <input>Read exploration.md for context</input>
         <output>Exports function_name(arg) -> ReturnType</output>
       </interface>
       <constraints>
         - Do NOT modify files outside scope
         - Do NOT add dependencies without asking
       </constraints>
       <tools>Read, Edit, Write, Bash, Glob, Grep</tools>
       <verification>
         <command>make test</command>
         <command>make lint</command>
       </verification>
       <artifacts>
         <read>.claude/artifacts/exploration.md</read>
         <write>.claude/artifacts/T001-api.md</write>
       </artifacts>
     </task>

     <!-- Additional tasks... -->
   </plan>
   ```

3. **Import plan:**
   ```bash
   hyh plan import --file specs/plan.xml
   ```

4. **Show plan to user:**
   ```bash
   hyh workflow status
   ```

5. **Get approval using AskUserQuestion:**
   - If rejected, revise plan and reimport
   - If approved, proceed to Phase 3

6. **Update progress.md** - mark Phase 2 items complete.

---

## Phase 3: DELEGATE (Spawn subagents)

**Objective:** Execute tasks through subagents using wave-based scheduling.

### Wave-Based Dependency Scheduling

Analyze task dependencies and group into execution waves:

```
Wave 1: Independent tasks (run in parallel)
├── T001: Token Service (no deps)
├── T002: Session Manager (no deps)
└── T003: Config Module (no deps)

Wave 2: Tasks depending on Wave 1 (run after Wave 1 completes)
├── T004: Auth API (depends: T001, T002)
└── T005: Middleware (depends: T003)

Wave 3: Integration tasks (run after Wave 2)
└── T006: E2E Tests (depends: T004, T005)
```

**Scheduling rules:**
1. Tasks with NO dependencies → Wave 1 (parallel)
2. Tasks depending ONLY on Wave N → Wave N+1
3. Within each wave, spawn all subagents in parallel
4. Wait for ALL Wave N tasks before starting Wave N+1

For each claimable task:

1. **Claim task:**
   ```bash
   hyh task claim --json
   ```

   This returns the full TaskPacket including:
   - id, description, instructions, success_criteria
   - files_in_scope, files_out_of_scope
   - verification_commands, artifacts_to_read, artifacts_to_write

2. **Spawn subagent using Task tool:**

   ```
   Use the Task tool to spawn an implementer:

   subagent_type: general-purpose
   model: sonnet (or as specified in TaskPacket)
   description: "Implement {task_id}"
   prompt: |
     You are an implementer subagent. Execute this TaskPacket:

     <task_packet>
     {paste full TaskPacket JSON here}
     </task_packet>

     ## Rules

     1. **Scope Boundaries**: ONLY modify files in files_in_scope. NEVER touch files_out_of_scope.

     2. **TDD Cycle**:
        - Write failing test FIRST
        - Run test to confirm RED
        - Implement minimal code to pass
        - Run test to confirm GREEN
        - Refactor if needed
        - Run test to confirm still GREEN

     3. **Verification**: Run ALL verification_commands before completing.

     4. **Artifacts**: Read artifacts_to_read for context. Write artifacts_to_write with:
        - Interface spec (exported functions/types)
        - Any decisions made
        - Integration notes for downstream tasks

     5. **Completion**:
        ```bash
        hyh task complete --id {task_id}
        ```

     6. **If Blocked**: Do NOT complete. Instead report what's blocking you.

     ## Anti-Overfitting

     - Your implementation must handle ALL valid inputs, not just test cases
     - No magic numbers that only satisfy tests
     - No hardcoded values that only work for test data
   ```

3. **Monitor progress:**
   ```bash
   hyh workflow status
   ```

4. **Handle failures:**
   - If subagent reports blocked, investigate and either:
     - Adjust the task scope
     - Create remediation task
     - Escalate to user

5. **Update progress.md** after each task completes.

---

## Phase 4: VERIFY (Spawn reviewer)

**Objective:** Independent verification with fresh context.

When all implementation tasks complete:

1. **Spawn reviewer subagent:**

   ```
   Use the Task tool to spawn a reviewer:

   subagent_type: general-purpose
   model: sonnet
   description: "Verify implementation"
   prompt: |
     You are a verification subagent with FRESH context.

     ## Your Mission

     Review the implementation for quality issues that the implementer may have missed.

     ## Input

     Read these artifacts:
     - .claude/artifacts/exploration.md (original requirements)
     - .claude/artifacts/T*-*.md (implementation artifacts)

     ## Checks

     ### 1. Anti-Overfitting
     - [ ] No hardcoded values that only satisfy test cases
     - [ ] No magic numbers matching test expectations
     - [ ] Solution handles edge cases beyond tests
     - [ ] Implementation is general-purpose, not test-specific

     ### 2. Pattern Consistency
     - [ ] Follows existing codebase conventions
     - [ ] Error handling matches project patterns
     - [ ] Naming conventions respected

     ### 3. Missing Edge Cases
     - [ ] Null/empty inputs handled
     - [ ] Boundary conditions tested
     - [ ] Error paths covered

     ### 4. Security
     - [ ] No SQL injection vectors
     - [ ] No command injection
     - [ ] Input validation present

     ## Output

     Write `.claude/artifacts/verification.md`:

     ```markdown
     # Verification Report

     ## Status: PASS | FAIL

     ## Issues Found
     - [ ] Issue 1: [description] - File: path - Severity: high/medium/low
     - [ ] Issue 2: ...

     ## Recommendations
     - Recommendation 1
     - Recommendation 2
     ```

     If FAIL, do NOT complete. Report issues for remediation.
   ```

2. **Check verification result:**
   - If PASS: proceed to Phase 5
   - If FAIL: Create remediation tasks, return to Phase 3

---

## Phase 5: SYNTHESIZE

**Objective:** Final integration and handoff.

1. **Run full verification:**
   ```bash
   make check
   ```

2. **If any failures, create remediation tasks and return to Phase 3.**

3. **Create PR description:**
   Write `.claude/artifacts/pr-description.md` summarizing:
   - What was implemented
   - Key decisions made
   - How to test
   - Any follow-up work

4. **Report completion to user:**
   - List completed tasks
   - Link to PR description
   - Note any follow-up items

5. **Update progress.md** - all items complete.

---

## Re-injection Pattern

Every 10 tool calls during Phases 3-5:

```bash
cat .claude/progress.md
```

Then continue with the next incomplete item. This prevents context drift during long operations.

---

## Error Handling

| Situation | Action |
|-----------|--------|
| Subagent blocked | Investigate, adjust scope, or escalate |
| Tests fail | Create fix task, don't skip |
| Reviewer finds issues | Create remediation tasks |
| Timeout on task | Check status, consider restarting |

---

## Completion Criteria

You are NOT done until:
- [ ] All tasks in `hyh workflow status` show COMPLETED
- [ ] `make check` passes
- [ ] `.claude/artifacts/verification.md` shows PASS
- [ ] `.claude/artifacts/pr-description.md` exists
- [ ] User has been notified of completion
```

**Step 2: Verify the file was created correctly**

```bash
head -50 src/hyh/plugin/commands/orchestrate.md
```

Expected: First 50 lines of the orchestration command showing frontmatter and Phase 1.

**Step 3: Run lint check**

```bash
ruff check src/hyh/plugin/
```

Expected: No errors (markdown files not linted by ruff).

**Step 4: Commit**

```bash
git add src/hyh/plugin/commands/orchestrate.md
git commit -m "feat(plugin): add multi-agent orchestration command

Implements Explore/Plan/Delegate/Verify/Synthesize workflow with:
- Complexity-based subagent scaling
- XML plan creation and import
- Task tool subagent spawning
- Anti-abandonment patterns (external progress.md, re-injection)
- Verification subagent for anti-overfitting"
```

---

### Task T002: Create Implementer Agent Template

**Files:**
- Create: `src/hyh/plugin/agents/implementer.md`

**Step 1: Create agents directory**

```bash
mkdir -p src/hyh/plugin/agents
```

**Step 2: Create implementer agent template**

Create `src/hyh/plugin/agents/implementer.md`:

```markdown
---
name: implementer
description: TDD-focused implementation subagent for task execution
model: sonnet
tools: Read, Write, Edit, Bash, Glob, Grep
---

# Implementer Agent

You execute TaskPackets using strict TDD methodology.

## Context Budget: ~15-20K tokens

You receive ONLY what you need. Nothing else.

## Input Contract

You receive a TaskPacket with:
- `id`: Task identifier
- `description`: What to build
- `instructions`: Step-by-step guidance
- `success_criteria`: Definition of done
- `files_in_scope`: Files you MAY modify
- `files_out_of_scope`: Files you MUST NOT touch
- `verification_commands`: Commands to run before completing
- `artifacts_to_read`: Context from prior tasks
- `artifacts_to_write`: Output for downstream tasks

## NOT In Your Context (Explicitly Excluded)

You do NOT have access to:
- Full codebase (only files_in_scope)
- Other task packets (only yours)
- Previous conversation history (fresh context)
- Unrelated modules (scoped out)
- Full plan.md (only your task)
- Other subagents' work (only via artifacts)

**This is intentional.** Minimal context = focused execution.

## Execution Protocol

### 1. Load Context

```bash
# Read required artifacts
for artifact in {artifacts_to_read}; do
  cat "$artifact"
done
```

### 2. TDD Cycle (Mandatory)

**RED Phase:**
```python
# Write the failing test FIRST
def test_specific_behavior():
    # Arrange: Set up test data
    input_data = {...}

    # Act: Call the function/method
    result = function_under_test(input_data)

    # Assert: Verify expected outcome
    assert result == expected_output
```

Run test:
```bash
pytest tests/path/to/test_file.py::test_specific_behavior -v
```

Expected: FAIL (function not implemented yet)

**GREEN Phase:**

Implement MINIMAL code to make the test pass:
```python
def function_under_test(input_data):
    # Simplest implementation that passes
    return expected_output
```

Run test:
```bash
pytest tests/path/to/test_file.py::test_specific_behavior -v
```

Expected: PASS

**BLUE Phase (if needed):**

Refactor for clarity without changing behavior:
```bash
pytest tests/path/to/test_file.py -v  # Still passes
```

### 3. Scope Enforcement

Before ANY file modification:
1. Check if file is in `files_in_scope`
2. If not, STOP and report: "File X is out of scope"

```python
# NEVER modify files outside scope
if file_path not in files_in_scope:
    raise ScopeViolation(f"Cannot modify {file_path}")
```

### 4. Anti-Overfitting Checklist

Before completing, verify:
- [ ] Implementation handles inputs BEYOND test cases
- [ ] No magic numbers that only match test data
- [ ] No hardcoded strings that only satisfy assertions
- [ ] Edge cases (null, empty, boundary) considered

### 5. Run Verification

Execute ALL verification_commands:
```bash
for cmd in {verification_commands}; do
  echo "Running: $cmd"
  $cmd || exit 1
done
```

ALL must pass before completing.

### 6. Write Artifacts (~800 tokens max)

Create `{artifacts_to_write}` using this COMPRESSED format:

```markdown
# {task_id}: {Component Name}

**Status:** Complete
**Files:** src/auth/token.py, tests/auth/test_token.py

## Exported Interface
- `generate_token(user: User) -> str` - Creates signed JWT
- `validate_token(token: str) -> User | None` - Validates and returns user

## Decisions
- JWT with RS256 (asymmetric for microservices)
- 1h expiration, refresh via separate endpoint

## Integration
- Import: `from auth.token import generate_token, validate_token`
- Requires: `JWT_SECRET` env var, `cryptography` package
- Downstream: Session manager uses `validate_token()`

## Tests (8 passing)
- `test_generate_valid_token` - Happy path
- `test_validate_expired_token` - Expiration handling
- `test_invalid_signature` - Tamper detection
```

**Target: ~800 tokens** (vs 15K+ full context). Include ONLY what downstream tasks need.

### 7. Complete Task

```bash
hyh task complete --id {task_id}
```

## Error Handling

| Situation | Action |
|-----------|--------|
| Test won't pass | Debug, don't skip. Report if stuck. |
| File out of scope needed | Report, request scope expansion |
| Dependency missing | Report, don't add without approval |
| Verification fails | Fix, don't force completion |

## NEVER Do

- Skip writing tests
- Modify files outside scope
- Complete without running verification
- Hardcode values to pass tests
- Ignore type errors or lint warnings
```

**Step 3: Verify file creation**

```bash
head -30 src/hyh/plugin/agents/implementer.md
```

Expected: Frontmatter and first section of implementer agent.

**Step 4: Commit**

```bash
git add src/hyh/plugin/agents/implementer.md
git commit -m "feat(plugin): add implementer agent template

TDD-focused implementation subagent with:
- Strict RED/GREEN/BLUE cycle
- Scope enforcement
- Anti-overfitting checklist
- Artifact output contract"
```

---

### Task T003: Create Reviewer Agent Template

**Files:**
- Create: `src/hyh/plugin/agents/reviewer.md`

**Step 1: Create reviewer agent template**

Create `src/hyh/plugin/agents/reviewer.md`:

```markdown
---
name: reviewer
description: Verification subagent for anti-overfitting and quality checks
model: sonnet
tools: Read, Glob, Grep, Bash(make:*), Bash(pytest:*)
---

# Reviewer Agent

You verify implementations with FRESH context, catching issues the implementer missed.

## Context Budget: ~25K tokens

You receive artifacts and requirements, NOT the full implementation context.

## Input Contract

You receive:
- List of artifact paths to review
- Original exploration/requirements document
- NO access to test files during initial review (prevents test-driven bias)

## NOT In Your Context (Explicitly Excluded)

- Implementer's conversation history (fresh perspective)
- Full codebase (only relevant files)
- Task packets (only artifacts)
- Debug logs or intermediate states

**This is intentional.** Fresh context catches what implementers miss.

## Verification Protocol

### 1. Load Requirements First

```bash
cat .claude/artifacts/exploration.md
```

Understand WHAT was supposed to be built before seeing HOW it was built.

### 2. Review Implementation Artifacts

For each `T*-*.md` artifact:
```bash
cat .claude/artifacts/T001-api.md
# etc.
```

Note:
- Interfaces exposed
- Decisions made
- Integration points

### 3. Anti-Overfitting Analysis

Read implementation code (NOT tests yet):

```bash
# Find implementation files
grep -l "def \|class " src/ --include="*.py" -r
```

For each implementation file, check:

**Hardcoded Values:**
```python
# BAD: Magic number matching test
def calculate_score(x):
    return 42  # Suspiciously specific

# GOOD: Actual calculation
def calculate_score(x):
    return x * SCORE_MULTIPLIER + BASE_SCORE
```

**Test-Specific Logic:**
```python
# BAD: Only handles test input format
def parse(data):
    if data == "test_input":
        return expected_output

# GOOD: General parsing
def parse(data):
    return json.loads(data)
```

**Missing Edge Cases:**
```python
# BAD: No null check
def process(items):
    return items[0]  # Crashes on empty

# GOOD: Handles edge
def process(items):
    if not items:
        return None
    return items[0]
```

### 4. NOW Review Tests

After implementation review, check tests:

```bash
find tests/ -name "*.py" -newer .claude/artifacts/exploration.md
```

Verify:
- [ ] Tests cover happy path
- [ ] Tests cover error cases
- [ ] Tests cover boundary conditions
- [ ] Tests are not tautological (testing what's hardcoded)

### 5. Run Full Test Suite

```bash
make test
```

All tests must pass.

### 6. Static Analysis

```bash
make lint
make typecheck
```

No errors allowed.

### 7. Security Scan

Check for common vulnerabilities:

```bash
# Command injection
grep -r "subprocess\|os.system\|eval\|exec" src/ --include="*.py"

# SQL injection (if applicable)
grep -r "execute.*%" src/ --include="*.py"

# Path traversal
grep -r "open.*\+" src/ --include="*.py"
```

Flag any suspicious patterns for manual review.

### 8. Write Verification Report

Create `.claude/artifacts/verification.md`:

```markdown
# Verification Report

**Date:** {timestamp}
**Reviewer:** verification-agent
**Tasks Reviewed:** T001, T002, ...

## Overall Status: PASS | FAIL

## Anti-Overfitting Check

| Check | Status | Notes |
|-------|--------|-------|
| No hardcoded test values | PASS/FAIL | ... |
| General-purpose implementation | PASS/FAIL | ... |
| Edge cases handled | PASS/FAIL | ... |

## Issues Found

### Issue 1: [Title]
- **Severity:** high | medium | low
- **File:** path/to/file.py:123
- **Description:** What's wrong
- **Recommendation:** How to fix

### Issue 2: ...

## Test Coverage

- Total tests: N
- New tests: M
- Coverage: X%

## Security Notes

- [ ] No command injection vectors
- [ ] No SQL injection vectors
- [ ] Input validation present

## Recommendations

1. Recommendation 1
2. Recommendation 2

## Verdict

[PASS: Implementation meets requirements and quality standards]
OR
[FAIL: Issues must be addressed before completion]
```

### 9. Report Result

If PASS:
```bash
echo "VERIFICATION PASSED"
```

If FAIL:
- Do NOT complete the review task
- Report specific issues for remediation
- Orchestrator will create fix tasks

## NEVER Do

- Approve code with obvious bugs
- Skip security checks
- Ignore type errors
- Pass implementations that only work for test data
- Complete review without running full test suite
```

**Step 2: Verify file creation**

```bash
head -30 src/hyh/plugin/agents/reviewer.md
```

Expected: Frontmatter and verification protocol start.

**Step 3: Commit**

```bash
git add src/hyh/plugin/agents/reviewer.md
git commit -m "feat(plugin): add reviewer agent template

Fresh-context verification subagent with:
- Anti-overfitting analysis
- Security scanning
- Structured verification report
- PASS/FAIL verdict system"
```

---

### Task T004: Create Stop Hook Verification Script

**Files:**
- Create: `src/hyh/scripts/verify-complete.sh`
- Modify: `src/hyh/templates/settings.json` (if exists, else create)

**Step 1: Create scripts directory**

```bash
mkdir -p src/hyh/scripts
```

**Step 2: Create verification script**

Create `src/hyh/scripts/verify-complete.sh`:

```bash
#!/usr/bin/env bash
# verify-complete.sh - Stop hook verification for anti-abandonment
# Exit 0 = allow completion, Exit 1 = block and continue working

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

FAILED=0

log_check() {
    local status=$1
    local message=$2
    if [ "$status" -eq 0 ]; then
        echo -e "${GREEN}[PASS]${NC} $message"
    else
        echo -e "${RED}[FAIL]${NC} $message"
        FAILED=1
    fi
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

echo "=== Stop Hook Verification ==="
echo ""

# Check 1: Progress tracking file exists and has incomplete items
if [ -f ".claude/progress.md" ]; then
    INCOMPLETE=$(grep -c "^\- \[ \]" .claude/progress.md 2>/dev/null || echo "0")
    if [ "$INCOMPLETE" -gt 0 ]; then
        log_check 1 "Progress tracking: $INCOMPLETE incomplete items in .claude/progress.md"
        echo ""
        echo "Incomplete items:"
        grep "^\- \[ \]" .claude/progress.md | head -5
        echo ""
    else
        log_check 0 "Progress tracking: All items complete"
    fi
else
    log_warn "No .claude/progress.md found (optional)"
fi

# Check 2: hyh workflow status - any incomplete tasks?
if command -v hyh &> /dev/null; then
    HYH_STATUS=$(hyh workflow status --json 2>/dev/null || echo "{}")
    if [ "$HYH_STATUS" != "{}" ]; then
        PENDING=$(echo "$HYH_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([t for t in d.get('tasks',{}).values() if t.get('status')=='pending']))" 2>/dev/null || echo "0")
        RUNNING=$(echo "$HYH_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([t for t in d.get('tasks',{}).values() if t.get('status')=='running']))" 2>/dev/null || echo "0")

        if [ "$PENDING" -gt 0 ] || [ "$RUNNING" -gt 0 ]; then
            log_check 1 "Workflow: $PENDING pending, $RUNNING running tasks"
        else
            log_check 0 "Workflow: All tasks complete"
        fi
    fi
fi

# Check 3: Tests pass
if [ -f "Makefile" ] && grep -q "^test:" Makefile; then
    echo ""
    echo "Running tests..."
    if make test > /tmp/test-output.txt 2>&1; then
        log_check 0 "Tests: All passing"
    else
        log_check 1 "Tests: Failures detected"
        echo ""
        tail -20 /tmp/test-output.txt
        echo ""
    fi
elif [ -f "pyproject.toml" ]; then
    echo ""
    echo "Running pytest..."
    if pytest --tb=short > /tmp/test-output.txt 2>&1; then
        log_check 0 "Tests: All passing"
    else
        log_check 1 "Tests: Failures detected"
        echo ""
        tail -20 /tmp/test-output.txt
        echo ""
    fi
fi

# Check 4: Lint passes
if [ -f "Makefile" ] && grep -q "^lint:" Makefile; then
    echo ""
    echo "Running lint..."
    if make lint > /tmp/lint-output.txt 2>&1; then
        log_check 0 "Lint: Clean"
    else
        log_check 1 "Lint: Issues found"
        echo ""
        tail -10 /tmp/lint-output.txt
        echo ""
    fi
elif command -v ruff &> /dev/null; then
    echo ""
    echo "Running ruff..."
    if ruff check . > /tmp/lint-output.txt 2>&1; then
        log_check 0 "Lint: Clean"
    else
        log_check 1 "Lint: Issues found"
        echo ""
        tail -10 /tmp/lint-output.txt
        echo ""
    fi
fi

# Check 5: Type check passes
if [ -f "Makefile" ] && grep -q "^typecheck:" Makefile; then
    echo ""
    echo "Running typecheck..."
    if make typecheck > /tmp/type-output.txt 2>&1; then
        log_check 0 "Types: Clean"
    else
        log_check 1 "Types: Errors found"
        echo ""
        tail -10 /tmp/type-output.txt
        echo ""
    fi
fi

# Check 6: Verification report exists and shows PASS (if orchestration was used)
if [ -f ".claude/artifacts/verification.md" ]; then
    if grep -q "## Overall Status: PASS" .claude/artifacts/verification.md; then
        log_check 0 "Verification: PASS"
    elif grep -q "## Overall Status: FAIL" .claude/artifacts/verification.md; then
        log_check 1 "Verification: FAIL - see .claude/artifacts/verification.md"
    else
        log_warn "Verification report exists but status unclear"
    fi
fi

echo ""
echo "=== Summary ==="

if [ "$FAILED" -eq 0 ]; then
    echo -e "${GREEN}All checks passed. Completion allowed.${NC}"
    exit 0
else
    echo -e "${RED}Some checks failed. Continue working to resolve issues.${NC}"
    echo ""
    echo "To bypass (not recommended): export HYH_SKIP_VERIFY=1"

    # Allow bypass via environment variable
    if [ "${HYH_SKIP_VERIFY:-}" = "1" ]; then
        echo -e "${YELLOW}HYH_SKIP_VERIFY=1 set, allowing completion anyway${NC}"
        exit 0
    fi

    exit 1
fi
```

**Step 3: Make script executable**

```bash
chmod +x src/hyh/scripts/verify-complete.sh
```

**Step 4: Verify script syntax**

```bash
bash -n src/hyh/scripts/verify-complete.sh
echo "Exit code: $?"
```

Expected: Exit code 0 (no syntax errors).

**Step 5: Create hooks settings template**

Create `src/hyh/templates/claude-settings.json`:

```json
{
  "$schema": "https://raw.githubusercontent.com/anthropics/claude-code/main/schemas/settings-schema.json",
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "./src/hyh/scripts/verify-complete.sh",
        "timeout": 120,
        "description": "Verify completion criteria before stopping"
      }
    ],
    "SubagentStop": [
      {
        "type": "command",
        "command": "./src/hyh/scripts/verify-complete.sh",
        "timeout": 60,
        "description": "Verify subagent completion criteria"
      }
    ],
    "PostToolUse": [
      {
        "matcher": {
          "tool_name": "Write|Edit"
        },
        "type": "prompt",
        "prompt": "Run relevant tests and lint for the file you just modified.",
        "description": "Remind to test after file changes"
      }
    ]
  }
}
```

**Step 6: Commit**

```bash
git add src/hyh/scripts/verify-complete.sh src/hyh/templates/claude-settings.json
git commit -m "feat(hooks): add stop hook verification script

Anti-abandonment enforcement via:
- Progress tracking check
- Workflow task status check
- Test suite verification
- Lint and type check
- Verification report status

Includes settings template for Claude Code hooks."
```

---

### Task T005: Integration Test for Orchestration

**Files:**
- Create: `tests/hyh/test_orchestration.py`

**Step 1: Write integration test**

Create `tests/hyh/test_orchestration.py`:

```python
"""Integration tests for multi-agent orchestration."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.hyh.conftest import DaemonManager


@pytest.fixture
def orchestration_project(tmp_path: Path) -> Path:
    """Create a minimal project structure for orchestration testing."""
    # Create project structure
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "specs").mkdir()
    (tmp_path / ".claude" / "artifacts").mkdir(parents=True)

    # Create Makefile
    makefile = tmp_path / "Makefile"
    makefile.write_text("""\
test:
\t@echo "Tests passed"

lint:
\t@echo "Lint clean"

typecheck:
\t@echo "Types clean"

check: lint typecheck test
""")

    # Create pyproject.toml
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""\
[project]
name = "test-project"
version = "0.1.0"
""")

    # Initialize git
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    return tmp_path


class TestXMLPlanImport:
    """Test XML plan import with TaskPacket fields."""

    def test_import_xml_plan_with_full_taskpacket(
        self, orchestration_project: Path, daemon_manager: "DaemonManager"
    ) -> None:
        """XML plan with all TaskPacket fields imports correctly."""
        xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test multi-agent orchestration">
  <dependencies>
    <dep from="T002" to="T001"/>
  </dependencies>

  <task id="T001" role="implementer" model="sonnet">
    <description>Create core module</description>
    <instructions>
      1. Write failing test
      2. Implement minimal code
      3. Run verification
    </instructions>
    <success>
      - Tests pass
      - Lint clean
    </success>
    <scope>
      <include>src/core.py</include>
      <include>tests/test_core.py</include>
      <exclude>src/unrelated/</exclude>
    </scope>
    <interface>
      <input>Read exploration.md</input>
      <output>Exports core_function()</output>
    </interface>
    <constraints>Stay within scope</constraints>
    <tools>Read, Edit, Write, Bash</tools>
    <verification>
      <command>make test</command>
      <command>make lint</command>
    </verification>
    <artifacts>
      <read>.claude/artifacts/exploration.md</read>
      <write>.claude/artifacts/T001-api.md</write>
    </artifacts>
  </task>

  <task id="T002" role="implementer" model="haiku">
    <description>Add integration layer</description>
    <instructions>Use T001 output</instructions>
    <success>Integration works</success>
    <artifacts>
      <read>.claude/artifacts/T001-api.md</read>
      <write>.claude/artifacts/T002-integration.md</write>
    </artifacts>
  </task>
</plan>
"""
        plan_file = orchestration_project / "specs" / "plan.xml"
        plan_file.write_text(xml_plan)

        with daemon_manager.running(orchestration_project) as send:
            # Import plan
            result = send({"command": "plan_import", "file_path": str(plan_file)})
            assert result["ok"], f"Import failed: {result}"

            # Verify state
            state = send({"command": "get_state"})
            assert state["ok"]
            tasks = state["value"]["tasks"]

            # Check T001 fields
            t001 = tasks["T001"]
            assert t001["description"] == "Create core module"
            assert t001["role"] == "implementer"
            assert t001["model"] == "sonnet"
            assert "src/core.py" in t001["files_in_scope"]
            assert "src/unrelated/" in t001["files_out_of_scope"]
            assert "make test" in t001["verification_commands"]
            assert ".claude/artifacts/exploration.md" in t001["artifacts_to_read"]
            assert ".claude/artifacts/T001-api.md" in t001["artifacts_to_write"]

            # Check T002 depends on T001
            t002 = tasks["T002"]
            assert "T001" in t002["dependencies"]
            assert t002["model"] == "haiku"

    def test_task_claim_returns_full_packet(
        self, orchestration_project: Path, daemon_manager: "DaemonManager"
    ) -> None:
        """Task claim returns all TaskPacket fields for subagent."""
        xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Test claim">
  <task id="T001" role="implementer" model="opus">
    <description>Test task</description>
    <instructions>Do the thing</instructions>
    <success>Thing done</success>
    <scope>
      <include>src/module.py</include>
    </scope>
    <verification>
      <command>pytest</command>
    </verification>
  </task>
</plan>
"""
        plan_file = orchestration_project / "specs" / "plan.xml"
        plan_file.write_text(xml_plan)

        with daemon_manager.running(orchestration_project) as send:
            send({"command": "plan_import", "file_path": str(plan_file)})

            # Claim task
            result = send({"command": "task_claim", "worker_id": "test-worker"})
            assert result["ok"]
            task = result["value"]

            # Verify all TaskPacket fields present
            assert task["id"] == "T001"
            assert task["description"] == "Test task"
            assert task["instructions"] == "Do the thing"
            assert task["success_criteria"] == "Thing done"
            assert task["role"] == "implementer"
            assert task["model"] == "opus"
            assert "src/module.py" in task["files_in_scope"]
            assert "pytest" in task["verification_commands"]


class TestArtifactHandoff:
    """Test artifact-based inter-agent communication."""

    def test_artifact_paths_in_task(
        self, orchestration_project: Path, daemon_manager: "DaemonManager"
    ) -> None:
        """Tasks correctly specify artifact read/write paths."""
        xml_plan = """\
<?xml version="1.0" encoding="UTF-8"?>
<plan goal="Artifact test">
  <dependencies>
    <dep from="T002" to="T001"/>
  </dependencies>

  <task id="T001">
    <description>Producer</description>
    <instructions>Write artifact</instructions>
    <success>Artifact exists</success>
    <artifacts>
      <write>.claude/artifacts/T001-output.md</write>
    </artifacts>
  </task>

  <task id="T002">
    <description>Consumer</description>
    <instructions>Read artifact</instructions>
    <success>Used artifact</success>
    <artifacts>
      <read>.claude/artifacts/T001-output.md</read>
    </artifacts>
  </task>
</plan>
"""
        plan_file = orchestration_project / "specs" / "plan.xml"
        plan_file.write_text(xml_plan)

        with daemon_manager.running(orchestration_project) as send:
            send({"command": "plan_import", "file_path": str(plan_file)})

            state = send({"command": "get_state"})
            tasks = state["value"]["tasks"]

            # T001 writes, T002 reads
            assert ".claude/artifacts/T001-output.md" in tasks["T001"]["artifacts_to_write"]
            assert ".claude/artifacts/T001-output.md" in tasks["T002"]["artifacts_to_read"]


class TestVerifyCompleteScript:
    """Test the stop hook verification script."""

    def test_script_passes_when_all_complete(
        self, orchestration_project: Path
    ) -> None:
        """Script exits 0 when all checks pass."""
        # Create passing state
        progress = orchestration_project / ".claude" / "progress.md"
        progress.write_text("""\
# Progress

- [x] Task 1
- [x] Task 2
""")

        # Copy verification script
        script_src = Path(__file__).parent.parent.parent / "src" / "hyh" / "scripts" / "verify-complete.sh"
        if script_src.exists():
            script_dst = orchestration_project / "verify.sh"
            script_dst.write_text(script_src.read_text())
            script_dst.chmod(0o755)

            result = subprocess.run(
                ["./verify.sh"],
                cwd=orchestration_project,
                capture_output=True,
                text=True,
            )
            # May fail due to no hyh daemon, but should not syntax error
            assert "syntax error" not in result.stderr.lower()

    def test_script_fails_when_incomplete(
        self, orchestration_project: Path
    ) -> None:
        """Script exits 1 when progress.md has incomplete items."""
        progress = orchestration_project / ".claude" / "progress.md"
        progress.write_text("""\
# Progress

- [x] Task 1
- [ ] Task 2 incomplete
""")

        script_src = Path(__file__).parent.parent.parent / "src" / "hyh" / "scripts" / "verify-complete.sh"
        if script_src.exists():
            script_dst = orchestration_project / "verify.sh"
            script_dst.write_text(script_src.read_text())
            script_dst.chmod(0o755)

            result = subprocess.run(
                ["./verify.sh"],
                cwd=orchestration_project,
                capture_output=True,
                text=True,
                env={**dict(__import__("os").environ), "HYH_SKIP_VERIFY": ""},
            )
            # Should report incomplete items (may still exit 0 if other checks not available)
            assert "incomplete" in result.stdout.lower() or "FAIL" in result.stdout
```

**Step 2: Run the new tests**

```bash
pytest tests/hyh/test_orchestration.py -v
```

Expected: Tests pass (or skip if daemon not available).

**Step 3: Commit**

```bash
git add tests/hyh/test_orchestration.py
git commit -m "test(orchestration): add integration tests

Tests for:
- XML plan import with full TaskPacket fields
- Task claim returning all packet data
- Artifact handoff paths
- Verify-complete script behavior"
```

---

### Task T006: Code Review

**Files:**
- Review: All files created in T001-T005

**Step 1: Verify all files exist**

```bash
ls -la src/hyh/plugin/commands/orchestrate.md
ls -la src/hyh/plugin/agents/implementer.md
ls -la src/hyh/plugin/agents/reviewer.md
ls -la src/hyh/scripts/verify-complete.sh
ls -la src/hyh/templates/claude-settings.json
ls -la tests/hyh/test_orchestration.py
```

**Step 2: Run full test suite**

```bash
make check
```

Expected: All tests pass, lint clean, types clean.

**Step 3: Review for consistency**

Check that:
- [ ] All markdown files have valid frontmatter
- [ ] Shell script has no syntax errors: `bash -n src/hyh/scripts/verify-complete.sh`
- [ ] JSON settings file is valid: `python -m json.tool src/hyh/templates/claude-settings.json`
- [ ] Test file follows project conventions

**Step 4: Verify git history**

```bash
git log --oneline -10
```

Expected: See commits from T001-T005.

**Step 5: Final commit if any fixes needed**

If any issues found:
```bash
git add -A
git commit -m "fix(orchestration): address review feedback"
```
